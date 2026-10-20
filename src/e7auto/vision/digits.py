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


@dataclass(frozen=True, slots=True)
class _Glyph:
    mask: np.ndarray
    dilated: np.ndarray
    pixels: int

    @classmethod
    def prepare(cls, image: np.ndarray) -> _Glyph:
        mask = (image > 0).astype(np.uint8)
        dilated = cv2.dilate(mask, _GLYPH_KERNEL)
        mask.setflags(write=False)
        dilated.setflags(write=False)
        return cls(mask, dilated, int(np.count_nonzero(mask)))


class DigitMatcher:
    """Shared glyph templates; each caller retains its own color and confidence policy."""
    def __init__(self, templates, template_keys):
        self._templates = templates
        self._template_keys = frozenset(template_keys)
        self._variants = None
        self._prepared_variants = None

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
    def _glyph_similarity(left: _Glyph, right: _Glyph) -> float:
        union = np.count_nonzero(left.mask | right.mask)
        if union == 0 or left.pixels == 0 or right.pixels == 0:
            return 0.0
        strict_overlap = float(np.count_nonzero(left.mask & right.mask) / union)
        left_coverage = float(np.count_nonzero(left.mask & right.dilated) / left.pixels)
        right_coverage = float(np.count_nonzero(right.mask & left.dilated) / right.pixels)
        tolerant_overlap = (
            2.0 * left_coverage * right_coverage / (left_coverage + right_coverage)
            if left_coverage + right_coverage else 0.0
        )
        return 0.60 * strict_overlap + 0.40 * tolerant_overlap

    @staticmethod
    def _shifted_glyphs(glyph: np.ndarray) -> tuple[_Glyph, ...]:
        height, width = glyph.shape
        variants = []
        for offset_y in range(-1, 2):
            for offset_x in range(-1, 2):
                shifted = np.zeros_like(glyph)
                dx, dy = max(0, offset_x), max(0, offset_y)
                sx, sy = max(0, -offset_x), max(0, -offset_y)
                w, h = width - abs(offset_x), height - abs(offset_y)
                shifted[dy:dy+h, dx:dx+w] = glyph[sy:sy+h, sx:sx+w]
                variants.append(_Glyph.prepare(shifted))
        return tuple(variants)

    @staticmethod
    def _prepare_variants(variants):
        return {digit: tuple((_Glyph.prepare(mask), width) for mask, width in items)
                for digit, items in variants.items()}

    def best_match(
        self,
        glyph: np.ndarray,
        template_variants: dict[str, tuple[tuple[np.ndarray, int], ...]],
        *, width_scale: float = 1.,
    ) -> _DigitMatch:
        shifted = self._shifted_glyphs(self._normalize_glyph(glyph))
        # Only repository-owned, immutable variants are cached across calls.
        prepared = (self._prepared_variants
                    if self._variants is not None and template_variants is self._variants[0]
                    else self._prepare_variants(template_variants))
        ys, xs = np.nonzero(glyph)
        glyph_width = int(xs.max() - xs.min() + 1) if len(xs) else 0
        class_scores: list[tuple[float, int, str]] = []
        for digit, variants in prepared.items():
            best_confidence = 0.0
            best_width_error = 2**31 - 1
            for template, template_width in variants:
                confidence = max(self._glyph_similarity(item, template) for item in shifted)
                width_error = abs(glyph_width - max(1,round(template_width*width_scale)))
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
        return _DigitMatch(best_digit, best_confidence, runner_up_confidence, best_width_error)


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
        self, scale_x: float = 1.,
    ) -> tuple[
        dict[str, tuple[tuple[np.ndarray, int], ...]],
        tuple[int, ...],
    ]:
        if scale_x != 1.:
            variants,widths = self.template_variants()
            return (variants,tuple(max(1,round(w*scale_x)) for w in widths))
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
        for variants in template_variants.values():
            for mask, _ in variants:
                mask.setflags(write=False)
        self._prepared_variants = self._prepare_variants(template_variants)
        self._variants = template_variants, tuple(template_widths)
        return self._variants
