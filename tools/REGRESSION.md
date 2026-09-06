# Tools Plan 4 回归清单

手动验证：工具窗由主界面「工具」页打开，和主界面共用同一个进程与 `QApplication`，
没有独立的 Tools 应用入口。

```powershell
.\build\build_work\custom\LiveAIO.exe -no-admin
# 工具页 → 备忘录 / 弹幕机 / 加班机 / 捡叶子 的「打开」

# UI 自动化辅助（列窗口、按控件名点击、截图）
.\scripts\ui_drive.ps1 -Tree
.\scripts\ui_drive.ps1 -Invoke "打开" -Index 0
.\scripts\ui_shot.ps1 -List
.\scripts\ui_shot.ps1 -Handle <hwnd> -Out shot.png
```

## MemoTool
- [x] 设置开关变更发出 `tool.memo.set`（字段与 Go schema 一致）
- [ ] 收到 `memo.item` 列表出现可点击消除条目（需真实礼物流）
- [ ] `stack=true` 时同 `stack_key` 叠加计数（需真实礼物流）

## Overlay / 采集
- [x] 双击悬浮窗软最小化（lower + 边框收起）— 代码路径保留
- [x] 再激活恢复 — 代码路径保留
- [x] 拖拽时不闪（freeze updates）— 代码路径保留
- [ ] OBS / 直播伴侣勾选「允许窗口透明」采集不发黑（需人工采集验证）

## Skin / Media
- [x] `resources/skin/danmu|overtime/default/skin.json` 被加载
- [x] 礼物名能解析到 `resources/gift/icon/*`（礼物选择器 / 加班机格子）
- [ ] 动画 webp：能出帧则播；失败回退静图（需人工）

## OvertimeTool
- [x] `tool.overtime.set` 含 hours/minutes/seconds/rules
- [x] cmd：start/pause/reset/clear_ledger（打开/关闭悬浮窗）
- [x] `tick` 刷新悬浮窗剩余时间
- [x] `ledger` 以 Go 下发为准（模拟送礼 → 用户时长统计）
- [x] `tool.overtime.sim_gift`

## DanmuTool
- [x] 设置 / 弹幕 / 礼物 / 关注 / 点赞 面板与旧布局一致
- [x] `tool.danmu.set` 字段：danmu_chat_on / gift / follow / like*
- [ ] `danmu.show` 气泡 fade-in → stay → fade-out（需真实弹幕流）
- [ ] 礼物气泡带图标；过多气泡裁剪（需真实弹幕流）

## LeafTool
- [x] `tool.leaf.set` 含 `rules[]`（gift / mode / value / min / max，最多 10 条）
- [x] 礼物从图鉴选择（gift_id + 名字）；规则按礼物名精确匹配
- [x] `tool.leaf.sim_gift` → Core → `leaf.spawn`（正数投放 / 负数回收）
- [x] 悬浮窗：物理堆叶、拖入垃圾桶消除、容量% 与缩放
- [ ] 真实礼物流下规则加减叶（需连直播间）

## DPI
- [x] 当前开发机（约 200% DPI）下主窗 / 工具窗 / PrintWindow 截图几何正常

## 单进程 / 主题
- [x] 仅一个 LiveAIO 进程、一个 QApplication；工具窗同进程打开
- [x] 无独立 Tools 宿主窗口
- [x] 四套主题可切换；`config.json` 持久化；重启后恢复
- [x] 第二次启动只抬起已有窗口（`ui.command` / `ui.show` → `ui.focus`）
- [x] 关闭到托盘只隐藏主窗，不结束 QApplication

## 生命周期隔离（消费者 100% / 空闲 0%）

- [ ] 开弹幕/加班/叶子设置页 → Core 收到 `tool.demand active=true`；未开悬浮窗时无重绘/无 leaf 物理
- [ ] 开悬浮窗后关设置页 → runtime 与收包仍在（状态 2），**不** tryRelease
- [ ] 关悬浮且无设置 → `tool.demand active=false`；该工具 afterMessage 停；三工具互不影响
- [ ] 「打开悬浮窗」按钮在设置页 ensure 异常后仍可 `publishToolDemand` + 打开
- [ ] 托盘 OverlayCommand 与按钮同源 ensure
- [ ] 无工具消费者时礼物 pixmap 可 `releaseGiftPixmapCaches`；启动不再全量 warm catalog
- [ ] 卡住假 TCP 客户端不读 → 其它 op / 托盘仍可用（Conn write deadline）
- [ ] 首开工具 toast「正在加载」后拆帧 LoadLibrary；关主窗不嵌套 QEventLoop 等 bye
