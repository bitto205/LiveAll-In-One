"""线路 3：mitmproxy local 模式拦截直播伴侣 WSS。"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import winreg
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# mitmproxy 体积庞大，改为按需加载（精简打包可整体排除）。
# 仅在真正启动线路 3 时导入；类型注解借助 __future__ annotations 延迟求值。
if TYPE_CHECKING:
    from mitmproxy import http

from listener.LiveProtobuf import try_parse_frame
from util.log_util import get_listener_logger, on_connect_success, ensure_console_logging
from util.models import CONTROL_STATUS_FINISH, ControlMessage, is_living_enter_status
from util.room_enter import describe_enter_status, is_room_enter_url, parse_room_enter_payload

logger = get_listener_logger(3)

_page_proxy_snapshot: Optional[bool] = None
_shutdown_fn: Optional[Callable[[], Awaitable[None]]] = None
_stop_event: Optional[asyncio.Event] = None
_ENTER_TIMEOUT = 15.0  # WebSocket 建立后等待进房状态（room/enter）的秒数


def check_system_proxy() -> dict:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except FileNotFoundError:
                server = ""
        return {"enabled": bool(enable), "server": server or ""}
    except Exception as e:
        logger.debug(f"读取系统代理失败: {e}")
        return {"enabled": False, "server": ""}


def _is_companion_index_modified() -> bool:
    from listener.listener4 import find_index_js, is_index_js_modified
    if not find_index_js():
        return False
    return is_index_js_modified()


def run_page_check() -> dict:
    global _page_proxy_snapshot
    from util.status_cache import invalidate_all
    invalidate_all()
    proxy = check_system_proxy()
    _page_proxy_snapshot = proxy["enabled"]
    status = get_page_status(force=True)
    logger.info(
        "页面检测 | 伴侣目录=%s | index.js=%s | 已改写=%s | 系统代理=%s | 代理地址=%s",
        status["companion_installed"],
        status["index_js_found"],
        status["index_modified"],
        status["system_proxy"],
        proxy.get("server", ""),
    )
    return status


def get_page_status(*, force: bool = False) -> dict:
    from util.status_cache import get_route3_status, get_proxy_enabled

    def _build() -> dict:
        from listener.listener4 import get_companion_path_fields
        proxy = (
            _page_proxy_snapshot
            if _page_proxy_snapshot is not None
            else get_proxy_enabled(check_system_proxy)
        )
        return {
            **get_companion_path_fields(),
            "index_modified": _is_companion_index_modified(),
            "system_proxy": proxy,
        }

    return get_route3_status(_build, force=force)


def _install_mitmproxy_cert() -> None:
    cert_path = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.cer")
    if not os.path.exists(cert_path):
        logger.warning("代理拦截 CA 证书文件未找到，传输层解密可能失败")
        return
    result = subprocess.run(
        ["certutil", "-addstore", "-f", "ROOT", cert_path],
        capture_output=True, text=True, errors="ignore",
    )
    if result.returncode == 0:
        logger.info("代理拦截 CA 证书已安装到系统受信任根证书，传输层解密就绪")
    else:
        logger.debug(f"certutil 返回 {result.returncode}（证书可能已存在）")


async def _teardown_local_redirector() -> None:
    """关闭 mitmproxy 进程级 LocalRedirector，避免 loop 关闭后仍回调已死 loop。"""
    try:
        from mitmproxy.proxy.mode_servers import LocalRedirectorInstance

        cls = LocalRedirectorInstance
        cls._instance = None
        server = cls._server
        if server is None:
            return
        cls._server = None
        try:
            server.set_intercept("")
        except Exception:
            pass
        try:
            server.close()
            await server.wait_closed()
            logger.info("本地重定向已关闭")
        except Exception as e:
            logger.debug(f"关闭本地重定向: {e}")
    except Exception as e:
        logger.debug(f"清理本地重定向: {e}")


TARGET_PROCESS = "直播伴侣.exe"
HOST_FILTER_KEYWORDS = ("webcast",)


def _is_webcast_flow(flow: http.HTTPFlow) -> bool:
    """host 可能是 IP，webcast 常在 path 里（如 /webcast/im/push/）。"""
    blob = f"{flow.request.host or ''}{flow.request.path or ''}".lower()
    return any(k in blob for k in HOST_FILTER_KEYWORDS)


class _DouyinWsAddon:
    def __init__(
        self,
        callback: Callable,
        on_status: Optional[Callable],
        connected: asyncio.Event,
        ws_ready: asyncio.Event,
        session_lost: asyncio.Event,
        loop: asyncio.AbstractEventLoop,
    ):
        self.callback = callback
        self.on_status = on_status
        self._connected = connected
        self._ws_ready = ws_ready
        self._session_lost = session_lost
        self._loop = loop
        self._ws_seen = False
        self._live_confirmed = False
        self._enter_seen = False
        self._session_active = False

    def _end_session(self, reason: str) -> None:
        if not self._session_active:
            return
        self._session_active = False
        self._ws_seen = False
        self._live_confirmed = False
        self._enter_seen = False
        logger.info(reason)
        if self.on_status:
            self.on_status(False)
        self._loop.call_soon_threadsafe(self._session_lost.set)

    def _confirm_live(self) -> None:
        if self._live_confirmed:
            return
        self._live_confirmed = True
        self._session_active = True
        self._connected.set()
        on_connect_success("listener3")
        logger.info("✅ 直播间正在直播")
        if self.on_status:
            self.on_status(True)

    def _handle_enter_payload(self, body: bytes | str) -> None:
        if self._enter_seen:
            return
        enter = parse_room_enter_payload(body)
        if enter is None:
            return
        self._enter_seen = True
        logger.info(
            "进房状态 enter.status=%s (%s) room_status=%s",
            enter.status,
            describe_enter_status(enter.status),
            enter.room_status,
        )
        try:
            self.callback(enter)
        except Exception as e:
            logger.debug(f"enter 回调异常: {e}")
        if is_living_enter_status(enter.status):
            self._confirm_live()
        else:
            logger.warning(
                "直播间未开播（enter.status=%s，%s）",
                enter.status,
                describe_enter_status(enter.status),
            )
            if self.on_status:
                self.on_status(False)
            self._loop.call_soon_threadsafe(self._session_lost.set)

    def request(self, flow: http.HTTPFlow):
        if flow.request.headers.get("upgrade", "").lower() == "websocket":
            if _is_webcast_flow(flow):
                logger.debug(f"WS 升级请求: {flow.request.host}{flow.request.path[:80]}")

    def response(self, flow: http.HTTPFlow):
        url = flow.request.pretty_url
        if not is_room_enter_url(url):
            return
        try:
            body = flow.response.content if flow.response else b""
        except Exception as e:
            logger.debug(f"读取 enter 响应失败: {e}")
            return
        self._handle_enter_payload(body)

    def websocket_start(self, flow: http.HTTPFlow):
        if not _is_webcast_flow(flow):
            return
        if not self._ws_seen:
            self._ws_seen = True
            self._ws_ready.set()
            logger.info("WebSocket 已建立，等待进房状态确认…")

    def websocket_message(self, flow: http.HTTPFlow):
        if not _is_webcast_flow(flow):
            return

        assert flow.websocket is not None
        message = flow.websocket.messages[-1]
        if message.from_client:
            return

        _, msgs = try_parse_frame(message.content)
        if not self._live_confirmed:
            return

        for msg in msgs:
            if isinstance(msg, ControlMessage) and msg.status == CONTROL_STATUS_FINISH:
                self._end_session("收到下播控制消息，结束监听")
                return
            try:
                self.callback(msg)
            except Exception as e:
                logger.error(f"回调异常: {e}")

    def websocket_end(self, flow: http.HTTPFlow):
        if not _is_webcast_flow(flow):
            return
        if self._session_active:
            self._end_session("WSS 连接已断开，结束监听")


async def shutdown() -> None:
    """停止 mitmproxy（切换线路 / 取消连接时调用）。"""
    global _shutdown_fn, _stop_event
    if _stop_event and not _stop_event.is_set():
        _stop_event.set()
    fn = _shutdown_fn
    _shutdown_fn = None
    if fn:
        await fn()
    else:
        await _teardown_local_redirector()


async def _wait_enter_outcome(
    connected: asyncio.Event,
    session_lost: asyncio.Event,
) -> None:
    """等待进房确认成功 / 明确失败 / 用户停止。"""
    assert _stop_event is not None
    conn_task = asyncio.create_task(connected.wait())
    lost_task = asyncio.create_task(session_lost.wait())
    stop_task = asyncio.create_task(_stop_event.wait())
    done, pending = await asyncio.wait(
        {conn_task, lost_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    if stop_task in done:
        raise asyncio.CancelledError("listener3 stopped")


async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
    target_process: str = TARGET_PROCESS,
):
    ensure_console_logging()
    global _shutdown_fn, _stop_event
    _stop_event = asyncio.Event()
    connected = asyncio.Event()
    ws_ready = asyncio.Event()
    session_lost = asyncio.Event()
    loop = asyncio.get_running_loop()

    logger.info("开始连接")
    logger.info("正在启动代理拦截本地模式…")
    try:
        from mitmproxy import options
        from mitmproxy.tools.dump import DumpMaster
    except ImportError as e:
        logger.error("该版本未内置线路 3 所需的 mitmproxy 组件：%s", e)
        if on_status:
            on_status(False)
        return
    try:
        opts = options.Options(mode=[f"local:{target_process}"])
        master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    except Exception as e:
        logger.error(f"代理拦截启动失败: {e}", exc_info=True)
        if on_status:
            on_status(False)
        return

    _install_mitmproxy_cert()
    master.addons.add(_DouyinWsAddon(callback, on_status, connected, ws_ready, session_lost, loop))

    logger.info(f"本地拦截模式已启动，拦截进程: {target_process}")
    logger.info("请在直播伴侣中断开并重新连接直播间，触发新的 WSS 握手")

    master_task = asyncio.create_task(master.run())

    async def _stop_master():
        try:
            master.shutdown()
        except Exception:
            pass
        master_task.cancel()
        try:
            await master_task
        except (asyncio.CancelledError, Exception):
            pass
        await _teardown_local_redirector()

    _shutdown_fn = _stop_master

    try:
        logger.info(f"{_ENTER_TIMEOUT:.0f}s 内等待进房开播状态（room/enter）…")
        try:
            await asyncio.wait_for(
                _wait_enter_outcome(connected, session_lost),
                timeout=_ENTER_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning(f"{_ENTER_TIMEOUT:.0f}s 内未收到进房开播状态，连接失败")
            await _stop_master()
            if on_status:
                on_status(False)
            return
        if not connected.is_set():
            logger.warning("进房状态显示未开播，连接失败")
            await _stop_master()
            return
    except asyncio.CancelledError:
        await _stop_master()
        return

    logger.info("已确认开播，持续监听…")
    session_task = asyncio.create_task(session_lost.wait())
    try:
        done, pending = await asyncio.wait(
            {master_task, session_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
    except asyncio.CancelledError:
        await _stop_master()
        raise
    else:
        await _stop_master()
        return
    finally:
        _shutdown_fn = None
        _stop_event = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    def _demo_callback(msg):
        print(f"[收到消息] {msg}")

    def _demo_status(connected: bool):
        print(f"[状态] {'已连接' if connected else '已断开'}")

    asyncio.run(start_listener(_demo_callback, on_status=_demo_status))
