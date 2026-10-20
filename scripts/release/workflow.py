"""Bind the installer and verified installed payload to their final Git inputs."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib

from scripts.common.paths import PROJECT_ROOT, release_runs_dir, build_nuitka_dir
from scripts.release.installer import MANIFEST, installed_files, installer_name, manifest_bytes


INPUT_DIRS = ("src/e7auto", "assets/templates", "assets/ui", "scripts/release", "scripts/common")
INPUT_FILES = ("launcher.py", "pyproject.toml", "requirements.lock", "config/internal.yaml", "config/chaos.yaml", "docs/user/使用说明.md")
SNAPSHOT = "build-inputs.json"
ARTIFACT = "build-artifact.json"
VALIDATION = "release-verification.json"
PAYLOAD = "payload-files.json"


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
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid release version")
    tree = ast.parse((root / "src/e7auto/__init__.py").read_text(encoding="utf-8"))
    source_versions = [ast.literal_eval(node.value) for node in tree.body
                       if isinstance(node, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)]
    readme = (root / "README.md").read_text(encoding="utf-8")
    guide = (root / "docs/user/使用说明.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if source_versions != [version] or f"当前检入源码版本为 `v{version}`" not in readme:
        raise ValueError("Project, source and README versions are not synchronized")
    if not guide.startswith(f"# E7auto v{version} x64 使用说明\n"):
        raise ValueError("Usage guide version is not synchronized")
    guide_versions = set(re.findall(r"E7auto_v([0-9A-Za-z.+-]+)_Setup_x64\.exe", guide))
    if guide_versions != {version}:
        raise ValueError("Usage guide installer name is not synchronized")
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


def _git_blobs(root: Path, names: list[str]) -> dict[str, str]:
    """Hash files in two batches, preserving Git filters and raw image bytes."""
    result = {}
    for raw in (False, True):
        group = [name for name in names if (Path(name).suffix.lower() in {".png", ".ico"}) == raw]
        if not group:
            continue
        # --stdin-paths accepts Git's C-quoted byte paths. Quote UTF-8 bytes,
        # including whitespace, quotes and backslashes, without shell parsing.
        data = b"".join(b'"' + b"".join(
            bytes((byte,)) if 32 <= byte < 127 and byte not in (34, 92)
            else f"\\{byte:03o}".encode("ascii")
            for byte in name.encode("utf-8")) + b'"\n' for name in group)
        options = ("--no-filters",) if raw else ()
        hashes = _git(root, "hash-object", "--stdin-paths", *options, data=data).decode("ascii").splitlines()
        if len(hashes) != len(group) or any(not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) for value in hashes):
            raise ValueError("Invalid batch Git hash response")
        result.update(zip(group, hashes, strict=True))
    return result


def _inputs(root: Path, *, fields: tuple[str, ...] = ("sha256", "git_blob")) -> dict:
    if not fields or set(fields) - {"sha256", "git_blob"}:
        raise ValueError("Unknown or empty input hash fields")
    names = _input_paths(root)
    contents = {name: (root / name).read_bytes() for name in names}
    blobs = _git_blobs(root, names) if "git_blob" in fields else {}
    if blobs and (names != _input_paths(root) or any(
            (root / name).read_bytes() != content for name, content in contents.items())):
        raise ValueError("Build inputs changed during hashing; retry with stable inputs")
    result = {name: ({"sha256": _sha(content)} if "sha256" in fields else {})
              for name, content in contents.items()}
    for name, oid in blobs.items():
        result[name]["git_blob"] = oid
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
    field = "sha256" if exact else "git_blob"
    current = _inputs(root, fields=(field,))
    expected = snapshot["inputs"]
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


def prepare(run: Path, directory: Path, root: Path = PROJECT_ROOT) -> dict:
    """Freeze the exact payload before ISCC reads it, including its owned-file list."""
    run = _run_directory(run, root)
    snapshot = _snapshot(run, root)
    base = build_nuitka_dir(root) / run.name
    if (directory.is_symlink() or directory.is_junction()
            or directory.resolve() != (base / 'launcher.dist').resolve()):
        raise ValueError("Payload must use this run's Nuitka build directory")
    if (run / PAYLOAD).exists() or (run / ARTIFACT).exists():
        raise ValueError("Payload already prepared; create a new build")
    _check_inputs(snapshot, root, exact=True)
    files = _release_files(directory)
    if MANIFEST in files:
        raise ValueError("Unexpected pre-existing installation manifest")
    (directory / MANIFEST).write_bytes(manifest_bytes(files))
    files[MANIFEST] = _file_sha(directory / MANIFEST)
    for name, hashes in snapshot["inputs"].items():
        target = "使用说明.md" if name == "docs/user/使用说明.md" else name
        if name.startswith("assets/") or name in {"config/internal.yaml", "config/chaos.yaml", "docs/user/使用说明.md"}:
            if files.get(target) != hashes["sha256"]:
                raise ValueError(f"Packaged input differs from build snapshot: {name}")
    result = {"schema_version": 2, "version": snapshot['version'],
              "directory": directory.relative_to(root).as_posix(), "release_files": files}
    _write(run / PAYLOAD, result)
    return result


def seal(run: Path, installer: Path, directory: Path, root: Path = PROJECT_ROOT) -> dict:
    run = _run_directory(run, root)
    snapshot = _snapshot(run, root)
    if (run / ARTIFACT).exists():
        raise ValueError("Build artifact record already exists; create a new build")
    _check_inputs(snapshot, root, exact=True)
    prepared = _read(run / PAYLOAD)
    if (prepared.get('version') != snapshot['version']
            or directory.resolve() != (root / prepared['directory']).resolve()
            or _release_files(directory) != prepared['release_files']):
        raise ValueError('Installer payload changed during compilation')
    expected = installer_name(snapshot['version'])
    if installer.name != expected or installer.is_symlink() or not installer.is_file():
        raise ValueError('Invalid installer output')
    with installer.open('rb') as stream:
        if stream.read(2) != b'MZ':
            raise ValueError('Installer is not a Windows executable')
    result = {"schema_version": 2, "version": snapshot["version"],
              "snapshot_sha256": _file_sha(run / SNAPSHOT),
              "installer_name": expected,
              "installer_sha256": _file_sha(installer), "installer_size": installer.stat().st_size,
              "release_files": prepared['release_files'], "payload_directory": prepared['directory']}
    _write(run / ARTIFACT, result)
    return result


def _artifact(run: Path, root: Path) -> tuple[dict, Path]:
    artifact = _read(run / ARTIFACT)
    snapshot = _snapshot(run, root)
    expected_name = installer_name(snapshot['version'])
    if (artifact.get("schema_version") != 2 or artifact.get("version") != snapshot["version"]
            or artifact.get("snapshot_sha256") != _file_sha(run / SNAPSHOT)
            or artifact.get("installer_name") != expected_name):
        raise ValueError("Artifact and input snapshot do not match")
    archive = root / "dist" / expected_name
    if archive.is_symlink() or not archive.resolve().is_relative_to((root / "dist").resolve()):
        raise ValueError("Linked release archive")
    if archive.stat().st_size != artifact["installer_size"] or _file_sha(archive) != artifact["installer_sha256"]:
        raise ValueError("Release installer changed after build verification")
    return artifact, archive


def record_validation(run: Path, report: dict, root: Path = PROJECT_ROOT) -> None:
    run = _run_directory(run, root)
    artifact, _ = _artifact(run, root)
    _check_inputs(_snapshot(run, root), root, exact=False)
    directory = Path(report['release'])
    if directory.resolve().is_relative_to(build_nuitka_dir(root).resolve()):
        raise ValueError('Verify an installed copy, not the compiler payload')
    if installed_files(directory) != artifact["release_files"]:
        raise ValueError("Installed files differ from the sealed payload")
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
            or not self_check.get("wgc_importable") or report.get('installation_verified') is not True):
        raise ValueError("Successful compiled release verification is required")
    from scripts.release.lifecycle import validate_report
    validate_report(run, root)
    result = {"version": snapshot["version"], "commit": commit, "installer": str(archive),
              "installer_sha256": artifact["installer_sha256"], "installer_size": artifact["installer_size"]}
    _write(run / "publish-check.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight")
    commands.add_parser("capture").add_argument("--run-dir", type=Path, required=True)
    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('--run-dir', type=Path, required=True)
    prepare_parser.add_argument('--directory', type=Path, required=True)
    seal_parser = commands.add_parser("seal")
    seal_parser.add_argument("--run-dir", type=Path, required=True)
    seal_parser.add_argument("--installer", type=Path, required=True)
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
        elif args.command == 'prepare':
            result = prepare(args.run_dir, args.directory)
        elif args.command == "seal":
            result = seal(args.run_dir, args.installer, args.directory)
        else:
            result = check(args.run_dir, args.revision)
    except (ValueError, OSError, KeyError, SyntaxError) as exc:
        parser.exit(1, f"Release workflow check failed: {exc}\n")
    if args.command in {"capture", "prepare", "seal"}:
        result = {"command": args.command, "version": result["version"], "run_dir": str(args.run_dir)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
