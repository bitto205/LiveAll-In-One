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
import gzip
import json
import logging
import os
import re
import secrets
import shutil
import struct
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

PROXY_PORT        = 8888
IPC_PORT          = 18998
_PROXY_VALUE      = f"127.0.0.1:{PROXY_PORT},direct://"
_TIMEOUT          = 60.0          # 等待首条 IPC 消息的超时（秒）
_SHELL_PROCESS    = "proxy_shell.exe"   # tasklist 检测用进程名
_SHELL_MARKER     = "proxy_shell.exe"   # 检测 patch 中是否已注入 spawn


# ─────────────────────────────────────────────
# CA 证书管理（patch 时由 Python 生成并安装）
# ─────────────────────────────────────────────

def _ca_paths() -> tuple[Path, Path]:
    d = Path.home() / ".livehelper"
    return d / "proxy_shell_ca.crt", d / "proxy_shell_ca.key"


def _ensure_ca_cert() -> Path:
    """若 CA 证书不存在则用 cryptography 库生成，返回 .crt 路径。"""
    cert_path, key_path = _ca_paths()
    if cert_path.exists() and key_path.exists():
        return cert_path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import datetime

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LiveHelper"),
        x509.NameAttribute(NameOID.COMMON_NAME, "LiveHelper Proxy CA"),
    ])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    logger.info(f"CA 证书已生成: {cert_path}")
    return cert_path


def _install_ca_cert() -> None:
    """将 CA 证书安装到 Windows ROOT 信任存储（需要管理员权限）。"""
    cert_path = _ensure_ca_cert()
    try:
        r = subprocess.run(
            ["certutil", "-addstore", "-f", "ROOT", str(cert_path)],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0:
            logger.info("CA 证书已安装到 Windows ROOT")
        else:
            logger.warning(f"certutil 返回非零: {r.returncode}\n{r.stderr.decode(errors='ignore')}")
    except Exception as e:
        logger.warning(f"certutil 异常: {e}")


# ─────────────────────────────────────────────
# 路径注册（主软件启动时调用，让 patch 能找到 exe）
# ─────────────────────────────────────────────

def _ipc_token_path() -> Path:
    return Path.home() / ".livehelper" / "ipc_token"


def _refresh_ipc_token() -> str:
    """每次主软件启动时重新生成 IPC token，写入磁盘后返回。"""
    token = secrets.token_hex(32)
    p = _ipc_token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(token, encoding="ascii")
    return token


def _shell_source() -> Optional[str]:
    """源 proxy_shell.exe：listener4.py 的同级目录，随主软件发布。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_shell.exe")
    return p if os.path.isfile(p) else None


def _load_shell_exe() -> Optional[str]:
    """
    读取 patch 时部署到直播伴侣目录的 proxy_shell.exe 路径。
    config.json 里的 proxy_shell_exe 由 patch_companion() 写入。
    """
    cfg_file = Path.home() / ".livehelper" / "config.json"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        p = cfg.get("proxy_shell_exe", "")
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    return None


def _save_deployed_exe(dest: str) -> None:
    """patch_companion() 部署完毕后，把目标路径写入 config.json。"""
    cfg_file = Path.home() / ".livehelper" / "config.json"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["proxy_shell_exe"] = dest
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def save_location() -> None:
    """将主软件目录写入 ~/.livehelper/config.json，刷新 IPC token。"""
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    cfg_file = Path.home() / ".livehelper" / "config.json"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["exe_dir"] = exe_dir
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    _refresh_ipc_token()
    logger.debug(f"主软件目录已注册: {exe_dir}")


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
      1. 把 proxy_shell.exe 从 listener/ 拷贝到 index.js 同级目录
      2. spawn 拷贝后的 proxy_shell.exe
      3. --proxy-server 参数（流量走 8888）
      4. 完整性校验绕过
      5. 生成并安装 CA 证书
    """
    path = find_index_js()
    if not path:
        return False, "未找到直播伴侣安装目录"

    # 源 exe（随主软件发布）
    src = _shell_source()
    if not src:
        return False, "未找到源 proxy_shell.exe（listener/ 目录），请确认软件完整性"

    # 目标：部署到 index.js 同级目录
    dest = os.path.join(os.path.dirname(path), "proxy_shell.exe")

    try:
        content = open(path, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        return False, f"读取 index.js 失败: {e}"

    # 已 patch 且 dest 就位 → 幂等
    if (f"127.0.0.1:{PROXY_PORT}" in content and _SHELL_MARKER in content
            and os.path.isfile(dest)):
        return True, "已经是 patch 状态"

    # ── 1. 拷贝 exe ──────────────────────────
    try:
        shutil.copy2(src, dest)
        logger.info(f"proxy_shell.exe 已部署到: {dest}")
    except Exception as e:
        return False, f"拷贝 proxy_shell.exe 失败: {e}"

    # ── 2. 记录部署路径到 config.json ────────
    _save_deployed_exe(dest)

    # ── 3. 备份 index.js ─────────────────────
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)

    new_content = content

    # ── 4. proxy-server appendSwitch ─────────
    proxy_re = re.compile(
        r'(\.commandLine\.appendSwitch\s*\(\s*["\']proxy-server["\'],\s*["\'])([^"\']*?)(["\'])'
    )
    proxy_inject = ""
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

    # ── 5. spawn（注入到 proxy-server 前） ───
    js_path = dest.replace("\\", "\\\\")
    spawn_code = (
        f';(function(){{var c=require("child_process");'
        f'try{{c.spawn("{js_path}",[],{{detached:false,stdio:"ignore",windowsHide:true}});}}'
        f'catch(e){{}}}}());'
    )
    idx = new_content.find("proxy-server")
    if idx >= 0:
        line_start = new_content.rfind(";", 0, idx) + 1
        new_content = new_content[:line_start] + spawn_code + new_content[line_start:]

    # ── 6. 完整性校验绕过 ────────────────────
    new_content, n = re.subn(r',!\w+\.ok\)', ',false)', new_content, count=1)
    if n == 0:
        logger.warning("未找到完整性校验 pattern，跳过")

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        return False, f"写入 index.js 失败: {e}"

    # ── 7. 生成并安装 CA 证书 ─────────────────
    _install_ca_cert()

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
# 运行时诊断
# ─────────────────────────────────────────────

def _is_ca_installed() -> bool:
    """检查 LiveHelper CA 证书是否已安装到 Windows ROOT 信任存储。"""
    cert_path, _ = _ca_paths()
    if not cert_path.exists():
        return False
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-ChildItem Cert:\\LocalMachine\\Root | "
             "Where-Object Subject -like '*LiveHelper*').Count -gt 0"],
            capture_output=True, timeout=10, encoding="utf-8", errors="ignore",
        )
        return "True" in r.stdout
    except Exception:
        return False


