from __future__ import annotations
from pathlib import Path
from typing import Any, Literal
import yaml
from e7auto.core.types import Point, Rect, Size
from e7auto.resources.manifest import load_template_manifest
from e7auto.configuration.models import DisplayConfig, TargetConfig, SlotConfig, TimingConfig, RefreshStrategyConfig, ScrollConfig, LoggingConfig, EntryThresholds, AppConfig, ConfigError
from e7auto.configuration.schema import COMMON_TEMPLATE_KEYS, _REQUIRED_TEMPLATES, _REQUIRED_ROIS, PENGUIN_CONTROLS, _REQUIRED_POINTS, _EXPECTED_TARGET_POLICY

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


def _size(value: Any, path: str, errors: list[str]) -> Size:
    raw = _mapping(value, path, errors)
    return Size(
        _positive_int(raw.get("width"), f"{path}.width", errors),
        _positive_int(raw.get("height"), f"{path}.height", errors),
    )


def _mapping(value: Any, path: str, errors: list[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        errors.append(f"{path} must be a mapping")
        return {}
    return value


def load_config(
    path: str | Path, *, template_profile: Literal["shop", "penguin"] = "shop"
) -> AppConfig:
    if template_profile not in {"shop", "penguin"}:
        raise ConfigError([f"unknown template profile: {template_profile}"])
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
    baseline_size = _size(game.get("baseline_client_size"), "game.baseline_client_size", errors)
    display_raw = _mapping(root.get("display"), "display", errors)
    display = DisplayConfig(
        reference_mode=_size(display_raw.get("reference_mode"), "display.reference_mode", errors),
        minimum_mode=_size(display_raw.get("minimum_mode"), "display.minimum_mode", errors),
        client_width_fraction=_positive_fraction(
            display_raw.get("client_width_fraction"),
            "display.client_width_fraction",
            errors,
        ),
    )
    expected_display = DisplayConfig(Size(3120, 2080), Size(2560, 1440), 0.60)
    if display != expected_display:
        errors.append(
            "display must use reference_mode 3120x2080, minimum_mode 2560x1440, "
            "and client_width_fraction 0.60"
        )

    economy = _mapping(root.get("economy"), "economy", errors)
    refresh_cost = _positive_int(economy.get("refresh_cost"), "economy.refresh_cost", errors)
    if refresh_cost != 3:
        errors.append("economy.refresh_cost must be the confirmed fixed value 3")

    # Production features use the same catalog loader. Explicit path registries
    # remain supported for small standalone/synthetic configurations.
    catalogs_raw = root.get("template_manifests")
    template_raw = _mapping(root.get("templates", {}), "templates", errors)
    template_paths: dict[str, Path] = {}
    template_manifest_paths: dict[str, Path] = {}
    template_sizes: dict[str, tuple[int, int]] = {}
    if catalogs_raw is not None:
        if template_raw:
            errors.append("templates and template_manifests cannot both register filenames")
        catalogs = _mapping(catalogs_raw, "template_manifests", errors)
        for profile in ("common", "shop", "penguin"):
            value = catalogs.get(profile)
            if not isinstance(value, str) or not value:
                errors.append(f"template_manifests.{profile} is required")
                continue
            template_manifest_paths[profile] = (source_path.parent / value).resolve()
        for profile in (("common", "shop") if template_profile == "shop" else ("common",)):
            if profile not in template_manifest_paths:
                continue
            try:
                registered, sizes = load_template_manifest(
                    template_manifest_paths[profile], (baseline_size.width, baseline_size.height),
                )
                if template_paths.keys() & registered.keys():
                    errors.append(f"duplicate template keys in {profile} manifest")
                template_paths.update(registered)
                template_sizes.update(sizes)
            except ValueError as exc:
                errors.append(str(exc))
    required_templates = (
        COMMON_TEMPLATE_KEYS if template_profile == "penguin" else _REQUIRED_TEMPLATES
    )
    selected_templates = COMMON_TEMPLATE_KEYS if template_profile == "penguin" else set(template_raw)
    for key in sorted((required_templates | selected_templates) if catalogs_raw is None else ()):
        value = template_raw.get(key)
        if not isinstance(value, str) or not value.strip():
            if key in required_templates:
                errors.append(f"templates.{key} is required")
            continue
        candidate = (source_path.parent / value).resolve()
        if not candidate.is_file():
            errors.append(f"templates.{key} does not exist: {candidate}")
        template_paths[key] = candidate
    for key in sorted(required_templates - template_paths.keys()):
        errors.append(f"templates.{key} is required")

    roi_raw = _mapping(root.get("rois"), "rois", errors)
    rois: dict[str, Rect] = {}
    required_rois = _REQUIRED_ROIS | (
        {f"penguin_{name}" for name in PENGUIN_CONTROLS if name != "sanctuary_entry"}
        if template_profile == "penguin" else set()
    )
    for key in sorted(required_rois | set(roi_raw)):
        if roi_raw.get(key) is None:
            if key in required_rois:
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

    # Keep validating the shared configuration structure, but penguin startup
    # must not read or require the shop's image files.
    target_templates = (
        {name + suffix for name in _EXPECTED_TARGET_POLICY for suffix in ("", "_confirm", "_purchased")}
        if template_profile == "penguin" else template_paths
    )
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
            if not isinstance(template, str) or template not in target_templates:
                errors.append(f"targets[{index}].template must reference a loaded template")
                template = ""
            if not isinstance(confirm_template, str) or confirm_template not in target_templates:
                errors.append(
                    f"targets[{index}].confirm_template must reference a loaded template"
                )
                confirm_template = ""
            if not isinstance(purchased_template, str) or purchased_template not in target_templates:
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
    entry_raw = _mapping(vision_raw.get("entry_thresholds"), "vision.entry_thresholds", errors)
    entry_thresholds: dict[str, EntryThresholds] = {}
    for feature in ("shop", "penguin"):
        values = _mapping(entry_raw.get(feature), f"vision.entry_thresholds.{feature}", errors)
        parsed = {}
        for name in ("color", "structure"):
            value = values.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
                errors.append(f"vision.entry_thresholds.{feature}.{name} must be in (0, 1]")
                value = 1.0
            parsed[name] = float(value)
        entry_thresholds[feature] = EntryThresholds(**parsed)
    penguin_control_confidence = vision_raw.get("penguin_control_confidence")
    if (isinstance(penguin_control_confidence, bool)
            or not isinstance(penguin_control_confidence, (int, float))
            or not 0 < penguin_control_confidence <= 1):
        errors.append("vision.penguin_control_confidence must be in (0, 1]")
        penguin_control_confidence = 1.0
    sky_stone_digit_confidence = vision_raw.get("sky_stone_digit_confidence")
    sky_stone_digit_margin = vision_raw.get("sky_stone_digit_margin")
    penguin_price_digit_confidence = vision_raw.get("penguin_price_digit_confidence")
    penguin_price_digit_margin = vision_raw.get("penguin_price_digit_margin")
    purchased_button_padding = _point(
        vision_raw.get("purchased_button_padding"), "vision.purchased_button_padding", errors,
    )
    if purchased_button_padding.x < 0 or purchased_button_padding.y < 0:
        errors.append("vision.purchased_button_padding must be non-negative")
    penguin_price_rect = _rect(
        vision_raw.get("penguin_price_rect"), "vision.penguin_price_rect", errors,
    )
    if penguin_price_rect.x < 0 or penguin_price_rect.y < 0:
        errors.append("vision.penguin_price_rect offsets must be non-negative")
    sky_stone_digits_offset = _point(
        vision_raw.get("sky_stone_digits_offset"),
        "vision.sky_stone_digits_offset",
        errors,
    )
    for name, value in (
        ("default_confidence", default_confidence),
        ("anchor_confidence", anchor_confidence),
        ("sky_stone_digit_confidence", sky_stone_digit_confidence),
        ("sky_stone_digit_margin", sky_stone_digit_margin),
        ("penguin_price_digit_confidence", penguin_price_digit_confidence),
        ("penguin_price_digit_margin", penguin_price_digit_margin),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < float(value) <= 1:
            errors.append(f"vision.{name} must be in (0, 1]")
    default_confidence = float(default_confidence) if isinstance(default_confidence, (int, float)) else 1.0
    anchor_confidence = float(anchor_confidence) if isinstance(anchor_confidence, (int, float)) else 1.0
    sky_stone_digit_confidence = (
        float(sky_stone_digit_confidence)
        if isinstance(sky_stone_digit_confidence, (int, float))
        else 1.0
    )
    sky_stone_digit_margin = (
        float(sky_stone_digit_margin)
        if isinstance(sky_stone_digit_margin, (int, float))
        else 1.0
    )

    overlay_raw = _mapping(root.get("overlay"), "overlay", errors)
    overlay_offset = _point(overlay_raw.get("offset"), "overlay.offset", errors)

    logging_raw = _mapping(root.get("logging"), "logging", errors)
    defaults = LoggingConfig()
    logging_config = LoggingConfig(
        **{
            name: _positive_int(logging_raw.get(name, getattr(defaults, name)), f"logging.{name}", errors)
            for name in ("keep_days", "keep_runs", "max_file_mb", "backup_count", "max_total_mb")
        }
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
    for key, (width, height) in template_sizes.items():
        roi_key = "left_icon_column" if key == "main_shop_icon" else key
        roi = rois.get(roi_key)
        if roi is not None and (roi.width < width or roi.height < height):
            errors.append(f"rois.{roi_key} must contain the complete {key} template")
    for key, point in points.items():
        if not point_in_client(point):
            errors.append(f"points.{key} must fit inside the baseline client")
    for index, slot in enumerate(slots):
        if not rect_in_client(slot.item_roi):
            errors.append(f"slots[{index}].item_roi must fit inside the baseline client")
        if not point_in_client(slot.buy_point):
            errors.append(f"slots[{index}].buy_point must fit inside the baseline client")
        if "purchased_button" in template_sizes:
            width, height = template_sizes["purchased_button"]
            width += 2 * purchased_button_padding.x
            height += 2 * purchased_button_padding.y
            button_roi = Rect(slot.buy_point.x - width // 2, slot.buy_point.y - height // 2, width, height)
            if not rect_in_client(button_roi):
                errors.append(f"vision.purchased_button_padding makes slots[{index}] button search exceed the client")
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
        display=display,
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
        entry_thresholds=entry_thresholds,
        penguin_control_confidence=float(penguin_control_confidence),
        penguin_price_digit_confidence=float(penguin_price_digit_confidence),
        penguin_price_digit_margin=float(penguin_price_digit_margin),
        purchased_button_padding=purchased_button_padding,
        penguin_price_rect=penguin_price_rect,
        sky_stone_digit_confidence=sky_stone_digit_confidence,
        sky_stone_digit_margin=sky_stone_digit_margin,
        sky_stone_digits_offset=sky_stone_digits_offset,
        overlay_offset=overlay_offset,
        logging=logging_config,
        template_manifest_paths=template_manifest_paths,
    )
