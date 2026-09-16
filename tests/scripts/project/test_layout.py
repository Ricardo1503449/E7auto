from pathlib import Path
import hashlib
import json
import shutil
import subprocess

import pytest

from scripts.common.paths import LAYOUT_POLICY, create_artifact_run, ensure_task_result_path, calibration_record_path
from scripts.project.check_layout import check_layout


@pytest.fixture
def project(tmp_path):
    policy = tmp_path / "scripts/project/layout.json"
    policy.parent.mkdir(parents=True)
    shutil.copyfile(LAYOUT_POLICY, policy)
    return tmp_path


def put(root, name, text="test"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_source_dependencies_are_checked_from_index_not_worktree(project):
    bad = 'from e7auto.features.penguin.flow import PenguinFlow\n'
    good = 'from e7auto.runtime.context import RuntimeContext\n'
    name = 'src/e7auto/features/shop/flow.py'
    put(project, name, bad)
    subprocess.run(['git', 'init', '-q'], cwd=project, check=True, capture_output=True)
    subprocess.run(['git', 'add', '.'], cwd=project, check=True, capture_output=True)
    put(project, name, good)
    assert check_layout(project) == []
    assert any('features cannot import each other' in error for error in check_layout(project, staged=True))
    subprocess.run(['git', 'add', name], cwd=project, check=True, capture_output=True)
    put(project, name, bad)
    assert any('features cannot import each other' in error for error in check_layout(project))
    assert check_layout(project, staged=True) == []


def test_runs_are_unique_and_records_own_outputs(project):
    first = create_artifact_run("tasks", "fix-entry", feature="shop", root=project)
    second = create_artifact_run("tasks", "fix-entry", feature="shop", root=project)
    assert first != second
    assert set(p.name for p in first.iterdir()) == {"task.json", "inputs", "previews", "results", "scratch"}
    (first / "results/result.json").write_text("{}")
    assert json.loads((first / "task.json").read_text())["feature"] == "shop"
    assert check_layout(project) == []


@pytest.mark.parametrize("kind,subject,feature,version", [
    ("misc", "test", None, None), ("tasks", "../escape", "shop", None),
    ("tasks", "test", "unknown", None), ("releases", "test", None, "../v1"),
    ("maintenance", "test", "shop", None), ("git", "test", None, "1.2.3"),
])
def test_invalid_ownership_never_creates_output(project, kind, subject, feature, version):
    with pytest.raises(ValueError):
        create_artifact_run(kind, subject, feature=feature, version=version, root=project)
    assert not (project / "artifacts").exists()


@pytest.mark.parametrize("path", [
    "artifacts/new-bug/result.json", "docs/another-fix.md", "tests/test_new.py",
    "tests/vision/debug.png", "tests/fixtures/unknown/source.png", "build/probe.py",
])
def test_misplaced_files_are_reported_even_when_ignored(project, path):
    put(project, ".gitignore", "artifacts/\nbuild/\n")
    put(project, path)
    assert any(path in error for error in check_layout(project))


def test_run_metadata_and_sections_are_checked(project):
    run = create_artifact_run("tasks", "fix-entry", feature="shop", root=project)
    (run / "loose.json").write_text("{}")
    (run / "previews/experiment.py").write_text("pass")
    record = json.loads((run / "task.json").read_text())
    record["feature"] = "penguin"
    (run / "task.json").write_text(json.dumps(record))
    errors = check_layout(project)
    assert any("loose.json" in e for e in errors)
    assert any("experiment.py" in e for e in errors)
    assert any("ownership" in e for e in errors)


def test_task_result_guard_allows_managed_or_explicit_external_export(project, tmp_path_factory):
    run = create_artifact_run("tasks", "validate-entry", feature="shop", root=project)
    result = run / "results/validation.json"
    assert ensure_task_result_path(result, project) == result
    with pytest.raises(ValueError):
        ensure_task_result_path(project / "logs/validation.json", project)
    outside = tmp_path_factory.mktemp("external") / "result.json"
    assert ensure_task_result_path(outside, project) == outside


def test_archive_is_frozen_and_unlisted_files_are_rejected(project):
    archive = project / "artifacts/archive/snapshots/old"
    source = put(archive, "source.py", "pass\n")
    put(archive, "_archive.json", json.dumps({"files": {"source.py": hashlib.sha256(source.read_bytes()).hexdigest()}}))
    assert check_layout(project) == []
    put(archive, "new-result.json", "{}")
    assert any("frozen archive" in e for e in check_layout(project))


def test_staged_mode_reads_the_index_not_the_working_copy(project):
    def git(*args):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
    git("init", "-q")
    put(project, "docs/README.md", "[Development](development/guide.md)\n")
    guide = put(project, "docs/development/guide.md", "Guide\n")
    git("add", "scripts", "docs")
    guide.unlink()
    assert any("Broken local link" in e for e in check_layout(project))
    assert check_layout(project, staged=True) == []
    invalid = put(project, "docs/loose.md")
    git("add", "docs/loose.md")
    invalid.unlink()
    assert any("docs/loose.md" in e for e in check_layout(project, staged=True))


def test_release_build_outputs_require_matching_run(project):
    run = create_artifact_run("releases", "standalone", version="1.3.2", root=project)
    put(project, f"build/nuitka/{run.name}/launcher.build/object.o")
    assert check_layout(project) == []
    (run / "task.json").unlink()
    assert any("no release task" in e for e in check_layout(project))


def test_calibration_record_names_are_explicit(project):
    assert calibration_record_path("manifest.yaml", project) == project / "shop/manifest.yaml"
    with pytest.raises(ValueError):
        calibration_record_path("../../other.yaml", project)


def test_empty_unknown_top_level_directory_is_rejected(project):
    (project / "artifacts/another-fix").mkdir(parents=True)
    assert any("Unexpected top-level directory" in e for e in check_layout(project))


def test_new_tool_cannot_reintroduce_ad_hoc_default(project):
    put(project, "scripts/validation/new_tool.py", 'output = PROJECT_ROOT / "artifacts" / "new-bug"\n')
    assert any("shared artifact/build paths" in e for e in check_layout(project))


def test_new_tool_cannot_write_validation_to_runtime_logs(project):
    put(project, "scripts/validation/new_tool.py", 'output = ROOT / "logs" / "validation.json"\n')
    assert any("must not default to runtime logs" in e for e in check_layout(project))


def test_candidate_export_uses_registered_namespace(project, monkeypatch):
    from scripts.common import paths
    monkeypatch.setattr(paths, "PROJECT_ROOT", project)
    monkeypatch.setattr(paths, "TEMPLATES_DIR", project / "assets/templates")
    monkeypatch.setattr(paths, "CALIBRATION_DIR", project / "docs/calibration")
    monkeypatch.setattr(paths, "CANDIDATES_DIR", project / "artifacts/template-candidates")
    candidate, source = paths.calibration_output_dirs()
    assert candidate == source
    (candidate / "source.json").write_text("{}")
    assert check_layout(project) == []
    with pytest.raises(ValueError, match="template-candidates"):
        paths.calibration_output_dirs(project / "artifacts/new-candidate")


def test_validation_cli_default_creates_owned_result_without_game_actions(project, monkeypatch):
    import sys
    from scripts.common import paths
    from scripts.validation import validate_background_mode as validator

    monkeypatch.setattr(paths, "PROJECT_ROOT", project)
    allocated = []

    def result_path(subject, feature, filename):
        run = create_artifact_run("tasks", subject, feature=feature, root=project)
        allocated.append(run)
        return run / "results" / filename

    monkeypatch.setattr(validator, "task_result_path", result_path)
    monkeypatch.setattr(validator, "_run_capture", lambda args: ({"status": "ok", "simulated": True}, 0))
    monkeypatch.setattr(sys, "argv", ["validator", "capture", "--acknowledge-shop-top-covered"])
    assert validator.main() == 0
    assert len(allocated) == 1
    assert json.loads((allocated[0] / "task.json").read_text())["status"] == "complete"
    assert json.loads(next((allocated[0] / "results").glob("*.json")).read_text())["simulated"] is True
    assert check_layout(project) == []


def test_build_script_uses_one_release_id_for_outputs():
    from tests.helpers.paths import ROOT
    script = (ROOT / "scripts/release/build-standalone.ps1").read_text(encoding="utf-8")
    assert "scripts.project.artifacts --kind releases" in script
    assert '"build\\nuitka\\$buildId"' in script
    assert '"build\\staging\\$buildId"' in script
    assert '"--output-dir=$buildDir"' in script
    assert '"docs\\user"' in script
    assert 'Tee-Object -FilePath (Join-Path $runDirectory "build.log")' in script
