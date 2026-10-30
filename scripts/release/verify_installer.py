"""Administrator-only, isolated install/repair/uninstall acceptance of a sealed EXE."""
from __future__ import annotations

import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time
import winreg

from scripts.common.paths import PROJECT_ROOT, build_staging_dir
from scripts.release import workflow
from scripts.release.distribution import promote_verified_installer
from scripts.release.lifecycle import CHECKS, SMOKE_CHECKS, select_plan
from scripts.release.installer import IDENTITY_KEY, MANIFEST, UNINSTALL_KEY

def registry_exists(key: str) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except FileNotFoundError:
        return False


def environment_snapshot() -> dict:
    result = {}
    for hive, name in ((winreg.HKEY_CURRENT_USER, 'Environment'),
                       (winreg.HKEY_LOCAL_MACHINE, r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')):
        with winreg.OpenKey(hive, name) as key:
            result[name] = [winreg.EnumValue(key, i) for i in range(winreg.QueryInfoKey(key)[1])]
    return result


def shell_folder(folder_id: int) -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    result = ctypes.windll.shell32.SHGetFolderPathW(None, folder_id, None, 0, buffer)
    if result:
        raise OSError(f'Cannot resolve shell folder: {result}')
    return Path(buffer.value)


def checked(command: list[str], *, expected_success=True, cwd=PROJECT_ROOT) -> subprocess.CompletedProcess:
    process = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding='utf-8',
                             errors='replace', timeout=180, check=False)
    if (process.returncode == 0) != expected_success:
        raise RuntimeError(f'Unexpected exit {process.returncode}: {command[0]}\n{process.stdout}\n{process.stderr}')
    return process


def wait_for_cleanup(predicate, timeout: float = 10.0) -> bool:
    """Inno's bootstrap can exit before _unins-done.tmp/self-deletion finishes."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if predicate():
                return True
        except OSError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def cleanup_verified_staging(run: Path, report: dict) -> None:
    report['staging_cleanup'] = 'retained_after_failure'
    report['staging_cleanup_error'] = None
    required = SMOKE_CHECKS if report.get('mode') == 'reuse' else CHECKS
    if (report['problems'] or not all(report['checks'].get(name) is True for name in required)
            or report.get('distribution', {}).get('status', 'published') != 'published'):
        return
    try:
        checked(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 str(PROJECT_ROOT / 'scripts/release/cleanup-build.ps1'),
                 '-ProjectRoot', str(PROJECT_ROOT), '-BuildId', run.name, '-Area', 'staging'])
        report['staging_cleanup'] = 'removed'
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        report['staging_cleanup'] = 'failed'
        report['staging_cleanup_error'] = str(exc)
        print(f'Installer acceptance passed; staging cleanup failed: {exc}', file=sys.stderr)


def finish_report(run: Path, report: dict) -> dict:
    workflow._write(run / 'installer-verification.json', report)
    report['distribution'] = {'status': 'not_attempted'}
    required = SMOKE_CHECKS if report.get('mode') == 'reuse' else CHECKS
    if not report['problems'] and all(report['checks'].get(name) is True for name in required):
        try:
            report['distribution'] = promote_verified_installer(run, PROJECT_ROOT)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            report['distribution'] = {'status': 'failed', 'error': str(exc)}
            print(f'Installer acceptance passed; dist promotion/cleanup failed: {exc}', file=sys.stderr)
    cleanup_verified_staging(run, report)
    workflow._write(run / 'installer-verification.json', report)
    return report


def verify(run: Path, *, force_full: bool = False) -> dict:
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise PermissionError('Run the installer acceptance tool in a Windows administrator terminal')
    run = workflow._run_directory(run, PROJECT_ROOT)
    artifact, setup = workflow._artifact(run, PROJECT_ROOT)
    workflow._check_inputs(workflow._snapshot(run, PROJECT_ROOT), PROJECT_ROOT, exact=False)
    plan = select_plan(run, force_full=force_full)
    if registry_exists(UNINSTALL_KEY) or registry_exists(IDENTITY_KEY):
        raise ValueError('An installation/identity already exists; refusing to alter it for testing')
    # A managed build task owns this new path; never use the real user installation.
    target = build_staging_dir() / run.name / '安装 验收' / 'E7auto'
    if target.exists():
        raise ValueError('Acceptance directory already exists; preserve it and inspect before retrying')
    desktop = shell_folder(0x19) / 'E7auto.lnk'
    start_menu = shell_folder(0x17) / 'E7auto.lnk'
    if desktop.exists() or start_menu.exists():
        raise ValueError('An E7auto shortcut already exists; refusing to overwrite it during testing')
    before_environment = environment_snapshot()
    report = {'artifact_sha256': workflow._file_sha(run / workflow.ARTIFACT),
              'installer_sha256': artifact['installer_sha256'], 'directory': str(target),
              'checks': {}, 'problems': [], **plan}
    flags = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-']
    def install(label, tasks='', expected_success=True):
        return checked([str(setup), *flags, f'/DIR={target}', f'/TASKS={tasks}',
                        f'/LOG={run / (label + ".log")}'], expected_success=expected_success)
    def uninstall(label, expected_success=True):
        exe = target / 'unins000.exe'
        if not exe.is_file():
            raise RuntimeError('Expected uninstaller missing')
        return checked([str(exe), *flags, f'/LOG={run / (label + ".log")}'], expected_success=expected_success)
    def mark(name, condition):
        if not condition:
            raise RuntimeError(f'Acceptance check failed: {name}')
        report['checks'][name] = True
    def verify_installed(label):
        completed = checked([sys.executable, '-B', '-m', 'scripts.release.verify_release',
                             '--run-dir', str(run), '--directory', str(target)])
        (run / (label + '.json')).write_text(completed.stdout, encoding='utf-8')
    try:
        install('install-custom')
        mark('custom_path', (target / 'E7auto.exe').is_file())
        mark('no_desktop_shortcut', not desktop.exists())
        mark('start_menu_shortcut', start_menu.is_file())
        verify_installed('installed-release')
        mark('installed_payload', True)

        if plan['mode'] == 'reuse':
            uninstall('uninstall-smoke')
            mark('uninstall_removes_registration', not registry_exists(UNINSTALL_KEY)
                 and not registry_exists(IDENTITY_KEY) and not desktop.exists() and not start_menu.exists())
            mark('empty_root_removed', wait_for_cleanup(lambda: not target.exists()))
            mark('environment_unchanged', environment_snapshot() == before_environment)
            return finish_report(run, report)

        # The same named lifetime guard used by the compiled GUI; no game/UI starts.
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateMutexW(None, False, r'Global\E7auto.Installed.Running')
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            install('running-install-refused', expected_success=False)
            mark('running_install_blocked', (target / 'E7auto.exe').is_file())
            uninstall('running-uninstall-refused', expected_success=False)
            mark('running_uninstall_blocked', (target / 'E7auto.exe').is_file())
        finally:
            kernel.CloseHandle(handle)

        data = {'logs/installer-test.log': b'acceptance log',
                'state/ui_preferences.json': b'{"run_end_notifications":false}',
                'state/overlay_position.json': b'{"x":12,"y":34}'}
        for name, content in data.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        # Model an owned file retired in a later release; the replacement manifest
        # must remove only that old file and retain runtime data.
        obsolete = target / 'retired-test.dll'
        obsolete.write_bytes(b'old owned payload')
        with (target / MANIFEST).open('a', encoding='utf-8') as stream:
            stream.write('retired-test.dll\n')
        install('upgrade-same-version', tasks='desktopicon')
        mark('upgrade_preserves_data', all((target / name).read_bytes() == value for name, value in data.items()))
        mark('obsolete_file_removed', not obsolete.exists())
        mark('desktop_shortcut', desktop.is_file())
        verify_installed('upgraded-release')
        unrelated = target / 'user-owned.txt'
        unrelated.write_bytes(b'preserve unrelated user file')
        uninstall('uninstall-with-user-file')
        mark('uninstall_removes_data', not (target / 'logs').exists() and not (target / 'state').exists())
        mark('uninstall_removes_registration', not registry_exists(UNINSTALL_KEY)
             and not registry_exists(IDENTITY_KEY) and not desktop.exists() and not start_menu.exists())
        mark('uninstall_preserves_unrelated', wait_for_cleanup(
            lambda: unrelated.read_bytes() == b'preserve unrelated user file' and list(target.iterdir()) == [unrelated]))
        unrelated.unlink()  # Test-created fixture only, never pre-existing data.
        target.rmdir()
        install('install-clean-second-pass')
        uninstall('uninstall-clean-second-pass')
        mark('empty_root_removed', wait_for_cleanup(lambda: not target.exists()))
        mark('environment_unchanged', environment_snapshot() == before_environment)
    except Exception as exc:
        report['problems'].append(f'{type(exc).__name__}: {exc}')
        # Leave a failed installation for inspection; never hide a failure by a
        # forced recursive cleanup or by removing the user's registry state.
    return finish_report(run, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--full', action='store_true', help='Force full lifecycle verification')
    parser.add_argument('--plan', action='store_true', help='Show scope and baseline without installing')
    args = parser.parse_args()
    try:
        report = select_plan(args.run_dir, force_full=args.full) if args.plan else verify(args.run_dir, force_full=args.full)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Installer verification refused: {exc}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(report.get('problems')) or report.get('distribution', {}).get('status') == 'failed')


if __name__ == '__main__':
    raise SystemExit(main())
