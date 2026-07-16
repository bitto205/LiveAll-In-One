"""应用日志：单次启动一份按时间命名的 session 日志（boot + listener 共用）。"""
from __future__ import annotations

import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from util.paths import log_dir

# 进程级 session：可用环境变量复用（便于测试）；默认按启动时刻命名
_session_ts = os.environ.get("LIVEAIO_LOG_SESSION") or datetime.now().strftime("%Y%m%d_%H%M%S")
_SESSION_LOG_PATH: Path | None = None
_attached_connect: set[str] = set()

_LOG_FMT = "%(asctime)s | %(levelname)s | %(message)s"

_MSG_LOG_DIR = log_dir() / "msg_log"

_ROUTE_BY_LISTENER = {
    "listener1": "1",
    "listener2": "2",
    "listener3": "3",
    "listener4": "4",
    "login": "登录",
}

_PROXY_NOISE_MARKERS = (
    "server connect ",
    "server disconnect ",
    "client connect",
    "client disconnect",
)


class _ProxyNoiseFilter(logging.Filter):
    """降级 mitmproxy / 代理的连接刷屏日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno > logging.INFO:
            return True
        msg = record.getMessage()
        return not any(marker in msg for marker in _PROXY_NOISE_MARKERS)


class _PrefixAdapter(logging.LoggerAdapter):
    def process(self, msg: Any, kwargs: dict) -> tuple[Any, dict]:
        prefix = self.extra["prefix"]
        text = str(msg)
        if not text.startswith(prefix):
            text = f"{prefix} {text}"
        return text, kwargs


def session_timestamp() -> str:
    return _session_ts


def session_log_path() -> Path:
    """本次运行唯一日志文件：log/YYYYMMDD_HHMMSS.log"""
    global _SESSION_LOG_PATH
    if _SESSION_LOG_PATH is None:
        root = log_dir()
        root.mkdir(parents=True, exist_ok=True)
        _SESSION_LOG_PATH = root / f"{_session_ts}.log"
    return _SESSION_LOG_PATH


def write_boot_line(msg: str) -> None:
    """启动早期文本行（UAC / launcher），写入同一份 session 日志。"""
    try:
        path = session_log_path()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{ts} | INFO | [boot] {msg}\n")
    except Exception:
        pass


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def get_tagged_logger(tag: str, name: str | None = None) -> _PrefixAdapter:
    logger_name = name or tag
    return _PrefixAdapter(get_logger(logger_name), {"prefix": f"[{tag}]"})


def get_listener_logger(route: int | str) -> _PrefixAdapter:
    r = str(route)
    return _PrefixAdapter(get_logger(f"listener{r}"), {"prefix": f"[线路{r}]"})


def _attach_noise_filter(handler: logging.Handler) -> None:
    if any(isinstance(f, _ProxyNoiseFilter) for f in handler.filters):
        return
    handler.addFilter(_ProxyNoiseFilter())


def suppress_proxy_noise() -> None:
    for name in ("mitmproxy", "mitmproxy.proxy", "mitmproxy.proxy.server"):
        logging.getLogger(name).setLevel(logging.WARNING)
    root = logging.getLogger()
    for handler in root.handlers:
        _attach_noise_filter(handler)


def _ensure_session_file_handler() -> Path:
    """把 root logger 接到本次 session 日志文件（幂等）。"""
    path = session_log_path()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    abs_path = str(path.resolve())
    if any(
        getattr(h, "baseFilename", "") == abs_path
        for h in logger.handlers
        if isinstance(h, logging.FileHandler)
    ):
        return path
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_LOG_FMT))
    _attach_noise_filter(fh)
    logger.addHandler(fh)
    return path


def ensure_startup_log() -> Path:
    """启动时挂上本次 session 文件日志（boot / listener / UI 共用）。"""
    path = _ensure_session_file_handler()
    ensure_console_logging()
    return path


def ensure_console_logging(level: int = logging.INFO) -> None:
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
    suppress_proxy_noise()


def on_connect_success(listener_name: str) -> None:
    """连接成功：继续写入同一份 session 日志（不再另开 listener_*.log）。"""
    ensure_console_logging()
    _ensure_session_file_handler()
    if listener_name in _attached_connect:
        return
    _attached_connect.add(listener_name)
    tag = _ROUTE_BY_LISTENER.get(listener_name, listener_name)
    if listener_name.startswith("listener"):
        get_listener_logger(tag).info("✅ 连接成功，开始记录日志 → %s", session_log_path().name)
    else:
        get_tagged_logger(tag, listener_name).info(
            "✅ 连接成功，开始记录日志 → %s", session_log_path().name
        )


def make_msg_logger(live_id: str) -> logging.Logger:
    """debug 弹幕明细仍单独落盘（与 session 运行日志分离）。"""
    root_dir = _MSG_LOG_DIR
    root_dir.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r'[\\/*?:"<>|]', "_", live_id)
    name = f"msg_{safe_id}_{_session_ts}"
    ml = logging.getLogger(name)
    ml.setLevel(logging.INFO)
    h = logging.FileHandler(root_dir / f"{safe_id}_{_session_ts}.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    ml.addHandler(h)
    ml.propagate = False
    return ml
