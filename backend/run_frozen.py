"""PyInstaller 冻结入口：直接导入 app 对象运行 uvicorn（不用字符串导入/reload，冻结更稳）。"""
import os
import sys
import threading
import time

# 无控制台窗口（console=False）冻结时 stdout/stderr 会是 None，
# uvicorn 的日志处理器写入 None 会在启动时崩溃。给它们一个可写兜底目标。
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import uvicorn

from app.main import app


def start_parent_watchdog() -> None:
    """Exit this backend if the desktop/launcher process that owns it disappears."""
    raw_pid = os.environ.get("XIADIE_PARENT_PID", "")
    try:
        parent_pid = int(raw_pid)
    except (TypeError, ValueError):
        return
    if parent_pid <= 0:
        return

    def watch() -> None:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL

            synchronize = 0x00100000
            infinite = 0xFFFFFFFF
            handle = kernel32.OpenProcess(synchronize, False, parent_pid)
            if not handle:
                os._exit(0)
            try:
                kernel32.WaitForSingleObject(handle, infinite)
            finally:
                kernel32.CloseHandle(handle)
            os._exit(0)

        while True:
            try:
                os.kill(parent_pid, 0)
            except OSError:
                os._exit(0)
            time.sleep(1)

    threading.Thread(target=watch, name="xiadie-parent-watchdog", daemon=True).start()

if __name__ == "__main__":
    start_parent_watchdog()
    port = int(os.environ.get("XIADIE_PORT", "8756"))
    # 冻结环境不能用 reload；用 app 对象直接跑。
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
