"""Promote an accepted installer and retain only that versioned EXE in dist."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re

from scripts.common.paths import PROJECT_ROOT, release_runs_dir
from scripts.release import lifecycle, workflow

_INSTALLER = re.compile(r'E7auto_v(\d+)\.(\d+)\.(\d+)_Setup_x64\.exe')


def _accepted_record(run: Path, root: Path) -> dict:
    run, artifact, snapshot = lifecycle._bundle(run, root)
    lifecycle.validate_report(run, root)
    validation = workflow._read(run / workflow.VALIDATION)
    report = validation.get('report', {})
    checked = report.get('self_check') or {}
    if (validation.get('artifact_sha256') != workflow._file_sha(run / workflow.ARTIFACT)
            or report.get('problems') != [] or report.get('installation_verified') is not True
            or checked.get('compiled') is not True or checked.get('wgc_importable') is not True
            or checked.get('version') != snapshot['version']):
        raise ValueError('Successful installed self-check is required before dist promotion')
    return artifact


def _created(run: Path) -> datetime:
    value = datetime.fromisoformat(workflow._read(run / 'task.json')['created_at'])
    if value.tzinfo is None:
        raise ValueError('Build creation time must include its timezone')
    return value


def _check_newer_build(run: Path, target: Path, artifact: dict, root: Path) -> None:
    if not target.exists() or workflow._file_sha(target) == artifact['installer_sha256']:
        return
    existing = workflow._file_sha(target)
    for path in (release_runs_dir(root) / ('v' + artifact['version'])).glob('*/task.json'):
        other = path.parent
        if other == run or _created(other) <= _created(run):
            continue
        try:
            newer = _accepted_record(other, root)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if newer['installer_sha256'] == existing:
            raise ValueError('A newer accepted build of this version is already in dist')


def promote_verified_installer(run: Path, root: Path = PROJECT_ROOT, *, plan: bool = False) -> dict:
    """Use existing sealed acceptance evidence; never rewrite historical reports."""
    root = root.absolute()
    run = workflow._run_directory(run, root)
    artifact = _accepted_record(run, root)
    _, source = workflow._artifact(run, root)
    directory = workflow._unlinked_path(root / 'dist', root)
    target = workflow._unlinked_path(directory / artifact['installer_name'], root)
    version = tuple(map(int, artifact['version'].split('.')))
    old = []
    if directory.exists():
        for path in sorted(directory.iterdir()):
            match = _INSTALLER.fullmatch(path.name)
            if match is None:
                continue
            workflow._unlinked_path(path, root)
            if not path.is_file() or path.resolve().parent != directory.resolve():
                raise ValueError(f'Unsafe dist installer: {path}')
            if tuple(map(int, match.groups())) > version:
                raise ValueError('A higher installer version is already in dist')
            if path != target:
                old.append((path, workflow._file_sha(path)))
    _check_newer_build(run, target, artifact, root)
    result = {'status': 'planned' if plan else 'published', 'version': artifact['version'],
              'installer': str(target), 'installer_sha256': artifact['installer_sha256'],
              'remove': [str(path) for path, _ in old], 'removed': []}
    if plan:
        return result
    try:
        # Validate the entire deletion set before changing any deliverable.
        for path, digest in old:
            workflow._unlinked_path(path, root)
            if path.resolve().parent != directory.resolve() or workflow._file_sha(path) != digest:
                raise ValueError(f'Dist installer changed before cleanup: {path}')
        directory.mkdir(exist_ok=True)
        workflow._unlinked_path(target, root)
        if source != target:
            # Same-volume atomic replacement also protects the last accepted same-version package.
            workflow._unlinked_path(source, root)
            if workflow._file_sha(source) != artifact['installer_sha256']:
                raise ValueError('Pending installer changed before promotion')
            source.replace(target)
        if workflow._file_sha(target) != artifact['installer_sha256']:
            raise ValueError('Promoted installer differs from the accepted artifact')
        for path, digest in old:
            workflow._unlinked_path(path, root)
            if path.resolve().parent != directory.resolve() or workflow._file_sha(path) != digest:
                raise ValueError(f'Dist installer changed during cleanup: {path}')
            path.unlink()
            result['removed'].append(str(path))
    except (OSError, ValueError) as exc:
        result.update(status='failed', error=str(exc))
        workflow._write(run / 'distribution-result.json', result)
        raise
    workflow._write(run / 'distribution-result.json', result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--plan', action='store_true', help='Inspect accepted-package promotion without writing')
    args = parser.parse_args()
    try:
        result = promote_verified_installer(args.run_dir, plan=args.plan)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f'Installer distribution refused: {exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
