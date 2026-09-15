"""Project paths shared by development tools, independent of process cwd."""
from pathlib import Path
from datetime import datetime
import json
import re
import uuid

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / "assets" / "templates"
CALIBRATION_DIR = PROJECT_ROOT / "docs" / "calibration"
CANDIDATES_DIR = PROJECT_ROOT / "artifacts" / "template-candidates"


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
    for protected in (TEMPLATES_DIR, CALIBRATION_DIR, PROJECT_ROOT / "config"):
        if resolved.is_relative_to(protected.resolve()):
            raise ValueError(f"Candidate export cannot write formal resources: {resolved}")
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
