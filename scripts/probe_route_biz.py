#!/usr/bin/env python3
"""Probe route 1/2: connect live_id and require business tool events (memo/danmu)."""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from collections import Counter
from typing import Any


def recv_lines(sock: socket.socket, buf: bytearray, timeout: float) -> list[dict[str, Any]]:
    sock.settimeout(timeout)
    out: list[dict[str, Any]] = []
    while True:
        try:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
            while True:
                idx = buf.find(b"\n")
                if idx < 0:
                    break
                line = bytes(buf[:idx]).strip()
                del buf[: idx + 1]
                if not line:
                    continue
                try:
                    out.append(json.loads(line.decode("utf-8")))
                except json.JSONDecodeError:
                    continue
        except socket.timeout:
            break
    return out


def send(sock: socket.socket, obj: dict[str, Any]) -> None:
    sock.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def safe(s: Any) -> str:
    return str(s).encode("utf-8", "replace").decode("utf-8", "replace")


def log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode(sys.stdout.encoding or "utf-8", "replace").decode(
            sys.stdout.encoding or "utf-8", "replace"
        ), flush=True)


def summarize(pkts: list[dict[str, Any]]) -> Counter:
    c: Counter = Counter()
    for p in pkts:
        op = str(p.get("op", ""))
        if op == "message":
            c[f"message:{p.get('type', '?')}"] += 1
        elif op in ("memo.item", "danmu.show"):
            c[f"{op}:{p.get('kind', '?')}"] += 1
        elif op == "status":
            c[f"status:connected={p.get('connected')}"] += 1
        elif op == "error":
            c[f"error:{p.get('code')}:{p.get('msg')}"] += 1
        else:
            c[op] += 1
    return c


def probe(route: str, live_id: str, wait_s: float, host: str, port: int) -> int:
    print(f"\n=== probe route={route} live_id={live_id} wait={wait_s}s ===", flush=True)
    sock = socket.create_connection((host, port), timeout=5)
    buf = bytearray()
    all_pkts: list[dict[str, Any]] = []

    # Drain hello
    all_pkts.extend(recv_lines(sock, buf, 1.5))

    for tool in ("memo", "danmu"):
        send(sock, {"op": "tool.demand", "tool": tool, "active": True})
    # Nested settings (flat keys zero-out filters — see Normalize*Settings).
    send(
        sock,
        {
            "op": "tool.memo.set",
            "settings": {
                "memo.gift.enabled": True,
                "memo.gift.stack": True,
                "memo.gift.min_diamonds": 0,
                "memo.follow.enabled": True,
                "memo.like.enabled": True,
                "memo.like.stack": True,
            },
        },
    )
    send(
        sock,
        {
            "op": "tool.danmu.set",
            "settings": {
                "danmu_chat_on": True,
                "danmu_gift_on": True,
                "danmu_gift_min_diamonds": 0,
                "danmu_follow_on": True,
                "danmu_like_on": True,
                "danmu_like_threshold": 1,
            },
        },
    )
    all_pkts.extend(recv_lines(sock, buf, 0.5))

    send(
        sock,
        {
            "op": "connect",
            "route": route,
            "live_id": live_id,
            "force_system": False,
        },
    )

    deadline = time.time() + wait_s
    connected = False
    biz: list[dict[str, Any]] = []
    while time.time() < deadline:
        pkts = recv_lines(sock, buf, min(1.0, max(0.1, deadline - time.time())))
        all_pkts.extend(pkts)
        for p in pkts:
            op = p.get("op")
            if op == "status" and p.get("connected") is True:
                connected = True
                log(f"[+] status connected route={p.get('route')}")
            elif op == "error":
                log(f"[!] error code={p.get('code')} msg={safe(p.get('msg'))}")
            elif op in ("memo.item", "danmu.show"):
                biz.append(p)
                log(
                    f"[+] BIZ {op} kind={p.get('kind')} "
                    f"user={safe(p.get('user'))} text={safe(p.get('text'))}"
                )
            elif op == "message":
                log(f"[.] message type={p.get('type')} user={safe(p.get('user'))}")
        if len(biz) >= 3:
            # enough proof
            break

    try:
        send(sock, {"op": "disconnect"})
        all_pkts.extend(recv_lines(sock, buf, 1.0))
        for tool in ("memo", "danmu"):
            send(sock, {"op": "tool.demand", "tool": tool, "active": False})
    except OSError as e:
        log(f"[!] cleanup socket error (ignored): {e}")
    try:
        sock.close()
    except OSError:
        pass

    counts = summarize(all_pkts)
    memo_n = sum(v for k, v in counts.items() if k.startswith("memo.item:"))
    danmu_n = sum(v for k, v in counts.items() if k.startswith("danmu.show:"))
    msg_n = sum(v for k, v in counts.items() if k.startswith("message:"))
    # Also count biz collected even if summarize missed due to abrupt close.
    if not memo_n:
        memo_n = sum(1 for p in biz if p.get("op") == "memo.item")
    if not danmu_n:
        danmu_n = sum(1 for p in biz if p.get("op") == "danmu.show")
    log(f"counts: {dict(counts)}")
    log(
        f"summary: connected={connected} memo.item={memo_n} "
        f"danmu.show={danmu_n} raw_message={msg_n}"
    )

    # Pass only when a business consumer got data.
    if memo_n > 0 or danmu_n > 0 or biz:
        log(f"PASS route {route}: business tool received data")
        return 0
    log(
        f"FAIL route {route}: no memo.item/danmu.show "
        f"(connected={connected}, raw_message={msg_n})"
    )
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", required=True, choices=("1", "2", "4"))
    ap.add_argument("--live-id", default="huwaifa168")
    ap.add_argument("--wait", type=float, default=90.0)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=19877)
    args = ap.parse_args()
    live_id = args.live_id
    if args.route == "4":
        live_id = ""
    try:
        return probe(args.route, live_id, args.wait, args.host, args.port)
    except OSError as e:
        print(f"FAIL route {args.route}: cannot talk to core: {e}", flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
