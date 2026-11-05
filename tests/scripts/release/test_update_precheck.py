"""An installed release must report the production updater precheck explicitly."""
from dataclasses import asdict
import json
import subprocess
import sys

import pytest

from scripts.release import verify_release


@pytest.mark.parametrize('value, exit_code', [
    (True, 0), (True, 2), ('missing', 0), (False, 0), (False, 2), (None, 0), (1, 0), ('true', 0),
])
def test_installed_verifier_requires_explicit_success(tmp_path, monkeypatch, capsys, value, exit_code):
    release = tmp_path / '安装 路径'
    (release / 'config').mkdir(parents=True)
    (release / 'E7auto.exe').write_bytes(b'MZ fixture')
    (release / 'config/internal.yaml').write_text(
        verify_release.yaml.safe_dump({'logging': asdict(verify_release.LoggingConfig())}), encoding='utf-8')
    (release / 'config/chaos.yaml').touch()
    (release / '使用说明.md').write_text('# E7auto v1.3.3 x64 使用说明\n', encoding='utf-8')
    (release / 'assets/templates').mkdir(parents=True)
    (release / 'assets/ui').mkdir()
    monkeypatch.setattr(verify_release, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(verify_release, 'project_version', lambda _path: '1.3.3')
    monkeypatch.setattr(verify_release, '__version__', '1.3.3')
    monkeypatch.setattr(verify_release, 'pe_machine', lambda _path: 0x8664)
    monkeypatch.setattr(verify_release, 'load_config', lambda *args, **kwargs: object())
    monkeypatch.setattr(verify_release, 'with_chaos_config', lambda *args, **kwargs: None)
    for name in ('verify_windows_versions', 'verify_template_assets', 'verify_ui_assets',
                 'verify_forbidden_release_files', 'verify_required_release_files',
                 'verify_native_runtime', 'verify_registration'):
        monkeypatch.setattr(verify_release, name, lambda *args: [])
    checked = {'compiled': True, 'version': '1.3.3', 'wgc_importable': True,
               'update_installation_error': 'native path unavailable' if value is False else ''}
    if value != 'missing':
        checked['update_installation_valid'] = value
    calls = []
    def self_check(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, exit_code, json.dumps(checked), '')
    monkeypatch.setattr(verify_release.subprocess, 'run', self_check)
    monkeypatch.setattr(sys, 'argv', ['verify_release', '--directory', str(release)])

    assert verify_release.main() == (0 if value is True and exit_code == 0 else 1)
    report = json.loads(capsys.readouterr().out)
    assert calls == [[str(release / 'E7auto.exe'), '--self-check']]
    assert report['self_check'] == checked
    assert len(report['problems']) == int(exit_code != 0) + int(value is not True)
    if exit_code:
        assert report['problems'][0].startswith('compiled self-check failed: 2')
    if value is not True:
        assert 'update installation validation' in report['problems'][-1]
        if value is False:
            assert 'native path unavailable' in report['problems'][-1]
