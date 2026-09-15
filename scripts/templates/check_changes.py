"""Read-only provenance check for working-tree, staged or committed template changes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

from scripts.common.paths import PROJECT_ROOT


FOLDERS = ("assets/templates", "docs/calibration/registrations")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def snapshot(root: Path, revision: str | None = None, *, staged: bool = False) -> dict[str, bytes]:
    files = {}
    if revision is not None or staged:
        if staged:
            entries = git(root, "ls-files", "--stage", "-z", "--", *FOLDERS)
        else:
            commit = git(root, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}").decode().strip()
            entries = git(root, "ls-tree", "-r", "-z", commit, "--", *FOLDERS)
        for entry in entries.split(b"\0"):
            if not entry:
                continue
            metadata, path = entry.split(b"\t", 1)
            mode, second, third = metadata.decode().split()
            if mode not in {"100644", "100755"} or (staged and third != "0"):
                raise ValueError(f"Unsupported or unmerged resource: {path!r}")
            files[path.decode("utf-8")] = git(root, "cat-file", "blob", second if staged else third)
    else:
        for folder in FOLDERS:
            directory = root / folder
            if directory.is_symlink():
                raise ValueError(f"Resource directory is a symlink: {directory}")
            for path in directory.rglob("*"):
                if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError(f"Resource escapes project or is a symlink: {path}")
                if path.is_file():
                    files[path.relative_to(root).as_posix()] = path.read_bytes()
    return files


def catalogs(files: dict[str, bytes]) -> dict[str, dict]:
    return {path: json.loads(data) for path, data in files.items()
            if re.fullmatch(r"assets/templates/[a-z][a-z0-9_]*/manifest\.json", path)}


def template_path(catalog_path: str, filename: str) -> str:
    path = PurePosixPath(filename)
    if (not filename or "\\" in filename or ":" in filename or path.is_absolute()
            or ".." in path.parts or path.suffix != ".png"):
        raise ValueError(f"Invalid template filename: {filename}")
    return (PurePosixPath(catalog_path).parent / path).as_posix()


def check(base: dict[str, bytes], current: dict[str, bytes]) -> tuple[list[str], int]:
    problems = []
    covered = set()
    changed_count = 0
    old_catalogs = catalogs(base)
    for catalog_path, catalog in catalogs(current).items():
        feature = PurePosixPath(catalog_path).parent.name
        try:
            baseline = catalog["baseline"]
            if (catalog["schema_version"] != 1 or len(baseline) != 2
                    or any(type(n) is not int or n <= 0 for n in baseline)
                    or not isinstance(catalog["templates"], dict) or not catalog["templates"]):
                raise ValueError("Invalid catalog schema/baseline")
            previous = old_catalogs.get(catalog_path, {}).get("templates", {})
            for key, entry in catalog["templates"].items():
                label = f"{feature}/{key}"
                path = template_path(catalog_path, entry["file"])
                if path in covered:
                    problems.append(f"{label}: template file has multiple owners")
                covered.add(path)
                data = current.get(path)
                if data is None or digest(data) != entry["sha256"]:
                    problems.append(f"{label}: PNG missing or manifest hash mismatch")
                    continue
                if (len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR"
                        or not all(0 < int.from_bytes(data[start:start+4], "big") <= limit
                                   for start, limit in zip((16, 20), baseline))):
                    problems.append(f"{label}: invalid PNG dimensions/header")
                old_entry = previous.get(key)
                changed = old_entry is None or base.get(path) != data or old_entry.get("file") != entry["file"]
                changed_count += int(changed)
                source = entry.get("source", {})
                if not isinstance(source, dict):
                    problems.append(f"{label}: invalid source metadata")
                    continue
                if "record" not in source:
                    if changed or (old_entry and "record" in old_entry.get("source", {})):
                        problems.append(f"{label}: missing registration source; use register prepare/apply")
                    continue
                record = source["record"]
                record_hash = source.get("record_sha256", "")
                if not isinstance(record, str) or not re.fullmatch(r"[0-9a-f]{64}", record_hash):
                    problems.append(f"{label}: invalid registration record/hash")
                    continue
                # register.py binds the record filename to the template and source
                # digests. Reusing old provenance after updating only PNG+hash fails.
                expected = f"docs/calibration/registrations/{feature}-{key}-{entry['sha256'][:12]}-{record_hash[:12]}"
                if record not in {expected + suffix for suffix in (".json", ".yaml", ".yml")}:
                    problems.append(f"{label}: registration record does not match current template")
                if record not in current or digest(current[record]) != record_hash:
                    problems.append(f"{label}: registration record missing or hash mismatch (include it in the same change)")
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            problems.append(f"{catalog_path}: {exc}")
    for path, data in current.items():
        if path.startswith("assets/templates/") and path.lower().endswith(".png") and path not in covered:
            # Unchanged legacy files are allowed; new/changed unregistered PNGs fail.
            if base.get(path) != data:
                problems.append(f"{path}: changed PNG is not registered in a feature manifest")
    # Removing a manifest must not hide still-existing templates from validation.
    for path in old_catalogs.keys() - catalogs(current).keys():
        prefix = str(PurePosixPath(path).parent) + "/"
        if any(name.startswith(prefix) and name.endswith(".png") for name in current):
            problems.append(f"{path}: manifest removed while template PNGs remain")
    return problems, changed_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--base", default="HEAD", help="Baseline commit, or EMPTY for the initial commit (default: HEAD)")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--staged", action="store_true", help="Read the Git index, not working files")
    scope.add_argument("--revision", help="Read a commit, e.g. HEAD in CI")
    args = parser.parse_args()
    try:
        base = {} if args.base == "EMPTY" else snapshot(args.project_root, args.base)
        current = snapshot(args.project_root, args.revision, staged=args.staged)
        problems, count = check(base, current)
        print(json.dumps({"scope": "staged" if args.staged else args.revision or "worktree",
                          "base": args.base, "changed_templates": count, "problems": problems},
                         ensure_ascii=False, indent=2))
        return int(bool(problems))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
