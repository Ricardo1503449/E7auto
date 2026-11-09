from __future__ import annotations
from dataclasses import dataclass
from math import ceil, isfinite
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
class _DigitReferences:
    features: np.ndarray
    widths: np.ndarray
    groups: tuple[tuple[str, int, int], ...]


class DigitMatcher:
    """One smoothed glyph reference set; callers own color and acceptance policy."""
    def __init__(self, templates, *, smoothing_sigma: float):
        if (isinstance(smoothing_sigma, bool)
                or not isinstance(smoothing_sigma, (int, float))
                or not isfinite(smoothing_sigma) or not 0 < smoothing_sigma <= 4):
            raise ValueError("digit smoothing sigma must be in (0, 4]")
        self._templates = templates
        self._smoothing_sigma = float(smoothing_sigma)
        self._padding = ceil(4*self._smoothing_sigma)+2
        self._variants = None
        self._references: _DigitReferences | None = None

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
    def _normalize_features(fields: np.ndarray) -> np.ndarray:
        flat = fields.reshape(len(fields), -1).astype(np.float32)
        flat -= flat.mean(axis=1, keepdims=True)
        norm = np.sqrt(np.einsum("np,np->n", flat, flat, dtype=np.float64))
        return np.divide(flat, norm[:, None], out=np.zeros_like(flat), where=norm[:, None] > 1e-12)

    def _blur(self, mask: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(mask.astype(np.float32), (0, 0), self._smoothing_sigma,
                                borderType=cv2.BORDER_CONSTANT)

    def _prepare_references(self, variants) -> _DigitReferences:
        fields, widths, groups = [], [], []
        p = self._padding
        for digit, items in variants.items():
            start = len(fields)
            for mask, width in items:
                mask = (mask > 0).astype(np.uint8)
                height, columns = mask.shape
                field = self._blur(np.pad(mask, p))
                fields.append(field[p:p+height, p:p+columns])
                widths.append(width)
            groups.append((digit, start, len(fields)))
        features = (self._normalize_features(np.stack(fields)) if fields
                    else np.empty((0, 48*32), np.float32))
        reference_widths = np.asarray(widths, dtype=np.int64)
        features.setflags(write=False)
        reference_widths.setflags(write=False)
        return _DigitReferences(features, reference_widths, tuple(groups))

    def _reference_features(self, variants) -> _DigitReferences:
        # Only repository-owned, immutable references survive this call. Foreign
        # candidates may be mutable and must not replace the shared reference set.
        if self._variants is not None and variants is self._variants[0]:
            if self._references is None:
                self._references = self._prepare_references(variants)
            return self._references
        return self._prepare_references(variants)

    def _score_matrix(self, glyph: np.ndarray, references: _DigitReferences) -> np.ndarray:
        normalized = self._normalize_glyph(glyph)
        height, width = normalized.shape
        p = self._padding
        # Blur once with zero support, then sample the same nine translations.
        # This retains edge evidence when a shifted foreground reaches the canvas.
        field = self._blur(np.pad(normalized, p))
        shifts = np.stack([field[p-dy:p+height-dy, p-dx:p+width-dx]
                           for dy in (-1, 0, 1) for dx in (-1, 0, 1)])
        features = self._normalize_features(shifts)
        return np.clip(np.einsum("qp,rp->qr", features, references.features,
                                 dtype=np.float64, optimize=False), 0., 1.)

    def _ranked_scores(self, glyph, variants, *, width_scale=1.):
        references = self._reference_features(variants)
        values = self._score_matrix(glyph, references).max(axis=0)
        _, xs = np.nonzero(glyph)
        width = int(xs.max()-xs.min()+1) if len(xs) else 0
        errors = np.abs(width-np.maximum(1, np.rint(references.widths*width_scale).astype(np.int64)))
        ranked = []
        for digit, start, end in references.groups:
            if start == end:
                ranked.append((0., 2**31-1, digit))
                continue
            score = float(values[start:end].max())
            error = int(errors[start:end][values[start:end] == score].min())
            ranked.append((score, error, digit))
        return sorted(ranked, key=lambda row: (-row[0], row[1], row[2]))

    def best_match(
        self,
        glyph: np.ndarray,
        template_variants: dict[str, tuple[tuple[np.ndarray, int], ...]],
        *, width_scale: float = 1.,
    ) -> _DigitMatch:
        class_scores = self._ranked_scores(glyph, template_variants, width_scale=width_scale)
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
        self._variants = template_variants, tuple(template_widths)
        return self._variants
