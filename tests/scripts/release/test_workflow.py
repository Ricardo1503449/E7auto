from pathlib import Path
import json
import shutil
import subprocess
import zipfile

import pytest

from scripts.common.paths import create_artifact_run
from scripts.release import workflow
from tests.helpers.paths import ROOT


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.PIPE).decode().strip()


def commit(root):
    git(root, "add", "-A")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def project(tmp_path):
    git(tmp_path, "init", "-q")
    for directory in workflow.INPUT_DIRS:
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    contents = {
        "pyproject.toml": '[project]\nversion = "1.3.3"\n',
        "src/e7auto/__init__.py": '__version__ = "1.3.3"\n',
        "src/e7auto/app.py": 'VALUE = 1\n',
        "README.md": '当前检入源码版本为 `v1.3.3`\n',
        "CHANGELOG.md": '## v1.3.3 — 2026-09-16\n\n### 修复\n\n- Example\n',
        "docs/user/使用说明.txt": 'E7auto v1.3.3 x64 使用说明\nE7auto_v1.3.3_x64.zip\n',
        "config/internal.yaml": 'example: true\n',
        "launcher.py": 'pass\n',
        "requirements.lock": 'example==1.0\n',
        "scripts/release/build.ps1": '# test\n',
        "scripts/common/paths.py": '# test\n',
        ".gitignore": 'artifacts/\ndist/\n',
        ".gitattributes": '*.py text eol=lf\n*.txt text eol=lf\n*.png -text\n',
    }
    for name, text in contents.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8").replace(b"\n", b"\r\n"))
    (tmp_path / "assets/templates/test.png").write_bytes(b"binary\r\nbytes")
    (tmp_path / "assets/ui/test.png").write_bytes(b"UI")
    commit(tmp_path)
    return tmp_path


def build(project):
    run = create_artifact_run("releases", "test", version="1.3.3", root=project)
    workflow.capture(run, project)
    dist = project / "dist/launcher.dist"
    dist.mkdir(parents=True)
    (dist / "E7auto.exe").write_bytes(b"test executable")
    shutil.copytree(project / "assets", dist / "assets")
    shutil.copytree(project / "config", dist / "config")
    shutil.copyfile(project / "docs/user/使用说明.txt", dist / "使用说明.txt")
    archive = project / "dist/E7auto_v1.3.3_x64.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in dist.rglob("*"):
            if path.is_file():
                zipped.write(path, path.relative_to(dist).as_posix())
    return run, archive, dist


def validated(project):
    run, archive, dist = build(project)
    workflow.seal(run, archive, dist, project)
    workflow.record_validation(run, {"problems": [], "self_check": {
        "compiled": True, "version": "1.3.3", "wgc_importable": True,
    }}, project)
    return run, archive, dist


def test_final_document_commit_does_not_invalidate_build_or_crlf_inputs(project):
    run, archive, _ = validated(project)
    record = project / "docs/releases/v1.3.3/VALIDATION.md"
    record.parent.mkdir(parents=True)
    record.write_text("Actual build results\n", encoding="utf-8")
    final = commit(project)
    result = workflow.check(run, final, project)
    assert result["commit"] == final and result["archive"] == str(archive)
    assert (run / "publish-check.json").is_file()


@pytest.mark.parametrize("change", ["code", "new-module", "deleted-module", "binary-newlines", "usage-guide"])
def test_changed_build_inputs_cannot_be_published(project, change):
    run, _, _ = validated(project)
    if change == "code":
        (project / "src/e7auto/app.py").write_text("VALUE = 2\n", encoding="utf-8")
    elif change == "new-module":
        (project / "src/e7auto/new.py").write_text("pass\n", encoding="utf-8")
    elif change == "deleted-module":
        (project / "src/e7auto/app.py").unlink()
    elif change == "binary-newlines":
        (project / "assets/templates/test.png").write_bytes(b"binary\nbytes")
    else:
        with (project / "docs/user/使用说明.txt").open("a", encoding="utf-8") as stream:
            stream.write("changed\n")
    with pytest.raises(ValueError, match="Build inputs changed"):
        workflow.check(run, root=project)


