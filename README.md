# LiveAll-In-One

LiveAll-In-One（LiveAIO）是一个面向抖音直播的桌面辅助工具。它可以连接直播间、接收弹幕与互动消息，并提供直播备忘录等辅助功能。

> **说明**：`main` 分支是较早的稳定快照，功能相对精简。完整功能与持续开发请使用 [`dev`](https://github.com/bitto205/LiveAll-In-One/tree/dev) 分支。

## 功能概览

- 连接抖音网页直播间，实时接收弹幕、礼物、点赞、关注、进场等消息
- 支持两种监听线路（线路一 / 线路二，见下方说明）
- 提供**直播备忘录**：将重要互动整理为可处理清单
- 支持颜色主题切换
- 支持关闭窗口后缩小到系统托盘

## 环境要求

- Windows 10 / 11
- Python 3.11+

## 安装与运行

```bash
# 克隆仓库并切换到 main 分支
git clone https://github.com/bitto205/LiveAll-In-One.git
cd LiveAll-In-One
git checkout main

# 创建虚拟环境（推荐）
python -m venv .venv
.venv\Scripts\activate

# 安装依赖
pip install PySide6 playwright

# 安装 Playwright 浏览器内核
playwright install chromium

# 启动应用
python main.py
```

首次运行前，Playwright 会将浏览器下载到项目目录下的 `browsers/` 文件夹（由 `main.py` 自动配置）。

## 第一次使用

1. 启动软件，进入**主页**。
2. 点击**登录**，在弹出的浏览器中完成抖音账号登录。
3. 填写直播间 ID 并保存。
4. 选择监听线路，点击**连接**。
5. 连接成功后，进入**工具**页面打开需要的辅助工具。

建议使用小号或非直播号登录。连接过程中尽量避免用该账号进入其他直播间，以免影响消息采集。

## 监听线路

### 线路一

基于 JS Hook 的网页直播间监听方案，通过 Playwright 注入脚本从页面内存读取消息。需要登录抖音账号。

### 线路二

网页直播间监听的另一套方案，同样需要登录。界面中保留了线路二入口；若当前 `main` 分支缺少对应实现文件，请改用线路一，或切换到 `dev` 分支。

## 工具

### 直播备忘录

将直播中值得留意的互动自动整理成清单，适合边播边处理：

- 礼物、关注、点赞、粉丝团等事件可分别开关
- 礼物可按最低钻石数过滤
- 同类礼物 / 同一用户的点赞支持叠加计数
- 支持手动添加自定义备忘

## 设置

- **颜色主题**：切换软件配色
- **关闭时缩小到托盘**：开启后，关闭主窗口不会退出程序，可从托盘重新打开

## 项目结构

```
main.py              # 应用入口
main_page.py         # 主窗口与托盘
config.py            # 本地配置（config.json）
models.py            # 消息数据模型
listener/
  listener1.py       # 线路一监听
  login.py           # 登录与 state.json 管理
pages/
  home_page.py       # 主页（登录、连接）
  tools_page.py      # 工具页
  settings_page.py   # 设置页
tools/
  memo_tool.py       # 直播备忘录
```

## 分支说明

| 分支 | 说明 |
|------|------|
| `main` | 早期精简版本，适合了解基础架构或作为稳定快照 |
| `dev` | 当前主力开发分支，包含更多线路、弹幕机、加班机等完整功能 |

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。

## 反馈

项目仍在持续迭代中。如有问题或建议，欢迎在 [Issues](https://github.com/bitto205/LiveAll-In-One/issues) 中反馈。
