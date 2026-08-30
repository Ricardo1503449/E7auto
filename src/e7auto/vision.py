from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from .config import AppConfig, Point, Rect, SlotConfig, TargetConfig
from .geometry import AdaptedFrame
from .ports import Frame


_GLYPH_KERNEL = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))


@dataclass(frozen=True, slots=True)
class Observation:
    object_id: str
    confidence: float
    roi: Rect
    anchor: Point


@dataclass(frozen=True, slots=True)
class InventoryMatch:
    target_id: str
    display_name: str
    slot_id: str
    slot_order: int
    buy_point: Point
    confidence: float
    roi: Rect
    is_purchased: bool = False


@dataclass(frozen=True, slots=True)
class SkyStoneBalanceObservation:
    value: int
    confidence: float
    roi: Rect


@dataclass(frozen=True, slots=True)
class _DigitMatch:
    digit: str
    confidence: float
    runner_up_confidence: float
    width_error: int

    @property
    def margin(self) -> float:
        return self.confidence - self.runner_up_confidence


@dataclass(frozen=True, slots=True)
class ScrollMovementObservation:
    mean_absolute_difference: float
    changed_fraction: float
    maximum_difference: int
    phase_shift_x: float
    phase_shift_y: float
    phase_response: float


def _inventory_gray(frame: Frame | AdaptedFrame, roi: Rect) -> np.ndarray:
    if isinstance(frame, AdaptedFrame):
        source = frame.normalized_roi(roi)
    else:
        height, width = frame.shape[:2]
        if roi.x < 0 or roi.y < 0 or roi.right > width or roi.bottom > height:
            raise ValueError(f"Scroll ROI outside frame: {roi} vs {width}x{height}")
        source = frame[roi.y : roi.bottom, roi.x : roi.right]
    if source.ndim != 3 or source.shape[2] not in (3, 4):
        raise ValueError("Scroll comparison frame must have 3 or 4 channels")
    conversion = cv2.COLOR_BGRA2GRAY if source.shape[2] == 4 else cv2.COLOR_BGR2GRAY
    return cv2.cvtColor(source, conversion)


