from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from e7auto.core.types import Point, Rect, Size
from e7auto.core.recognition import ComponentLayout

@dataclass(frozen=True, slots=True)
class DisplayConfig:
    reference_mode: Size
    minimum_mode: Size
    client_width_fraction: float


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
    capture_frame_timeout_ms: int = 1000
    capture_recovery_timeout_ms: int = 3000
    capture_rebuild_cooldown_ms: int = 60000
    capture_rebuild_window_ms: int = 600000
    capture_rebuild_max_attempts: int = 3


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
    maximum_horizontal_shift_px: float = 4.0
    minimum_overlap_fraction: float = 0.50
    minimum_content_score: float = 0.75
    minimum_content_stddev: float = 8.0
    content_alignment_tolerance_px: float = 4.0


@dataclass(frozen=True, slots=True)
class LoggingConfig:
    keep_days: int = 7
    keep_runs: int = 20
    max_file_mb: int = 10
    backup_count: int = 3
    max_total_mb: int = 500


@dataclass(frozen=True, slots=True)
class EntryThresholds:
    color: float
    structure: float


@dataclass(frozen=True, slots=True)
class AppConfig:
    source_path: Path
    executable_path: Path
    process_name: str
    window_title: str
    baseline_client_size: Size
    display: DisplayConfig
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
    entry_thresholds: dict[str, EntryThresholds]
    penguin_control_confidence: float
    penguin_price_digit_confidence: float
    penguin_price_digit_margin: float
    purchased_button_padding: Point
    penguin_price_rect: Rect
    sky_stone_digit_confidence: float
    sky_stone_digit_margin: float
    sky_stone_digits_offset: Point | None
    overlay_offset: Point
    logging: LoggingConfig
    template_manifest_paths: dict[str, Path] = field(default_factory=dict)
    network_error_template: str = "network_connection_abnormal"
    network_retry_template: str = "network_retry"
    candidate_cache_mib: int = 64
    component_layouts: dict[str, ComponentLayout] = field(default_factory=dict)


class ConfigError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(errors))
