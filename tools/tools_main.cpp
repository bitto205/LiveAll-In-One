#include <QApplication>
#include <QCloseEvent>
#include <QComboBox>
#include <QDialog>
#include <QAbstractItemView>
#include <QEasingCurve>
#include <QFontMetrics>
#include <QGraphicsDropShadowEffect>
#include <QGridLayout>
#include <QHeaderView>
#include <QImage>
#include <QLinearGradient>
#include <QEvent>
#include <QMessageBox>
#include <QMetaType>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QPixmap>
#include <QRandomGenerator>
#include <QScreen>
#include <QSet>
#include <QShowEvent>
#include <QSignalBlocker>
#include <QTableWidget>
#include <QTableWidgetItem>
#include <QTextEdit>
#include <QTimer>
#include <QVariantAnimation>

#ifdef Q_OS_WIN
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#  include <windowsx.h>
#endif

#include <algorithm>
#include <cmath>

#include "tools_common.cpp"
#include "../resources/cpp/gift.cpp"
#include "../resources/cpp/skin.cpp"
#include "../util/gift_widgets.cpp"

namespace liveaio::tools {
using ToolSkin = liveaio::resources::ToolSkin;
using liveaio::resources::resolveGiftIconPath;
using liveaio::util::OverlayHost;
using liveaio::util::OverlayResizeFreeze;
using liveaio::util::ThemedComboBox;
using liveaio::util::prepareAppAlphaFormat;
}  // namespace liveaio::tools

#include "memo_tool.cpp"
#include "danmu_tool.cpp"
#include "overtime_tool.cpp"
#include "leaf_tool.cpp"
#include "liveaio_tools_api.h"

namespace liveaio::tools {

static void warmToolCatalogs(QObject* context) {
    Q_UNUSED(context);
    // 礼物 catalog/icon 按展示需求异步拉；启动不再全量预热。
}

struct ToolEntry {
    QString id;
    ToolRuntimeBase* runtime = nullptr;
    ToolWindowBase* panel = nullptr;
    bool demandOn = false;
};

// 同进程插件：工具 runtime + 设置窗生命周期由这里持有。
class ToolsSession final : public QObject {
public:
    static ToolsSession& instance() {
        static ToolsSession* session = new ToolsSession(qApp);
        return *session;
    }

    bool openTool(const QString& id) {
        ensureCore();
        ToolEntry* entry = ensureEntry(id);
        if (!entry) return false;
        publishDemand(id, true);
        prepOverlayShell(id);

        if (entry->panel) {
            entry->panel->applyChromeStyle();
            entry->panel->show();
            entry->panel->raise();
            entry->panel->activateWindow();
            return true;
        }

        ToolWindowBase* panel = nullptr;
        if (id == QStringLiteral("memo")) {
            panel = createMemoTool(core_);
        } else if (id == QStringLiteral("danmu")) {
            panel = createDanmuTool(core_, static_cast<DanmuToolRuntime*>(entry->runtime));
        } else if (id == QStringLiteral("overtime")) {
            panel = createOvertimeTool(core_, static_cast<ot::OvertimeToolRuntime*>(entry->runtime));
        } else if (id == QStringLiteral("leaf")) {
            panel = leaf::createLeafTool(core_, static_cast<leaf::LeafToolRuntime*>(entry->runtime));
        }
        if (!panel) return false;

        panel->applyChromeStyle();
        entry->panel = panel;
        panel->setPanelCloseHandler([this](const QString& toolId) { onPanelClosed(toolId); });
        panel->show();
        panel->raise();
        panel->activateWindow();
        return true;
    }

    void warm() { ensureCore(); }

    ot::GiftPickerPopup* giftPicker() { return liveaio::util::sessionGiftPicker(); }

