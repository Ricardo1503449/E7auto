from pathlib import Path
import json
import shutil
import subprocess

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
        "docs/user/使用说明.md": '# E7auto v1.3.3 x64 使用说明\nE7auto_v1.3.3_Setup_x64.exe\n',
        "config/internal.yaml": 'example: true\n',
        "config/chaos.yaml": 'feature: chaos\n',
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


def build(project, *, prepare_payload=True):
    run = create_artifact_run("releases", "test", version="1.3.3", root=project)
    workflow.capture(run, project)
    dist = project / "build/nuitka" / run.name / "launcher.dist"
    dist.mkdir(parents=True)
    (dist / "E7auto.exe").write_bytes(b"test executable")
    shutil.copytree(project / "assets", dist / "assets")
    shutil.copytree(project / "config", dist / "config")
    shutil.copyfile(project / "docs/user/使用说明.md", dist / "使用说明.md")
    archive = project / "dist/E7auto_v1.3.3_Setup_x64.exe"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"MZsynthetic installer")
    if prepare_payload:
        workflow.prepare(run, dist, project)
    return run, archive, dist


def validated(project):
    run, archive, dist = build(project)
    workflow.seal(run, archive, dist, project)
    installed = project / "installed/E7auto"
    shutil.copytree(dist, installed)
    workflow.record_validation(run, {"release": str(installed), "installation_verified": True,
        "problems": [], "self_check": {
        "compiled": True, "version": "1.3.3", "wgc_importable": True,
    }}, project)
    from scripts.release.verify_installer import CHECKS
    workflow._write(run / 'installer-verification.json', {
        'artifact_sha256': workflow._file_sha(run / workflow.ARTIFACT),
        'installer_sha256': workflow._file_sha(archive),
        'checks': dict.fromkeys(CHECKS, True), 'problems': []})
    return run, archive, dist


def test_final_document_commit_does_not_invalidate_build_or_crlf_inputs(project):
    run, archive, _ = validated(project)
    record = project / "docs/releases/v1.3.3/VALIDATION.md"
    record.parent.mkdir(parents=True)
    record.write_text("Actual build results\n", encoding="utf-8")
    final = commit(project)
    result = workflow.check(run, final, project)
    assert result["commit"] == final and result["installer"] == str(archive)
    assert (run / "publish-check.json").is_file()


def test_exact_input_check_uses_raw_hashes_without_starting_git(project, monkeypatch):
    original = workflow._inputs(project)
    def forbidden(*args, **kwargs):
        raise AssertionError('Raw byte checking must not launch Git')
    monkeypatch.setattr(workflow, '_git', forbidden)
    raw = workflow._inputs(project, fields=('sha256',))
    assert raw == {name: {'sha256': values['sha256']} for name, values in original.items()}
    workflow._check_inputs({'inputs': original}, project, exact=True)
    (project/'src/e7auto/new.py').write_text('new input', encoding='utf8')
    with pytest.raises(ValueError, match='new.py'):
        workflow._check_inputs({'inputs': original}, project, exact=True)


def test_git_only_input_check_preserves_filters_without_raw_hashes(project, monkeypatch):
    original = workflow._inputs(project)
    def forbidden(*args):
        raise AssertionError('Git-only checking must not compute raw SHA-256')
    monkeypatch.setattr(workflow, '_sha', forbidden)
    assert workflow._inputs(project, fields=('git_blob',)) == {
        name: {'git_blob': values['git_blob']} for name, values in original.items()}


