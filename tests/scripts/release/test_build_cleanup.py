import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.helpers.paths import ROOT


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell cleanup')
BUILD_ID = '20261001-120000-installer-1234abcd'


def cleanup(project: Path, build_id: str = BUILD_ID, area: str = 'nuitka'):
    return subprocess.run(
        [shutil.which('powershell.exe'), '-NoProfile', '-ExecutionPolicy', 'Bypass',
         '-File', str(ROOT / 'scripts/release/cleanup-build.ps1'),
         '-ProjectRoot', str(project), '-BuildId', build_id, '-Area', area],
        capture_output=True, timeout=30,
    )


@pytest.mark.parametrize('area', ['nuitka', 'staging'])
def test_cleanup_removes_only_current_build_and_preserves_deliverables(tmp_path, area):
    project = tmp_path / '项目 with spaces'
    target = project / 'build' / area / BUILD_ID
    for name in ('launcher.build/module.obj', 'launcher.dist/E7auto.exe'):
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'generated')
    retained = ['build/nuitka/older/launcher.dist/E7auto.exe',
                'build/staging/current/test.txt', 'dist/Setup.exe',
                'artifacts/releases/report.json', '.nuitka-cache/cached.obj']
    for name in retained:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'keep')
    result = cleanup(project, area=area)
    assert result.returncode == 0, result.stderr
    assert not target.exists()
    assert all((project / name).read_bytes() == b'keep' for name in retained)


@pytest.mark.parametrize('build_id', ['..', '../other', 'C:\\Windows', '*'])
def test_cleanup_refuses_path_or_wildcard_build_ids(tmp_path, build_id):
    marker = tmp_path / 'keep.txt'
    marker.write_bytes(b'keep')
    assert cleanup(tmp_path, build_id).returncode != 0
    assert marker.read_bytes() == b'keep'


@pytest.mark.parametrize('linked_part', ['build', 'target', 'child'])
@pytest.mark.parametrize('area', ['nuitka', 'staging'])
def test_cleanup_refuses_junctions_without_touching_their_target(tmp_path, linked_part, area):
    project = tmp_path / 'project'
    target = project / 'build' / area / BUILD_ID
    outside = tmp_path / 'outside'
    outside.mkdir()
    marker = outside / 'keep.txt'
    marker.write_bytes(b'keep')
    link = {'build': project / 'build', 'target': target, 'child': target / 'linked'}[linked_part]
    link.parent.mkdir(parents=True)
    # Creating/removing this junction uses PowerShell only; no shell-composed delete.
    command = "New-Item -ItemType Junction -Path $env:CLEANUP_TEST_LINK -Target $env:CLEANUP_TEST_TARGET | Out-Null"
    result = subprocess.run(
        [shutil.which('powershell.exe'), '-NoProfile', '-Command', command],
        env={**os.environ, 'CLEANUP_TEST_LINK': str(link), 'CLEANUP_TEST_TARGET': str(outside)},
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    try:
        assert cleanup(project, area=area).returncode != 0
        assert marker.read_bytes() == b'keep'
        assert link.exists()
    finally:
        # Remove the test-created junction itself, without recursive traversal.
        link.rmdir()


def test_cleanup_rejects_unapproved_output_area(tmp_path):
    assert cleanup(tmp_path, area='..').returncode != 0


@pytest.mark.parametrize('outcome', ['passed', 'failed', 'incomplete', 'cleanup_error'])
def test_installer_cleanup_requires_complete_success_and_keeps_acceptance_result(tmp_path, monkeypatch, outcome):
    from scripts.release import verify_installer

    calls = []
    report = {'checks': dict.fromkeys(verify_installer.CHECKS, True), 'problems': []}
    if outcome == 'failed':
        report['problems'].append('acceptance failed')
    if outcome == 'incomplete':
        report['checks']['empty_root_removed'] = False

    def checked(command):
        calls.append(command)
        if outcome == 'cleanup_error':
            raise RuntimeError('file occupied')

    monkeypatch.setattr(verify_installer, 'checked', checked)
    verify_installer.cleanup_verified_staging(tmp_path / BUILD_ID, report)
    if outcome in ('failed', 'incomplete'):
        assert calls == []
        assert report['staging_cleanup'] == 'retained_after_failure'
    else:
        assert calls[0][-4:] == ['-BuildId', BUILD_ID, '-Area', 'staging']
        assert report['problems'] == []
        assert report['staging_cleanup'] == ('failed' if outcome == 'cleanup_error' else 'removed')


@pytest.mark.parametrize('outcome', ['passed', 'failed', 'incomplete', 'promotion_error'])
def test_finish_report_promotes_only_after_acceptance_and_before_staging_cleanup(tmp_path, monkeypatch, outcome):
    from scripts.release import verify_installer as verifier
    calls = []
    report = {'checks': dict.fromkeys(verifier.CHECKS, True), 'problems': []}
    if outcome == 'failed': report['problems'] = ['installation failed']
    if outcome == 'incomplete': report['checks']['empty_root_removed'] = False
    monkeypatch.setattr(verifier.workflow, '_write', lambda *_: calls.append('write'))
    def promote(*_):
        calls.append('promote')
        if outcome == 'promotion_error': raise PermissionError('locked installer')
        return {'status': 'published'}
    monkeypatch.setattr(verifier, 'promote_verified_installer', promote)
    monkeypatch.setattr(verifier, 'checked', lambda *_: calls.append('cleanup'))
    verifier.finish_report(tmp_path, report)
    expected = ['write', 'promote', 'cleanup', 'write'] if outcome == 'passed' else (
        ['write', 'promote', 'write'] if outcome == 'promotion_error' else ['write', 'write'])
    assert calls == expected
    assert report['staging_cleanup'] == ('removed' if outcome == 'passed' else 'retained_after_failure')
