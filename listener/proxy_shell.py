"""
proxy_shell.py — 直播伴侣持久代理进程

作为直播伴侣的子进程运行，持续监听 8888 端口作为 HTTP CONNECT 代理。
对 webcast 域名做 TLS MITM，解析 WebSocket 弹幕帧并通过 TCP IPC（18998 端口）
推送给主软件。主软件随时可以连接 IPC 端口开始接收消息。

运行模式：
  proxy_shell.exe --setup    生成 CA 证书并安装到 Windows 受信任根，需管理员权限
  proxy_shell.exe            正常启动代理

日志输出到 <exe 所在目录>/proxy_shell_log/
"""

import argparse
import gzip
import json
import logging
import os
import socket
import ssl
import struct
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────

PROXY_PORT       = 8888
IPC_PORT         = 18998
WEBCAST_KEYWORDS = ("webcast",)


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def _ca_dir() -> Path:
    return Path.home() / ".livehelper"


# ─────────────────────────────────────────────
# 日志
# ─────────────────────────────────────────────

def _setup_logging() -> None:
    log_dir = _exe_dir() / "proxy_shell_log"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"proxy_shell_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.FileHandler(str(log_file), encoding="utf-8")],
    )


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# CA 证书管理
# ─────────────────────────────────────────────

_ca_key:  Optional[rsa.RSAPrivateKey]   = None
_ca_cert: Optional[x509.Certificate]   = None
_leaf_cache: dict[str, tuple[str, str]] = {}
_leaf_lock = threading.Lock()


def _load_or_create_ca() -> bool:
    global _ca_key, _ca_cert
    ca_dir = _ca_dir()
    ca_dir.mkdir(parents=True, exist_ok=True)
    cert_file = ca_dir / "proxy_shell_ca.crt"
    key_file  = ca_dir / "proxy_shell_ca.key"

    if cert_file.exists() and key_file.exists():
        try:
            with open(key_file, "rb") as f:
                _ca_key = serialization.load_pem_private_key(f.read(), password=None)
            with open(cert_file, "rb") as f:
                _ca_cert = x509.load_pem_x509_certificate(f.read())
            logger.info("CA 证书已加载")
            return True
        except Exception as e:
            logger.error(f"加载 CA 证书失败: {e}")

    logger.info("生成新 CA 证书...")
    _ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(tz=timezone.utc)
    subj = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME,         "LiveHelper Proxy CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME,   "LiveHelper"),
    ])
    _ca_cert = (
        x509.CertificateBuilder()
        .subject_name(subj).issuer_name(subj)
        .public_key(_ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=False, content_commitment=False,
            key_encipherment=False, data_encipherment=False, key_agreement=False,
            key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False,
        ), critical=True)
        .sign(_ca_key, hashes.SHA256())
    )
    with open(cert_file, "wb") as f:
        f.write(_ca_cert.public_bytes(serialization.Encoding.PEM))
    with open(key_file, "wb") as f:
        f.write(_ca_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    logger.info(f"CA 证书已保存到 {cert_file}")
    return True


def _install_ca() -> None:
    cert_file = str(_ca_dir() / "proxy_shell_ca.crt")
    if not os.path.exists(cert_file):
        logger.warning("CA 证书文件不存在，跳过安装")
        return
    r = subprocess.run(
        ["certutil", "-addstore", "-f", "ROOT", cert_file],
        capture_output=True, text=True, errors="ignore",
    )
    if r.returncode == 0:
        logger.info("CA 证书已安装到 Windows ROOT 受信任根证书")
    else:
        logger.debug(f"certutil 返回 {r.returncode}（证书可能已存在）")


def _leaf_cert_for(host: str) -> tuple[str, str]:
    """返回 (cert_pem_path, key_pem_path)，按 host 缓存。"""
    with _leaf_lock:
        if host in _leaf_cache:
            return _leaf_cache[host]

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(tz=timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(_ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(hours=1))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(host)]),
                critical=False,
            )
            .sign(_ca_key, hashes.SHA256())
        )
        cert_dir = _ca_dir() / "leafcerts"
        cert_dir.mkdir(parents=True, exist_ok=True)
        safe = host.replace("*", "star").replace(".", "_")
        cert_path = str(cert_dir / f"{safe}.crt")
        key_path  = str(cert_dir / f"{safe}.key")
        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ))
        _leaf_cache[host] = (cert_path, key_path)
        return cert_path, key_path


# ─────────────────────────────────────────────
# IPC 服务
# ─────────────────────────────────────────────

