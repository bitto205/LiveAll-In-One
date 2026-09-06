# LiveAIO 契约：进程 / 通信 / 工具 UI / 归属

> 单一真源。协议常量与 schema 以 Go [`protocol.go`](protocol.go) / [`models.go`](models.go) 为准；本文与 [`protocol_examples.md`](protocol_examples.md) 只做说明。  
> C++ pages/tools 对接细则见 [`CPP_UI_CONTRACT.md`](CPP_UI_CONTRACT.md)。  
> **原则：** Go（Core DLL）是协议 / config / 工具业务 / 线路编排 owner；C++ 只做 pages + tools UI。

---

### 1.0 目录布局：F5 / 产物 / 安装包（勿混用）

| 布局 | `LIVEAIO_ROOT` | 二进制 | `resources/` / `browsers/` |
|------|----------------|--------|----------------------------|
| **Repo / F5** | 仓库根（推荐显式设 env） | `{root}/build/build_work/custom/` | 仓库根下的 `resources/`、`browsers/` |
| **产物 flat** | exe 目录（`custom/` 或 `<ver>/`） | 与 exe 同目录 | **须**有 `resources/`；`browsers/` 可选（`-Browsers`） |
| **NSIS 安装** | `$INSTDIR` | 同目录 flat | 同左 |

**判定（[`paths.go`](paths.go)）：**

- `isPackagedRoot` = 存在 `LiveAIOCore.dll` **且** `resources/`。仅有 `browsers/` **不算**完整包（否则会钉死在不完整的 `custom/`，礼物/皮肤全挂）。
- 非完整包则向上走到仓库根（`go.mod` + `main` + `resources`）。
- DLL 查找 `FindArtifact` / Pages·Tools 共用顺序：包内 flat → exe 旁 → `build_work/custom` → `build_work/<ver>`（名降序）。

构建：[`scripts/build.ps1`](../scripts/build.ps1) **每次**把 `resources/` 同步进产物目录，保证 `custom/` 可作为完整包启动。

**Qt 进程：** `QApplication` 只跑在 `LiveAIO.exe --pages` 子进程里。Core/Go **禁止** `LoadLibrary(LiveAIOPages.dll)`（同址空间会 `winthrow`/`0xc0000005`）。托盘在旁路 `LockOSThread`。进程存活由 Supervisor 等待循环拥有。

---

## 1. 进程与通信

```text
LiveAIO.exe / go main     壳（Supervisor）：生命周期、提权、单实例、进程退出
  └── Core（同进程或 DLL）  总线：hub / IPC / 线路 / 工具业务；托盘=模块
        └──（懒）子进程 LiveAIO.exe --pages
              ├── LiveAIOPages.dll   Qt 主界面
              └── LiveAIOTools.dll   工具窗（由 Pages 加载）
```

| 角色 | 负责 | 不负责 |
|------|------|--------|
| **壳**（`scripts/host` / `main/`） | 提权、环境、`LoadLibrary` Core、单实例、**进程退出权**；`--pages` 只跑 Qt | 礼物规则、线路解析 |
| **Core Supervisor** | hub/IPC、线路、配置、托盘、按需 `StartPagesChild` | 不当「托盘进程」；不进程内嵌 Qt |
| **Pages/Tools** | UI（独立进程）；关窗/`shutdown` 经 IPC 通知 Supervisor | 不拥有 Core 进程 |

**懒加载：** 无 UI 请求时不起 Pages 子进程；Supervisor 在 `<-uiReq` / `<-ctx.Done()` / 子进程退出上活着。

调试：`go build ./main` → 同 Supervisor。生产：`LiveAIO.exe` 提权后 → `LiveAIO_CoreMain` → `SupervisorRun`。

| 场景 | 谁在跑 |
|------|--------|
| 关界面（detach） | Core + 托盘模块；Pages 事件循环仍可在（视实现）或已退 |
| 线路 1/2 | Core 同进程 chromedp |
| 线路 3 | Core：`proxy_shell` + 临时系统代理 + Shell IPC |
| 线路 4 | Core `PrepareR4` + `shellipc` |
| 工具 / 页面开着 | Core + Pages/Tools |

