"""工具模块公共：单例守卫、礼物图标/名称缓存、应用退出。"""
from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from util.log_util import get_tagged_logger
from util.paths import gift_dir

logger = get_tagged_logger("工具", "tools.common")

_GIFT_ICON_DIR = str(gift_dir() / "icon")
_GIFT_NAMES_CACHE: list[str] | None = None
_GIFT_SRC_CACHE: dict[str, QPixmap] = {}
_APP_SHUTTING_DOWN = False
_TOOLS_PAGE = None


class ToolSingleton:
    """工具窗单例 mixin：配合 QMainWindow，避免重复 __new__/__init__ 样板。"""

    _instance = None

    def __new__(cls, parent=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @staticmethod
    def guard_init(obj) -> bool:
        if getattr(obj, "_initialized", False):
            return False
        obj._initialized = True
        return True


def bind_tools_page(page) -> None:
    global _TOOLS_PAGE
    _TOOLS_PAGE = page


def unregister_tool_from_page(tool_name: str) -> None:
    page = _TOOLS_PAGE
    if page is not None and hasattr(page, "unregister_tool"):
        page.unregister_tool(tool_name)


def release_tool_singleton(
    tool_cls: type,
    *,
    cleanup: Callable | None = None,
) -> None:
    inst = getattr(tool_cls, "_instance", None)
    if inst is None:
        return
    if cleanup is not None:
        cleanup(inst)
    tool_cls._instance = None
    if hasattr(inst, "_initialized"):
        inst._initialized = False


def mark_app_shutting_down() -> None:
    global _APP_SHUTTING_DOWN
    _APP_SHUTTING_DOWN = True


def is_app_shutting_down() -> bool:
    return _APP_SHUTTING_DOWN


def shutdown_all_tools() -> None:
    """退出主程序时关闭所有工具窗及其悬浮子窗。"""
    mark_app_shutting_down()
    closed = _force_close_all_tool_windows()
    if closed:
        logger.info("已关闭 %d 个工具窗口", closed)


def _force_close_all_tool_windows() -> int:
    from tools import get_tools

    closed = 0
    for meta in get_tools():
        inst = getattr(meta.cls, "_instance", None)
        if inst is None:
            continue
        cleanup = getattr(inst, "_cleanup_for_release", None)
        for attr in ("_danmu_win", "_overtime_win", "_user_time_win"):
            sub = getattr(inst, attr, None)
            if sub is not None:
                sub.close()
        inst.close()
        release_tool_singleton(meta.cls, cleanup=cleanup)
        unregister_tool_from_page(meta.name)
        closed += 1
    return closed


def gift_names_cached() -> list[str]:
    global _GIFT_NAMES_CACHE
    if _GIFT_NAMES_CACHE is None:
        from resources.gift.gift_info import all_gifts
        _GIFT_NAMES_CACHE = sorted(all_gifts().keys())
    return _GIFT_NAMES_CACHE


def _screen_dpr() -> float:
    from PySide6.QtWidgets import QApplication
    scr = QApplication.primaryScreen()
    return float(scr.devicePixelRatio()) if scr else 1.0


def scale_pixmap_dpr(px: QPixmap, side: int) -> QPixmap:
    """缩到逻辑边长×DPR 的物理像素，再标记 devicePixelRatio，避免高分屏发糊。"""
    dpr = _screen_dpr()
    phys = max(1, round(max(1, int(side)) * dpr))
    out = px.scaled(phys, phys, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out.setDevicePixelRatio(dpr)
    return out


def load_gift_pixmap(gift_name: str, side: int, *, tool_id: str = "overtime") -> QPixmap | None:
    from resources.gift.gift_info import get_gift_id, get_icon_path

    path = get_icon_path(gift_name)
    if not path:
        gid = get_gift_id(gift_name)
        if gid:
            for ext in (".webp", ".png", ".jpg"):
                p = os.path.join(_GIFT_ICON_DIR, f"{gid}{ext}")
                if os.path.exists(p):
                    path = p
                    break
    if not path:
        return None

    src = _GIFT_SRC_CACHE.get(path)
    if src is None or src.isNull():
        src = QPixmap(path)
        if src.isNull():
            # 部分 webp：Pillow 解码一次，按 DPR=1 缓存源图，后续 Qt 缩放
            from resources.skin.media import load_still
            still = load_still(path, max(side, 168), dpr=1.0, scale="smooth")
            if still.pixmap.isNull():
                return None
            src = still.pixmap
            src.setDevicePixelRatio(1.0)
        _GIFT_SRC_CACHE[path] = src

    return scale_pixmap_dpr(src, side)
