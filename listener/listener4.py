"""
listener4.py 鈥?绾胯矾 4锛歱atch 鐩存挱浼翠荆 + proxy_shell IPC 鏀跺脊骞曘€?"""
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
_TIMEOUT          = 60.0          # 绛夊緟棣栨潯 IPC 娑堟伅鐨勮秴鏃讹紙绉掞級
_SHELL_PROCESS    = "proxy_shell.exe"   # tasklist 妫€娴嬬敤杩涚▼鍚?_SHELL_MARKER     = "proxy_shell.exe"   # 妫€娴?patch 涓槸鍚﹀凡娉ㄥ叆 spawn
_IPC_CTRL_PREFIX  = b"__LH_CTRL__:"
_IPC_CTRL_WS_UP   = b"WS_CONNECTED"
_IPC_CTRL_WS_DOWN = b"WS_DISCONNECTED"


# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# CA 璇佷功绠＄悊锛坧atch 鏃剁敱 Python 鐢熸垚骞跺畨瑁咃級
# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def _ca_paths() -> tuple[Path, Path]:
    d = Path.home() / ".livehelper"
    return d / "proxy_shell_ca.crt", d / "proxy_shell_ca.key"


def _ensure_ca_cert() -> Path:
    """鑻?CA 璇佷功涓嶅瓨鍦ㄥ垯鐢?cryptography 搴撶敓鎴愶紝杩斿洖 .crt 璺緞銆?""
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
    logger.info(f"CA 璇佷功宸茬敓鎴? {cert_path}")
    return cert_path


def _install_ca_cert() -> None:
    """灏?CA 璇佷功瀹夎鍒?Windows ROOT 淇′换瀛樺偍锛堥渶瑕佺鐞嗗憳鏉冮檺锛夈€?""
    cert_path = _ensure_ca_cert()
    try:
        r = subprocess.run(
            ["certutil", "-addstore", "-f", "ROOT", str(cert_path)],
            capture_output=True, timeout=30,
        )
        if r.returncode == 0:
            logger.info("CA 璇佷功宸插畨瑁呭埌 Windows ROOT")
        else:
            logger.warning(f"certutil 杩斿洖闈為浂: {r.returncode}\n{r.stderr.decode(errors='ignore')}")
    except Exception as e:
        logger.warning(f"certutil 寮傚父: {e}")


# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# 璺緞娉ㄥ唽锛堜富杞欢鍚姩鏃惰皟鐢紝璁?patch 鑳芥壘鍒?exe锛?# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def _ipc_token_path() -> Path:
    return Path.home() / ".livehelper" / "ipc_token"


def _refresh_ipc_token() -> str:
    """姣忔涓昏蒋浠跺惎鍔ㄦ椂閲嶆柊鐢熸垚 IPC token锛屽啓鍏ョ鐩樺悗杩斿洖銆?""
    token = secrets.token_hex(32)
    p = _ipc_token_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(token, encoding="ascii")
    return token


def _shell_source() -> Optional[str]:
    """婧?proxy_shell.exe锛歭istener4.py 鐨勫悓绾х洰褰曪紝闅忎富杞欢鍙戝竷銆?""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy_shell.exe")
    return p if os.path.isfile(p) else None


def _load_shell_exe() -> Optional[str]:
    """
    璇诲彇 patch 鏃堕儴缃插埌鐩存挱浼翠荆鐩綍鐨?proxy_shell.exe 璺緞銆?    config.json 閲岀殑 proxy_shell_exe 鐢?patch_companion() 鍐欏叆銆?    """
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
    """patch_companion() 閮ㄧ讲瀹屾瘯鍚庯紝鎶婄洰鏍囪矾寰勫啓鍏?config.json銆?""
    cfg_file = Path.home() / ".livehelper" / "config.json"
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["proxy_shell_exe"] = dest
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def save_location() -> None:
    """灏嗕富杞欢鐩綍鍐欏叆 ~/.livehelper/config.json锛屽埛鏂?IPC token銆?""
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
    logger.debug(f"涓昏蒋浠剁洰褰曞凡娉ㄥ唽: {exe_dir}")


# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# 鐩存挱浼翠荆璺緞鏌ユ壘
# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

