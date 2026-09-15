"""Candidate PNGs carry a checksum separate from historical source formats."""
from pathlib import Path
import hashlib
import json

import numpy as np

from scripts.common.image_io import write_png
from scripts.common.paths import ensure_candidate_path


def write_candidate_png(path: Path, image: np.ndarray) -> None:
    path = ensure_candidate_path(path)
    write_png(path, image)
    metadata = {
        "schema_version": 1,
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": [int(image.shape[1]), int(image.shape[0])],
    }
    ensure_candidate_path(path.with_suffix(".candidate.json")).write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8",
    )
