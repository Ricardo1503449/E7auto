from __future__ import annotations

import importlib.metadata
import json
import re
import site
import sys
import tomllib
from pathlib import Path

from e7auto import __version__


def _canonicalize_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def _locked_versions(path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        versions[_canonicalize_name(name)] = version
    return versions


def _project_version(path: Path) -> str:
    project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
    version = project.get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("pyproject.toml does not contain a static project version")
    return version


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    expected = (root / ".venv").resolve()
    locked = _locked_versions(root / "requirements.lock")
    installed = {
        _canonicalize_name(distribution.metadata["Name"]): distribution.version
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    project_version = _project_version(root / "pyproject.toml")
    try:
        installed_project_version = importlib.metadata.version("e7auto")
    except importlib.metadata.PackageNotFoundError:
        installed_project_version = None
    missing = sorted(set(locked) - set(installed))
    mismatched = [
        {
            "package": name,
            "expected": expected_version,
            "actual": installed[name],
        }
        for name, expected_version in sorted(locked.items())
        if name in installed and installed[name] != expected_version
    ]
    report = {
        "prefix": str(Path(sys.prefix).resolve()),
        "expected_prefix": str(expected),
        "base_prefix": str(Path(sys.base_prefix).resolve()),
        "user_site_enabled": bool(site.ENABLE_USER_SITE),
        "user_site_on_sys_path": site.getusersitepackages() in sys.path,
        "project_version": project_version,
        "source_version": __version__,
        "installed_project_version": installed_project_version,
        "locked_packages_missing": missing,
        "locked_packages_mismatched": mismatched,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if (
        Path(sys.prefix).resolve() == expected
        and Path(sys.base_prefix).resolve() != expected
        and not site.ENABLE_USER_SITE
        and site.getusersitepackages() not in sys.path
        and project_version == __version__ == installed_project_version
        and not report["locked_packages_missing"]
        and not report["locked_packages_mismatched"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
