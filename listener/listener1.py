"""
listener1.py — JS Hook 方案
注入 JS 拦截 Array.prototype.push，从页面内存直接捞消息。

使用:
    from listener.listener1 import start_listener

    def on_status(connected: bool):
        print("已连接" if connected else "已断开")

依赖:
    pip install playwright protobuf
    playwright install chromium-headless-shell   # 下载到 browsers/ 目录
    先运行 login.py 生成 state.json（可选；没有也可连接，仅日志提醒）
    或设 config.json use_system_browser=true 跳过 bundled

浏览器策略:
    默认优先使用 browsers/ 下的 bundled Chromium；
    force_system=True 或 config.json 中 use_system_browser=true 时改用系统 Chrome/Edge。
    系统浏览器冷启动较慢，进房状态（web/enter）等待窗口自动放宽到 _ENTER_TIMEOUT_SYSTEM。
    开播判定以 webcast/room/web/enter 的 status 为准（2=开播，4=未开播/结束）。
"""

import asyncio
import json
import os
import sys
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright

from util.playwright_bootstrap import launch_chromium
from util.log_util import get_listener_logger, make_msg_logger, on_connect_success
from util.models import (
    CONTROL_STATUS_FINISH,
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
    is_living_enter_status,
)
from util.room_enter import (
    describe_enter_status,
    extract_room_status_from_page,
    is_room_enter_url,
    parse_room_enter_payload,
)

logger = get_listener_logger(1)

_ENTER_TIMEOUT = 15.0       # 进入直播间后等待 web/enter 状态的秒数
_ENTER_TIMEOUT_SYSTEM = 28.0  # 系统浏览器（打包常走这条）更慢，放宽进房状态窗口

# 供 UI 线程优雅停止（避免 loop.stop 打断 Playwright 管道清理）
_STOP_SESSION: dict = {"loop": None, "shutdown": None}


