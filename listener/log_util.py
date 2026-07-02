"""Listener 日志：应用启动即输出到控制台，连接成功后再写文件。"""
import logging
import os
import re
import sys
from datetime import datetime

_attached: set[str] = set()
_session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
_LOG_FMT = "%(asctime)s | %(levelname)s | %(message)s"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def ensure_console_logging(level: int = logging.INFO) -> None:
    """挂载控制台日志（幂等）。Windows 上 root 默认常为 WARNING，须显式降到 INFO。"""
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(_LOG_FMT)
    has_stream = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )
    if not has_stream:
        sh = logging.StreamHandler(sys.stdout)
        sh.setLevel(level)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    else:
        for h in root.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(level)


def on_connect_success(listener_name: str) -> None:
    """连接成功后挂载文件日志（每 listener 仅一次）。"""
    ensure_console_logging()
    if listener_name in _attached:
        return
    _attached.add(listener_name)
    os.makedirs("log", exist_ok=True)
    root = logging.getLogger()
    fmt = logging.Formatter(_LOG_FMT)
    path = f"log/{listener_name}_{_session_ts}.log"
    if not any(getattr(h, "baseFilename", "") == os.path.abspath(path) for h in root.handlers):
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    logging.getLogger(listener_name).info("✅ 连接成功，开始记录日志")


def make_msg_logger(live_id: str) -> logging.Logger:
    os.makedirs("msg_log", exist_ok=True)
    safe_id = re.sub(r'[\\/*?:"<>|]', "_", live_id)
    name = f"msg_{safe_id}_{_session_ts}"
    ml = logging.getLogger(name)
    ml.setLevel(logging.INFO)
    h = logging.FileHandler(f"msg_log/{safe_id}_{_session_ts}.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    ml.addHandler(h)
    ml.propagate = False
    return ml
