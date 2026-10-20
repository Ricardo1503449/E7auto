"""Validate an installed update handoff and launch the existing Windows installer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from threading import Event
import winreg

import pythoncom
import win32api
from win32com.shell import shell, shellcon

from e7auto import __version__
from e7auto.platform.installation import is_installed_build

PRODUCT_ID = "{438C8C44-EB0D-48EA-BA98-6B4589FE35B2}"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{PRODUCT_ID}_is1"
JOB_ID = re.compile(r"v\d+\.\d+\.\d+-[0-9a-f]{32}")


def ordinary_path(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or item.is_junction():
            raise ValueError("更新路径不能包含符号链接或目录联接。")


def validate_installation(root: Path) -> None:
    if not is_installed_build():
        raise ValueError("当前为源码运行，请使用安装版执行自动更新。")
    ordinary_path(root)
    if Path(sys.executable).resolve() != (root / "E7auto.exe").resolve():
        raise ValueError("当前程序与安装目录不一致，无法自动更新。")
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINSTALL_KEY, 0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        installed, _ = winreg.QueryValueEx(key, "InstallLocation")
    if Path(installed).resolve() != root.resolve():
        raise ValueError("安装登记与当前目录不一致，请通过安装包更新。")
    manifest = root / 'install-files.txt'
    ordinary_path(manifest)
    lines = manifest.read_text(encoding="utf-8-sig").splitlines()
    if not lines or lines[0] != PRODUCT_ID:
        raise ValueError("安装文件清单不属于 E7auto。")


def _job(root: Path, job_id: str) -> Path:
    if not JOB_ID.fullmatch(job_id):
        raise ValueError("更新任务编号无效。")
    directory = root / "state" / "updates" / job_id
    ordinary_path(directory)
    return directory


def launch_update(root: Path, installer: Path, version: str, size: int, digest: str,
                  cancelled: Event) -> bool:
    """Called on a COM-initialized worker; never kills the running application."""
    validate_installation(root)
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise ValueError("安装版本无效。")
    if tuple(map(int, version[1:].split('.'))) <= tuple(map(int, __version__.split('.'))):
        raise ValueError("更新版本必须高于当前版本。")
    directory = _job(root, installer.parent.name)
    if (not directory.name.startswith(version + '-')
            or installer != directory / f"E7auto_{version}_Setup_x64.exe"):
        raise ValueError("安装包不属于本次更新目录。")
    ordinary_path(installer)
    if installer.stat().st_size != size:
        raise ValueError("安装包大小发生变化，请重新下载。")
    checksum = hashlib.sha256()
    with installer.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError("下载文件不是 Windows 安装包。")
        stream.seek(0)
        while block := stream.read(1024 * 1024):
            if cancelled.is_set():
                return False
            checksum.update(block)
    if checksum.hexdigest() != digest:
        raise ValueError("安装包校验失败，请重新下载。")
    info = win32api.GetFileVersionInfo(str(installer), '\\')
    actual_version = (info['FileVersionMS'] >> 16, info['FileVersionMS'] & 0xFFFF,
                      info['FileVersionLS'] >> 16, info['FileVersionLS'] & 0xFFFF)
    if actual_version != (*map(int, version[1:].split('.')), 0):
        raise ValueError("安装包内的版本与发布版本不一致，请重新检查更新。")
    if cancelled.is_set():
        return False
    arguments = ['/SP-', '/SILENT', '/NORESTART', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS',
                 '/E7AUTOUPDATE=1', f'/E7AUTOUPDATEID={directory.name}',
                 f'/DIR={root}', f'/LOG={directory / "install.log"}']
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED | pythoncom.COINIT_DISABLE_OLE1DDE)
    try:
        if cancelled.is_set():
            return False
        result = shell.ShellExecuteEx(
            # pywin32 exposes SEE_MASK_NOASYNC under its equivalent legacy name.
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS | shellcon.SEE_MASK_FLAG_DDEWAIT,
            lpVerb='runas', lpFile=str(installer), lpParameters=subprocess.list2cmdline(arguments),
            lpDirectory=str(root), nShow=1,
        )
        handle = result.get('hProcess')
        if handle is None:
            raise OSError("安装器未返回进程句柄。")
        win32api.CloseHandle(handle)
        return True
    finally:
        pythoncom.CoUninitialize()


def cleanup_applied_update(root: Path, job_id: str) -> None:
    """After setup exits, delete only the verified package for this running version."""
    validate_installation(root)
    directory = _job(root, job_id)
    metadata = directory / 'request.json'
    ordinary_path(metadata)
    data = json.loads(metadata.read_text(encoding='utf-8'))
    version = f'v{__version__}'
    if not isinstance(data, dict) or data.get('version') != version or not job_id.startswith(version + '-'):
        return
    name = f'E7auto_{version}_Setup_x64.exe'
    installer = directory / name
    ordinary_path(installer)
    if (data.get('name') != name or not installer.is_file()
            or installer.stat().st_size != data.get('size')):
        return
    with installer.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if digest == data.get('sha256'):
        # The setup loader can briefly outlive the setup mutex and retain its EXE.
        for attempt in range(20):
            try:
                installer.unlink(missing_ok=True)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.25)
    # request.json and install.log remain as small, per-update diagnostic records.
