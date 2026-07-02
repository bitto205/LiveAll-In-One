"""礼物信息查询（gift_info.json + icon/）"""
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
    global _cache
    _cache = None
    _load()


def get_diamonds(gift_name: str) -> int | None:
    entry = _load().get(gift_name)
    return entry["price"] if entry else None


def get_gift_id(gift_name: str) -> int | None:
    entry = _load().get(gift_name)
    return entry["gift_id"] if entry else None


_ICON_EXTS = (".webp", ".png", ".jpg", ".jpeg", ".gif")


def get_icon_path(gift_name: str) -> str | None:
    for ext in _ICON_EXTS:
        path = os.path.join(_ICON, f"{gift_name}{ext}")
        if os.path.isfile(path):
            return path
    return None


def icon_exists(gift_name: str) -> bool:
    return get_icon_path(gift_name) is not None


def all_gifts() -> dict[str, dict]:
    return dict(_load())