def _measure_gray_movement(
    before_gray: np.ndarray,
    after_gray: np.ndarray,
    difference_threshold: int,
    *,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> ScrollMovementObservation:
    difference = cv2.absdiff(before_gray, after_gray)
    phase_shift, phase_response = cv2.phaseCorrelate(
        before_gray.astype(np.float32),
        after_gray.astype(np.float32),
    )
    return ScrollMovementObservation(
        mean_absolute_difference=float(np.mean(difference)),
        changed_fraction=float(np.mean(difference > difference_threshold)),
        maximum_difference=int(np.max(difference)),
        phase_shift_x=float(phase_shift[0] * scale_x),
        phase_shift_y=float(phase_shift[1] * scale_y),
        phase_response=float(phase_response),
    )


def measure_inventory_scroll(
    before: Frame,
    after: Frame,
    roi: Rect,
    difference_threshold: int,
) -> ScrollMovementObservation:
    if before.shape != after.shape:
        raise ValueError(
            f"Scroll comparison frames must have identical shapes: {before.shape} != {after.shape}"
        )
    if difference_threshold <= 0:
        raise ValueError("Scroll difference threshold must be positive")

    return _measure_gray_movement(
        _inventory_gray(before, roi),
        _inventory_gray(after, roi),
        difference_threshold,
    )


def measure_inventory_scroll_stability(
    before: Frame,
    after: Frame,
    roi: Rect,
    difference_threshold: int,
    downsample_factor: int,
) -> ScrollMovementObservation:
    if before.shape != after.shape:
        raise ValueError(
            f"Scroll comparison frames must have identical shapes: {before.shape} != {after.shape}"
        )
    if difference_threshold <= 0:
        raise ValueError("Scroll difference threshold must be positive")
    if downsample_factor <= 0:
        raise ValueError("Scroll stability downsample factor must be positive")

    before_gray = _inventory_gray(before, roi)
    after_gray = _inventory_gray(after, roi)
    width = max(1, before_gray.shape[1] // downsample_factor)
    height = max(1, before_gray.shape[0] // downsample_factor)
    size = (width, height)
    before_small = cv2.resize(before_gray, size, interpolation=cv2.INTER_AREA)
    after_small = cv2.resize(after_gray, size, interpolation=cv2.INTER_AREA)
    return _measure_gray_movement(
        before_small,
        after_small,
        difference_threshold,
        scale_x=before_gray.shape[1] / width,
        scale_y=before_gray.shape[0] / height,
    )


class PurchaseOutcome(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    INSUFFICIENT_FUNDS = "insufficient_funds"


@dataclass(frozen=True, slots=True)
class TemplateData:
    image: Frame
    mask: np.ndarray | None = None


class TemplateRepository:
    def __init__(self, config: AppConfig):
        self._templates: dict[str, TemplateData] = {}
        for key, path in config.template_paths.items():
            loaded = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if loaded is None or loaded.size == 0:
                raise ValueError(f"Cannot load template {key}: {path}")
            if loaded.ndim != 3 or loaded.shape[2] not in (3, 4):
                raise ValueError(f"Template must have 3 or 4 channels: {key}: {path}")
            image = np.ascontiguousarray(loaded[:, :, :3])
            mask: np.ndarray | None = None
            if loaded.shape[2] == 4 and not np.all(loaded[:, :, 3] == 255):
                mask = np.ascontiguousarray(loaded[:, :, 3])
                if not np.any(mask):
                    raise ValueError(f"Template alpha mask is empty: {key}: {path}")
            self._templates[key] = TemplateData(image, mask)

    def get(self, key: str) -> TemplateData:
        try:
            return self._templates[key]
        except KeyError as exc:
            raise KeyError(f"Unknown template: {key}") from exc


class OpenCvGameVision:
    """In-memory-only vision service. It has no filesystem write API."""

    def __init__(self, config: AppConfig, templates: TemplateRepository):
        self._config = config
        self._templates = templates
        self._targets: tuple[TargetConfig, ...] = config.targets
        self._targets_by_id = {target.target_id: target for target in config.targets}
        self._sky_stone_variants: tuple[
            dict[str, tuple[tuple[np.ndarray, int], ...]],
            tuple[int, ...],
        ] | None = None

    @staticmethod
    def _bgr(frame: Frame | AdaptedFrame) -> Frame | AdaptedFrame:
        if isinstance(frame, AdaptedFrame):
            return frame
        if frame.ndim != 3 or frame.shape[2] not in (3, 4):
            raise ValueError("Frame must be an HxWx3 or HxWx4 uint8 array")
        return np.ascontiguousarray(frame[:, :, :3])

    @staticmethod
    def _crop(frame: Frame | AdaptedFrame, roi: Rect) -> Frame:
        if isinstance(frame, AdaptedFrame):
            return frame.normalized_roi(roi)
        height, width = frame.shape[:2]
        if roi.x < 0 or roi.y < 0 or roi.right > width or roi.bottom > height:
            raise ValueError(f"ROI outside frame: {roi} vs {width}x{height}")
        return frame[roi.y : roi.bottom, roi.x : roi.right]

    def match(self, frame: Frame, template_key: str, roi: Rect, threshold: float) -> Observation | None:
        return self._match_bgr(self._bgr(frame), template_key, roi, threshold)

    def _match_bgr(
        self,
        bgr_frame: Frame | AdaptedFrame,
        template_key: str,
        roi: Rect,
        threshold: float,
    ) -> Observation | None:
        """Match against an already prepared contiguous BGR frame.

        Inventory scanning performs many template comparisons against one captured
        frame.  Preparing the MSS BGRA frame once avoids copying the full client
        image for every individual comparison while preserving the public
        single-match path above.
        """

        source = self._crop(bgr_frame, roi)
        template_data = self._templates.get(template_key)
        template = template_data.image
        if template.shape[0] > source.shape[0] or template.shape[1] > source.shape[1]:
            return None
        if template_data.mask is None:
            result = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
            _, confidence, _, location = cv2.minMaxLoc(result)
        else:
            result = cv2.matchTemplate(
                source,
                template,
                cv2.TM_SQDIFF_NORMED,
                mask=template_data.mask,
            )
            result = np.nan_to_num(result, nan=np.inf, posinf=np.inf, neginf=np.inf)
            difference, _, location, _ = cv2.minMaxLoc(result)
            confidence = 1.0 - float(difference)
        confidence = max(0.0, min(1.0, float(confidence)))
        if float(confidence) < threshold:
            return None
        anchor = Point(
            roi.x + location[0] + template.shape[1] // 2,
            roi.y + location[1] + template.shape[0] // 2,
        )
        return Observation(template_key, float(confidence), roi, anchor)

    def main_shop_icon(self, frame: Frame) -> Observation | None:
        return self.match(
            frame,
            "main_shop_icon",
            self._config.rois["main_shop_icon"],
            self._config.anchor_confidence,
        )

    def shop_ready(self, frame: Frame) -> Observation | None:
        return self.match(
            frame,
            "shop_refresh_button",
            self._config.rois["shop_refresh_button"],
            self._config.anchor_confidence,
        )

    def shop_exit_icon(self, frame: Frame) -> Observation | None:
        return self.match(
            frame,
            "shop_exit_icon",
            self._config.rois["shop_exit_icon"],
            self._config.anchor_confidence,
        )

    def network_connection_error(self, frame: Frame) -> Observation | None:
        return self.match(frame, "network_connection_abnormal", self._config.rois["network_error"], self._config.default_confidence)

    def network_retry(self, frame: Frame) -> Observation | None:
        return self.match(frame, "network_retry", self._config.rois["network_retry"], self._config.default_confidence)

    def refresh_confirm_dialog(self, frame: Frame) -> Observation | None:
        prompt = self.match(
            frame,
            "refresh_confirm_prompt",
            self._config.rois["refresh_confirm_prompt"],
            self._config.default_confidence,
        )
        if prompt is None:
            return None
        button = self.match(
            frame,
            "refresh_confirm_button",
            self._config.rois["refresh_confirm_button"],
            self._config.anchor_confidence,
        )
        if button is None:
            return None
        return Observation(
            "refresh_confirm_dialog",
            min(prompt.confidence, button.confidence),
            prompt.roi,
            button.anchor,
        )

    def confirm_dialog(self, frame: Frame, target_id: str) -> Observation | None:
        target = self._targets_by_id[target_id]
        identity = self.match(
            frame,
            target.confirm_template,
            self._config.rois["confirm_item"],
            self._config.default_confidence,
        )
        if identity is None:
            return None
        button = self.match(
            frame,
            "confirm_button",
            self._config.rois["confirm_button"],
            self._config.anchor_confidence,
        )
        if button is None:
            return None
        return Observation(
            f"confirm:{target_id}",
            min(identity.confidence, button.confidence),
            identity.roi,
            button.anchor,
        )

    def purchase_outcome(self, frame: Frame, target_id: str, item_roi: Rect) -> PurchaseOutcome:
        warning_roi = self._config.rois["purchase_result"]
        insufficient = self.match(
            frame,
            "insufficient_funds",
            warning_roi,
            self._config.anchor_confidence,
        )
        if insufficient is not None:
            return PurchaseOutcome.INSUFFICIENT_FUNDS
        target = self._targets_by_id[target_id]
        success = self.match(
            frame,
            target.purchased_template,
            item_roi,
            self._config.default_confidence,
        )
        if success is not None:
            return PurchaseOutcome.SUCCESS
        return PurchaseOutcome.PENDING

    def scan_inventory(
        self,
        frame: Frame,
        screen: str,
        enabled_target_ids: frozenset[str] | None = None,
        excluded_slot_ids: frozenset[str] = frozenset(),
    ) -> tuple[InventoryMatch, ...]:
        bgr_frame = self._bgr(frame)
        targets = (
            self._targets
            if enabled_target_ids is None
            else tuple(
                target
                for target in self._targets
                if target.target_id in enabled_target_ids
            )
        )
        matches: list[InventoryMatch] = []
        for slot in (
            item
            for item in self._config.slots
            if item.screen == screen and item.slot_id not in excluded_slot_ids
        ):
            candidates: list[InventoryMatch] = []
            for target in targets:
                available = self._match_bgr(
                    bgr_frame,
                    target.template,
                    slot.item_roi,
                    self._config.default_confidence,
                )
                purchased = self._match_bgr(
                    bgr_frame,
                    target.purchased_template,
                    slot.item_roi,
                    self._config.default_confidence,
                )
                if available is None and purchased is None:
                    continue
                is_purchased = purchased is not None and (
                    available is None or purchased.confidence >= available.confidence
                )
                observation = purchased if is_purchased else available
                assert observation is not None
                candidates.append(
                    InventoryMatch(
                        target.target_id,
                        target.display_name,
                        slot.slot_id,
                        slot.order,
                        slot.buy_point,
                        observation.confidence,
                        slot.item_roi,
                        is_purchased,
                    )
                )
            if candidates:
                matches.append(
                    max(
                        candidates,
                        key=lambda item: (item.confidence, item.is_purchased),
                    )
                )
        return tuple(sorted(matches, key=lambda item: item.slot_order))

    def inventory_scroll_movement(
        self,
        before: Frame,
        after: Frame,
    ) -> ScrollMovementObservation:
        return measure_inventory_scroll(
            before,
            after,
            self._config.rois["inventory_list"],
            self._config.scroll.difference_threshold,
        )

    def inventory_scroll_stability(
        self,
        before: Frame,
        after: Frame,
    ) -> ScrollMovementObservation:
        return measure_inventory_scroll_stability(
            before,
            after,
            self._config.rois["inventory_list"],
            self._config.scroll.difference_threshold,
            self._config.scroll.downsample_factor,
        )

    @staticmethod
    def _neutral_bright_mask(image: Frame) -> np.ndarray:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        return (
            (hsv[:, :, 1] <= 40)
            & (hsv[:, :, 2] >= 160)
        ).astype(np.uint8)

    @staticmethod
    def _normalize_glyph(mask: np.ndarray, width: int = 32, height: int = 48) -> np.ndarray:
        ys, xs = np.nonzero(mask)
        if not len(xs):
            return np.zeros((height, width), dtype=np.uint8)
        glyph = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
        scale = min((width - 2) / glyph.shape[1], (height - 2) / glyph.shape[0])
        resized_width = max(1, int(round(glyph.shape[1] * scale)))
        resized_height = max(1, int(round(glyph.shape[0] * scale)))
        resized = cv2.resize(
            glyph.astype(np.uint8),
            (resized_width, resized_height),
            interpolation=cv2.INTER_NEAREST,
        )
        normalized = np.zeros((height, width), dtype=np.uint8)
        x = (width - resized_width) // 2
        y = (height - resized_height) // 2
        normalized[y : y + resized_height, x : x + resized_width] = resized > 0
        return normalized

    @staticmethod
    def _glyph_similarity(left: np.ndarray, right: np.ndarray) -> float:
        left_mask = (left > 0).astype(np.uint8)
        right_mask = (right > 0).astype(np.uint8)
        union = np.count_nonzero(left_mask | right_mask)
        if union == 0:
            return 0.0
        left_pixels = np.count_nonzero(left_mask)
        right_pixels = np.count_nonzero(right_mask)
        if left_pixels == 0 or right_pixels == 0:
            return 0.0
        intersection = np.count_nonzero(left_mask & right_mask)
        strict_overlap = float(intersection / union)

        left_coverage = float(
            np.count_nonzero(left_mask & cv2.dilate(right_mask, _GLYPH_KERNEL))
            / left_pixels
        )
        right_coverage = float(
            np.count_nonzero(right_mask & cv2.dilate(left_mask, _GLYPH_KERNEL))
            / right_pixels
        )
        tolerant_overlap = (
            2.0 * left_coverage * right_coverage / (left_coverage + right_coverage)
            if left_coverage + right_coverage
            else 0.0
        )
        return 0.60 * strict_overlap + 0.40 * tolerant_overlap

    @classmethod
    def _aligned_glyph_similarity(cls, glyph: np.ndarray, template: np.ndarray) -> float:
        best = 0.0
        height, width = glyph.shape
        for offset_y in range(-1, 2):
            for offset_x in range(-1, 2):
                shifted = np.zeros_like(glyph)
                destination_x = max(0, offset_x)
                destination_y = max(0, offset_y)
                source_x = max(0, -offset_x)
                source_y = max(0, -offset_y)
                copy_width = width - abs(offset_x)
                copy_height = height - abs(offset_y)
                shifted[
                    destination_y : destination_y + copy_height,
                    destination_x : destination_x + copy_width,
                ] = glyph[
                    source_y : source_y + copy_height,
                    source_x : source_x + copy_width,
                ]
                best = max(best, cls._glyph_similarity(shifted, template))
        return best

    @classmethod
    def _best_digit_match(
        cls,
        glyph: np.ndarray,
        template_variants: dict[str, tuple[tuple[np.ndarray, int], ...]],
    ) -> _DigitMatch:
        normalized = cls._normalize_glyph(glyph)
        ys, xs = np.nonzero(glyph)
        glyph_width = int(xs.max() - xs.min() + 1) if len(xs) else 0
        class_scores: list[tuple[float, int, str]] = []
        for digit, variants in template_variants.items():
            best_confidence = 0.0
            best_width_error = 2**31 - 1
            for template_mask, template_width in variants:
                confidence = cls._aligned_glyph_similarity(normalized, template_mask)
                width_error = abs(glyph_width - template_width)
                if confidence > best_confidence or (
                    confidence == best_confidence and width_error < best_width_error
                ):
                    best_confidence = confidence
                    best_width_error = width_error
            class_scores.append((best_confidence, best_width_error, digit))
        class_scores.sort(key=lambda item: (-item[0], item[1], item[2]))
        if not class_scores:
            return _DigitMatch("", 0.0, 0.0, 0)
        best_confidence, best_width_error, best_digit = class_scores[0]
        runner_up_confidence = class_scores[1][0] if len(class_scores) > 1 else 0.0
        return _DigitMatch(
            best_digit,
            best_confidence,
            runner_up_confidence,
            best_width_error,
        )

    def _digit_match_is_safe(self, match: _DigitMatch) -> bool:
        return (
            bool(match.digit)
            and match.confidence >= self._config.sky_stone_digit_confidence
            and match.margin >= self._config.sky_stone_digit_margin
        )

    @classmethod
    def _stroke_variants(cls, template_mask: np.ndarray) -> tuple[np.ndarray, ...]:
        source = (template_mask > 0).astype(np.uint8)
        candidates = (
            source,
            cv2.dilate(source, _GLYPH_KERNEL, iterations=1),
            cv2.erode(source, _GLYPH_KERNEL, iterations=1),
        )
        variants: list[np.ndarray] = []
        for candidate in candidates:
            if not np.any(candidate):
                continue
            normalized = cls._normalize_glyph(candidate)
            if not any(np.array_equal(normalized, existing) for existing in variants):
                variants.append(normalized)
        return tuple(variants)

    def _sky_stone_template_variants(
        self,
    ) -> tuple[
        dict[str, tuple[tuple[np.ndarray, int], ...]],
        tuple[int, ...],
    ]:
        if self._sky_stone_variants is not None:
            return self._sky_stone_variants
        template_variants: dict[str, tuple[tuple[np.ndarray, int], ...]] = {}
        template_widths: list[int] = []
        for digit in "0123456789":
            template_keys = [f"sky_stone_digit_{digit}"]
            if digit == "0" and "sky_stone_digit_0_wide" in self._config.template_paths:
                template_keys.append("sky_stone_digit_0_wide")
            variants: list[tuple[np.ndarray, int]] = []
            for template_key in template_keys:
                template = self._templates.get(template_key)
                template_mask = (
                    template.mask > 0
                    if template.mask is not None
                    else self._neutral_bright_mask(template.image) > 0
                )
                _, foreground_xs = np.nonzero(template_mask)
                foreground_width = int(foreground_xs.max() - foreground_xs.min() + 1)
                variants.extend(
                    (variant, foreground_width)
                    for variant in self._stroke_variants(template_mask)
                )
                template_widths.append(foreground_width)
            template_variants[digit] = tuple(variants)
        self._sky_stone_variants = template_variants, tuple(template_widths)
        return self._sky_stone_variants

    def _split_merged_digit_component(
        self,
        glyph: np.ndarray,
        template_variants: dict[str, tuple[tuple[np.ndarray, int], ...]],
        minimum_width: int,
        maximum_width: int,
    ) -> tuple[tuple[str, float], ...] | None:
        ys, xs = np.nonzero(glyph)
        if not len(xs):
            return None
        glyph = glyph[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
        width = glyph.shape[1]
        solutions: list[tuple[tuple[str, float], ...]] = []

        def visit(start: int, decoded: list[tuple[str, float]], width_error: int) -> None:
            remaining = width - start
            if remaining == 0:
                if len(decoded) >= 2:
                    solutions.append(tuple(decoded) + (("", float(width_error)),))
                return
            if remaining < minimum_width:
                return
            latest_end = min(width, start + maximum_width)
            for end in range(start + minimum_width, latest_end + 1):
                trailing = width - end
                if trailing and trailing < minimum_width:
                    continue
                segment = glyph[:, start:end]
                match = self._best_digit_match(segment, template_variants)
                if not self._digit_match_is_safe(match):
                    continue
                visit(
                    end,
                    [*decoded, (match.digit, match.confidence)],
                    width_error + match.width_error,
                )

        visit(0, [], 0)
        if not solutions:
            return None

        def rank(solution: tuple[tuple[str, float], ...]) -> tuple[float, float, int, int]:
            decoded = solution[:-1]
            width_error = int(solution[-1][1])
            confidences = [confidence for _, confidence in decoded]
            return (
                min(confidences),
                sum(confidences) / len(confidences),
                -width_error,
                -len(decoded),
            )

        best = max(solutions, key=rank)
        return best[:-1]

    def _sky_stone_digits_roi(
        self, frame: Frame, icon: Observation
    ) -> Rect | None:
        base_roi = self._config.rois["sky_stone_digits"]
        offset = self._config.sky_stone_digits_offset
        if offset is None:
            roi = base_roi
        else:
            icon_template = self._templates.get("sky_stone_icon").image
            icon_left = icon.anchor.x - icon_template.shape[1] // 2
            icon_top = icon.anchor.y - icon_template.shape[0] // 2
            roi = Rect(
                icon_left + offset.x,
                icon_top + offset.y,
                base_roi.width,
                base_roi.height,
            )
        if isinstance(frame, AdaptedFrame):
            frame_width = frame.transform.baseline.width
            frame_height = frame.transform.baseline.height
        else:
            frame_height, frame_width = frame.shape[:2]
        if (
            roi.x < 0
            or roi.y < 0
            or roi.right > frame_width
            or roi.bottom > frame_height
        ):
            return None
        return roi

    def _read_sky_stone_digits(
        self,
        frame: Frame | AdaptedFrame,
        roi: Rect,
    ) -> tuple[int, float] | None:
        source = self._crop(self._bgr(frame), roi)
        mask = self._neutral_bright_mask(source)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        min_height = max(8, int(round(source.shape[0] * 0.40)))
        components: list[tuple[int, np.ndarray]] = []
        for component in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[component])
            if height < min_height or area < 20:
                continue
            glyph = labels[y : y + height, x : x + width] == component
            components.append((x, glyph))
        components.sort(key=lambda item: item[0])
        if not components:
            return None

        template_variants, template_widths = self._sky_stone_template_variants()

        minimum_segment_width = max(1, min(template_widths) - 2)
        maximum_segment_width = max(template_widths) + 2

        parsed: list[str] = []
        confidences: list[float] = []
        for _, glyph in components:
            match = self._best_digit_match(glyph, template_variants)
            if self._digit_match_is_safe(match):
                parsed.append(match.digit)
                confidences.append(match.confidence)
                continue

            if glyph.shape[1] <= maximum_segment_width:
                return None
            split = self._split_merged_digit_component(
                glyph,
                template_variants,
                minimum_segment_width,
                maximum_segment_width,
            )
            if split is None:
                return None
            parsed.extend(digit for digit, _ in split)
            confidences.extend(confidence for _, confidence in split)
        return int("".join(parsed)), min(confidences)

    def sky_stone_balance(
        self, frame: Frame | AdaptedFrame
    ) -> SkyStoneBalanceObservation | None:
        icon = self.match(
            frame,
            "sky_stone_icon",
            self._config.rois["sky_stone_icon"],
            self._config.anchor_confidence,
        )
        if icon is None:
            return None

        roi = self._sky_stone_digits_roi(frame, icon)
        if roi is None:
            return None
        parsed = self._read_sky_stone_digits(frame, roi)
        if parsed is None:
            return None
        value, confidence = parsed
        return SkyStoneBalanceObservation(value, min(icon.confidence, confidence), roi)