    bool overlayCommand(const QString& id, const QString& action) {
        ensureCore();
        const OverlayToolId tool = id == QStringLiteral("danmu")
            ? OverlayToolId::Danmu
            : id == QStringLiteral("overtime") ? OverlayToolId::Overtime
            : id == QStringLiteral("leaf") ? OverlayToolId::Leaf
            : OverlayToolId::None;
        if (tool == OverlayToolId::None) return false;

        // 双入口 ensure：托盘/按钮路径与设置页一样先拉起实例。
        ToolEntry* entry = ensureEntry(id);
        if (!entry || !entry->runtime) return false;
        publishDemand(id, true);
        prepOverlayShell(id);

        auto& host = OverlayHostService::instance();
        const bool active = host.isToolActive(tool);
        if (action == QStringLiteral("open")) {
            if (active) return true;
        } else if (action == QStringLiteral("toggle")) {
            if (id == QStringLiteral("danmu")) {
                static_cast<DanmuToolRuntime*>(entry->runtime)->toggleOverlay(nullptr);
            } else if (id == QStringLiteral("overtime")) {
                static_cast<ot::OvertimeToolRuntime*>(entry->runtime)
                    ->toggleOverlay(ot::loadSettings(), nullptr);
            } else {
                static_cast<leaf::LeafToolRuntime*>(entry->runtime)->toggleOverlay(nullptr);
            }
            return true;
        } else {
            return host.command(tool, action);
        }

        if (id == QStringLiteral("danmu")) {
            static_cast<DanmuToolRuntime*>(entry->runtime)->toggleOverlay(nullptr);
        } else if (id == QStringLiteral("overtime")) {
            static_cast<ot::OvertimeToolRuntime*>(entry->runtime)
                ->toggleOverlay(ot::loadSettings(), nullptr);
        } else {
            static_cast<leaf::LeafToolRuntime*>(entry->runtime)->toggleOverlay(nullptr);
        }
        return true;
    }

    int overlayState(const QString& id) {
        const OverlayToolId tool = id == QStringLiteral("danmu")
            ? OverlayToolId::Danmu
            : id == QStringLiteral("overtime") ? OverlayToolId::Overtime
            : id == QStringLiteral("leaf") ? OverlayToolId::Leaf
            : OverlayToolId::None;
        return OverlayHostService::instance().stateBits(tool);
    }

    void refreshOpenPanelsTheme() {
        for (auto it = entries_.begin(); it != entries_.end(); ++it) {
            if (it->panel) {
                it->panel->applyChromeStyle();
                it->panel->refreshTheme();
            }
        }
    }

    // 主窗全关（未开托盘）时连锁关掉所有工具设置窗 + 悬浮窗。
    void shutdownAll() {
        OverlayHostService::instance().closeAll();
        const QList<ToolWindowBase*> panels = [this]() {
            QList<ToolWindowBase*> out;
            for (auto it = entries_.begin(); it != entries_.end(); ++it) {
                if (it->panel) out.append(it->panel);
            }
            return out;
        }();
        for (ToolWindowBase* panel : panels) {
            if (panel) panel->close();
        }
        const QStringList ids = entries_.keys();
        for (const QString& id : ids) {
            ToolEntry& entry = entries_[id];
            entry.panel = nullptr;
            entry.demandOn = false;
            publishToolDemand(id, false);
            if (entry.runtime) {
                entry.runtime->deleteLater();
                entry.runtime = nullptr;
            }
        }
        entries_.clear();
        liveaio::util::hideSessionGiftPicker();
        liveaio::resources::releaseGiftPixmapCaches();
    }

private:
    explicit ToolsSession(QObject* parent) : QObject(parent) {}

    void ensureCore() {
        if (core_) return;
        if (g_appRoot.isEmpty()) {
            g_appRoot = qEnvironmentVariable("LIVEAIO_ROOT");
            if (g_appRoot.isEmpty()) g_appRoot = QDir::currentPath();
        }
        liveaio::util::setToolAppRoot(g_appRoot);
        prepareAppAlphaFormat();
        core_ = new CoreClient(this);
        setToolDemandCore(core_);
        installConfigBridge();
        liveaio::util::setGiftPickerParent(this);
        core_->setPacketCallback([this](const QJsonObject& packet) { dispatchPacket(packet); });
        core_->setStatusCallback([this](bool connected) { dispatchStatus(connected); });
        core_->connectToCore();
        warmToolCatalogs(this);
    }

    static OverlayToolId overlayIdFor(const QString& id) {
        if (id == QStringLiteral("danmu")) return OverlayToolId::Danmu;
        if (id == QStringLiteral("overtime")) return OverlayToolId::Overtime;
        if (id == QStringLiteral("leaf")) return OverlayToolId::Leaf;
        return OverlayToolId::None;
    }

    void prepOverlayShell(const QString& id) {
        const OverlayToolId tool = overlayIdFor(id);
        if (tool == OverlayToolId::None) return;
        OverlayHostService::instance().prepare(tool);
    }

    void publishDemand(const QString& id, bool active) {
        if (entries_.contains(id)) {
            ToolEntry& entry = entries_[id];
            if (entry.demandOn == active) return;
            entry.demandOn = active;
        } else if (!active) {
            return;
        }
        publishToolDemand(id, active);
    }

