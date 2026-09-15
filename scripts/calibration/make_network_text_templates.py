from pathlib import Path
import argparse
import hashlib
import json

import cv2
import numpy as np

from scripts.common.candidates import write_candidate_png as write_png
from scripts.common.paths import ensure_candidate_path
from scripts.common.paths import calibration_output_dirs, template_relative_path


def convert(path: Path, output_path: Path | None = None) -> None:
    if output_path is None:
        output_dir, _ = calibration_output_dirs()
        output_path = output_dir / template_relative_path(path.name, feature="common")
    if path.resolve() == output_path.resolve():
        raise ValueError("Candidate conversion must not overwrite its source")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"cannot read {path}")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    # The modal text is neutral white; the shop backdrop is dark and saturated.
    neutral = hsv[:, :, 1] <= 55
    value = hsv[:, :, 2].astype(np.float32)
    alpha = np.clip((value - 85.0) * 3.0, 0, 255).astype(np.uint8)
    alpha[~neutral] = 0
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    ys, xs = np.nonzero(alpha > 8)
    if len(xs) == 0:
        raise SystemExit(f"no text pixels found in {path}")
    pad = 3
    x0, x1 = max(0, int(xs.min()) - pad), min(image.shape[1], int(xs.max()) + pad + 1)
    y0, y1 = max(0, int(ys.min()) - pad), min(image.shape[0], int(ys.max()) + pad + 1)
    rgba = cv2.cvtColor(image[y0:y1, x0:x1], cv2.COLOR_BGR2BGRA)
    rgba[:, :, 3] = alpha[y0:y1, x0:x1]
    write_png(output_path, rgba)
    ensure_candidate_path(output_path.with_suffix(".source.json")).write_text(json.dumps({
        "source_path": str(path.resolve()),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "crop": {"x": x0, "y": y0, "width": x1-x0, "height": y1-y0},
        "method": "alpha-masked neutral text",
    }, indent=2) + "\n", encoding="utf-8")
    print(output_path, rgba.shape)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export alpha-masked candidates from supplied network crops")
    parser.add_argument("--template-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output_dir, _ = calibration_output_dirs(args.output_dir)
    for filename in ("network_connection_abnormal.png", "network_retry.png"):
        convert(args.template_dir / filename, output_dir / template_relative_path(filename, feature="common"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
