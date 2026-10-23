"""Derive each viewport's complete rows from the calibrated inventory grid."""
from __future__ import annotations

from dataclasses import dataclass

from e7auto.core.types import Rect
from .models import SlotConfig


@dataclass(frozen=True, slots=True)
class ViewportRow:
    row_id: str
    roi: Rect


def visible_shop_rows(slots: tuple[SlotConfig, ...], content: Rect, screen: str) -> tuple[ViewportRow, ...]:
    if screen not in {"top", "bottom"}:
        raise ValueError(f"Unknown inventory screen: {screen}")
    top = sorted((slot for slot in slots if slot.screen == "top"), key=lambda slot: slot.order)
    if len(top) < 2:
        raise ValueError("viewport layout requires two top row anchors")
    first, second = top[:2]
    order_span = second.order - first.order
    if order_span <= 0:
        raise ValueError("viewport row orders must be distinct")
    pitch, remainder = divmod(second.item_roi.y - first.item_roi.y, order_span)
    if remainder or pitch < first.item_roi.height or pitch <= 0:
        raise ValueError("viewport row pitch must be integral and rows must not overlap")
    orders = sorted({slot.order for slot in slots})
    if orders[0] != 0 or orders[-1] + 1 != len(orders):
        raise ValueError("viewport row orders must cover a contiguous inventory from zero")
    groups = {}
    for view in ("top", "bottom"):
        group = sorted((slot for slot in slots if slot.screen == view), key=lambda slot: slot.order)
        if not group:
            raise ValueError(f"viewport layout is missing {view} anchors")
        anchor = group[0]
        seen = set()
        for slot in group:
            rect = slot.item_roi
            if slot.order in seen:
                raise ValueError("viewport row orders must be distinct within each screen")
            seen.add(slot.order)
            if ((rect.x, rect.width, rect.height) != (first.item_roi.x, first.item_roi.width, first.item_roi.height)
                    or rect.y != anchor.item_roi.y + (slot.order - anchor.order) * pitch):
                raise ValueError("viewport anchors must share the calibrated icon geometry and row pitch")
            if not (content.x <= rect.x < rect.right <= content.right
                    and content.y <= rect.y < rect.bottom <= content.bottom):
                raise ValueError("viewport anchors must fit inside inventory_content")
        groups[view] = anchor
    anchor = groups[screen]
    rows = []
    for order in orders:
        rect = Rect(anchor.item_roi.x, anchor.item_roi.y + (order - anchor.order) * pitch,
                    anchor.item_roi.width, anchor.item_roi.height)
        # Visibility is decided on the complete icon, before any pixel inset.
        if content.x <= rect.x and rect.right <= content.right and content.y <= rect.y and rect.bottom <= content.bottom:
            rows.append(ViewportRow(f"{screen}_{order + 1}", rect))
    if not rows:
        raise ValueError("viewport has no complete visible rows")
    return tuple(rows)
