"""Project paths shared by development tools, independent of process cwd."""
from pathlib import Path
from datetime import datetime
import json
import re
import uuid
import subprocess
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BUILD_NUITKA_DIR = PROJECT_ROOT / "build" / "nuitka"
TEMPLATES_DIR = PROJECT_ROOT / "assets" / "templates"
CALIBRATION_DIR = PROJECT_ROOT / "docs" / "calibration"
CANDIDATES_DIR = PROJECT_ROOT / "artifacts" / "template-candidates"
LAYOUT_POLICY = PROJECT_ROOT / "scripts" / "project" / "layout.json"


def build_nuitka_dir(root: Path = PROJECT_ROOT) -> Path:
    return root / "build" / "nuitka"


def build_staging_dir(root: Path = PROJECT_ROOT) -> Path:
    return root / "build" / "staging"


def release_runs_dir(root: Path = PROJECT_ROOT) -> Path:
    return root / "artifacts" / "releases"


def layout_policy() -> dict:
    return json.loads(LAYOUT_POLICY.read_text(encoding="utf-8"))


def development_tool_path(name: str, *, kind: str = "installations", root: Path = PROJECT_ROOT) -> Path:
    """Resolve registered local tools/cache/state, never a task-dependent default."""
    policy = json.loads((root / "scripts/project/layout.json").read_text(encoding="utf-8"))
    registry = policy["development_tools"]
    if registry["root"] != "tools":
        raise ValueError("Development tools must use the registered tools root")
    try:
        item = registry[kind][name]
    except KeyError as exc:
        raise ValueError(f"Unregistered development tool {kind}/{name}") from exc
    relative = item["path"] if kind == "installations" else item
    base = root.resolve() / registry["root"]
    parts = Path(relative)
    if parts.is_absolute() or ".." in parts.parts or base.is_symlink() or base.is_junction():
        raise ValueError("Invalid development tool path")
    resolved = (base / parts).resolve()
    cursor = base
    for part in parts.parts:
        cursor = cursor / part
        if cursor.is_symlink() or cursor.is_junction():
            raise ValueError("Linked development tool area is not allowed")
    if not resolved.is_relative_to(base.resolve()):
        raise ValueError("Development tool path escapes its registered root")
    return resolved


def development_tool_entry(name: str, *, root: Path = PROJECT_ROOT) -> Path:
    policy = json.loads((root / "scripts/project/layout.json").read_text(encoding="utf-8"))
    entry = policy["development_tools"]["installations"][name]["entry"]
    directory = development_tool_path(name, root=root)
    result = (directory / entry).resolve()
    if not result.is_relative_to(directory):
        raise ValueError("Development tool entry escapes its installation")
    return result


def development_run_directory(path: Path) -> Path:
    """Accept a managed maintenance run or a shop task for development logs."""
    path = path.resolve()
    metadata = json.loads((path / "task.json").read_text(encoding="utf-8"))
    if metadata.get("kind") == "tasks":
        return managed_task_directory(path, feature="shop")
    base = PROJECT_ROOT / "artifacts" / "maintenance"
    if (path.parent != base.resolve() or metadata.get("kind") != "maintenance"
            or metadata.get("run_id") != path.name or not metadata.get("subject")):
        raise ValueError("Tool output needs a managed maintenance run or shop task")
    return path


def calibration_record_path(name: str, directory: Path = CALIBRATION_DIR) -> Path:
    """Resolve named historical evidence without depending on the caller's cwd."""
    try:
        relative = layout_policy()["calibration_records"][name]
    except KeyError as exc:
        raise ValueError(f"Unknown calibration record: {name}") from exc
    candidate = (directory / relative).resolve()
    if not candidate.is_relative_to(directory.resolve()):
        raise ValueError(f"Calibration record escapes its directory: {name}")
    return candidate


def ensure_calibration_output_path(path: Path, record_name: str) -> Path:
    resolved = path.resolve()
    if resolved == calibration_record_path(record_name).resolve():
        return resolved
    return ensure_task_result_path(path)