def request_listener_stop() -> bool:
    """从其他线程请求停止当前监听。成功调度返回 True。"""
    loop = _STOP_SESSION.get("loop")
    shutdown = _STOP_SESSION.get("shutdown")
    if not loop or not shutdown or loop.is_closed() or not loop.is_running():
        return False
    try:
        asyncio.run_coroutine_threadsafe(shutdown(), loop)
        return True
    except Exception:
        return False


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
    *,
    force_system: bool = False,
    browser_channel: str | None = None,
):
    from util.playwright_bootstrap import close_playwright

    msg_logger = make_msg_logger(live_id) if debug else None
    logger.info(f"开始连接，直播间: {live_id}")

    def _emit_status(val: bool):
        if on_status:
            try:
                on_status(val)
            except Exception as e:
                logger.debug(f"状态回调异常: {e}")

    playwright = await async_playwright().start()
    browser = None
    context = None
    try:
        browser = await launch_chromium(
            playwright, headless=headless, force_system=force_system, channel=browser_channel,
        )
        from util.playwright_bootstrap import use_system_browser
        enter_timeout = (
            _ENTER_TIMEOUT_SYSTEM
            if (force_system or use_system_browser())
            else _ENTER_TIMEOUT
        )
        ctx_kwargs: dict = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1920, "height": 1080},
        }
        if state_file and os.path.isfile(state_file):
            ctx_kwargs["storage_state"] = state_file
            logger.info("已加载登录态: %s", state_file)
        else:
            logger.warning(
                "未找到登录态文件 %s，将以未登录方式连接（无法拿到礼物数据，可能被限流）",
                state_file or "state.json",
            )
        context = await browser.new_context(**ctx_kwargs)
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)
        page = await context.new_page()
        await page.add_init_script(_HOOK_JS)

        # 以 webcast/room/web/enter 的 status 判定开播（2=开播，4=未开播/结束）
        _state = {"live_confirmed": False, "stopped": False, "enter_seen": False}
        stop_event = asyncio.Event()
        enter_timer: asyncio.Task | None = None

        async def _shutdown():
            if _state["stopped"]:
                if not stop_event.is_set():
                    stop_event.set()
                return
            _state["stopped"] = True
            nonlocal enter_timer
            if enter_timer and not enter_timer.done():
                enter_timer.cancel()
            # 只关浏览器层；driver 在外层 finally 统一 stop，避免早发 status 打断清理
            try:
                if context is not None:
                    await context.close()
            except Exception:
                pass
            try:
                if browser is not None:
                    await browser.close()
            except Exception:
                pass
            stop_event.set()

        _STOP_SESSION["loop"] = asyncio.get_running_loop()
        _STOP_SESSION["shutdown"] = _shutdown

        async def _apply_enter(enter) -> None:
            if enter is None or _state["enter_seen"] or _state["stopped"]:
                return
            _state["enter_seen"] = True
            logger.info(
                "进房状态 enter.status=%s (%s) room_status=%s",
                enter.status,
                describe_enter_status(enter.status),
                enter.room_status,
            )
            try:
                callback(enter)
            except Exception as e:
                logger.debug(f"enter 回调异常: {e}")
            if is_living_enter_status(enter.status):
                _confirm_live()
            else:
                await _fail_unlive(
                    f"直播间未开播（enter.status={enter.status}，{describe_enter_status(enter.status)}）"
                )

        async def _fail_unlive(reason: str):
            if _state["live_confirmed"] or _state["stopped"]:
                return
            logger.warning(reason)
            # 立刻刷新 UI（打包版 playwright.stop 可能卡住，不能等 finally 才 emit）
            _emit_status(False)
            await _shutdown()

        async def _disconnect_clean(reason: str | None = None):
            if _state["stopped"]:
                return
            if reason:
                logger.info(reason)
            notify = _state["live_confirmed"]
            if notify:
                _emit_status(False)
            await _shutdown()

        def _confirm_live() -> None:
            if _state["live_confirmed"] or _state["stopped"]:
                return
            _state["live_confirmed"] = True
            nonlocal enter_timer
            if enter_timer and not enter_timer.done():
                enter_timer.cancel()
            on_connect_success("listener1")
            logger.info("✅ 直播间正在直播")
            _emit_status(True)

        async def _on_response(resp):
            if _state["stopped"] or _state["enter_seen"]:
                return
            if not is_room_enter_url(resp.url):
                return
            body = None
            try:
                body = await resp.text()
            except Exception:
                try:
                    raw = await resp.body()
                    body = raw.decode("utf-8", errors="ignore") if raw else None
                except Exception as e:
                    logger.debug(f"读取 enter 响应失败: {e}")
                    return
            await _apply_enter(parse_room_enter_payload(body))

        def handle_console(msg):
            text = msg.text
            if not text.startswith("DY_MSG:"):
                return
            try:
                raw   = json.loads(text[7:])
                built = _build(raw)
                if not built:
                    return
                if isinstance(built, ControlMessage) and built.status == CONTROL_STATUS_FINISH:
                    if _state["live_confirmed"] and not _state["stopped"]:
                        asyncio.ensure_future(
                            _disconnect_clean("收到下播控制消息，结束监听")
                        )
                    return
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
            asyncio.ensure_future(_disconnect_clean())

        page.on("close", _on_page_close)
        page.on("console", handle_console)
        page.on("response", lambda r: asyncio.ensure_future(_on_response(r)))

        logger.info(f"已进入直播间: {live_id}")
        await page.goto(
            f"https://live.douyin.com/{live_id}",
            wait_until="commit",
        )

        async def _enter_wait_timeout():
            await asyncio.sleep(enter_timeout)
            if _state["enter_seen"] or _state["live_confirmed"] or _state["stopped"]:
                return
            # XHR 丢失时兜底读页面 RENDER_DATA（打包系统 Chrome 常见）
            try:
                fallback = await extract_room_status_from_page(page)
            except Exception as e:
                logger.debug(f"页面兜底解析失败: {e}")
                fallback = None
            if fallback is not None:
                logger.info("未捕获 web/enter，使用页面 RENDER_DATA 兜底")
                await _apply_enter(fallback)
                return
            await _fail_unlive(
                f"{enter_timeout:.0f}s 内未收到进房状态（web/enter），判定为未开播"
            )

        enter_timer = asyncio.create_task(_enter_wait_timeout())
        await stop_event.wait()
    finally:
        try:
            await close_playwright(playwright, browser=browser, context=context)
        finally:
            if _STOP_SESSION.get("loop") is asyncio.get_running_loop():
                _STOP_SESSION["loop"] = None
                _STOP_SESSION["shutdown"] = None


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
    force_system: bool = False,
    browser_channel: str | None = None,
):
    """
    启动 JS Hook 监听，阻塞运行。

    参数:
        live_id       直播间 ID
        callback      每条消息的回调，接收 LiveMessage 子类实例
        state_file    playwright 登录态文件（login.py 生成）
        headless      是否无头模式
        debug         True 时将所有 msg 写入 log/msg_log/<live_id>_<ts>.log
        on_status     连接状态回调 on_status(True)=已连接  on_status(False)=已断开
        force_system  True 时强制使用系统 Chrome/Edge（不用项目 browsers/）
    """
    asyncio.run(_run(live_id, callback, state_file, headless, debug, on_status,
                     force_system=force_system, browser_channel=browser_channel))


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