    ToolEntry* ensureEntry(const QString& id) {
        if (entries_.contains(id)) return &entries_[id];
        ToolEntry entry;
        entry.id = id;
        if (id == QStringLiteral("danmu")) {
            entry.runtime = createDanmuRuntime(this, [this, id]() { tryReleaseTool(id); });
        } else if (id == QStringLiteral("overtime")) {
            entry.runtime = createOvertimeRuntime(this, [this, id]() { tryReleaseTool(id); });
        } else if (id == QStringLiteral("leaf")) {
            entry.runtime = leaf::createLeafRuntime(this, [this, id]() { tryReleaseTool(id); });
        } else if (id != QStringLiteral("memo")) {
            return nullptr;
        }
        entries_.insert(id, entry);
        return &entries_[id];
    }

    void onPanelClosed(const QString& id) {
        if (!entries_.contains(id)) return;
        ToolEntry& entry = entries_[id];
        entry.panel = nullptr;
        tryReleaseTool(id);
    }

    void tryReleaseTool(const QString& id) {
        if (!entries_.contains(id)) return;
        ToolEntry& entry = entries_[id];
        if (entry.panel) return;
        if (entry.runtime && entry.runtime->isOverlayActive()) return;
        entry.demandOn = false;
        publishToolDemand(id, false);
        if (entry.runtime) {
            entry.runtime->deleteLater();
            entry.runtime = nullptr;
        }
        entries_.remove(id);
        if (entries_.isEmpty()) {
            liveaio::resources::releaseGiftPixmapCaches();
            liveaio::util::hideSessionGiftPicker();
        }
    }

    void dispatchPacket(const QJsonObject& packet) {
        const QString op = packet.value(QStringLiteral("op")).toString();
        if (op != QStringLiteral("memo.item") && op != QStringLiteral("danmu.show")
            && op != QStringLiteral("tick") && op != QStringLiteral("ledger")
            && op != QStringLiteral("leaf.spawn")) {
            return;
        }
        for (auto it = entries_.begin(); it != entries_.end(); ++it) {
            ToolEntry& entry = it.value();
            if (entry.runtime) entry.runtime->onCorePacket(packet);
            else if (entry.panel) entry.panel->onCorePacket(packet);
        }
    }

    void dispatchStatus(bool connected) {
        for (auto it = entries_.begin(); it != entries_.end(); ++it) {
            ToolEntry& entry = it.value();
            if (entry.runtime) entry.runtime->onCoreStatus(connected);
            if (entry.panel) entry.panel->onCoreStatus(connected);
        }
    }

    CoreClient* core_ = nullptr;
    QMap<QString, ToolEntry> entries_;
};

}  // namespace liveaio::tools

extern "C" LIVEAIO_TOOLS_API void LiveAIO_ToolsWarm(void) {
    if (!QApplication::instance()) return;
    liveaio::tools::ToolsSession::instance().warm();
}

extern "C" LIVEAIO_TOOLS_API int LiveAIO_ToolsOpen(const char* tool_id) {
    if (!QApplication::instance()) return 1;
    const QString id = QString::fromUtf8(tool_id ? tool_id : "").trimmed();
    if (id.isEmpty()) return 2;
    return liveaio::tools::ToolsSession::instance().openTool(id) ? 0 : 3;
}

extern "C" LIVEAIO_TOOLS_API int LiveAIO_ToolsOverlayCommand(
    const char* tool_id, const char* action) {
    if (!QApplication::instance()) return 1;
    const QString id = QString::fromUtf8(tool_id ? tool_id : "").trimmed();
    const QString cmd = QString::fromUtf8(action ? action : "").trimmed();
    if (id.isEmpty() || cmd.isEmpty()) return 2;
    return liveaio::tools::ToolsSession::instance().overlayCommand(id, cmd) ? 0 : 3;
}

extern "C" LIVEAIO_TOOLS_API int LiveAIO_ToolsOverlayState(const char* tool_id) {
    if (!QApplication::instance()) return 0;
    const QString id = QString::fromUtf8(tool_id ? tool_id : "").trimmed();
    return liveaio::tools::ToolsSession::instance().overlayState(id);
}

extern "C" LIVEAIO_TOOLS_API void LiveAIO_ToolsApplyTheme(const char* theme_name) {
    if (!QApplication::instance()) return;
    const QString name = QString::fromUtf8(theme_name ? theme_name : "").trimmed();
    if (name.isEmpty()) return;
    liveaio::util::applyThemeName(name);
    // 只刷工具窗自身样式，禁止 qApp->setStyleSheet(toolQss) 盖掉主窗 shellQss。
    liveaio::tools::ToolsSession::instance().refreshOpenPanelsTheme();
    if (auto* picker = liveaio::util::sessionGiftPicker()) picker->refreshTheme();
}

extern "C" LIVEAIO_TOOLS_API void LiveAIO_ToolsShutdown(void) {
    if (!QApplication::instance()) return;
    liveaio::tools::ToolsSession::instance().shutdownAll();
}
