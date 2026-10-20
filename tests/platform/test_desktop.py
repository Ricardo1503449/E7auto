import pytest

from e7auto.platform import desktop


@pytest.mark.parametrize("fails", [False, True])
def test_default_handler_balances_thread_com_initialization(monkeypatch, fails):
    calls = []
    monkeypatch.setattr(desktop.pythoncom, "CoInitializeEx", lambda flags: calls.append(("init", flags)))
    monkeypatch.setattr(desktop.pythoncom, "CoUninitialize", lambda: calls.append("uninit"))
    def start(target, verb):
        calls.append((target, verb))
        if fails:
            raise OSError("no handler")
    monkeypatch.setattr(desktop.os, "startfile", start)
    if fails:
        with pytest.raises(OSError):
            desktop.open_default("https://example.test/project")
    else:
        desktop.open_default("https://example.test/project")
    assert calls == [("init", desktop.pythoncom.COINIT_APARTMENTTHREADED | desktop.pythoncom.COINIT_DISABLE_OLE1DDE),
                     ("https://example.test/project", "open"), "uninit"]