def _is_proxy_running() -> bool:
    """检测 proxy_shell.exe 是否在进程列表中。"""
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_SHELL_PROCESS}", "/NH"],
            capture_output=True, timeout=5, encoding="utf-8", errors="ignore",
        )
        return _SHELL_PROCESS.lower() in r.stdout.lower()
    except Exception:
        return False


def get_route4_status() -> dict:
    """
    主软件启动时调用，返回线路 4 整体状态，供 UI 展示。

    Keys:
        companion_installed  直播伴侣是否已安装
        index_js_found       找到 index.js
        is_patched           已 patch（含 spawn + proxy-server）
        exe_in_place         proxy_shell.exe 文件存在
        ca_installed         CA 证书在 Windows ROOT 信任链中
    """
    patched    = is_patched()
    shell_exe  = _load_shell_exe()
    status = {
        "companion_installed": bool(_find_install_dir()),
        "index_js_found":      bool(find_index_js()),
        "is_patched":          patched,
        "exe_in_place":        bool(shell_exe and os.path.isfile(shell_exe)),
        "ca_installed":        _is_ca_installed() if patched else False,
    }
    logger.info(
        "[线路4 启动状态] 伴侣已装=%s | index.js=%s | 已patch=%s | "
        "exe就位=%s | 证书已装=%s",
        status["companion_installed"], status["index_js_found"],
        status["is_patched"], status["exe_in_place"], status["ca_installed"],
    )
    return status


