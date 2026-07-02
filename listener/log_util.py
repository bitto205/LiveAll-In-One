"""Listener 日志：首次连接成功后再写入文件，避免 import 时 basicConfig。"""
import logging
import os
import re
from datetime import datetime

_attached: set[str] = set()
_session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def on_connect_success(listener_name: str) -> None:
    """连接成功后挂载文件日志（每 listener 仅一次）。"""
    if listener_name in _attached:
        return
    _attached.add(listener_name)
    os.makedirs("log", exist_ok=True)
    root = logging.getLogger()
    if root.level == logging.NOTSET:
        root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    path = f"log/{listener_name}_{_session_ts}.log"
    if not any(getattr(h, "baseFilename", "") == os.path.abspath(path) for h in root.handlers):
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)
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