| 项 | 约定 |
|----|------|
| 传输 | JSONL；TCP `127.0.0.1:19877`（[`DefaultTCPAddr`](protocol.go)） |
| 信封 | 每行 JSON，必有 `op` |
| 配置写者 | **仅 Go core** 读写 `config.json` 与 schema |
| UI 退出 | 关窗 = 断 IPC，**不**杀 Core；显式退出才 `shutdown` / `ui.command` `quit.shutdown_all` |

握手（[`server.go`](server.go) + [`hub.go`](hub.go)）：

1. TCP 连接建立 → Core 发 `ready`
2. Hub `addConn` → 发 `capabilities`（含 `features`）→ 发当前 `status`

### 1.1 UI → Core（命令）

与 `hub.handle` / `handleUICommand` 对齐：

| op | 谁发 | 作用 |
|----|------|------|
| `ping` | pages/tools | 探活 → `pong` |
| `shutdown` | 显式退出 | 停 Core |
| `connect` / `disconnect` | pages | 会话（`route` / `live_id` / `force_system`） |
| `status` | pages | 拉取当前连接态（点对点回） |
| `frame.push` | 线路 2/3/4（或同进程回调等价） | `payload_b64` → Go 解析 → `message` + 工具事件 |
| `message.ingest` | 线路 1（已解析） | **只**跑工具业务，**不** echo `message` |
| `tool.overtime.set` / `cmd` / `sim_gift` | OvertimeTool | 规则 / 控制 / 模拟送礼 |
| `tool.leaf.set` / `sim_gift` | LeafTool | 礼物→叶子规则 / 模拟送礼 |
| `tool.danmu.set` | DanmuTool | 过滤开关 |
| `tool.memo.set` | MemoTool | 过滤开关 |
| `tool.demand` | tools | 消费者门控：`tool`=`leaf\|danmu\|overtime\|memo`，`active` bool；0→1 / 1→0 |
| `config.set` / `config.get` | pages/tools | 统一读写 config |
| `ui.command` | pages | 登录 / 线路环境 / patch / 退出类动作（见下） |

`ui.command` 的 `action`（[`hub_ui.go`](hub_ui.go)）：

| action | 回执 op |
|--------|---------|
| `login.query` / `login.start` | `login.state` |
| `route.env` | `route.env` |
| `route4.set_companion_path` / `route4.patch` / `route4.unpatch` / `route3.unpatch` | `route.env` |
| `ui.show` | 广播 `ui.focus` + 必要时加载 Pages，`status` ack |
| `quit.detach_ui` | `status` ack |
| `quit.shutdown_all` | `ready` + `bye` 后停 Core |

### 1.2 Core → UI（事件）

| op | 消费方 | 作用 |
|----|--------|------|
| `ready` / `pong` / `error` | 握手 / 日志 | 连接与错误 |
| `capabilities` | pages/tools | 协议能力 + `features` |
| `status` | pages → 工具 | 连接态 |
| `message` | pages 广播；**工具不做二次业务过滤** | 字段以 Go schema 为准 |
| `config.ok` | 发起 `config.set` 的客户端 | 写入成功回执（`key` / `value`） |
| `config.value` | 发起 `config.get` 的客户端 | 单 key 或整表 `values` |
| `login.state` / `route.env` | pages | 登录文案 / 线路环境 |
| `tick` | OvertimeTool | `remaining_seconds`, `running` |
| `ledger` | OvertimeTool | 用户台账 |
| `danmu.show` | DanmuTool | 已过滤条目 |
| `memo.item` | MemoTool | 已过滤条目 |
| `leaf.spawn` | LeafTool | 礼物匹配后的叶子增减（`count` 可为负） |

`ui.focus`（`OpFocusUI`）：托盘「打开界面」或第二次启动时由 hub 广播；已运行的 Pages 抬起既有窗口。
第二个进程只发 `ui.command` `ui.show` 然后退出，绝不自己加载 Pages。

样例 JSON 见 [`protocol_examples.md`](protocol_examples.md)。

### 1.3 线路数据路径（无双解析）