def test_batch_hashes_match_per_file_git_with_unicode_spaces_and_raw_images(project, monkeypatch):
    (project / 'src/e7auto/空 格.py').write_bytes(b'VALUE = 3\r\n')
    (project / 'assets/ui/空 格.ico').write_bytes(b'icon\r\nbytes')
    with (project / '.gitattributes').open('a', encoding='utf-8') as stream:
        stream.write('\n*.ico text eol=lf\n')  # Image policy must bypass even this filter.
    names = workflow._input_paths(project)
    original = workflow._git
    expected = {name: original(project, 'hash-object',
                '--no-filters' if Path(name).suffix.lower() in {'.png', '.ico'} else '--path='+name,
                '--stdin', data=(project/name).read_bytes()).decode().strip() for name in names}
    calls = []
    def tracked(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(workflow, '_git', tracked)
    actual = workflow._inputs(project, fields=('git_blob',))
    assert {name: values['git_blob'] for name, values in actual.items()} == expected
    assert len(calls) == 2 and all('--stdin-paths' in call for call in calls)


@pytest.mark.parametrize('change', ['edit', 'add', 'delete'])
def test_mutation_during_batch_hashing_is_rejected(project, monkeypatch, change):
    original = workflow._git_blobs
    def mutate(root, names):
        result = original(root, names)
        if change == 'edit':
            (root/'src/e7auto/app.py').write_bytes(b'changed')
        elif change == 'add':
            (root/'src/e7auto/new.py').write_bytes(b'added')
        else:
            (root/'src/e7auto/app.py').unlink()
        return result
    monkeypatch.setattr(workflow, '_git_blobs', mutate)
    with pytest.raises(ValueError, match='changed during hashing'):
        workflow._inputs(project)


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
        with (project / "docs/user/使用说明.md").open("a", encoding="utf-8") as stream:
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
        workflow.record_validation(run, {"release": str(project / "installed/E7auto"), "problems": ["compiled self-check failed"], "self_check": None}, project)
    with pytest.raises((ValueError, FileNotFoundError)):
        workflow.check(run, root=project)


@pytest.mark.parametrize("config_name", ["internal.yaml", "chaos.yaml"])
def test_source_mutation_during_build_is_rejected_before_sealing(project, config_name):
    run, archive, dist = build(project)
    (project / "config" / config_name).write_text("example: false\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Build inputs changed"):
        workflow.seal(run, archive, dist, project)
    assert not (run / workflow.ARTIFACT).exists()


def test_old_packaged_guide_is_rejected_before_compiling_installer(project):
    run, archive, dist = build(project, prepare_payload=False)
    (dist / "使用说明.md").write_bytes(b"old guide")
    with pytest.raises(ValueError, match="Packaged input differs"):
        workflow.prepare(run, dist, project)


def test_mutated_payload_during_installer_compilation_is_rejected(project):
    run, setup, dist = build(project)
    (dist / 'E7auto.exe').write_bytes(b'MZchanged')
    with pytest.raises(ValueError, match='payload changed'):
        workflow.seal(run, setup, dist, project)


def test_installed_contents_must_match_payload_and_not_compiler_directory(project):
    run, setup, dist = validated(project)
    report = {'release': str(dist), 'problems': [], 'self_check': {}}
    with pytest.raises(ValueError, match='installed copy'):
        workflow.record_validation(run, report, project)
    installed = project / 'installed/E7auto'
    (installed / 'E7auto.exe').write_bytes(b'tampered')
    report['release'] = str(installed)
    with pytest.raises(ValueError, match='Installed files differ'):
        workflow.record_validation(run, report, project)


@pytest.mark.parametrize('failure', ['missing', 'wrong-artifact', 'failed-check'])
def test_publish_requires_installer_lifecycle_verification(project, failure):
    run, _, _ = validated(project)
    path = run / 'installer-verification.json'
    if failure == 'missing':
        path.unlink()
    else:
        record = json.loads(path.read_text(encoding='utf-8'))
        if failure == 'wrong-artifact':
            record['installer_sha256'] = 'wrong'
        else:
            record['checks']['upgrade_preserves_data'] = False
        workflow._write(path, record)
    with pytest.raises((ValueError, FileNotFoundError)):
        workflow.check(run, root=project)


@pytest.mark.parametrize("name,text", [
    ("src/e7auto/__init__.py", '__version__ = "1.3.2"\n'),
    ("CHANGELOG.md", '## v1.3.2 — 2026-09-16\n'),
    ("docs/user/使用说明.md", '# E7auto v1.3.3 x64 使用说明\nE7auto_v1.3.3_Setup_x64.exe\n待构建源码'),
    ("docs/user/使用说明.md", '# E7auto v1.3.3 x64 使用说明\nE7auto_v1.3.3_Setup_x64.exe\nE7auto_v1.3.2_Setup_x64.exe'),
], ids=["source-version", "changelog-version", "transient-status", "mixed-guide-archives"])
def test_preflight_rejects_unsynchronized_or_transient_documents(project, name, text):
    (project / name).write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        workflow.preflight(project)


def test_records_cannot_be_written_into_unmanaged_directories(project):
    with pytest.raises(ValueError, match="managed release directory"):
        workflow.capture(project / "dist", project)


def test_build_freezes_payload_and_seals_before_installer_publication():
    script = (ROOT / "scripts/release/build-standalone.ps1").read_text(encoding="utf-8")
    assert script.index("workflow preflight") < script.index("workflow capture") < script.index("-m nuitka")
    assert script.index("workflow prepare") < script.index("& $IsccPath /Qp") < script.index("workflow seal") < script.index("Move-Item -LiteralPath $temporaryInstaller")
    assert "Compress-Archive" not in script and '"dist\\launcher.dist"' not in script
    assert script.index("Move-Item -LiteralPath $temporaryInstaller") < script.index("$buildSucceeded = $true") < script.index("'cleanup-build.ps1'")
    assert "if ($buildSucceeded) {\n        foreach ($area in @('nuitka', 'staging')) {" in script
    assert "nuitka_cleanup = $cleanupResults['nuitka'].status" in script
    assert "staging_cleanup = $cleanupResults['staging'].status" in script


def test_final_check_accepts_scoped_reuse_but_requires_new_package_self_check(project):
    from scripts.release import lifecycle

    baseline, _, _ = validated(project)
    old = workflow._read(baseline/lifecycle.REPORT)
    old['environment'] = lifecycle.environment_identity()
    workflow._write(baseline/lifecycle.REPORT, old)
    (project/'assets/templates/test.png').write_bytes(b'updated template bytes')
    current, archive, dist = build(project)
    workflow.seal(current, archive, dist, project)
    for run in (baseline, current):
        workflow._write(run/'build-result.json', {'succeeded': True, 'build_id': run.name,
                                                  'compiler_sha256': '1'*64, 'inno_version': '6.7.3'})
    installed = project/'installed/new-package'
    shutil.copytree(dist, installed)
    workflow.record_validation(current, {'release': str(installed), 'installation_verified': True,
        'problems': [], 'self_check': {'compiled': True, 'version': '1.3.3', 'wgc_importable': True}}, project)
    plan = lifecycle.select_plan(current, project)
    assert plan['mode'] == 'reuse'
    workflow._write(current/lifecycle.REPORT, {
        **plan, 'artifact_sha256': workflow._file_sha(current/workflow.ARTIFACT),
        'installer_sha256': workflow._file_sha(archive),
        'checks': dict.fromkeys(lifecycle.SMOKE_CHECKS, True), 'problems': []})
    revision = commit(project)
    assert workflow.check(current, revision, project)['installer'] == str(archive)
    (current/workflow.VALIDATION).unlink()
    with pytest.raises(OSError):
        workflow.check(current, revision, project)


def test_release_verifier_passes_failed_results_to_the_managed_record(tmp_path, monkeypatch, capsys):
    from scripts.release import verify_release
    import sys
    calls = []
    monkeypatch.setattr(verify_release, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(verify_release, "project_version", lambda _: "1.3.3")
    monkeypatch.setattr(verify_release, "record_validation", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["verify_release", "--run-dir", str(tmp_path / "run"), "--directory", str(tmp_path / "installed")])
    assert verify_release.main() == 1
    report = json.loads(capsys.readouterr().out)
    assert calls == [(tmp_path / "run", report, tmp_path)]
    assert report["problems"] and report["self_check"] is None
