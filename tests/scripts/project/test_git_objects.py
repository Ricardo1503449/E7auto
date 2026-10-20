import subprocess

import pytest

from scripts.common import git_objects


def test_batch_reader_preserves_binary_bytes_caches_and_closes_on_error(tmp_path, monkeypatch):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True, capture_output=True)
    contents = (b'\x89PNG\r\n\x00binary\n', b'', '中文\r\n'.encode('utf8'))
    ids = [subprocess.check_output(['git', 'hash-object', '-w', '--stdin'], cwd=tmp_path, input=data).decode().strip()
           for data in contents]
    started = []
    original = subprocess.Popen
    def counted(*args, **kwargs):
        process = original(*args, **kwargs)
        started.append(process)
        return process
    monkeypatch.setattr(git_objects.subprocess, 'Popen', counted)
    with pytest.raises(ValueError, match='Cannot read Git blob'):
        with git_objects.GitBlobReader(tmp_path) as reader:
            for oid, data in zip(ids, contents):
                assert reader.read(oid) == data
                assert reader.read(oid) is reader.read(oid)
            reader.read('0'*40)
    assert len(started) == 1
    assert started[0].poll() == 0


def test_unused_reader_and_invalid_object_id_do_not_start_git(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No process should be started')
    monkeypatch.setattr(git_objects.subprocess, 'Popen', forbidden)
    with git_objects.GitBlobReader(tmp_path) as reader:
        with pytest.raises(ValueError, match='Invalid Git object ID'):
            reader.read('HEAD:file')


def test_failed_git_process_is_reaped(tmp_path):
    reader = git_objects.GitBlobReader(tmp_path)
    with pytest.raises((ValueError, BrokenPipeError)):
        with reader:
            try:
                reader.read('0'*40)
            finally:
                process = reader._process
    assert reader._process is None
    assert process.poll() is not None
