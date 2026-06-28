"""
gift/gift_info.py — 礼物信息查询

用法：
    from gift.gift_info import get_diamonds, get_gift_id, get_icon_path

    diamonds = get_diamonds("嘉年华")   # → 30000，查不到返回 None
    gid      = get_gift_id("嘉年华")    # → int gift_id，查不到返回 None
    icon     = get_icon_path("嘉年华")  # → 实际路径，找不到返回 None
"""

import json
import os

_DIR  = os.path.dirname(os.path.abspath(__file__))
_JSON = os.path.join(_DIR, "gift_info.json")
_ICON = os.path.join(_DIR, "icon")

_cache: dict[str, dict] | None = None


def _load() -> dict[str, dict]:
    global _cache
    if _cache is None:
        try:
            with open(_JSON, encoding="utf-8") as f:
                _cache = json.load(f)
        except Exception:
            _cache = {}
    return _cache


def reload():
    """强制重新从磁盘加载（手动更新 JSON 后调用）。"""
    global _cache
    _cache = None
    _load()


def get_diamonds(gift_name: str) -> int | None:
    """返回礼物钻石价格，查不到返回 None。"""
    entry = _load().get(gift_name)
    return entry["price"] if entry else None


def get_gift_id(gift_name: str) -> int | None:
    """返回礼物 gift_id，查不到返回 None。"""
    entry = _load().get(gift_name)
    return entry["gift_id"] if entry else None


_ICON_EXTS = (".webp", ".png", ".jpg", ".jpeg", ".gif")


def get_icon_path(gift_name: str) -> str | None:
    """返回礼物图标的实际路径，找不到返回 None。"""
    for ext in _ICON_EXTS:
        path = os.path.join(_ICON, f"{gift_name}{ext}")
        if os.path.isfile(path):
            return path
    return None


def icon_exists(gift_name: str) -> bool:
    return get_icon_path(gift_name) is not None


def all_gifts() -> dict[str, dict]:
    """返回全部礼物信息字典副本 {name: {price, gift_id}}。"""
    return dict(_load())
