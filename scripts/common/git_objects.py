"""Read exact Git blobs through one lazily started process per check."""
from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
import re
import subprocess


class GitBlobReader(AbstractContextManager):
    def __init__(self, root: Path):
        self.root = root
        self._process = None
        self._cache: dict[str, bytes] = {}

    def read(self, oid: str) -> bytes:
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid):
            raise ValueError(f"Invalid Git object ID: {oid!r}")
        if oid in self._cache:
            return self._cache[oid]
        if self._process is None:
            self._process = subprocess.Popen(
                ["git", "cat-file", "--batch"], cwd=self.root,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        process = self._process
        process.stdin.write(oid.encode("ascii") + b"\n")
        process.stdin.flush()
        header = process.stdout.readline().split()
        if len(header) != 3 or header[:2] != [oid.encode("ascii"), b"blob"]:
            raise ValueError(f"Cannot read Git blob {oid}: {header!r}")
        try:
            size = int(header[2])
        except ValueError as exc:
            raise ValueError(f"Invalid Git blob size: {header!r}") from exc
        if size < 0:
            raise ValueError(f"Invalid Git blob size: {header!r}")
        content = process.stdout.read(size)
        if len(content) != size or process.stdout.read(1) != b"\n":
            raise ValueError(f"Truncated Git blob: {oid}")
        self._cache[oid] = content
        return content

    def __exit__(self, exc_type, exc_value, traceback):
        process, self._process = self._process, None
        self._cache.clear()
        if process is not None:
            # Closing input lets cat-file exit; communicate drains diagnostics
            # and reaps it on success, missing objects and caller exceptions.
            try:
                process.stdin.close()
            except BrokenPipeError:
                pass  # Git may have failed before consuming its first request.
            finally:
                process.stdin = None
            _, stderr = process.communicate()
            if process.returncode and exc_type is None:
                raise ValueError(stderr.decode("utf-8", errors="replace").strip())
        return False
