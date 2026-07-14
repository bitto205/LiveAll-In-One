"""
main.py — 轻量启动器：先提权，再加载 app_main（避免提权前加载 Qt/Playwright）。
"""

import os
import sys


def _trace_boot(msg: str) -> None:
    try:
        # 与 listener / app 共用 log/YYYYMMDD_HHMMSS.log
        from util.log_util import write_boot_line
        write_boot_line(msg)
    except Exception:
        # 极早期 import 失败时的兜底（仍按时间轴命名，避免再写 boot.log）
        try:
            from datetime import datetime
            root = os.path.dirname(os.path.abspath(sys.argv[0]))
            log_folder = os.path.join(root, "log")
            os.makedirs(log_folder, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(log_folder, f"{ts}_boot_fallback.log")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now().isoformat()} | [boot] {msg}\n")
        except Exception:
            pass


def _resolve_uac_launch() -> tuple[str, str | None]:
    """ShellExecuteW 的 lpFile / lpParameters；必须在 os.chdir 之前调用。"""
    from util.paths import is_compiled

    script = os.path.abspath(sys.argv[0])
    extra = [f'"{a}"' for a in sys.argv[1:]]

    if is_compiled():
        # 打包版：直接以 exe 提权，参数仅 argv[1:]
        params = " ".join(extra) if extra else None
        return script, params

    # 开发版：用 Python 解释器提权，脚本路径作为参数
    py = os.path.abspath(sys.executable)
    params = " ".join([f'"{script}"', *extra])
    return py, params


def _ensure_admin(launcher_exe: str, launcher_params: str | None) -> None:
    from util.paths import app_root
    import ctypes

    root = str(app_root())
    if ctypes.windll.shell32.IsUserAnAdmin():
        _trace_boot("admin ok")
        return
    _trace_boot("requesting UAC elevation")
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", launcher_exe, launcher_params, root, 1,
    )
    if rc <= 32:
        _trace_boot(f"UAC launch failed exe={launcher_exe} rc={rc}")
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
    # 尽早把仓库根放进 path，便于 boot 写入统一 session 日志
    _script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)

    _trace_boot("launcher entry")
    from util.paths import app_root

    launcher_exe, launcher_params = _resolve_uac_launch()
    root = app_root()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    _ensure_admin(launcher_exe, launcher_params)

    _trace_boot("loading app_main")
    try:
        from app_main import run_app
        sys.exit(run_app())
    except Exception:
        _trace_boot("app_main failed")
        raise
