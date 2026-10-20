"""Windows default-handler launch, called on a dedicated background thread."""
import os

import pythoncom


def open_default(target: str) -> None:
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED | pythoncom.COINIT_DISABLE_OLE1DDE)
    try:
        os.startfile(target, "open")
    finally:
        pythoncom.CoUninitialize()
