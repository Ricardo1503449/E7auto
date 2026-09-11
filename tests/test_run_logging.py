from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from e7auto.config import ConfigError, LoggingConfig, load_config
from e7auto.run_logging import RunLogManager


def test_group_retention_removes_whole_run(tmp_path: Path) -> None:
    for name in ('run-old.log', 'run-old.log.1', 'run-old.log.2'):
        path = tmp_path / name
        path.write_text('old', encoding='utf-8')
        os.utime(path, (1_600_000_000, 1_600_000_000))
    with_run = RunLogManager(tmp_path, LoggingConfig(keep_runs=1)).start('new')
    with_run.close()
    assert not list(tmp_path.glob('run-old.log*'))
    assert with_run.path.exists()


def test_total_capacity_prunes_groups(tmp_path: Path) -> None:
    old = tmp_path / 'run-old.log'
    old.write_bytes(b'x' * (1024 * 1024))
    log = RunLogManager(tmp_path, LoggingConfig(max_total_mb=1)).start('new')
    log.close()
    assert not old.exists()
    assert log.path.exists()


def test_active_run_is_protected_across_processes(tmp_path: Path) -> None:
    log = RunLogManager(tmp_path, LoggingConfig()).start('active')
    try:
        code = '''from pathlib import Path
import sys
from e7auto.run_logging import RunLogManager
from e7auto.config import LoggingConfig
log = RunLogManager(Path(sys.argv[1]), LoggingConfig(keep_runs=1)).start('child')
log.close()
'''
        subprocess.run([sys.executable, '-c', code, str(tmp_path)], check=True)
        assert log.path.exists()
        log.event('still_writable')
    finally:
        log.close()
    assert 'still_writable' in log.path.read_text(encoding='utf-8')
    newest = RunLogManager(tmp_path, LoggingConfig(keep_runs=1)).start('last')
    newest.close()
    assert len(list(tmp_path.glob('run-*.log'))) == 1
    assert not list(tmp_path.glob('*.lock'))


def test_rotation_repeats_context_and_caps_segments(tmp_path: Path) -> None:
    log = RunLogManager(tmp_path, LoggingConfig(backup_count=3)).start('rotate')
    log.event('window_prepared', client_width=1536, dpi=120)
    log.event('wgc_initialized', initial_item_size=(1552, 905))
    # Exercise real rotation at a small threshold without writing tens of MB.
    log.handler.maxBytes = 1800
    for index in range(18):
        log.event('recognition', index=index, text='中文' * 250)
    log.event('run_stopped', detail='failure')
    log.close()
    log.close()
    segments = [p for p in tmp_path.iterdir() if '.log' in p.name]
    assert len(segments) == 4
    for segment in segments:
        text = segment.read_text(encoding='utf-8')
        assert 'run_log_started' in text
        assert 'window_prepared' in text
        assert 'wgc_initialized' in text
        assert 'version=' in text
    assert 'run_stopped' in log.path.read_text(encoding='utf-8')


def test_cleanup_failure_is_warning_and_does_not_stop_logging(tmp_path: Path, monkeypatch) -> None:
    old = tmp_path / 'run-old.log'
    old.write_text('old', encoding='utf-8')
    original = Path.unlink

    def fail(path, *args, **kwargs):
        if path == old:
            raise PermissionError('locked by other software')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', fail)
    log = RunLogManager(tmp_path, LoggingConfig(keep_runs=1)).start('new')
    log.event('run_stopped', reason='manual_f5')
    log.close()
    text = log.path.read_text(encoding='utf-8')
    assert 'WARNING event=log_cleanup_warning' in text
    assert 'locked by other software' in text
    assert 'reason=manual_f5' in text
    assert old.exists()


def test_close_prunes_historical_run_created_since_start(tmp_path: Path) -> None:
    log = RunLogManager(tmp_path, LoggingConfig(keep_runs=1)).start('new')
    old = tmp_path / 'run-old.log'
    old.write_text('old', encoding='utf-8')
    log.close()
    assert not old.exists()


@pytest.mark.parametrize('name', asdict(LoggingConfig()))
@pytest.mark.parametrize('value', [0, -1, True, '10'])
def test_retention_parameters_require_positive_integers(tmp_path: Path, name, value) -> None:
    config = yaml.safe_load(Path('config/internal.yaml').read_text(encoding='utf-8'))
    config['logging'][name] = value
    path = tmp_path / 'config.yaml'
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    with pytest.raises(ConfigError, match=f'logging.{name}'):
        load_config(path)


def test_source_logging_uses_unified_defaults() -> None:
    config = yaml.safe_load(Path('config/internal.yaml').read_text(encoding='utf-8'))
    assert config['logging'] == asdict(LoggingConfig())


def test_rotation_also_triggers_group_cleanup(tmp_path: Path) -> None:
    log = RunLogManager(tmp_path, LoggingConfig(keep_runs=1)).start('new')
    old = tmp_path / 'run-old.log'
    old.write_text('old', encoding='utf-8')
    log.handler.maxBytes = 1000
    log.event('large', text='x' * 1200)
    assert not old.exists()
    log.close()


def test_age_uses_newest_segment_of_whole_run(tmp_path: Path) -> None:
    old_segment = tmp_path / 'run-existing.log.1'
    old_segment.write_text('old', encoding='utf-8')
    os.utime(old_segment, (1_600_000_000, 1_600_000_000))
    recent_segment = tmp_path / 'run-existing.log'
    recent_segment.write_text('recent', encoding='utf-8')
    log = RunLogManager(tmp_path, LoggingConfig()).start('new')
    log.close()
    assert old_segment.exists() and recent_segment.exists()
