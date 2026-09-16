from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from e7auto.configuration.models import ConfigError, LoggingConfig
from e7auto.configuration.loader import load_config
from e7auto.logging.run import RunLogManager, _field_value, _message


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


@pytest.mark.parametrize('value,expected', [
    ('0.800000', '0.8'), ('12.340', '12.34'), ('0.000', '0'), ('-0.000', '-0'),
    ('-393.380', '-393.38'), ('0.000001', '0.000001'),
    ('12345678901234567890.123456789000', '12345678901234567890.123456789'),
])
def test_measurements_are_compacted_without_float_rounding(value, expected):
    actual = _field_value('phase_shift_y', value)
    assert actual == expected
    assert Decimal(actual) == Decimal(value)
    assert Decimal(actual).is_signed() == Decimal(value).is_signed()


def test_vector_positions_and_missing_values_are_preserved():
    value = 'na,-393.380,-0.000,0.004000,nan,inf,0.000001'
    assert _field_value('pair_shift_y', value) == 'na,-393.38,-0,0.004,nan,inf,0.000001'
    assert _field_value('fallback_block_scores', 'na,0.800000,0.000000,0.750000') == 'na,0.8,0,0.75'


@pytest.mark.parametrize('key', ['version', 'path', 'detail', 'error', 'traceback', 'run_id', 'object'])
def test_arbitrary_text_is_never_interpreted_as_a_measurement(key):
    assert _field_value(key, '12.340000') == '12.340000'


@pytest.mark.parametrize('value', ['001.2000', '1e-10', 'nan', 'inf', 'timeout 1.000', '0.8\n1.000'])
def test_non_decimal_measurements_keep_original_text_and_escaping(value):
    assert _field_value('confidence', value) == value.replace('\n', '\\n')


def test_real_log_keeps_every_event_and_error_detail_while_compacting_numbers(tmp_path: Path):
    log = RunLogManager(tmp_path, LoggingConfig()).start('compact')
    original_error = 'failure at x=12.340000\nstack\tline'
    for _ in range(3):
        log.event('recognition', object='shop', detected=False, confidence='0.800000')
    log.event('scroll_settle_trace', outcome='timeout', pair_shift_y='na,-393.380,0.000',
              fallback_checks=2, fallback_block_scores='na,0.800000,0.100000,0.200000')
    log.event('internal_error', error=original_error, traceback=original_error)
    log.event('run_stopped', reason='scroll_verification_failed', detail=original_error)
    log.close()
    text = log.path.read_text(encoding='utf-8')
    lines = text.splitlines()
    assert len(lines) == 7  # Started + three unchanged observations + trace + error + stopped.
    assert text.count('event=recognition ') == 3
    assert text.count('confidence=0.8') == 3
    assert 'pair_shift_y=na,-393.38,0' in text
    assert 'fallback_checks=2' in text
    assert 'fallback_block_scores=na,0.8,0.1,0.2' in text
    assert text.count('failure at x=12.340000\\nstack\\tline') == 3
    assert 'event=run_stopped' in lines[-1]
    assert 'reason=scroll_verification_failed' in lines[-1]


def test_message_keeps_keys_order_and_non_measurement_values():
    assert _message('input', {'repetition': 1, 'background_message_queued': True, 'logical_x': 1500}) == (
        'event=input background_message_queued=True logical_x=1500 repetition=1'
    )