def get_route4_connect_check() -> dict:
    """
    连接直播间前调用，做双向路径 + 进程诊断并写 log。

    Keys:
        exe_known_to_main        listener4 能找到 proxy_shell.exe
        main_location_registered config.json 中 exe_dir 与当前 main 路径一致
        path_mismatch            index.js 中注入路径与当前 exe 路径不符
        exe_running              proxy_shell.exe 进程正在运行
    """
    shell_exe = _load_shell_exe()
    exe_known = bool(shell_exe and os.path.isfile(shell_exe))

    main_location_registered = False
    try:
        cfg = json.loads(
            (Path.home() / ".livehelper" / "config.json").read_text(encoding="utf-8")
        )
        current = os.path.normcase(os.path.dirname(os.path.abspath(sys.argv[0])))
        stored  = os.path.normcase(cfg.get("exe_dir", ""))
        main_location_registered = bool(stored) and current == stored
    except Exception:
        pass

    mismatch    = check_path_mismatch()
    exe_running = _is_proxy_running()

    result = {
        "exe_known_to_main":        exe_known,
        "main_location_registered": main_location_registered,
        "path_mismatch":            mismatch,
        "exe_running":              exe_running,
    }

    logger.info(
        "[线路4 连接诊断] exe路径已知=%s | main位置已注册=%s | "
        "路径变动=%s | 进程运行=%s",
        exe_known, main_location_registered, mismatch, exe_running,
    )
    if mismatch:
        logger.warning(
            "proxy_shell.exe 路径已变动（软件目录被移动？），建议重新 Patch 直播伴侣"
        )
    if not main_location_registered:
        logger.warning("主软件位置未注册或已变动，建议重启主软件以更新路径")
    if not exe_running:
        logger.warning(
            "proxy_shell.exe 未检测到运行中（直播伴侣启动后会自动 spawn；"
            "若伴侣已开启请检查 patch 状态）"
        )

    return result


# ─────────────────────────────────────────────
# Protobuf 解析（接收 Go 推来的原始 PushFrame 字节）
# ─────────────────────────────────────────────

def _g(obj, *keys, default=""):
    for k in keys:
        v = getattr(obj, k, None)
        if v is not None and v != "" and v != 0:
            return v
    return default


def _uname(user) -> str:
    return getattr(user, "nickname", "") or getattr(user, "nick_name", "")


def _uid(user) -> str:
    return str(getattr(user, "id", "") or getattr(user, "id_str", ""))


def _parse_item_pb(method: str, payload: bytes):
    import listener.Live_pb2 as pb
    try:
        if method == "WebcastChatMessage":
            m = pb.ChatMessage(); m.ParseFromString(payload)
            u, c = _uname(m.user), getattr(m, "content", "")
            if u and c:
                return ChatMessage(user=u, user_id=_uid(m.user), content=c)

        elif method == "WebcastGiftMessage":
            m = pb.GiftMessage(); m.ParseFromString(payload)
            if m.repeatEnd != 1:
                return None
            u = _uname(m.user)
            name = m.gift.name if m.gift else ""
            gid  = int(m.gift.id) if m.gift else 0
            cnt  = int(m.comboCount) if m.comboCount else 1
            if u and name:
                return GiftMessage(user=u, user_id=_uid(m.user),
                                   gift=name, gift_id=gid, count=cnt, repeat_end=1)

        elif method == "WebcastLikeMessage":
            m = pb.LikeMessage(); m.ParseFromString(payload)
            u = _uname(m.user)
            if u:
                return LikeMessage(user=u, user_id=_uid(m.user),
                                   count=int(_g(m, "count", default=1)))

        elif method == "WebcastMemberMessage":
            m = pb.MemberMessage(); m.ParseFromString(payload)
            u = _uname(m.user)
            if u:
                return EnterMessage(user=u, user_id=_uid(m.user))

        elif method == "WebcastSocialMessage":
            m = pb.SocialMessage(); m.ParseFromString(payload)
            u = _uname(m.user)
            if u:
                return FollowMessage(user=u, user_id=_uid(m.user))

        elif method == "WebcastRoomUserSeqMessage":
            m = pb.RoomUserSeqMessage(); m.ParseFromString(payload)
            return OnlineMessage(
                current=int(_g(m, "total", default=0)),
                total=int(_g(m, "totalPvForAnchor", "total_pv_for_anchor", default=0)),
            )

        elif method == "WebcastFansclubMessage":
            m = pb.FansclubMessage(); m.ParseFromString(payload)
            return FansclubMessage(
                user=_uname(m.user) if hasattr(m, "user") else "",
                user_id=_uid(m.user) if hasattr(m, "user") else "",
                content=getattr(m, "content", ""),
            )

        elif method == "WebcastEmojiChatMessage":
            m = pb.EmojiChatMessage(); m.ParseFromString(payload)
            return EmojiChatMessage(
                user=_uname(m.user) if hasattr(m, "user") else "",
                user_id=_uid(m.user) if hasattr(m, "user") else "",
                emoji_id=str(_g(m, "emojiId", "emoji_id", default="")),
                default_content=str(_g(m, "defaultContent", "default_content", default="")),
            )

        elif method == "WebcastRoomStatsMessage":
            m = pb.RoomStatsMessage(); m.ParseFromString(payload)
            return RoomStatsMessage(
                display_long=str(_g(m, "displayLong", "display_long", default="")),
            )

        elif method == "WebcastControlMessage":
            m = pb.ControlMessage(); m.ParseFromString(payload)
            return ControlMessage(status=int(getattr(m, "status", 0)))

    except Exception as e:
        logger.debug(f"pb 解析失败 [{method}]: {e}")
    return None