def test_uncommitted_build_is_allowed_but_final_tag_must_include_the_inputs(project):
    (project / "src/e7auto/app.py").write_text("VALUE = 2\n", encoding="utf-8")
    run, _, _ = validated(project)
    with pytest.raises(ValueError, match="Final commit inputs differ"):
        workflow.check(run, root=project)
    final = commit(project)
    assert workflow.check(run, final, project)["commit"] == final


def test_git_text_filters_must_not_change_packaged_image_bytes(project):
    with (project / ".gitattributes").open("a", encoding="utf-8") as stream:
        stream.write("\n*.png text eol=lf\n")
    git(project, "add", "--renormalize", "assets")
    commit(project)
    run, _, _ = validated(project)
    with pytest.raises(ValueError, match="Final commit inputs differ"):
        workflow.check(run, root=project)


@pytest.mark.parametrize("failure", ["archive", "missing-validation", "failed-self-check"])
def test_upload_requires_the_exact_archive_and_successful_compiled_verification(project, failure):
    run, archive, _ = validated(project)
    if failure == "archive":
        archive.write_bytes(archive.read_bytes() + b"tampered")
    elif failure == "missing-validation":
        (run / workflow.VALIDATION).unlink()
    else:
        workflow.record_validation(run, {"problems": ["compiled self-check failed"], "self_check": None}, project)
    with pytest.raises((ValueError, FileNotFoundError)):
        workflow.check(run, root=project)


def test_source_mutation_during_build_is_rejected_before_sealing(project):
    run, archive, dist = build(project)
    (project / "config/internal.yaml").write_text("example: false\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Build inputs changed"):
        workflow.seal(run, archive, dist, project)
    assert not (run / workflow.ARTIFACT).exists()


def test_old_packaged_guide_is_rejected_even_when_zip_matches_dist(project):
    run, archive, dist = build(project)
    (dist / "使用说明.txt").write_bytes(b"old guide")
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in dist.rglob("*"):
            if path.is_file():
                zipped.write(path, path.relative_to(dist).as_posix())
    with pytest.raises(ValueError, match="Packaged input differs"):
        workflow.seal(run, archive, dist, project)


@pytest.mark.parametrize("name,text", [
    ("src/e7auto/__init__.py", '__version__ = "1.3.2"\n'),
    ("CHANGELOG.md", '## v1.3.2 — 2026-09-16\n'),
    ("docs/user/使用说明.txt", 'E7auto v1.3.3 x64 使用说明\nE7auto_v1.3.3_x64.zip\n待构建源码'),
    ("docs/user/使用说明.txt", 'E7auto v1.3.3 x64 使用说明\nE7auto_v1.3.3_x64.zip\nE7auto_v1.3.2_x64.zip'),
], ids=["source-version", "changelog-version", "transient-status", "mixed-guide-archives"])
def test_preflight_rejects_unsynchronized_or_transient_documents(project, name, text):
    (project / name).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        workflow.preflight(project)


def test_records_cannot_be_written_into_unmanaged_directories(project):
    with pytest.raises(ValueError, match="managed release directory"):
        workflow.capture(project / "dist", project)


def test_build_runs_document_check_before_compile_and_seals_before_zip_publication():
    script = (ROOT / "scripts/release/build-standalone.ps1").read_text(encoding="utf-8")
    assert script.index("workflow preflight") < script.index("workflow capture") < script.index("-m nuitka")
    assert script.index("Compress-Archive") < script.index("workflow seal") < script.index("Move-Item -LiteralPath $temporaryReleaseZip")


def test_release_verifier_passes_failed_results_to_the_managed_record(tmp_path, monkeypatch, capsys):
    from scripts.release import verify_release
    import sys
    calls = []
    monkeypatch.setattr(verify_release, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(verify_release, "project_version", lambda _: "1.3.3")
    monkeypatch.setattr(verify_release, "record_validation", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["verify_release", "--run-dir", str(tmp_path / "run")])
    assert verify_release.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert calls == [(tmp_path / "run", report, tmp_path)]
    assert report["problems"] and report["self_check"] is None
