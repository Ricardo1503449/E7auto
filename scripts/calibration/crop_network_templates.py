from pathlib import Path
import argparse

import cv2

from scripts.common.image_io import write_png
from scripts.common.paths import TEMPLATES_DIR


def crop(source: Path, output_dir: Path, name: str, x: int, y: int, width: int, height: int) -> None:
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"cannot read screenshot: {source}")
    # The screenshot includes the desktop/window chrome. Coordinates below are
    # client-relative after removing the 47px/117px outer frame.
    client_x, client_y = 47, 117
    result = image[client_y + y : client_y + y + height, client_x + x : client_x + x + width]
    if result.shape[:2] != (height, width):
        raise SystemExit(f"crop outside source for {name}: {result.shape}")
    output_dir.mkdir(parents=True, exist_ok=True)
    write_png(output_dir / name, result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Crop network prompts from a supplied reference image")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=TEMPLATES_DIR)
    args = parser.parse_args()
    crop(args.source, args.output_dir, "network_connection_abnormal.png", 900, 545, 540, 105)
    crop(args.source, args.output_dir, "network_retry.png", 1060, 760, 300, 120)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
