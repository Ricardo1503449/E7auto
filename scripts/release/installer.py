"""Installer ownership, payload inventory and installed-product verification."""
from pathlib import Path, PureWindowsPath
import hashlib
import re

PRODUCT_ID = "{438C8C44-EB0D-48EA-BA98-6B4589FE35B2}"
APP_ID = "E7auto.Installed"
MANIFEST = "install-files.txt"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{PRODUCT_ID}_is1"
IDENTITY_KEY = rf"Software\Classes\AppUserModelId\{APP_ID}"


def installer_name(version: str) -> str:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Installer releases require a three-part numeric version")
    return f"E7auto_v{version}_Setup_x64.exe"


def manifest_bytes(names) -> bytes:
    paths = sorted({*names, MANIFEST})
    for name in paths:
        path = PureWindowsPath(name)
        if (path.is_absolute() or path.drive or path.root or '..' in name or not path.parts
                or any(char in name for char in ':*?\r\n')
                or path.parts[0].lower() in {'logs', 'state'}
                or path.name.lower().startswith('unins')):
            raise ValueError(f"Unsafe installer payload path: {name}")
    if len({name.lower() for name in paths}) != len(paths):
        raise ValueError("Case-colliding installer payload")
    return ('\r\n'.join([PRODUCT_ID, *(name.replace('/', '\\') for name in paths)]) + '\r\n').encode('utf-8-sig')


def installed_files(directory: Path) -> dict[str, str]:
    """Runtime data and Inno's own generated uninstaller are not build inputs."""
    if not directory.is_dir() or directory.is_symlink() or directory.is_junction():
        raise ValueError("Invalid installed directory")
    files = {}
    for path in directory.rglob('*'):
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError(f"Linked installed file: {path}")
        name = path.relative_to(directory).as_posix()
        if name.split('/')[0] in {'logs', 'state'} or re.fullmatch(r'unins\d+\.(exe|dat|msg)', name):
            continue
        if path.is_file():
            with path.open('rb') as stream:
                files[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return files


def verify_registration(directory: Path, version: str) -> list[str]:
    import winreg
    problems = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            location = winreg.QueryValueEx(key, 'InstallLocation')[0]
            actual_version = winreg.QueryValueEx(key, 'DisplayVersion')[0]
            command = winreg.QueryValueEx(key, 'UninstallString')[0]
        if Path(location).resolve() != directory.resolve():
            problems.append('uninstall registration points to a different directory')
        if actual_version != version:
            problems.append('uninstall registration version differs')
        uninstall = Path(command.strip('"'))
        if uninstall.parent.resolve() != directory.resolve() or not uninstall.is_file():
            problems.append('missing or misplaced uninstaller')
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, IDENTITY_KEY, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            icon = winreg.QueryValueEx(key, 'IconUri')[0]
            name = winreg.QueryValueEx(key, 'DisplayName')[0]
            activator = winreg.QueryValueEx(key, 'CustomActivator')[0]
        if (Path(icon).resolve() != (directory / 'assets/ui/e7auto-icon-64.png').resolve()
                or name != 'E7auto' or activator != '{5914C5E5-CC54-4A2B-A664-DDF892B3D861}'):
            problems.append('installed notification identity differs')
    except (OSError, ValueError, TypeError) as exc:
        problems.append(f'invalid installation registration: {exc}')
    return problems
