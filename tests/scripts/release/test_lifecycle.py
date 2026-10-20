import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.common.paths import create_artifact_run
from scripts.release import lifecycle, workflow


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def make_run(root, *, full=False):
    run = create_artifact_run('releases', 'lifecycle-test', version='1.4.0', root=root)
    snapshot = {'schema_version': 1, 'version': '1.4.0', 'inputs': {
        name: {'sha256': 'a'*64} for name in (
            'scripts/release/e7auto.iss', 'scripts/release/cleanup-build.ps1',
            'scripts/release/verify_installer.py', 'requirements.lock',
            'src/e7auto/app.py', 'src/e7auto/platform/installation.py',
            'src/e7auto/features/chaos/auto_motion.py', 'src/e7auto/vision/frames.py',
            'config/chaos.yaml', 'assets/templates/chaos/auto.png', lifecycle.GUIDE)}}
    write(run/workflow.SNAPSHOT, snapshot)
    artifact = {'schema_version': 2, 'version': '1.4.0',
                'snapshot_sha256': workflow._file_sha(run/workflow.SNAPSHOT),
                'installer_name': 'E7auto_v1.4.0_Setup_x64.exe', 'installer_sha256': 'b'*64,
                'release_files': {'E7auto.exe': 'c'*64, 'native.dll': 'd'*64,
                                  'config/chaos.yaml': 'e'*64,
                                  'assets/templates/chaos/auto.png': 'f'*64}}
    write(run/workflow.ARTIFACT, artifact)
    write(run/'build-result.json', {'succeeded': True, 'build_id': run.name,
                                    'compiler_sha256': '1'*64, 'inno_version': '6.7.3'})
    if full:
        write(run/workflow.VALIDATION, {
            'artifact_sha256': workflow._file_sha(run/workflow.ARTIFACT),
            'report': {'problems': [], 'installation_verified': True,
                       'self_check': {'compiled': True, 'wgc_importable': True, 'version': '1.4.0'}}})
        write(run/lifecycle.REPORT, {'artifact_sha256': workflow._file_sha(run/workflow.ARTIFACT),
                                     'installer_sha256': artifact['installer_sha256'],
                                     'environment': lifecycle.environment_identity(),
                                     'checks': dict.fromkeys(lifecycle.CHECKS, True), 'problems': []})
    return run


def mutate_input(run, name):
    snapshot = workflow._read(run/workflow.SNAPSHOT)
    snapshot['inputs'][name] = {'sha256': '9'*64}
    write(run/workflow.SNAPSHOT, snapshot)
    artifact = workflow._read(run/workflow.ARTIFACT)
    artifact['snapshot_sha256'] = workflow._file_sha(run/workflow.SNAPSHOT)
    write(run/workflow.ARTIFACT, artifact)


def reused_report(run, root):
    plan = lifecycle.select_plan(run, root)
    assert plan['mode'] == 'reuse', plan
    report = {**plan, 'artifact_sha256': workflow._file_sha(run/workflow.ARTIFACT),
              'installer_sha256': workflow._read(run/workflow.ARTIFACT)['installer_sha256'],
              'checks': dict.fromkeys(lifecycle.SMOKE_CHECKS, True), 'problems': []}
    write(run/lifecycle.REPORT, report)
    return report


@pytest.mark.parametrize('name', ['src/e7auto/features/chaos/auto_motion.py',
                                 'src/e7auto/vision/frames.py', 'config/chaos.yaml',
                                 'assets/templates/chaos/auto.png', lifecycle.GUIDE])
def test_business_changes_reuse_only_explicit_old_checks(tmp_path, name):
    baseline = make_run(tmp_path, full=True)
    current = make_run(tmp_path)
    mutate_input(current, name)
    report = reused_report(current, tmp_path)
    assert report['baseline']['run_directory'] == baseline.relative_to(tmp_path).as_posix()
    assert 'upgrade_preserves_data' not in report['checks']
    assert 'upgrade_preserves_data' in report['reused_checks']
    lifecycle.validate_report(current, tmp_path)


@pytest.mark.parametrize('name', ['scripts/release/e7auto.iss',
                                 'scripts/release/cleanup-build.ps1',
                                 'scripts/release/verify_installer.py', 'requirements.lock',
                                 'src/e7auto/app.py', 'src/e7auto/platform/installation.py',
                                 'src/e7auto/new_unknown_layer.py'])
def test_sensitive_or_unknown_input_changes_require_full(tmp_path, name):
    make_run(tmp_path, full=True)
    current = make_run(tmp_path)
    mutate_input(current, name)
    assert lifecycle.select_plan(current, tmp_path)['reason'] == 'lifecycle_scope_changed'


@pytest.mark.parametrize('change', ['file_list', 'native', 'compiler', 'missing_compiler'])
def test_packaging_or_compiler_changes_require_full(tmp_path, change):
    make_run(tmp_path, full=True)
    current = make_run(tmp_path)
    if change in ('file_list', 'native'):
        artifact = workflow._read(current/workflow.ARTIFACT)
        artifact['release_files']['new.txt' if change == 'file_list' else 'native.dll'] = '0'*64
        write(current/workflow.ARTIFACT, artifact)
    else:
        build = workflow._read(current/'build-result.json')
        build['compiler_sha256'] = '0'*64 if change == 'compiler' else None
        write(current/'build-result.json', build)
    assert lifecycle.select_plan(current, tmp_path)['mode'] == 'full'


