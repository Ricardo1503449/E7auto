"""Lossless PNG I/O for explicitly supplied offline calibration images."""
from pathlib import Path

import cv2
import numpy as np


def read_png(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.size == 0:
        raise RuntimeError(f"Cannot decode PNG: {path}")
    return image


def read_rgba_png(path: Path) -> np.ndarray:
    image = read_png(path)
    if image.ndim != 3 or image.shape[2] != 4:
        raise RuntimeError(f"Expected an RGBA PNG: {path}")
    return image


def read_color_rgba_png(path: Path) -> np.ndarray:
    image = read_png(path)
    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise RuntimeError(f"Expected a color PNG: {path}")
    if image.shape[2] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)
    return np.ascontiguousarray(image)


def write_png(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise RuntimeError(f"Cannot encode PNG: {path}")
    encoded.tofile(path)
