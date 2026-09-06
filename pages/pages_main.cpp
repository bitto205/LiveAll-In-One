#include "pages_common.cpp"
#include "home_page.cpp"
#include "tools_page.cpp"
#include "settings_page.cpp"
#include "main_page.cpp"

#include "liveaio_pages_api.h"

#include <QAtomicPointer>
#include <QAtomicInt>
#include <QCoreApplication>
#include <QMetaObject>
#include <QThread>

namespace {
// Go 侧可能再次请求显示界面。Qt 不允许一个进程里先后跑两个 QApplication，
// 所以这里保留主窗口指针，让第二次请求只是抬窗。
QAtomicPointer<liveaio::pages::MainWindow> g_mainWindow = nullptr;
QAtomicInt g_startHidden = 0;
}  // namespace

extern "C" LIVEAIO_PAGES_API int LiveAIO_PagesRun(int argc, char** argv) {
    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("LiveAIO"));
    app.setStyle(QStringLiteral("Fusion"));
    // 关闭工具窗或收起主窗都不能结束应用：整个进程只有这一个 QApplication。
    app.setQuitOnLastWindowClosed(false);

    const QString envRoot = qEnvironmentVariable("LIVEAIO_ROOT");
    liveaio::pages::g_appRoot = envRoot.isEmpty() ? QDir::currentPath() : envRoot;
    QDir::setCurrent(liveaio::pages::g_appRoot);

    auto* core = new liveaio::pages::CoreClient(&app);
    liveaio::pages::installConfigBridge(core);

    liveaio::util::applyThemeName(
        liveaio::pages::configValue(QStringLiteral("theme"),
                                    liveaio::util::defaultThemeName()).toString());
    liveaio::pages::g_minimizeToTray =
        liveaio::pages::configValue(QStringLiteral("minimize_to_tray"), false).toBool();

    auto* win = new liveaio::pages::MainWindow(core);
    core->setPacketCallback([win](const QJsonObject& packet) { win->onCorePacket(packet); });
    core->setStatusCallback([win](bool connected) { win->onCoreStatus(connected); });
    core->connectToCore();

    const bool startHidden = g_startHidden.loadAcquire() != 0;
    if (!startHidden) win->show();
    win->requestInitialState();
    g_mainWindow.storeRelease(win);
    const int code = app.exec();
    g_mainWindow.storeRelease(nullptr);
    return code;
}

extern "C" LIVEAIO_PAGES_API void LiveAIO_PagesSetStartHidden(int hidden) {
    g_startHidden.storeRelease(hidden ? 1 : 0);
}

extern "C" LIVEAIO_PAGES_API int LiveAIO_PagesShow(void) {
    liveaio::pages::MainWindow* win = g_mainWindow.loadAcquire();
    if (!win || !QCoreApplication::instance()) return 0;
    // 调用方是 Go 的任意线程，必须回到 UI 线程再动窗口。
    QMetaObject::invokeMethod(win, [win]() { win->raiseFromTray(); }, Qt::QueuedConnection);
    return 1;
}

extern "C" LIVEAIO_PAGES_API int LiveAIO_PagesOverlayCommand(
    const char* tool_id, const char* action) {
    if (!QCoreApplication::instance()) return 1;
    const QString id = QString::fromUtf8(tool_id ? tool_id : "").trimmed();
    const QString cmd = QString::fromUtf8(action ? action : "").trimmed();
    if (id.isEmpty() || cmd.isEmpty()) return 2;
    int result = 3;
    auto invoke = [&]() {
        QString error;
        result = liveaio::pages::ToolsPlugin::instance().overlayCommand(id, cmd, &error) ? 0 : 3;
    };
    if (QThread::currentThread() == QCoreApplication::instance()->thread()) invoke();
    else QMetaObject::invokeMethod(QCoreApplication::instance(), invoke, Qt::BlockingQueuedConnection);
    return result;
}

extern "C" LIVEAIO_PAGES_API int LiveAIO_PagesOverlayState(const char* tool_id) {
    if (!QCoreApplication::instance()) return 0;
    const QString id = QString::fromUtf8(tool_id ? tool_id : "").trimmed();
    int state = 0;
    auto invoke = [&]() {
        state = liveaio::pages::ToolsPlugin::instance().overlayState(id);
    };
    if (QThread::currentThread() == QCoreApplication::instance()->thread()) invoke();
    else QMetaObject::invokeMethod(QCoreApplication::instance(), invoke, Qt::BlockingQueuedConnection);
    return state;
}
