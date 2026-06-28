"""
listener3.py — 系统代理 + MITM 拦截方案

不依赖 Playwright/浏览器自动化，直接在网络层用本地代理拦截 WSS 流量解析。
复用 listener2.py 现成的 protobuf 解析逻辑（_parse_frame），解析口径完全一致，
不重复维护两套解析代码。

设计参考：DouyinBarrageGrab (ape-byte) 的系统代理抓包方案
https://github.com/ape-byte/DouyinBarrageGrab

跟 listener1（JS Hook）/ listener2（Playwright 拦截 WSS 帧）的本质区别：
    listener1/2 自己用 Playwright 启动一个受控浏览器，自动打开直播间页面；
    listener3 不开浏览器，而是起一个本地 MITM 代理服务器，你自己手动把
    浏览器（或其他任何会产生抖音 WSS 流量的程序）的网络代理设置指向这个
    端口，所有经过的 HTTPS/WSS 流量会被解密、按域名过滤、解析。

这意味着 listener3 也能用来抓"抖音直播伴侣"客户端的弹幕（因为直播伴侣
内嵌 Chromium，走的也是标准 HTTPS/WSS，同样能被系统代理拦截），不只是
网页版直播间——但这不是本项目当前的目标场景，提一下只是说明这个方案
的能力边界。

【准备工作】
    pip install mitmproxy
    （Windows 如果 pip 装的时候报 C 扩展编译错误，去 https://mitmproxy.org/
     下载官方安装包装好，它会带一个独立的 Python 环境，不需要再额外配置）

【首次使用：必须先装好 mitmproxy 的根证书，否则 HTTPS 流量解不开】
    1. 先跑一次 listener3.py（哪怕还没配浏览器代理）
    2. 把要监听的浏览器代理设置成 127.0.0.1:<端口>（默认 8888）
    3. 浏览器访问 http://mitm.it ，按提示给这个浏览器安装根证书
       （这是 mitmproxy 自己生成的本机证书，只用来解密你自己电脑上的流量，
       不会被任何外部利用；某些安全软件可能会弹提示，是正常现象）
    4. 证书装完后重启一下浏览器，才能正常解密 HTTPS

【配置浏览器走代理】
    建议用一个独立的浏览器实例/配置文件来跑这个，不要全局改系统代理
    （否则你平时上网的所有流量都会被解密记录，没必要）。
    最简单的办法：装一个 SwitchyOmega 之类的代理切换扩展，只给这一个
    Profile 配置代理指向 127.0.0.1:8888，平时用默认 Profile 不受影响。

【使用方法】
    from listener3 import start_listener

    def on_message(msg):
        print(msg)

    def on_status(connected: bool):
        print("已连接" if connected else "已断开")

    import asyncio
    asyncio.run(start_listener(on_message, on_status=on_status))

    然后用配置好代理的浏览器打开抖音直播间，正常看播放即可。

【已知限制】（系统代理方案的固有限制，跟原版 DouyinBarrageGrab 一致）
    - 只能拦截"握手之后"建立的新 WSS 连接。如果浏览器已经打开直播间在
      播放中途才启动本程序，是抓不到的，必须保证 listener3 先跑起来，
      再去开直播间页面
    - 只有最终送达客户端的消息才能被截获，被抖音服务器过滤掉的内容
      （比如被风控吞掉的弹幕）天然就抓不到，这跟 listener1/2 是一样的限制
    - 默认只处理域名里带 "webcast" 关键字的 WSS 连接，避免把无关流量也
      解密浪费 CPU；如果某天域名规则变了，改 HOST_FILTER_KEYWORDS 常量
"""
import asyncio
import logging
from typing import Callable, Optional

from mitmproxy import http, options
from mitmproxy.tools.dump import DumpMaster

from listener2 import _parse_frame  # 直接复用，解析口径跟 listener2 完全一致

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8888
# 只处理域名里带这些关键字的 WSS 连接，其余流量原样放过、不解析
HOST_FILTER_KEYWORDS = ("webcast",)


class _DouyinWsAddon:
    """
    mitmproxy 的 addon。mitmproxy 会在每次收到一条完整的 WebSocket 消息时
    调用 websocket_message(flow) —— 这个 hook 名字和签名是 mitmproxy 自带的
    dumper 插件本身也在用的标准写法，不是猜的。
    """

    def __init__(self, callback: Callable, on_status: Optional[Callable] = None):
        self.callback = callback
        self.on_status = on_status
        self._seen_first = False

    def websocket_message(self, flow: http.HTTPFlow):
        host = flow.request.host or ""
        if not any(k in host for k in HOST_FILTER_KEYWORDS):
            return

        assert flow.websocket is not None
        message = flow.websocket.messages[-1]
        if message.from_client:
            return  # 只关心服务器推给客户端的消息，忽略客户端发的心跳/请求帧

        if not self._seen_first:
            self._seen_first = True
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


async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
    port: int = DEFAULT_PORT,
):
    """
    启动本地 MITM 代理，监听并解析抖音 WSS 流量。

    跟 listener1/listener2 的 start_listener 不同：这个函数不会自己打开
    浏览器，而是常驻起一个代理服务，等你手动把浏览器代理指过来。
    这个 await 会一直阻塞直到外部取消这个 asyncio task（比如 GUI 那边的
    ListenerThread 调 task.cancel()）。
    """
    opts = options.Options(listen_host="0.0.0.0", listen_port=port)
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    master.addons.add(_DouyinWsAddon(callback, on_status))

    logger.info(f"MITM 代理已启动，监听端口 {port}")
    logger.info("首次使用请用要监听的浏览器访问 http://mitm.it 安装根证书")
    logger.info(f"然后把该浏览器的代理设置为 127.0.0.1:{port}")

    try:
        await master.run()
    except asyncio.CancelledError:
        master.shutdown()
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