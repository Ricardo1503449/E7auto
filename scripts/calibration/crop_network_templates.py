from pathlib import Path
import argparse

import cv2

from scripts.common.image_io import write_png
from scripts.common.paths import calibration_output_dirs, template_relative_path


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
    output = output_dir / template_relative_path(name, feature="common")
    write_png(output, result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Crop network prompts from a supplied reference image")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    output_dir, _ = calibration_output_dirs(args.output_dir)
    crop(args.source, output_dir, "network_connection_abnormal.png", 900, 545, 540, 105)
    crop(args.source, output_dir, "network_retry.png", 1060, 760, 300, 120)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
