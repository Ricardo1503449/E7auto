from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from e7auto.vision import TemplateRepository

from .helpers import make_config


@pytest.mark.parametrize("directory", ["E7 shop", "E7 商店脚本"])
@pytest.mark.parametrize("mode", ["bgr", "opaque", "masked"])
def test_load_template_preserves_pixels_and_alpha(
    tmp_path: Path, directory: str, mode: str
) -> None:
    path = tmp_path / directory / "assets" / "templates" / "confirm_button.png"
    path.parent.mkdir(parents=True)
    pixels = np.arange(60, dtype=np.uint8).reshape(4, 5, 3)
    alpha = np.full((4, 5), 255, dtype=np.uint8)
    if mode == "masked":
        alpha[0, :3] = [0, 64, 128]
    original = pixels if mode == "bgr" else np.dstack((pixels, alpha))
    ok, encoded = cv2.imencode(".png", original)
    assert ok
    path.write_bytes(encoded.tobytes())

    repository = TemplateRepository(
        replace(make_config(), template_paths={"confirm_button": path})
    )
    template = repository.get("confirm_button")

    np.testing.assert_array_equal(template.image, pixels)
    assert template.image.flags.c_contiguous
    if mode == "masked":
        np.testing.assert_array_equal(template.mask, alpha)
        assert template.mask.flags.c_contiguous
    else:
        assert template.mask is None


@pytest.mark.parametrize("contents", [None, b"", b"not a PNG"])
def test_unreadable_template_reports_key_and_path(
    tmp_path: Path, contents: bytes | None
) -> None:
    path = tmp_path / "confirm_button.png"
    if contents is not None:
        path.write_bytes(contents)

    with pytest.raises(ValueError) as error:
        TemplateRepository(
            replace(make_config(), template_paths={"confirm_button": path})
        )

    assert str(error.value) == f"Cannot load template confirm_button: {path}"


@pytest.mark.parametrize("mode", ["grayscale", "transparent"])
def test_invalid_template_channels_or_alpha_are_rejected(
    tmp_path: Path, mode: str
) -> None:
    path = tmp_path / "confirm_button.png"
    shape = (4, 5) if mode == "grayscale" else (4, 5, 4)
    ok, encoded = cv2.imencode(".png", np.zeros(shape, dtype=np.uint8))
    assert ok
    path.write_bytes(encoded.tobytes())
    expected = (
        "Template must have 3 or 4 channels"
        if mode == "grayscale"
        else "Template alpha mask is empty"
    )

    with pytest.raises(ValueError, match=expected):
        TemplateRepository(
            replace(make_config(), template_paths={"confirm_button": path})
        )
