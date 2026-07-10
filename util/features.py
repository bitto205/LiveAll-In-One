"""构建/运行时的功能开关。"""
from __future__ import annotations

import importlib.util

import config as _cfg


def _mitmproxy_available() -> bool:
    try:
        return importlib.util.find_spec("mitmproxy") is not None
    except Exception:
        return False


def route3_enabled() -> bool:
    """线路三是否可用（--rm3 打包会在 config 写入 route3_enabled=false）。"""
    flag = _cfg.get("route3_enabled")
    if flag is not None:
        return bool(flag)
    return _mitmproxy_available()


def picker_routes() -> tuple[str, ...]:
    """主页线路选择卡片上显示的线路。"""
    routes: list[str] = ["1", "2"]
    if route3_enabled():
        routes.append("3")
    routes.append("4")
    return tuple(routes)