def test_missing_failed_or_corrupt_evidence_and_explicit_full_never_reuse(tmp_path):
    baseline = make_run(tmp_path, full=True)
    current = make_run(tmp_path)
    assert lifecycle.select_plan(current, tmp_path, force_full=True)['mode'] == 'full'
    report = workflow._read(baseline/lifecycle.REPORT)
    report['problems'] = ['new failure']
    write(baseline/lifecycle.REPORT, report)
    assert lifecycle.select_plan(current, tmp_path)['reason'] == 'previous_acceptance_failure'
    (baseline/lifecycle.REPORT).write_text('invalid json', encoding='utf8')
    assert lifecycle.select_plan(current, tmp_path)['mode'] == 'full'
    (baseline/lifecycle.REPORT).unlink()
    assert lifecycle.select_plan(current, tmp_path)['mode'] == 'full'


@pytest.mark.parametrize('change', ['reference', 'baseline', 'current_scope', 'missing_smoke', 'missing_reused'])
def test_publication_rejects_tampered_or_incomplete_reuse(tmp_path, change):
    baseline = make_run(tmp_path, full=True)
    current = make_run(tmp_path)
    report = reused_report(current, tmp_path)
    if change == 'reference':
        report['baseline']['run_directory'] = '../outside'
    elif change == 'baseline':
        old = workflow._read(baseline/lifecycle.REPORT)
        old['checks']['upgrade_preserves_data'] = False
        write(baseline/lifecycle.REPORT, old)
    elif change == 'current_scope':
        mutate_input(current, 'requirements.lock')
        report['artifact_sha256'] = workflow._file_sha(current/workflow.ARTIFACT)
    elif change == 'missing_smoke':
        report['checks']['installed_payload'] = False
    else:
        report['reused_checks'] = []
    write(current/lifecycle.REPORT, report)
    with pytest.raises((ValueError, OSError)):
        lifecycle.validate_report(current, tmp_path)


def test_reuse_always_points_to_full_evidence_without_chaining(tmp_path):
    baseline = make_run(tmp_path, full=True)
    previous = make_run(tmp_path)
    reused_report(previous, tmp_path)
    current = make_run(tmp_path)
    report = reused_report(current, tmp_path)
    assert report['baseline']['run_directory'] == baseline.relative_to(tmp_path).as_posix()
    lifecycle.validate_report(current, tmp_path)


@pytest.mark.parametrize('fail_self_check', [False, True])
def test_smoke_installs_current_package_and_failure_preserves_scene(tmp_path, monkeypatch, fail_self_check):
    from scripts.release import verify_installer as verifier

    make_run(tmp_path, full=True)
    run = make_run(tmp_path)
    artifact = workflow._read(run/workflow.ARTIFACT)
    setup = tmp_path/'Setup.exe'
    target = tmp_path/'build/staging'/run.name/'安装 验收/E7auto'
    desktop, menu = tmp_path/'desktop', tmp_path/'menu'
    desktop.mkdir(); menu.mkdir()
    monkeypatch.setattr(verifier, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(verifier, 'build_staging_dir', lambda: tmp_path/'build/staging')
    monkeypatch.setattr(verifier.ctypes.windll.shell32, 'IsUserAnAdmin', lambda: True)
    monkeypatch.setattr(workflow, '_artifact', lambda *_: (artifact, setup))
    monkeypatch.setattr(workflow, '_check_inputs', lambda *args, **kwargs: None)
    monkeypatch.setattr(verifier, 'select_plan', lambda r, **kw: lifecycle.select_plan(r, tmp_path, **kw))
    monkeypatch.setattr(verifier, 'registry_exists', lambda _: False)
    monkeypatch.setattr(verifier, 'shell_folder', lambda n: desktop if n == 0x19 else menu)
    monkeypatch.setattr(verifier, 'environment_snapshot', lambda: {'unchanged': True})
    commands = []

    def checked(command, **kwargs):
        commands.append(command)
        if command[0] == str(setup):
            target.mkdir(parents=True)
            for name in ('E7auto.exe', 'unins000.exe'):(target/name).write_bytes(b'test')
            (menu/'E7auto.lnk').write_bytes(b'test')
        elif 'scripts.release.verify_release' in command:
            if fail_self_check:raise RuntimeError('new package self-check failed')
        elif command[0] == str(target/'unins000.exe'):
            for path in target.iterdir():path.unlink()
            target.rmdir();(menu/'E7auto.lnk').unlink()
        elif command[0] == 'powershell.exe':
            target.parent.rmdir();target.parent.parent.rmdir()
        else:
            raise AssertionError(command)
        return SimpleNamespace(stdout='{}')

    monkeypatch.setattr(verifier, 'checked', checked)
    report = verifier.verify(run)
    assert report['mode'] == 'reuse'
    assert sum(c[0] == str(setup) for c in commands) == 1
    assert not any('upgrade-same-version.log' in ' '.join(c) for c in commands)
    if fail_self_check:
        assert report['problems'] and target.exists()
        assert report['staging_cleanup'] == 'retained_after_failure'
    else:
        assert report['problems'] == [] and not target.exists()
        assert report['staging_cleanup'] == 'removed'
        assert set(report['checks']) == set(lifecycle.SMOKE_CHECKS)
        lifecycle.validate_report(run, tmp_path)


@pytest.mark.parametrize('environment', [None, {'system': 'different'}])
def test_environment_change_or_missing_environment_requires_full(tmp_path, environment):
    baseline = make_run(tmp_path, full=True)
    current = make_run(tmp_path)
    report = workflow._read(baseline/lifecycle.REPORT)
    report['environment'] = environment
    write(baseline/lifecycle.REPORT, report)
    assert lifecycle.select_plan(current, tmp_path)['reason'] == 'acceptance_environment_changed_or_unrecorded'
