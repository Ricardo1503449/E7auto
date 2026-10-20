import ctypes as ct
from types import SimpleNamespace
from xml.etree.ElementTree import fromstring

import pytest

from e7auto.platform import notifications as native
from e7auto.platform.toast_runtime import HRESULT, PTR, OUT, ToastRuntime, guid, method


def test_toast_xml_preserves_unicode_and_escapes_markup(tmp_path):
    title = '异常停止 < & " 🚀'
    message = '原因\n目录 C:/a&b\0\x01\ud800'
    icon = tmp_path / '图标 & 空格.png'
    xml = fromstring(native.toast_xml(title, message, icon))
    assert xml.tag == 'toast' and xml.get('duration') == 'short'
    assert xml.find('.//binding').get('template') == 'ToastGeneric'
    texts = xml.findall('.//text')
    assert texts[0].text == title and texts[1].text == '原因\n目录 C:/a&b'
    assert xml.find('.//image').get('src') == icon.resolve().as_uri()
    assert xml.find('audio').get('src') == 'ms-winsoundevent:Notification.Default'
    assert xml.find('audio').get('silent') is None
    assert xml.find('actions') is None


def test_sender_registers_own_identity_once_and_never_removes_history(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(native, '_register_identity', lambda p: calls.append(('register', p)))
    class Runtime:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            calls.append(('release',))
        def show(self, app_id, xml):
            calls.append(('show', app_id, xml))
    monkeypatch.setattr(native, 'ToastRuntime', Runtime)
    notifier = native.WindowsNotifier(tmp_path / 'icon.png')
    notifier.show('第一次', '完成')
    notifier.show('第二次', '停止')
    assert [call[0] for call in calls] == ['register', 'show', 'release', 'show', 'release']
    assert calls[1][1] == calls[3][1] == native.APP_ID
    assert not hasattr(notifier, 'close') and not hasattr(notifier, 'handle_message')


def test_identity_only_registers_own_per_user_display_fields(tmp_path, monkeypatch):
    calls = []
    class Key:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(native, '_activator', object())
    def create(hive, path, reserved, access):
        calls.append((hive, path, access))
        return Key()
    monkeypatch.setattr(native.winreg, 'CreateKeyEx', create)
    monkeypatch.setattr(native.winreg, 'SetValueEx', lambda key, name, reserved, kind, value: calls.append((name, value)))
    native._register_identity(tmp_path / 'logo.png')
    assert calls[0] == (native.winreg.HKEY_CURRENT_USER, native._IDENTITY_KEY, native.winreg.KEY_SET_VALUE)
    assert dict(calls[1:]) == {'DisplayName': 'E7auto', 'IconUri': str((tmp_path / 'logo.png').resolve()),
                              'CustomActivator': native._ACTIVATOR_ID}


def test_installed_identity_is_read_only_and_uses_the_selected_directory(tmp_path, monkeypatch):
    calls = []
    icon = tmp_path / '安装 目录/logo.png'
    class Key:
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(native, '_activator', object())
    monkeypatch.setattr(native, 'is_installed_build', lambda: True)
    monkeypatch.setattr(native.winreg, 'OpenKey', lambda *a: calls.append(a) or Key())
    monkeypatch.setattr(native.winreg, 'QueryValueEx', lambda *a: (str(icon.resolve()), native.winreg.REG_SZ))
    monkeypatch.setattr(native.winreg, 'CreateKeyEx', lambda *a: pytest.fail('installed app must not create HKCU identity'))
    native._register_identity(icon)
    assert calls[0][0] == native.winreg.HKEY_LOCAL_MACHINE
    assert calls[0][1].endswith('E7auto.Installed')
    with pytest.raises(RuntimeError, match='different installation'):
        native._register_identity(tmp_path / 'different.png')


def test_registration_failure_is_retryable_without_sending(tmp_path, monkeypatch):
    calls = []
    def fail(path):
        calls.append(path)
        raise PermissionError('read only registry')
    monkeypatch.setattr(native, '_register_identity', fail)
    notifier = native.WindowsNotifier(tmp_path / 'icon.png')
    for _ in range(2):
        with pytest.raises(PermissionError):
            notifier.show('title', 'message')
    assert len(calls) == 2 and not notifier._registered


def test_dismiss_callback_handles_com_contract_without_activation():
    # Construct callback storage without registering a class/identity on this PC.
    activator = native._DismissActivator.__new__(native._DismissActivator)
    activator._callbacks, activator._storage, activator._references = [], [], {}
    signature = ct.WINFUNCTYPE(HRESULT, PTR, ct.c_wchar_p, ct.c_wchar_p, PTR, ct.c_ulong)
    activator._callback_ptr = activator._object(native._CALLBACK, [(signature, lambda *args: 0)])
    pointer = activator._callback_ptr
    output = PTR()
    assert method(pointer, 0, HRESULT, PTR, OUT)(pointer, guid(native._CALLBACK), ct.byref(output)) == 0
    assert output.value == pointer
    assert method(pointer, 3, HRESULT, ct.c_wchar_p, ct.c_wchar_p, PTR, ct.c_ulong)(pointer, native.APP_ID, '', None, 0) == 0
    assert activator._create_instance(0, 1, guid(native._CALLBACK), ct.pointer(output)) < 0
    assert activator._create_instance(0, None, guid(native._CALLBACK), ct.pointer(output)) == 0
    assert method(pointer, 0, HRESULT, PTR, OUT)(pointer, guid('11111111-1111-1111-1111-111111111111'), ct.byref(output)) < 0
    assert output.value is None


def test_real_winrt_xml_abi_loads_without_pywinrt_or_notifications():
    # Real COM/HSTRING marshalling, but no registration, posting or history reads.
    with ToastRuntime() as runtime:
        document = runtime.create('Windows.Data.Xml.Dom.XmlDocument')
        loader = runtime.query(document, '6cd0e74e-ee65-4489-9ebf-ca43e87ba637')
        runtime.call(loader, 6, runtime.string('<toast><text>中文🚀 &amp; 内容</text></toast>'))


@pytest.mark.parametrize('initialization', [0, 1, -2147417850])
def test_apartment_balance_and_hstring_release_on_error(monkeypatch, initialization):
    calls = []
    class Function:
        def __init__(self, callback):
            self.callback = callback
        def __call__(self, *args):
            return self.callback(*args)
    def create_string(text, length, output):
        assert length == 3  # CJK + non-BMP UTF-16 surrogate pair.
        ct.cast(output, OUT)[0] = 123
        return 0
    api = SimpleNamespace(**{name: Function(callback) for name, callback in {
        'RoInitialize': lambda mode: initialization,
        'RoUninitialize': lambda: calls.append('uninitialize'),
        'WindowsCreateString': create_string,
        'WindowsDeleteString': lambda handle: calls.append('delete string'),
        'RoActivateInstance': lambda *a: 0,
        'RoGetActivationFactory': lambda *a: 0,
    }.items()})
    monkeypatch.setattr('e7auto.platform.toast_runtime.ct.WinDLL', lambda *a, **k: api)
    with pytest.raises(ValueError):
        with ToastRuntime() as runtime:
            runtime.string('中🚀')
            raise ValueError('test cleanup')
    assert calls == (['delete string'] if initialization < 0 else ['delete string', 'uninitialize'])
