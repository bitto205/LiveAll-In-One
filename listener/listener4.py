"""
listener4.py - 线路 4：patch 直播伴侣 + proxy_shell IPC 收消息
"""
import asyncio
import filecmp
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

from listener.log_util import get_logger, on_connect_success
from listener.LiveProtobuf import parse_frame

logger = get_logger(__name__)

PROXY_PORT        = 19088
IPC_PORT          = 19098
_PROXY_VALUE      = f"127.0.0.1:{PROXY_PORT},direct://"
_TIMEOUT          = 60.0
_SHELL_PROCESS    = "proxy_shell.exe"   # process name for tasklist
_SHELL_MARKER     = "proxy_shell.exe"   # marker in patched index.js
_IPC_CTRL_PREFIX  = b"__LH_CTRL__:"
_IPC_CTRL_WS_UP   = b"WS_CONNECTED"
_IPC_CTRL_WS_DOWN = b"WS_DISCONNECTED"


# ---------------------------------------------------------
# CA 证书管理（patch 时由 Python 生成并安装）
# ---------------------------------------------------------

def _ca_paths() -> tuple[Path, Path]:
    d = Path.home() / ".livehelper"
    return d / "proxy_shell_ca.crt", d / "proxy_shell_ca.key"


def _ensure_ca_cert() -> Path:
    """Create CA certificate when missing and return crt path."""
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
    """Install the CA cert into Windows ROOT store."""
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
        logger.warning(f"certutil 寮傚父: {e}")


# ---------------------------------------------------------
# 路径注册（主程序启动时调用，保证 patch 能找到 exe）
# ---------------------------------------------------------

def _ipc_token_path() -> Path:
    return Path.home() / ".livehelper" / "ipc_token"


def _refresh_ipc_token() -> str:
    """Refresh IPC token and save to disk."""
    token = secrets.token_hex(32)
    p = _ipc_token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(token, encoding="ascii")
    return token


