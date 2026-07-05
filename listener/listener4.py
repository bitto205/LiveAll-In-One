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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from listener.log_util import get_listener_logger, on_connect_success
from listener.LiveProtobuf import parse_frame, try_parse_frame

logger = get_listener_logger(4)

PROXY_PORT        = 19088
IPC_PORT          = 19098
_PROXY_VALUE      = f"127.0.0.1:{PROXY_PORT},direct://"
_TIMEOUT          = 60.0
_LIVE_DATA_TIMEOUT = 10.0  # WS 建立后等待初始 PushFrame
_SHELL_PROCESS    = "proxy_shell.exe"   # process name for tasklist
_SHELL_MARKER     = "proxy_shell.exe"   # marker in patched index.js
_IPC_CTRL_PREFIX  = b"__LH_CTRL__:"
_IPC_CTRL_WS_OPEN = b"WS_OPEN"
_IPC_CTRL_WS_DATA = b"WS_CONNECTED"
_IPC_CTRL_WS_DOWN = b"WS_DISCONNECTED"
_IPC_CTRL_LIVE_ON = b"LIVE_ON_AIR:true"
_IPC_CTRL_LIVE_OFF = b"LIVE_ON_AIR:false"
_IPC_QUERY_LIVE_ON_AIR = b"__LH_QUERY__:LIVE_ON_AIR\n"
_IPC_REPLY_LIVE_PREFIX = "__LH_REPLY__:LIVE_ON_AIR:"
_HEALTH_TIMEOUT = 3.0
_HEALTH_BUFFER = 5.0
_HEALTH_POLL_INTERVAL = 0.25

_IPC_SESSION: dict = {"writer": None, "loop": None, "user_stop": False}


def request_listener_stop() -> bool:
    """请求线路 4 listener 优雅退出（关闭 IPC 连接，不波及 main）。"""
    _IPC_SESSION["user_stop"] = True
    loop = _IPC_SESSION.get("loop")
    writer = _IPC_SESSION.get("writer")
    if writer is None and loop is None:
        return False

    def _close_writer() -> None:
        if writer is not None and not writer.is_closing():
            writer.close()

    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(_close_writer)
    elif writer is not None and not writer.is_closing():
        try:
            writer.close()
        except Exception:
            pass
    return True

_AIO_DIR = Path.home() / ".liveaio"
_LEGACY_DIR = Path.home() / ".livehelper"


def _aio_data_dir() -> Path:
    """LiveAIO 用户数据目录（首次自动从旧 .livehelper 迁移）。"""
    if _AIO_DIR.exists():
        return _AIO_DIR
    if _LEGACY_DIR.exists():
        try:
            shutil.copytree(_LEGACY_DIR, _AIO_DIR)
            logger.info("已从 .livehelper 迁移到 .liveaio")
        except Exception as e:
            logger.warning("迁移 .livehelper 失败: %s", e)
            return _LEGACY_DIR
    _AIO_DIR.mkdir(parents=True, exist_ok=True)
    return _AIO_DIR


def _aio_cfg_file() -> Path:
    return _aio_data_dir() / "config.json"


# ---------------------------------------------------------
# CA 证书管理（patch 时由 Python 生成并安装）
# ---------------------------------------------------------

def _ca_paths() -> tuple[Path, Path]:
    d = _aio_data_dir()
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
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "LiveAIO"),
        x509.NameAttribute(NameOID.COMMON_NAME, "LiveAIO Proxy CA"),
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
            logger.info("CA 证书已安装到 Windows 受信任根证书")
        else:
            logger.warning(f"certutil 返回非零: {r.returncode}\n{r.stderr.decode(errors='ignore')}")
    except Exception as e:
        logger.warning(f"certutil 执行失败: {e}")


# ---------------------------------------------------------
# 路径注册（主程序启动时调用，保证 patch 能找到 exe）
# ---------------------------------------------------------

