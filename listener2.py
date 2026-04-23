import asyncio
import gzip
import logging
from datetime import datetime
from playwright.async_api import async_playwright

import Live_pb2


# ================== 日志 ==================
log_filename = f"douyin_live_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

logger.info("=== 抖音直播监听器启动 ===")
logger.info(f"日志文件：{log_filename}")


# ================== 核心 ==================
async def main():
    async with async_playwright() as p:

        # 使用登录态
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(storage_state="state.json")

        page = await context.new_page()

        # 防止重复 WS
        seen_ws = set()

        async def handle_frame(payload: bytes):
            try:
                # ==== 解析 PushFrame ====
                frame = Live_pb2.PushFrame()
                frame.ParseFromString(payload)

                if not frame.payload:
                    return

                # ==== gzip 解压 ====
                origin_bytes = gzip.decompress(frame.payload)

                # ==== 解析 LiveResponse ====
                response = Live_pb2.LiveResponse()
                response.ParseFromString(origin_bytes)

                for item in response.messagesList:
                    method = item.method

                    # ================== 礼物 ==================
                    if method == "WebcastGiftMessage":
                        msg = Live_pb2.GiftMessage()
                        msg.ParseFromString(item.payload)

                        repeatend = getattr(msg, "repeatEnd", None)

                        # 只输出连击结束帧
                        if repeatend is not None and int(repeatend) != 1:
                            continue

                        count = (
                            getattr(msg, "comboCount", 0)
                            or getattr(msg, "repeatCount", 0)
                            or 1
                        )

                        data = {
                            "type": "gift",
                            "user": msg.user.nickname,
                            "gift": msg.gift.name,
                            "count": count,
                            "repeatend": int(repeatend) if repeatend is not None else 1
                        }

                        print(data)

                    # ================== 弹幕 ==================
                    elif method == "WebcastChatMessage":
                        msg = Live_pb2.ChatMessage()
                        msg.ParseFromString(item.payload)

                        data = {
                            "type": "chat",
                            "user": msg.user.nickname,
                            "content": msg.content
                        }

                        print(data)

                    # ================== 进房 ==================
                    elif method == "WebcastMemberMessage":
                        msg = Live_pb2.MemberMessage()
                        msg.ParseFromString(item.payload)

                        data = {
                            "type": "enter",
                            "user": msg.user.nickname
                        }

                        print(data)

                    # ❌ 点赞、关注全部删除

            except Exception as e:
                logger.error(f"解析异常: {e}")

        # ================== WS监听 ==================
        def on_websocket(ws):
            if "/push/v2/" in ws.url:
                if ws.url in seen_ws:
                    return
                seen_ws.add(ws.url)

                logger.info("🚀 捕获 WebSocket")
                logger.info(f"WS: {ws.url}")

                ws.on(
                    "framereceived",
                    lambda payload: asyncio.create_task(handle_frame(payload))
                )

        page.on("websocket", on_websocket)

        # ================== 启动 ==================
        url = "https://live.douyin.com/YOUR_LIVE_ID"  # 👉 你自己改直播间
        logger.info(f"打开直播间：{url}")

        await page.goto(url)
        await page.wait_for_timeout(10000)

        logger.info("✅ 监听启动成功")

        await asyncio.Event().wait()


# ================== 入口 ==================
asyncio.run(main())