| 线路 | 抓帧/会话 | 解析 | 工具业务 |
|------|-----------|------|----------|
| 2 / 3 / 4 | Go listener → `OnFrame` / Shell | **仅 Go** `TryParseFrame` | Go |
| 1 | Go chromedp JS hook → `OnMessage` | 线路侧已映射 | Go（`message.ingest` 语义，不 echo） |

禁止：同一帧在 UI 或第二套解析器再 parse 一次进工具。

### 1.4 C++ UI 懒加载与 overlay

Pages/Tools 的懒加载矩阵、同 Qt 实例内的独立透明 overlay 双窗、Async-by-default 规则见 [`CPP_UI_CONTRACT.md`](CPP_UI_CONTRACT.md)（§懒加载矩阵、§透明 overlay、§Async-by-default、§消费者边界）。

### 1.5 生命周期隔离（有消费者 100% / 无消费者 0%）

| 规则 | 约定 |
|------|------|
| 消费者边界 | leaf/danmu/overtime：`panel`（设置窗）∪ `overlay`（悬浮窗，含最小化）；memo：仅 panel |
| 清零条件 | 该工具 `!panel && !overlay` 才 `tryRelease` / Core `Stop`；**关设置留悬浮不得杀** |
| 拉起 | 进设置页异步 `ensure`；「打开悬浮窗」按钮与托盘 `OverlayCommand` 同样 `ensure` |
| 预备态 | 同进程待命（不提前挂 Root / 不跑物理与气泡绘制）；`show` 才渲染 |
| Core 门控 | UI 发 `tool.demand`（`tool` + `active`）；`afterMessage` / overtime ticker 仅对 Active 引擎工作 |
| 礼物 | 无展示需求不预载；按需异步 icon；无消费者清 pixmap；Go catalog 按需 Load |
| IPC | `Conn` 读写有 deadline；坏连接踢掉；`Server.Stop` 关连接且有界等待 |
| 锁 | mutex 内不广播 / 不 CDP / 不 `Send`；队列满不反压 CDP `ListenTarget` |

---

## 2. 工具：业务 vs UI

### 2.1 归属

| | Go core（业务） | C++ `tools/*`（UI） |
|--|----------------|---------------------|
| 加班机 | 规则换算、tick、ledger、sim | 设置表单、悬浮窗皮肤/几何、显示剩余秒 |
| 弹幕机 | 类型开关、钻石/点赞阈值、累计 | 气泡窗、皮肤、后缀展示 |
| 备忘录 | 过滤、堆叠键 | 列表、清空、自定义行、皮肤 |
| 捡叶子 | 礼物规则→叶子增减（`leaf.spawn`） | 物理悬浮窗、规则卡、垃圾桶交互 |

**禁止**在 C++ 再实现礼物/弹幕业务过滤。工具只消费 `tick` / `ledger` / `danmu.show` / `memo.item` / `leaf.spawn`，只发 `tool.*.set|cmd|sim_gift`。

设置变更：

```text
Qt 控件 → config.set →（可选）tool.*.set → core 业务生效
```

模拟送礼：`tool.overtime.sim_gift` / `tool.leaf.sim_gift`（不走本地假业务路径）。

### 2.2 设置字段（推给 core 的 JSON）

与 [`Normalize*Settings`](models.go) 对齐。

**danmu**（`tool.danmu.set` → `settings`）

- `danmu_chat_on`, `danmu_gift_on`, `danmu_gift_min_diamonds`
- `danmu_follow_on`, `danmu_like_on`, `danmu_like_threshold`, `danmu_like_accumulate`

**memo**（`tool.memo.set` → `settings`）

- `memo.gift.enabled`, `memo.gift.stack`, `memo.gift.min_diamonds`
- `memo.follow.enabled`, `memo.like.enabled`, `memo.like.stack`

**overtime**（`tool.overtime.set` → `settings`）

- `hours`, `minutes`, `seconds`
- `rules[]`: `{gift, mode: add|sub|random, unit: s|m|h, value, min, max}`  
  （中文「加/减/随机」「秒/分/时」由 Go 归一化）

**leaf**（`tool.leaf.set` → `settings`）