def _ipc_token_path() -> Path:
    return _aio_data_dir() / "ipc_token"


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
    cfg_file = _aio_cfg_file()
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
    cfg_file = _aio_cfg_file()
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
    cfg_file = _aio_cfg_file()
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
_PATCH_VERIFY_CACHE: dict[tuple, bool] = {}
_EXE_IDENT_CACHE: dict[tuple, bool] = {}
_INDEX_MOD_CACHE: dict[tuple, bool] = {}
_PROXY_SWITCH_RE = re.compile(
    r'(\.commandLine\.appendSwitch\s*\(\s*["\']proxy-server["\'],\s*["\'])([^"\']*?)(["\'])'
)
_SPAWN_TRACE_RE = re.compile(
    r';?\(function\(\)\{var c=require\("child_process"\);'
    r'try\{c\.spawn\("[^"\\]*(?:\\.[^"\\]*)*",\[\],\{detached:false,stdio:"ignore",windowsHide:true\}\);'
    r'\}catch\(e\)\{\}\}\(\)\);'
)
_PROXY_INJECT_TRACE_RE = re.compile(
    r'\w+\.commandLine\.appendSwitch\("proxy-server","'
    + re.escape(_PROXY_VALUE)
    + r'"\);'
)


def _invalidate_status_cache() -> None:
    from listener.status_cache import invalidate_all
    invalidate_all()
    _PATCH_VERIFY_CACHE.clear()
    _EXE_IDENT_CACHE.clear()
    _INDEX_MOD_CACHE.clear()


def _scan_install_dir_registry() -> Optional[str]:
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


def _find_install_dir() -> Optional[str]:
    from listener.status_cache import get_registry_dir
    return get_registry_dir(_scan_install_dir_registry)


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
        logger.info("[伴侣路径] 无效的手动路径已清除: %s", raw)
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
    _invalidate_status_cache()
    return True, "Companion path saved"


def clear_manual_companion_dir() -> None:
    try:
        import config as _cfg
        _cfg.set(_COMPANION_DIR_CFG, "")
    except Exception:
        pass
    _invalidate_status_cache()


def sync_companion_dir_from_registry() -> bool:
    """Prefer registry path and clear manual override when present."""
    reg = _find_install_dir()
    if not reg:
        return False
    if _read_manual_companion_dir_cfg():
        clear_manual_companion_dir()
        logger.info("[伴侣路径] 注册表路径优先: %s", reg)
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
    from listener.status_cache import get_index_js_path

    def _resolve() -> Optional[str]:
        root = get_companion_install_dir()
        if not root:
            return None
        return find_index_js_in_root(root)

    return get_index_js_path(_resolve)


# ---------------------------------------------------------
# index.js / exe 检测
# ---------------------------------------------------------

def _patch_catalog_path() -> Path:
    return _aio_data_dir() / "index_patch_catalog.json"


def _deployed_shell_path() -> Optional[str]:
    path = find_index_js()
    if not path:
        return None
    return os.path.join(os.path.dirname(path), _SHELL_PROCESS)


