from __future__ import annotations
import cv2
import numpy as np
from e7auto.core.types import Point, Rect
from e7auto.vision.frames import AdaptedFrame
from e7auto.core.ports import Frame
from e7auto.core.observations import Observation
from e7auto.resources.templates import TemplateRepository

class TemplateMatcher:
    """Array-only matching, independent of any feature flow."""
    def __init__(self, templates: TemplateRepository):
        self._templates = templates

    @staticmethod
    def prepare_bgr(frame: Frame | AdaptedFrame) -> Frame | AdaptedFrame:
        if isinstance(frame, AdaptedFrame):
            return frame
        if frame.ndim != 3 or frame.shape[2] not in (3, 4):
            raise ValueError("Frame must be an HxWx3 or HxWx4 uint8 array")
        return np.ascontiguousarray(frame[:, :, :3])


    @staticmethod
    def crop(frame: Frame | AdaptedFrame, roi: Rect) -> Frame:
        if isinstance(frame, AdaptedFrame):
            return frame.normalized_roi(roi)
        height, width = frame.shape[:2]
        if roi.x < 0 or roi.y < 0 or roi.right > width or roi.bottom > height:
            raise ValueError(f"ROI outside frame: {roi} vs {width}x{height}")
        return frame[roi.y : roi.bottom, roi.x : roi.right]


    def match(self, frame: Frame, template_key: str, roi: Rect, threshold: float) -> Observation | None:
        return self.match_bgr(self.prepare_bgr(frame), template_key, roi, threshold)


    def match_entry(
        self, frame: Frame, template_key: str, roi: Rect, threshold: float,
        *, structure_threshold: float,
    ) -> Observation | None:
        """Require color and mean-subtracted structure at the same location."""
        return self.match_bgr(
            self.prepare_bgr(frame), template_key, roi, threshold, structure_threshold=structure_threshold,
        )


    def match_bgr(
        self,
        bgr_frame: Frame | AdaptedFrame,
        template_key: str,
        roi: Rect,
        threshold: float,
        *,
        structure_threshold: float | None = None,
    ) -> Observation | None:
        """Match against an already prepared contiguous BGR frame.

        Inventory scanning performs many template comparisons against one captured
        frame.  Preparing the captured BGRA frame once avoids copying the full client
        image for every individual comparison while preserving the public
        single-match path above.
        """

        source = self.crop(bgr_frame, roi)
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
        if structure_threshold is not None:
            # Keep all color-qualified candidates: the best color match can be
            # wallpaper while a slightly lower-scoring candidate is the icon.
            color_scores = result if template_data.mask is None else 1.0 - result
            candidates = np.isfinite(color_scores) & (color_scores >= threshold)
            if not np.any(candidates):
                return None
            mask = template_data.mask
            pixels = template.reshape(-1, 3) if mask is None else template[mask > 0]
            if not pixels.size or not np.any(np.ptp(pixels, axis=0)):
                return None  # A constant template has no structure to verify.
            structure = cv2.matchTemplate(
                source, template, cv2.TM_CCOEFF_NORMED, mask=mask,
            )
            # CCOEFF removes each channel's mean before comparing spatial
            # variation. Constant patches can yield NaN/Inf; fail closed.
            candidates &= np.isfinite(structure) & (structure >= structure_threshold)
            if not np.any(candidates):
                return None
            qualified_scores = np.where(candidates, color_scores, -np.inf)
            _, confidence, _, location = cv2.minMaxLoc(qualified_scores)
        confidence = max(0.0, min(1.0, float(confidence)))
        if float(confidence) < threshold:
            return None
        anchor = Point(
            roi.x + location[0] + template.shape[1] // 2,
            roi.y + location[1] + template.shape[0] // 2,
        )
        return Observation(template_key, float(confidence), roi, anchor)
