"""Synthetic template project fixture shared by registration/check tests."""
import numpy as np
import pytest
import yaml
from scripts.common.candidates import write_candidate_png
from scripts.common.image_io import write_png
from scripts.templates import register

@pytest.fixture
def project(tmp_path):
    root = tmp_path / "项目 with spaces"
    (root / "config").mkdir(parents=True)
    (root / "config/internal.yaml").write_text(yaml.safe_dump({
        "game": {"baseline_client_size": {"width": 64, "height": 48}},
        "rois": {"unchanged": [1, 2, 3, 4]},
    }), encoding="utf-8")
    directory = root / "assets/templates/shop"
    entries = {}
    for key in ("entry", "other"):
        image = np.arange(12*10*3, dtype=np.uint8).reshape(12, 10, 3)
        path = directory / f"{key}.png"
        write_png(path, image)
        entries[key] = {"file": path.name, "sha256": register.file_digest(path),
                        "source": {"note": "synthetic fixture"}, "label": key}
    (directory / "manifest.json").write_bytes(register.encoded({
        "schema_version": 1, "baseline": [64, 48], "custom_metadata": {"keep": True}, "templates": entries,
    }))
    candidate = tmp_path / "候选" / "new.png"
    write_candidate_png(candidate, np.full((12, 10, 3), 123, np.uint8))
    source = candidate.parent / "source.yaml"
    source.write_text(yaml.safe_dump({"source": "synthetic test pixels", "crop": [1, 2, 10, 12]}), encoding="utf-8")
    return root, candidate, source