def _index_file_sig(path: str) -> Optional[tuple]:
    try:
        return (path, os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        return None


def _build_spawn_code(dest: str) -> str:
    js_path = dest.replace("\\", "\\\\")
    return (
        f';(function(){{var c=require("child_process");'
        f'try{{c.spawn("{js_path}",[],{{detached:false,stdio:"ignore",windowsHide:true}});}}'
        f"catch(e){{}}}}());"
    )


def _load_patch_catalog() -> Optional[dict]:
    cat_file = _patch_catalog_path()
    if not cat_file.is_file():
        return None
    try:
        data = json.loads(cat_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _save_patch_catalog(catalog: dict) -> None:
    cat_file = _patch_catalog_path()
    cat_file.parent.mkdir(parents=True, exist_ok=True)
    cat_file.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _clear_patch_catalog() -> None:
    try:
        cat_file = _patch_catalog_path()
        if cat_file.is_file():
            cat_file.unlink()
    except Exception:
        pass


def _apply_patch(content: str, dest: str, index_path: str) -> tuple[Optional[str], dict, str]:
    """Inject patch snippets and return patched text plus catalog metadata."""
    new_content = content
    catalog: dict = {
        "index_path": os.path.normpath(index_path),
        "spawn": _build_spawn_code(dest),
    }

    m = _PROXY_SWITCH_RE.search(new_content)
    if m:
        catalog["proxy_mode"] = "replace"
        catalog["proxy_prev_value"] = m.group(2)
        new_content = _PROXY_SWITCH_RE.sub(
            rf"\g<1>{_PROXY_VALUE}\g<3>", new_content, count=1
        )
    else:
        ready_re = re.compile(r"(\b(\w+)\.on\s*\(\s*['\"]ready['\"])")
        rm = ready_re.search(new_content)
        if not rm:
            return None, {}, "No suitable injection point found in index.js"
        app_var = rm.group(2)
        proxy_inject = (
            f'{app_var}.commandLine.appendSwitch("proxy-server","{_PROXY_VALUE}");'
        )
        catalog["proxy_mode"] = "inject"
        catalog["proxy_inject"] = proxy_inject
        new_content = (
            new_content[: rm.start()] + proxy_inject + new_content[rm.start() :]
        )

    spawn = catalog["spawn"]
    idx = new_content.find("proxy-server")
    if idx >= 0:
        line_start = new_content.rfind(";", 0, idx) + 1
        new_content = new_content[:line_start] + spawn + new_content[line_start:]

    ok_m = re.search(r",!\w+\.ok\)", new_content)
    if ok_m:
        catalog["ok_before"] = ok_m.group(0)
    new_content, ok_n = re.subn(r",!\w+\.ok\)", ",false)", new_content, count=1)
    if ok_n == 0:
        catalog.pop("ok_before", None)

    return new_content, catalog, ""


def _remove_patch(content: str, catalog: dict) -> str:
    """Remove only catalog-recorded patch injections from index.js."""
    out = content
    spawn = catalog.get("spawn", "")
    if spawn:
        out = out.replace(spawn, "", 1)

    mode = catalog.get("proxy_mode")
    if mode == "inject":
        inj = catalog.get("proxy_inject", "")
        if inj:
            out = out.replace(inj, "", 1)
    elif mode == "replace":
        prev = catalog.get("proxy_prev_value")
        if prev is not None:
            restore_re = re.compile(
                r'(\.commandLine\.appendSwitch\s*\(\s*["\']proxy-server["\'],\s*["\'])'
                + re.escape(_PROXY_VALUE)
                + r'(["\'])'
            )
            out = restore_re.sub(rf"\g<1>{prev}\g<2>", out, count=1)

    ok_before = catalog.get("ok_before")
    if ok_before:
        out = out.replace(",false)", ok_before, 1)
    return out


def _strip_patch_traces(content: str) -> str:
    """Heuristically remove patch injections (legacy or leftover traces)."""
    out = content
    out = _SPAWN_TRACE_RE.sub("", out, count=1)
    out = _PROXY_INJECT_TRACE_RE.sub("", out, count=1)
    if _PROXY_VALUE in out:
        proxy_restore_re = re.compile(
            r'(\.commandLine\.appendSwitch\s*\(\s*["\']proxy-server["\'],\s*["\'])'
            + re.escape(_PROXY_VALUE)
            + r'(["\'])'
        )
        out = proxy_restore_re.sub(r"\g<1>direct://\g<2>", out, count=1)
    return out


def _content_has_patch_traces(content: str) -> bool:
    markers = (f"127.0.0.1:{PROXY_PORT}", _SHELL_MARKER)
    if any(m in content for m in markers):
        return True
    return bool(_SPAWN_TRACE_RE.search(content))


def _prepare_index_for_patch(content: str, index_path: str) -> str:
    """Strip existing patch injections before applying a fresh patch."""
    catalog = _load_patch_catalog()
    if catalog and os.path.normcase(catalog.get("index_path", "")) == os.path.normcase(index_path):
        content = _remove_patch(content, catalog)
    return _strip_patch_traces(content)


def _strip_index_patch_traces(content: str, index_path: str) -> str:
    """Remove catalog patch and any remaining traces from index.js."""
    catalog = _load_patch_catalog()
    if catalog and os.path.normcase(catalog.get("index_path", "")) == os.path.normcase(index_path):
        content = _remove_patch(content, catalog)
    return _strip_patch_traces(content)


def _verify_patch_in_content(text: str, catalog: dict, dest: str) -> bool:
    """Strictly verify only catalog-recorded injections are present."""
    spawn = catalog.get("spawn")
    if not spawn or spawn not in text:
        return False
    if spawn != _build_spawn_code(dest):
        return False

    mode = catalog.get("proxy_mode")
    if mode == "inject":
        inj = catalog.get("proxy_inject")
        if not inj or inj not in text:
            return False
    elif mode == "replace":
        m = _PROXY_SWITCH_RE.search(text)
        if not m or m.group(2) != _PROXY_VALUE:
            return False
    else:
        return False

    if catalog.get("ok_before") and ",false)" not in text:
        return False
    return True


def _file_has_patch_markers(path: str) -> bool:
    try:
        content = open(path, encoding="utf-8", errors="ignore").read()
    except Exception:
        return False
    return _content_has_patch_traces(content)


def is_index_js_patched() -> bool:
    """Whether index.js strictly matches saved patch catalog injections."""
    path = find_index_js()
    dest = _deployed_shell_path()
    if not path or not dest:
        return False
    catalog = _load_patch_catalog()
    if not catalog:
        return False
    if os.path.normcase(catalog.get("index_path", "")) != os.path.normcase(path):
        return False
    try:
        cat_mtime = 0.0
        cat_file = _patch_catalog_path()
        if cat_file.is_file():
            cat_mtime = cat_file.stat().st_mtime
        sig = (path, os.path.getmtime(path), os.path.getsize(path), cat_mtime, dest)
        if sig in _PATCH_VERIFY_CACHE:
            return _PATCH_VERIFY_CACHE[sig]
        text = open(path, encoding="utf-8", errors="ignore").read()
        result = _verify_patch_in_content(text, catalog, dest)
        _PATCH_VERIFY_CACHE[sig] = result
        return result
    except Exception:
        return False


def is_index_js_modified() -> bool:
    """Whether index.js is patched or still contains patch markers."""
    if is_index_js_patched():
        return True
    path = find_index_js()
    if not path:
        return False
    sig = _index_file_sig(path)
    if sig is not None and sig in _INDEX_MOD_CACHE:
        return _INDEX_MOD_CACHE[sig]
    result = _file_has_patch_markers(path)
    if sig is not None:
        _INDEX_MOD_CACHE[sig] = result
    return result


def is_exe_identical_to_source(deployed: Optional[str] = None) -> bool:
    """Whether deployed proxy_shell.exe matches bundled source bytes."""
    src = _shell_source()
    dest = deployed or _deployed_shell_path()
    if not src or not dest or not os.path.isfile(dest):
        return False
    try:
        sig = (
            src,
            os.path.getmtime(src),
            os.path.getsize(src),
            dest,
            os.path.getmtime(dest),
            os.path.getsize(dest),
        )
        if sig in _EXE_IDENT_CACHE:
            return _EXE_IDENT_CACHE[sig]
        result = filecmp.cmp(src, dest, shallow=False)
        _EXE_IDENT_CACHE[sig] = result
        return result
    except OSError:
        return False


def is_patched() -> bool:
    """Patch 可用：exe 一致且 index.js 注入与 catalog 严格一致。"""
    return is_exe_identical_to_source() and is_index_js_patched()


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

    if is_patched():
        return True, "Already patched"

    try:
        original = open(path, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        return False, f"读取 index.js 失败: {e}"

    original = _prepare_index_for_patch(original, path)

    new_content, catalog, err = _apply_patch(original, dest, path)
    if new_content is None:
        return False, err

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

    _save_patch_catalog(catalog)
    _install_ca_cert()
    _invalidate_status_cache()
    return True, "Patch successful. Restart companion app to take effect."


def unpatch_companion() -> tuple[bool, str]:
    """Remove patch injections and leftover traces from index.js."""
    path = find_index_js()
    if not path:
        return False, "Companion install not found"

    catalog = _load_patch_catalog()
    has_traces = _file_has_patch_markers(path)
    if not catalog and not has_traces:
        return False, "当前未 patch，无需 Unpatch"

    try:
        content = open(path, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        return False, f"读取 index.js 失败: {e}"

    restored = _strip_index_patch_traces(content, path)
    if _content_has_patch_traces(restored):
        return False, "仍有 patch 痕迹未能完全清除"

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(restored)
    except Exception as e:
        return False, f"写入 index.js 失败: {e}"

    _clear_patch_catalog()
    _invalidate_status_cache()
    return True, "已移除 patch 注入内容"


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
    """Check whether LiveAIO CA is installed in Windows ROOT store."""
    cert_path, _ = _ca_paths()
    if not cert_path.exists():
        return False
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$s = Get-ChildItem Cert:\\LocalMachine\\Root | "
             "Where-Object { $_.Subject -like '*LiveAIO*' -or $_.Subject -like '*LiveHelper*' }; "
             "($s.Count -gt 0)"],
            capture_output=True, timeout=10, encoding="utf-8", errors="ignore",
        )
        return "True" in r.stdout
    except Exception:
        return False


def _is_proxy_running() -> bool:
    """Check whether proxy_shell.exe process is running."""
    from listener.status_cache import get_proxy_running

    def _scan() -> bool:
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {_SHELL_PROCESS}", "/NH"],
                capture_output=True, timeout=2, encoding="utf-8", errors="ignore",
            )
            return _SHELL_PROCESS.lower() in r.stdout.lower()
        except Exception:
            return False

    return get_proxy_running(_scan)


def is_proxy_shell_running() -> bool:
    return _is_proxy_running()


async def _tcp_port_open(host: str, port: int, *, timeout: float = _HEALTH_TIMEOUT) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def check_proxy_shell_health() -> tuple[bool, str]:
    """Check proxy_shell process and TCP ports, retry up to 5s."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _HEALTH_BUFFER
    last_detail = "proxy_shell 进程未运行"
    while True:
        if not _is_proxy_running():
            last_detail = "proxy_shell 进程未运行"
        elif not await _tcp_port_open("127.0.0.1", IPC_PORT, timeout=0.8):
            last_detail = "IPC 端口未监听"
        elif not await _tcp_port_open("127.0.0.1", PROXY_PORT, timeout=0.8):
            last_detail = "代理 TCP 端口未监听"
        else:
            return True, ""
        if loop.time() >= deadline:
            return False, last_detail
        await asyncio.sleep(_HEALTH_POLL_INTERVAL)


async def query_live_on_air() -> tuple[Optional[bool], str]:
    """Ask Go proxy_shell whether live room WS data channel is active."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", IPC_PORT),
            timeout=_HEALTH_TIMEOUT,
        )
    except Exception as e:
        return None, f"IPC 连接失败: {e}"

    try:
        token = _ipc_token_path().read_text(encoding="ascii").strip()
        writer.write(token.encode("ascii") + b"\n")
        writer.write(_IPC_QUERY_LIVE_ON_AIR)
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=_HEALTH_TIMEOUT)
    except Exception as e:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        return None, f"IPC 开播查询失败: {e}"

    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass

    text = line.decode("ascii", errors="ignore").strip()
    if text == f"{_IPC_REPLY_LIVE_PREFIX}true":
        return True, ""
    if text == f"{_IPC_REPLY_LIVE_PREFIX}false":
        return False, ""
    return None, f"IPC 开播查询响应异常: {text!r}"


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
    index_patched = is_index_js_patched()
    index_modified = is_index_js_modified()
    patched_ok = exe_identical and index_patched
    shell_exe = _load_shell_exe()
    return {
        **path_fields,
        "is_patched":          patched_ok,
        "index_patched":       index_patched,
        "index_modified":      index_modified,
        "exe_identical":       exe_identical,
        "exe_in_place":        bool(shell_exe and os.path.isfile(shell_exe)),
        "patch_needed":        bool(path_fields["index_js_found"]) and not patched_ok,
    }


