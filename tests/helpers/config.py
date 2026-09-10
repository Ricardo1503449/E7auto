from __future__ import annotations

from pathlib import Path

from e7auto.config import (
    AppConfig,
    DisplayConfig,
    LoggingConfig,
    Point,
    Rect,
    RefreshStrategyConfig,
    ScrollConfig,
    Size,
    SlotConfig,
    TargetConfig,
    TimingConfig,
)


def make_config(
    *, refresh_cost: int = 3, stable_frames: int = 1, include_friendship: bool = False
) -> AppConfig:
    targets = [
        TargetConfig("wood", "木材", "wood", "wood_confirm", "wood_purchased"),
        TargetConfig("ore", "矿石", "ore", "ore_confirm", "ore_purchased"),
    ]
    if include_friendship:
        targets.append(
            TargetConfig(
                "friendship_points",
                "友情点数",
                "friendship_points",
                "friendship_points_confirm",
                "friendship_points_purchased",
                True,
            )
        )
    return AppConfig(
        source_path=Path("synthetic.yaml"),
        executable_path=Path(r"D:\Games\Fake\Game.exe"),
        process_name="Game.exe",
        window_title="Game Window",
        baseline_client_size=Size(100, 80),
        display=DisplayConfig(Size(400, 400), Size(100, 80), 0.75),
        refresh_cost=refresh_cost,
        template_paths={},
        rois={
            "main_shop_icon": Rect(0, 0, 10, 10),
            "shop_refresh_button": Rect(80, 0, 10, 10),
            "shop_exit_icon": Rect(1, 1, 10, 10),
            "refresh_confirm_prompt": Rect(20, 20, 20, 10),
            "refresh_confirm_button": Rect(40, 40, 20, 10),
            "inventory_list": Rect(10, 10, 70, 50),
            "confirm_item": Rect(20, 20, 20, 20),
            "confirm_button": Rect(40, 40, 20, 10),
            "purchase_result": Rect(30, 20, 40, 20),
            "sky_stone_icon": Rect(60, 0, 10, 10),
            "sky_stone_digits": Rect(70, 0, 30, 10),
        },
        points={
            "shop_icon": Point(5, 5),
            "shop_exit_button": Point(6, 6),
            "main_screen_wake": Point(50, 40),
            "refresh_button": Point(90, 70),
            "refresh_confirm_button": Point(55, 45),
            "confirm_button": Point(50, 45),
        },
        targets=tuple(targets),
        slots=(
            SlotConfig("top-1", "top", 0, Rect(10, 10, 20, 20), Point(30, 20)),
            SlotConfig("top-2", "top", 1, Rect(40, 10, 20, 20), Point(60, 20)),
            SlotConfig("bottom-1", "bottom", 0, Rect(10, 35, 20, 20), Point(30, 45)),
        ),
        scroll=ScrollConfig(
            Point(50, 40),
            -120,
            1,
            100,
            800,
            200,
            100,
            2,
            1.0,
            0.80,
            4,
            300,
            8,
            0.30,
        ),
        timing=TimingConfig(10, 30, 30, 30, 30, 30, stable_frames),
        refresh_strategy=RefreshStrategyConfig((13, 13, 13, 10), (5, 180, 5)),
        default_confidence=0.9,
        anchor_confidence=0.93,
        sky_stone_digit_confidence=0.8,
        sky_stone_digit_margin=0.08,
        sky_stone_digits_offset=None,
        overlay_offset=Point(7, 9),
        logging=LoggingConfig(14, 100),
    )
