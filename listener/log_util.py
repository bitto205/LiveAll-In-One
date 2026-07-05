"""应用日志：统一格式，连接成功后可写文件。"""
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any

_attached: set[str] = set()
_session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
_LOG_FMT = "%(asctime)s | %(levelname)s | %(message)s"

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
        _attach_noise_filter(fh)
        root.addHandler(fh)
    tag = _ROUTE_BY_LISTENER.get(listener_name, listener_name)
    if listener_name.startswith("listener"):
        get_listener_logger(tag).info("✅ 连接成功，开始记录日志")
    else:
        get_tagged_logger(tag, listener_name).info("✅ 连接成功，开始记录日志")


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
