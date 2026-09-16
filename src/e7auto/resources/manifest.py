"""Shared, read-only template catalog loading; no runtime search parameters."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct


def load_template_manifest(
    path: Path, baseline: tuple[int, int], *, prefix: str = "",
) -> tuple[dict[str, Path], dict[str, tuple[int, int]]]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest["schema_version"] != 1 or manifest["baseline"] != list(baseline):
            raise ValueError("template calibration baseline mismatch")
        entries = manifest["templates"]
        if not isinstance(entries, dict) or not entries:
            raise ValueError("template catalog must not be empty")
        paths, sizes = {}, {}
        directory = path.resolve().parent
        for name, entry in entries.items():
            if not isinstance(name, str) or not name or not entry.get("source"):
                raise ValueError(f"template name/source is required: {name}")
            candidate = (directory / entry["file"]).resolve()
            if not candidate.is_relative_to(directory):
                raise ValueError(f"invalid template path: {name}")
            if not candidate.is_file():
                raise ValueError(f"{name} does not exist: {candidate}")
            data = candidate.read_bytes()
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"template integrity mismatch: {name}")
            if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR" or len(data) < 24:
                raise ValueError(f"invalid PNG template: {name}")
            width, height = struct.unpack(">II", data[16:24])
            if not (0 < width <= baseline[0] and 0 < height <= baseline[1]):
                raise ValueError(f"invalid template dimensions: {name}")
            paths[prefix + name] = candidate
            sizes[prefix + name] = (width, height)
        return paths, sizes
    except (OSError, KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError(f"Template manifest {path}: {exc}") from exc
