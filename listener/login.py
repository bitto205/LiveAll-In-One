"""
login.py — 登录管理

用法:
    from login import do_login
    do_login()          # 两层检测，按需登录

    python login.py     # 直接运行，同上
"""

import asyncio
import json
import logging
import os
import time

from playwright.async_api import async_playwright

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
STATE_FILE = "state.json"

logger = logging.getLogger(__name__)


def get_login_ui_state(state_file: str = STATE_FILE) -> tuple[str, bool]:
    """线路 1/2 登录态 UI：(状态文案, 登录按钮是否可点)。"""
    if not os.path.exists(state_file):
        return "❌ 未登录", True
    ok, detail = _check_cookie_expiry(state_file)
    if not ok:
        if "过期" in detail:
            return "⚠️ 登录已过期", True
        if "未找到" in detail:
            return "⚠️ 未找到登录凭证", True
        return f"❌ {detail}", True
    if "无过期" in detail:
        return "✅ 已登录", False
    if "还剩" in detail:
        days = detail.split("还剩约")[-1].split("天")[0].strip()
        return f"✅ 已登录，还剩约 {days} 天", False
    return "✅ 已登录", False


def _check_cookie_expiry(state_file: str) -> tuple[bool, str]:
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        return False, f"读取失败: {e}"

    now = time.time()

    for c in state.get("cookies", []):
        if c.get("name") != "sessionid":
            continue

        if not c.get("value", ""):
            return False, "sessionid 值为空"

        expires = c.get("expires", -1)

        if expires == -1:
            return True, "sessionid 存在（无过期时间）"

        remaining = expires - now
        if remaining <= 0:
            return False, "sessionid 已过期"

        days = int(remaining / 86400)
        return True, f"sessionid 有效，还剩约 {days} 天"

    return False, "未找到 sessionid"


# ─────────────────────────────────────────────
# 注入到页面的"已完成登录"确认按钮
# ─────────────────────────────────────────────
_CONFIRM_JS = """
(() => {
    const inject = () => {
        if (document.getElementById('__dy_login_btn__')) return;

        const btn = document.createElement('div');
        btn.id = '__dy_login_btn__';
        btn.style.cssText = `
            position: fixed;
            bottom: 30px;
            right: 30px;
            z-index: 999999;
            background: #fe2c55;
            color: #fff;
            font-size: 16px;
            font-weight: bold;
            padding: 14px 28px;
            border-radius: 8px;
            cursor: pointer;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            user-select: none;
        `;
        btn.innerText = '✅  我已完成登录';
        btn.onclick = () => {
            window.__LOGIN_DONE__ = true;
            btn.innerText = '⏳ 保存中...';
            btn.style.background = '#888';
        };
        document.body.appendChild(btn);
    };

    // 每次导航后重新注入（add_init_script 在 DOM 就绪前执行，需要等 DOMContentLoaded）
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', inject);
    } else {
        inject();
    }
})();
"""


# ─────────────────────────────────────────────
# 第二层：弹出浏览器扫码
# ─────────────────────────────────────────────
async def _run_login(state_file: str) -> bool:
    logger.info("启动登录流程，请在浏览器中扫码，完成后点击页面右下角按钮...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await context.new_page()
        await page.add_init_script(_CONFIRM_JS)
        await page.goto("https://www.douyin.com/", wait_until="domcontentloaded")

        while True:
            logger.info("等待手动确认（点击页面右下角按钮）...")

            # 等用户点按钮，浏览器关闭时会抛异常
            try:
                await page.wait_for_function(
                    "() => window.__LOGIN_DONE__ === true",
                    timeout=0,
                )
            except Exception:
                logger.warning("浏览器已关闭，登录取消")
                return False

            await asyncio.sleep(1)
            await context.storage_state(path=state_file)

            valid, reason = _check_cookie_expiry(state_file)

            if valid:
                logger.info(f"✅ 登录成功，已保存 {state_file}（{reason}）")
                await browser.close()
                return True

            # 未检测到登录态 → 页面注入红色提示，恢复按钮，继续等待
            logger.warning(f"⚠️  未检测到登录态（{reason}），提示用户重试")
            await page.evaluate("""
                (() => {
                    // 恢复按钮
                    const btn = document.getElementById('__dy_login_btn__');
                    if (btn) {
                        btn.innerText = '✅  我已完成登录';
                        btn.style.background = '#fe2c55';
                    }
                    window.__LOGIN_DONE__ = false;

                    // 注入红色提示（3 秒后自动消失）
                    const old = document.getElementById('__dy_login_warn__');
                    if (old) old.remove();
                    const warn = document.createElement('div');
                    warn.id = '__dy_login_warn__';
                    warn.style.cssText = `
                        position: fixed;
                        bottom: 90px;
                        right: 30px;
                        z-index: 999999;
                        background: #d32f2f;
                        color: #fff;
                        font-size: 14px;
                        padding: 10px 18px;
                        border-radius: 6px;
                        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
                    `;
                    warn.innerText = '❌  未检测到登录态，请确认是否已登录';
                    document.body.appendChild(warn);
                    setTimeout(() => warn.remove(), 3000);
                })();
            """)
            # 继续下一轮循环，等待再次点击按钮

    return False   # 理论上不会执行到这里，兜底


# ─────────────────────────────────────────────
# 公开接口
# ─────────────────────────────────────────────
def do_login(state_file: str = STATE_FILE) -> bool:
    """
    两层检测，按需登录。
    返回 True = 登录态有效，False = 登录失败。
    """
    import os
    from datetime import datetime
    from listener.log_util import on_connect_success

    if not os.path.exists(state_file):
        logger.info(f"{state_file} 不存在，需要登录")
    else:
        valid, reason = _check_cookie_expiry(state_file)
        if valid:
            on_connect_success("login")
            logger.info(f"✅ 登录有效：{reason}")
            return True
        logger.warning(f"⚠️  登录失效：{reason}，重新登录")

    ok = asyncio.run(_run_login(state_file))
    if ok:
        on_connect_success("login")
    return ok


# ─────────────────────────────────────────────
# 直接运行
# ─────────────────────────────────────────────
if __name__ == "__main__":
    ok = do_login()
    logger.info("🎉 就绪" if ok else "❌ 登录失败")