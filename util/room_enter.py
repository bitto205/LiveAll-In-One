"""解析抖音进房接口（webcast/room/web/enter 等）中的开播状态。"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import unquote

from util.models import (
    ROOM_ENTER_STATUS_ENDED,
    ROOM_ENTER_STATUS_LIVING,
    RoomEnterStatusMessage,
    is_living_enter_status,
)

# URL / 路径片段：匹配网页与伴侣进房相关接口
_ENTER_PATH_RE = re.compile(r"/webcast/room/(?:web/)?enter", re.I)
_STATUS_IN_HTML_RE = re.compile(
    r'"status"\s*:\s*(?P<status>[24])\s*,\s*"status_str"\s*:\s*"\1"',
)


def is_room_enter_url(url: str) -> bool:
    return bool(url and _ENTER_PATH_RE.search(url))


def _as_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def parse_room_enter_payload(payload: Any) -> RoomEnterStatusMessage | None:
    """
    从 enter 接口 JSON（dict / str / bytes）解析房间开播状态。
    成功时返回 RoomEnterStatusMessage；无法解析则返回 None。
    """
    if payload is None:
        return None
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8", errors="ignore")
        except Exception:
            return None
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except Exception:
            return None
    if not isinstance(payload, dict):
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    room_status = _as_int(data.get("room_status"), 0)
    rooms = data.get("data")
    room: dict | None = None
    if isinstance(rooms, list) and rooms:
        first = rooms[0]
        if isinstance(first, dict):
            room = first
    elif isinstance(rooms, dict):
        room = rooms

    if room is None:
        # 无房间对象：多数情况表示未开播 / 无场次
        return RoomEnterStatusMessage(
            status=ROOM_ENTER_STATUS_ENDED,
            room_status=room_status,
        )

    status = _as_int(room.get("status"), _as_int(room.get("status_str"), 0))
    return RoomEnterStatusMessage(
        status=status,
        room_status=room_status,
        title=str(room.get("title") or ""),
        id_str=str(room.get("id_str") or room.get("id") or ""),
    )


def describe_enter_status(status: int) -> str:
    if is_living_enter_status(status):
        return "开播中"
    if status == ROOM_ENTER_STATUS_ENDED:
        return "未开播/已结束"
    return f"未知状态({status})"


def _walk_for_room_status(obj: Any, depth: int = 0) -> RoomEnterStatusMessage | None:
    """从 SSR / RENDER_DATA 嵌套结构里找房间 status。"""
    if depth > 8 or obj is None:
        return None
    if isinstance(obj, dict):
        if "status" in obj and ("status_str" in obj or "id_str" in obj or "title" in obj):
            st = _as_int(obj.get("status"), _as_int(obj.get("status_str"), 0))
            if st in (ROOM_ENTER_STATUS_LIVING, ROOM_ENTER_STATUS_ENDED):
                return RoomEnterStatusMessage(
                    status=st,
                    room_status=_as_int(obj.get("room_status"), 0),
                    title=str(obj.get("title") or ""),
                    id_str=str(obj.get("id_str") or obj.get("id") or ""),
                )
        for v in obj.values():
            found = _walk_for_room_status(v, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj[:20]:
            found = _walk_for_room_status(v, depth + 1)
            if found is not None:
                return found
    return None


def parse_render_data_text(raw: str) -> RoomEnterStatusMessage | None:
    """解析页面 #RENDER_DATA（常为 URL 编码 JSON）。"""
    if not raw:
        return None
    text = raw.strip()
    for candidate in (text, unquote(text)):
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        found = _walk_for_room_status(data)
        if found is not None:
            return found
        m = _STATUS_IN_HTML_RE.search(candidate)
        if m:
            return RoomEnterStatusMessage(status=int(m.group("status")))
    m = _STATUS_IN_HTML_RE.search(text)
    if m:
        return RoomEnterStatusMessage(status=int(m.group("status")))
    return None


async def extract_room_status_from_page(page) -> RoomEnterStatusMessage | None:
    """
    enter 响应丢失时的兜底：读 RENDER_DATA / 页面文案。
    打包环境（系统 Chrome headless、无登录）更容易丢 XHR。
    """
    try:
        raw = await page.evaluate(
            """() => {
                const el = document.getElementById('RENDER_DATA');
                if (el && el.textContent) return el.textContent;
                return null;
            }"""
        )
        if raw:
            found = parse_render_data_text(str(raw))
            if found is not None:
                return found
    except Exception:
        pass

    try:
        html = await page.content()
        found = parse_render_data_text(html)
        if found is not None:
            return found
        if "直播已结束" in html or "直播结束" in html:
            return RoomEnterStatusMessage(status=ROOM_ENTER_STATUS_ENDED)
    except Exception:
        pass
    return None
