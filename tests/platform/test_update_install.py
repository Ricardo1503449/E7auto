import hashlib
import json
from pathlib import Path
from threading import Event

import pytest

from e7auto.platform import update_install


@pytest.fixture
def package(tmp_path, monkeypatch):
    root = tmp_path / '安装 路径'
    job = 'v9.0.0-' + 'a' * 32
    directory = root / 'state' / 'updates' / job
    directory.mkdir(parents=True)
    path = directory / 'E7auto_v9.0.0_Setup_x64.exe'
    content = b'MZ test installer'
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    (directory / 'request.json').write_text(json.dumps({
        'version': 'v9.0.0', 'name': path.name, 'size': len(content), 'sha256': digest,
    }), encoding='utf-8')
    (directory / 'install.log').write_text('installation log', encoding='utf-8')
    monkeypatch.setattr(update_install, 'validate_installation', lambda root: None)
    monkeypatch.setattr(update_install.win32api, 'GetFileVersionInfo',
                        lambda *args: {'FileVersionMS': 9 << 16, 'FileVersionLS': 0})
    return root, path, digest, len(content)


def test_launch_verifies_file_and_passes_quoted_update_only_arguments(package, monkeypatch):
    root, path, digest, size = package
    calls = []
    monkeypatch.setattr(update_install.pythoncom, 'CoInitializeEx', lambda flags: calls.append('init'))
    monkeypatch.setattr(update_install.pythoncom, 'CoUninitialize', lambda: calls.append('uninit'))
    monkeypatch.setattr(update_install.shell, 'ShellExecuteEx', lambda **kwargs: calls.append(kwargs) or {'hProcess': 123})
    monkeypatch.setattr(update_install.win32api, 'CloseHandle', lambda handle: calls.append(('close', handle)))
    assert update_install.launch_update(root, path, 'v9.0.0', size, digest, Event())
    assert calls[0] == 'init' and calls[-1] == 'uninit'
    launch = calls[1]
    assert launch['lpVerb'] == 'runas' and launch['lpFile'] == str(path)
    assert f'"/DIR={root}"' in launch['lpParameters']
    assert '/E7AUTOUPDATE=1' in launch['lpParameters'] and path.parent.name in launch['lpParameters']
    assert '/SILENT' in launch['lpParameters'] and '/NORESTART' in launch['lpParameters']
    assert '/FORCECLOSEAPPLICATIONS' not in launch['lpParameters']
    assert ('close', 123) in calls


@pytest.mark.parametrize('kind', ['size', 'digest', 'not_exe', 'outside', 'old_version', 'cancel'])
def test_bad_package_or_cancellation_never_invokes_windows(package, monkeypatch, kind):
    root, path, digest, size = package
    monkeypatch.setattr(update_install.shell, 'ShellExecuteEx', lambda **kwargs: pytest.fail('must not execute'))
    version = 'v9.0.0'
    cancelled = Event()
    if kind == 'size': size += 1
    if kind == 'digest': digest = '0' * 64
    if kind == 'not_exe': path.write_bytes(b'XX test installer')
    if kind == 'outside': path = root / path.name
    if kind == 'old_version': version = 'v1.0.0'
    if kind == 'cancel':
        cancelled.set()
        assert update_install.launch_update(root, path, version, size, digest, cancelled) is False
    else:
        with pytest.raises((ValueError, OSError)):
            update_install.launch_update(root, path, version, size, digest, cancelled)


@pytest.mark.parametrize('changed', [None, 'version', 'digest', 'name'])
def test_success_cleanup_removes_only_matching_package_and_retains_records(package, monkeypatch, changed):
    root, path, digest, size = package
    monkeypatch.setattr(update_install, '__version__', '9.0.0' if changed != 'version' else '8.0.0')
    unrelated = path.parent / 'user-file.txt'
    unrelated.write_text('keep', encoding='utf-8')
    metadata = path.parent / 'request.json'
    data = json.loads(metadata.read_text(encoding='utf-8'))
    if changed == 'digest': data['sha256'] = '0' * 64
    if changed == 'name': data['name'] = '../user-file.txt'
    metadata.write_text(json.dumps(data), encoding='utf-8')
    update_install.cleanup_applied_update(root, path.parent.name)
    assert path.exists() is (changed is not None)
    assert unrelated.read_text(encoding='utf-8') == 'keep'
    assert (path.parent / 'install.log').is_file() and metadata.is_file()


