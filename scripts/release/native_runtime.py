"""Normalize MSVC DLLs in generated build output and verify release consistency."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil

from e7auto.platform.native_runtime import MINIMUM_MSVC_VERSION, MSVC_FILES, dll_version, runtime_distribution_files
from scripts.common.paths import BUILD_NUITKA_DIR, PROJECT_ROOT


def verify_native_runtime(directory: Path) -> list[str]:
    problems = []
    for name in MSVC_FILES:
        if not (directory / name).is_file():
            problems.append(f"missing root MSVC runtime: {name}")
    for path in directory.rglob("*.dll"):
        if path.name.lower() not in MSVC_FILES:
            continue
        if not path.resolve().is_relative_to(directory.resolve()):
            problems.append(f"linked MSVC runtime escapes release: {path}")
            continue
        try:
            version = dll_version(path)
        except Exception as exc:
            problems.append(f"cannot read MSVC runtime version: {path}: {exc}")
        else:
            if version < MINIMUM_MSVC_VERSION:
                problems.append(f"outdated MSVC runtime: {path}: {version}")
    return problems


def synchronize_native_runtime(directory: Path) -> None:
    original = directory.absolute()
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("Linked compiled output directory is not allowed")
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError("Compiled output directory does not exist")
    if (original.is_relative_to(PROJECT_ROOT) or directory.is_relative_to(PROJECT_ROOT)) and not directory.is_relative_to(BUILD_NUITKA_DIR):
        raise ValueError("Project runtime synchronization is restricted to build/nuitka output")
    sources = runtime_distribution_files()
    targets = {directory / name for name in sources}
    targets.update(p for p in directory.rglob("*.dll") if p.name.lower() in sources)
    # Validate every destination before copying; do not touch source wheels or old releases.
    if any(p.is_symlink() or not p.resolve().is_relative_to(directory) for p in targets):
        raise ValueError("Linked native runtime destination is not allowed")
    for target in sorted(targets):
        shutil.copyfile(sources[target.name.lower()], target)
    problems = verify_native_runtime(directory)
    if problems:
        raise RuntimeError("; ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    synchronize_native_runtime(args.directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
