"""线路 3：mitmproxy local 模式拦截直播伴侣 WSS。"""
import asyncio
import logging
import os
import subprocess
import winreg
from typing import Awaitable, Callable, Optional

from mitmproxy import http, options
from mitmproxy.tools.dump import DumpMaster

from listener.LiveProtobuf import parse_frame
from listener.log_util import get_logger, on_connect_success, ensure_console_logging
from listener.models import ControlMessage

logger = get_logger(__name__)

_page_proxy_snapshot: Optional[bool] = None
_shutdown_fn: Optional[Callable[[], Awaitable[None]]] = None
_stop_event: Optional[asyncio.Event] = None

_CONNECT_TIMEOUT = 60.0


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
    proxy = check_system_proxy()
    _page_proxy_snapshot = proxy["enabled"]
    status = get_page_status()
    logger.info(
        "[线路3 页面检测] 伴侣目录=%s | index.js=%s | 已改写=%s | 系统代理=%s | server=%s",
        status["companion_installed"],
        status["index_js_found"],
        status["index_modified"],
        status["system_proxy"],
        proxy.get("server", ""),
    )
    return status


def get_page_status() -> dict:
    from listener.listener4 import get_companion_path_fields

    proxy = _page_proxy_snapshot if _page_proxy_snapshot is not None else False
    index_mod = _is_companion_index_modified()
    return {
        **get_companion_path_fields(),
        "index_modified": index_mod,
        "system_proxy": proxy,
    }


def _install_mitmproxy_cert() -> None:
    cert_path = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.cer")
    if not os.path.exists(cert_path):
        logger.warning("mitmproxy CA 证书文件未找到，TLS 解密可能失败")
        return
    result = subprocess.run(
        ["certutil", "-addstore", "-f", "ROOT", cert_path],
        capture_output=True, text=True, errors="ignore",
    )
    if result.returncode == 0:
        logger.info("mitmproxy CA 证书已安装到系统受信任根证书，TLS 解密就绪")
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
            logger.info("[线路3] LocalRedirector 已关闭")
        except Exception as e:
            logger.debug(f"[线路3] LocalRedirector close: {e}")
    except Exception as e:
        logger.debug(f"[线路3] teardown redirector: {e}")


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
        session_lost: asyncio.Event,
        loop: asyncio.AbstractEventLoop,
    ):
        self.callback = callback
        self.on_status = on_status
        self._connected = connected
        self._session_lost = session_lost
        self._loop = loop
        self._seen_first = False
        self._session_active = False

    def _end_session(self, reason: str) -> None:
        if not self._session_active:
            return
        self._session_active = False
        self._seen_first = False
        logger.info(reason)
        if self.on_status:
            self.on_status(False)
        self._loop.call_soon_threadsafe(self._session_lost.set)

    def request(self, flow: http.HTTPFlow):
        if flow.request.headers.get("upgrade", "").lower() == "websocket":
            if _is_webcast_flow(flow):
                logger.info(f"WS 升级请求: {flow.request.host}{flow.request.path[:80]}")

    def websocket_message(self, flow: http.HTTPFlow):
        if not _is_webcast_flow(flow):
            return

        assert flow.websocket is not None
        message = flow.websocket.messages[-1]
        if message.from_client:
            return

        if not self._seen_first:
            self._seen_first = True
            self._session_active = True
            self._connected.set()
            on_connect_success("listener3")
            logger.info("监听到目标 WSS 连接，开始解析消息")
            if self.on_status:
                self.on_status(True)

        try:
            msgs = parse_frame(message.content)
        except Exception as e:
            logger.debug(f"帧解析失败: {e}")
            return

        for msg in msgs:
            if isinstance(msg, ControlMessage) and msg.status == 3:
                self._end_session("收到下播控制消息，结束监听")
                return
            try:
                self.callback(msg)
            except Exception as e:
                logger.error(f"callback 异常: {e}")

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


async def _wait_connect_or_stop(connected: asyncio.Event) -> None:
    assert _stop_event is not None
    conn_task = asyncio.create_task(connected.wait())
    stop_task = asyncio.create_task(_stop_event.wait())
    done, pending = await asyncio.wait(
        {conn_task, stop_task},
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
    if not connected.is_set():
        raise RuntimeError("connect wait ended without connection")


async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
    target_process: str = TARGET_PROCESS,
):
    ensure_console_logging()
    global _shutdown_fn, _stop_event
    _stop_event = asyncio.Event()
    connected = asyncio.Event()
    session_lost = asyncio.Event()
    loop = asyncio.get_running_loop()

    logger.info("[线路3] 正在启动 mitmproxy local 模式…")
    try:
        opts = options.Options(mode=[f"local:{target_process}"])
        master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    except Exception as e:
        logger.error(f"[线路3] mitmproxy 启动失败: {e}", exc_info=True)
        if on_status:
            on_status(False)
        return

    _install_mitmproxy_cert()
    master.addons.add(_DouyinWsAddon(callback, on_status, connected, session_lost, loop))

    logger.info(f"local 模式已启动，拦截进程: {target_process}")
    logger.info(
        f"请在直播伴侣中断开并重新连接直播间，触发新的 WSS 握手（{_CONNECT_TIMEOUT:.0f}s 超时）"
    )

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
        await asyncio.wait_for(_wait_connect_or_stop(connected), timeout=_CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"[线路3] {_CONNECT_TIMEOUT:.0f}s 内未检测到 WSS 连接，连接失败")
        await _stop_master()
        if on_status:
            on_status(False)
        return
    except asyncio.CancelledError:
        await _stop_master()
        return

    logger.info("[线路3] WSS 已连接，持续监听…")
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
