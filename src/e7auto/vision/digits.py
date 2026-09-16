from __future__ import annotations
from dataclasses import dataclass
import cv2
import numpy as np
from e7auto.core.ports import Frame
_GLYPH_KERNEL = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))

@dataclass(frozen=True, slots=True)
class _DigitMatch:
    digit: str
    confidence: float
    runner_up_confidence: float
    width_error: int

    @property
    def margin(self) -> float:
        return self.confidence - self.runner_up_confidence


class DigitMatcher:
    """Shared glyph templates; each caller retains its own color and confidence policy."""
    def __init__(self, templates, template_keys):
        self._templates = templates
        self._template_keys = frozenset(template_keys)
        self._variants = None

    @staticmethod
    def neutral_bright_mask(image: Frame) -> np.ndarray:
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
    def best_match(
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


    def template_variants(
        self,
    ) -> tuple[
        dict[str, tuple[tuple[np.ndarray, int], ...]],
        tuple[int, ...],
    ]:
        if self._variants is not None:
            return self._variants
        template_variants: dict[str, tuple[tuple[np.ndarray, int], ...]] = {}
        template_widths: list[int] = []
        for digit in "0123456789":
            template_keys = [f"sky_stone_digit_{digit}"]
            if digit == "0" and "sky_stone_digit_0_wide" in self._template_keys:
                template_keys.append("sky_stone_digit_0_wide")
            variants: list[tuple[np.ndarray, int]] = []
            for template_key in template_keys:
                template = self._templates.get(template_key)
                template_mask = (
                    template.mask > 0
                    if template.mask is not None
                    else self.neutral_bright_mask(template.image) > 0
                )
                _, foreground_xs = np.nonzero(template_mask)
                foreground_width = int(foreground_xs.max() - foreground_xs.min() + 1)
                variants.extend(
                    (variant, foreground_width)
                    for variant in self._stroke_variants(template_mask)
                )
                template_widths.append(foreground_width)
            template_variants[digit] = tuple(variants)
        self._variants = template_variants, tuple(template_widths)
        return self._variants
