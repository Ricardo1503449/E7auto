from __future__ import annotations

import importlib.metadata
import json
import site
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    expected = (root / ".venv").resolve()
    lock_names = {
        line.split("==", 1)[0].casefold()
        for line in (root / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "==" in line
    }
    installed = {
        distribution.metadata["Name"].casefold()
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    report = {
        "prefix": str(Path(sys.prefix).resolve()),
        "expected_prefix": str(expected),
        "base_prefix": str(Path(sys.base_prefix).resolve()),
        "user_site_enabled": bool(site.ENABLE_USER_SITE),
        "user_site_on_sys_path": site.getusersitepackages() in sys.path,
        "locked_packages_missing": sorted(lock_names - installed),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if (
        Path(sys.prefix).resolve() == expected
        and Path(sys.base_prefix).resolve() != expected
        and not site.ENABLE_USER_SITE
        and site.getusersitepackages() not in sys.path
        and not report["locked_packages_missing"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())