def create_artifact_run(
    kind: str, subject: str, *, feature: str | None = None,
    version: str | None = None, root: Path = PROJECT_ROOT,
) -> Path:
    """Allocate a unique run, with ownership metadata; never overwrite a run."""
    if kind not in {"tasks", "releases", "git", "maintenance"}:
        raise ValueError(f"Unknown artifact kind: {kind}")
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", subject):
        raise ValueError("Subject must be a lowercase hyphenated identifier")
    if kind == "tasks" and (not isinstance(feature, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]*", feature)
            or feature not in layout_policy()["features"]):
        raise ValueError(f"Unregistered task feature: {feature}")
    if kind != "tasks" and feature is not None:
        raise ValueError("Only tasks accept a feature")
    if kind == "releases":
        if not version or not re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
            raise ValueError("Releases require an explicit version")
        version = version if version.startswith("v") else "v" + version
    elif version is not None:
        raise ValueError("Only releases accept a version")
    root = root.resolve()
    parent = root / "artifacts" / kind
    if kind == "tasks":
        parent /= feature
    elif kind == "releases":
        parent /= version
    if not parent.resolve().is_relative_to(root):
        raise ValueError("Artifact directory escapes project root")
    now = datetime.now().astimezone()
    run_id = f"{now:%Y%m%d-%H%M%S}-{subject}-{uuid.uuid4().hex[:8]}"
    output = parent / run_id
    output.mkdir(parents=True, exist_ok=False)
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    )
    metadata = {
        "schema_version": 1, "kind": kind, "subject": subject,
        "feature": feature, "version": version, "run_id": run_id,
        "created_at": now.isoformat(), "status": "active",
        "source_revision": revision.stdout.strip() if revision.returncode == 0 else None,
    }
    (output / "task.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sections = layout_policy()["task_sections"] if kind == "tasks" else ["scratch"]
    for section in sections:
        (output / section).mkdir()
    return output


def task_result_path(subject: str, feature: str, filename: str) -> Path:
    if Path(filename).name != filename or not filename:
        raise ValueError("Result filename must be a basename")
    result = create_artifact_run("tasks", subject, feature=feature) / "results" / filename
    print(f"Validation result: {result}", file=sys.stderr)
    return result


def finish_artifact_run(directory: Path, *, status: str = "complete") -> None:
    if status not in {"complete", "failed", "archived"}:
        raise ValueError("Invalid final task status")
    record = directory / "task.json"
    metadata = json.loads(record.read_text(encoding="utf-8-sig"))
    if metadata["run_id"] != directory.name:
        raise ValueError("Task ownership mismatch")
    metadata["status"] = status
    metadata["finished_at"] = datetime.now().astimezone().isoformat()
    record.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finish_validation_output(path: Path) -> None:
    """A written report completes the tool run; pass/fail remains in the report."""
    relative = path.resolve()
    if relative.is_relative_to(PROJECT_ROOT):
        parts = relative.relative_to(PROJECT_ROOT).parts
        if len(parts) >= 6 and parts[:2] == ("artifacts", "tasks") and parts[4] == "results":
            finish_artifact_run(PROJECT_ROOT.joinpath(*parts[:4]))


def ensure_task_result_path(path: Path, root: Path = PROJECT_ROOT) -> Path:
    """Explicit external test/export paths are supported; in-project output is managed."""
    resolved, root = path.resolve(), root.resolve()
    if Path(os.path.abspath(path)).is_relative_to(root) and not resolved.is_relative_to(root):
        raise ValueError("Project result path escapes through a link")
    if not resolved.is_relative_to(root):
        return resolved
    parts = resolved.relative_to(root).parts
    if (len(parts) < 6 or parts[:2] != ("artifacts", "tasks")
            or parts[2] not in layout_policy()["features"] or parts[4] != "results"
            or not (root.joinpath(*parts[:4]) / "task.json").is_file()):
        raise ValueError("Project validation output must be inside a managed task/results directory")
    return resolved


def managed_task_directory(path: Path, *, feature: str, root: Path = PROJECT_ROOT) -> Path:
    """Validate an existing task before staging tools or other local development output."""
    resolved = path.resolve()
    base = root.resolve() / "artifacts" / "tasks" / feature
    if feature not in layout_policy()["features"] or not resolved.is_relative_to(base):
        raise ValueError("Tool output must use a registered feature task")
    metadata = json.loads((resolved / "task.json").read_text(encoding="utf-8"))
    if metadata.get("kind") != "tasks" or metadata.get("feature") != feature:
        raise ValueError("Tool output must use a registered feature task")
    return resolved


def feature_directory(feature: str, template_root: Path = TEMPLATES_DIR) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", feature):
        raise ValueError(f"Invalid template feature: {feature}")
    root = template_root.resolve()
    directory = (root / feature).resolve()
    if not directory.is_relative_to(root):
        raise ValueError("Feature directory escapes template root")
    return directory


def template_relative_path(template_key: str, *, feature: str, template_root: Path = TEMPLATES_DIR) -> Path:
    """Resolve a registered key (or legacy PNG basename) in an explicit feature."""
    directory = feature_directory(feature, template_root)
    entries = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["templates"]
    key = template_key[:-4] if template_key.endswith(".png") else template_key
    if key not in entries:
        raise ValueError(f"Unregistered template: {feature}/{key}")
    candidate = (directory / entries[key]["file"]).resolve()
    if not candidate.is_relative_to(directory) or candidate.suffix.lower() != ".png":
        raise ValueError(f"Invalid registered template path: {feature}/{key}")
    return candidate.relative_to(template_root.resolve())


def ensure_candidate_path(path: Path) -> Path:
    resolved = path.resolve()
    if Path(os.path.abspath(path)).is_relative_to(PROJECT_ROOT) and not resolved.is_relative_to(PROJECT_ROOT):
        raise ValueError("Project candidate path escapes through a link")
    for protected in (TEMPLATES_DIR, CALIBRATION_DIR, PROJECT_ROOT / "config"):
        if resolved.is_relative_to(protected.resolve()):
            raise ValueError(f"Candidate export cannot write formal resources: {resolved}")
    if resolved.is_relative_to(PROJECT_ROOT) and not resolved.is_relative_to(CANDIDATES_DIR.resolve()):
        raise ValueError("Project candidates must be inside artifacts/template-candidates")
    if resolved.is_relative_to(PROJECT_ROOT):
        parts = resolved.relative_to(CANDIDATES_DIR.resolve()).parts
        if not parts or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", parts[0]):
            raise ValueError("Project candidates require a timestamp and unique exporter run ID")
    return resolved


def calibration_output_dirs(
    output_dir: Path | None = None, manifest_dir: Path | None = None
) -> tuple[Path, Path]:
    """Candidate outputs and provenance never overwrite formal resources."""
    if output_dir is None:
        output_dir = CANDIDATES_DIR / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    output_dir = ensure_candidate_path(output_dir)
    if manifest_dir is None:
        manifest_dir = output_dir
    manifest_dir = ensure_candidate_path(manifest_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    print(f"Candidate output: {output_dir}")
    return output_dir, manifest_dir
