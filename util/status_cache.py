"""线路 3/4 环境检测缓存（缩短点击进页等待）。"""
from __future__ import annotations

import time

_REG_DIR: tuple[float, str | None] = (0.0, None)
_INDEX_JS: tuple[float, str | None] = (0.0, None)
_PROXY_ENABLED: tuple[float, bool | None] = (0.0, None)
_PROXY_RUNNING: tuple[float, bool] = (0.0, False)
_R3_STATUS: tuple[float, dict | None] = (0.0, None)
_R4_STATUS: tuple[float, dict | None] = (0.0, None)

REG_TTL = 3.0
STATUS_TTL = 1.2
PROXY_TTL = 1.0
PROC_TTL = 0.8


def invalidate_all() -> None:
    global _REG_DIR, _INDEX_JS, _PROXY_ENABLED, _PROXY_RUNNING
    global _R3_STATUS, _R4_STATUS
    _REG_DIR = (0.0, None)
    _INDEX_JS = (0.0, None)
    _PROXY_ENABLED = (0.0, None)
    _PROXY_RUNNING = (0.0, False)
    _R3_STATUS = (0.0, None)
    _R4_STATUS = (0.0, None)


def _fresh(ts: float, ttl: float) -> bool:
    return (time.monotonic() - ts) < ttl


def get_registry_dir(getter, *, force: bool = False) -> str | None:
    global _REG_DIR
    if not force and _fresh(_REG_DIR[0], REG_TTL):
        return _REG_DIR[1]
    path = getter()
    _REG_DIR = (time.monotonic(), path)
    return path


def get_index_js_path(resolver, *, force: bool = False) -> str | None:
    global _INDEX_JS
    if not force and _fresh(_INDEX_JS[0], REG_TTL):
        return _INDEX_JS[1]
    path = resolver()
    _INDEX_JS = (time.monotonic(), path)
    return path


def get_proxy_enabled(getter, *, force: bool = False) -> bool:
    global _PROXY_ENABLED
    if not force and _fresh(_PROXY_ENABLED[0], PROXY_TTL) and _PROXY_ENABLED[1] is not None:
        return _PROXY_ENABLED[1]
    enabled = bool(getter().get("enabled"))
    _PROXY_ENABLED = (time.monotonic(), enabled)
    return enabled


def get_proxy_running(getter, *, force: bool = False) -> bool:
    global _PROXY_RUNNING
    if not force and _fresh(_PROXY_RUNNING[0], PROC_TTL):
        return _PROXY_RUNNING[1]
    running = bool(getter())
    _PROXY_RUNNING = (time.monotonic(), running)
    return running


def get_route3_status(builder, *, force: bool = False) -> dict:
    global _R3_STATUS
    if not force and _fresh(_R3_STATUS[0], STATUS_TTL) and _R3_STATUS[1] is not None:
        return dict(_R3_STATUS[1])
    status = builder()
    _R3_STATUS = (time.monotonic(), status)
    return dict(status)


def get_route4_status(builder, *, force: bool = False) -> dict:
    global _R4_STATUS
    if not force and _fresh(_R4_STATUS[0], STATUS_TTL) and _R4_STATUS[1] is not None:
        return dict(_R4_STATUS[1])
    status = builder()
    _R4_STATUS = (time.monotonic(), status)
    return dict(status)
