"""Check release preparation and bind a verified ZIP to its final Git inputs."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib
import zipfile

from scripts.common.paths import PROJECT_ROOT, release_runs_dir


INPUT_DIRS = ("src/e7auto", "assets/templates", "assets/ui", "scripts/release", "scripts/common")
INPUT_FILES = ("launcher.py", "pyproject.toml", "requirements.lock", "config/internal.yaml", "docs/user/使用说明.txt")
SNAPSHOT = "build-inputs.json"
ARTIFACT = "build-artifact.json"
VALIDATION = "release-verification.json"


def _git(root: Path, *args: str, data: bytes | None = None) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, input=data, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(path: Path, value: dict) -> None:
    if path.is_symlink() or not path.resolve().is_relative_to(path.parent.resolve()):
        raise ValueError(f"Linked release record: {path}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def preflight(root: Path = PROJECT_ROOT) -> str:
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        raise ValueError("Invalid release version")
    tree = ast.parse((root / "src/e7auto/__init__.py").read_text(encoding="utf-8"))
    source_versions = [ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)]
    readme = (root / "README.md").read_text(encoding="utf-8")
    guide = (root / "docs/user/使用说明.txt").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if source_versions != [version] or f"当前检入源码版本为 `v{version}`" not in readme:
        raise ValueError("Project, source and README versions are not synchronized")
    if not guide.startswith(f"E7auto v{version} x64 使用说明\n"):
        raise ValueError("Usage guide version is not synchronized")
    guide_versions = set(re.findall(r"E7auto_v([0-9A-Za-z.+-]+)_x64\.zip", guide))
    if guide_versions != {version}:
        raise ValueError("Usage guide archive name is not synchronized")
    if not re.search(rf"^## v{re.escape(version)} — \d{{4}}-\d{{2}}-\d{{2}}$", changelog, re.M):
        raise ValueError("CHANGELOG version/date entry is missing")
    if re.search(r"^### (?:发布准备|本机构建|发布)\s*$", changelog, re.M):
        raise ValueError("CHANGELOG must not contain release-process sections")
    if re.search(r"待构建源码|尚未构建|尚未发布GitHub|已完成本机验证并发布", readme + guide):
        raise ValueError("README/usage guide contains transient release status")
    return version


def _run_directory(run: Path, root: Path) -> Path:
    run = run.absolute()
    base = release_runs_dir(root).resolve()
    resolved = run.resolve()
    if not resolved.is_relative_to(base) or run.is_symlink() or run.is_junction():
        raise ValueError("Release records must use a managed release directory")
    relative = resolved.relative_to(base)
    if len(relative.parts) != 2:
        raise ValueError("Release records need a version and run ID")
    record = _read(resolved / "task.json")
    if (record.get("kind"), record.get("version"), record.get("run_id")) != ("releases", relative.parts[0], relative.parts[1]):
        raise ValueError("Release directory ownership mismatch")
    return resolved


def _included(name: str) -> bool:
    parts = Path(name).parts
    return "__pycache__" not in parts and not name.endswith((".pyc", ".pyo"))


def _input_paths(root: Path) -> list[str]:
    names = set(INPUT_FILES)
    if (root / ".gitattributes").exists():
        names.add(".gitattributes")
    for directory in INPUT_DIRS:
        base = root / directory
        if not base.is_dir():
            raise ValueError(f"Missing input directory: {directory}")
        for path in [base, *base.rglob("*")]:
            if not _included(path.relative_to(root).as_posix()):
                continue
            if path.is_symlink() or path.is_junction():
                raise ValueError(f"Linked build input: {path}")
            if path.is_file():
                names.add(path.relative_to(root).as_posix())
    for name in names:
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Missing or linked build input: {name}")
    return sorted(names)


def _inputs(root: Path) -> dict:
    result = {}
    for name in _input_paths(root):
        content = (root / name).read_bytes()
        # Text follows Git's real filters; image bytes must survive Git unchanged.
        filter_option = "--no-filters" if Path(name).suffix.lower() in {".png", ".ico"} else "--path=" + name
        blob = _git(root, "hash-object", filter_option, "--stdin", data=content).decode().strip()
        result[name] = {"sha256": _sha(content), "git_blob": blob}
    return result


def capture(run: Path, root: Path = PROJECT_ROOT) -> dict:
    version = preflight(root)
    run = _run_directory(run, root)
    if run.parent.name != "v" + version or (run / SNAPSHOT).exists():
        raise ValueError("Wrong release version or existing immutable input snapshot")
    result = {"schema_version": 1, "version": version,
              "base_revision": _git(root, "rev-parse", "HEAD").decode().strip(),
              "inputs": _inputs(root)}
    _write(run / SNAPSHOT, result)
    return result


def _snapshot(run: Path, root: Path) -> dict:
    snapshot = _read(run / SNAPSHOT)
    if snapshot.get("schema_version") != 1 or run.parent.name != "v" + snapshot["version"]:
        raise ValueError("Invalid build input snapshot")
    return snapshot


def _check_inputs(snapshot: dict, root: Path, *, exact: bool) -> None:
    current = _inputs(root)
    expected = snapshot["inputs"]
    field = "sha256" if exact else "git_blob"
    changed = sorted(name for name in set(current) | set(expected)
                     if current.get(name, {}).get(field) != expected.get(name, {}).get(field))
    if changed:
        raise ValueError("Build inputs changed; rebuild required: " + ", ".join(changed))


def _release_files(directory: Path) -> dict[str, str]:
    if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
        raise ValueError("Invalid standalone directory")
    files = {}
    for path in directory.rglob("*"):
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(directory.resolve()):
            raise ValueError(f"Linked release file: {path}")
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = _file_sha(path)
    if not files:
        raise ValueError("Empty standalone directory")
    return files


def seal(run: Path, archive: Path, directory: Path, root: Path = PROJECT_ROOT) -> dict:
    run = _run_directory(run, root)
    snapshot = _snapshot(run, root)
    if (run / ARTIFACT).exists():
        raise ValueError("Build artifact record already exists; create a new build")
    _check_inputs(snapshot, root, exact=True)
    files = _release_files(directory)
    with zipfile.ZipFile(archive) as zipped:
        entries = [entry for entry in zipped.infolist() if not entry.is_dir()]
        names = [entry.filename.replace("\\", "/") for entry in entries]
        if len(set(names)) != len(names) or set(names) != set(files):
            raise ValueError("ZIP file list does not match standalone directory")
        for name, entry in zip(names, entries):
            with zipped.open(entry) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != files[name]:
                    raise ValueError(f"ZIP content differs: {name}")
    # The unchanged inputs must also be the resources actually packaged.
    for name, hashes in snapshot["inputs"].items():
        target = "使用说明.txt" if name == "docs/user/使用说明.txt" else name
        if name.startswith("assets/") or name in {"config/internal.yaml", "docs/user/使用说明.txt"}:
            if files.get(target) != hashes["sha256"]:
                raise ValueError(f"Packaged input differs from build snapshot: {name}")
    result = {"schema_version": 1, "version": snapshot["version"],
              "snapshot_sha256": _file_sha(run / SNAPSHOT),
              "archive_name": f"E7auto_v{snapshot['version']}_x64.zip",
              "archive_sha256": _file_sha(archive), "archive_size": archive.stat().st_size,
              "release_files": files}
    _write(run / ARTIFACT, result)
    return result


def _artifact(run: Path, root: Path) -> tuple[dict, Path]:
    artifact = _read(run / ARTIFACT)
    snapshot = _snapshot(run, root)
    expected_name = f"E7auto_v{snapshot['version']}_x64.zip"
    if (artifact.get("schema_version") != 1 or artifact.get("version") != snapshot["version"]
            or artifact.get("snapshot_sha256") != _file_sha(run / SNAPSHOT)
            or artifact.get("archive_name") != expected_name):
        raise ValueError("Artifact and input snapshot do not match")
    archive = root / "dist" / expected_name
    if archive.is_symlink() or not archive.resolve().is_relative_to((root / "dist").resolve()):
        raise ValueError("Linked release archive")
    if archive.stat().st_size != artifact["archive_size"] or _file_sha(archive) != artifact["archive_sha256"]:
        raise ValueError("Release archive changed after build verification")
    return artifact, archive


def record_validation(run: Path, report: dict, root: Path = PROJECT_ROOT) -> None:
    run = _run_directory(run, root)
    artifact, _ = _artifact(run, root)
    _check_inputs(_snapshot(run, root), root, exact=False)
    if _release_files(root / "dist/launcher.dist") != artifact["release_files"]:
        raise ValueError("Standalone files changed after ZIP verification")
    _write(run / VALIDATION, {"artifact_sha256": _file_sha(run / ARTIFACT), "report": report})


def check(run: Path, revision: str = "HEAD", root: Path = PROJECT_ROOT) -> dict:
    run = _run_directory(run, root)
    snapshot = _snapshot(run, root)
    if preflight(root) != snapshot["version"]:
        raise ValueError("Current version differs from the build snapshot")
    artifact, archive = _artifact(run, root)
    _check_inputs(snapshot, root, exact=False)
    commit = _git(root, "rev-parse", "--verify", revision + "^{commit}").decode().strip()
    tree = _git(root, "ls-tree", "-r", "-z", commit, "--", *INPUT_DIRS, *INPUT_FILES, ".gitattributes")
    blobs = {}
    for row in tree.split(b"\0"):
        if not row:
            continue
        info, path = row.split(b"\t", 1)
        mode, kind, oid = info.decode().split()
        name = path.decode("utf-8")
        if _included(name):
            if mode not in {"100644", "100755"} or kind != "blob":
                raise ValueError(f"Unsupported committed build input: {name}")
            blobs[name] = oid
    if blobs != {name: info["git_blob"] for name, info in snapshot["inputs"].items()}:
        raise ValueError("Final commit inputs differ from the verified build")
    validation = _read(run / VALIDATION)
    report = validation.get("report", {})
    self_check = report.get("self_check") or {}
    if (validation.get("artifact_sha256") != _file_sha(run / ARTIFACT)
            or report.get("problems") != [] or not self_check.get("compiled")
            or self_check.get("version") != snapshot["version"]
            or not self_check.get("wgc_importable")):
        raise ValueError("Successful compiled release verification is required")
    result = {"version": snapshot["version"], "commit": commit, "archive": str(archive),
              "archive_sha256": artifact["archive_sha256"], "archive_size": artifact["archive_size"]}
    _write(run / "publish-check.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight")
    commands.add_parser("capture").add_argument("--run-dir", type=Path, required=True)
    seal_parser = commands.add_parser("seal")
    seal_parser.add_argument("--run-dir", type=Path, required=True)
    seal_parser.add_argument("--archive", type=Path, required=True)
    seal_parser.add_argument("--directory", type=Path, required=True)
    check_parser = commands.add_parser("check")
    check_parser.add_argument("--run-dir", type=Path, required=True)
    check_parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    try:
        if args.command == "preflight":
            result = {"version": preflight()}
        elif args.command == "capture":
            result = capture(args.run_dir)
        elif args.command == "seal":
            result = seal(args.run_dir, args.archive, args.directory)
        else:
            result = check(args.run_dir, args.revision)
    except (ValueError, OSError, KeyError, SyntaxError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Release workflow check failed: {exc}\n")
    if args.command in {"capture", "seal"}:
        result = {"command": args.command, "version": result["version"], "run_dir": str(args.run_dir)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
