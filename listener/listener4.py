"""
listener4.py — 直播伴侣代理注入方案（proxy_shell 架构）

原理：
1. patch_companion() 向直播伴侣 index.js 注入两段代码：
   - spawn proxy_shell.exe（代理进程，开机/开播时自动启动）
   - --proxy-server=127.0.0.1:8888,direct://（所有流量走 proxy_shell）
2. proxy_shell.exe 持久运行，对 webcast WSS 做 TLS MITM，解析弹幕
3. start_listener() 连接 proxy_shell 的 IPC 端口（18998）接收 JSON 消息

优势：
- proxy_shell 跟着直播伴侣生命周期，主软件随时可以接入，无需先起代理
- 不依赖 mitmproxy / WinDivert，exe 轻量（约 9 MB）
- 不占用系统代理，证书只对 webcast 域生效
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import winreg
from pathlib import Path
from typing import Callable, Optional

try:
    from models import (ChatMessage, ControlMessage, EmojiChatMessage,
                        EnterMessage, FansclubMessage, FollowMessage,
                        GiftMessage, LikeMessage, OnlineMessage,
                        RoomRankMessage, RoomStatsMessage)
except ImportError:
    from sys import path as _p
    _p.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from models import (ChatMessage, ControlMessage, EmojiChatMessage,
                        EnterMessage, FansclubMessage, FollowMessage,
                        GiftMessage, LikeMessage, OnlineMessage,
                        RoomRankMessage, RoomStatsMessage)

logger = logging.getLogger(__name__)

PROXY_PORT   = 8888
IPC_PORT     = 18998
_PROXY_VALUE = f"127.0.0.1:{PROXY_PORT},direct://"
_TIMEOUT     = 60.0          # 等待首条 IPC 消息的超时（秒）

# proxy_shell.exe 在 listener/ 目录下
_SHELL_MARKER = "proxy_shell.exe"   # 用于检测 patch 中是否已注入 spawn


# ─────────────────────────────────────────────
# 路径注册（主软件启动时调用，让 patch 能找到 exe）
# ─────────────────────────────────────────────

def save_location() -> None:
    """将当前项目目录写入 ~/.livehelper/config.json，供 patch_companion 使用。"""
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    shell_exe = os.path.join(exe_dir, "listener", "proxy_shell.exe")
    cfg = {"exe_dir": exe_dir, "proxy_shell_exe": shell_exe}
    cfg_dir = Path.home() / ".livehelper"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    logger.debug(f"路径已注册: {shell_exe}")


def _load_shell_exe() -> Optional[str]:
    """从 config.json 读取 proxy_shell.exe 路径；回退到本文件旁边。"""
    cfg_file = Path.home() / ".livehelper" / "config.json"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        p = cfg.get("proxy_shell_exe", "")
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    # 回退：listener4.py 旁边找 proxy_shell.exe
    fallback = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_shell.exe")
    return fallback if os.path.isfile(fallback) else None


# ─────────────────────────────────────────────
# 直播伴侣路径查找
# ─────────────────────────────────────────────

def _find_install_dir() -> Optional[str]:
    subkeys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ]
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for subkey in subkeys:
            try:
                with winreg.OpenKey(hive, subkey) as root:
                    for i in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, i)) as entry:
                                try:
                                    name = winreg.QueryValueEx(entry, "DisplayName")[0]
                                    if "直播伴侣" not in name:
                                        continue
                                    loc = winreg.QueryValueEx(entry, "InstallLocation")[0]
                                    if loc and os.path.isdir(loc):
                                        return loc.rstrip("\\")
                                except FileNotFoundError:
                                    pass
                        except Exception:
                            continue
            except Exception:
                continue
    return None


def find_index_js() -> Optional[str]:
    """返回直播伴侣 index.js 的完整路径。支持 Launcher 多版本目录结构。"""
    root = _find_install_dir()
    if not root:
        return None

    candidates: list[str] = []

    config_path = os.path.join(root, "launcher_config.json")
    if os.path.isfile(config_path):
        try:
            cfg = json.load(open(config_path, encoding="utf-8", errors="ignore"))
            for key in ("cur_path", "new_path"):
                ver = cfg.get(key, "")
                if ver:
                    for rel in ("index.js", os.path.join("app.asar.unpacked", "index.js")):
                        candidates.append(os.path.join(root, ver, "resources", "app", rel))
        except Exception:
            pass

    for rel in (
        os.path.join("resources", "app", "index.js"),
        os.path.join("resources", "app.asar.unpacked", "index.js"),
    ):
        candidates.append(os.path.join(root, rel))

    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


# ─────────────────────────────────────────────
# Patch / Unpatch
# ─────────────────────────────────────────────

def is_patched() -> bool:
    path = find_index_js()
    if not path:
        return False
    try:
        content = open(path, encoding="utf-8", errors="ignore").read()
        return f"127.0.0.1:{PROXY_PORT}" in content and _SHELL_MARKER in content
    except Exception:
        return False


def patch_companion() -> tuple[bool, str]:
    """
    向直播伴侣 index.js 注入：
      1. proxy_shell.exe spawn 代码（开播时自动起代理）
      2. --proxy-server 参数（流量走 8888）
      3. 完整性校验绕过
    同时运行 proxy_shell.exe --setup 安装 CA 证书。
    """
    path = find_index_js()
    if not path:
        return False, "未找到直播伴侣安装目录"

    shell_exe = _load_shell_exe()
    if not shell_exe:
        return False, "未找到 proxy_shell.exe，请确认软件目录完整"

    try:
        content = open(path, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        return False, f"读取 index.js 失败: {e}"

    if f"127.0.0.1:{PROXY_PORT}" in content and _SHELL_MARKER in content:
        return True, "已经是 patch 状态"

    # 备份
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)

    new_content = content

    # 1. proxy-server appendSwitch（替换已有或注入新）
    proxy_re = re.compile(
        r'(\.commandLine\.appendSwitch\s*\(\s*["\']proxy-server["\'],\s*["\'])([^"\']*?)(["\'])'
    )
    if proxy_re.search(new_content):
        new_content = proxy_re.sub(rf'\g<1>{_PROXY_VALUE}\g<3>', new_content)
    else:
        ready_re = re.compile(r'(\b(\w+)\.on\s*\(\s*["\']ready["\'])')
        m = ready_re.search(new_content)
        if not m:
            return False, "未找到合适的注入点，index.js 结构可能已变更"
        app_var = m.group(2)
        proxy_inject = f'{app_var}.commandLine.appendSwitch("proxy-server","{_PROXY_VALUE}");'
        new_content = new_content[:m.start()] + proxy_inject + new_content[m.start():]

    # 2. spawn proxy_shell.exe（在同一注入点前再插一段）
    js_path = shell_exe.replace("\\", "\\\\")
    spawn_code = (
        f';(function(){{var c=require("child_process");'
        f'try{{c.spawn("{js_path}",[],{{detached:false,stdio:"ignore",windowsHide:true}});}}'
        f'catch(e){{}}}}());'
    )
    # 找到刚才注入的 proxy_inject（或已有的 appendSwitch），在其前面插入 spawn
    inject_anchor = proxy_inject if not proxy_re.search(content) else \
        f'.commandLine.appendSwitch("proxy-server","{_PROXY_VALUE}")'
    idx = new_content.find("proxy-server")
    if idx >= 0:
        # 找到 appendSwitch 那一行的行首位置
        line_start = new_content.rfind(";", 0, idx) + 1
        new_content = new_content[:line_start] + spawn_code + new_content[line_start:]

    # 3. 完整性校验绕过
    new_content, n = re.subn(r',!\w+\.ok\)', ',false)', new_content, count=1)
    if n == 0:
        logger.warning("未找到完整性校验 pattern")

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        return False, f"写入 index.js 失败: {e}"

    # 4. 运行 proxy_shell --setup 安装 CA 证书（主软件有管理员权限）
    try:
        subprocess.run([shell_exe, "--setup"], capture_output=True, timeout=30)
        logger.info("proxy_shell --setup 完成")
    except Exception as e:
        logger.warning(f"proxy_shell --setup 异常: {e}")

    return True, "Patch 成功！请重启直播伴侣使设置生效"


def unpatch_companion() -> tuple[bool, str]:
    path = find_index_js()
    if not path:
        return False, "未找到直播伴侣"
    bak = path + ".bak"
    if not os.path.exists(bak):
        return False, "未找到备份文件"
    try:
        shutil.copy2(bak, path)
        return True, "已还原原始 index.js"
    except Exception as e:
        return False, f"还原失败: {e}"


def check_path_mismatch() -> bool:
    """检查 index.js 中注入的 proxy_shell.exe 路径是否与当前路径一致。"""
    path = find_index_js()
    if not path:
        return False
    shell_exe = _load_shell_exe()
    if not shell_exe:
        return False
    try:
        content = open(path, encoding="utf-8", errors="ignore").read()
        js_path = shell_exe.replace("\\", "\\\\")
        return js_path not in content and _SHELL_MARKER in content
    except Exception:
        return False


# ─────────────────────────────────────────────
# JSON → model 转换
# ─────────────────────────────────────────────

def _json_to_msg(data: dict):
    t = data.get("type", "")
    try:
        if t == "chat":
            return ChatMessage(user=data["user"], user_id=data.get("user_id", ""),
                               content=data.get("content", ""))
        if t == "gift":
            return GiftMessage(user=data["user"], user_id=data.get("user_id", ""),
                               gift=data.get("gift", ""), gift_id=data.get("gift_id", 0),
                               count=data.get("count", 1), repeat_end=1)
        if t == "like":
            return LikeMessage(user=data["user"], user_id=data.get("user_id", ""),
                               count=data.get("count", 1))
        if t == "enter":
            return EnterMessage(user=data["user"], user_id=data.get("user_id", ""))
        if t == "follow":
            return FollowMessage(user=data["user"], user_id=data.get("user_id", ""))
        if t == "online":
            return OnlineMessage(current=data.get("current", 0), total=data.get("total", 0))
        if t == "fansclub":
            return FansclubMessage(user=data.get("user", ""), user_id=data.get("user_id", ""),
                                   content=data.get("content", ""))
        if t == "emoji":
            return EmojiChatMessage(user=data.get("user", ""), user_id=data.get("user_id", ""),
                                    emoji_id=data.get("emoji_id", ""),
                                    default_content=data.get("default_content", ""))
        if t == "room_stats":
            return RoomStatsMessage(display_long=data.get("display_long", ""))
        if t == "control":
            return ControlMessage(status=data.get("status", 0))
    except Exception as e:
        logger.debug(f"json_to_msg [{t}]: {e}")
    return None


# ─────────────────────────────────────────────
# 监听入口（IPC 客户端）
# ─────────────────────────────────────────────

async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
) -> None:
    """
    连接 proxy_shell 的 IPC 端口（18998），接收 JSON 弹幕消息并转发给 callback。
    60 秒内没有任何消息则超时。
    """
    if not is_patched():
        logger.error("直播伴侣未 patch，请先执行 Patch 操作")
        if on_status:
            on_status(False)
        return

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", IPC_PORT),
            timeout=10,
        )
    except Exception as e:
        logger.error(f"连接 IPC 端口 {IPC_PORT} 失败: {e}（proxy_shell 是否已随直播伴侣启动？）")
        if on_status:
            on_status(False)
        return

    logger.info(f"已连接 proxy_shell IPC（{IPC_PORT}），等待弹幕消息…（{_TIMEOUT:.0f}s 超时）")

    async def _recv() -> None:
        first = True
        async for line in reader:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                msg = _json_to_msg(data)
                if msg is None:
                    continue
                if first:
                    first = False
                    logger.info("✅ 收到首条弹幕，连接正常")
                    if on_status:
                        on_status(True)
                callback(msg)
            except Exception as e:
                logger.debug(f"IPC 消息解析失败: {e}")

    try:
        await asyncio.wait_for(_recv(), timeout=_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning(f"{_TIMEOUT:.0f}s 内未收到消息，请确认直播伴侣已重启并开播")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"IPC 接收异常: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        if on_status:
            on_status(False)
