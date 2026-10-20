"""Candidate PNGs carry a checksum separate from historical source formats."""
from pathlib import Path
import hashlib
import json

import cv2
import numpy as np

from scripts.common.image_io import write_png
from scripts.common.paths import ensure_candidate_path


def write_candidate_png(path: Path, image: np.ndarray, *, png_bytes: bytes | None = None) -> None:
    path = ensure_candidate_path(path)
    if png_bytes is None:
        write_png(path, image)
    else:
        decoded = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
        if not png_bytes.startswith(b"\x89PNG\r\n\x1a\n") or decoded is None or not np.array_equal(decoded, image):
            raise ValueError("Preserved PNG bytes do not match candidate pixels")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes)
    metadata = {
        "schema_version": 1,
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": [int(image.shape[1]), int(image.shape[0])],
    }
    ensure_candidate_path(path.with_suffix(".candidate.json")).write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8",
    )