def _shell_source() -> Optional[str]:
    """Return bundled proxy_shell.exe path near this file."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_shell.exe")
    return p if os.path.isfile(p) else None


def _load_shell_exe() -> Optional[str]:
    """Read deployed proxy_shell.exe path from config.json."""
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
    """Persist deployed proxy_shell.exe path into config.json."""
    cfg_file = Path.home() / ".livehelper" / "config.json"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["proxy_shell_exe"] = dest
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def save_location() -> None:
    """Persist current main app location and refresh IPC token."""
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
    logger.debug(f"主程序目录已注册: {exe_dir}")


# ---------------------------------------------------------
# 直播伴侣路径查找
# ---------------------------------------------------------

_COMPANION_DIR_CFG = "companion_install_dir"


def _find_install_dir() -> Optional[str]:
    """Find companion install directory from registry."""
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


def is_companion_in_registry() -> bool:
    return bool(_find_install_dir())


def _read_manual_companion_dir_cfg() -> str:
    try:
        import config as _cfg
        return (_cfg.get(_COMPANION_DIR_CFG, "") or "").strip()
    except Exception:
        return ""


def validate_manual_companion_dir() -> tuple[Optional[str], bool]:
    """Validate manual companion directory from config."""
    raw = _read_manual_companion_dir_cfg()
    if not raw:
        return None, False
    p = os.path.normpath(raw)
    if not os.path.isdir(p) or not find_index_js_in_root(p):
        clear_manual_companion_dir()
        logger.info("[companion-path] invalid manual path cleared: %s", raw)
        return None, True
    return p, False


def get_manual_companion_dir() -> Optional[str]:
    path, _ = validate_manual_companion_dir()
    return path


def set_manual_companion_dir(path: str) -> tuple[bool, str]:
    """Set manual companion root directory."""
    path = os.path.normpath(path.strip().rstrip("\\/"))
    if not path or not os.path.isdir(path):
        return False, "Invalid directory"
    if not find_index_js_in_root(path):
        return False, "index.js not found in selected directory"
    try:
        import config as _cfg
        _cfg.set(_COMPANION_DIR_CFG, path)
    except Exception as e:
        return False, f"Failed to save path: {e}"
    return True, "Companion path saved"


def clear_manual_companion_dir() -> None:
    try:
        import config as _cfg
        _cfg.set(_COMPANION_DIR_CFG, "")
    except Exception:
        pass


def sync_companion_dir_from_registry() -> bool:
    """Prefer registry path and clear manual override when present."""
    reg = _find_install_dir()
    if not reg:
        return False
    if _read_manual_companion_dir_cfg():
        clear_manual_companion_dir()
        logger.info("[companion-path] registry path takes precedence: %s", reg)
    return True


def get_companion_install_dir() -> Optional[str]:
    """Return companion path from registry first, fallback manual config."""
    reg = _find_install_dir()
    if reg:
        return reg
    return get_manual_companion_dir()


def _index_js_candidates(root: str) -> list[str]:
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
    return candidates


def find_index_js_in_root(root: str) -> Optional[str]:
    for p in _index_js_candidates(root):
        if os.path.isfile(p):
            return p
    return None


def find_index_js() -> Optional[str]:
    """Return full path of companion index.js."""
    root = get_companion_install_dir()
    if not root:
        return None
    return find_index_js_in_root(root)


# ---------------------------------------------------------
# index.js / exe 检测
# ---------------------------------------------------------

def _index_js_paths() -> tuple[Optional[str], Optional[str]]:
    path = find_index_js()
    if not path:
        return None, None
    return path, path + ".bak"


def _deployed_shell_path() -> Optional[str]:
    path = find_index_js()
    if not path:
        return None
    return os.path.join(os.path.dirname(path), _SHELL_PROCESS)


def is_index_js_modified() -> bool:
    """Whether index.js differs from backup baseline."""
    path, bak = _index_js_paths()
    if not path:
        return False
    try:
        current = open(path, encoding="utf-8", errors="ignore").read()
        if not bak or not os.path.exists(bak):
            return (
                f"127.0.0.1:{PROXY_PORT}" in current
                or _SHELL_MARKER in current
            )
        original = open(bak, encoding="utf-8", errors="ignore").read()
        return current != original
    except Exception:
        return False


def is_exe_identical_to_source(deployed: Optional[str] = None) -> bool:
    """Whether deployed proxy_shell.exe matches bundled source bytes."""
    src = _shell_source()
    dest = deployed or _deployed_shell_path()
    if not src or not dest or not os.path.isfile(dest):
        return False
    return filecmp.cmp(src, dest, shallow=False)


def _build_patched_content(original: str, dest: str) -> tuple[Optional[str], str]:
    """Build complete patched index.js content from original backup."""
    new_content = original

    proxy_re = re.compile(
        r'(\.commandLine\.appendSwitch\s*\(\s*["\']proxy-server["\'],\s*["\'])([^"\']*?)(["\'])'
    )
    if proxy_re.search(new_content):
        new_content = proxy_re.sub(rf"\g<1>{_PROXY_VALUE}\g<3>", new_content)
    else:
        ready_re = re.compile(r"(\b(\w+)\.on\s*\(\s*['\"]ready['\"])")
        m = ready_re.search(new_content)
        if not m:
            return None, "No suitable injection point found in index.js"
        app_var = m.group(2)
        proxy_inject = (
            f'{app_var}.commandLine.appendSwitch("proxy-server","{_PROXY_VALUE}");'
        )
        new_content = new_content[: m.start()] + proxy_inject + new_content[m.start() :]

    js_path = dest.replace("\\", "\\\\")
    spawn_code = (
        f';(function(){{var c=require("child_process");'
        f'try{{c.spawn("{js_path}",[],{{detached:false,stdio:"ignore",windowsHide:true}});}}'
        f"catch(e){{}}}}());"
    )
    idx = new_content.find("proxy-server")
    if idx >= 0:
        line_start = new_content.rfind(";", 0, idx) + 1
        new_content = new_content[:line_start] + spawn_code + new_content[line_start:]

    new_content, _ = re.subn(r",!\w+\.ok\)", ",false)", new_content, count=1)
    return new_content, ""


def get_expected_patched_index_js() -> Optional[str]:
    """Compute expected fully-patched index.js from backup."""
    path, bak = _index_js_paths()
    if not path or not bak or not os.path.exists(bak):
        return None
    dest = os.path.join(os.path.dirname(path), _SHELL_PROCESS)
    content, err = _build_patched_content(
        open(bak, encoding="utf-8", errors="ignore").read(),
        dest,
    )
    if content is None:
        logger.warning(f"无法生成预期 patch 文本: {err}")
        return None
    return content


def is_index_js_exactly_patched() -> bool:
    """Whether current index.js exactly matches expected patched output."""
    path, _ = _index_js_paths()
    expected = get_expected_patched_index_js()
    if not path or expected is None:
        return False
    try:
        actual = open(path, encoding="utf-8", errors="ignore").read()
        return actual == expected
    except Exception:
        return False


def is_patched() -> bool:
    """Strict patch status: exe match + exact index.js match."""
    return is_exe_identical_to_source() and is_index_js_exactly_patched()


# ---------------------------------------------------------
# Patch / Unpatch
# ---------------------------------------------------------

def patch_companion() -> tuple[bool, str]:
    """Patch companion index.js and deploy proxy_shell.exe."""
    path = find_index_js()
    if not path:
        return False, "Companion install directory not found"

    src = _shell_source()
    if not src:
        return False, "Bundled proxy_shell.exe not found in listener directory"

    dest = os.path.join(os.path.dirname(path), _SHELL_PROCESS)
    bak = path + ".bak"

    if not os.path.exists(bak):
        try:
            shutil.copy2(path, bak)
        except Exception as e:
            return False, f"备份 index.js 失败: {e}"

    try:
        original = open(bak, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        return False, f"读取备份 index.js 失败: {e}"

    new_content, err = _build_patched_content(original, dest)
    if new_content is None:
        return False, err

    if is_index_js_exactly_patched() and is_exe_identical_to_source(dest):
        return True, "Already patched"

    try:
        shutil.copy2(src, dest)
        logger.info(f"proxy_shell.exe 已部署到: {dest}")
    except Exception as e:
        return False, f"复制 proxy_shell.exe 失败: {e}"

    _save_deployed_exe(dest)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        return False, f"写入 index.js 失败: {e}"

    _install_ca_cert()
    return True, "Patch successful. Restart companion app to take effect."


def unpatch_companion() -> tuple[bool, str]:
    path = find_index_js()
    if not path:
        return False, "Companion install not found"
    bak = path + ".bak"
    if not os.path.exists(bak):
        return False, "Backup file not found"
    try:
        shutil.copy2(bak, path)
        return True, "已还原原始 index.js"
    except Exception as e:
        return False, f"还原失败: {e}"


def check_path_mismatch() -> bool:
    """Check whether injected proxy_shell path mismatches current path."""
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


# ---------------------------------------------------------
# 运行时诊断
# ---------------------------------------------------------

def _is_ca_installed() -> bool:
    """Check whether LiveHelper CA is installed in Windows ROOT store."""
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
    """Check whether proxy_shell.exe process is running."""
    try:
        r = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {_SHELL_PROCESS}", "/NH"],
            capture_output=True, timeout=5, encoding="utf-8", errors="ignore",
        )
        return _SHELL_PROCESS.lower() in r.stdout.lower()
    except Exception:
        return False


def is_proxy_shell_running() -> bool:
    return _is_proxy_running()


def get_companion_path_fields() -> dict:
    """Route 3/4 shared companion path status fields."""
    _, manual_invalid = validate_manual_companion_dir()
    install_dir = get_companion_install_dir()
    return {
        "companion_in_registry": is_companion_in_registry(),
        "companion_installed":     bool(install_dir),
        "manual_path_invalid":     manual_invalid,
        "manual_companion_dir":    install_dir or "",
        "index_js_found":          bool(find_index_js()),
    }


def _build_page_status() -> dict:
    """Build route 4 page status without logging."""
    path_fields = get_companion_path_fields()
    exe_identical = is_exe_identical_to_source()
    index_exact = is_index_js_exactly_patched()
    patched_strict = exe_identical and index_exact
    shell_exe = _load_shell_exe()
    return {
        **path_fields,
        "is_patched":          patched_strict,
        "exe_identical":       exe_identical,
        "index_exact":         index_exact,
        "exe_in_place":        bool(shell_exe and os.path.isfile(shell_exe)),
        "ca_installed":        _is_ca_installed() if patched_strict else False,
        "patch_needed":        bool(path_fields["index_js_found"]) and not patched_strict,
    }


def run_page_check() -> dict:
    """Run route 4 page check and log current patch status."""
    status = _build_page_status()
    logger.info(
        "[route4 page check] registry=%s | companion=%s | index.js=%s | strict_patch=%s | "
        "exe_identical=%s | index_exact=%s | ca_installed=%s",
        status["companion_in_registry"], status["companion_installed"],
        status["index_js_found"],
        status["is_patched"], status["exe_identical"], status["index_exact"],
        status["ca_installed"],
    )
    return status


def get_page_status() -> dict:
    """Return route 4 UI status from current checks."""
    return _build_page_status()


def get_route4_connect_check() -> dict:
    """Run route 4 pre-connect diagnostics."""
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
        "[route4 pre-connect] exe_known=%s | main_registered=%s | "
        "path_mismatch=%s | process_running=%s",
        exe_known, main_location_registered, mismatch, exe_running,
    )
    if mismatch:
        logger.warning(
            "proxy_shell.exe path mismatch detected; re-patch companion is recommended"
        )
    if not main_location_registered:
        logger.warning("main executable path is missing or changed; restart app to refresh saved path")
    if not exe_running:
        logger.warning(
            "proxy_shell.exe not detected in process list (it should be spawned by companion app)"
        )

    return result


async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
) -> None:
    """Connect to proxy_shell IPC and forward parsed messages to callback."""
    logger.info("=== route 4 connect start ===")
    if not is_patched():
        logger.error("Companion is not patched; run Patch first")
        if on_status:
            on_status(False)
        return

    check = get_route4_connect_check()
    if not check["exe_running"]:
        logger.error("proxy_shell.exe is not running; cannot establish IPC")
        if on_status:
            on_status(False)
        return

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", IPC_PORT),
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Failed to connect IPC port {IPC_PORT}: {e}")
        if on_status:
            on_status(False)
        return

    # Send auth token for IPC handshake.
    try:
        token = _ipc_token_path().read_text(encoding="ascii").strip()
        writer.write(token.encode("ascii") + b"\n")
        await writer.drain()
    except Exception as e:
        logger.error(f"Failed to send IPC token: {e}")
        writer.close()
        if on_status:
            on_status(False)
        return

    logger.info(f"Connected to proxy_shell IPC ({IPC_PORT}), waiting for messages ({_TIMEOUT:.0f}s timeout)")

    ws_active = False

    async def _recv() -> None:
        nonlocal ws_active
        awaiting_first = True
        live_confirmed = False

        def _confirm_live(source: str) -> None:
            nonlocal awaiting_first, live_confirmed
            ws_active = True
            if live_confirmed:
                return
            live_confirmed = True
            awaiting_first = False
            on_connect_success("listener4")
            logger.info(f"{source}，连接成功")
            if on_status:
                on_status(True)

        while True:
            if awaiting_first:
                hdr = await asyncio.wait_for(reader.readexactly(4), timeout=_TIMEOUT)
            else:
                hdr = await reader.readexactly(4)
            length = struct.unpack(">I", hdr)[0]
            data = await reader.readexactly(length)

            if data.startswith(_IPC_CTRL_PREFIX):
                ctrl = data[len(_IPC_CTRL_PREFIX):].strip()
                if ctrl == _IPC_CTRL_WS_UP:
                    _confirm_live("IPC 控制消息: WS_CONNECTED")
                elif ctrl == _IPC_CTRL_WS_DOWN:
                    ws_active = False
                    logger.warning("IPC 控制消息: WS_DISCONNECTED，结束当前连接")
                    if on_status:
                        on_status(False)
                    return
                continue

            msgs = parse_frame(data)
            if msgs and not live_confirmed:
                _confirm_live("收到首条直播消息")
            for msg in msgs:
                try:
                    callback(msg)
                except Exception as e:
                    logger.debug(f"callback 异常: {e}")

    try:
        await _recv()
    except asyncio.TimeoutError:
        logger.warning(f"No IPC message received within {_TIMEOUT:.0f}s")
    except asyncio.IncompleteReadError:
        if ws_active:
            logger.warning("IPC 连接断开（直播通道已中断）")
        else:
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
