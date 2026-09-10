from pathlib import Path
import argparse

import cv2
import numpy as np

from scripts.common.image_io import write_png


def convert(path: Path) -> None:
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
    write_png(path, rgba)
    print(path.name, rgba.shape)


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert supplied network crops to alpha-masked text in place")
    parser.add_argument("--template-dir", type=Path, required=True)
    args = parser.parse_args()
    for filename in ("network_connection_abnormal.png", "network_retry.png"):
        convert(args.template_dir / filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