_COMPANION_DIR_CFG = "companion_install_dir"


def _find_install_dir() -> Optional[str]:
    """娉ㄥ唽琛ㄦ娴嬬洿鎾即渚ｅ畨瑁呯洰褰曘€?""
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
                                    if "鐩存挱浼翠荆" not in name:
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
    """
    璇诲彇骞舵牎楠屾墜鍔ㄨ矾寰勩€傝嫢鐩綍涓嶅瓨鍦ㄦ垨缂哄皯 index.js锛岃涓烘棤鏁堝苟娓呴櫎 config銆?
    Returns:
        (鏈夋晥璺緞鎴?None, 鏄惁鍒氬垽瀹氫负鏃犳晥)
    """
    raw = _read_manual_companion_dir_cfg()
    if not raw:
        return None, False
    p = os.path.normpath(raw)
    if not os.path.isdir(p) or not find_index_js_in_root(p):
        clear_manual_companion_dir()
        logger.info("[鐩存挱浼翠荆璺緞] 鎵嬪姩璺緞鏃犳晥锛堟棤 index.js 鎴栫洰褰曚笉瀛樺湪锛夛紝宸叉竻闄? %s", raw)
        return None, True
    return p, False


def get_manual_companion_dir() -> Optional[str]:
    path, _ = validate_manual_companion_dir()
    return path


def set_manual_companion_dir(path: str) -> tuple[bool, str]:
    """鐢ㄦ埛鎵嬪姩鎸囧畾鐩存挱浼翠荆鏍圭洰褰曪紝鍐欏叆 config.json銆?""
    path = os.path.normpath(path.strip().rstrip("\\/"))
    if not path or not os.path.isdir(path):
        return False, "鐩綍鏃犳晥"
    if not find_index_js_in_root(path):
        return False, "璇ユ寚瀹氱洰褰曟棤鏁?
    try:
        import config as _cfg
        _cfg.set(_COMPANION_DIR_CFG, path)
    except Exception as e:
        return False, f"淇濆瓨璺緞澶辫触: {e}"
    return True, "鐩存挱浼翠荆璺緞宸蹭繚瀛?


def clear_manual_companion_dir() -> None:
    try:
        import config as _cfg
        _cfg.set(_COMPANION_DIR_CFG, "")
    except Exception:
        pass


