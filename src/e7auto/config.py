from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )


@dataclass(frozen=True, slots=True)
class Size:
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class TargetConfig:
    target_id: str
    display_name: str
    template: str
    confirm_template: str
    purchased_template: str
    user_selectable: bool = False


@dataclass(frozen=True, slots=True)
class SlotConfig:
    slot_id: str
    screen: str
    order: int
    item_roi: Rect
    buy_point: Point


@dataclass(frozen=True, slots=True)
class TimingConfig:
    poll_interval_ms: int
    entry_timeout_ms: int
    scan_timeout_ms: int
    dialog_timeout_ms: int
    purchase_result_timeout_ms: int
    refresh_timeout_ms: int
    stable_frames: int


@dataclass(frozen=True, slots=True)
class RefreshStrategyConfig:
    batch_refreshes: tuple[int, int, int, int]
    recovery_wait_seconds: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class ScrollConfig:
    cursor_point: Point
    delta: int
    repetitions: int
    interval_ms: int
    settle_ms: int
    minimum_settle_ms: int
    settle_poll_interval_ms: int
    stable_observations: int
    maximum_pairwise_shift_px: float
    minimum_phase_response: float
    downsample_factor: int
    minimum_upward_shift_px: int
    difference_threshold: int
    minimum_changed_fraction: float


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    keep_days: int
    keep_files: int
    profile: str = "detailed"
    max_file_mb: int = 0


@dataclass(frozen=True, slots=True)
class AppConfig:
    source_path: Path
    executable_path: Path
    process_name: str
    window_title: str
    baseline_client_size: Size
    refresh_cost: int
    template_paths: dict[str, Path]
    rois: dict[str, Rect]
    points: dict[str, Point]
    targets: tuple[TargetConfig, ...]
    slots: tuple[SlotConfig, ...]
    scroll: ScrollConfig
    timing: TimingConfig
    refresh_strategy: RefreshStrategyConfig
    default_confidence: float
    anchor_confidence: float
    sky_stone_digit_confidence: float
    sky_stone_digits_offset: Point | None
    overlay_offset: Point
    logging: LoggingConfig
    network_error_template: str = "network_connection_abnormal"
    network_retry_template: str = "network_retry"

    def screen_point(self, client_bounds: Rect, point: Point) -> Point:
        return Point(client_bounds.x + point.x, client_bounds.y + point.y)


class ConfigError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(errors))


_REQUIRED_TEMPLATES = {
    "main_shop_icon",
    "shop_refresh_button",
    "shop_exit_icon",
    "refresh_confirm_prompt",
    "refresh_confirm_button",
    "confirm_button",
    "insufficient_funds",
    "sky_stone_icon",
    *(f"sky_stone_digit_{digit}" for digit in range(10)),
    "sky_stone_digit_0_wide",
}
_REQUIRED_ROIS = {
    "main_shop_icon",
    "shop_refresh_button",
    "shop_exit_icon",
    "refresh_confirm_prompt",
    "refresh_confirm_button",
    "inventory_list",
    "confirm_item",
    "confirm_button",
    "purchase_result",
    "sky_stone_icon",
    "sky_stone_digits",
}
_REQUIRED_POINTS = {
    "shop_icon",
    "shop_exit_button",
    "main_screen_wake",
    "refresh_button",
    "refresh_confirm_button",
    "confirm_button",
}
_EXPECTED_TARGET_POLICY = {
    "covenant_bookmark": False,
    "mystic_medal": False,
    "friendship_points": True,
}


def _positive_int(value: Any, path: str, errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        errors.append(f"{path} must be a positive integer")
        return 1
    return value


def _positive_fraction(value: Any, path: str, errors: list[str]) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < float(value) <= 1
    ):
        errors.append(f"{path} must be in (0, 1]")
        return 1.0
    return float(value)


def _positive_number(value: Any, path: str, errors: list[str]) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or float(value) <= 0
    ):
        errors.append(f"{path} must be a positive number")
        return 1.0
    return float(value)