class _IPCServer:
    def __init__(self):
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind(("127.0.0.1", IPC_PORT))
        except OSError:
            logger.warning(f"IPC 端口 {IPC_PORT} 已被占用，IPC 服务未启动")
            return
        srv.listen(10)
        logger.info(f"IPC 服务监听 127.0.0.1:{IPC_PORT}")
        threading.Thread(target=self._accept_loop, args=(srv,), daemon=True).start()

    def _accept_loop(self, srv: socket.socket) -> None:
        while True:
            try:
                conn, addr = srv.accept()
                logger.info(f"IPC 客户端连接: {addr}")
                with self._lock:
                    self._clients.append(conn)
                threading.Thread(target=self._watch, args=(conn,), daemon=True).start()
            except Exception as e:
                logger.error(f"IPC accept 错误: {e}")

    def _watch(self, conn: socket.socket) -> None:
        try:
            while conn.recv(1024):
                pass
        except Exception:
            pass
        with self._lock:
            if conn in self._clients:
                self._clients.remove(conn)
        logger.info("IPC 客户端断开")

    def push(self, msg: dict) -> None:
        if not self._clients:
            return
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        dead = []
        with self._lock:
            for c in list(self._clients):
                try:
                    c.sendall(data)
                except Exception:
                    dead.append(c)
            for c in dead:
                if c in self._clients:
                    self._clients.remove(c)


# ─────────────────────────────────────────────
# Protobuf 解析（手写，无外部依赖）
# ─────────────────────────────────────────────

def _pb_varint(data: bytes, i: int) -> tuple[int, int]:
    v, s = 0, 0
    while i < len(data):
        b = data[i]; i += 1
        v |= (b & 0x7F) << s; s += 7
        if not (b & 0x80):
            return v, i
    return 0, i


def _pb_fields(data: bytes) -> dict[int, list]:
    fields: dict[int, list] = {}
    i = 0
    while i < len(data):
        try:
            tag, i = _pb_varint(data, i)
            if tag == 0:
                break
            fn, wt = tag >> 3, tag & 7
            if wt == 0:
                v, i = _pb_varint(data, i)
                fields.setdefault(fn, []).append(("v", v))
            elif wt == 1:
                if i + 8 > len(data): break
                v = struct.unpack_from("<Q", data, i)[0]; i += 8
                fields.setdefault(fn, []).append(("64", v))
            elif wt == 2:
                ln, i = _pb_varint(data, i)
                if i + ln > len(data): break
                fields.setdefault(fn, []).append(("b", data[i:i+ln])); i += ln
            elif wt == 5:
                if i + 4 > len(data): break
                v = struct.unpack_from("<I", data, i)[0]; i += 4
                fields.setdefault(fn, []).append(("32", v))
            else:
                break
        except Exception:
            break
    return fields


def _pstr(f: dict, n: int, d: str = "") -> str:
    for wt, v in f.get(n, []):
        if wt == "b":
            try: return v.decode("utf-8", errors="replace")
            except: pass
    return d


def _pint(f: dict, *ns: int, d: int = 0) -> int:
    for n in ns:
        for wt, v in f.get(n, []):
            if wt == "v": return v
    return d


def _pbytes(f: dict, n: int) -> bytes:
    for wt, v in f.get(n, []):
        if wt == "b": return v
    return b""


def _parse_user(data: bytes) -> tuple[str, str]:
    f = _pb_fields(data)
    nick = _pstr(f, 3)
    uid  = _pstr(f, 1028) or (str(_pint(f, 1)) if _pint(f, 1) else "")
    return nick, uid


def _parse_item(method: str, payload: bytes) -> Optional[dict]:
    try:
        f = _pb_fields(payload)
        if method == "WebcastChatMessage":
            nick, uid = _parse_user(_pbytes(f, 2))
            content   = _pstr(f, 3)
            if nick and content:
                return {"type": "chat", "user": nick, "user_id": uid, "content": content}

        elif method == "WebcastGiftMessage":
            if _pint(f, 9) != 1:
                return None
            nick, uid = _parse_user(_pbytes(f, 7))
            combo     = _pint(f, 6, d=1)
            gf        = _pb_fields(_pbytes(f, 15))
            gift_name = _pstr(gf, 16)
            gift_id   = _pint(gf, 5)
            if nick and gift_name:
                return {"type": "gift", "user": nick, "user_id": uid,
                        "gift": gift_name, "gift_id": gift_id,
                        "count": combo, "repeat_end": 1}

        elif method == "WebcastLikeMessage":
            nick, uid = _parse_user(_pbytes(f, 5))
            if nick:
                return {"type": "like", "user": nick, "user_id": uid, "count": _pint(f, 2, d=1)}

        elif method == "WebcastMemberMessage":
            nick, uid = _parse_user(_pbytes(f, 2))
            if nick:
                return {"type": "enter", "user": nick, "user_id": uid}

        elif method == "WebcastSocialMessage":
            nick, uid = _parse_user(_pbytes(f, 2))
            if nick:
                return {"type": "follow", "user": nick, "user_id": uid}

        elif method == "WebcastRoomUserSeqMessage":
            return {"type": "online",
                    "current": _pint(f, 3, 1),
                    "total":   _pint(f, 4, 5)}

        elif method == "WebcastFansclubMessage":
            nick, uid = _parse_user(_pbytes(f, 2))
            return {"type": "fansclub", "user": nick, "user_id": uid,
                    "content": _pstr(f, 3) or _pstr(f, 4)}

        elif method == "WebcastEmojiChatMessage":
            nick, uid = _parse_user(_pbytes(f, 2))
            return {"type": "emoji", "user": nick, "user_id": uid,
                    "emoji_id": _pstr(f, 3), "default_content": _pstr(f, 4)}

        elif method == "WebcastRoomStatsMessage":
            return {"type": "room_stats", "display_long": _pstr(f, 4)}

        elif method == "WebcastControlMessage":
            return {"type": "control", "status": _pint(f, 2, 1)}

    except Exception as e:
        logger.debug(f"parse_item [{method}]: {e}")
    return None