def sync_companion_dir_from_registry() -> bool:
    """
    娉ㄥ唽琛ㄦ娴嬪埌鐩存挱浼翠荆鏃讹紝娓呴櫎鎵嬪姩鎸囧畾璺緞锛屼互娉ㄥ唽琛ㄧ洰褰曚负鍑嗐€?    杩斿洖鏄惁鍛戒腑娉ㄥ唽琛ㄣ€?    """
    reg = _find_install_dir()
    if not reg:
        return False
    if _read_manual_companion_dir_cfg():
        clear_manual_companion_dir()
        logger.info("[鐩存挱浼翠荆璺緞] 娉ㄥ唽琛ㄤ紭鍏堬紝宸叉竻闄ゆ墜鍔ㄨ矾寰勶紙娉ㄥ唽琛? %s锛?, reg)
    return True


def get_companion_install_dir() -> Optional[str]:
    """浼樺厛娉ㄥ唽琛紝鍚﹀垯浣跨敤鐢ㄦ埛鎵嬪姩鎸囧畾鐨勮矾寰勩€?""
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
    """杩斿洖鐩存挱浼翠荆 index.js 鐨勫畬鏁磋矾寰勩€傛敮鎸?Launcher 澶氱増鏈洰褰曠粨鏋勩€?""
    root = get_companion_install_dir()
    if not root:
        return None
    return find_index_js_in_root(root)


# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# index.js / exe 妫€娴?# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

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
    """绾胯矾 3锛歩ndex.js 鏄惁鐩稿 .bak 琚敼鍐欙紙浠绘剰宸紓锛夈€?""
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
    """绾胯矾 4锛氶儴缃茬殑 proxy_shell.exe 鏄惁涓庡彂甯冨寘鍐呮簮鏂囦欢瀛楄妭绾т竴鑷淬€?""
    src = _shell_source()
    dest = deployed or _deployed_shell_path()
    if not src or not dest or not os.path.isfile(dest):
        return False
    return filecmp.cmp(src, dest, shallow=False)


def _build_patched_content(original: str, dest: str) -> tuple[Optional[str], str]:
    """
    浠庡師濮?index.js 鐢熸垚瀹屾暣 patch 鏂囨湰锛堣鐩栧紡锛氬缁堜互 .bak 涓哄熀鍑嗭紝涓嶅閲忓彔鍔狅級銆?    """
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
            return None, "鏈壘鍒板悎閫傜殑娉ㄥ叆鐐癸紝index.js 缁撴瀯鍙兘宸插彉鏇?
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
    """绾胯矾 4锛氭牴鎹?.bak 璁＄畻鏈熸湜鐨?index.js 鍏ㄦ枃锛堢簿纭尮閰嶇敤锛夈€?""
    path, bak = _index_js_paths()
    if not path or not bak or not os.path.exists(bak):
        return None
    dest = os.path.join(os.path.dirname(path), _SHELL_PROCESS)
    content, err = _build_patched_content(
        open(bak, encoding="utf-8", errors="ignore").read(),
        dest,
    )
    if content is None:
        logger.warning(f"鏃犳硶鐢熸垚鏈熸湜 patch 鏂囨湰: {err}")
        return None
    return content


def is_index_js_exactly_patched() -> bool:
    """绾胯矾 4锛氬綋鍓?index.js 鏄惁涓庢湡鏈?patch 鏂囨湰瀹屽叏涓€鑷达紙涓嶈兘澶氬啓涓嶈兘灏戝啓锛夈€?""
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
    """绾胯矾 4 涓ユ牸 patch 鐘舵€侊細exe 涓€鑷?+ index.js 绮剧‘鍖归厤銆?""
    return is_exe_identical_to_source() and is_index_js_exactly_patched()


# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# Patch / Unpatch
# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def patch_companion() -> tuple[bool, str]:
    """
    鍚戠洿鎾即渚?index.js 娉ㄥ叆锛?      1. 鎶?proxy_shell.exe 浠?listener/ 鎷疯礉鍒?index.js 鍚岀骇鐩綍
      2. spawn 鎷疯礉鍚庣殑 proxy_shell.exe
      3. --proxy-server 鍙傛暟锛堟祦閲忚蛋 8888锛?      4. 瀹屾暣鎬ф牎楠岀粫杩?      5. 鐢熸垚骞跺畨瑁?CA 璇佷功
    """
    path = find_index_js()
    if not path:
        return False, "鏈壘鍒扮洿鎾即渚ｅ畨瑁呯洰褰?

    src = _shell_source()
    if not src:
        return False, "鏈壘鍒版簮 proxy_shell.exe锛坙istener/ 鐩綍锛夛紝璇风‘璁よ蒋浠跺畬鏁存€?

    dest = os.path.join(os.path.dirname(path), _SHELL_PROCESS)
    bak = path + ".bak"

    if not os.path.exists(bak):
        try:
            shutil.copy2(path, bak)
        except Exception as e:
            return False, f"澶囦唤 index.js 澶辫触: {e}"

    try:
        original = open(bak, encoding="utf-8", errors="ignore").read()
    except Exception as e:
        return False, f"璇诲彇澶囦唤 index.js 澶辫触: {e}"

    new_content, err = _build_patched_content(original, dest)
    if new_content is None:
        return False, err

    if is_index_js_exactly_patched() and is_exe_identical_to_source(dest):
        return True, "宸茬粡鏄?patch 鐘舵€?

    try:
        shutil.copy2(src, dest)
        logger.info(f"proxy_shell.exe 宸查儴缃插埌: {dest}")
    except Exception as e:
        return False, f"鎷疯礉 proxy_shell.exe 澶辫触: {e}"

    _save_deployed_exe(dest)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:
        return False, f"鍐欏叆 index.js 澶辫触: {e}"

    _install_ca_cert()
    return True, "Patch 鎴愬姛锛佽閲嶅惎鐩存挱浼翠荆浣胯缃敓鏁?


def unpatch_companion() -> tuple[bool, str]:
    path = find_index_js()
    if not path:
        return False, "鏈壘鍒扮洿鎾即渚?
    bak = path + ".bak"
    if not os.path.exists(bak):
        return False, "鏈壘鍒板浠芥枃浠?
    try:
        shutil.copy2(bak, path)
        return True, "宸茶繕鍘熷師濮?index.js"
    except Exception as e:
        return False, f"杩樺師澶辫触: {e}"


def check_path_mismatch() -> bool:
    """妫€鏌?index.js 涓敞鍏ョ殑 proxy_shell.exe 璺緞鏄惁涓庡綋鍓嶈矾寰勪竴鑷淬€?""
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


# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
# 杩愯鏃惰瘖鏂?# 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€

def _is_ca_installed() -> bool:
    """妫€鏌?LiveHelper CA 璇佷功鏄惁宸插畨瑁呭埌 Windows ROOT 淇′换瀛樺偍銆?""
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
    """妫€娴?proxy_shell.exe 鏄惁鍦ㄨ繘绋嬪垪琛ㄤ腑銆?""
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
    """绾胯矾 3/4 鍏变韩锛氫即渚ｈ矾寰勬娴嬶紙鍚墜鍔ㄨ矾寰勬棤鏁堝垽瀹氾級銆?""
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
    """缁勮绾胯矾 4 椤甸潰鐘舵€侊紙涓嶈鏃ュ織锛夈€?""
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
    """杩涘叆绾胯矾 4 瀛愰〉闈㈡椂璋冪敤锛堟湭杩炴帴鐩存挱闂存椂锛夈€傛娴?patch 鐘舵€佸苟璁板綍鏃ュ織銆?""
    status = _build_page_status()
    logger.info(
        "[绾胯矾4 椤甸潰妫€娴媇 娉ㄥ唽琛?%s | 浼翠荆鐩綍=%s | index.js=%s | 涓ユ牸patch=%s | "
        "exe涓€鑷?%s | index绮剧‘=%s | 璇佷功宸茶=%s",
        status["companion_in_registry"], status["companion_installed"],
        status["index_js_found"],
        status["is_patched"], status["exe_identical"], status["index_exact"],
        status["ca_installed"],
    )
    return status


def get_page_status() -> dict:
    """绾胯矾 4 UI 鐘舵€侊紙璇诲彇褰撳墠 patch / exe / index 妫€娴嬶級銆?""
    return _build_page_status()


def get_route4_connect_check() -> dict:
    """
    杩炴帴鐩存挱闂村墠璋冪敤锛屽仛鍙屽悜璺緞 + 杩涚▼璇婃柇骞跺啓 log銆?
    Keys:
        exe_known_to_main        listener4 鑳芥壘鍒?proxy_shell.exe
        main_location_registered config.json 涓?exe_dir 涓庡綋鍓?main 璺緞涓€鑷?        path_mismatch            index.js 涓敞鍏ヨ矾寰勪笌褰撳墠 exe 璺緞涓嶇
        exe_running              proxy_shell.exe 杩涚▼姝ｅ湪杩愯
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
        "[绾胯矾4 杩炴帴璇婃柇] exe璺緞宸茬煡=%s | main浣嶇疆宸叉敞鍐?%s | "
        "璺緞鍙樺姩=%s | 杩涚▼杩愯=%s",
        exe_known, main_location_registered, mismatch, exe_running,
    )
    if mismatch:
        logger.warning(
            "proxy_shell.exe 璺緞宸插彉鍔紙杞欢鐩綍琚Щ鍔紵锛夛紝寤鸿閲嶆柊 Patch 鐩存挱浼翠荆"
        )
    if not main_location_registered:
        logger.warning("涓昏蒋浠朵綅缃湭娉ㄥ唽鎴栧凡鍙樺姩锛屽缓璁噸鍚富杞欢浠ユ洿鏂拌矾寰?)
    if not exe_running:
        logger.warning(
            "proxy_shell.exe 鏈娴嬪埌杩愯涓紙鐩存挱浼翠荆鍚姩鍚庝細鑷姩 spawn锛?
            "鑻ヤ即渚ｅ凡寮€鍚妫€鏌?patch 鐘舵€侊級"
        )

    return result


async def start_listener(
    callback: Callable,
    on_status: Optional[Callable] = None,
) -> None:
    """
    杩炴帴 proxy_shell 鐨?IPC 绔彛锛?8998锛夛紝鎺ユ敹鍘熷 PushFrame 瀛楄妭甯э紙4瀛楄妭闀垮害鍓嶇紑锛夛紝
    鏈湴 protobuf 瑙ｆ瀽鍚庤浆鍙戠粰 callback銆?0 绉掑唴鏃犻鏉℃秷鎭垯瓒呮椂銆?    """
    logger.info("=== 绾胯矾 4 杩炴帴鍚姩 ===")
    if not is_patched():
        logger.error("鐩存挱浼翠荆鏈?patch锛岃鍏堟墽琛?Patch 鎿嶄綔")
        if on_status:
            on_status(False)
        return

    check = get_route4_connect_check()
    if not check["exe_running"]:
        logger.error("proxy_shell.exe 鏈繍琛岋紝鏃犳硶寤虹珛 IPC 杩炴帴")
        if on_status:
            on_status(False)
        return

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", IPC_PORT),
            timeout=10,
        )
    except Exception as e:
        logger.error(f"杩炴帴 IPC 绔彛 {IPC_PORT} 澶辫触: {e}锛坧roxy_shell 鏄惁宸查殢鐩存挱浼翠荆鍚姩锛燂級")
        if on_status:
            on_status(False)
        return

    # 鍙戦€?token 瀹屾垚韬唤楠岃瘉锛圙o 鍦?3 绉掑唴鏍￠獙锛?    try:
        token = _ipc_token_path().read_text(encoding="ascii").strip()
        writer.write(token.encode("ascii") + b"\n")
        await writer.drain()
    except Exception as e:
        logger.error(f"IPC token 鍙戦€佸け璐? {e}")
        writer.close()
        if on_status:
            on_status(False)
        return

    logger.info(f"宸茶繛鎺?proxy_shell IPC锛坽IPC_PORT}锛夛紝绛夊緟寮瑰箷娑堟伅鈥︼紙{_TIMEOUT:.0f}s 瓒呮椂锛?)

    async def _recv() -> None:
        first = True
        ws_active = False
        while True:
            # 4-byte big-endian length prefix (with timeout only before first message)
            if first:
                hdr = await asyncio.wait_for(reader.readexactly(4), timeout=_TIMEOUT)
            else:
                hdr = await reader.readexactly(4)
            length = struct.unpack(">I", hdr)[0]
            data = await reader.readexactly(length)

            # Go 主动上报的控制消息（非 protobuf PushFrame）
            if data.startswith(_IPC_CTRL_PREFIX):
                ctrl = data[len(_IPC_CTRL_PREFIX):].strip()
                if ctrl == _IPC_CTRL_WS_UP:
                    ws_active = True
                    logger.info("IPC 控制消息: WS_CONNECTED")
                elif ctrl == _IPC_CTRL_WS_DOWN:
                    ws_active = False
                    logger.warning("IPC 控制消息: WS_DISCONNECTED，结束当前连接")
                    if on_status:
                        on_status(False)
                    return
                continue

            msgs = parse_frame(data)
            for msg in msgs:
                if first:
                    first = False
                    ws_active = True
                    on_connect_success("listener4")
                    logger.info("鉁?棣栨潯寮瑰箷宸插埌杈撅紝IPC 閫氶亾姝ｅ父锛屽紑濮嬭浆鍙?)
                    if on_status:
                        on_status(True)
                try:
                    callback(msg)
                except Exception as e:
                    logger.debug(f"callback 寮傚父: {e}")

    try:
        await _recv()
    except asyncio.TimeoutError:
        logger.warning(f"{_TIMEOUT:.0f}s 鍐呮湭鏀跺埌娑堟伅锛岃纭鐩存挱浼翠荆宸查噸鍚苟寮€鎾?)
    except asyncio.IncompleteReadError:
        if ws_active:
            logger.warning("IPC 连接断开（直播通道已中断）")
        else:
            logger.info("IPC 连接已断开")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"IPC 鎺ユ敹寮傚父: {e}")
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        if on_status:
            on_status(False)
