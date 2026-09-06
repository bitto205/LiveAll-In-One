C++ tools：同一宿主进程多工具窗。

- 公共悬浮窗组件：`../util/widgets.cpp`（透明采集、软最小化、拖拽冻结）
- `memo_tool.cpp` / `danmu_tool.cpp` / `overtime_tool.cpp` / `leaf_tool.cpp`
- 皮肤 / 礼物读取：`../resources/cpp/skin.cpp`、`../resources/cpp/gift.cpp`

### 捡叶子（`leaf`）

- 业务在 Go `core/leaf.go`：`tool.leaf.set` / `tool.leaf.sim_gift` → `leaf.spawn`
- UI：设置页（容量%、叶/桶缩放、礼物规则）+ 透明物理悬浮窗（拖叶进垃圾桶）
- 礼物标识：图鉴 `gift_id` + 名字；规则存名字，与消息礼物名精确匹配
