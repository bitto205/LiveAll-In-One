"""
listener1.py — JS Hook 方案
注入 JS 拦截 Array.prototype.push，从页面内存直接捞消息。

使用:
    from listener1 import start_listener

    def on_status(connected: bool):
        print("已连接" if connected else "已断开")

    start_listener("YOUR_LIVE_ID", on_message, on_status=on_status)
    start_listener("YOUR_LIVE_ID", on_message, debug=True)

依赖:
    pip install playwright
    playwright install chromium
    先运行 login.py 生成 state.json
"""

import asyncio
import json
import os
import sys
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright

from listener.log_util import get_listener_logger, make_msg_logger, on_connect_success
from listener.models import (
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

logger = get_listener_logger(1)

_WS_TIMEOUT = 10.0       # 进入直播间后等待 WebSocket 建立的秒数
_LIVE_DATA_TIMEOUT = 10.0  # WebSocket 建立后等待直播消息的秒数


# ─────────────────────────────────────────────
# 注入 JS
# ─────────────────────────────────────────────
_HOOK_JS = r"""
(() => {
    if (window.__DY_HOOK__) return;
    window.__DY_HOOK__ = true;

    const oldPush = Array.prototype.push;

    Array.prototype.push = function (...args) {
        try {
            for (const msg of args) {
                if (!msg || typeof msg !== "object") continue;
                const method = msg.method;
                if (!method) continue;

                const payload = msg.payload || {};
                const user    = payload.user?.desensitized_nickname
                             || payload.user?.nickname
                             || "";
                const user_id = String(payload.user?.id || payload.user?.id_str || "");
                let data = null;

                if (method === "WebcastChatMessage") {
                    const content = payload.content || "";
                    if (user && content)
                        data = { type: "chat", user, user_id, content };
                }
                else if (method === "WebcastGiftMessage") {
                    const gift       = payload?.gift?.name || "";
                    const gift_id    = payload?.gift?.id ?? 0;
                    const repeat_end = payload?.repeat_end;
                    const count      = payload?.combo_count ? Number(payload.combo_count) : 1;
                    if (user && gift && String(repeat_end).trim() === "1")
                        data = { type: "gift", user, user_id, gift, gift_id, count,
                                 repeat_end: 1 };
                }
                else if (method === "WebcastLikeMessage") {
                    const count = Number(payload?.count || 1);
                    if (user) data = { type: "like", user, user_id, count };
                }
                else if (method === "WebcastMemberMessage") {
                    if (user) data = { type: "enter", user, user_id };
                }
                else if (method === "WebcastSocialMessage") {
                    if (user) data = { type: "follow", user, user_id };
                }
                else if (method === "WebcastRoomUserSeqMessage") {
                    data = {
                        type:    "online",
                        current: Number(payload?.total || 0),
                        total:   Number(payload?.total_pv_for_anchor || 0),
                    };
                }
                else if (method === "WebcastFansclubMessage") {
                    const content = payload?.content || "";
                    data = { type: "fansclub", user, user_id, content };
                }
                else if (method === "WebcastEmojiChatMessage") {
                    const emoji_id        = String(payload?.emoji_id || "");
                    const default_content = payload?.default_content || "";
                    data = { type: "emoji", user, user_id, emoji_id, default_content };
                }
                else if (method === "WebcastRoomStatsMessage") {
                    const display_long = payload?.display_long || "";
                    if (display_long) data = { type: "room_stats", display_long };
                }
                else if (method === "WebcastRoomRankMessage") {
                    const raw = payload?.ranks_list || [];
                    const ranks = raw.map(r => ({
                        user_id:  String(r?.user?.id || ""),
                        nickname: r?.user?.nickname || r?.user?.nick_name || "",
                        rank:     Number(r?.rank || 0),
                    }));
                    data = { type: "rank", ranks };
                }
                else if (method === "WebcastControlMessage") {
                    const status = Number(payload?.status || 0);
                    data = { type: "control", status };
                }

                if (data) {
                    try { console.log("DY_MSG:" + JSON.stringify(data)); } catch(e) {}
                }
            }
        } catch(e) {}

        return oldPush.apply(this, args);
    };
})();
"""


# ─────────────────────────────────────────────
# console → dataclass
# ─────────────────────────────────────────────
def _build(raw: dict) -> LiveMessage | None:
    t = raw.get("type", "")
    try:
        if t == "chat":
            return ChatMessage(user=raw["user"], user_id=raw.get("user_id", ""),
                               content=raw.get("content", ""))
        if t == "gift":
            return GiftMessage(user=raw["user"], user_id=raw.get("user_id", ""),
                               gift=raw["gift"], gift_id=int(raw.get("gift_id", 0)),
                               count=int(raw.get("count", 1)),
                               repeat_end=int(raw.get("repeat_end", -1)))
        if t == "like":
            return LikeMessage(user=raw["user"], user_id=raw.get("user_id", ""),
                               count=int(raw.get("count", 1)))
        if t == "enter":
            return EnterMessage(user=raw["user"], user_id=raw.get("user_id", ""))
        if t == "follow":
            return FollowMessage(user=raw["user"], user_id=raw.get("user_id", ""))
        if t == "online":
            return OnlineMessage(current=int(raw.get("current", 0)),
                                 total=int(raw.get("total", 0)))
        if t == "fansclub":
            return FansclubMessage(user=raw.get("user", ""), user_id=raw.get("user_id", ""),
                                   content=raw.get("content", ""))
        if t == "emoji":
            return EmojiChatMessage(user=raw.get("user", ""), user_id=raw.get("user_id", ""),
                                    emoji_id=raw.get("emoji_id", ""),
                                    default_content=raw.get("default_content", ""))
        if t == "room_stats":
            return RoomStatsMessage(display_long=raw.get("display_long", ""))
        if t == "rank":
            return RoomRankMessage(ranks=raw.get("ranks", []))
        if t == "control":
            return ControlMessage(status=int(raw.get("status", 0)))
    except Exception as e:
        logger.debug(f"消息构建失败 [{t}]: {e}")
    return None


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
    msg_logger = make_msg_logger(live_id) if debug else None
    logger.info(f"开始连接，直播间: {live_id}")

    def _emit_status(val: bool):
        if on_status:
            try:
                on_status(val)
            except Exception as e:
                logger.debug(f"状态回调异常: {e}")

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
        await page.add_init_script(_HOOK_JS)

        # 两阶段：10s 内建立 WS → WS 后 10s 内收到任意支持的直播消息
        _state = {"live_confirmed": False, "stopped": False}
        stop_event = asyncio.Event()
        seen_ws: set[str] = set()
        ws_connected = False
        live_timer: asyncio.Task | None = None

        async def _shutdown():
            if _state["stopped"]:
                if not stop_event.is_set():
                    stop_event.set()
                return
            _state["stopped"] = True
            nonlocal live_timer
            if live_timer and not live_timer.done():
                live_timer.cancel()
            try:
                await browser.close()
            except Exception:
                pass
            stop_event.set()

        async def _fail_unlive(reason: str):
            if _state["live_confirmed"] or _state["stopped"]:
                return
            logger.warning(reason)
            _emit_status(False)
            await _shutdown()

        async def _start_live_wait():
            nonlocal live_timer
            if live_timer and not live_timer.done():
                live_timer.cancel()

            async def _live_timeout():
                await asyncio.sleep(_LIVE_DATA_TIMEOUT)
                if not _state["live_confirmed"]:
                    await _fail_unlive(
                        f"WebSocket 建立后 {_LIVE_DATA_TIMEOUT:.0f}s 内未收到直播消息，判定为未开播"
                    )

            live_timer = asyncio.create_task(_live_timeout())

        def _on_websocket(ws):
            nonlocal ws_connected
            if "/push/v2/" not in ws.url:
                return
            if ws.url in seen_ws:
                return
            seen_ws.add(ws.url)
            if ws_connected:
                return
            ws_connected = True
            logger.info(
                f"WebSocket 已建立，{_LIVE_DATA_TIMEOUT:.0f}s 内等待直播消息…"
            )
            asyncio.ensure_future(_start_live_wait())

        def handle_console(msg):
            text = msg.text
            if not text.startswith("DY_MSG:"):
                return
            try:
                raw   = json.loads(text[7:])
                built = _build(raw)
                if not built:
                    return
                if not _state["live_confirmed"]:
                    _state["live_confirmed"] = True
                    if live_timer and not live_timer.done():
                        live_timer.cancel()
                    on_connect_success("listener1")
                    logger.info("✅ 直播间正在直播")
                    _emit_status(True)
                if not _state["live_confirmed"]:
                    return
                if msg_logger:
                    msg_logger.info(built)
                callback(built)
            except Exception as e:
                logger.debug(f"控制台解析失败: {e}")

        def _on_page_close(_):
            if _state["stopped"]:
                return
            if _state["live_confirmed"]:
                _emit_status(False)
            asyncio.ensure_future(_shutdown())

        page.on("close", _on_page_close)
        page.on("console", handle_console)
        page.on("websocket", _on_websocket)

        logger.info(f"已进入直播间: {live_id}")
        await page.goto(
            f"https://live.douyin.com/{live_id}",
            wait_until="commit",
        )

        async def _ws_wait_timeout():
            await asyncio.sleep(_WS_TIMEOUT)
            if not ws_connected:
                await _fail_unlive(
                    f"{_WS_TIMEOUT:.0f}s 内未建立 WebSocket，判定为未开播"
                )

        asyncio.create_task(_ws_wait_timeout())
        await stop_event.wait()


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
    启动 JS Hook 监听，阻塞运行。

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

    start_listener("", on_message, on_status=on_status, debug=True)