def _point(value: Any, path: str, errors: list[str]) -> Point:
    if not isinstance(value, dict) or not all(
        isinstance(value.get(k), int) and not isinstance(value.get(k), bool) for k in ("x", "y")
    ):
        errors.append(f"{path} must contain integer x and y")
        return Point(0, 0)
    return Point(value["x"], value["y"])


def _rect(value: Any, path: str, errors: list[str]) -> Rect:
    if not isinstance(value, dict) or not all(
        isinstance(value.get(k), int) and not isinstance(value.get(k), bool)
        for k in ("x", "y", "width", "height")
    ):
        errors.append(f"{path} must contain integer x, y, width, and height")
        return Rect(0, 0, 1, 1)
    width = _positive_int(value["width"], f"{path}.width", errors)
    height = _positive_int(value["height"], f"{path}.height", errors)
    return Rect(value["x"], value["y"], width, height)


def _mapping(value: Any, path: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path} must be a mapping")
        return {}
    return value


def load_config(path: str | Path) -> AppConfig:
    source_path = Path(path).resolve()
    errors: list[str] = []
    try:
        raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError([f"cannot read configuration: {exc}"]) from exc
    root = _mapping(raw, "root", errors)
    if root.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if root.get("calibration_complete") is not True:
        errors.append("calibration_complete must be true after real-machine calibration")

    game = _mapping(root.get("game"), "game", errors)
    executable_path_raw = game.get("executable_path")
    window_title = game.get("window_title")
    if not isinstance(executable_path_raw, str) or not executable_path_raw.strip():
        errors.append("game.executable_path is required")
        executable_path = Path("missing.exe")
    else:
        executable_path = Path(executable_path_raw)
        if (
            executable_path.suffix.casefold() != ".exe"
            or (not executable_path.is_absolute() and executable_path.name != str(executable_path))
        ):
            errors.append(
                "game.executable_path must be an absolute .exe path or an .exe filename"
            )
    process_name = executable_path.name
    if not isinstance(window_title, str) or not window_title.strip():
        errors.append("game.window_title is required")
        window_title = ""
    size_raw = _mapping(game.get("baseline_client_size"), "game.baseline_client_size", errors)
    baseline_size = Size(
        _positive_int(size_raw.get("width"), "game.baseline_client_size.width", errors),
        _positive_int(size_raw.get("height"), "game.baseline_client_size.height", errors),
    )

    economy = _mapping(root.get("economy"), "economy", errors)
    refresh_cost = _positive_int(economy.get("refresh_cost"), "economy.refresh_cost", errors)
    if refresh_cost != 3:
        errors.append("economy.refresh_cost must be the confirmed fixed value 3")

    template_raw = _mapping(root.get("templates"), "templates", errors)
    template_paths: dict[str, Path] = {}
    for key in sorted(_REQUIRED_TEMPLATES | set(template_raw)):
        value = template_raw.get(key)
        if not isinstance(value, str) or not value.strip():
            if key in _REQUIRED_TEMPLATES:
                errors.append(f"templates.{key} is required")
            continue
        candidate = (source_path.parent / value).resolve()
        if not candidate.is_file():
            errors.append(f"templates.{key} does not exist: {candidate}")
        template_paths[key] = candidate

    roi_raw = _mapping(root.get("rois"), "rois", errors)
    rois: dict[str, Rect] = {}
    for key in sorted(_REQUIRED_ROIS | set(roi_raw)):
        if roi_raw.get(key) is None:
            if key in _REQUIRED_ROIS:
                errors.append(f"rois.{key} is required")
            continue
        rois[key] = _rect(roi_raw[key], f"rois.{key}", errors)

    points_raw = _mapping(root.get("points"), "points", errors)
    points: dict[str, Point] = {}
    for key in sorted(_REQUIRED_POINTS | set(points_raw)):
        if points_raw.get(key) is None:
            if key in _REQUIRED_POINTS:
                errors.append(f"points.{key} is required")
            continue
        points[key] = _point(points_raw[key], f"points.{key}", errors)

    targets_raw = root.get("targets")
    targets: list[TargetConfig] = []
    if not isinstance(targets_raw, list) or not targets_raw:
        errors.append("targets must contain at least one calibrated target")
    else:
        for index, item in enumerate(targets_raw):
            item = _mapping(item, f"targets[{index}]", errors)
            target_id = item.get("id")
            display_name = item.get("display_name")
            template = item.get("template")
            confirm_template = item.get("confirm_template")
            purchased_template = item.get("purchased_template")
            user_selectable = item.get("user_selectable", False)
            if not isinstance(target_id, str) or not target_id:
                errors.append(f"targets[{index}].id is required")
                target_id = f"invalid-{index}"
            if not isinstance(display_name, str) or not display_name:
                errors.append(f"targets[{index}].display_name is required")
                display_name = target_id
            if not isinstance(template, str) or template not in template_paths:
                errors.append(f"targets[{index}].template must reference a loaded template")
                template = ""
            if not isinstance(confirm_template, str) or confirm_template not in template_paths:
                errors.append(
                    f"targets[{index}].confirm_template must reference a loaded template"
                )
                confirm_template = ""
            if not isinstance(purchased_template, str) or purchased_template not in template_paths:
                errors.append(
                    f"targets[{index}].purchased_template must reference a loaded template"
                )
                purchased_template = ""
            if not isinstance(user_selectable, bool):
                errors.append(f"targets[{index}].user_selectable must be a boolean")
                user_selectable = False
            targets.append(
                TargetConfig(
                    target_id,
                    display_name,
                    template,
                    confirm_template,
                    purchased_template,
                    user_selectable,
                )
            )

    slots_raw = root.get("slots")
    slots: list[SlotConfig] = []
    if not isinstance(slots_raw, list) or not slots_raw:
        errors.append("slots must contain at least one calibrated slot")
    else:
        for index, item in enumerate(slots_raw):
            item = _mapping(item, f"slots[{index}]", errors)
            slot_id = item.get("id")
            screen = item.get("screen")
            order = item.get("order")
            if not isinstance(slot_id, str) or not slot_id:
                errors.append(f"slots[{index}].id is required")
                slot_id = f"invalid-{index}"
            if screen not in {"top", "bottom"}:
                errors.append(f"slots[{index}].screen must be 'top' or 'bottom'")
                screen = "top"
            if not isinstance(order, int) or isinstance(order, bool) or order < 0:
                errors.append(f"slots[{index}].order must be a non-negative integer")
                order = index
            slots.append(
                SlotConfig(
                    slot_id,
                    screen,
                    order,
                    _rect(item.get("item_roi"), f"slots[{index}].item_roi", errors),
                    _point(item.get("buy_point"), f"slots[{index}].buy_point", errors),
                )
            )

    scroll_raw = _mapping(root.get("scroll"), "scroll", errors)
    scroll = ScrollConfig(
        _point(scroll_raw.get("cursor_point"), "scroll.cursor_point", errors),
        scroll_raw.get("delta") if isinstance(scroll_raw.get("delta"), int) else 0,
        _positive_int(scroll_raw.get("repetitions"), "scroll.repetitions", errors),
        _positive_int(scroll_raw.get("interval_ms"), "scroll.interval_ms", errors),
        _positive_int(scroll_raw.get("settle_ms"), "scroll.settle_ms", errors),
        _positive_int(
            scroll_raw.get("minimum_settle_ms"),
            "scroll.minimum_settle_ms",
            errors,
        ),
        _positive_int(
            scroll_raw.get("settle_poll_interval_ms"),
            "scroll.settle_poll_interval_ms",
            errors,
        ),
        _positive_int(
            scroll_raw.get("stable_observations"),
            "scroll.stable_observations",
            errors,
        ),
        _positive_number(
            scroll_raw.get("maximum_pairwise_shift_px"),
            "scroll.maximum_pairwise_shift_px",
            errors,
        ),
        _positive_fraction(
            scroll_raw.get("minimum_phase_response"),
            "scroll.minimum_phase_response",
            errors,
        ),
        _positive_int(
            scroll_raw.get("downsample_factor"),
            "scroll.downsample_factor",
            errors,
        ),
        _positive_int(
            scroll_raw.get("minimum_upward_shift_px"),
            "scroll.minimum_upward_shift_px",
            errors,
        ),
        _positive_int(
            scroll_raw.get("difference_threshold"),
            "scroll.difference_threshold",
            errors,
        ),
        _positive_fraction(
            scroll_raw.get("minimum_changed_fraction"),
            "scroll.minimum_changed_fraction",
            errors,
        ),
    )
    if scroll.delta == 0:
        errors.append("scroll.delta must be a non-zero integer")
    if isinstance(scroll_raw.get("delta"), bool):
        errors.append("scroll.delta must not be a boolean")
    if scroll.minimum_settle_ms >= scroll.settle_ms:
        errors.append("scroll.minimum_settle_ms must be less than scroll.settle_ms")
    if scroll.settle_poll_interval_ms > scroll.settle_ms - scroll.minimum_settle_ms:
        errors.append(
            "scroll.settle_poll_interval_ms must fit between minimum_settle_ms and settle_ms"
        )
    if scroll.maximum_pairwise_shift_px >= scroll.minimum_upward_shift_px:
        errors.append(
            "scroll.maximum_pairwise_shift_px must be less than minimum_upward_shift_px"
        )

    timing_raw = _mapping(root.get("timing"), "timing", errors)
    timing = TimingConfig(
        *(
            _positive_int(timing_raw.get(name), f"timing.{name}", errors)
            for name in (
                "poll_interval_ms",
                "entry_timeout_ms",
                "scan_timeout_ms",
                "dialog_timeout_ms",
                "purchase_result_timeout_ms",
                "refresh_timeout_ms",
                "stable_frames",
            )
        )
    )

    strategy_raw = _mapping(root.get("refresh_strategy"), "refresh_strategy", errors)

    def positive_integer_tuple(name: str, length: int) -> tuple[int, ...]:
        raw = strategy_raw.get(name)
        if not isinstance(raw, list) or len(raw) != length:
            errors.append(f"refresh_strategy.{name} must contain exactly {length} integers")
            return tuple(1 for _ in range(length))
        return tuple(
            _positive_int(value, f"refresh_strategy.{name}[{index}]", errors)
            for index, value in enumerate(raw)
        )

    refresh_strategy = RefreshStrategyConfig(
        positive_integer_tuple("batch_refreshes", 4),  # type: ignore[arg-type]
        positive_integer_tuple("recovery_wait_seconds", 3),  # type: ignore[arg-type]
    )
    expected_refresh_strategy = RefreshStrategyConfig((13, 13, 13, 10), (5, 180, 5))
    if refresh_strategy != expected_refresh_strategy:
        errors.append(
            "refresh_strategy must use batch_refreshes [13, 13, 13, 10] "
            "and recovery_wait_seconds [5, 180, 5]"
        )

    vision_raw = _mapping(root.get("vision"), "vision", errors)
    default_confidence = vision_raw.get("default_confidence")
    anchor_confidence = vision_raw.get("anchor_confidence")
    sky_stone_digit_confidence = vision_raw.get("sky_stone_digit_confidence")
    sky_stone_digits_offset = _point(
        vision_raw.get("sky_stone_digits_offset"),
        "vision.sky_stone_digits_offset",
        errors,
    )
    for name, value in (
        ("default_confidence", default_confidence),
        ("anchor_confidence", anchor_confidence),
        ("sky_stone_digit_confidence", sky_stone_digit_confidence),
    ):
        if not isinstance(value, (int, float)) or not 0 < float(value) <= 1:
            errors.append(f"vision.{name} must be in (0, 1]")
    default_confidence = float(default_confidence) if isinstance(default_confidence, (int, float)) else 1.0
    anchor_confidence = float(anchor_confidence) if isinstance(anchor_confidence, (int, float)) else 1.0
    sky_stone_digit_confidence = (
        float(sky_stone_digit_confidence)
        if isinstance(sky_stone_digit_confidence, (int, float))
        else 1.0
    )

    overlay_raw = _mapping(root.get("overlay"), "overlay", errors)
    overlay_offset = _point(overlay_raw.get("offset"), "overlay.offset", errors)

    logging_raw = _mapping(root.get("logging"), "logging", errors)
    profile = logging_raw.get("profile", "detailed")
    if profile not in {"detailed", "compact"}:
        errors.append("logging.profile must be 'detailed' or 'compact'")
        profile = "detailed"
    max_file_mb = logging_raw.get("max_file_mb", 0)
    if not isinstance(max_file_mb, int) or max_file_mb < 0:
        errors.append("logging.max_file_mb must be a non-negative integer")
        max_file_mb = 0
    logging_config = LoggingConfig(
        _positive_int(logging_raw.get("keep_days"), "logging.keep_days", errors),
        _positive_int(logging_raw.get("keep_files"), "logging.keep_files", errors),
        profile,
        max_file_mb,
    )

    def point_in_client(point: Point) -> bool:
        return 0 <= point.x < baseline_size.width and 0 <= point.y < baseline_size.height

    def rect_in_client(rect: Rect) -> bool:
        return (
            rect.x >= 0
            and rect.y >= 0
            and rect.right <= baseline_size.width
            and rect.bottom <= baseline_size.height
        )

    for key, roi in rois.items():
        if not rect_in_client(roi):
            errors.append(f"rois.{key} must fit inside the baseline client")
    for key, point in points.items():
        if not point_in_client(point):
            errors.append(f"points.{key} must fit inside the baseline client")
    for index, slot in enumerate(slots):
        if not rect_in_client(slot.item_roi):
            errors.append(f"slots[{index}].item_roi must fit inside the baseline client")
        if not point_in_client(slot.buy_point):
            errors.append(f"slots[{index}].buy_point must fit inside the baseline client")
    if not point_in_client(scroll.cursor_point):
        errors.append("scroll.cursor_point must fit inside the baseline client")
    inventory_roi = rois.get("inventory_list")
    if inventory_roi is not None and not (
        inventory_roi.x <= scroll.cursor_point.x < inventory_roi.right
        and inventory_roi.y <= scroll.cursor_point.y < inventory_roi.bottom
    ):
        errors.append("scroll.cursor_point must be inside rois.inventory_list")

    target_ids = [target.target_id for target in targets]
    if len(set(target_ids)) != len(target_ids):
        errors.append("target ids must be unique")
    actual_target_policy = {
        target.target_id: target.user_selectable for target in targets
    }
    if actual_target_policy != _EXPECTED_TARGET_POLICY:
        errors.append(
            "targets must be exactly covenant_bookmark and mystic_medal as mandatory, "
            "plus friendship_points as user-selectable"
        )
    slot_ids = [slot.slot_id for slot in slots]
    slot_orders = [(slot.screen, slot.order) for slot in slots]
    if len(set(slot_ids)) != len(slot_ids):
        errors.append("slot ids must be unique")
    if len(set(slot_orders)) != len(slot_orders):
        errors.append("slot order must be unique within each screen")
    if slots and {slot.screen for slot in slots} != {"top", "bottom"}:
        errors.append("slots must include calibrated entries for both top and bottom screens")

    if errors:
        raise ConfigError(errors)
    return AppConfig(
        source_path=source_path,
        executable_path=executable_path,
        process_name=process_name,
        window_title=window_title,
        baseline_client_size=baseline_size,
        refresh_cost=refresh_cost,
        template_paths=template_paths,
        rois=rois,
        points=points,
        targets=tuple(targets),
        slots=tuple(sorted(slots, key=lambda item: item.order)),
        scroll=scroll,
        timing=timing,
        refresh_strategy=refresh_strategy,
        default_confidence=default_confidence,
        anchor_confidence=anchor_confidence,
        sky_stone_digit_confidence=sky_stone_digit_confidence,
        sky_stone_digits_offset=sky_stone_digits_offset,
        overlay_offset=overlay_offset,
        logging=logging_config,
    )
