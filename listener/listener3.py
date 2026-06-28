"""
listener3.py — OS 级本地进程拦截方案，专门只监听"直播伴侣"客户端

不依赖 Playwright/浏览器自动化，也不需要碰系统代理设置（不会跟你已有的
Clash Verge 之类的代理冲突）。用 mitmproxy 官方自带的 local 模式，在
OS 层面直接按进程名拦截流量，对目标进程完全透明无感。复用 listener2.py
现成的 protobuf 解析逻辑（_parse_frame），解析口径完全一致。

设计参考：
- DouyinBarrageGrab (ape-byte) 的进程过滤思路
  https://github.com/ape-byte/DouyinBarrageGrab
- mitmproxy 官方 local 模式（OS 级拦截）
  https://docs.mitmproxy.org/stable/concepts/modes/
  https://www.mitmproxy.org/posts/local-capture/windows/

跟之前版本的核心区别：
    旧版本走系统代理（system-wide proxy），所有流量都先过 mitmproxy
    再判断是不是目标进程，意味着你的系统代理位置被占用了，没法再用来
    跑 Clash Verge 之类的工具。
    这一版换成 mitmproxy 的 local 模式——它在 OS 层面（Windows 上靠
    WinDivert 这个内核级抓包库）直接按进程名/PID 截获网络包，不需要
    任何应用把代理指向它、不占用系统代理设置、跟你现有的代理工具
    完全不冲突。

【Windows 上的额外依赖（重要）】
    pip install mitmproxy mitmproxy-windows

    mitmproxy-windows 这个包专门提供 Windows 下 local 模式需要的
    WinDivert 转发组件，光装 mitmproxy 本体是不够的。

【权限要求】
    必须以管理员身份运行本脚本——WinDivert 这个底层抓包库本身就要求
    管理员权限，不是 mitmproxy 自己加的限制，权限不够会直接加载失败。

【首次使用：还是要装一次 mitmproxy 的根证书】
    1. 管理员身份运行 listener3.py
    2. 打开"直播伴侣"，正常连接/开播
    3. 第一次连接时如果 HTTPS 解密失败（日志里会有报错），找一个能上网的
       浏览器访问 http://mitm.it 装一次根证书（装完不需要重启全部软件，
       只要重新连一次直播间通常就行）

【使用方法】
    from listener3 import start_listener

    def on_message(msg):
        print(msg)

    def on_status(connected: bool):
        print("已连接" if connected else "已断开")

    import asyncio
    asyncio.run(start_listener(on_message, on_status=on_status))

【已知限制】
    - 只能拦截"握手之后"建立的新 WSS 连接，必须保证 listener3 先跑起来
    - 只有最终送达客户端的消息才能被截获，被抖音服务器过滤掉的内容
      天然抓不到
    - local 模式是 mitmproxy 比较新的功能，不同 Windows 版本/权限环境下
      表现可能有差异，遇到问题先确认：①是不是用管理员身份跑的 ②是不是
      装了 mitmproxy-windows ③进程名关键词对不对（任务管理器核实）
    - 如果 local 模式在你的环境下死活跑不起来，退回旧的系统代理方案
      作为备用：上游代理链式转发（System Proxy -> listener3 -> Clash Verge）
      是验证过可行的，需要的话告诉我，把这部分代码加回来
"""
import asyncio
import logging
import os
import subprocess
from typing import Callable, Optional

from mitmproxy import http, options
from mitmproxy.tools.dump import DumpMaster

try:
    from listener.listener2 import _parse_frame  # 从项目根导入时
except ImportError:
    from listener2 import _parse_frame           # 直接运行 __main__ 时

logger = logging.getLogger(__name__)


def _install_mitmproxy_cert() -> None:
    """将 mitmproxy CA 证书安装到 Windows 系统受信任根证书（需要管理员权限）。
    DumpMaster 初始化后证书才会生成，所以必须在 master 创建后调用。
    """
    cert_path = os.path.expanduser(r"~\.mitmproxy\mitmproxy-ca-cert.cer")
    if not os.path.exists(cert_path):
        logger.warning("mitmproxy CA 证书文件未找到，TLS 解密可能失败")
        return
    result = subprocess.run(
        ["certutil", "-addstore", "-f", "ROOT", cert_path],
        capture_output=True, text=True, errors="ignore"
    )
    if result.returncode == 0:
        logger.info("mitmproxy CA 证书已安装到系统受信任根证书，TLS 解密就绪")
    else:
        logger.debug(f"certutil 返回 {result.returncode}（证书可能已存在，无需重复安装）")


