# main — 调试用进程壳

与正式 `LiveAIO.exe` 同职责：**生命周期 / 提权（可 `--no-admin`）/ 驱动 Core Supervisor**。  
不是业务 owner；hub、线路、工具规则在 Core，界面在 Pages/Tools。

| 文件 | 作用 |
|------|------|
| `main.go` | `core.SupervisorRun` 入口 |
| `go.work` | 可选 Go workspace；仓库根执行时设 `GOWORK=main/go.work` |
| `*.exe` | 本地 `go build` 产物（已 gitignore） |

## 为什么 `go.mod` 还在仓库根？

Go 模块根必须覆盖 `core/`、`listener/` 等包；`go.mod` 不能放进 `main/`。

正式产物：`scripts/build.ps1` → `LiveAIO.exe`（壳）+ `LiveAIOCore.dll`（Supervisor/总线）。