def get_page_status(*, force: bool = False) -> dict:
    """Return route 4 UI status from current checks."""
    from listener.status_cache import get_route4_status
    return get_route4_status(_build_page_status, force=force)


def run_page_check() -> dict:
    """Run route 4 page check and log current patch status."""
    status = get_page_status(force=True)
    ca_installed = _is_ca_installed() if status["is_patched"] else False
    logger.info(
        "[线路4 页面检测] 注册表=%s | 伴侣=%s | index.js=%s | 已注入=%s | "
        "exe一致=%s | index已注入=%s | 证书已安装=%s",
        status["companion_in_registry"], status["companion_installed"],
        status["index_js_found"],
        status["is_patched"], status["exe_identical"],
        status["index_patched"],
        ca_installed,
    )
    return status


def get_route4_connect_check() -> dict:
    """Run route 4 pre-connect diagnostics."""
    shell_exe = _load_shell_exe()
    exe_known = bool(shell_exe and os.path.isfile(shell_exe))

    main_location_registered = False
    try:
        cfg = json.loads(_aio_cfg_file().read_text(encoding="utf-8"))
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
        "[线路4 连接前检测] exe已知=%s | 主程序已注册=%s | "
        "路径不一致=%s | 进程运行中=%s",
        exe_known, main_location_registered, mismatch, exe_running,
    )
    if mismatch:
        logger.warning(
            "检测到 proxy_shell.exe 路径不一致，建议重新执行 Patch"
        )
    if not main_location_registered:
        logger.warning("主程序路径缺失或已变更，请重启应用以刷新保存的路径")
    if not exe_running:
        logger.warning(
            "进程列表中未检测到 proxy_shell.exe（应由直播伴侣启动）"
        )

    return result