def _parse_frame_pb(data: bytes) -> list:
    import listener.Live_pb2 as pb
    results = []
    try:
        frame = pb.PushFrame(); frame.ParseFromString(data)
        if not frame.payload:
            return results
        try:
            body = gzip.decompress(frame.payload)
        except Exception:
            body = frame.payload
        response = pb.LiveResponse(); response.ParseFromString(body)
        for item in response.messagesList:
            msg = _parse_item_pb(item.method, item.payload)
            if msg is not None:
                results.append(msg)
    except Exception as e:
        logger.debug(f"帧解析失败: {e}")
    return results


# ─────────────────────────────────────────────
# 监听入口（IPC 客户端）
# ─────────────────────────────────────────────

async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
) -> None:
    """
    连接 proxy_shell 的 IPC 端口（18998），接收原始 PushFrame 字节帧（4字节长度前缀），
    本地 protobuf 解析后转发给 callback。60 秒内无首条消息则超时。
    """
    logger.info("=== 线路 4 连接启动 ===")
    if not is_patched():
        logger.error("直播伴侣未 patch，请先执行 Patch 操作")
        if on_status:
            on_status(False)
        return

    check = get_route4_connect_check()
    if not check["exe_running"]:
        logger.error("proxy_shell.exe 未运行，无法建立 IPC 连接")
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

    # 发送 token 完成身份验证（Go 在 3 秒内校验）
    try:
        token = _ipc_token_path().read_text(encoding="ascii").strip()
        writer.write(token.encode("ascii") + b"\n")
        await writer.drain()
    except Exception as e:
        logger.error(f"IPC token 发送失败: {e}")
        writer.close()
        if on_status:
            on_status(False)
        return

    logger.info(f"已连接 proxy_shell IPC（{IPC_PORT}），等待弹幕消息…（{_TIMEOUT:.0f}s 超时）")

    async def _recv() -> None:
        first = True
        while True:
            # 4-byte big-endian length prefix (with timeout only before first message)
            if first:
                hdr = await asyncio.wait_for(reader.readexactly(4), timeout=_TIMEOUT)
            else:
                hdr = await reader.readexactly(4)
            length = struct.unpack(">I", hdr)[0]
            data = await reader.readexactly(length)
            msgs = _parse_frame_pb(data)
            for msg in msgs:
                if first:
                    first = False
                    logger.info("✅ 首条弹幕已到达，IPC 通道正常，开始转发")
                    if on_status:
                        on_status(True)
                try:
                    callback(msg)
                except Exception as e:
                    logger.debug(f"callback 异常: {e}")

    try:
        await _recv()
    except asyncio.TimeoutError:
        logger.warning(f"{_TIMEOUT:.0f}s 内未收到消息，请确认直播伴侣已重启并开播")
    except asyncio.IncompleteReadError:
        logger.info("IPC 连接已断开")
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
