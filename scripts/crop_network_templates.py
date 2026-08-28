from pathlib import Path

import cv2


SCREENSHOT_DIR = Path(r"C:\Users\lxy\Pictures\Screenshots")
SOURCE = next(
    path for path in SCREENSHOT_DIR.glob("*2026-08-25 184019.png")
    if path.is_file()
)
OUT = Path(__file__).resolve().parents[1] / "assets" / "templates"


def crop(name: str, x: int, y: int, width: int, height: int) -> None:
    image = cv2.imread(str(SOURCE), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"cannot read screenshot: {SOURCE}")
    # The screenshot includes the desktop/window chrome. Coordinates below are
    # client-relative after removing the 47px/117px outer frame.
    client_x, client_y = 47, 117
    result = image[client_y + y : client_y + y + height, client_x + x : client_x + x + width]
    if result.shape[:2] != (height, width):
        raise SystemExit(f"crop outside source for {name}: {result.shape}")
    OUT.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(OUT / name), result):
        raise SystemExit(f"cannot write {name}")


crop("network_connection_abnormal.png", 900, 545, 540, 105)
crop("network_retry.png", 1060, 760, 300, 120)
