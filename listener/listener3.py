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
from listener.log_util import get_logger, on_connect_success

logger = get_logger(__name__)

_page_proxy_snapshot: Optional[bool] = None
_shutdown_fn: Optional[Callable[[], Awaitable[None]]] = None


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


TARGET_PROCESS = "直播伴侣.exe"
HOST_FILTER_KEYWORDS = ("webcast",)


class _DouyinWsAddon:
    def __init__(self, callback: Callable, on_status: Optional[Callable], connected: asyncio.Event):
        self.callback = callback
        self.on_status = on_status
        self._connected = connected
        self._live_confirmed = False

    def request(self, flow: http.HTTPFlow):
        if flow.request.headers.get("upgrade", "").lower() == "websocket":
            logger.info(f"WS 升级请求: {flow.request.host}{flow.request.path[:80]}")

    def websocket_message(self, flow: http.HTTPFlow):
        host = flow.request.host or ""
        if not any(k in host for k in HOST_FILTER_KEYWORDS):
            return

        assert flow.websocket is not None
        message = flow.websocket.messages[-1]
        if message.from_client:
            return

        try:
            msgs = parse_frame(message.content)
        except Exception as e:
            logger.debug(f"帧解析失败: {e}")
            return

        if msgs and not self._live_confirmed:
            self._live_confirmed = True
            self._connected.set()
            on_connect_success("listener3")
            logger.info("收到初始直播消息，连接成功")
            if self.on_status:
                self.on_status(True)

        for msg in msgs:
            try:
                self.callback(msg)
            except Exception as e:
                logger.error(f"callback 异常: {e}")

    def websocket_end(self, flow: http.HTTPFlow):
        host = flow.request.host or ""
        if not any(k in host for k in HOST_FILTER_KEYWORDS):
            return
        if not self._live_confirmed:
            return
        logger.info("WSS 连接已断开")
        self._live_confirmed = False
        if self.on_status:
            self.on_status(False)


async def shutdown() -> None:
    """彻底关闭 mitmproxy / WinDivert（切换线路或取消连接时调用）。"""
    global _shutdown_fn
    fn = _shutdown_fn
    _shutdown_fn = None
    if fn:
        await fn()


async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
    target_process: str = TARGET_PROCESS,
):
    global _shutdown_fn
    connected = asyncio.Event()
    opts = options.Options(mode=[f"local:{target_process}"])
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    _install_mitmproxy_cert()
    master.addons.add(_DouyinWsAddon(callback, on_status, connected))

    logger.info(f"local 模式已启动，拦截进程: {target_process}")
    logger.info("请在直播伴侣中开播，等待 WSS 连接及初始消息…")

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

    _shutdown_fn = _stop_master

    try:
        wait_connected = asyncio.create_task(connected.wait())
        done, pending = await asyncio.wait(
            {wait_connected, master_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

        if wait_connected in done and connected.is_set():
            await master_task
        else:
            await _stop_master()
            return
    except asyncio.CancelledError:
        await _stop_master()
        raise
    finally:
        _shutdown_fn = None
        if on_status:
            on_status(False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    def _demo_callback(msg):
        print(f"[收到消息] {msg}")

    def _demo_status(connected: bool):
        print(f"[状态] {'已连接' if connected else '已断开'}")

    asyncio.run(start_listener(_demo_callback, on_status=_demo_status))
