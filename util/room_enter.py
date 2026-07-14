"""解析抖音进房接口（webcast/room/web/enter 等）中的开播状态。"""

from __future__ import annotations

import json
import re
from typing import Any

from util.models import (
    ROOM_ENTER_STATUS_ENDED,
    ROOM_ENTER_STATUS_LIVING,
    RoomEnterStatusMessage,
    is_living_enter_status,
)

# URL / 路径片段：匹配网页与伴侣进房相关接口
_ENTER_PATH_RE = re.compile(r"/webcast/room/(?:web/)?enter", re.I)


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