- `rules[]`: `{gift, mode: add|sub|random, value, min, max}`（最多 10 条）  
  - `gift`：礼物图鉴里的**唯一礼物名**（与消息侧礼物名精确匹配；`Null`/空忽略）  
  - 图鉴同时以 **gift_id + 名字** 标识礼物；规则配置从目录选礼物，匹配按名字  
  - `mode`：`add` 加叶、`sub` 减叶、`random` 用 `min`/`max`；`count` 可为负（`leaf.spawn`）

---

## 3. Ownership

| 层 | 负责 |
|----|------|
| 壳 `scripts/host` / `main/` | 进程生命周期、提权、环境、加载 Core；调试与正式同职责 |
| Go `core` Supervisor | 协议、config、工具业务、托盘**模块**、按需 Pages、hub |
| Go `core/dll` | `LiveAIO_CoreMain` → Supervisor |
| Go `listener` | 线路输入边界与 proto 解码；不写 UI |
| C++ `pages` | 连接页 / 设置 / 登录与线路环境展示 |
| C++ `tools` | 悬浮工具窗与皮肤绘制 |

---

## 4. 目录布局（平面内聚）

### 4.1 仓库根保持干净（约定）

**根目录尽量不放东西；优先归档进已有目录，禁止为杂项新建顶层文件夹。**

| 允许留在仓库根 | 原因 |
|----------------|------|
| `go.mod` / `go.sum` | Go **模块根**（不能放进 `main/`） |
| `LICENSE` / `README.md` | 对外入口 |
| `.gitignore` / `.clangd` | 工具约定在仓库根 |
| `.git/` / `.cursor/` / `.vscode/` | 工具目录 |
| 包目录：`core/` `listener/` `main/` `pages/` `tools/` `util/` `resources/` `scripts/` `build/` | 已有结构 |

| 勿放根目录 | 归档到 |
|------------|--------|
| `go.work` / `go.work.sum` | [`main/go.work`](../main/go.work)（本地 workspace；根用 `/go.work` 忽略） |
| 资源致谢 | [`resources/CREDITS.md`](../resources/CREDITS.md) |
| `compile_commands.json` | 仅 `build/cmake/all/`（根 `.clangd` 已指 `CompilationDatabase: build/cmake/all`） |
| 构建/脚本杂项 | `scripts/` |
| 共享 C++/小工具 | `util/` |
| 运行时 `config.json` / `state.json` / `log/` / `browsers/` | 可出现在 **LIVEAIO_ROOT**（F5=仓库根或安装目录），**不入库** |

新增文件前先问：能否放进 `scripts/` / `util/` / `main/` / `resources/` / `core/`？能则不进根、不新建顶层目录。

### 4.2 包树

```text
main/                 调试用 Go exe 入口；go.work（workspace）
core/                 协议 / hub / config / 工具业务 / 托盘 / UAC / Pages DLL 拉起
core/dll/             LiveAIOCore.dll 导出入口（c-shared）
listener/             线路采集
tools/                C++ Qt 工具源码
pages/                C++ Qt 页面源码
util/                 共享 C++ 控件等
scripts/              build.ps1 / CMake / NSIS / host
build/                唯一构建产物与 CMake 缓存（gitignore 大部）
resources/
  image/              应用图标
  gift/               图鉴 JSON + icon/
  skin/               皮肤 JSON
  cpp/                C++ 读接口（gift.cpp / skin.cpp；勿与 .go 同目录）
  CREDITS.md          第三方素材致谢
```

| 路径 | 归属 |
|------|------|
| `resources/gift/` | 图鉴；Go `resources/gift.go` + C++ `resources/cpp/gift.cpp` |
| `resources/skin/` | 皮肤；Go `skin.go` 路径辅助 + C++ `resources/cpp/skin.cpp` |
| `resources/image/` | 托盘/窗口图标 |

---

## 5. 验收清单

- [ ] 新业务状态只加在 Go，不在 UI 侧加倒计时/过滤
- [ ] 新 UI 事件有 `op` 名，先写入 `protocol.go`，再同步本文 + `protocol_examples.md` + `CPP_UI_CONTRACT.md`
- [ ] UI 只通过 IPC 收 core 事件；无第二套 op 名、无直写 `config.json`
- [ ] 静态资源只放 `resources/`
