"""
listener1 / listener2 系统浏览器可行性测试。

直接调用真实的 listener._run，强制走系统浏览器（chrome→edge 回退），
挂直播间一段时间，统计各类消息数量，验证系统浏览器能否正常收到消息。

用法:
    python test/_test_listener_system_browser.py
    python test/_test_listener_system_browser.py --seconds 40 --live-id YOUR_LIVE_ID
    python test/_test_listener_system_browser.py --only 1        # 只测 listener1
    python test/_test_listener_system_browser.py --headed        # 显示浏览器窗口
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# Windows 控制台默认 GBK，直播消息 / emoji 会触发 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from util import playwright_bootstrap as pb  # noqa: E402
from util.log_util import ensure_console_logging  # noqa: E402

import listener.listener1 as l1  # noqa: E402
import listener.listener2 as l2  # noqa: E402


def _load_live_id(cli: str | None) -> str:
    if cli:
        return cli.strip()
    cfg = ROOT / "config.json"
    if cfg.is_file():
        try:
            lid = json.loads(cfg.read_text(encoding="utf-8")).get("live_id", "")
            if lid:
                return str(lid).strip()
        except Exception:
            pass
    return ""


def _make_empty_state() -> str:
    """没有登录态时用一个合法的空 storage_state。"""
    real = ROOT / "state.json"
    if real.is_file():
        return str(real)
    fd, path = tempfile.mkstemp(suffix="_state.json", prefix="empty_")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"cookies": [], "origins": []}, f)
    return path


async def _probe(runner, name: str, live_id: str, state_file: str,
                 headless: bool, seconds: float, *,
                 force_system: bool = True,
                 browser_channel: str | None = None) -> dict:
    counts: Counter[str] = Counter()
    result = {"name": name, "connected": False, "total": 0,
              "by_type": counts, "error": ""}

    def on_status(connected: bool):
        if connected:
            result["connected"] = True

    def on_message(msg):
        counts[type(msg).__name__] += 1
        result["total"] += 1

    task = asyncio.ensure_future(
        runner(live_id, on_message, state_file, headless, False, on_status,
               force_system=force_system, browser_channel=browser_channel)
    )
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=seconds)
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    return result


def _print_result(r: dict, seconds: float) -> None:
    print("=" * 56)
    verdict = "可用 ✅" if (r["connected"] and r["total"] > 0) else "未收到消息 ❌"
    print(f"[{r['name']}] {verdict}")
    print(f"  连接确认: {r['connected']}")
    print(f"  {seconds:.0f}s 内消息总数: {r['total']}")
    if r["by_type"]:
        for k, v in sorted(r["by_type"].items(), key=lambda x: -x[1]):
            print(f"    {k:20} {v}")
    if r["error"]:
        print(f"  错误: {r['error']}")
    print()


async def _main(args: argparse.Namespace) -> int:
    ensure_console_logging(logging.INFO)
    live_id = _load_live_id(args.live_id)
    if not live_id:
        print("请提供 --live-id 或在 config.json 设置 live_id")
        return 2

    state_file = _make_empty_state()
    login_state = "已登录 state.json" if Path(state_file).name == "state.json" \
        else "无登录态（空 state）"
    headless = not args.headed
    force_system = not args.bundled
    browser_channel = args.channel
    if args.bundled:
        pb.configure_playwright_browsers(prefer_system=False)
        browser_channel = None
    elif browser_channel:
        force_system = True
    if args.bundled:
        backend = "项目 bundled"
    elif browser_channel == "msedge":
        backend = "系统 Edge"
    elif browser_channel == "chrome":
        backend = "系统 Chrome"
    elif force_system:
        backend = "系统 Chrome/Edge (force_system)"
    else:
        backend = "项目 bundled"

    print(f"listener 浏览器可行性测试")
    print(f"  live_id  = {live_id}")
    print(f"  登录态   = {login_state}")
    print(f"  headless = {headless}")
    print(f"  每项时长 = {args.seconds}s")
    print(f"  浏览器   = {backend}")
    print()

    results = []
    if args.only in (None, "1"):
        print(f"--- listener1 (JS Hook / console) {backend} ---")
        results.append(await _probe(l1._run, "listener1", live_id,
                                    state_file, headless, args.seconds,
                                    force_system=force_system,
                                    browser_channel=browser_channel))
    if args.only in (None, "2"):
        print(f"--- listener2 (WSS / protobuf) {backend} ---")
        results.append(await _probe(l2._run, "listener2", live_id,
                                    state_file, headless, args.seconds,
                                    force_system=force_system,
                                    browser_channel=browser_channel))

    print()
    for r in results:
        _print_result(r, args.seconds)

    if Path(state_file).name != "state.json":
        try:
            os.remove(state_file)
        except Exception:
            pass

    ok = all(r["connected"] and r["total"] > 0 for r in results) and results
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="listener 系统浏览器可行性测试")
    p.add_argument("--live-id", help="直播间 ID（默认读 config.json）")
    p.add_argument("--seconds", type=float, default=30.0, help="每个 listener 挂机秒数")
    p.add_argument("--only", choices=("1", "2"), help="只测某个 listener")
    p.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    p.add_argument("--bundled", action="store_true",
                   help="用项目 bundled 浏览器对照（默认强制系统浏览器）")
    p.add_argument("--channel", choices=("chrome", "msedge"),
                   help="指定系统浏览器通道（默认 chrome→edge 回退）")
    return asyncio.run(_main(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
