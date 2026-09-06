#pragma once

#ifdef _WIN32
#  ifdef LIVEAIO_TOOLS_BUILD
#    define LIVEAIO_TOOLS_API __declspec(dllexport)
#  else
#    define LIVEAIO_TOOLS_API __declspec(dllimport)
#  endif
#else
#  define LIVEAIO_TOOLS_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Tools 只作为 Pages 进程内的插件存在：没有第二个 QApplication，也没有宿主窗口。
 * 打开一个工具窗口。tool_id: "memo" | "danmu" | "overtime" | "leaf"。返回 0 表示成功。 */
LIVEAIO_TOOLS_API int LiveAIO_ToolsOpen(const char* tool_id);

/* 不打开设置窗，直接控制指定透明悬浮窗。
 * action: open | toggle | close | frame.toggle | frame.show | frame.hide |
 *         lock.toggle | lock | unlock | pin.toggle | pin | unpin。 */
LIVEAIO_TOOLS_API int LiveAIO_ToolsOverlayCommand(
    const char* tool_id, const char* action);

/* 状态位：1=已打开，2=边框显示，4=锁定，8=桌面置顶。 */
LIVEAIO_TOOLS_API int LiveAIO_ToolsOverlayState(const char* tool_id);

/* 进入 Tools 页时预连 Core、预加载 config/catalog（不必打开具体工具）。 */
LIVEAIO_TOOLS_API void LiveAIO_ToolsWarm(void);

/* 主界面切换主题时同步已打开的工具窗（theme_name 为 util/widgets.cpp 的主题名）。 */
LIVEAIO_TOOLS_API void LiveAIO_ToolsApplyTheme(const char* theme_name);

/* 主窗全退出（非托盘收起）时连锁关闭所有工具设置窗与透明悬浮窗。 */
LIVEAIO_TOOLS_API void LiveAIO_ToolsShutdown(void);

#ifdef __cplusplus
}
#endif
