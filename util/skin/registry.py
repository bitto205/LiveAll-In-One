"""皮肤发现与选用。JSON 在 resources/skin/<tool>/<id>/，绘制实现在本包。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

import config as _cfg
from util.paths import skin_root
from util.skin.base import ToolSkin
from util.skin.danmu_default import DanmuDefaultSkin
from util.skin.overtime_default import OvertimeDefaultSkin

logger = logging.getLogger(__name__)

_SKIN_FACTORIES: dict[str, dict[str, Callable[[Path, dict], ToolSkin]]] = {
    "danmu": {
        "default": lambda root, meta: DanmuDefaultSkin(root, meta),
    },
    "overtime": {
        "default": lambda root, meta: OvertimeDefaultSkin(root, meta),
    },
}

_CACHE: dict[tuple[str, str], ToolSkin] = {}


def skin_tool_dir(tool_id: str) -> Path:
    return skin_root() / tool_id


def _config_key(tool_id: str) -> str:
    return f"skin.{tool_id}"


def _read_meta(skin_dir: Path) -> dict:
    path = skin_dir / "skin.json"
    if not path.is_file():
        return {"id": skin_dir.name, "name": skin_dir.name}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"id": skin_dir.name}
    except Exception as e:
        logger.warning("读取皮肤失败 %s: %s", path, e)
        return {"id": skin_dir.name, "name": skin_dir.name}


def list_skins(tool_id: str) -> list[dict]:
    base = skin_tool_dir(tool_id)
    out: list[dict] = []
    if not base.is_dir():
        return out
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        meta = _read_meta(child)
        sid = str(meta.get("id") or child.name)
        declared = meta.get("tool")
        if declared and str(declared) != tool_id:
            logger.warning(
                "忽略皮肤 %s：tool=%s 与目录 %s 不一致",
                child, declared, tool_id,
            )
            continue
        out.append({
            "id": sid,
            "name": str(meta.get("name") or sid),
            "path": str(child),
            "version": int(meta.get("version") or 1),
        })
    return out


def get_skin(tool_id: str, skin_id: str | None = None) -> ToolSkin:
    sid = skin_id or "default"
    key = (tool_id, sid)
    if key in _CACHE:
        return _CACHE[key]

    skin_dir = skin_tool_dir(tool_id) / sid
    if not skin_dir.is_dir() and sid != "default":
        skin_dir = skin_tool_dir(tool_id) / "default"
        sid = "default"
        key = (tool_id, sid)
        if key in _CACHE:
            return _CACHE[key]

    meta = _read_meta(skin_dir) if skin_dir.is_dir() else {"id": sid, "name": sid}
    meta.setdefault("tool", tool_id)
    meta.setdefault("id", sid)

    factories = _SKIN_FACTORIES.get(tool_id) or {}
    factory = factories.get(sid) or factories.get("default")
    root = skin_dir if skin_dir.is_dir() else skin_tool_dir(tool_id) / "default"
    if factory is None:
        skin: ToolSkin = ToolSkin(root, meta)
    else:
        skin = factory(root, meta)
    skin.tool_id = tool_id
    _CACHE[key] = skin
    return skin


def get_active_skin(tool_id: str) -> ToolSkin:
    sid = _cfg.get(_config_key(tool_id), "default")
    if not isinstance(sid, str) or not sid:
        sid = "default"
    available = {s["id"] for s in list_skins(tool_id)}
    if available and sid not in available and "default" in available:
        sid = "default"
    return get_skin(tool_id, sid)


def set_active_skin(tool_id: str, skin_id: str) -> None:
    _cfg.set(_config_key(tool_id), skin_id)
    for k in list(_CACHE):
        if k[0] == tool_id:
            _CACHE.pop(k, None)


def clear_skin_cache() -> None:
    _CACHE.clear()
