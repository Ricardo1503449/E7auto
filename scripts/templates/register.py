"""Reviewable template registration with optimistic checks and durable recovery."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

import cv2
import numpy as np
import yaml

from e7auto.template_manifest import load_template_manifest
from scripts.common.paths import PROJECT_ROOT, feature_directory


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str | None:
    if path.exists() and not path.is_file():
        raise ValueError(f"Expected a regular file: {path}")
    return digest(path.read_bytes()) if path.is_file() else None


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _candidate_path(root: Path, path: Path) -> Path:
    path = path.resolve()
    for base in (root / "assets/templates", root / "docs/calibration", root / "config"):
        if path.is_relative_to(base.resolve()):
            raise ValueError("Candidate/plan output cannot be inside formal resource directories")
    if path.is_relative_to(root.resolve()) and not path.is_relative_to((root / "artifacts/template-candidates").resolve()):
        raise ValueError("Project candidate and plan files must be inside artifacts/template-candidates")
    if path.is_relative_to(root.resolve()):
        parts = path.relative_to((root / "artifacts/template-candidates").resolve()).parts
        if len(parts) < 2 or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", parts[0]):
            raise ValueError("Project candidate/plan requires an exporter run directory")
    return path


def _destinations(root: Path, feature: str, key: str, filename: str,
                  candidate_hash: str, source_hash: str, source_suffix: str) -> dict[str, Path]:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
        raise ValueError("Template key must use lowercase letters, digits and underscores")
    if not all(re.fullmatch(r"[0-9a-f]{64}", h) for h in (candidate_hash, source_hash)):
        raise ValueError("Invalid candidate/source hash")
    if source_suffix not in {".json", ".yaml", ".yml"}:
        raise ValueError("Source record must be JSON or YAML")
    directory = feature_directory(feature, root / "assets/templates")
    if not directory.is_relative_to(root):
        raise ValueError("Template directory escapes project root")
    if (directory / "manifest.json").is_symlink():
        raise ValueError("Runtime manifest must not be a symbolic link")
    if Path(filename).is_absolute() or Path(filename).drive:
        raise ValueError("Template filename must be relative")
    template = (directory / filename).resolve()
    if not template.is_relative_to(directory) or template.suffix.lower() != ".png":
        raise ValueError("Template filename escapes feature directory or is not PNG")
    records = (root / "docs/calibration/registrations").resolve()
    if not records.is_relative_to(root):
        raise ValueError("Registration records escape project root")
    return {
        "record": records / f"{feature}-{key}-{candidate_hash[:12]}-{source_hash[:12]}{source_suffix}",
        "template": template,
        "manifest": directory / "manifest.json",
    }


def prepare(root: Path, feature: str, key: str, candidate: Path, source_record: Path,
            filename: str | None = None) -> dict:
    """Read only: validate inputs and pin every input/output involved in apply."""
    root = root.resolve()
    candidate = _candidate_path(root, candidate)
    source_record = source_record.resolve()
    data, source_data = candidate.read_bytes(), source_record.read_bytes()
    metadata_bytes = candidate.with_suffix(".candidate.json").read_bytes()
    metadata = json.loads(metadata_bytes)
    if metadata.get("schema_version") != 1 or metadata.get("file") != candidate.name or metadata.get("sha256") != digest(data):
        raise ValueError("Candidate checksum metadata does not match PNG")
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if (data[:8] != b"\x89PNG\r\n\x1a\n" or image is None or image.dtype != np.uint8 or image.ndim != 3
            or image.shape[2] not in (3, 4) or metadata.get("size") != [image.shape[1], image.shape[0]]):
        raise ValueError("Candidate must be a valid 8-bit color PNG with matching dimensions")
    if image.shape[2] == 4 and not np.any(image[:, :, 3]):
        raise ValueError("Candidate has an empty alpha mask")
    source = yaml.safe_load(source_data)
    if not isinstance(source, dict) or not source:
        raise ValueError("Source record must contain a nonempty metadata object")
    config = yaml.safe_load((root / "config/internal.yaml").read_text(encoding="utf-8"))
    size = config["game"]["baseline_client_size"]
    baseline = [size["width"], size["height"]]
    if any(type(v) is not int or v <= 0 for v in baseline):
        raise ValueError("Invalid project baseline")
    if image.shape[1] > baseline[0] or image.shape[0] > baseline[1]:
        raise ValueError("Candidate exceeds project baseline")
    directory = feature_directory(feature, root / "assets/templates")
    manifest_path = directory / "manifest.json"
    manifest_bytes = manifest_path.read_bytes() if manifest_path.is_file() else None
    if manifest_bytes is None:
        catalog = {"schema_version": 1, "baseline": baseline, "templates": {}}
    else:
        # Existing corruption is never legitimized by refreshing a digest.
        load_template_manifest(manifest_path, tuple(baseline))
        if file_digest(manifest_path) != digest(manifest_bytes):
            raise ValueError("Manifest changed while preparing plan")
        catalog = json.loads(manifest_bytes)
    previous = catalog["templates"].get(key)
    if previous is None and filename is None:
        raise ValueError("New templates require an explicit --filename")
    if previous is not None:
        if filename is not None and filename != previous["file"]:
            raise ValueError("Registration does not rename an existing template")
        filename = previous["file"]
    destinations = _destinations(root, feature, key, filename, digest(data), digest(source_data), source_record.suffix.lower())
    for other_key, entry in catalog["templates"].items():
        if other_key != key and (directory / entry["file"]).resolve() == destinations["template"]:
            raise ValueError("Filename already belongs to another template")
    if previous is None and destinations["template"].exists():
        raise ValueError("New registration cannot overwrite an unregistered file")
    if previous is not None and file_digest(destinations["template"]) != previous["sha256"]:
        raise ValueError("Template changed while preparing plan")
    if destinations["record"].exists() and file_digest(destinations["record"]) != digest(source_data):
        raise ValueError("Source record destination already contains different data")
    updated = dict(previous or {})
    updated.update(file=filename, sha256=digest(data), source={
        "record": destinations["record"].relative_to(root).as_posix(),
        "record_sha256": digest(source_data),
    })
    catalog["templates"][key] = updated
    return {
        "schema_version": 1, "project_root": str(root), "feature": feature, "key": key,
        "filename": filename, "candidate": str(candidate), "source_record": str(source_record),
        "candidate_sha256": digest(data), "source_sha256": digest(source_data),
        "candidate_metadata_sha256": digest(metadata_bytes),
        "baseline": baseline, "catalog": catalog,
        "destinations": {role: str(path.relative_to(root)) for role, path in destinations.items()},
        "expected": {role: file_digest(path) for role, path in destinations.items()},
    }


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".registration-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _work_directory(root: Path) -> Path:
    work = (root / "artifacts/template-registration").resolve()
    if not work.is_relative_to(root):
        raise ValueError("Registration workspace escapes project root")
    return work


@contextmanager
def _lock(root: Path):
    work = _work_directory(root)
    work.mkdir(parents=True, exist_ok=True)
    lock_path = work / "writer.lock"
    if not lock_path.resolve().is_relative_to(work):
        raise ValueError("Registration lock escapes workspace")
    with lock_path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            acquire = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(stream, fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as exc:
            raise ValueError("Another registration/recovery is running") from exc
        try:
            yield work
        finally:
            stream.seek(0)
            release()


def _journal_paths(root: Path, journal: dict) -> dict[str, Path]:
    plan = journal["plan"]
    if Path(plan["project_root"]).resolve() != root:
        raise ValueError("Transaction belongs to another project")
    paths = _destinations(root, plan["feature"], plan["key"], plan["filename"],
                          plan["candidate_sha256"], plan["source_sha256"], Path(plan["source_record"]).suffix.lower())
    if plan["destinations"] != {role: str(path.relative_to(root)) for role, path in paths.items()}:
        raise ValueError("Transaction contains unexpected output paths")
    return paths


def _recover(root: Path, transaction: Path) -> str:
    journal_path = transaction / "journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    if journal["status"] in {"committed", "rolled_back"}:
        return journal["status"]
    paths = _journal_paths(root, journal)
    if set(journal["entries"]) != set(paths):
        raise ValueError("Incomplete recovery journal")
    restore = []
    for role, path in paths.items():
        entry = journal["entries"][role]
        old = entry["old_sha256"]
        if old != journal["plan"]["expected"][role]:
            raise ValueError("Recovery backup does not match original plan")
        previous = (transaction / f"{role}.old").read_bytes() if old is not None else None
        new = (transaction / f"{role}.new").read_bytes()
        if (previous is not None and digest(previous) != old) or digest(new) != entry["new_sha256"]:
            raise ValueError("Recovery backup/staged data checksum mismatch")
        current = file_digest(path)
        if current not in (old, entry["new_sha256"]):
            raise ValueError(f"Recovery conflict; preserving external change: {path}")
        if current != old:
            restore.append((path, previous, entry["new_sha256"]))
    # Preflight all paths before restoring; never clobber a later external edit.
    for path, previous, expected in reversed(restore):
        if file_digest(path) != expected:
            raise ValueError(f"Recovery conflict: {path}")
        if previous is None:
            path.unlink()
        else:
            _atomic_write(path, previous)
    journal["status"] = "rolled_back"
    _atomic_write(journal_path, encoded(journal))
    return "rolled_back"


def apply(root: Path, plan: dict) -> str:
    root = root.resolve()
    with _lock(root) as work:
        for pending in work.glob("transactions/*/journal.json"):
            if json.loads(pending.read_text(encoding="utf-8"))["status"] not in {"committed", "rolled_back"}:
                raise ValueError(f"Recover unfinished transaction first: {pending.parent.name}")
        fresh = prepare(root, plan["feature"], plan["key"], Path(plan["candidate"]),
                        Path(plan["source_record"]), plan["filename"])
        if fresh != plan:
            raise ValueError("Inputs or formal resources changed after preview; prepare a new plan")
        paths = _journal_paths(root, {"plan": plan})
        contents = {"record": Path(plan["source_record"]).read_bytes(),
                    "template": Path(plan["candidate"]).read_bytes(), "manifest": encoded(plan["catalog"])}
        if digest(contents["record"]) != plan["source_sha256"] or digest(contents["template"]) != plan["candidate_sha256"]:
            raise ValueError("Candidate/source changed during apply")
        transaction = work / "transactions" / uuid.uuid4().hex
        transaction.mkdir(parents=True)
        journal = {"schema_version": 1, "status": "applying", "plan": plan, "entries": {}}
        for role, path in paths.items():
            previous = path.read_bytes() if path.is_file() else None
            if (digest(previous) if previous is not None else None) != plan["expected"][role]:
                raise ValueError("Formal resource changed during staging")
            if previous is not None:
                _atomic_write(transaction / f"{role}.old", previous)
            _atomic_write(transaction / f"{role}.new", contents[role])
            journal["entries"][role] = {"old_sha256": plan["expected"][role], "new_sha256": digest(contents[role])}
        journal_path = transaction / "journal.json"
        _atomic_write(journal_path, encoded(journal))
        try:
            # Provenance first, image next, runtime manifest last. Readers fail
            # closed on any interim mismatch; multi-file replacement is not atomic.
            for role, path in paths.items():
                if file_digest(path) != plan["expected"][role]:
                    raise ValueError(f"Formal resource changed before replacement: {path}")
                _atomic_write(path, contents[role])
            load_template_manifest(paths["manifest"], tuple(plan["baseline"]))
            for role, path in paths.items():
                if file_digest(path) != digest(contents[role]):
                    raise ValueError(f"Formal resource changed during final verification: {path}")
            journal["status"] = "committed"
            _atomic_write(journal_path, encoded(journal))
        except Exception as exc:
            try:
                _recover(root, transaction)
            except Exception as recovery_error:
                raise ValueError(f"Registration failed; recover transaction {transaction.name}: {recovery_error}") from exc
            raise ValueError(f"Registration failed and was rolled back ({transaction.name}): {exc}") from exc
        return transaction.name


def recover(root: Path, transaction_id: str) -> str:
    root = root.resolve()
    if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise ValueError("Invalid transaction ID")
    with _lock(root) as work:
        transaction = (work / "transactions" / transaction_id).resolve()
        if not transaction.is_relative_to(work):
            raise ValueError("Recovery transaction escapes registration workspace")
        return _recover(root, transaction)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    modes = parser.add_subparsers(dest="mode", required=True)
    preview = modes.add_parser("prepare", help="Validate and write a reviewable plan; no formal writes")
    preview.add_argument("--feature", required=True)
    preview.add_argument("--key", required=True)
    preview.add_argument("--candidate", type=Path, required=True)
    preview.add_argument("--source-record", type=Path, required=True)
    preview.add_argument("--filename", help="Required for new templates; relative to feature directory")
    preview.add_argument("--plan-out", type=Path, required=True)
    commit = modes.add_parser("apply", help="Apply exactly one previously reviewed plan")
    commit.add_argument("--plan", type=Path, required=True)
    rollback = modes.add_parser("recover", help="Roll back an interrupted/failed transaction")
    rollback.add_argument("--transaction", required=True)
    args = parser.parse_args()
    try:
        root = args.project_root.resolve()
        if args.mode == "prepare":
            plan = prepare(root, args.feature, args.key, args.candidate, args.source_record, args.filename)
            plan_path = _candidate_path(root, args.plan_out)
            if plan_path in {args.candidate.resolve(), args.source_record.resolve(),
                             args.candidate.resolve().with_suffix(".candidate.json")}:
                raise ValueError("Plan output cannot overwrite its inputs")
            _atomic_write(plan_path, encoded(plan))
            print(json.dumps({"plan": str(args.plan_out.resolve()), "destinations": plan["destinations"],
                              "expected": plan["expected"], "candidate_sha256": plan["candidate_sha256"]}, ensure_ascii=False, indent=2))
        elif args.mode == "apply":
            print(json.dumps({"status": "committed", "transaction": apply(root, json.loads(args.plan.read_text(encoding="utf-8")))}))
        else:
            print(json.dumps({"status": recover(root, args.transaction)}))
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
