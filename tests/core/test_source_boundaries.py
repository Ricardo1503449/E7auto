import pytest

from scripts.project.check_dependencies import check_dependencies


POLICY = {
    'features': ['shop', 'penguin'],
    'source_rules': {
        'layers': ['core', 'runtime', 'vision', 'features', 'ui', 'platform'],
        'root_modules': ['e7auto', 'e7auto.app', 'e7auto.bootstrap'],
        'compatibility_modules': ['e7auto.config'],
        'legacy_modules': ['e7auto.automation'],
        'deferred_wgc_imports': {'e7auto.bootstrap': ['create']},
    },
}


@pytest.mark.parametrize('path,source', [
    ('features/shop/flow.py', 'from ..penguin.flow import PenguinFlow'),
    ('features/shop/flow.py', 'def run():\n    import e7auto.features.penguin.flow'),
    ('runtime/session.py', 'from e7auto.features.shop.flow import ShopFlow'),
    ('vision/digits.py', 'from e7auto.features.shop import vision'),
    ('features/penguin/flow.py', 'from e7auto.platform import windows'),
    ('features/shop/flow.py', 'import ctypes'),
    ('features/shop/flow.py', 'import win32gui'),
    ('runtime/capture.py', 'import winrt.windows.graphics.capture'),
    ('core/types.py', 'from e7auto.runtime import session'),
    ('features/shop/flow.py', 'from e7auto.config import AppConfig'),
    ('features/shop/flow.py', 'from e7auto.automation.engine import AutomationEngine'),
    ('runtime/session.py', 'from scripts.common.paths import PROJECT_ROOT'),
    ('vision/digits.py', 'import tests.helpers'),
    ('features/unknown/flow.py', 'pass'),
    ('misc/helper.py', 'pass'),
    ('features/shop/flow.py', 'import importlib\nimportlib.import_module("e7auto.features.penguin.flow")'),
    ('bootstrap.py', 'from e7auto.platform.wgc_capture import WindowsGraphicsCaptureService'),
    ('bootstrap.py', 'def wrong_factory():\n    from e7auto.platform.wgc_capture import WindowsGraphicsCaptureService'),
    ('ui/worker.py', 'import winrt'),
])
def test_rejects_dependency_and_ownership_violations(path, source):
    name = 'src/e7auto/' + path
    assert check_dependencies([name], lambda _: source.encode(), POLICY)


@pytest.mark.parametrize('path,source', [
    ('features/shop/flow.py', 'from e7auto.runtime.context import RuntimeContext'),
    ('features/penguin/vision.py', 'from e7auto.vision.digits import DigitMatcher'),
    ('features/shop/flow.py', 'from .contracts import ShopVision'),
    ('platform/capture.py', 'import ctypes'),
    ('bootstrap.py', 'def create():\n    from e7auto.platform.wgc_capture import WindowsGraphicsCaptureService'),
    ('config.py', 'from e7auto.core.types import Point'),
])
def test_allows_explicit_shared_contracts_and_composition(path, source):
    name = 'src/e7auto/' + path
    assert check_dependencies([name], lambda _: source.encode(), POLICY) == []


def test_real_source_obeys_registered_boundaries():
    import json
    from tests.helpers.paths import ROOT
    paths = [p.relative_to(ROOT).as_posix() for p in (ROOT / 'src/e7auto').rglob('*.py')]
    policy = json.loads((ROOT / 'scripts/project/layout.json').read_text())
    assert check_dependencies(paths, lambda name: (ROOT / name).read_bytes(), policy) == []


@pytest.mark.parametrize('module,forbidden', [
    ('e7auto.features.penguin.flow', 'e7auto.features.shop'),
    ('e7auto.features.penguin.vision', 'e7auto.features.shop'),
    ('e7auto.features.shop.flow', 'e7auto.features.penguin'),
    ('e7auto.features.shop.vision', 'e7auto.features.penguin'),
    ('e7auto.runtime.context', 'e7auto.features'),
])
def test_feature_imports_do_not_load_another_feature_or_native_runtime(module, forbidden):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-B', '-c', f'''
import importlib, sys
importlib.import_module({module!r})
for prefix in ({forbidden!r}, 'PySide6', 'winrt', 'e7auto.platform'):
    assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules), prefix
'''], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_unknown_production_feature_is_rejected_before_resource_or_native_setup(monkeypatch):
    import e7auto.bootstrap as bootstrap
    def forbidden(*args, **kwargs):
        raise AssertionError('resource setup must not start')
    monkeypatch.setattr(bootstrap, 'TemplateRepository', forbidden)
    with pytest.raises(ValueError, match='Unknown feature'):
        bootstrap.create_production_session(None, 'unknown', None, None, None)
