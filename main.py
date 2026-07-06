"""
main.py — 轻量启动器：先提权，再加载 app_main（避免提权前加载 Qt/Playwright）。
"""

import os
import sys


def _trace_boot(msg: str) -> None:
    try:
        root = os.path.dirname(os.path.abspath(sys.argv[0]))
        log = os.path.join(root, "log", "boot.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            from datetime import datetime
            f.write(f"{datetime.now().isoformat()} | {msg}\n")
    except Exception:
        pass


def _launcher_exe() -> str:
    return os.path.abspath(sys.argv[0])


def _ensure_admin() -> None:
    from util.paths import app_root
    import ctypes

    root = str(app_root())
    if ctypes.windll.shell32.IsUserAnAdmin():
        _trace_boot("admin ok")
        return
    _trace_boot("requesting UAC elevation")
    exe = _launcher_exe()
    params = " ".join(f'"{a}"' for a in sys.argv[1:]) if len(sys.argv) > 1 else None
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, root, 1)
    if rc <= 32:
        _trace_boot(f"UAC launch failed exe={exe} rc={rc}")
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"无法以管理员身份启动 LiveAIO（错误码 {rc}）。\n"
                f"请右键 exe →「以管理员身份运行」。",
                "LiveAIO",
                0x10,
            )
        except Exception:
            pass
    else:
        _trace_boot("UAC child launched, parent exit")
    sys.exit(0)


if __name__ == "__main__":
    _trace_boot("launcher entry")
    from util.paths import app_root

    root = app_root()
    os.chdir(root)
    sys.path.insert(0, str(root))
    _ensure_admin()

    _trace_boot("loading app_main")
    try:
        from app_main import run_app
        sys.exit(run_app())
    except Exception:
        _trace_boot("app_main failed")
        raise
