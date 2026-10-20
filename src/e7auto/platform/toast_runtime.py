"""Scoped Windows Runtime ABI adapter for Toasts, without PyWinRT imports.

Interface IDs and slots follow Windows SDK metadata; HSTRING and COM ownership
stays in one apartment and is independent of WGC resources.
"""
from __future__ import annotations

import ctypes as ct
from contextlib import ExitStack
from uuid import UUID

PTR = ct.c_void_p
OUT = ct.POINTER(PTR)
HRESULT = ct.c_long


def check_hresult(value: int) -> None:
    if value < 0:
        raise OSError(f"Windows Toast HRESULT 0x{value & 0xFFFFFFFF:08X}")


def guid(value: str):
    return (ct.c_ubyte * 16).from_buffer_copy(UUID(value).bytes_le)


def method(pointer, slot: int, result_type, *argument_types):
    table = ct.cast(pointer, ct.POINTER(ct.POINTER(PTR))).contents
    return ct.WINFUNCTYPE(result_type, PTR, *argument_types)(table[slot])


class ToastRuntime:
    def __init__(self) -> None:
        self._resources = ExitStack()
        self._api = ct.WinDLL("combase", use_last_error=True)
        signatures = {
            "RoInitialize": ([ct.c_uint], HRESULT),
            "RoUninitialize": ([], None),
            "WindowsCreateString": ([ct.c_wchar_p, ct.c_uint, OUT], HRESULT),
            "WindowsDeleteString": ([PTR], HRESULT),
            "RoActivateInstance": ([PTR, OUT], HRESULT),
            "RoGetActivationFactory": ([PTR, PTR, OUT], HRESULT),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self._api, name)
            function.argtypes, function.restype = args, result

    def __enter__(self):
        result = self._api.RoInitialize(0)
        if result & 0xFFFFFFFF != 0x80010106:  # RPC_E_CHANGED_MODE: use existing apartment.
            check_hresult(result)
            self._resources.callback(self._api.RoUninitialize)
        return self

    def __exit__(self, *args):
        self._resources.close()

    def string(self, value: str):
        handle = PTR()
        check_hresult(self._api.WindowsCreateString(value, len(value.encode("utf-16-le")) // 2, ct.byref(handle)))
        self._resources.callback(self._api.WindowsDeleteString, handle)
        return handle

    def _own(self, pointer):
        if not pointer.value:
            raise OSError("Windows Toast returned an empty interface")
        self._resources.callback(method(pointer, 2, ct.c_ulong), pointer)  # IUnknown.Release
        return pointer

    def create(self, name: str, iid: str | None = None):
        pointer = PTR()
        name_handle = self.string(name)
        if iid is None:
            result = self._api.RoActivateInstance(name_handle, ct.byref(pointer))
        else:
            result = self._api.RoGetActivationFactory(name_handle, guid(iid), ct.byref(pointer))
        check_hresult(result)
        return self._own(pointer)

    def query(self, pointer, iid: str):
        result = PTR()
        check_hresult(method(pointer, 0, HRESULT, PTR, OUT)(pointer, guid(iid), ct.byref(result)))
        return self._own(result)

    def call(self, pointer, slot: int, *args) -> None:
        check_hresult(method(pointer, slot, HRESULT, *([PTR] * len(args)))(pointer, *args))

    def returned_interface(self, pointer, slot: int, *args):
        result = PTR()
        check_hresult(method(pointer, slot, HRESULT, *([PTR] * len(args)), OUT)(pointer, *args, ct.byref(result)))
        return self._own(result)

    def show(self, app_id: str, xml: str) -> None:
        document = self.create("Windows.Data.Xml.Dom.XmlDocument")
        loader = self.query(document, "6cd0e74e-ee65-4489-9ebf-ca43e87ba637")
        self.call(loader, 6, self.string(xml))  # IXmlDocumentIO.LoadXml
        document = self.query(document, "f7f3a506-1e87-42d6-bcfb-b8c809fa5494")
        factory = self.create("Windows.UI.Notifications.ToastNotification", "04124b20-82c6-4229-b109-fd9ed4662b53")
        toast = self.returned_interface(factory, 6, document)
        manager = self.create("Windows.UI.Notifications.ToastNotificationManager", "50ac103f-d235-4598-bbef-98fe4d1a3ad4")
        notifier = self.returned_interface(manager, 7, self.string(app_id))
        self.call(notifier, 6, toast)  # IToastNotifier.Show; never Hide or clear history.
