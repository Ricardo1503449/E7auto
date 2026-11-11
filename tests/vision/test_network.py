"""Shared crystal identity and dimension-specific network text dispatch."""
import hashlib
import json

import cv2
import numpy as np
import pytest

from e7auto.bootstrap import create_network_service
from e7auto.configuration.loader import load_config
from e7auto.core.types import ClientPoint, Rect, Size
from e7auto.resources.templates import TemplateRepository
from e7auto.vision.frames import CoordinateTransform, FrameView
from e7auto.vision.matching import TemplateMatcher
from tests.helpers.paths import ROOT


@pytest.fixture(scope="module")
def network_resources():
    config = load_config(ROOT / "config/internal.yaml")
    metadata = json.loads((ROOT / "tests/fixtures/common/network_samples.json").read_text(encoding="utf-8"))
    samples = {}
    for name, source in metadata["samples"].items():
        data = (ROOT / "tests/fixtures/common" / source["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == source["sha256"]
        samples[name] = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    return config, TemplateRepository(config), samples


@pytest.mark.parametrize("feature", ["shop", "penguin", "chaos"])
@pytest.mark.parametrize("size", [(2322, 1306), (1536, 864), (1152, 648)])
def test_shared_service_real_roi_and_original_text_results(network_resources, monkeypatch, feature, size):
    config, repository, samples = network_resources
    service = create_network_service(config, repository, feature)
    assert config.network.loading.color == .90 and config.network.loading.structure == .60
    expected_strategy = "locate_all" if feature == "chaos" else "match"
    assert service.detector.text_search == expected_strategy
    calls = []
    for method in ("match", "locate_all"):
        original = getattr(service.detector.matcher, method)
        def record(*args, _original=original, _method=method, **kwargs):
            calls.append((_method, args[1]))
            return _original(*args, **kwargs)
        monkeypatch.setattr(service.detector.matcher, method, record)
    reference = TemplateMatcher(repository)
    transform = CoordinateTransform(Size(2322, 1306), Size(*size))
    expected_signals = {"loading": (True, False, False), "retry": (False, True, True),
                        "clear": (False, False, False)}
    for name, pixels in samples.items():
        # Embed the unmodified ROI in a full client before making synthetic scales.
        # Recognition keeps full-client geometry, including for the child view.
        full = np.zeros((1306, 2322, 3), np.uint8)
        full[533:880, 900:1440] = pixels
        if size != (2322, 1306):
            full = cv2.resize(full, size, interpolation=cv2.INTER_AREA)
        view = FrameView(full, transform).crop(Rect(900, 533, 540, 347))
        observed = service.observe(view)
        assert (observed.loading is not None, observed.error is not None,
                observed.retry is not None) == expected_signals[name]
        if observed.loading is not None:
            assert observed.loading.object_id == "network_loading"
        for key, roi, actual in (
            ("network_connection_abnormal", Rect(900, 545, 540, 105), observed.error),
            ("network_retry", Rect(1060, 760, 300, 120), observed.retry),
        ):
            if expected_strategy == "locate_all":
                found = reference.locate_all(view, key, view.region(roi, clip=True), .90, .80, limit=1)
                old = found[0] if found else None
            else:
                old = reference.match(view, key, roi, .90, structure_threshold=.80)
            assert (actual is None) == (old is None)
            if old is not None:
                assert actual.bounds == old.bounds
                assert actual.confidence == pytest.approx(old.confidence)
                anchor = old.anchor
                if feature == "chaos" and transform.is_identity:
                    anchor = ClientPoint(round(old.bounds.x + old.bounds.width * .5),
                                         round(old.bounds.y + old.bounds.height * .5))
                assert actual.anchor == anchor
    text_calls = [method for method, key in calls if key in {"network_connection_abnormal", "network_retry"}]
    assert text_calls == [expected_strategy] * 6


@pytest.mark.parametrize("size", [(2322, 1306), (1152, 648)])
def test_crystal_observation_is_equal_for_cold_and_warm_cache(network_resources, size):
    config, repository, samples = network_resources
    service = create_network_service(config, repository, 'shop')
    full = np.zeros((1306, 2322, 3), np.uint8)
    full[533:880, 900:1440] = samples['loading']
    if size != (2322, 1306):
        full = cv2.resize(full, size, interpolation=cv2.INTER_AREA)
    view = FrameView(full, CoordinateTransform(Size(2322, 1306), Size(*size))).crop(Rect(900, 533, 540, 347))
    cold = service.observe(view)
    warm = service.observe(view)
    service.detector.matcher.cache.clear()
    cleared = service.observe(view)
    assert cold.loading is not None
    assert cold == warm == cleared
