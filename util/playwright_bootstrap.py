"""Playwright 浏览器：检测 bundled / 回退系统 Edge·Chrome。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from util.log_util import get_tagged_logger

logger = get_tagged_logger("浏览器", __name__)

ENV_USE_SYSTEM = "LIVEAIO_USE_SYSTEM_BROWSER"
ENV_BUNDLED_EXE = "LIVEAIO_BUNDLED_CHROME_EXE"
_BASE_ARGS = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]

# Chrome headless 部分环境需 CLI 显式 new 模式
_CHROME_HEADLESS_ARGS = ("--headless=new",)

_WIN_CANDIDATES = (
    ("chromium_headless_shell-*", "chrome-headless-shell-win64", "chrome-headless-shell.exe"),
    ("chromium_headless_shell-*", "chrome-headless-shell-win32", "chrome-headless-shell.exe"),
)


from util.paths import app_root


def _app_root() -> Path:
    return app_root()


def _find_bundled_exe(browsers_dir: Path) -> Path | None:
    if not browsers_dir.is_dir():
        return None
    for glob_pat, sub, exe in _WIN_CANDIDATES:
        for d in browsers_dir.glob(glob_pat):
            path = d / sub / exe
            if path.is_file():
                return path
    return None


def use_system_browser() -> bool:
    return os.environ.get(ENV_USE_SYSTEM) == "1"


def mark_system_browser() -> None:
    os.environ[ENV_USE_SYSTEM] = "1"
    os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    os.environ.pop(ENV_BUNDLED_EXE, None)


def bundled_chrome_exe(browsers_dir: Path | None = None) -> Path | None:
    """定位 bundled headless shell；不依赖 Playwright 驱动内置的 revision 号。"""
    cached = os.environ.get(ENV_BUNDLED_EXE)
    if cached and Path(cached).is_file():
        return Path(cached)
    root = Path(browsers_dir) if browsers_dir else _app_root() / "browsers"
    exe = _find_bundled_exe(root)
    if exe is not None:
        os.environ[ENV_BUNDLED_EXE] = str(exe)
    return exe


def prefer_system_browser() -> bool:
    """读取用户偏好：config.json 的 use_system_browser。默认 False（优先 bundled）。"""
    try:
        import config as _cfg
        return bool(_cfg.get("use_system_browser", False))
    except Exception:
        return False


def configure_playwright_browsers(
    app_root: str | Path | None = None,
    *,
    prefer_system: bool | None = None,
) -> bool:
    """配置 Playwright 浏览器来源。返回 True=使用项目 browsers/，False=系统 Chrome/Edge。

    prefer_system=None 时读取 config.json（use_system_browser）；
    为 True 时即使存在 browsers/ 也强制使用系统浏览器。
    """
    if prefer_system is None:
        prefer_system = prefer_system_browser()
    if prefer_system:
        mark_system_browser()
        return False
    root = Path(app_root) if app_root else _app_root()
    browsers = root / "browsers"
    exe = bundled_chrome_exe(browsers)
    if exe is not None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
        os.environ[ENV_USE_SYSTEM] = "0"
        return True
    mark_system_browser()
    return False


def ensure_configured() -> bool:
    """未配置时按项目根目录自动检测（login / listener 独立运行时）。"""
    if ENV_USE_SYSTEM in os.environ:
        return not use_system_browser()
    return configure_playwright_browsers()


def _system_launch_opts(headless: bool) -> tuple[bool, list[str]]:
    """系统 Chrome/Edge 启动参数。返回 (playwright headless 标志, args)。"""
    args = list(_BASE_ARGS)
    if not headless:
        return False, args
    args.extend(_CHROME_HEADLESS_ARGS)
    return True, args


def _system_channels(channel: str | None) -> tuple[str, ...]:
    """选择系统浏览器通道。默认 chrome → msedge 回退。"""
    if channel == "chrome":
        return ("chrome",)
    if channel == "msedge":
        return ("msedge",)
    return ("chrome", "msedge")


async def launch_chromium(
    p,
    *,
    headless: bool = True,
    force_system: bool = False,
    channel: str | None = None,
):
    """启动 Chromium：优先 bundled；目录缺失或启动失败则回退 chrome → msedge。

    force_system=True：始终使用系统浏览器（登录扫码等场景）。
    channel：指定系统浏览器通道（"chrome" / "msedge"），跳过 bundled 且只试该通道。
    """
    ensure_configured()

    if channel in ("chrome", "msedge"):
        force_system = True

    if not force_system and not use_system_browser():
        exe = bundled_chrome_exe()
        if exe is None:
            logger.warning("未找到 bundled headless shell，回退系统浏览器")
            mark_system_browser()
        else:
            try:
                return await p.chromium.launch(
                    headless=headless,
                    executable_path=str(exe),
                    args=_BASE_ARGS,
                )
            except Exception as e:
                logger.warning("bundled 浏览器启动失败，回退系统浏览器: %s", e)
                mark_system_browser()

    channels = _system_channels(channel)
    launch_headless, args = _system_launch_opts(headless)
    last_error: Exception | None = None
    for ch in channels:
        try:
            browser = await p.chromium.launch(
                channel=ch,
                headless=launch_headless,
                args=args,
            )
            name = "Chrome" if ch == "chrome" else "Edge"
            logger.info("已启动系统浏览器: %s (headless=%s)", name, headless)
            return browser
        except Exception as e:
            last_error = e
            logger.debug("系统浏览器 %s 启动失败: %s", ch, e)

    raise RuntimeError("未能启动系统 Chrome/Edge，请确认已安装") from last_error


async def close_playwright(
    playwright,
    *,
    browser=None,
    context=None,
    drain_seconds: float = 0.15,
    stop_timeout: float = 5.0,
) -> None:
    """按顺序关闭 context/browser/driver，并短暂 drain（Windows Proactor 管道清理）。"""
    import asyncio

    if context is not None:
        try:
            await asyncio.wait_for(context.close(), timeout=3.0)
        except Exception:
            pass
    if browser is not None:
        try:
            await asyncio.wait_for(browser.close(), timeout=3.0)
        except Exception:
            pass
    if playwright is not None:
        try:
            await asyncio.wait_for(playwright.stop(), timeout=stop_timeout)
        except Exception:
            pass
    if drain_seconds > 0:
        try:
            await asyncio.sleep(drain_seconds)
        except Exception:
            pass


def log_browser_mode() -> None:
    ensure_configured()
    if use_system_browser():
        reason = "用户偏好" if prefer_system_browser() else "未找到项目 browsers/"
        logger.info("Playwright: 使用系统 Chrome/Edge（%s）", reason)
    else:
        exe = bundled_chrome_exe()
        if exe is not None:
            logger.info("Playwright: 使用项目内浏览器 (%s)", exe)
        else:
            path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
            logger.info("Playwright: 使用项目内浏览器 (%s)", path)
