"""
listener2.py — WSS 拦截方案
playwright 捕获真实 WebSocket 帧，protobuf 解析，协议级稳定。

使用:
    from listener.listener2 import start_listener

    def on_status(connected: bool):
        print("已连接" if connected else "已断开")

    start_listener("YOUR_LIVE_ID", on_message, on_status=on_status)
    start_listener("YOUR_LIVE_ID", on_message, debug=True)

    # 强制系统 Chrome/Edge（不用 browsers/ 里的 bundled）
    start_listener("YOUR_LIVE_ID", on_message, force_system=True)

依赖:
    pip install playwright protobuf
    playwright install chromium-headless-shell   # 下载到 browsers/ 目录
    先运行 login.py 生成 state.json（可选；没有也可连接，仅日志提醒）
    或设 config.json use_system_browser=true 跳过 bundled

浏览器策略:
    默认优先使用 browsers/ 下的 bundled Chromium（headless shell）；
    force_system=True 或 config.json 中 use_system_browser=true 时改用系统 Chrome/Edge。
    系统浏览器冷启动较慢，进房状态等待窗口自动放宽到 _ENTER_TIMEOUT_SYSTEM。
    开播判定以 webcast/room/web/enter 的 status 为准（2=开播，4=未开播/结束）。
"""

import asyncio
import os
import sys
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.async_api import async_playwright

from util.playwright_bootstrap import launch_chromium
from listener.LiveProtobuf import try_parse_frame
from util.log_util import get_listener_logger, make_msg_logger, on_connect_success
from util.models import CONTROL_STATUS_FINISH, ControlMessage, LiveMessage, is_living_enter_status
from util.room_enter import describe_enter_status, is_room_enter_url, parse_room_enter_payload

logger = get_listener_logger(2)

_ENTER_TIMEOUT = 15.0       # 进入直播间后等待 web/enter 状态的秒数
_ENTER_TIMEOUT_SYSTEM = 20.0  # 系统浏览器冷启动较慢，放宽进房状态窗口

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
    *,
    force_system: bool = False,
    browser_channel: str | None = None,
):
    from util.playwright_bootstrap import close_playwright

    msg_logger = make_msg_logger(live_id) if debug else None
    enter_timeout = _ENTER_TIMEOUT_SYSTEM if force_system else _ENTER_TIMEOUT
    logger.info(f"开始连接，直播间: {live_id}")

    def _emit_status(val: bool):
        if on_status:
            try:
                on_status(val)
            except Exception as e:
                logger.debug(f"状态回调异常: {e}")

    notify_offline = False
    playwright = await async_playwright().start()
    browser = None
    context = None
    try:
        browser = await launch_chromium(
            playwright, headless=headless, force_system=force_system, channel=browser_channel,
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

        async def _fail_unlive(reason: str):
            nonlocal notify_offline
            if _state["live_confirmed"] or _state["stopped"]:
                return
            logger.warning(reason)
            await _shutdown()
            notify_offline = True

        async def _disconnect_clean(reason: str | None = None):
            nonlocal notify_offline
            if _state["stopped"]:
                return
            if reason:
                logger.info(reason)
            notify = _state["live_confirmed"]
            await _shutdown()
            if notify:
                notify_offline = True

        def _confirm_live() -> None:
            if _state["live_confirmed"] or _state["stopped"]:
                return
            _state["live_confirmed"] = True
            nonlocal enter_timer
            if enter_timer and not enter_timer.done():
                enter_timer.cancel()
            on_connect_success("listener2")
            logger.info("✅ 直播间正在直播")
            _emit_status(True)

        async def _on_response(resp):
            if _state["stopped"] or _state["enter_seen"]:
                return
            if not is_room_enter_url(resp.url):
                return
            try:
                body = await resp.text()
            except Exception as e:
                logger.debug(f"读取 enter 响应失败: {e}")
                return
            enter = parse_room_enter_payload(body)
            if enter is None:
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

        def on_websocket(ws):
            if "/push/v2/" not in ws.url:
                return

            def on_frame(raw: bytes):
                _, msgs = try_parse_frame(raw)
                if not _state["live_confirmed"]:
                    return
                for msg in msgs:
                    try:
                        if isinstance(msg, ControlMessage) and msg.status == CONTROL_STATUS_FINISH:
                            if not _state["stopped"]:
                                asyncio.ensure_future(
                                    _disconnect_clean("收到下播控制消息，结束监听")
                                )
                            return
                        if msg_logger:
                            msg_logger.info(msg)
                        callback(msg)
                    except Exception as e:
                        logger.error(f"回调异常: {e}")

            def on_ws_close():
                if _state["stopped"]:
                    return
                if _state["live_confirmed"]:
                    logger.warning("WebSocket 已断开")
                    asyncio.ensure_future(_disconnect_clean())

            ws.on("framereceived", on_frame)
            ws.on("close", on_ws_close)

        def _on_page_close(_):
            if _state["stopped"]:
                return
            asyncio.ensure_future(_disconnect_clean())

        page.on("close", _on_page_close)
        page.on("response", lambda r: asyncio.ensure_future(_on_response(r)))
        page.on("websocket", on_websocket)

        logger.info(f"已进入直播间: {live_id}")
        await page.goto(
            f"https://live.douyin.com/{live_id}",
            wait_until="commit",
        )

        async def _enter_wait_timeout():
            await asyncio.sleep(enter_timeout)
            if not _state["enter_seen"] and not _state["live_confirmed"]:
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

    if notify_offline:
        _emit_status(False)


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
    启动 WSS 拦截监听，阻塞运行。

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