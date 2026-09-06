# LiveAIO Core IPC Protocol v0.1

> 总契约：[`CONTRACT.md`](CONTRACT.md)  
> C++ 对接：[`CPP_UI_CONTRACT.md`](CPP_UI_CONTRACT.md)  
> 本文只放 **JSON 样例** 与字段对照表。常量以 [`protocol.go`](protocol.go) 为准。

## Process topology

```text
LiveAIO.exe (C++ LoadLibrary)
  ├── LiveAIOCore.dll   Go hub / TCP :19877 / 托盘 / 线路 / 工具业务
  ├── LiveAIOPages.dll  C++ pages（JSONL 客户端）
  └── LiveAIOTools.dll  C++ tools（JSONL 客户端）
```

- Go Core 是协议与 config owner；不创建业务窗口（托盘除外）。
- Pages/Tools 是 attach-only UI，关窗不断 Core。

## Transport

| Mode | Address | When |
|------|---------|------|
| Named Pipe (optional) | `\\.\pipe\liveaio-core` | 预留 |
| TCP（主路径） | `127.0.0.1:19877` | UI 连接 |

**Framing:** UTF-8 **JSON Lines**（每行一个 JSON，`\n` 结尾）。

**Handshake:** 接受连接后写 `ready`；hub 再写 `capabilities`（含 `features`）与当前 `status`。客户端可发 `ping` → `pong`。

## Envelope

```json
{"op": "<command_or_event_name>", ...}
```

可选：`id`（关联请求/响应）、`ts`（unix ms）。

---

## Commands（UI → Core）

### ping

```json
{"op":"ping","id":"1"}
```

### shutdown

```json
{"op":"shutdown"}
```

### connect / disconnect

```json
{"op":"connect","live_id":"123","route":"4","force_system":false}
{"op":"disconnect"}
```

### status（拉取当前态，点对点回）

```json
{"op":"status"}
```

### frame.push（线路 2/3/4）

```json
{"op":"frame.push","payload_b64":"<base64 of raw WS frame bytes>"}
```

### message.ingest（线路 1：已解析，不 echo `message`）

```json
{"op":"message.ingest","type":"gift","user":"u","user_id":"1","gift":"小心心","count":1}
```

### config.set / config.get

```json
{"op":"config.set","key":"overtime.settings","value":{}}
{"op":"config.get","key":"overtime.settings","id":"2"}
{"op":"config.get"}
```

无 `key` 的 `config.get` 返回整表。

### tool.*

```json
{"op":"tool.overtime.set","settings":{}}
{"op":"tool.overtime.cmd","cmd":"reset"}
{"op":"tool.overtime.sim_gift","gift":"小心心","count":1}
{"op":"tool.leaf.set","settings":{"rules":[{"gift":"小心心","mode":"add","value":1,"min":0,"max":0}]}}
{"op":"tool.leaf.sim_gift","gift":"小心心","count":1}
{"op":"leaf.spawn","gift":"小心心","count":1,"user":"LiveAIO"}
{"op":"tool.danmu.set","settings":{}}
{"op":"tool.memo.set","settings":{}}
{"op":"tool.demand","tool":"danmu","active":true}
{"op":"tool.demand","tool":"danmu","active":false}
```

### ui.command

```json
{"op":"ui.command","action":"login.query"}
{"op":"ui.command","action":"login.start"}
{"op":"ui.command","action":"route.env","route":"4"}
{"op":"ui.command","action":"route4.patch"}
{"op":"ui.command","action":"ui.show"}
{"op":"ui.command","action":"quit.detach_ui"}
{"op":"ui.command","action":"quit.shutdown_all"}
```

---

## Events（Core → UI）

### ready

```json
{"op":"ready","version":"0.1.0","protocol_version":"0.1.0"}
```

### capabilities

```json
{
  "op":"capabilities",
  "capabilities":{
    "protocol_version":"0.1.0",
    "ui_owner":"go_host",
    "config_owner":"go_core",
    "listener_owner":"listener_boundary",
    "supports_routes":["1","2","3","4"],
    "tool_events":["tick","ledger","danmu.show","memo.item"],
    "tool_commands":["tool.overtime.set","tool.overtime.cmd","tool.overtime.sim_gift","tool.danmu.set","tool.memo.set","tool.demand","ui.command","config.set","config.get"]
  },
  "features":{
    "themes":["dark","light"],
    "supports_tray":true,
    "supports_detach_ui":true,
    "supports_shutdown":true,
    "supports_tools_host":true
  }
}
```

### ui.focus

托盘「打开界面」或第二次启动时广播；已在运行的 Pages 收到后抬起既有窗口，不再开新进程。

```json
{"op":"ui.focus"}
```

### pong / error

```json
{"op":"pong","id":"1"}
{"op":"error","code":"unsupported","msg":"..."}
```

### status

```json
{"op":"status","connected":true,"route":"4","live_id":"123","driver":"shellipc","health":"ok","force_system":false}
```

### config.ok / config.value

```json
{"op":"config.ok","key":"overtime.settings","value":{}}
{"op":"config.value","key":"overtime.settings","value":{},"exists":true}
{"op":"config.value","values":{"overtime.settings":{}}}
```

### message（字段以 Go schema 为准）

```json
{"op":"message","type":"chat","user":"Alice","user_id":"1","content":"hi"}
{"op":"message","type":"gift","user":"Bob","user_id":"2","gift":"小心心","gift_id":5655,"count":1,"repeat_end":1}
{"op":"message","type":"like","user":"C","user_id":"3","count":5}
{"op":"message","type":"follow","user":"D","user_id":"4","action":1,"share_type":0,"share_target":"","follow_count":0}
{"op":"message","type":"control","status":3}
```

### login.state / route.env

```json
{"op":"login.state","text":"✅ 已登录","can_login":false}
{"op":"route.env","route":"4","ready":true}
```

### tick / ledger / danmu.show / memo.item

```json
{"op":"tick","remaining_seconds":120,"running":true}
{"op":"ledger","entries":[{"user":"Bob","user_id":"2","seconds":30}]}
{"op":"danmu.show","kind":"chat","user":"A","text":"hi"}
{"op":"memo.item","kind":"gift","user":"B","text":"送出小心心 x1","stack_key":"gift:B:小心心"}
```

---

## Core message `type` 字段

| `type` | 主要字段 |
|--------|----------|
| chat | user, user_id, content |
| gift | user, user_id, gift, gift_id, count, repeat_end |
| like | user, user_id, count |
| enter | user, user_id |
| follow | user, user_id, action, share_type, share_target, follow_count |
| fansclub | user, user_id, content |
| online | current, total |
| control | status |
| room_enter | status, room_status, title, id_str |
| emoji | user, user_id, emoji_id, default_content |
| room_stats | display_* , total, display_type |
| rank | ranks |

常量：开播 enter status `2`，结束 `4`，control 结束 `3`。

---

## Example session

```json
{"op":"ready","version":"0.1.0","protocol_version":"0.1.0"}
{"op":"capabilities","capabilities":{"protocol_version":"0.1.0"},"features":{"supports_tray":true}}
{"op":"status","connected":false}
{"op":"ping","id":"1"}
{"op":"pong","id":"1"}
{"op":"config.set","key":"danmu.settings","value":{"danmu_chat_on":true}}
{"op":"config.ok","key":"danmu.settings","value":{"danmu_chat_on":true}}
{"op":"shutdown"}
```

## Binary location

生产：`build/build_work/custom/`（或版本目录）下的 `LiveAIO.exe` + `LiveAIOCore.dll` + Pages/Tools dll。  
调试：`go build ./main` 或 F5。
