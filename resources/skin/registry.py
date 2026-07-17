"""皮肤发现与选用。只展示已注册皮肤；JSON 在 resources/skin/<tool>/<id>/。"""
from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
from typing import Callable

import config as _cfg
from util.paths import skin_root
from resources.skin.base import ToolSkin
from resources.skin.danmu.default.skin import DanmuDefaultSkin
from resources.skin.overtime.default.skin import OvertimeDefaultSkin

logger = logging.getLogger(__name__)

SkinFactory = Callable[[Path, dict], ToolSkin]

_SKIN_FACTORIES: dict[str, dict[str, SkinFactory]] = {
    "danmu": {
        "default": lambda root, meta: DanmuDefaultSkin(root, meta),
    },
    "overtime": {
        "default": lambda root, meta: OvertimeDefaultSkin(root, meta),
    },
}

# 可选皮肤：模块源文件存在且可导入才注册（空残留目录 / 仅 __pycache__ 不会注册）
_OPTIONAL_SKINS: tuple[tuple[str, str, str, str], ...] = (
    ("danmu", "nemuru", "resources.skin.danmu.nemuru.components", "DanmuNemuruSkin"),
    ("overtime", "nemuru", "resources.skin.overtime.nemuru.components", "OvertimeNemuruSkin"),
)

_CACHE: dict[tuple[str, str], ToolSkin] = {}


def skin_tool_dir(tool_id: str) -> Path:
    return skin_root() / tool_id


def register_skin(tool_id: str, skin_id: str, factory: SkinFactory) -> None:
    """显式注册一套皮肤；list_skins 只返回已注册项。"""
    _SKIN_FACTORIES.setdefault(tool_id, {})[skin_id] = factory


def _register_optional_skins() -> None:
    for tool_id, skin_id, mod_name, cls_name in _OPTIONAL_SKINS:
        skin_dir = skin_tool_dir(tool_id) / skin_id
        # 必须有源码，避免分支切换后空目录 + 残留 .pyc 被当成皮肤
        if not (skin_dir / "components.py").is_file() and not (skin_dir / "skin.py").is_file():
            continue
        try:
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name)
            register_skin(
                tool_id,
                skin_id,
                lambda root, meta, _cls=cls: _cls(root, meta),
            )
        except Exception as e:
            logger.debug("跳过可选皮肤 %s/%s: %s", tool_id, skin_id, e)


def _config_key(tool_id: str) -> str:
    return f"skin.{tool_id}"


def _read_meta(skin_dir: Path) -> dict:
    path = skin_dir / "skin.json"
    if not path.is_file():
        return {"id": skin_dir.name, "name": skin_dir.name}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {"id": skin_dir.name}
    except Exception as e:
        logger.warning("读取皮肤失败 %s: %s", path, e)
        return {"id": skin_dir.name, "name": skin_dir.name}


def list_skins(tool_id: str) -> list[dict]:
    """只返回已 register 的皮肤，不扫描目录。"""
    factories = _SKIN_FACTORIES.get(tool_id) or {}
    out: list[dict] = []
    for sid in sorted(factories.keys()):
        skin_dir = skin_tool_dir(tool_id) / sid
        meta = _read_meta(skin_dir) if skin_dir.is_dir() else {"id": sid, "name": sid}
        declared = meta.get("tool")
        if declared and str(declared) != tool_id:
            logger.warning("忽略皮肤 %s：tool=%s 与 %s 不一致", sid, declared, tool_id)
            continue
        out.append({
            "id": str(meta.get("id") or sid),
            "name": str(meta.get("name") or sid),
            "path": str(skin_dir),
            "version": int(meta.get("version") or 1),
        })
    return out


def get_skin(tool_id: str, skin_id: str | None = None) -> ToolSkin:
    sid = skin_id or "default"
    factories = _SKIN_FACTORIES.get(tool_id) or {}
    if sid not in factories:
        sid = "default"

    key = (tool_id, sid)
    if key in _CACHE:
        return _CACHE[key]

    skin_dir = skin_tool_dir(tool_id) / sid
    meta = _read_meta(skin_dir) if skin_dir.is_dir() else {"id": sid, "name": sid}
    meta.setdefault("tool", tool_id)
    meta.setdefault("id", sid)

    factory = factories.get(sid) or factories.get("default")
    root = skin_dir if skin_dir.is_dir() else skin_tool_dir(tool_id) / "default"
    skin = ToolSkin(root, meta) if factory is None else factory(root, meta)
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


_register_optional_skins()
