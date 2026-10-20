"""External Node/npm selection and owned development-run validation."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

from scripts.common.paths import development_tool_path, development_run_directory


def external_node(node: str | None = None, npm_cli: str | None = None) -> tuple[Path, Path]:
    config = development_tool_path('tool_paths', kind='states')
    settings = json.loads(config.read_text(encoding='utf-8')) if config.is_file() else {}
    if settings and settings.get('schema_version') != 1:
        raise ValueError('Unsupported private tool-path configuration')
    selected = node or settings.get('node_executable') or shutil.which('node')
    if not selected:
        raise ValueError('Configure an external Node.js 22+ executable')
    executable = Path(selected).resolve()
    if not executable.is_file():
        raise ValueError('Configured external Node executable is missing; update tool paths')
    version = subprocess.check_output([str(executable),'--version'],text=True,encoding='utf-8').strip()
    match = re.fullmatch(r'v(\d+)\.\d+\.\d+',version)
    if not match or int(match[1]) < 22:
        raise ValueError('Selected Node must be version 22 or newer; no implicit fallback')
    npm = Path(npm_cli or settings.get('npm_cli') or executable.parent/'node_modules/npm/bin/npm-cli.js').resolve()
    if not npm.is_file():
        raise ValueError('Configure the external npm/bin/npm-cli.js path')
    return executable,npm