def _parse_frame(data: bytes) -> list[dict]:
    results = []
    try:
        f       = _pb_fields(data)
        payload = _pbytes(f, 8)
        if not payload:
            return results
        try:
            body = gzip.decompress(payload)
        except Exception:
            body = payload
        rf = _pb_fields(body)
        for wt, msg_bytes in rf.get(1, []):
            if wt != "b":
                continue
            mf     = _pb_fields(msg_bytes)
            method = _pstr(mf, 1)
            parsed = _parse_item(method, _pbytes(mf, 2))
            if parsed:
                results.append(parsed)
    except Exception as e:
        logger.debug(f"parse_frame: {e}")
    return results


# ─────────────────────────────────────────────
# WebSocket 帧读写
# ─────────────────────────────────────────────

def _recv_all(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf += chunk
    return buf


def _read_ws_frame(sock: socket.socket) -> tuple[int, bytes, bool]:
    """返回 (opcode, payload, fin)。"""
    hdr = _recv_all(sock, 2)
    fin     = bool(hdr[0] & 0x80)
    opcode  = hdr[0] & 0x0F
    masked  = bool(hdr[1] & 0x80)
    plen    = hdr[1] & 0x7F

    if plen == 126:
        plen = struct.unpack("!H", _recv_all(sock, 2))[0]
    elif plen == 127:
        plen = struct.unpack("!Q", _recv_all(sock, 8))[0]

    mask_key = _recv_all(sock, 4) if masked else b""
    payload  = bytearray(_recv_all(sock, plen))
    if masked:
        for i in range(len(payload)):
            payload[i] ^= mask_key[i % 4]
    return opcode, bytes(payload), fin


def _write_ws_frame(sock: socket.socket, opcode: int, payload: bytes, fin: bool = True) -> None:
    b0  = (0x80 if fin else 0) | opcode
    plen = len(payload)
    if plen < 126:
        hdr = bytes([b0, plen])
    elif plen <= 0xFFFF:
        hdr = bytes([b0, 126]) + struct.pack("!H", plen)
    else:
        hdr = bytes([b0, 127]) + struct.pack("!Q", plen)
    sock.sendall(hdr + payload)


# ─────────────────────────────────────────────
# WebSocket 中继（带消息解析）
# ─────────────────────────────────────────────

def _relay_ws(client: socket.socket, server: socket.socket,
              host: str, ipc: _IPCServer) -> None:
    acc: bytearray = bytearray()
    cur_opcode: int = 0

    def server_to_client() -> None:
        nonlocal acc, cur_opcode
        try:
            while True:
                opcode, payload, fin = _read_ws_frame(server)
                _write_ws_frame(client, opcode, payload, fin)
                if opcode != 0:
                    cur_opcode = opcode
                    acc = bytearray(payload)
                else:
                    acc.extend(payload)
                if fin and cur_opcode == 2:
                    for msg in _parse_frame(bytes(acc)):
                        ipc.push(msg)
                    acc = bytearray()
        except Exception as e:
            logger.debug(f"WS server→client [{host}]: {e}")

    def client_to_server() -> None:
        try:
            while True:
                opcode, payload, fin = _read_ws_frame(client)
                _write_ws_frame(server, opcode, payload, fin)
        except Exception as e:
            logger.debug(f"WS client→server [{host}]: {e}")

    t = threading.Thread(target=server_to_client, daemon=True)
    t.start()
    client_to_server()
    t.join(timeout=2)


# ─────────────────────────────────────────────
# HTTP 中继（处理 WS 升级，其余 tunnel）
# ─────────────────────────────────────────────

def _relay_http(client_tls: ssl.SSLSocket, server_tls: ssl.SSLSocket,
                host: str, ipc: _IPCServer) -> None:
    """读取 HTTP 请求并转发；检测到 101 后切换为 WS 中继；其余直接 tunnel。"""
    try:
        # 读请求头
        req_buf = b""
        while b"\r\n\r\n" not in req_buf:
            chunk = client_tls.recv(4096)
            if not chunk:
                return
            req_buf += chunk

        is_ws = b"websocket" in req_buf.lower()
        server_tls.sendall(req_buf)

        # 读响应头
        resp_buf = b""
        while b"\r\n\r\n" not in resp_buf:
            chunk = server_tls.recv(4096)
            if not chunk:
                return
            resp_buf += chunk

        client_tls.sendall(resp_buf)

        first_line = resp_buf.split(b"\r\n")[0].decode("latin-1", errors="ignore")
        try:
            status = int(first_line.split()[1])
        except Exception:
            return

        if is_ws and status == 101:
            logger.info(f"WS 连接建立: {host}")
            _relay_ws(client_tls, server_tls, host, ipc)
        else:
            _raw_tunnel(client_tls, server_tls)

    except Exception as e:
        logger.debug(f"relay_http [{host}]: {e}")


def _raw_tunnel(a: socket.socket, b: socket.socket) -> None:
    def copy(src: socket.socket, dst: socket.socket) -> None:
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass

    t = threading.Thread(target=copy, args=(b, a), daemon=True)
    t.start()
    copy(a, b)
    t.join(timeout=2)


# ─────────────────────────────────────────────
# 代理连接处理
# ─────────────────────────────────────────────

def _is_webcast(host: str) -> bool:
    return any(k in host for k in WEBCAST_KEYWORDS)


def _handle(conn: socket.socket, ipc: _IPCServer) -> None:
    server: Optional[socket.socket] = None
    try:
        conn.settimeout(30)

        # 读 CONNECT 请求
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
                return
            buf += chunk

        first_line = buf.split(b"\r\n")[0].decode("latin-1", errors="ignore")
        parts = first_line.split()
        if len(parts) < 2 or parts[0] != "CONNECT":
            return

        target = parts[1]
        host, port_str = (target.rsplit(":", 1) if ":" in target else (target, "443"))
        port = int(port_str)

        server = socket.create_connection((host, port), timeout=10)
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        conn.settimeout(None)
        server.settimeout(None)

        if _is_webcast(host):
            cert_path, key_path = _leaf_cert_for(host)
            ctx_c = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx_c.load_cert_chain(cert_path, key_path)
            try:
                client_tls = ctx_c.wrap_socket(conn, server_side=True)
            except ssl.SSLError as e:
                logger.debug(f"客户端 TLS 握手失败 [{host}]: {e}")
                return

            ctx_s = ssl.create_default_context()
            try:
                server_tls = ctx_s.wrap_socket(server, server_hostname=host)
            except ssl.SSLError as e:
                logger.debug(f"服务端 TLS 握手失败 [{host}]: {e}")
                client_tls.close()
                return

            _relay_http(client_tls, server_tls, host, ipc)
        else:
            _raw_tunnel(conn, server)

    except Exception as e:
        logger.debug(f"handle error: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
        if server:
            try:
                server.close()
            except Exception:
                pass


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────

def _setup() -> None:
    """生成 CA 证书并安装到 Windows 受信任根（需管理员权限）。"""
    print("[proxy_shell] 初始化 CA 证书...")
    _load_or_create_ca()
    _install_ca()
    print("[proxy_shell] setup 完成")


def _run() -> None:
    _setup_logging()
    logger.info("proxy_shell 启动")

    if not _load_or_create_ca():
        logger.error("CA 证书初始化失败，退出")
        return

    ipc = _IPCServer()
    ipc.start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", PROXY_PORT))
    except OSError:
        logger.info(f"端口 {PROXY_PORT} 已被占用，已有实例运行，退出")
        return

    srv.listen(128)
    logger.info(f"HTTP 代理监听 0.0.0.0:{PROXY_PORT}")

    while True:
        try:
            conn, _ = srv.accept()
            threading.Thread(target=_handle, args=(conn, ipc), daemon=True).start()
        except Exception as e:
            logger.error(f"accept: {e}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", action="store_true",
                        help="生成并安装 CA 证书（需管理员权限）")
    args = parser.parse_args()
    if args.setup:
        _setup()
    else:
        _run()


if __name__ == "__main__":
    main()