# local 模式的拦截目标，已通过任务管理器实测确认进程名为"直播伴侣.exe"
# （会同时跑多个 PID，是 Electron 应用常见的多进程架构，mitmproxy 的
# local 模式天然按进程名匹配所有同名 PID，不需要额外处理）
TARGET_PROCESS = "直播伴侣.exe"

# 进一步按域名过滤，避免"直播伴侣"自己的其他网络请求（更新检测、统计上报等）
# 也被拿去尝试解析协议，产生不必要的噪音和报错日志
HOST_FILTER_KEYWORDS = ("webcast",)


class _DouyinWsAddon:
    """
    mitmproxy 的 addon。因为这一版用的是 local 模式，到达这里的流量已经
    是 OS 层面筛过的"直播伴侣"专属流量，不需要再像旧版本那样自己用
    psutil 反查进程归属——这一层过滤已经由 mitmproxy 自己在更底层做掉了。
    这里只需要再做域名过滤，剔除"直播伴侣"自身产生的无关请求。
    """

    def __init__(self, callback: Callable, on_status: Optional[Callable],
                 connected: asyncio.Event):
        self.callback = callback
        self.on_status = on_status
        self._connected = connected
        self._seen_first = False

    def request(self, flow: http.HTTPFlow):
        """记录 WebSocket 升级请求，用于诊断是否抓到了正确的域名。"""
        if flow.request.headers.get("upgrade", "").lower() == "websocket":
            logger.info(f"WS 升级请求: {flow.request.host}{flow.request.path[:80]}")

    def websocket_message(self, flow: http.HTTPFlow):
        host = flow.request.host or ""
        if not any(k in host for k in HOST_FILTER_KEYWORDS):
            return

        assert flow.websocket is not None
        message = flow.websocket.messages[-1]
        if message.from_client:
            return  # 只关心服务器推给客户端的消息

        if not self._seen_first:
            self._seen_first = True
            self._connected.set()
            logger.info("✅ 监听到目标 WSS 连接，开始解析消息")
            if self.on_status:
                self.on_status(True)

        try:
            msgs = _parse_frame(message.content)
        except Exception as e:
            logger.debug(f"帧解析失败: {e}")
            return

        for msg in msgs:
            try:
                self.callback(msg)
            except Exception as e:
                logger.error(f"callback 异常: {e}")

    def websocket_end(self, flow: http.HTTPFlow):
        host = flow.request.host or ""
        if not any(k in host for k in HOST_FILTER_KEYWORDS):
            return
        logger.info("WSS 连接已断开")
        if self.on_status:
            self.on_status(False)


_CONNECT_TIMEOUT = 60.0  # 秒，等待首次 WSS 连接的超时时间（需要用户手动触发直播伴侣重连）


async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
    target_process: str = TARGET_PROCESS,
):
    """
    启动 mitmproxy local 模式拦截直播伴侣流量。
    30s 内未检测到 WSS 连接则视为连接失败，on_status(False) 后返回。
    """
    connected = asyncio.Event()
    opts = options.Options(mode=[f"local:{target_process}"])
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    # DumpMaster 初始化后 CA 证书才会生成，此处自动装入 Windows 受信任根证书
    _install_mitmproxy_cert()
    master.addons.add(_DouyinWsAddon(callback, on_status, connected))

    logger.info(f"local 模式已启动，拦截进程: {target_process}")
    logger.info("⚠️  请在直播伴侣中断开并重新连接直播间，触发新的 WSS 握手（60s 超时）")

    master_task = asyncio.create_task(master.run())

    async def _stop_master():
        """关闭 mitmproxy 并等待 WinDivert 完全释放，避免 loop 关闭时报错。"""
        master.shutdown()
        master_task.cancel()
        try:
            await master_task
        except (asyncio.CancelledError, Exception):
            pass

    # 等待首次 WSS 连接，超时视为连接失败
    try:
        await asyncio.wait_for(connected.wait(), timeout=_CONNECT_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"{_CONNECT_TIMEOUT:.0f}s 内未检测到 WSS 连接，连接失败")
        await _stop_master()
        if on_status:
            on_status(False)
        return
    except asyncio.CancelledError:
        await _stop_master()
        raise

    # 已连接，持续运行直到外部停止
    try:
        await master_task
    except asyncio.CancelledError:
        await _stop_master()
        raise
    finally:
        if on_status:
            on_status(False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    def _demo_callback(msg):
        print(f"[收到消息] {msg}")

    def _demo_status(connected: bool):
        print(f"[状态] {'🟢 已连接' if connected else '🔴 已断开'}")

    asyncio.run(start_listener(_demo_callback, on_status=_demo_status))