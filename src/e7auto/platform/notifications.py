"""Desktop Toast identity and delivery; Windows owns notification history."""
from __future__ import annotations

import atexit
import ctypes as ct
from pathlib import Path
import winreg
from xml.etree.ElementTree import Element, SubElement, tostring

from .toast_runtime import HRESULT, OUT, PTR, ToastRuntime, check_hresult, guid
from .installation import INSTALLED_APP_ID, app_user_model_id, is_installed_build


APP_ID = "E7auto.E7auto"
_ACTIVATOR_ID = "{5914C5E5-CC54-4A2B-A664-DDF892B3D861}"
_IDENTITY_KEY = rf"Software\Classes\AppUserModelId\{APP_ID}"
_UNKNOWN = "00000000-0000-0000-c000-000000000046"
_CLASS_FACTORY = "00000001-0000-0000-c000-000000000046"
_CALLBACK = "53e31837-6600-4a81-9395-75cffe746f94"
_activator = None


class _DismissActivator:
    """No-op callback: selecting a message never launches/focuses the app.

Callback storage lives until process shutdown. No LocalServer32/autostart entry
is installed; old messages cannot launch the application after it has exited.
"""
    def __init__(self) -> None:
        self._cookie = ct.c_ulong()
        self._runtime = ToastRuntime()
        self._runtime.__enter__()
        self._callbacks = []
        self._storage = []
        self._references = {}
        self._callback_ptr = self._object(_CALLBACK, [
            (ct.WINFUNCTYPE(HRESULT, PTR, ct.c_wchar_p, ct.c_wchar_p, PTR, ct.c_ulong), lambda *args: 0),
        ])
        self._factory_ptr = self._object(_CLASS_FACTORY, [
            (ct.WINFUNCTYPE(HRESULT, PTR, PTR, PTR, OUT), self._create_instance),
            (ct.WINFUNCTYPE(HRESULT, PTR, ct.c_int), lambda *args: 0),
        ])
        self._ole = ct.WinDLL("ole32")
        self._ole.CoRegisterClassObject.argtypes = [PTR, PTR, ct.c_ulong, ct.c_ulong, ct.POINTER(ct.c_ulong)]
        self._ole.CoRegisterClassObject.restype = HRESULT
        self._ole.CoRevokeClassObject.argtypes = [ct.c_ulong]
        self._ole.CoRevokeClassObject.restype = HRESULT
        try:
            check_hresult(self._ole.CoRegisterClassObject(guid(_ACTIVATOR_ID), self._factory_ptr, 4, 1, ct.byref(self._cookie)))
        except Exception:
            self._runtime.__exit__(None, None, None)
            raise

    def _object(self, iid: str, extra_methods: list) -> int:
        def query(this, requested, result):
            result[0] = None
            if ct.string_at(requested, 16) not in (bytes(guid(iid)), bytes(guid(_UNKNOWN))):
                return -2147467262  # E_NOINTERFACE
            result[0] = this
            add_ref(this)
            return 0

        def add_ref(this):
            self._references[this] = self._references.get(this, 1) + 1
            return self._references[this]

        def release(this):
            self._references[this] = max(1, self._references.get(this, 1) - 1)
            return self._references[this]

        callbacks = [
            ct.WINFUNCTYPE(HRESULT, PTR, PTR, OUT)(query),
            ct.WINFUNCTYPE(ct.c_ulong, PTR)(add_ref),
            ct.WINFUNCTYPE(ct.c_ulong, PTR)(release),
            *(signature(function) for signature, function in extra_methods),
        ]
        table = (PTR * len(callbacks))(*(ct.cast(callback, PTR).value for callback in callbacks))
        instance = (PTR * 1)(ct.addressof(table))
        self._callbacks.extend(callbacks)
        self._storage.extend((table, instance))
        return ct.addressof(instance)

    def _create_instance(self, this, outer, requested, result):
        result[0] = None
        if outer:
            return -2147221232  # CLASS_E_NOAGGREGATION
        if ct.string_at(requested, 16) not in (bytes(guid(_CALLBACK)), bytes(guid(_UNKNOWN))):
            return -2147467262
        result[0] = self._callback_ptr
        self._references[self._callback_ptr] = self._references.get(self._callback_ptr, 1) + 1
        return 0

    def close(self) -> None:
        if self._cookie.value:
            self._ole.CoRevokeClassObject(self._cookie)
            self._cookie.value = 0
            self._runtime.__exit__(None, None, None)


def _register_identity(icon_path: Path) -> None:
    global _activator
    if _activator is None:
        _activator = _DismissActivator()
        atexit.register(_activator.close)
    if is_installed_build():
        # The installer owns the machine identity. Never create per-user keys
        # that a different administrator would be unable to remove at uninstall.
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            rf"Software\Classes\AppUserModelId\{INSTALLED_APP_ID}", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            icon, _ = winreg.QueryValueEx(key, "IconUri")
            if Path(icon).resolve() != icon_path.resolve():
                raise RuntimeError("Installed notification identity points to a different installation")
        return
    # Own identity only; never modify notification, sound or startup preferences.
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _IDENTITY_KEY, 0, winreg.KEY_SET_VALUE) as key:
        for name, value in (("DisplayName", "E7auto"), ("IconUri", str(icon_path.resolve())),
                            ("CustomActivator", _ACTIVATOR_ID)):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def toast_xml(title: str, message: str, icon_path: Path) -> str:
    def clean(text):
        return "".join(char for char in text if char in "\t\n\r" or
                       0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD or
                       0x10000 <= ord(char) <= 0x10FFFF)
    toast = Element("toast", {"duration": "short"})
    binding = SubElement(SubElement(toast, "visual"), "binding", {"template": "ToastGeneric"})
    SubElement(binding, "text").text = clean(title)
    SubElement(binding, "text").text = clean(message)
    SubElement(binding, "image", {"placement": "appLogoOverride", "src": icon_path.resolve().as_uri()})
    SubElement(toast, "audio", {"src": "ms-winsoundevent:Notification.Default"})
    return tostring(toast, encoding="unicode")


class WindowsNotifier:
    def __init__(self, icon_path: Path) -> None:
        self._icon_path = icon_path
        self._registered = False

    def show(self, title: str, message: str) -> None:
        if not self._registered:
            _register_identity(self._icon_path)
            self._registered = True
        with ToastRuntime() as runtime:
            runtime.show(app_user_model_id(), toast_xml(title, message, self._icon_path))
