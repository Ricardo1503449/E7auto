import numpy as np

from .paths import FIXTURES_DIR


def readiness_frame(key: str) -> np.ndarray:
    """Lossless relevant regions on a synthetic client canvas; no invented game pixels."""
    frame = np.zeros((1306, 2322, 3), np.uint8)
    with np.load(FIXTURES_DIR / "shop/inventory_readiness.npz") as images:
        frame[90:1290, 950:1850] = images[key + "_content"]
        frame[1125:1275, 105:685] = images[key + "_button"]
    return frame


def viewport_frame() -> np.ndarray:
    frame = np.zeros((1306, 2322, 3), np.uint8)
    with np.load(FIXTURES_DIR / "shop/inventory_viewport_latest.npz") as images:
        frame[90:1290, 950:1850] = images["content"]
        frame[1125:1275, 105:685] = images["button"]
    return frame
