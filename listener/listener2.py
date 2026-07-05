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
import os
import sys
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright

from listener.LiveProtobuf import try_parse_frame
from listener.log_util import get_listener_logger, make_msg_logger, on_connect_success
from listener.models import LiveMessage

logger = get_listener_logger(2)

_WS_TIMEOUT = 10.0       # 进入直播间后等待 WebSocket 建立的秒数
_LIVE_DATA_TIMEOUT = 10.0  # WebSocket 建立后等待直播消息的秒数


# 统一解析接口已迁移到 listener/LiveProtobuf.py


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

        def on_websocket(ws):
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

            def on_frame(raw: bytes):
                channel_ok, msgs = try_parse_frame(raw)
                if channel_ok and not _state["live_confirmed"]:
                    _state["live_confirmed"] = True
                    if live_timer and not live_timer.done():
                        live_timer.cancel()
                    on_connect_success("listener2")
                    logger.info("✅ 直播间正在直播")
                    _emit_status(True)
                if not _state["live_confirmed"]:
                    return
                for msg in msgs:
                    try:
                        if msg_logger:
                            msg_logger.info(msg)
                        callback(msg)
                    except Exception as e:
                        logger.error(f"回调异常: {e}")

            def on_ws_close():
                if _state["stopped"]:
                    return
                logger.warning("WebSocket 已断开")
                _emit_status(False)

            ws.on("framereceived", on_frame)
            ws.on("close", on_ws_close)

        def _on_page_close(_):
            if _state["stopped"]:
                return
            if _state["live_confirmed"]:
                _emit_status(False)
            asyncio.ensure_future(_shutdown())

        page.on("close", _on_page_close)
        page.on("websocket", on_websocket)

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

    start_listener("", on_message, on_status=on_status, debug=True)