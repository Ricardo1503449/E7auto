from types import SimpleNamespace

import pytest

from e7auto.platform import installation


def test_source_identity_does_not_create_an_installer_guard(monkeypatch):
    monkeypatch.setattr(installation, 'is_installed_build', lambda: False)
    monkeypatch.setattr(installation.ctypes, 'WinDLL', lambda *a, **k: pytest.fail('source must not create mutex'))
    assert installation.app_user_model_id() == 'E7auto.E7auto'
    with installation.application_guard():
        pass


def test_installed_guard_remains_owned_until_exit_and_closes_after_error(monkeypatch):
    calls = []
    class Function:
        def __init__(self, action): self.action = action
        def __call__(self, *args): return self.action(*args)
    api = SimpleNamespace(CreateMutexW=Function(lambda *a: calls.append(('create', a[2])) or 123),
                          OpenMutexW=Function(lambda *a: 0),
                          CloseHandle=Function(lambda h: calls.append(('close', h)) or 1))
    monkeypatch.setattr(installation.ctypes, 'get_last_error', lambda: 2)
    monkeypatch.setattr(installation, 'is_installed_build', lambda: True)
    monkeypatch.setattr(installation.ctypes, 'WinDLL', lambda *a, **k: api)
    assert installation.app_user_model_id() == 'E7auto.Installed'
    with pytest.raises(ValueError):
        with installation.application_guard():
            assert calls == [('create', installation.RUNNING_MUTEX)]
            raise ValueError('GUI failure')
    assert calls[-1] == ('close', 123)


@pytest.mark.parametrize('wait, clears', [(False, False), (True, False), (True, True)])
def test_update_restart_waits_boundedly_for_setup_without_creating_running_mutex(monkeypatch, wait, clears):
    calls, now = [], [0.0]
    class Function:
        def __init__(self, action): self.action = action
        def __call__(self, *args): return self.action(*args)
    def open_mutex(*args):
        return 7 if not clears or now[0] < 0.2 else 0
    api = SimpleNamespace(CreateMutexW=Function(lambda *a: calls.append('running') or 123),
                          OpenMutexW=Function(open_mutex), CloseHandle=Function(lambda h: 1))
    monkeypatch.setattr(installation, 'is_installed_build', lambda: True)
    monkeypatch.setattr(installation.ctypes, 'WinDLL', lambda *a, **k: api)
    monkeypatch.setattr(installation.ctypes, 'get_last_error', lambda: 2)
    monkeypatch.setattr(installation.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(installation.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    if wait and clears:
        with installation.application_guard(wait_for_setup=True):
            assert calls == ['running'] and now[0] >= 0.2
    else:
        with pytest.raises(RuntimeError, match='安装或卸载'):
            with installation.application_guard(wait_for_setup=wait):
                pytest.fail('must not enter while setup is active')
        assert not calls
        assert now[0] == 0 if not wait else 60 <= now[0] < 61
