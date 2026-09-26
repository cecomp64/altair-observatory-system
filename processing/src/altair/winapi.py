"""The few Win32 calls Altair needs, as no-ops elsewhere (SPEC §3.2, §4.1):
exclusive-open checks (is NINA still writing this file?), keeping the PC
awake while jobs are queued, and whether we run in an interactive session."""
from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
INVALID_HANDLE_VALUE = -1

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def exclusive_open_ok(path: str | Path) -> bool:
    """True when the file can be opened with no sharing, which fails while
    another process (NINA, over SMB too) still has it open. Always True off
    Windows, where the size/mtime stability rule alone applies."""
    if not IS_WINDOWS:
        return True
    import ctypes
    from ctypes import wintypes

    create = ctypes.windll.kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), GENERIC_READ, 0, None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
    if handle in (None, wintypes.HANDLE(INVALID_HANDLE_VALUE).value):
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def keep_awake(on: bool) -> None:
    """SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) while jobs
    are queued or running; ES_CONTINUOUS alone lets the PC sleep again."""
    if not IS_WINDOWS:
        return
    import ctypes

    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


def session_id() -> int | None:
    """The Windows session of this process; 0 means a service session with no
    desktop, where PixInsight can't run (SPEC §4.1)."""
    if not IS_WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes

    sid = wintypes.DWORD()
    if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
        return sid.value
    return None


def long_paths_enabled() -> bool | None:
    if not IS_WINDOWS:
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            return winreg.QueryValueEx(key, "LongPathsEnabled")[0] == 1
    except OSError:
        return False