@pytest.mark.parametrize('job', ['../outside', 'v9.0.0-../../outside', '', 'a' * 32])
def test_cleanup_rejects_non_job_paths(package, job):
    root, path, _, _ = package
    with pytest.raises(ValueError):
        update_install.cleanup_applied_update(root, job)
    assert path.exists()


def test_launch_cancelled_by_windows_preserves_package_and_balances_com(package, monkeypatch):
    root, path, digest, size = package
    calls = []
    monkeypatch.setattr(update_install.pythoncom, 'CoInitializeEx', lambda flags: calls.append('init'))
    monkeypatch.setattr(update_install.pythoncom, 'CoUninitialize', lambda: calls.append('uninit'))
    def reject(**kwargs): raise OSError(1223, 'cancelled')
    monkeypatch.setattr(update_install.shell, 'ShellExecuteEx', reject)
    with pytest.raises(OSError):
        update_install.launch_update(root, path, 'v9.0.0', size, digest, Event())
    assert calls == ['init', 'uninit'] and path.exists()


def test_success_cleanup_retries_brief_installer_file_lock(package, monkeypatch):
    root, path, _, _ = package
    monkeypatch.setattr(update_install, '__version__', '9.0.0')
    original = Path.unlink
    attempts, sleeps = [], []
    def unlink(item, *args, **kwargs):
        if item == path:
            attempts.append(True)
            if len(attempts) < 3:
                raise PermissionError('setup loader still running')
        return original(item, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', unlink)
    monkeypatch.setattr(update_install.time, 'sleep', sleeps.append)
    update_install.cleanup_applied_update(root, path.parent.name)
    assert len(attempts) == 3 and sleeps == [0.25, 0.25] and not path.exists()


@pytest.mark.parametrize('invalid', [None, 'executable', 'registry', 'manifest'])
def test_installed_root_requires_matching_executable_registration_and_manifest(tmp_path, monkeypatch, invalid):
    from contextlib import nullcontext
    root = tmp_path / 'installed'
    root.mkdir()
    (root / 'E7auto.exe').write_bytes(b'MZ')
    (root / 'install-files.txt').write_text('' if invalid == 'manifest' else update_install.PRODUCT_ID + '\n', encoding='utf-8')
    monkeypatch.setattr(update_install, 'is_installed_build', lambda: True)
    monkeypatch.setattr(update_install.sys, 'executable', str(tmp_path / 'python.exe' if invalid == 'executable' else root / 'E7auto.exe'))
    monkeypatch.setattr(update_install.winreg, 'OpenKey', lambda *args: nullcontext(object()))
    monkeypatch.setattr(update_install.winreg, 'QueryValueEx', lambda *args: (str(tmp_path if invalid == 'registry' else root), 1))
    if invalid is None:
        update_install.validate_installation(root)
    else:
        with pytest.raises(ValueError):
            update_install.validate_installation(root)


def test_linked_package_is_refused_before_windows_launch(package, monkeypatch):
    root, path, digest, size = package
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda item: item == path or original(item))
    monkeypatch.setattr(update_install.shell, 'ShellExecuteEx', lambda **kwargs: pytest.fail('must not execute'))
    with pytest.raises(ValueError, match='链接'):
        update_install.launch_update(root, path, 'v9.0.0', size, digest, Event())


def test_installer_embedded_version_must_match_release_metadata(package, monkeypatch):
    root, path, digest, size = package
    monkeypatch.setattr(update_install.win32api, 'GetFileVersionInfo',
                        lambda *args: {'FileVersionMS': 8 << 16, 'FileVersionLS': 0})
    monkeypatch.setattr(update_install.shell, 'ShellExecuteEx', lambda **kwargs: pytest.fail('must not execute'))
    with pytest.raises(ValueError, match='版本不一致'):
        update_install.launch_update(root, path, 'v9.0.0', size, digest, Event())
