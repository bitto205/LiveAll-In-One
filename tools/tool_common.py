"""工具模块公共：单例守卫、礼物图标/名称缓存、应用退出。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

_GIFT_ICON_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gift", "icon",
)
_GIFT_NAMES_CACHE: list[str] | None = None
_APP_SHUTTING_DOWN = False


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


def mark_app_shutting_down() -> None:
    global _APP_SHUTTING_DOWN
    _APP_SHUTTING_DOWN = True


def is_app_shutting_down() -> bool:
    return _APP_SHUTTING_DOWN


def shutdown_all_tools() -> None:
    """退出主程序时关闭所有工具窗及其悬浮子窗。"""
    mark_app_shutting_down()
    from tools import get_tools

    for meta in get_tools():
        inst = getattr(meta.cls, "_instance", None)
        if inst is None:
            continue
        for attr in ("_danmu_win", "_overtime_win", "_user_time_win"):
            sub = getattr(inst, attr, None)
            if sub is not None:
                sub.close()
        inst.close()


def gift_names_cached() -> list[str]:
    global _GIFT_NAMES_CACHE
    if _GIFT_NAMES_CACHE is None:
        from gift.gift_info import all_gifts
        _GIFT_NAMES_CACHE = sorted(all_gifts().keys())
    return _GIFT_NAMES_CACHE


def load_gift_pixmap(gift_name: str, side: int) -> QPixmap | None:
    from gift.gift_info import get_gift_id, get_icon_path

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
    px = QPixmap(path)
    if px.isNull():
        return None
    return px.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
