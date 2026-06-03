"""
listener2.py — WSS 拦截方案
playwright 捕获真实 WebSocket 帧，protobuf 解析，协议级稳定。

使用:
    from listener2 import start_listener

    def on_status(connected: bool):
        print("已连接" if connected else "已断开")

    start_listener("YOUR_LIVE_ID", on_message, on_status=on_status)
    start_listener("YOUR_LIVE_ID", on_message, debug=True)

依赖:
    pip install playwright protobuf
    playwright install chromium
    先运行 login.py 生成 state.json
"""

import asyncio
import gzip
import logging
import os
from datetime import datetime
from typing import Callable

from playwright.async_api import async_playwright

import Live_pb2
from models import (
    ChatMessage,
    ControlMessage,
    EmojiChatMessage,
    EnterMessage,
    FansclubMessage,
    FollowMessage,
    GiftMessage,
    LikeMessage,
    LiveMessage,
    OnlineMessage,
    RoomRankMessage,
    RoomStatsMessage,
)

# ─────────────────────────────────────────────
# 目录
# ─────────────────────────────────────────────
os.makedirs("log", exist_ok=True)
os.makedirs("msg_log", exist_ok=True)

_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

# ─────────────────────────────────────────────
# 主日志：系统事件，不含 msg 内容
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(f"log/listener2_{_ts}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# msg 日志：仅 debug=True 时启用
# ─────────────────────────────────────────────
def _make_msg_logger(live_id: str) -> logging.Logger:
    name = f"msg_{live_id}_{_ts}"
    ml = logging.getLogger(name)
    ml.setLevel(logging.INFO)
    h = logging.FileHandler(f"msg_log/{live_id}_{_ts}.log", encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
    ml.addHandler(h)
    ml.propagate = False
    return ml


# ─────────────────────────────────────────────
# 兼容工具
# ─────────────────────────────────────────────
def _g(obj, *keys, default=""):
    for k in keys:
        v = getattr(obj, k, None)
        if v is not None and v != "" and v != 0:
            return v
    return default

def _user_name(user) -> str:
    return _g(user, "nickname", "nick_name")

def _user_id(user) -> str:
    return str(_g(user, "id", "id_str", "userId", default=""))


# ─────────────────────────────────────────────
# 单条消息解析
# ─────────────────────────────────────────────
def _parse_item(method: str, payload: bytes) -> LiveMessage | None:
    try:
        if method == "WebcastChatMessage":
            pb = Live_pb2.ChatMessage()
            pb.ParseFromString(payload)
            user = _user_name(pb.user)
            content = getattr(pb, "content", "")
            if user and content:
                return ChatMessage(user=user, user_id=_user_id(pb.user), content=content)

        elif method == "WebcastGiftMessage":
            pb = Live_pb2.GiftMessage()
            pb.ParseFromString(payload)
            if int(_g(pb, "repeatEnd", "repeat_end", default=0)) != 1:
                return None
            user = _user_name(pb.user)
            gift_name = _g(pb.gift, "name") if pb.gift else ""
            gift_id   = int(_g(pb.gift, "id", default=0)) if pb.gift else 0
            count     = int(_g(pb, "comboCount", "combo_count", default=1))
            if user and gift_name:
                return GiftMessage(user=user, user_id=_user_id(pb.user),
                                   gift=gift_name, gift_id=gift_id, count=count)

        elif method == "WebcastLikeMessage":
            pb = Live_pb2.LikeMessage()
            pb.ParseFromString(payload)
            user = _user_name(pb.user)
            if user:
                return LikeMessage(user=user, user_id=_user_id(pb.user),
                                   count=int(_g(pb, "count", default=1)))

        elif method == "WebcastMemberMessage":
            pb = Live_pb2.MemberMessage()
            pb.ParseFromString(payload)
            user = _user_name(pb.user)
            if user:
                return EnterMessage(user=user, user_id=_user_id(pb.user))

        elif method == "WebcastSocialMessage":
            pb = Live_pb2.SocialMessage()
            pb.ParseFromString(payload)
            user = _user_name(pb.user)
            if user:
                return FollowMessage(user=user, user_id=_user_id(pb.user))

        elif method == "WebcastRoomUserSeqMessage":
            pb = Live_pb2.RoomUserSeqMessage()
            pb.ParseFromString(payload)
            return OnlineMessage(
                current=int(_g(pb, "total", default=0)),
                total=int(_g(pb, "totalPvForAnchor", "total_pv_for_anchor", default=0)),
            )

        elif method == "WebcastFansclubMessage":
            pb = Live_pb2.FansclubMessage()
            pb.ParseFromString(payload)
            return FansclubMessage(
                user=_user_name(pb.user) if hasattr(pb, "user") else "",
                user_id=_user_id(pb.user) if hasattr(pb, "user") else "",
                content=getattr(pb, "content", ""),
            )

        elif method == "WebcastEmojiChatMessage":
            pb = Live_pb2.EmojiChatMessage()
            pb.ParseFromString(payload)
            return EmojiChatMessage(
                user=_user_name(pb.user) if hasattr(pb, "user") else "",
                user_id=_user_id(pb.user) if hasattr(pb, "user") else "",
                emoji_id=str(_g(pb, "emojiId", "emoji_id", default="")),
                default_content=str(_g(pb, "defaultContent", "default_content", default="")),
            )

        elif method == "WebcastRoomStatsMessage":
            pb = Live_pb2.RoomStatsMessage()
            pb.ParseFromString(payload)
            return RoomStatsMessage(
                display_long=str(_g(pb, "displayLong", "display_long", default="")),
            )

        elif method == "WebcastRoomRankMessage":
            pb = Live_pb2.RoomRankMessage()
            pb.ParseFromString(payload)
            return RoomRankMessage(
                ranks=list(_g(pb, "ranksList", "ranks_list", default=[]))
            )

        elif method == "WebcastControlMessage":
            pb = Live_pb2.ControlMessage()
            pb.ParseFromString(payload)
            return ControlMessage(status=int(getattr(pb, "status", 0)))

        else:
            logger.debug(f"未处理消息类型: {method}")

    except Exception as e:
        logger.debug(f"解析失败 [{method}]: {e}")

    return None


def _parse_frame(payload: bytes) -> list[LiveMessage]:
    results: list[LiveMessage] = []
    try:
        frame = Live_pb2.PushFrame()
        frame.ParseFromString(payload)
        if not frame.payload:
            return results
        try:
            body = gzip.decompress(frame.payload)
        except Exception:
            body = frame.payload
        response = Live_pb2.LiveResponse()
        response.ParseFromString(body)
    except Exception as e:
        logger.debug(f"帧解析失败: {e}")
        return results

    for item in response.messagesList:
        msg = _parse_item(item.method, item.payload)
        if msg is not None:
            results.append(msg)
    return results


# ─────────────────────────────────────────────
# 核心
# ─────────────────────────────────────────────
async def _run(
    live_id: str,
    callback: Callable[[LiveMessage], None],
    state_file: str,
    headless: bool,
    debug: bool,
    on_status: Callable[[bool], None] | None,
):
    msg_logger = _make_msg_logger(live_id) if debug else None
    seen_ws: set[str] = set()

    def _emit_status(val: bool):
        if on_status:
            try:
                on_status(val)
            except Exception as e:
                logger.debug(f"on_status 回调异常: {e}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            storage_state=state_file,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)
        page = await context.new_page()

        def on_websocket(ws):
            if "/push/v2/" not in ws.url:
                return
            if ws.url in seen_ws:
                return
            seen_ws.add(ws.url)
            logger.info("✅ WebSocket 已捕获")
            _emit_status(True)          # ── 连接成功

            def on_frame(raw: bytes):
                for msg in _parse_frame(raw):
                    try:
                        if msg_logger:
                            msg_logger.info(msg)
                        callback(msg)
                    except Exception as e:
                        logger.error(f"callback 异常: {e}")

            def on_ws_close():
                logger.warning("WebSocket 已断开")
                _emit_status(False)     # ── 断开

            ws.on("framereceived", on_frame)
            ws.on("close", on_ws_close)

        # 页面意外关闭也触发断开
        page.on("close", lambda _: _emit_status(False))

        page.on("websocket", on_websocket)

        logger.info(f"✅ 已进入直播间: {live_id}")
        await page.goto(
            f"https://live.douyin.com/{live_id}",
            wait_until="commit",
        )
        await asyncio.Event().wait()


# ─────────────────────────────────────────────
# 公开接口
# ─────────────────────────────────────────────
def start_listener(
    live_id: str,
    callback: Callable[[LiveMessage], None],
    *,
    state_file: str = "state.json",
    headless: bool = True,
    debug: bool = False,
    on_status: Callable[[bool], None] | None = None,
):
    """
    启动 WSS 拦截监听，阻塞运行。

    参数:
        live_id     直播间 ID
        callback    每条消息的回调，接收 LiveMessage 子类实例
        state_file  playwright 登录态文件（login.py 生成）
        headless    是否无头模式
        debug       True 时将所有 msg 写入 msg_log/<live_id>_<ts>.log
        on_status   连接状态回调 on_status(True)=已连接  on_status(False)=已断开
    """
    asyncio.run(_run(live_id, callback, state_file, headless, debug, on_status))


# ─────────────────────────────────────────────
# 直接运行示例
# ─────────────────────────────────────────────
if __name__ == "__main__":

    connected = False

    def on_status(is_connected: bool):
        global connected
        connected = is_connected
        print(f"[状态] {'🟢 已连接' if is_connected else '🔴 已断开'}")

    def on_message(msg: LiveMessage):
        print(msg)

    start_listener("YOUR_LIVE_ID", on_message, on_status=on_status, debug=True)
