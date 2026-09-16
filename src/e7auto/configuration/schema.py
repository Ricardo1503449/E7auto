"""Names required by the existing YAML schema; does not select a runtime feature."""
COMMON_TEMPLATE_KEYS = frozenset({
    *(f"sky_stone_digit_{digit}" for digit in range(10)),
    "sky_stone_digit_0_wide",
    "network_connection_abnormal",
    "network_retry",
})


_REQUIRED_TEMPLATES = {
    "main_shop_icon",
    "shop_refresh_button",
    "shop_exit_icon",
    "refresh_confirm_prompt",
    "refresh_confirm_button",
    "confirm_button",
    "insufficient_funds",
    "purchased_button",
    "sky_stone_icon",
    *(f"sky_stone_digit_{digit}" for digit in range(10)),
    "sky_stone_digit_0_wide",
}


_REQUIRED_ROIS = {
    "left_icon_column",
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


PENGUIN_CONTROLS = (
    "sanctuary_entry", "forest_entry", "growth_altar", "penguin_buy_102",
    "penguin_buy_5100", "quantity_max", "penguin_complete", "reward_close",
    "altar_close", "forest_back", "sanctuary_back", "purchase_currency",
    "purchase_cancel",
)


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
