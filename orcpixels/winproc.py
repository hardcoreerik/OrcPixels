"""Start other programs from the frozen Windows build without leaking our DLL folder.

PyInstaller's bootloader calls SetDllDirectoryW(<install>\\_internal) so OrcPixels finds its
bundled DLLs. Windows hands that setting down to every child process: a browser started
from OrcPixels (webbrowser.open), and everything *it* starts later (helpers, extension
native hosts), then loads VCRUNTIME140.dll etc. from our install folder. Those processes
hold our files open for hours, and an update's installer can't replace them (OTA failure
on 2026-10-15: chrome-native-host.exe had _internal\\VCRUNTIME140.dll loaded, Setup
couldn't close it and rolled back). PyInstaller documents the fix: SetDllDirectoryW(NULL)
before starting a child, which restores the normal search path for it.

clean_dll_search() does that around one launch and then puts our folder back, so our own
lazily loaded extension modules keep working. No-op outside a frozen Windows build.
"""
from __future__ import annotations

import contextlib
import os
import sys
import threading
import webbrowser

_lock = threading.Lock()


def _kernel32():
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        return k
    except (ImportError, AttributeError, OSError):
        return None


@contextlib.contextmanager
def clean_dll_search(kernel32=None, home: str | None = None):
    """Children started inside this block get the standard DLL search path."""
    k = kernel32 if kernel32 is not None else _kernel32()
    if k is None:
        yield
        return
    home = home or getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    with _lock:
        k.SetDllDirectoryW(None)
        try:
            yield
        finally:
            k.SetDllDirectoryW(home)


def open_url(url: str) -> bool:
    """webbrowser.open without handing our DLL folder to the browser."""
    with clean_dll_search():
        return webbrowser.open(url)