async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
) -> None:
    """Connect to proxy_shell IPC and forward parsed messages to callback."""
    logger.info("=== 线路4 开始连接 ===")
    if not is_patched():
        logger.error("直播伴侣尚未注入，请先执行 Patch")
        if on_status:
            on_status(False)
        return

    healthy, detail = await check_proxy_shell_health()
    if not healthy:
        logger.error(f"proxy_shell未启动或异常 ({detail})")
        if on_status:
            on_status(False)
        return

    on_air, qerr = await query_live_on_air()
    if qerr:
        logger.error(f"proxy_shell未启动或异常 ({qerr})")
        if on_status:
            on_status(False)
        return
    if not on_air:
        logger.warning("直播间未开播")
        if on_status:
            on_status(False)
        return

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", IPC_PORT),
            timeout=10,
        )
    except Exception as e:
        logger.error(f"proxy_shell未启动或异常 (IPC 连接失败: {e})")
        if on_status:
            on_status(False)
        return

    # Send auth token for IPC handshake.
    try:
        token = _ipc_token_path().read_text(encoding="ascii").strip()
        writer.write(token.encode("ascii") + b"\n")
        await writer.drain()
    except Exception as e:
        logger.error(f"发送 IPC 令牌失败: {e}")
        writer.close()
        if on_status:
            on_status(False)
        return

    logger.info(f"已连接 proxy_shell IPC（端口 {IPC_PORT}），等待 WebSocket…")

    _IPC_SESSION["user_stop"] = False
    _IPC_SESSION["loop"] = asyncio.get_running_loop()
    _IPC_SESSION["writer"] = writer

    ws_active = False
    connected = False

    async def _read_packet(timeout: float | None) -> bytes:
        if timeout is not None:
            hdr = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
        else:
            hdr = await reader.readexactly(4)
        length = struct.unpack(">I", hdr)[0]
        return await reader.readexactly(length)

    async def _recv() -> None:
        nonlocal ws_active
        ws_seen = False
        live_confirmed = False
        loop = asyncio.get_running_loop()

        def _confirm_live() -> None:
            nonlocal live_confirmed, connected
            ws_active = True
            if live_confirmed:
                return
            live_confirmed = True
            connected = True
            on_connect_success("listener4")
            logger.info("✅ 直播间正在直播")
            if on_status:
                on_status(True)

        class _WsDisconnected(Exception):
            pass

        def _handle_live_off() -> None:
            nonlocal ws_active, connected
            ws_active = False
            notify = connected or live_confirmed or ws_seen
            connected = False
            logger.warning("直播间已下播")
            if notify and on_status:
                on_status(False)
            raise _WsDisconnected()

        def _handle_data(data: bytes) -> None:
            nonlocal ws_seen, ws_active, live_confirmed
            if data.startswith(_IPC_CTRL_PREFIX):
                ctrl = data[len(_IPC_CTRL_PREFIX):].strip()
                if ctrl == _IPC_CTRL_LIVE_ON:
                    ws_active = True
                    if not live_confirmed:
                        _confirm_live()
                elif ctrl == _IPC_CTRL_LIVE_OFF:
                    _handle_live_off()
                elif ctrl == _IPC_CTRL_WS_OPEN:
                    if not ws_seen:
                        ws_seen = True
                        logger.info(
                            f"IPC: WebSocket 已建立，{_LIVE_DATA_TIMEOUT:.0f}s 内等待初始直播数据…"
                        )
                    ws_active = True
                elif ctrl == _IPC_CTRL_WS_DATA:
                    ws_active = True
                    if not live_confirmed:
                        _confirm_live()
                elif ctrl == _IPC_CTRL_WS_DOWN:
                    logger.warning("IPC: WebSocket 已断开")
                    _handle_live_off()
                return

            channel_ok, msgs = try_parse_frame(data)
            if channel_ok and not live_confirmed:
                _confirm_live()
            for msg in msgs:
                try:
                    callback(msg)
                except Exception as e:
                    logger.debug(f"回调异常: {e}")

        # 阶段 1：等待 WebSocket 建立（无超时）
        while not ws_seen:
            data = await _read_packet(None)
            try:
                _handle_data(data)
            except _WsDisconnected:
                return

        # 阶段 2：WS 已建立，10s 内等待可解析的初始 PushFrame
        deadline = loop.time() + _LIVE_DATA_TIMEOUT
        while not live_confirmed:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            data = await _read_packet(remaining)
            try:
                _handle_data(data)
            except _WsDisconnected:
                return

        # 阶段 3：持续收消息
        while True:
            data = await _read_packet(None)
            try:
                _handle_data(data)
            except _WsDisconnected:
                return

    try:
        await _recv()
    except asyncio.TimeoutError:
        logger.warning("直播间未开播")
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
        _IPC_SESSION["writer"] = None
        _IPC_SESSION["loop"] = None
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        if on_status and connected and not _IPC_SESSION["user_stop"]:
            on_status(False)
        _IPC_SESSION["user_stop"] = False
