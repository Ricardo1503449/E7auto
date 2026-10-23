from __future__ import annotations
import cv2
import numpy as np
from e7auto.configuration.models import AppConfig
from e7auto.core.types import Rect, ClientRect
from e7auto.vision.frames import FrameView
from e7auto.core.ports import Frame
from e7auto.core.observations import Observation
from e7auto.features.shop.contracts import InventoryMatch, SkyStoneBalanceObservation, ScrollMovementObservation, PurchaseOutcome
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.matching import TemplateMatcher
from e7auto.vision.candidates import CandidateCache
from e7auto.vision.frames import frame_view
from e7auto.vision.digits import DigitMatcher, _DigitMatch
from e7auto.vision.network import NetworkVision
from e7auto.vision.navigation import StartupNavigationVision
from e7auto.features.shop.scroll_vision import measure_inventory_scroll, measure_inventory_scroll_stability

class ShopVision:
    """Shop detectors composed from shared matching and glyph services."""
    def __init__(self, config: AppConfig, templates: TemplateRepository):
        self._config = config
        self._templates = templates
        self._targets = config.targets
        self._targets_by_id = {target.target_id: target for target in config.targets}
        self._matcher = TemplateMatcher(templates, CandidateCache(config.candidate_cache_mib*1024*1024))
        self._digits = DigitMatcher(templates, config.template_paths)
        self._network = NetworkVision(config, self._matcher)
        self._navigation = StartupNavigationVision(config, self._matcher)

    def navigation_menu(self, frame):
        return self._navigation.navigation_menu(frame)

    def navigation_tavern(self, frame):
        return self._navigation.navigation_tavern(frame)

    def match(self, frame, template_key: str, roi: Rect, threshold: float) -> Observation | None:
        return self._matcher.match(frame, template_key, roi, threshold)

    def match_entry(self, frame, template_key: str, roi: Rect, threshold: float,
                    *, structure_threshold: float) -> Observation | None:
        return self._matcher.match_entry(frame, template_key, roi, threshold,
                                         structure_threshold=structure_threshold)

    def network_connection_error(self, frame):
        return self._network.network_connection_error(frame)

    def network_retry(self, frame):
        return self._network.network_retry(frame)

    def main_shop_icon(self, frame: Frame) -> Observation | None:
        thresholds = self._config.entry_thresholds["shop"]
        return self.match_entry(
            frame,
            "main_shop_icon",
            self._config.rois["left_icon_column"],
            thresholds.color,
            structure_threshold=thresholds.structure,
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
            prompt.bounds,
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
            identity.bounds,
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
        if self.purchased_button(frame, item_roi) is not None:
            return PurchaseOutcome.SUCCESS_BUTTON
        return PurchaseOutcome.PENDING


    def purchased_button(self, frame: Frame, item_roi: Rect) -> Observation | None:
        """Read the sold-out button only in the original purchase slot.

        This is purchase-result evidence, not a way to infer an item's identity
        during inventory scanning. The caller has already confirmed the target.
        """
        slots = [slot for slot in self._config.slots if slot.item_roi == item_roi]
        if len(slots) != 1 or "purchased_button" not in self._config.template_paths:
            return None
        height, width = self._templates.get("purchased_button").image.shape[:2]
        # Follow the selected row's configured anchor; padding is per side.
        padding = self._config.purchased_button_padding
        width += 2 * padding.x
        height += 2 * padding.y
        center = slots[0].buy_point
        roi = Rect(center.x - width // 2, center.y - height // 2, width, height)
        baseline = self._config.baseline_client_size
        if roi.x < 0 or roi.y < 0 or roi.right > baseline.width or roi.bottom > baseline.height:
            return None
        return self.match(frame, "purchased_button", roi, self._config.anchor_confidence)


    def scan_inventory(
        self,
        frame: Frame,
        screen: str,
        enabled_target_ids: frozenset[str] | None = None,
        excluded_slot_ids: frozenset[str] = frozenset(),
    ) -> tuple[InventoryMatch, ...]:
        bgr_frame = self._matcher.prepare_bgr(frame)
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
                available = self._matcher.match_bgr(
                    bgr_frame,
                    target.template,
                    slot.item_roi,
                    self._config.default_confidence,
                )
                purchased = self._matcher.match_bgr(
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
            self._config.rois["inventory_content"],
            self._config.scroll.difference_threshold,
            config=self._config.scroll,
        )


    def inventory_scroll_stability(
        self,
        before: Frame,
        after: Frame,
    ) -> ScrollMovementObservation:
        return measure_inventory_scroll_stability(
            before,
            after,
            self._config.rois["inventory_content"],
            self._config.scroll.difference_threshold,
            self._config.scroll.downsample_factor,
        )


    def _digit_match_is_safe(self, match: _DigitMatch) -> bool:
        return (
            bool(match.digit)
            and match.confidence >= self._config.sky_stone_digit_confidence
            and match.margin >= self._config.sky_stone_digit_margin
        )


    def _split_merged_digit_component(
        self,
        glyph: np.ndarray,
        template_variants: dict[str, tuple[tuple[np.ndarray, int], ...]],
        minimum_width: int,
        maximum_width: int,
        *, width_scale: float = 1.,
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
                match = self._digits.best_match(segment, template_variants, width_scale=width_scale)
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
        view = frame_view(frame)
        base_roi = view.transform.rect(self._config.rois["sky_stone_digits"])
        offset = self._config.sky_stone_digits_offset
        if offset is None:
            roi = base_roi
        else:
            image = self._templates.get("sky_stone_icon").image
            g=icon.geometry
            sx,sy=(g.scale_x,g.scale_y) if g is not None else (icon.bounds.width/image.shape[1],icon.bounds.height/image.shape[0])
            x,y=(g.x,g.y) if g is not None else (icon.bounds.x,icon.bounds.y)
            left = round(x+offset.x*sx)
            top = round(y+offset.y*sy)
            roi = ClientRect(left,top,base_roi.right-left,base_roi.height)
            if left<base_roi.x or left>=base_roi.right:
                return None
        b=view.bounds
        if roi.x<b.x or roi.y<b.y or roi.right>b.right or roi.bottom>b.bottom:
            return None
        return roi


    def _read_sky_stone_digits(
        self,
        frame: Frame | FrameView,
        roi: Rect,
    ) -> tuple[int, float] | None:
        source = self._matcher.crop(self._matcher.prepare_bgr(frame), roi)
        mask = self._digits.neutral_bright_mask(source)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        sx,sy = frame_view(frame).transform.scale_x,frame_view(frame).transform.scale_y
        min_height = max(round(8*sy), int(round(source.shape[0] * 0.40)))
        components: list[tuple[int, int, np.ndarray]] = []
        for component in range(1, count):
            x, y, width, height, area = (int(value) for value in stats[component])
            if height < min_height or area < 20*sx*sy:
                continue
            glyph = labels[y : y + height, x : x + width] == component
            components.append((x, width, glyph))
        components.sort(key=lambda item: item[0])
        if not components:
            return None

        template_variants, template_widths = self._digits.template_variants(sx)

        minimum_segment_width = max(1, min(template_widths) - max(1,round(2*sx)))
        maximum_segment_width = max(template_widths) + max(1,round(2*sx))
        maximum_run_gap = maximum_segment_width

        parsed: list[str] = []
        confidences: list[float] = []
        previous_right = 0
        for x, width, glyph in components:
            gap = x if not parsed else x - previous_right
            # Thousands separators are too short to become components, while the
            # much larger gap before the next header control terminates this run.
            if gap > maximum_run_gap:
                if parsed:
                    break
                return None

            match = self._digits.best_match(glyph, template_variants, width_scale=sx)
            if self._digit_match_is_safe(match):
                parsed.append(match.digit)
                confidences.append(match.confidence)
                previous_right = x + width
                continue

            if glyph.shape[1] <= maximum_segment_width:
                return None
            split = self._split_merged_digit_component(
                glyph,
                template_variants,
                minimum_segment_width,
                maximum_segment_width,
                width_scale=sx,
            )
            if split is None:
                return None
            parsed.extend(digit for digit, _ in split)
            confidences.extend(confidence for _, confidence in split)
            previous_right = x + width
        return int("".join(parsed)), min(confidences)


    def sky_stone_balance(
        self, frame: Frame | FrameView
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

    def clear_template_cache(self):
        self._matcher.cache.clear()
