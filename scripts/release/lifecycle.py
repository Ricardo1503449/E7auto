"""Select and verify installer lifecycle evidence without relabelling old runs."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import platform

from scripts.common.paths import PROJECT_ROOT, release_runs_dir
from scripts.release import workflow


CHECKS = ('custom_path', 'no_desktop_shortcut', 'start_menu_shortcut', 'installed_payload',
          'running_install_blocked', 'running_uninstall_blocked', 'upgrade_preserves_data',
          'obsolete_file_removed', 'desktop_shortcut', 'uninstall_removes_data',
          'uninstall_removes_registration', 'uninstall_preserves_unrelated',
          'empty_root_removed', 'environment_unchanged')
SMOKE_CHECKS = ('custom_path', 'no_desktop_shortcut', 'start_menu_shortcut', 'installed_payload',
                'uninstall_removes_registration', 'empty_root_removed', 'environment_unchanged')
REUSED_CHECKS = tuple(name for name in CHECKS if name not in SMOKE_CHECKS)
REPORT = 'installer-verification.json'
# Unknown changes remain full-verification triggers. Packaged file-list changes
# and native/runtime bytes are checked independently of this source allowlist.
BUSINESS_PREFIXES = ('src/e7auto/features/', 'src/e7auto/vision/', 'config/', 'assets/templates/')
GUIDE = 'docs/user/使用说明.md'


def environment_identity() -> dict:
    return {'system': platform.system(), 'release': platform.release(),
            'version': platform.version(), 'machine': platform.machine()}


def _bundle(run: Path, root: Path):
    run = workflow._run_directory(run, root)
    artifact = workflow._read(run / workflow.ARTIFACT)
    snapshot = workflow._snapshot(run, root)
    if (artifact.get('schema_version') != 2 or artifact.get('version') != snapshot['version']
            or artifact.get('snapshot_sha256') != workflow._file_sha(run / workflow.SNAPSHOT)):
        raise ValueError('Invalid lifecycle build binding')
    return run, artifact, snapshot


def _bound_report(run: Path, artifact: dict) -> dict:
    report = workflow._read(run / REPORT)
    if (report.get('artifact_sha256') != workflow._file_sha(run / workflow.ARTIFACT)
            or report.get('installer_sha256') != artifact['installer_sha256']
            or report.get('problems') != []):
        raise ValueError('Successful installation/upgrade/uninstall verification is required')
    return report


def _full_report(run: Path, root: Path):
    run, artifact, snapshot = _bundle(run, root)
    report = _bound_report(run, artifact)
    if report.get('mode', 'full') != 'full' or any(report.get('checks', {}).get(k) is not True for k in CHECKS):
        raise ValueError('Baseline must contain an actual full lifecycle verification')
    validation = workflow._read(run / workflow.VALIDATION)
    installed = validation.get('report', {})
    self_check = installed.get('self_check') or {}
    if (validation.get('artifact_sha256') != workflow._file_sha(run / workflow.ARTIFACT)
            or installed.get('problems') != [] or installed.get('installation_verified') is not True
            or self_check.get('compiled') is not True or self_check.get('wgc_importable') is not True
            or self_check.get('version') != snapshot['version']):
        raise ValueError('Baseline installed self-check is missing or invalid')
    return run, artifact, snapshot


def scope(run: Path, root: Path = PROJECT_ROOT) -> dict:
    run, artifact, snapshot = _bundle(run, root)
    build = workflow._read(run / 'build-result.json')
    compiler = build.get('compiler_sha256')
    if (build.get('succeeded') is not True or build.get('build_id') != run.name
            or not isinstance(compiler, str) or len(compiler) != 64 or not build.get('inno_version')):
        raise ValueError('Compiler provenance is incomplete; full lifecycle verification required')
    protected = {name: info['sha256'] for name, info in snapshot['inputs'].items()
                 if name != GUIDE and not name.startswith(BUSINESS_PREFIXES)}
    files = artifact['release_files']
    native = {name: digest for name, digest in files.items()
              if name not in ('E7auto.exe', '使用说明.md')
              and not name.startswith(('config/', 'assets/templates/'))}
    return {'version': snapshot['version'], 'protected_inputs': protected,
            'payload_names': sorted(files), 'protected_payload': native,
            'compiler_sha256': compiler, 'inno_version': build['inno_version']}


def _reference(run: Path, root: Path) -> dict:
    return {'run_directory': run.relative_to(root).as_posix(),
            'hashes': {name: workflow._file_sha(run / name) for name in (
                workflow.SNAPSHOT, workflow.ARTIFACT, workflow.VALIDATION, REPORT, 'build-result.json')}}


def _created(run: Path) -> float:
    return datetime.fromisoformat(workflow._read(run / 'task.json')['created_at']).timestamp()


def select_plan(run: Path, root: Path = PROJECT_ROOT, *, force_full: bool = False) -> dict:
    run, _, _ = _bundle(run, root)
    environment = environment_identity()
    full = {'mode': 'full', 'reason': 'no_valid_full_baseline', 'baseline': None,
            'environment': environment}
    if force_full:
        return {**full, 'reason': 'explicit_full_verification'}
    try:
        current = scope(run, root)
    except (OSError, ValueError, KeyError, TypeError):
        return {**full, 'reason': 'incomplete_current_provenance'}
    try:
        records = sorted(release_runs_dir(root).glob('*/*/' + REPORT),
                         key=lambda path: _created(path.parent), reverse=True)
        created = _created(run)
    except (OSError, ValueError, KeyError, TypeError):
        return {**full, 'reason': 'invalid_previous_evidence'}
    for path in records:
        if path.parent == run or _created(path.parent) >= created:
            continue
        try:
            previous = workflow._read(path)
            if previous.get('problems') != []:
                return {**full, 'reason': 'previous_acceptance_failure'}
            if previous.get('mode') == 'reuse':
                validate_report(path.parent, root)
                continue  # Never chain reused evidence as a new full baseline.
            baseline, _, _ = _full_report(path.parent, root)
            if previous.get('environment') != environment:
                return {**full, 'reason': 'acceptance_environment_changed_or_unrecorded'}
            previous_scope = scope(baseline, root)
            differences = [key for key in current if current[key] != previous_scope[key]]
            if differences:
                return {**full, 'reason': 'lifecycle_scope_changed', 'changed_scope': differences}
            return {'mode': 'reuse', 'reason': 'lifecycle_scope_unchanged',
                    'environment': environment, 'baseline': _reference(baseline, root),
                    'reused_checks': list(REUSED_CHECKS)}
        except (OSError, ValueError, KeyError, TypeError):
            return {**full, 'reason': 'invalid_previous_evidence'}
    return full


def validate_report(run: Path, root: Path = PROJECT_ROOT) -> None:
    run, artifact, _ = _bundle(run, root)
    report = _bound_report(run, artifact)
    mode = report.get('mode', 'full')
    required = CHECKS if mode == 'full' else SMOKE_CHECKS
    if mode not in ('full', 'reuse') or any(report.get('checks', {}).get(k) is not True for k in required):
        raise ValueError('Successful installation/upgrade/uninstall verification is required')
    if mode == 'reuse':
        reference = report.get('baseline') or {}
        name = reference.get('run_directory')
        if not isinstance(name, str):
            raise ValueError('Missing lifecycle baseline reference')
        baseline, _, _ = _full_report(root / name, root)
        baseline_environment = workflow._read(baseline / REPORT).get('environment')
        if not baseline_environment or report.get('environment') != baseline_environment:
            raise ValueError('Acceptance environment differs from the full baseline')
        if _created(baseline) >= _created(run) or reference != _reference(baseline, root):
            raise ValueError('Lifecycle baseline reference changed or is not older')
        if scope(run, root) != scope(baseline, root):
            raise ValueError('Lifecycle scope changed; full verification required')
        if report.get('reused_checks') != list(REUSED_CHECKS):
            raise ValueError('Reused lifecycle checks must be explicit')
