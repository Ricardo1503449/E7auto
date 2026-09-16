"""Read-only checks for source layout and ignored local development artifacts."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import unquote

from scripts.common.paths import PROJECT_ROOT
from scripts.project.check_dependencies import check_dependencies

RUN_ID = re.compile(r"\d{8}-\d{6}-[a-z][a-z0-9-]*-[0-9a-f]{8}")
VERSION = re.compile(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")
POLICY = "scripts/project/layout.json"
TEXT_RESULTS = {".json", ".yaml", ".yml", ".md", ".txt", ".log", ".exit", ".csv", ".tsv", ".xml", ".sha256"}
PREVIEWS = {".png", ".jpg", ".jpeg", ".svg", ".webp", ".html", ".json"}


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def _files(root: Path, names: list[str], errors: list[str]) -> set[str]:
    found = set()
    for name in names:
        base = root / name
        if not base.exists():
            continue
        if base.is_symlink() or base.is_junction():
            errors.append(f"Linked layout root is not allowed: {name}")
            continue
        for directory, children, files in os.walk(base, followlinks=False):
            for child in children[:]:
                path = Path(directory) / child
                if path.is_symlink() or path.is_junction():
                    errors.append(f"Linked directory is not allowed: {path.relative_to(root).as_posix()}")
                    children.remove(child)
                elif child in {"__pycache__", ".pytest_cache"}:
                    children.remove(child)
            for file in files:
                path = Path(directory) / file
                if path.is_symlink():
                    errors.append(f"Linked file is not allowed: {path.relative_to(root).as_posix()}")
                else:
                    found.add(path.relative_to(root).as_posix())
    return found


def check_layout(root: Path = PROJECT_ROOT, *, staged: bool = False) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    if staged:
        entries = _git(root, "ls-files", "--stage", "-z").split(b"\0")
        blobs = {}
        for entry in entries:
            if not entry:
                continue
            info, raw_name = entry.split(b"\t", 1)
            mode, oid, stage = info.split()
            name = raw_name.decode("utf-8")
            if stage != b"0":
                errors.append(f"Unmerged index entry: {name}")
            if mode == b"120000" and name.split("/")[0] in {"docs", "tests", "artifacts", "build", "src"}:
                errors.append(f"Staged symlink in managed directory: {name}")
            blobs[name] = oid.decode()
        paths = set(blobs)

        def read(name: str) -> bytes:
            if name not in blobs:
                raise FileNotFoundError(name)
            return _git(root, "cat-file", "blob", blobs[name])

        def exists(name: str) -> bool:
            return name in paths or any(p.startswith(name.rstrip("/") + "/") for p in paths)
    else:
        paths = _files(root, ["docs", "tests", "artifacts", "build", "scripts", "src"], errors)
        paths.update(name for name in ["README.md", "AGENTS.md", "pyproject.toml"] if (root / name).is_file())

        def read(name: str) -> bytes:
            return (root / name).read_bytes()

        def exists(name: str) -> bool:
            return (root / name).exists()
    try:
        policy = json.loads(read(POLICY))
        if policy["schema_version"] != 1:
            raise ValueError("unsupported schema")
        features = set(policy["features"])
        required_lists = ["artifact_categories", "task_sections", "archive_categories", "docs_sections",
                          "test_sections", "test_script_sections", "build_sections"]
        if not features or any(not re.fullmatch(r"[a-z][a-z0-9_]*", f) for f in features):
            raise ValueError("invalid feature registry")
        for key in required_lists:
            if not isinstance(policy[key], list) or not policy[key]:
                raise ValueError(f"invalid {key}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return errors + [f"Missing or invalid layout policy ({'index' if staged else 'worktree'}): {exc}"]

    if not staged:
        for top, key in [("docs", "docs_sections"), ("tests", "test_sections"),
                         ("artifacts", "artifact_categories"), ("build", "build_sections")]:
            directory = root / top
            if directory.is_dir():
                for child in directory.iterdir():
                    if child.is_dir() and child.name not in set(policy[key]) | {"__pycache__", ".pytest_cache"}:
                        errors.append(f"Unexpected top-level directory: {top}/{child.name}")

    runs: dict[str, tuple[str, str | None]] = {}
    archives: set[str] = set()
    for name in sorted(paths):
        p = PurePosixPath(name)
        parts, suffix = p.parts, p.suffix.lower()
        top = parts[0]
        if top == "docs":
            if name == "docs/README.md":
                continue
            if len(parts) < 3 or parts[1] not in policy["docs_sections"]:
                errors.append(f"Document outside its category: {name}")
                continue
            section = parts[1]
            if section != "calibration" and suffix not in ({".md", ".txt"} if section == "user" else {".md"}):
                errors.append(f"Raw output is not a document: {name}")
            if section == "validation" and (len(parts) < 4 or parts[2] not in features | {"templates"}):
                errors.append(f"Validation document needs a registered subject: {name}")
            if section == "releases" and (len(parts) < 4 or not VERSION.fullmatch(parts[2])):
                errors.append(f"Release document needs a version: {name}")
            if section == "calibration":
                if name == "docs/calibration/README.md":
                    continue
                known = {"docs/calibration/" + value for value in policy["calibration_records"].values()}
                registered = len(parts) == 4 and parts[2] == "registrations" and suffix == ".json"
                if name not in known and not registered:
                    errors.append(f"Unregistered calibration record: {name}")
        elif top == "tests":
            if name in {"tests/__init__.py", "tests/conftest.py", "tests/README.md"}:
                continue
            if len(parts) < 3 or parts[1] not in policy["test_sections"]:
                errors.append(f"Test outside its responsibility directory: {name}")
                continue
            if parts[1] == "fixtures":
                if len(parts) < 4 or parts[2] not in features:
                    errors.append(f"Fixture needs a registered owner: {name}")
                if suffix not in {".png", ".jpg", ".jpeg", ".npz", ".npy", ".json", ".yaml", ".txt", ".md"}:
                    errors.append(f"Unexpected fixture file type: {name}")
            elif suffix not in {".py", ".md"}:
                errors.append(f"Generated output in test source directory: {name}")
            if parts[1] == "scripts" and p.name != "__init__.py" and (
                len(parts) < 4 or parts[2] not in policy["test_script_sections"]
            ):
                errors.append(f"Tool test needs a tool category: {name}")
        elif top == "build":
            if name == "build/README.md":
                continue
            if staged:
                errors.append(f"Build output must not be staged: {name}")
            elif len(parts) < 4 or parts[1] not in policy["build_sections"] or not RUN_ID.fullmatch(parts[2]):
                errors.append(f"Build output needs a managed build ID: {name}")
            elif not any(f"artifacts/releases/{version}/{parts[2]}/task.json" in paths
                         for version in {PurePosixPath(s).parts[2] for s in paths if s.startswith('artifacts/releases/') and len(PurePosixPath(s).parts) > 2}):
                errors.append(f"Build output has no release task: {name}")
        elif top == "artifacts":
            if name == "artifacts/README.md":
                continue
            if staged:
                errors.append(f"Local artifact must not be staged: {name}")
                continue
            if len(parts) < 3 or parts[1] not in policy["artifact_categories"]:
                errors.append(f"Artifact outside its category: {name}")
                continue
            category = parts[1]
            if category == "template-registration":
                valid = name == "artifacts/template-registration/writer.lock" or (
                    len(parts) == 5 and parts[2] == "transactions" and re.fullmatch(r"[0-9a-f]{32}", parts[3])
                    and (parts[4] == "journal.json" or re.fullmatch(r"(?:record|template|manifest)\.(?:old|new)", parts[4]))
                )
                if not valid:
                    errors.append(f"Unexpected registration workspace entry: {name}")
                continue
            if category == "template-candidates":
                if len(parts) < 4 or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", parts[2]):
                    errors.append(f"Candidate output needs the exporter run directory: {name}")
                elif suffix not in {".png", ".json", ".yaml", ".yml", ".md"}:
                    errors.append(f"Unexpected candidate file type: {name}")
                continue
            if category == "archive":
                if len(parts) < 5 or parts[2] not in policy["archive_categories"]:
                    errors.append(f"Archive needs a category and inventory: {name}")
                else:
                    archives.add("/".join(parts[:4]))
                continue
            start = 4 if category in {"tasks", "releases"} else 3
            if len(parts) <= start or not RUN_ID.fullmatch(parts[start - 1]):
                errors.append(f"Artifact needs a managed run ID: {name}")
                continue
            owner = parts[2] if category in {"tasks", "releases"} else None
            if category == "tasks" and owner not in features:
                errors.append(f"Unregistered task owner: {name}")
            if category == "releases" and not VERSION.fullmatch(owner):
                errors.append(f"Invalid release version: {name}")
            run = "/".join(parts[:start])
            runs[run] = category, owner
            within = parts[start:]
            if within in [("task.json",), ("README.md",)]:
                continue
            if category == "tasks":
                if len(within) < 2 or within[0] not in policy["task_sections"]:
                    errors.append(f"Task output needs inputs/previews/results/scratch: {name}")
                elif within[0] == "previews" and suffix not in PREVIEWS:
                    errors.append(f"Executable or non-preview file in previews: {name}")
                elif within[0] == "results" and suffix not in TEXT_RESULTS:
                    errors.append(f"Unexpected task result file type: {name}")
            elif len(within) > 1 and within[0] != "scratch":
                errors.append(f"Unexpected artifact nesting: {name}")
            elif len(within) == 1 and suffix not in TEXT_RESULTS | ({".patch", ".nul", ".bin"} if category == "git" else set()):
                errors.append(f"Script or unexpected output belongs in scratch: {name}")

    for run, (kind, owner) in sorted(runs.items()):
        try:
            metadata = json.loads(read(run + "/task.json"))
            expected_owner = metadata.get("feature") if kind == "tasks" else metadata.get("version") if kind == "releases" else None
            if (metadata["schema_version"] != 1 or metadata["kind"] != kind or expected_owner != owner
                    or metadata["run_id"] != PurePosixPath(run).name
                    or metadata["status"] not in {"active", "complete", "failed", "archived"}
                    or not metadata.get("subject") or not metadata.get("created_at")):
                raise ValueError("metadata does not match its directory")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"Invalid task ownership record: {run}: {exc}")
    for archive in sorted(archives):
        try:
            inventory = json.loads(read(archive + "/_archive.json"))["files"]
            actual = {p[len(archive) + 1:] for p in paths if p.startswith(archive + "/") and p != archive + "/_archive.json"}
            if actual != set(inventory):
                raise ValueError("inventory mismatch; new outputs cannot be added to a frozen archive")
            for relative, digest in inventory.items():
                if PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
                    raise ValueError("unsafe archive inventory path")
                if hashlib.sha256(read(archive + "/" + relative)).hexdigest() != digest:
                    raise ValueError(f"archived content changed: {relative}")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"Invalid frozen archive: {archive}: {exc}")

    # Resolve local Markdown links against the same view being checked (index or disk).
    for name in sorted(paths):
        if not ((name.startswith("docs/") and name.endswith(".md")) or name in {"README.md", "AGENTS.md", "tests/README.md", "artifacts/README.md"}):
            continue
        text = read(name).decode("utf-8-sig")
        for match in re.finditer(r"!?\[[^\]\n]*\]\((<[^>]+>|[^)\s]+)(?:\s+[^)]*)?\)", text):
            target = match.group(1).strip("<>")
            if target.startswith("#") or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                continue
            target = unquote(target.split("#", 1)[0])
            candidate = (root / target.lstrip("/")) if target.startswith("/") else root / PurePosixPath(name).parent / target
            # Index validation must not follow unstaged filesystem links.
            absolute = Path(os.path.abspath(candidate)) if staged else candidate.resolve()
            if not absolute.is_relative_to(root) or not exists(absolute.relative_to(root).as_posix()):
                errors.append(f"Broken local link: {name} -> {target}")
    # Guard the active build contract, without executing a build or game operation.
    build_script = "scripts/release/build-standalone.ps1"
    if build_script in paths:
        source = read(build_script).decode("utf-8-sig")
        if "--output-dir=dist" in source or '"docs") $usageFileName' in source:
            errors.append("Build script still uses the old output/document layout")
    # Production tools must obtain task/build paths from the shared owner. This
    # catches direct literal defaults; it deliberately does not execute code.
    def literal_path(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value.replace("\\", "/").strip("/").split("/")
        if isinstance(node, ast.Name) and node.id in {"root", "ROOT", "PROJECT_ROOT", "project_root"}:
            return ["<root>"]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            left, right = literal_path(node.left), literal_path(node.right)
            return left + right if left is not None and right is not None else None
        return None

    for name in sorted(paths):
        if not name.startswith("scripts/") or not name.endswith(".py") or name in {
            "scripts/common/paths.py", "scripts/project/check_layout.py"
        }:
            continue
        try:
            syntax = ast.parse(read(name).decode("utf-8-sig"))
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f"Cannot inspect tool output defaults: {name}: {exc}")
            continue
        for node in ast.walk(syntax):
            if not isinstance(node, ast.BinOp):
                continue
            segments = literal_path(node)
            if not segments or len(segments) < 2 or segments[0] != "<root>":
                continue
            if segments[1] in {"artifacts", "build"}:
                registration_root = name == "scripts/templates/register.py" and segments[1:] in [
                    ["artifacts", "template-registration"], ["artifacts", "template-candidates"]
                ]
                if not registration_root:
                    errors.append(f"Tool must use shared artifact/build paths: {name}:{node.lineno}")
            if name.startswith(("scripts/validation/", "scripts/calibration/")) and segments[1] == "logs":
                errors.append(f"Development validation must not default to runtime logs: {name}:{node.lineno}")
    errors.extend(check_dependencies(paths, read, policy))
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Read exact index contents; do not inspect the working copy")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    try:
        errors = check_layout(args.root, staged=args.staged)
    except (OSError, ValueError) as exc:
        errors = [str(exc)]
    print(json.dumps({"mode": "staged" if args.staged else "worktree", "problems": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
