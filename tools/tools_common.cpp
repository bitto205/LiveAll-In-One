// tools/tools_common.cpp — Tools 插件共享设施：core 客户端、配置读写、工具窗基类。
// 主题与控件来自 util/widgets.cpp，工具窗不再写死颜色。

#include <QApplication>
#include <QCheckBox>
#include <QChildEvent>
#include <QCloseEvent>
#include <QCursor>
#include <QDir>
#include <QEnterEvent>
#include <QFile>
#include <QFormLayout>
#include <QFrame>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLineF>
#include <QLineEdit>
#include <QListWidget>
#include <QMainWindow>
#include <QMap>
#include <QObject>
#include <QPainter>
#include <QPainterPath>
#include <QPointer>
#include <QPushButton>
#include <QRadialGradient>
#include <QScreen>
#include <QScrollArea>
#include <QScrollBar>
#include <QSizePolicy>
#include <QSpinBox>
#include <QStackedWidget>
#include <QTcpSocket>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>

#include <algorithm>
#include <cmath>
#include <functional>
#include <memory>

#include "../util/widgets.cpp"

namespace liveaio::tools {

using liveaio::util::StepCard;
using liveaio::util::ThemedToggle;
using liveaio::util::labelRow;
using liveaio::util::qssDanger;
using liveaio::util::qssDisabled;
using liveaio::util::qssOutlined;
using liveaio::util::qssMutedLabel;
using liveaio::util::scrollPage;
using liveaio::util::theme;
using liveaio::util::deferNextTick;
using liveaio::util::WidgetDeferredDestroy;

static constexpr const char* kCoreHost = "127.0.0.1";
static constexpr quint16 kCorePort = 19877;

static QString g_appRoot;
static std::function<void(const QJsonObject&)> g_sendPacket;
static QVariantMap g_configCache;
static bool g_configCacheLoaded = false;

struct ToolMeta {
    QString id;
    QString title;
    QString desc;
    QString icon;
};

static const QList<ToolMeta>& toolCatalog() {
    static const QList<ToolMeta> catalog = {
        {QStringLiteral("memo"), QStringLiteral("备忘录"),
         QStringLiteral("将礼物、关注、点赞记录为可消除的列表条目"), QStringLiteral("📋")},
        {QStringLiteral("danmu"), QStringLiteral("弹幕机"),
         QStringLiteral("透明悬浮弹幕显示窗口"), QStringLiteral("💬")},
        {QStringLiteral("overtime"), QStringLiteral("加班机"),
         QStringLiteral("透明悬浮加班显示窗口"), QStringLiteral("⏱")},
        {QStringLiteral("leaf"), QStringLiteral("捡叶子"),
         QStringLiteral("送礼堆叶子，拖到垃圾桶消除"), QStringLiteral("🍃")},
    };
    return catalog;
}

static QVariantMap readConfigMap() {
    QFile file(QDir(g_appRoot).filePath(QStringLiteral("config.json")));
    if (!file.open(QIODevice::ReadOnly)) return {};
    QJsonParseError err;
    const auto doc = QJsonDocument::fromJson(file.readAll(), &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject()) return {};
    return doc.object().toVariantMap();
}

static void mergeConfigValues(const QVariantMap& values) {
    for (auto it = values.constBegin(); it != values.constEnd(); ++it) {
        g_configCache.insert(it.key(), it.value());
    }
    g_configCacheLoaded = true;
}

static QVariant configValue(const QString& key, const QVariant& fallback = {}) {
    if (g_configCacheLoaded) {
        const auto it = g_configCache.constFind(key);
        if (it != g_configCache.constEnd()) return it.value();
    }
    const QVariantMap map = readConfigMap();
    const auto it = map.constFind(key);
    return it == map.constEnd() ? fallback : it.value();
}

// core 是 config.json 的唯一写者；乐观更新缓存，未连上时入队 ready 后 flush。
static void writeConfigValue(const QString& key, const QVariant& value) {
    g_configCache.insert(key, value);
    g_configCacheLoaded = true;
    if (g_sendPacket) g_sendPacket(QJsonObject{
        {QStringLiteral("op"), QStringLiteral("config.set")},
        {QStringLiteral("key"), key},
        {QStringLiteral("value"), QJsonValue::fromVariant(value)},
    });
}

static void installConfigBridge() {
    mergeConfigValues(readConfigMap());
    liveaio::util::setConfigAccessors(
        [](const QString& key, const QVariant& fallback) { return configValue(key, fallback); },
        [](const QString& key, const QVariant& value) { writeConfigValue(key, value); });
    // 主题由主界面驱动（LiveAIO_ToolsApplyTheme），工具侧不重复写配置。
    liveaio::util::setThemePersistHook({});
}

class CoreClient final : public QObject {
public:
    explicit CoreClient(QObject* parent = nullptr) : QObject(parent), socket_(new QTcpSocket(this)) {
        g_sendPacket = [this](const QJsonObject& packet) { queueOrSend(packet); };
        QObject::connect(socket_, &QTcpSocket::readyRead, this, [this]() { onReadyRead(); });
        QObject::connect(socket_, &QTcpSocket::connected, this, [this]() {
            connected_ = true;
            if (statusCallback_) statusCallback_(true);
        });
        QObject::connect(socket_, &QTcpSocket::disconnected, this, [this]() {
            connected_ = false;
            ready_ = false;
            if (statusCallback_) statusCallback_(false);
        });
    }

    void connectToCore() { socket_->connectToHost(QString::fromLatin1(kCoreHost), kCorePort); }
    void setPacketCallback(std::function<void(const QJsonObject&)> cb) { packetCallback_ = std::move(cb); }
    void setStatusCallback(std::function<void(bool)> cb) { statusCallback_ = std::move(cb); }
    bool isReady() const { return ready_; }

    void send(const QJsonObject& packet) { queueOrSend(packet); }

private:
    void queueOrSend(const QJsonObject& packet) {
        const QString op = packet.value(QStringLiteral("op")).toString();
        if (op == QStringLiteral("config.set") && !ready_) {
            pendingSets_.append(packet);
            return;
        }
        if (socket_->state() != QAbstractSocket::ConnectedState) {
            if (op == QStringLiteral("config.set")) pendingSets_.append(packet);
            return;
        }
        QByteArray out = QJsonDocument(packet).toJson(QJsonDocument::Compact);
        out.push_back('\n');
        socket_->write(out);
    }

    void onReady() {
        ready_ = true;
        send(QJsonObject{{QStringLiteral("op"), QStringLiteral("config.get")}});
        flushPendingSets();
    }

    void flushPendingSets() {
        if (!ready_ || socket_->state() != QAbstractSocket::ConnectedState) return;
        const auto pending = pendingSets_;
        pendingSets_.clear();
        for (const QJsonObject& p : pending) send(p);
    }

    bool handleConfigPacket(const QJsonObject& packet) {
        const QString op = packet.value(QStringLiteral("op")).toString();
        if (op == QStringLiteral("config.value")) {
            if (packet.contains(QStringLiteral("values"))) {
                mergeConfigValues(packet.value(QStringLiteral("values")).toObject().toVariantMap());
            } else if (packet.contains(QStringLiteral("key"))) {
                g_configCache.insert(packet.value(QStringLiteral("key")).toString(),
                                     packet.value(QStringLiteral("value")).toVariant());
                g_configCacheLoaded = true;
            }
            return true;
        }
        if (op == QStringLiteral("config.ok")) {
            const QString key = packet.value(QStringLiteral("key")).toString();
            if (!key.isEmpty()) {
                g_configCache.insert(key, packet.value(QStringLiteral("value")).toVariant());
                g_configCacheLoaded = true;
            }
            return true;
        }
        return false;
    }

    void onReadyRead() {
        buffer_.append(socket_->readAll());
        while (true) {
            const int idx = buffer_.indexOf('\n');
            if (idx < 0) break;
            QByteArray line = buffer_.left(idx).trimmed();
            buffer_.remove(0, idx + 1);
            if (line.isEmpty()) continue;
            QJsonParseError err;
            const auto doc = QJsonDocument::fromJson(line, &err);
            if (err.error != QJsonParseError::NoError || !doc.isObject()) continue;
            QJsonObject packet = doc.object();
            const QString op = packet.value(QStringLiteral("op")).toString();
            if (op == QStringLiteral("ready")) onReady();
            if (handleConfigPacket(packet)) continue;
            if (packetCallback_) packetCallback_(packet);
        }
    }

    QTcpSocket* socket_;
    QByteArray buffer_;
    QVector<QJsonObject> pendingSets_;
    bool connected_ = false;
    bool ready_ = false;
    std::function<void(const QJsonObject&)> packetCallback_;
    std::function<void(bool)> statusCallback_;
};

// UI→Core 消费者门控；ToolsSession / 打开按钮共用，防止单入口 ensure 失败。
inline CoreClient*& toolDemandCoreSlot() {
    static CoreClient* core = nullptr;
    return core;
}
inline void setToolDemandCore(CoreClient* core) { toolDemandCoreSlot() = core; }
inline void publishToolDemand(const QString& tool, bool active) {
    CoreClient* core = toolDemandCoreSlot();
    if (!core) return;
    core->send(QJsonObject{
        {QStringLiteral("op"), QStringLiteral("tool.demand")},
        {QStringLiteral("tool"), tool},
        {QStringLiteral("active"), active},
    });
}

class ToolsSession;

class ToolRuntimeBase : public QObject {
public:
    explicit ToolRuntimeBase(QObject* parent = nullptr) : QObject(parent) {}
    virtual QString toolId() const = 0;
    virtual bool isOverlayActive() const { return false; }
    virtual void onCorePacket(const QJsonObject&) {}
    virtual void onCoreStatus(bool) {}
};

class ToolWindowBase : public QMainWindow {
public:
    explicit ToolWindowBase(CoreClient* core, QWidget* parent = nullptr)
        : QMainWindow(parent, Qt::Window), core_(core) {
        setAttribute(Qt::WA_QuitOnClose, false);
        // 工具窗按钮统一走容器代理判定悬停，含懒加载 Tab 与动态卡片。
        liveaio::util::installHoverAutoWiring(this);
    }
    virtual QString toolId() const = 0;
    virtual void onCorePacket(const QJsonObject& packet) = 0;
    virtual void onCoreStatus(bool connected) { Q_UNUSED(connected); }
    virtual void refreshTheme() {}
    virtual void onPanelClosing() {}
    // 工具窗样式写在窗自身，禁止写到 qApp，以免盖掉主窗 shellQss。
    virtual void applyChromeStyle() { setStyleSheet(liveaio::util::toolQss()); }

    void setPanelCloseHandler(std::function<void(const QString&)> handler) {
        panelCloseHandler_ = std::move(handler);
    }

protected:
    void sendCore(const QJsonObject& packet) const {
        if (core_) core_->send(packet);
    }
    CoreClient* core() const { return core_; }

    void closeEvent(QCloseEvent* event) override {
        onPanelClosing();
        if (panelCloseHandler_) panelCloseHandler_(toolId());
        event->accept();
        hide();
        deleteLater();
    }

private:
    CoreClient* core_;
    std::function<void(const QString&)> panelCloseHandler_;
};

// 旧 memo/danmu/overtime 设置窗共用的顶部 Tab 壳。
class TabbedToolWindow : public ToolWindowBase {
public:
    TabbedToolWindow(CoreClient* core, const QStringList& tabNames, int barHeight = 44)
        : ToolWindowBase(core) {
        auto* root = new QWidget(this);
        root->setObjectName(QStringLiteral("ToolRoot"));
        setCentralWidget(root);

        auto* lay = new QVBoxLayout(root);
        lay->setContentsMargins(0, 0, 0, 0);
        lay->setSpacing(0);

        auto* topbar = new QWidget(root);
        topbar->setObjectName(QStringLiteral("TopBar"));
        topbar->setFixedHeight(barHeight);
        auto* tb = new QHBoxLayout(topbar);
        tb->setContentsMargins(8, 0, 8, 0);
        tb->setSpacing(0);
        for (int i = 0; i < tabNames.size(); ++i) {
            auto* btn = new QPushButton(tabNames.at(i), topbar);
            btn->setObjectName(QStringLiteral("TabBtn"));
            btn->setFixedHeight(barHeight);
            btn->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
            btn->setCursor(Qt::PointingHandCursor);
            liveaio::util::suppressButtonFocus(btn);
            QObject::connect(btn, &QPushButton::clicked, this, [this, i]() { switchTab(i); });
            tabs_.append(btn);
            tb->addWidget(btn);
        }
        tb->addStretch();
        lay->addWidget(topbar);

        stack_ = new QStackedWidget(root);
        lay->addWidget(stack_);
    }

    void switchTab(int index) {
        ensureTab(index);
        stack_->setCurrentIndex(index);
        for (int i = 0; i < tabs_.size(); ++i) {
            tabs_[i]->setProperty("active", i == index);
            tabs_[i]->style()->unpolish(tabs_[i]);
            tabs_[i]->style()->polish(tabs_[i]);
        }
    }

    void addTabPlaceholder() {
        auto* ph = new QWidget(stack_);
        ph->setObjectName(QStringLiteral("TabPlaceholder"));
        stack_->addWidget(ph);
        const int index = stack_->indexOf(ph);
        if (tabPlaceholders_.size() <= index) tabPlaceholders_.resize(index + 1);
        if (tabBuilt_.size() <= index) tabBuilt_.resize(index + 1);
        tabPlaceholders_[index] = ph;
        tabBuilt_[index] = false;
    }

    void replaceTabPlaceholder(int index, QWidget* page) {
        if (!page) return;
        if (index >= 0 && index < tabPlaceholders_.size() && tabPlaceholders_[index]) {
            QWidget* ph = tabPlaceholders_[index];
            const int idx = stack_->indexOf(ph);
            stack_->removeWidget(ph);
            ph->deleteLater();
            tabPlaceholders_[index] = nullptr;
            if (idx >= 0) stack_->insertWidget(idx, page);
            else stack_->addWidget(page);
            return;
        }
        addTabPage(page);
    }

protected:
    void addTabPage(QWidget* page) { stack_->addWidget(page); }

    void ensureTab(int index) {
        if (index < 0 || index >= tabFactories_.size() || !tabFactories_[index]) return;
        if (tabBuilt_.size() <= index) tabBuilt_.resize(index + 1);
        if (tabBuilt_[index]) return;
        tabBuilt_[index] = true;
        replaceTabPlaceholder(index, tabFactories_[index]());
    }

    void setTabFactory(int index, std::function<QWidget*()> factory) {
        if (index >= tabFactories_.size()) tabFactories_.resize(index + 1);
        tabFactories_[index] = std::move(factory);
        if (tabBuilt_.size() <= index) tabBuilt_.resize(index + 1);
    }

    QStackedWidget* stack_ = nullptr;
    QVector<QPushButton*> tabs_;
    QVector<QWidget*> tabPlaceholders_;
    QVector<std::function<QWidget*()>> tabFactories_;
    QVector<bool> tabBuilt_;
};

// ─────────────────────────────────────────────
// 悬浮窗外框：顶栏 + 三侧边框以左上圆圈为圆心做圆形 clip 波纹展开
// （旧 tools/danmu_tool._DanmuRoot 与 overtime_tool._OvertimeRoot 的共同部分）
// ─────────────────────────────────────────────
class RippleOverlayRoot : public QWidget {
public:
    static constexpr int kBorderW = 2;
    // 边框外圈光晕：宽度与最深处透明度（贴近 Win11 窗影的淡度）。
    static constexpr int kShadowW = 6;
    static constexpr int kShadowAlpha = 46;
    static constexpr int kTopbarH = 32;
    static constexpr int kResizeHit = 18;
    static constexpr int kBtnW = 32;
    static constexpr int kIconDraw = 18;
    static constexpr int kCircleOff = 0;
    static constexpr qreal kIconStroke = 1.35;
    static constexpr qreal kIconBoxRadius = 2.0;
    static constexpr qreal kIconBoxInset = 4.8;
    static constexpr int kAnimMs = 320;
    static constexpr int kLockAnimMs = 180;
    static constexpr int kHoverAnimMs = 120;

    explicit RippleOverlayRoot(QMainWindow* win, QWidget* parent = nullptr)
        : QWidget(parent), win_(win) {
        setMouseTracking(true);
        setStyleSheet(QStringLiteral("background: transparent;"));
        freeze_ = std::make_unique<liveaio::util::OverlayResizeFreeze>(
            win, [this]() { handleResizeResume(); });

        leftBox_ = new QWidget(this);
        leftBox_->setAttribute(Qt::WA_StyledBackground, true);
        // 连续菜单底由三个按钮各自绘制。容器必须透明，否则按钮隐藏后它仍会留下整条底色。
        leftBox_->setStyleSheet(QStringLiteral("background: transparent;"));
        auto* leftLay = new QHBoxLayout(leftBox_);
        leftLay->setContentsMargins(kCircleOff, 0, 0, 0);
        leftLay->setSpacing(0);
        leftLay->setAlignment(Qt::AlignVCenter);

        frame_ = new FrameBoxButton(leftBox_);
        QObject::connect(frame_, &QPushButton::clicked, this, [this]() {
            if (!frame_->interactionEnabled()) return;
            if (onFrameClicked_) onFrameClicked_();
        });
        leftLay->addWidget(frame_);

        lock_ = new LockBoxButton(leftBox_);
        QObject::connect(lock_, &QPushButton::clicked, this, [this]() {
            if (!lock_->interactionEnabled()) return;
            if (onLockClicked_) onLockClicked_();
        });
        leftLay->addWidget(lock_);

        pin_ = new PinBoxButton(leftBox_);
        QObject::connect(pin_, &QPushButton::clicked, this, [this]() {
            if (onPinClicked_) onPinClicked_();
        });
        leftLay->addWidget(pin_);

        // 点左侧 chrome 钮时也要把本窗抬到置顶层最前（多悬浮窗叠放）。
        auto bringHost = [this]() { bringHostForward(); };
        QObject::connect(frame_, &QPushButton::pressed, this, bringHost);
        QObject::connect(lock_, &QPushButton::pressed, this, bringHost);
        QObject::connect(pin_, &QPushButton::pressed, this, bringHost);

        hideTimer_ = new QTimer(this);
        hideTimer_->setSingleShot(true);
        hideTimer_->setInterval(2000);
        QObject::connect(hideTimer_, &QTimer::timeout, this, [this]() {
            if (transitioning_) return;
            if (!pointerNearControls()) hideControlsNow();
        });

        content_ = new QWidget(this);
        content_->setAttribute(Qt::WA_TranslucentBackground);
        content_->setAttribute(Qt::WA_TransparentForMouseEvents);
        content_->setStyleSheet(QStringLiteral("background: transparent;"));
        watchMouseTree(content_);

        refreshChrome();
        liveaio::util::onThemeChange(this, [this](const QString&) {
            refreshChrome();
            update();
        });
    }

    void setOnFrameClicked(std::function<void()> cb) { onFrameClicked_ = std::move(cb); }
    void setOnLockClicked(std::function<void()> cb) { onLockClicked_ = std::move(cb); }
    void setOnPinClicked(std::function<void()> cb) { onPinClicked_ = std::move(cb); }
    void setOnMinimize(std::function<void()> cb) { onMinimize_ = std::move(cb); }
    void setOnClose(std::function<void()> cb) { onClose_ = std::move(cb); }

    void setChromeState(bool borderShown, bool locked, bool transitioning) {
        borderShown_ = borderShown;
        locked_ = locked;
        transitioning_ = transitioning;
        if (transitioning) {
            hideTimer_->stop();
            frame_->setProgress(borderShown ? 1.0 : 0.0, true);
            lock_->setLocked(locked);
        } else {
            frame_->setProgress(borderShown ? 1.0 : 0.0, false);
            lock_->setLocked(locked);
            lock_->setAccentProgress(borderShown ? 1.0 : 0.0);
            pin_->setAccentProgress(borderShown ? 1.0 : 0.0);
        }
        onChromeVisualSync(borderShown, locked, transitioning);
        onChromeAccentProgress(borderShown ? 1.0 : 0.0);
        refreshControlVisibility();
    }

    void setPinVisual(bool pinned, bool animate) {
        pin_->setPinned(pinned, animate);
    }

    // 编辑护脸区等：强制保持边框「显示」，禁止收起。
    void setFrameForced(bool on) {
        if (frameForced_ == on) return;
        frameForced_ = on;
        if (frameForced_ && !borderShown_) {
            borderShown_ = true;
            frame_->setProgress(1.0, false);
            lock_->setAccentProgress(1.0);
            pin_->setAccentProgress(1.0);
        }
        // 编辑护脸时禁用隐藏/锁定钮；退出后恢复。
        setFrameLockInteractionEnabled(!frameForced_);
        onChromeVisualSync(borderShown_ || frameForced_, locked_, transitioning_);
        onChromeAccentProgress((borderShown_ || frameForced_) ? 1.0 : 0.0);
        refreshControlVisibility();
    }
    bool frameForced() const { return frameForced_; }

    // 编辑态等：灰掉并屏蔽 frame/lock 点击。
    void setFrameLockInteractionEnabled(bool on) {
        if (frame_) frame_->setInteractionEnabled(on);
        if (lock_) lock_->setInteractionEnabled(on);
    }

    bool resizeFrozen() const { return freeze_->frozen(); }
    QWidget* content() const { return content_; }
    qreal radius() const { return r_; }
    QMainWindow* hostWindow() const { return win_; }

    // 空白处点穿到后面的窗口；只拦边框/按钮，以及子类声明的内容命中。
    bool wantsMouseAt(const QPoint& local) const {
        if (dragging_ || resizing_) return true;
        if (auto* grab = QWidget::mouseGrabber()) {
            if (grab == this || isAncestorOf(grab) || grab == win_) return true;
        }
        if (leftBox_ && leftBox_->isVisible() && leftBox_->geometry().contains(local)) {
            return true;
        }
        if (extraChromeContains(local)) return true;
        const QPoint center(static_cast<int>(centerX()), static_cast<int>(centerY()));
        if (QLineF(local, center).length() <= proximityRadiusPx()) return true;
        if (!locked_) {
            if ((borderShown_ || frameForced_) && local.y() >= 0 && local.y() < kTopbarH) return true;
            if (edgeAt(local) != Edge::None) return true;
            if (minBtnRect().contains(local) || closeBtnRect().contains(local)) return true;
        }
        if (content_ && content_->geometry().contains(local)) {
            const QPoint cp(local.x() - content_->x(), local.y() - content_->y());
            if (contentWantsMouse(cp)) return true;
        }
        return false;
    }

    void syncHoverCursor(const QPoint& local) {
        updateProximity(local);
        updateBorderActionHover(local);
        // 左上三钮优先于边框缩放热区（二者在左侧重叠）。
        if (chromeHandAt(local)) {
            applyCursor(Qt::PointingHandCursor, local);
            return;
        }
        if (locked_) {
            applyCursor(Qt::ArrowCursor, local);
            return;
        }
        const Edge edge = edgeAt(local);
        if (edge != Edge::None) {
            applyCursor(cursorFor(edge), local);
            return;
        }
        if (local.y() >= 0 && local.y() < kTopbarH) {
            applyCursor(Qt::ArrowCursor, local);
        }
    }

    // 左上隐藏/锁定/置顶，以及顶栏最小化/关闭：应显示手型。
    bool chromeHandAt(const QPoint& local) const {
        if (leftBox_ && leftBox_->isVisible() && leftBox_->geometry().contains(local)) {
            return true;
        }
        if (extraChromeContains(local)) return true;
        if (!locked_ && borderActionsEnabled()) {
            if (minBtnRect().contains(local) || closeBtnRect().contains(local)) return true;
        }
        return false;
    }

    // 半透明窗默认按 framebuffer alpha 做命中；chrome 热区即使像素几乎透明也要吃鼠标。
    bool chromeSolidHitAt(const QPoint& local) const {
        return chromeHandAt(local);
    }

    qreal maxRadius() const {
        const qreal cx = centerX();
        const qreal cy = centerY();
        return std::max(std::max(std::hypot(cx, cy), std::hypot(width() - cx, cy)),
                        std::max(std::hypot(cx, height() - cy),
                                 std::hypot(width() - cx, height() - cy)));
    }

    void setRadius(qreal r) {
        r_ = r;
        const qreal maxR = std::max(maxRadius(), 1.0);
        const qreal progress = std::clamp(r_ / maxR, 0.0, 1.0);
        // 过渡动画期间由 FrameBoxButton.shapeAnim_ 独占图标形态，避免与波纹 progress 每帧抢写导致闪烁。
        if (!transitioning_) {
            frame_->setProgress(progress, false);
        }
        lock_->setAccentProgress(progress);
        pin_->setAccentProgress(progress);
        onChromeAccentProgress(progress);
        updateLeftBoxMask();
        update();
    }

protected:
    enum class Edge { None, Left, Right, Bottom, BottomLeft, BottomRight };

    // 子类按自身比例约束改写；默认自由缩放。
    virtual QRect resizeGeometry(Edge edge, const QPoint& delta, const QRect& start) const {
        QRect geo = start;
        if (edge == Edge::Right || edge == Edge::BottomRight) geo.setRight(geo.right() + delta.x());
        if (edge == Edge::Left || edge == Edge::BottomLeft) geo.setLeft(geo.left() + delta.x());
        if (edge == Edge::Bottom || edge == Edge::BottomLeft || edge == Edge::BottomRight) {
            geo.setBottom(geo.bottom() + delta.y());
        }
        return geo;
    }

    virtual void onContentGeometryChanged() {}
    // 拖拽中低帧率刷新内容（默认走完整布局；加班机等可改成轻量布局）。
    virtual void onContentGeometryWhileResizing() { onContentGeometryChanged(); }
    virtual void onResizeBegin() {}
    virtual void onResizeResume() {}
    // 叶子等需要缩放同帧更新碰撞/绘制时返回 true，跳过节流。
    virtual bool wantsSyncResizeLayout() const { return false; }
    // 编辑护脸等场景：缩放拖拽时仍开抗锯齿，半透明暗化/阴影跟框同帧高质量。
    virtual bool wantsHighQualityResizePaint() const { return false; }
    virtual bool contentWantsMouse(const QPoint&) const { return false; }

    // 子类（捡叶子）在三钮右侧挂额外 chrome。
    virtual int extraChromeWidth() const { return 0; }
    virtual void layoutExtraChrome(const QRect& /*leftGeo*/) {}
    virtual bool extraChromeContains(const QPoint& /*local*/) const { return false; }
    virtual void setExtraChromeVisible(bool /*vis*/) {}
    // 边框展开进度（0=隐藏光晕悬停，1=展开矩形悬停），额外 chrome 跟 lock/pin 同步。
    virtual void onChromeAccentProgress(qreal /*progress*/) {}
    virtual void onChromeVisualSync(bool /*borderShown*/, bool /*locked*/,
                                    bool /*transitioning*/) {}

    bool resizing() const { return resizing_; }

public:
    // 供叶子等额外 chrome 方钮复用，与 frame/lock/pin 同绘制基线。
    static void startHoverAnimation(QVariantAnimation* anim, qreal from, qreal to) {
        anim->stop();
        anim->setStartValue(from);
        anim->setEndValue(to);
        anim->start();
    }

    static void polishChromeButton(QPushButton* btn) {
        liveaio::util::polishFlatChromeButton(btn);
    }

    static void paintChromeHover(QPainter& p, const QRect& rect,
                                 qreal hover, qreal expanded) {
        const qreal h = std::clamp(hover, 0.0, 1.0);
        const qreal e = std::clamp(expanded, 0.0, 1.0);
        if (h <= 0.001) return;

        if (e > 0.001) {
            QColor fill(theme().btnHover);
            fill.setAlphaF(fill.alphaF() * h * e);
            p.fillRect(rect, fill);
        }
        const qreal glowAmount = h * (1.0 - e);
        if (glowAmount <= 0.001) return;

        QColor center(196, 196, 196, static_cast<int>(120 * glowAmount));
        QColor middle(196, 196, 196, static_cast<int>(52 * glowAmount));
        QColor edge(196, 196, 196, 0);
        QRadialGradient glow(rect.center(), std::min(rect.width(), rect.height()) * 0.52);
        glow.setColorAt(0.0, center);
        glow.setColorAt(0.48, middle);
        glow.setColorAt(1.0, edge);
        p.fillRect(rect, glow);
    }

    static QPen crispIconPen(const QColor& color) {
        QPen pen(color, kIconStroke, Qt::SolidLine, Qt::RoundCap, Qt::RoundJoin);
        pen.setCosmetic(false);
        return pen;
    }

    static void beginCrispIconPaint(QPainter& p, const QWidget* w) {
        p.setRenderHint(QPainter::Antialiasing, true);
        p.setRenderHint(QPainter::TextAntialiasing, false);
        p.setRenderHint(QPainter::SmoothPixmapTransform, false);
        const qreal padX = (w->width() - kIconDraw) / 2.0;
        const qreal padY = (w->height() - kIconDraw) / 2.0;
        p.translate(padX, padY);
    }

    static QRectF iconBox() {
        return QRectF(kIconBoxInset, kIconBoxInset,
                      kIconDraw - kIconBoxInset * 2.0,
                      kIconDraw - kIconBoxInset * 2.0);
    }

    static QColor accentIconColor(qreal accent) {
        const QColor expanded(theme().text);
        const QColor collapsed(148, 148, 148);
        const qreal t = std::clamp(accent, 0.0, 1.0);
        return QColor(
            static_cast<int>(collapsed.red() + (expanded.red() - collapsed.red()) * t),
            static_cast<int>(collapsed.green() + (expanded.green() - collapsed.green()) * t),
            static_cast<int>(collapsed.blue() + (expanded.blue() - collapsed.blue()) * t));
    }

    static void drawCrispRoundBox(QPainter& p, const QRectF& box, qreal radius,
                                  const QColor& color, qreal fillStrength = 0.0) {
        p.setPen(crispIconPen(color));
        if (fillStrength > 0.001) {
            QColor fill = color;
            fill.setAlphaF(std::clamp(fillStrength, 0.0, 1.0));
            p.setBrush(fill);
        } else {
            p.setBrush(Qt::NoBrush);
        }
        p.drawRoundedRect(box, radius, radius);
    }

    void paintEvent(QPaintEvent*) override {
        if (r_ <= 0) return;

        const auto& C = theme();
        const qreal cx = centerX();
        const qreal cy = centerY();

        QPainter p(this);
        // 默认缩放时关 AA 降本；护脸编辑等要求同帧高质量时保持开启。
        const bool hq = !resizing_ || wantsHighQualityResizePaint();
        p.setRenderHint(QPainter::Antialiasing, hq);
        QPainterPath clip;
        clip.addEllipse(cx - r_, cy - r_, r_ * 2, r_ * 2);
        p.setClipPath(clip);

        // 阴影先画，边框后画：最内圈与边框重叠但不会盖掉细边框。
        // 高质量模式下用抗锯齿圆角环，避免拖拽时阴影锯齿与暗化不同步。
        {
            const bool aa = p.renderHints().testFlag(QPainter::Antialiasing);
            p.setBrush(Qt::NoBrush);
            if (hq) {
                p.setRenderHint(QPainter::Antialiasing, true);
                for (int i = 0; i <= kShadowW; ++i) {
                    const qreal t = static_cast<qreal>(i + 1) / (kShadowW + 1);
                    const int alpha = static_cast<int>(std::lround(kShadowAlpha * t * t));
                    if (alpha <= 0) continue;
                    QPen shadowPen(QColor(0, 0, 0, alpha), 1.25);
                    shadowPen.setCapStyle(Qt::RoundCap);
                    shadowPen.setJoinStyle(Qt::RoundJoin);
                    p.setPen(shadowPen);
                    const QRectF r(i + 0.5, i + 0.5,
                                   width() - 1.0 - i * 2, height() - 1.0 - i * 2);
                    p.drawRoundedRect(r, 1.0, 1.0);
                }
            } else {
                p.setRenderHint(QPainter::Antialiasing, false);
                for (int i = 0; i <= kShadowW; ++i) {
                    const qreal t = static_cast<qreal>(i + 1) / (kShadowW + 1);
                    const int alpha = static_cast<int>(std::lround(kShadowAlpha * t * t));
                    if (alpha <= 0) continue;
                    QPen shadowPen(QColor(0, 0, 0, alpha), 1);
                    shadowPen.setCapStyle(Qt::FlatCap);
                    p.setPen(shadowPen);
                    p.drawRect(QRect(i, i, width() - 1 - i * 2, height() - 1 - i * 2));
                }
            }
            p.setRenderHint(QPainter::Antialiasing, aa);
        }

        p.setPen(Qt::NoPen);
        p.setBrush(QColor(C.sidebar));
        p.drawRect(kShadowW, kShadowW, width() - kShadowW * 2, kTopbarH - kShadowW);

        // 边框整体内移 kShadowW，最外圈让给阴影。
        const int hw = std::max(1, kBorderW / 2);
        const int bx = kShadowW + hw;
        QPen pen(QColor(C.sidebar), kBorderW);
        pen.setCapStyle(Qt::FlatCap);
        p.setPen(pen);
        p.setBrush(Qt::NoBrush);
        p.drawLine(bx, kShadowW, bx, height() - kShadowW);
        p.drawLine(width() - bx, kShadowW, width() - bx, height() - kShadowW);
        p.drawLine(kShadowW, height() - bx, width() - kShadowW, height() - bx);

        // 最小化/关闭画在边框 clip 内，与波纹同帧渲染，避免独立控件 mask 闪烁。
        paintBorderActionButtons(p);
    }

    void resizeEvent(QResizeEvent* event) override {
        QWidget::resizeEvent(event);
        const int leftTotal = kCircleOff + kBtnW * 3;
        // 顶栏内容整体让出外圈阴影。
        const QRect leftGeo(kShadowW, kShadowW, leftTotal, kTopbarH - kShadowW);
        leftBox_->setGeometry(leftGeo);
        layoutExtraChrome(leftGeo);
        content_->setGeometry(0, kTopbarH, width(), height() - kTopbarH);
        // 边框已展开时半径跟随窗口，否则放大后圆形 clip 会裁掉新边框。
        if (freeze_->frozen() && r_ > kTopbarH * 0.5) r_ = maxRadius();
        updateLeftBoxMask();
        update();
        if (freeze_->frozen() && !wantsSyncResizeLayout()) {
            requestThrottledContentLayout();
        } else {
            onContentGeometryChanged();
        }
    }

    bool eventFilter(QObject* watched, QEvent* event) override {
        if (event->type() == QEvent::ChildAdded) {
            if (auto* w = qobject_cast<QWidget*>(static_cast<QChildEvent*>(event)->child())) {
                watchMouseTree(w);
            }
            return QWidget::eventFilter(watched, event);
        }
        auto* w = qobject_cast<QWidget*>(watched);
        if (!w || !content_ || locked_) return QWidget::eventFilter(watched, event);
        if (w != content_ && !content_->isAncestorOf(w)) {
            return QWidget::eventFilter(watched, event);
        }
        const auto type = event->type();
        if (type != QEvent::MouseButtonPress && type != QEvent::MouseMove
            && type != QEvent::MouseButtonRelease) {
            return QWidget::eventFilter(watched, event);
        }
        auto* me = static_cast<QMouseEvent*>(event);
        const QPoint local = w->mapTo(this, me->position().toPoint());
        const QPoint global = me->globalPosition().toPoint();
        if (tryChromeMouse(type, local, global, me->buttons(), me->button())) {
            return true;
        }
        return QWidget::eventFilter(watched, event);
    }

    void mousePressEvent(QMouseEvent* event) override {
        if (event->button() != Qt::LeftButton) return;
        bringHostForward();
        if (locked_) return;
        const QPoint pos = event->position().toPoint();
        if (handleBorderActionPress(pos)) return;
        if (beginResizeAt(pos, event->globalPosition().toPoint())) return;
        if (pos.y() < kTopbarH && r_ > kTopbarH * 0.5) {
            dragging_ = true;
            dragAnchor_ = event->globalPosition().toPoint() - win_->pos();
        }
    }

    void mouseMoveEvent(QMouseEvent* event) override {
        const QPoint pos = event->position().toPoint();
        if (locked_) {
            syncHoverCursor(pos);
            return;
        }
        const QPoint gpos = event->globalPosition().toPoint();
        if (event->buttons() & Qt::LeftButton) {
            updateProximity(pos);
            updateBorderActionHover(pos);
            if (applyResizeDrag(gpos)) return;
            if (dragging_) {
                win_->move(gpos - dragAnchor_);
                return;
            }
        }
        syncHoverCursor(pos);
    }

    void enterEvent(QEnterEvent* event) override {
        QWidget::enterEvent(event);
        updateProximity(event->position().toPoint());
        updateBorderActionHover(event->position().toPoint());
    }

    void leaveEvent(QEvent* event) override {
        QWidget::leaveEvent(event);
        if (hoverAction_ != BorderAction::None) {
            hoverAction_ = BorderAction::None;
            update();
        }
        if (controlsNear_ && !hideTimer_->isActive()) hideTimer_->start();
    }

    void mouseReleaseEvent(QMouseEvent*) override {
        endResizeDrag();
    }

private:
    enum class BorderAction { None, Minimize, Close };

    QRect minBtnRect() const {
        return QRect(width() - kShadowW - kBtnW * 2, kShadowW, kBtnW, kTopbarH - kShadowW);
    }
    QRect closeBtnRect() const {
        return QRect(width() - kShadowW - kBtnW, kShadowW, kBtnW, kTopbarH - kShadowW);
    }

    bool borderActionsEnabled() const {
        return borderShown_ && !transitioning_ && !locked_;
    }

    BorderAction hitBorderAction(const QPoint& pos) const {
        if (pos.y() < 0 || pos.y() >= kTopbarH) return BorderAction::None;
        if (closeBtnRect().contains(pos)) return BorderAction::Close;
        if (minBtnRect().contains(pos)) return BorderAction::Minimize;
        return BorderAction::None;
    }

    bool handleBorderActionPress(const QPoint& pos) {
        if (!borderActionsEnabled()) return false;
        switch (hitBorderAction(pos)) {
        case BorderAction::Minimize:
            if (onMinimize_) onMinimize_();
            return true;
        case BorderAction::Close:
            // 工具关闭按钮直接注销宿主槽，不绕一轮窗口关闭事件。
            if (onClose_) onClose_();
            else win_->close();
            return true;
        default:
            return false;
        }
    }

    void updateBorderActionHover(const QPoint& pos) {
        const BorderAction next = borderActionsEnabled() ? hitBorderAction(pos) : BorderAction::None;
        if (next == hoverAction_) return;
        hoverAction_ = next;
        update(QRect(width() - kBtnW * 2, 0, kBtnW * 2, kTopbarH));
    }

    void paintBorderActionButtons(QPainter& p) {
        const auto& C = theme();
        auto paintOne = [&](const QRect& rect, const QString& label, bool closeStyle) {
            const bool hovered = borderActionsEnabled()
                && ((closeStyle && hoverAction_ == BorderAction::Close)
                    || (!closeStyle && hoverAction_ == BorderAction::Minimize));
            if (hovered) {
                p.fillRect(rect, QColor(closeStyle ? C.closeHover : C.btnHover));
            }
            const QColor base(C.textMuted);
            const QColor hot(closeStyle ? QStringLiteral("#ffffff") : C.text);
            p.setPen(hovered ? hot : base);
            QFont f = font();
            f.setPixelSize(14);
            f.setStyleStrategy(QFont::PreferAntialias);
            p.setFont(f);
            p.setRenderHint(QPainter::TextAntialiasing, true);
            p.drawText(rect, Qt::AlignHCenter | Qt::AlignVCenter, label);
        };
        paintOne(minBtnRect(), QStringLiteral("─"), false);
        paintOne(closeBtnRect(), QStringLiteral("✕"), true);
    }

    // 边框按钮：展开=完整正方形（与锁/置顶初始统一）；收起=右上+左下角短边（约留 1/3）。
    class FrameBoxButton final : public QPushButton {
    public:
        explicit FrameBoxButton(QWidget* parent) : QPushButton(parent) {
            setFixedSize(kBtnW, kTopbarH - kShadowW);
            setCursor(Qt::PointingHandCursor);
            polishChromeButton(this);

            hoverAnim_ = new QVariantAnimation(this);
            hoverAnim_->setDuration(kHoverAnimMs);
            hoverAnim_->setEasingCurve(QEasingCurve::OutCubic);
            QObject::connect(hoverAnim_, &QVariantAnimation::valueChanged, this,
                             [this](const QVariant& v) {
                hoverT_ = v.toReal();
                update();
            });
        }

        void setProgress(qreal value, bool animate) {
            const qreal target = std::clamp(value, 0.0, 1.0);
            if (!animate) {
                shapeAnim_.stop();
                shapeT_ = target;
                update();
                return;
            }
            shapeAnim_.stop();
            shapeAnim_.setDuration(kLockAnimMs);
            shapeAnim_.setEasingCurve(QEasingCurve::InOutCubic);
            shapeAnim_.setStartValue(shapeT_);
            shapeAnim_.setEndValue(target);
            if (!shapeConnected_) {
                shapeConnected_ = true;
                QObject::connect(&shapeAnim_, &QVariantAnimation::valueChanged, this,
                                 [this](const QVariant& v) {
                    shapeT_ = v.toReal();
                    update();
                });
            }
            shapeAnim_.start();
        }

        void setInteractionEnabled(bool on) {
            if (interactionEnabled_ == on) return;
            interactionEnabled_ = on;
            setCursor(on ? Qt::PointingHandCursor : Qt::ArrowCursor);
            if (!on) startHoverAnimation(hoverAnim_, hoverT_, 0.0);
            update();
        }
        bool interactionEnabled() const { return interactionEnabled_; }

    protected:
        bool event(QEvent* e) override {
            if (!interactionEnabled_ && (e->type() == QEvent::Enter || e->type() == QEvent::Leave
                                         || e->type() == QEvent::MouseButtonPress
                                         || e->type() == QEvent::MouseButtonRelease)) {
                return true;
            }
            if (e->type() == QEvent::Enter) {
                startHoverAnimation(hoverAnim_, hoverT_, 1.0);
            } else if (e->type() == QEvent::Leave) {
                startHoverAnimation(hoverAnim_, hoverT_, 0.0);
            }
            return QPushButton::event(e);
        }

        void paintEvent(QPaintEvent*) override {
            QPainter p(this);
            // 半透明分层窗按像素 alpha 命中：隐藏态只描两角线时，未绘制区域会点穿。
            // 先铺 1/255 不透明底，保证整块正方形（含未渲染部分）可点可悬停。
            p.fillRect(rect(), QColor(0, 0, 0, 1));
            if (interactionEnabled_) {
                paintChromeHover(p, rect(), hoverT_, shapeT_);
            }

            beginCrispIconPaint(p, this);
            // shapeT_=1 完整圆角方；shapeT_=0 同方框的右上/左下角短边（保持圆角，不裁剪以免闪）
            QColor color = accentIconColor(shapeT_);
            if (!interactionEnabled_) {
                color = QColor(148, 148, 148, 120);
            }
            const QRectF box = iconBox();
            const qreal hideT = 1.0 - shapeT_;
            static constexpr qreal kRemain = 0.382;
            const qreal keepFrac = 1.0 - hideT * (1.0 - kRemain);

            // 用 keepFrac 连续变形，避免接近展开时在「整框 / 短边」间跳切闪一下。
            if (keepFrac >= 0.995) {
                drawCrispRoundBox(p, box, kIconBoxRadius, color, 0.0);
                return;
            }

            const qreal side = std::min(box.width(), box.height());
            const qreal keep = side * keepFrac;
            drawRoundBoxCornerArms(p, box, kIconBoxRadius, keep, color);
        }

        static void drawRoundBoxCornerArms(QPainter& p, const QRectF& box, qreal radius,
                                           qreal keep, const QColor& color) {
            p.setPen(crispIconPen(color));
            p.setBrush(Qt::NoBrush);
            const qreal r = std::min(radius, keep * 0.5);
            // 右上：顶边一段 + 圆角 + 右边一段
            QPainterPath ur;
            ur.moveTo(box.right() - keep, box.top());
            ur.lineTo(box.right() - r, box.top());
            ur.arcTo(QRectF(box.right() - 2.0 * r, box.top(), 2.0 * r, 2.0 * r), 90.0, -90.0);
            ur.lineTo(box.right(), box.top() + keep);
            p.drawPath(ur);
            // 左下：底边一段 + 圆角 + 左边一段
            QPainterPath ll;
            ll.moveTo(box.left() + keep, box.bottom());
            ll.lineTo(box.left() + r, box.bottom());
            ll.arcTo(QRectF(box.left(), box.bottom() - 2.0 * r, 2.0 * r, 2.0 * r), -90.0, -90.0);
            ll.lineTo(box.left(), box.bottom() - keep);
            p.drawPath(ll);
        }

    private:
        QVariantAnimation shapeAnim_{this};
        QVariantAnimation* hoverAnim_ = nullptr;
        qreal shapeT_ = 1.0;
        qreal hoverT_ = 0.0;
        bool shapeConnected_ = false;
        bool interactionEnabled_ = true;
    };

    class LockBoxButton final : public QPushButton {
    public:
        explicit LockBoxButton(QWidget* parent) : QPushButton(parent) {
            setFixedSize(kBtnW, kTopbarH - kShadowW);
            setCursor(Qt::PointingHandCursor);
            setFlat(true);
            polishChromeButton(this);

            lockAnim_ = new QVariantAnimation(this);
            lockAnim_->setDuration(kLockAnimMs);
            lockAnim_->setEasingCurve(QEasingCurve::OutCubic);
            QObject::connect(lockAnim_, &QVariantAnimation::valueChanged, this, [this](const QVariant& v) {
                lockT_ = v.toReal();
                update();
            });

            hoverAnim_ = new QVariantAnimation(this);
            hoverAnim_->setDuration(kHoverAnimMs);
            hoverAnim_->setEasingCurve(QEasingCurve::OutCubic);
            QObject::connect(hoverAnim_, &QVariantAnimation::valueChanged, this, [this](const QVariant& v) {
                hoverT_ = v.toReal();
                update();
            });
        }

        void setLocked(bool locked) {
            if (isLocked_ == locked) return;
            isLocked_ = locked;
            lockAnim_->stop();
            lockAnim_->setStartValue(lockT_);
            lockAnim_->setEndValue(locked ? 1.0 : 0.0);
            lockAnim_->start();
        }

        void setAccentProgress(qreal progress) {
            accentT_ = std::clamp(progress, 0.0, 1.0);
            update();
        }

        void setInteractionEnabled(bool on) {
            if (interactionEnabled_ == on) return;
            interactionEnabled_ = on;
            setCursor(on ? Qt::PointingHandCursor : Qt::ArrowCursor);
            if (!on) startHoverAnimation(hoverAnim_, hoverT_, 0.0);
            update();
        }
        bool interactionEnabled() const { return interactionEnabled_; }

    protected:
        bool event(QEvent* e) override {
            if (!interactionEnabled_ && (e->type() == QEvent::Enter || e->type() == QEvent::Leave
                                         || e->type() == QEvent::MouseButtonPress
                                         || e->type() == QEvent::MouseButtonRelease)) {
                return true;
            }
            if (e->type() == QEvent::Enter) {
                startHoverAnimation(hoverAnim_, hoverT_, 1.0);
            } else if (e->type() == QEvent::Leave) {
                startHoverAnimation(hoverAnim_, hoverT_, 0.0);
            }
            return QPushButton::event(e);
        }

        void paintEvent(QPaintEvent*) override {
            QPainter p(this);
            p.setRenderHint(QPainter::Antialiasing, false);
            if (interactionEnabled_) {
                paintChromeHover(p, rect(), hoverT_, accentT_);
            }

            beginCrispIconPaint(p, this);
            QColor iconColor = accentIconColor(accentT_);
            if (!interactionEnabled_) {
                iconColor = QColor(148, 148, 148, 120);
            }
            drawCrispRoundBox(p, iconBox(), kIconBoxRadius, iconColor, lockT_);
        }

    private:
        bool isLocked_ = false;
        qreal accentT_ = 1.0;
        qreal lockT_ = 0.0;
        qreal hoverT_ = 0.0;
        QVariantAnimation* lockAnim_ = nullptr;
        QVariantAnimation* hoverAnim_ = nullptr;
        bool interactionEnabled_ = true;
    };

    // 置顶按钮：默认单正方形；开启后沿对角线拆成两块（左上 / 右下错位）。
    class PinBoxButton final : public QPushButton {
    public:
        explicit PinBoxButton(QWidget* parent) : QPushButton(parent) {
            setFixedSize(kBtnW, kTopbarH - kShadowW);
            setCursor(Qt::PointingHandCursor);
            setFlat(true);
            polishChromeButton(this);

            pinAnim_ = new QVariantAnimation(this);
            pinAnim_->setDuration(kLockAnimMs);
            pinAnim_->setEasingCurve(QEasingCurve::OutCubic);
            QObject::connect(pinAnim_, &QVariantAnimation::valueChanged, this, [this](const QVariant& v) {
                pinT_ = v.toReal();
                update();
            });

            hoverAnim_ = new QVariantAnimation(this);
            hoverAnim_->setDuration(kHoverAnimMs);
            hoverAnim_->setEasingCurve(QEasingCurve::OutCubic);
            QObject::connect(hoverAnim_, &QVariantAnimation::valueChanged, this, [this](const QVariant& v) {
                hoverT_ = v.toReal();
                update();
            });
        }

        void setPinned(bool pinned, bool animate) {
            if (isPinned_ == pinned) {
                if (!animate) {
                    pinAnim_->stop();
                    pinT_ = pinned ? 1.0 : 0.0;
                    update();
                }
                return;
            }
            isPinned_ = pinned;
            if (!animate) {
                pinAnim_->stop();
                pinT_ = pinned ? 1.0 : 0.0;
                update();
                return;
            }
            pinAnim_->stop();
            pinAnim_->setStartValue(pinT_);
            pinAnim_->setEndValue(pinned ? 1.0 : 0.0);
            pinAnim_->start();
        }

        void setAccentProgress(qreal progress) {
            accentT_ = std::clamp(progress, 0.0, 1.0);
            update();
        }

    protected:
        bool event(QEvent* e) override {
            if (e->type() == QEvent::Enter) {
                startHoverAnimation(hoverAnim_, hoverT_, 1.0);
            } else if (e->type() == QEvent::Leave) {
                startHoverAnimation(hoverAnim_, hoverT_, 0.0);
            }
            return QPushButton::event(e);
        }

        void paintEvent(QPaintEvent*) override {
            QPainter p(this);
            p.setRenderHint(QPainter::Antialiasing, false);
            paintChromeHover(p, rect(), hoverT_, accentT_);

            beginCrispIconPaint(p, this);
            const QColor iconColor = accentIconColor(accentT_);
            const QRectF box = iconBox();
            // 两同尺寸方框沿对角线错位；相对位移 dx=dy=2o 时重叠面积比 = ((s-2o)/s)^2
            // 厚描边下重叠易脏：目标约 40%
            const qreal s = std::min(box.width(), box.height());
            static constexpr qreal kOverlap = 0.40;
            const qreal oMax = s * (1.0 - std::sqrt(kOverlap)) / 2.0;
            const qreal o = pinT_ * oMax;
            if (pinT_ < 0.02) {
                drawCrispRoundBox(p, box, kIconBoxRadius, iconColor, 0.0);
            } else {
                drawCrispRoundBox(p, box.translated(-o, -o), kIconBoxRadius, iconColor, 0.0);
                drawCrispRoundBox(p, box.translated(o, o), kIconBoxRadius, iconColor, 0.0);
            }
        }

    private:
        bool isPinned_ = false;
        qreal accentT_ = 1.0;
        qreal pinT_ = 0.0;
        qreal hoverT_ = 0.0;
        QVariantAnimation* pinAnim_ = nullptr;
        QVariantAnimation* hoverAnim_ = nullptr;
    };

    // 三钮等距：邻近显隐圆心取 leftBox 几何中心（含阴影内缩）。
    static qreal centerX() { return kShadowW + kCircleOff + (kBtnW * 3) / 2.0; }
    static qreal centerY() { return kShadowW + (kTopbarH - kShadowW) / 2.0; }

    void refreshChrome() {
        if (leftBox_) {
            leftBox_->setStyleSheet(QStringLiteral("background: transparent;"));
        }
        frame_->update();
        lock_->update();
        pin_->update();
        update(QRect(width() - kBtnW * 2, 0, kBtnW * 2, kTopbarH));
    }

    void bringHostForward() {
        if (!win_) return;
        win_->raise();
        win_->activateWindow();
#ifdef Q_OS_WIN
        // 多个 TOPMOST 叠放时，再点一次 HWND_TOPMOST 把本窗抬到置顶组最前。
        const HWND hwnd = reinterpret_cast<HWND>(win_->winId());
        if (!hwnd) return;
        const LONG_PTR ex = GetWindowLongPtr(hwnd, GWL_EXSTYLE);
        if (ex & WS_EX_TOPMOST) {
            SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
        }
#endif
    }

    int proximityRadiusPx() const {
        const QScreen* screen = win_ ? win_->screen() : QApplication::primaryScreen();
        const qreal dpi = screen ? screen->logicalDotsPerInch() : 96.0;
        return std::max(48, static_cast<int>(std::lround(dpi * 3.0 / 2.54)));
    }

    bool pointerNearControls() const {
        const QPoint local = mapFromGlobal(QCursor::pos());
        const QPoint center(static_cast<int>(centerX()), static_cast<int>(centerY()));
        return QLineF(local, center).length() <= proximityRadiusPx();
    }

    void updateProximity(const QPoint& local) {
        if (transitioning_) return;
        const QPoint center(static_cast<int>(centerX()), static_cast<int>(centerY()));
        if (QLineF(local, center).length() <= proximityRadiusPx()) {
            hideTimer_->stop();
            controlsNear_ = true;
            refreshControlVisibility();
        } else if (controlsNear_ && !hideTimer_->isActive()) {
            hideTimer_->start();
        }
    }

    void hideControlsNow() {
        if (transitioning_) return;
        controlsNear_ = false;
        refreshControlVisibility();
    }

    // leftBox_ 曾用圆形 mask 跟随波纹半径；半径动画时逐帧裁切会触发子控件
    // Enter/Leave，隐藏/显示钮因此闪烁。菜单底已透明，不再需要 mask。
    void updateLeftBoxMask() {
        if (!leftBox_) return;
        leftBox_->clearMask();
    }

    void refreshControlVisibility() {
        const bool showChrome = borderShown_ || frameForced_ || controlsNear_ || transitioning_;
        const QRect dirty = leftBox_ ? leftBox_->geometry()
                                     : QRect(0, 0, kCircleOff + kBtnW * 3, kTopbarH);
        leftBox_->setVisible(showChrome);
        frame_->setVisible(showChrome);
        lock_->setVisible(showChrome);
        pin_->setVisible(showChrome);
        setExtraChromeVisible(showChrome);
        if (showChrome) {
            leftBox_->raise();
            frame_->raise();
            lock_->raise();
            pin_->raise();
            updateLeftBoxMask();
        }
        // 隐藏子控件后强制父级擦除原区域，半透明窗口不会留下上一帧菜单底。
        update(dirty.adjusted(-1, -1, 1, 1));
        if (extraChromeWidth() > 0 && leftBox_) {
            update(QRect(leftBox_->geometry().right(), leftBox_->y(),
                         extraChromeWidth() + 8, leftBox_->height()).adjusted(-1, -1, 1, 1));
        }
        update(QRect(width() - kBtnW * 2, 0, kBtnW * 2, kTopbarH));
    }

    void requestThrottledContentLayout() {
        if (!contentThrottle_) {
            contentThrottle_ = new QTimer(this);
            contentThrottle_->setSingleShot(true);
            QObject::connect(contentThrottle_, &QTimer::timeout, this, [this]() {
                if (freeze_->frozen()) onContentGeometryWhileResizing();
            });
        }
        if (!contentThrottle_->isActive()) contentThrottle_->start(kContentThrottleMs);
    }

    void handleResizeResume() {
        if (contentThrottle_) contentThrottle_->stop();
        onContentGeometryChanged();
        onResizeResume();
    }

    void watchMouseTree(QWidget* w) {
        if (!w) return;
        w->installEventFilter(this);
        w->setMouseTracking(true);
        const auto kids = w->findChildren<QWidget*>(Qt::FindDirectChildrenOnly);
        for (QWidget* c : kids) watchMouseTree(c);
    }

    void applyCursor(Qt::CursorShape shape, const QPoint& local) {
        auto set = [shape](QWidget* w) {
            if (w && w->cursor().shape() != shape) w->setCursor(shape);
        };
        set(this);
        set(win_);
        set(content_);
        if (QWidget* hit = childAt(local)) {
            set(hit);
            for (QWidget* p = hit->parentWidget(); p && p != this; p = p->parentWidget()) {
                set(p);
            }
        }
#ifdef Q_OS_WIN
        // WM_SETCURSOR 路径靠这里真正改系统指针；必须覆盖 PointingHand。
        LPCWSTR id = IDC_ARROW;
        switch (shape) {
        case Qt::SizeHorCursor: id = IDC_SIZEWE; break;
        case Qt::SizeVerCursor: id = IDC_SIZENS; break;
        case Qt::SizeFDiagCursor: id = IDC_SIZENWSE; break;
        case Qt::SizeBDiagCursor: id = IDC_SIZENESW; break;
        case Qt::PointingHandCursor:
        case Qt::OpenHandCursor:
        case Qt::ClosedHandCursor: id = IDC_HAND; break;
        default: break;
        }
        SetCursor(LoadCursor(nullptr, id));
#endif
    }

    bool beginResizeAt(const QPoint& localPos, const QPoint& globalPos) {
        const Edge edge = edgeAt(localPos);
        if (edge == Edge::None) return false;
        freeze_->begin();
        resizing_ = true;
        onResizeBegin();
        resizeEdge_ = edge;
        resizeStartGeo_ = win_->geometry();
        resizeStartPos_ = globalPos;
        applyCursor(cursorFor(edge), localPos);
        grabMouse();
        return true;
    }

    bool applyResizeDrag(const QPoint& globalPos) {
        if (!resizing_) return false;
        const QRect geo = resizeGeometry(resizeEdge_, globalPos - resizeStartPos_, resizeStartGeo_);
        if (geo.width() >= win_->minimumWidth() && geo.height() >= win_->minimumHeight()
            && geo.width() > 0 && geo.height() > 0) {
            win_->setGeometry(geo);
        }
        return true;
    }

    void endResizeDrag() {
        const bool wasResize = resizing_;
        if (wasResize && QWidget::mouseGrabber() == this) releaseMouse();
        dragging_ = false;
        resizing_ = false;
        resizeEdge_ = Edge::None;
        syncHoverCursor(mapFromGlobal(QCursor::pos()));
        if (wasResize) freeze_->end();
    }

    // 内容层（叶子画布等）盖住边框热区时，仍由外壳处理缩放。
    bool tryChromeMouse(QEvent::Type type, const QPoint& local, const QPoint& global,
                        Qt::MouseButtons buttons, Qt::MouseButton button) {
        if (type == QEvent::MouseButtonPress) {
            if (button != Qt::LeftButton) return false;
            bringHostForward();
            if (locked_) return false;
            if (handleBorderActionPress(local)) return true;
            return beginResizeAt(local, global);
        }
        if (type == QEvent::MouseMove) {
            updateProximity(local);
            updateBorderActionHover(local);
            if (locked_) return false;
            if (buttons & Qt::LeftButton) {
                if (applyResizeDrag(global)) return true;
            }
            if (resizing_) return true;
            const Edge edge = edgeAt(local);
            if (edge != Edge::None && !(buttons & Qt::LeftButton)) {
                applyCursor(cursorFor(edge), local);
                return true;
            }
            return false;
        }
        if (type == QEvent::MouseButtonRelease) {
            if (!resizing_) return false;
            endResizeDrag();
            return true;
        }
        return false;
    }

    Edge edgeAt(const QPoint& pos) const {
        // 边框隐藏/过渡/锁定时不允许缩放；左上三钮与缩放热区重叠时也不抢命中。
        if ((!borderShown_ && !frameForced_) || transitioning_ || locked_) return Edge::None;
        if (leftBox_ && leftBox_->isVisible() && leftBox_->geometry().contains(pos)) {
            return Edge::None;
        }
        if (extraChromeContains(pos)) return Edge::None;
        if (minBtnRect().contains(pos) || closeBtnRect().contains(pos)) {
            return Edge::None;
        }
        const bool left = pos.x() < kResizeHit;
        const bool right = pos.x() > width() - kResizeHit;
        const bool bottom = pos.y() > height() - kResizeHit;
        if (left && bottom) return Edge::BottomLeft;
        if (right && bottom) return Edge::BottomRight;
        if (left) return Edge::Left;
        if (right) return Edge::Right;
        if (bottom) return Edge::Bottom;
        return Edge::None;
    }

    static Qt::CursorShape cursorFor(Edge edge) {
        switch (edge) {
        case Edge::Left:
        case Edge::Right: return Qt::SizeHorCursor;
        case Edge::Bottom: return Qt::SizeVerCursor;
        case Edge::BottomLeft: return Qt::SizeBDiagCursor;
        case Edge::BottomRight: return Qt::SizeFDiagCursor;
        default: return Qt::ArrowCursor;
        }
    }

    static constexpr int kContentThrottleMs = 80;  // 拖拽中内容约 12fps

    QMainWindow* win_ = nullptr;
    QWidget* leftBox_ = nullptr;
    QWidget* content_ = nullptr;
    FrameBoxButton* frame_ = nullptr;
    LockBoxButton* lock_ = nullptr;
    PinBoxButton* pin_ = nullptr;
    QTimer* hideTimer_ = nullptr;
    QTimer* contentThrottle_ = nullptr;
    std::unique_ptr<liveaio::util::OverlayResizeFreeze> freeze_;
    std::function<void()> onFrameClicked_;
    std::function<void()> onLockClicked_;
    std::function<void()> onPinClicked_;
    std::function<void()> onMinimize_;
    std::function<void()> onClose_;
    qreal r_ = 0.0;
    bool borderShown_ = true;
    bool frameForced_ = false;
    bool locked_ = false;
    bool transitioning_ = false;
    bool controlsNear_ = false;
    BorderAction hoverAction_ = BorderAction::None;
    bool dragging_ = false;
    QPoint dragAnchor_;
    bool resizing_ = false;
    Edge resizeEdge_ = Edge::None;
    QRect resizeStartGeo_;
    QPoint resizeStartPos_;
};

// 半透明悬浮窗：几何持久化 + 波纹展开动画，供弹幕机/加班机复用。
class RippleOverlayWindow : public QMainWindow {
public:
    RippleOverlayWindow(const QString& title, const QString& geoKey)
        : QMainWindow(nullptr, Qt::FramelessWindowHint | Qt::Window), geoKey_(geoKey) {
        setAttribute(Qt::WA_TranslucentBackground);
        setAttribute(Qt::WA_QuitOnClose, false);
        liveaio::util::enableCaptureTransparency(this);
        setWindowTitle(title);
        setStyleSheet(liveaio::util::popupChromeQss());
        liveaio::util::onThemeChange(this, [this](const QString&) {
            setStyleSheet(liveaio::util::popupChromeQss());
        });

        anim_ = new QVariantAnimation(this);
        anim_->setDuration(RippleOverlayRoot::kAnimMs);
        anim_->setEasingCurve(QEasingCurve::OutQuart);
        QObject::connect(anim_, &QVariantAnimation::valueChanged, this, [this](const QVariant& v) {
            animR_ = v.toReal();
            if (root_) root_->setRadius(animR_);
        });
        QObject::connect(anim_, &QVariantAnimation::finished, this, [this]() {
            if (root_) root_->setChromeState(shown_, locked_, false);
        });
    }

    void setOnClosed(std::function<void()> cb) { onClosed_ = std::move(cb); }

    void setGeometryKey(const QString& key) { geoKey_ = key; }

    RippleOverlayRoot* takeRoot() {
        RippleOverlayRoot* r = root_;
        setCentralWidget(nullptr);
        root_ = nullptr;
        return r;
    }

    void toggleFrame() {
        if (!root_) return;
        if (locked_ && !shown_) return;
        // 护脸编辑等强制显示时禁止收起边框。
        if (frameForced_ && shown_) return;
        shown_ = !shown_;
        anim_->stop();
        root_->setChromeState(shown_, locked_, true);
        anim_->setStartValue(animR_);
        anim_->setEndValue(shown_ ? std::max(root_->maxRadius(), 1.0) : 0.0);
        anim_->start();
    }

    void setFrameShown(bool shown) {
        if (shown_ == shown || (shown && locked_ && !frameForced_)) return;
        if (!shown && frameForced_) return;
        toggleFrame();
    }

    void setFrameForced(bool on) {
        if (frameForced_ == on) return;
        frameForced_ = on;
        if (root_) root_->setFrameForced(on);
        if (on) {
            if (locked_) {
                // 强制显示优先于锁定收起：解锁边框视觉但仍可保持 locked_ 状态？
                // 计划：编辑态强制展开；若当前锁定则先解锁以免无法 show。
                locked_ = false;
                if (root_) root_->setChromeState(true, false, false);
            }
            if (!shown_) {
                shown_ = true;
                anim_->stop();
                if (root_) {
                    root_->setChromeState(true, locked_, true);
                    anim_->setStartValue(animR_);
                    anim_->setEndValue(std::max(root_->maxRadius(), 1.0));
                    anim_->start();
                }
            } else if (root_) {
                root_->setChromeState(true, locked_, false);
            }
        }
    }
    bool frameForced() const { return frameForced_; }

    void setLocked(bool locked) {
        // 护脸编辑强制显示期间不允许锁定。
        if (frameForced_ && locked) return;
        if (locked_ == locked) return;
        const bool wasLocked = locked_;
        locked_ = locked;
        if (locked_ && shown_) {
            shown_ = false;
            anim_->stop();
            if (root_) root_->setChromeState(false, true, true);
            anim_->setStartValue(animR_);
            anim_->setEndValue(0.0);
            anim_->start();
            return;
        }
        if (!locked_ && wasLocked) {
            shown_ = true;
            anim_->stop();
            if (root_) root_->setChromeState(true, false, true);
            anim_->setStartValue(animR_);
            anim_->setEndValue(root_ ? std::max(root_->maxRadius(), 1.0) : 0.0);
            anim_->start();
            return;
        }
        if (root_) root_->setChromeState(shown_, locked_, false);
    }

    // 置顶与锁定无关：锁定时仍可开关；不强制绑定边框显隐。
    void setPinned(bool pinned) {
        if (pinned_ == pinned) return;
        pinned_ = pinned;
        applyTopmost(pinned_);
        if (root_) root_->setPinVisual(pinned_, true);
        if (pinned_) {
            // 置顶后保持可绘制：不因失焦被压到下层后停更。
            raise();
            update();
        }
    }

    bool frameShown() const { return shown_; }
    bool locked() const { return locked_; }
    bool pinned() const { return pinned_; }

    bool applyChromeCommand(const QString& action) {
        if (action == QStringLiteral("frame.toggle")) setFrameShown(!shown_);
        else if (action == QStringLiteral("frame.show")) setFrameShown(true);
        else if (action == QStringLiteral("frame.hide")) setFrameShown(false);
        else if (action == QStringLiteral("lock.toggle")) setLocked(!locked_);
        else if (action == QStringLiteral("lock")) setLocked(true);
        else if (action == QStringLiteral("unlock")) setLocked(false);
        else if (action == QStringLiteral("pin.toggle")) setPinned(!pinned_);
        else if (action == QStringLiteral("pin")) setPinned(true);
        else if (action == QStringLiteral("unpin")) setPinned(false);
        else return false;
        return true;
    }

    void resetChromeForOpen() {
        anim_->stop();
        shown_ = true;
        locked_ = false;
        if (pinned_) {
            pinned_ = false;
            applyTopmost(false);
        }
        if (root_) root_->setPinVisual(false, false);
    }

    void minimizeOverlay() {
        if (shown_) toggleFrame();
        if (pinned_) {
            // 最小化只收边框：保持 TOPMOST，后台继续定时/绘制。
            raise();
            applyTopmost(true);
            update();
        } else {
            lower();
        }
    }

    void syncRadiusAfterResize() {
        if (!root_) return;
        if (shown_ && !animRunning()) animR_ = root_->maxRadius();
        root_->setRadius(animR_);
    }

    void restoreGeometryFromConfig(int defW, int defH) {
        const QVariantMap saved = configValue(geoKey_).toMap();
        if (saved.isEmpty()) {
            resize(defW, defH);
            return;
        }
        const int x = saved.value(QStringLiteral("x")).toInt();
        const int y = saved.value(QStringLiteral("y")).toInt();
        const int w = qMax(minimumWidth(), saved.value(QStringLiteral("w"), defW).toInt());
        const int h = qMax(minimumHeight(), saved.value(QStringLiteral("h"), defH).toInt());
        bool onScreen = false;
        for (auto* screen : QApplication::screens()) {
            if (screen->availableGeometry().contains(QRect(x, y, 1, 1))) {
                onScreen = true;
                break;
            }
        }
        if (onScreen) setGeometry(x, y, w, h);
        else resize(w, h);
    }

protected:
    void attachRoot(RippleOverlayRoot* root) {
        root_ = root;
        setCentralWidget(root_);
        root_->setOnFrameClicked([this]() { toggleFrame(); });
        root_->setOnLockClicked([this]() { setLocked(!locked_); });
        root_->setOnPinClicked([this]() { setPinned(!pinned_); });
        root_->setOnMinimize([this]() { minimizeOverlay(); });
        root_->setChromeState(shown_, locked_, false);
        root_->setPinVisual(pinned_, false);
    }

    RippleOverlayRoot* root() const { return root_; }
    bool animRunning() const { return anim_->state() == QAbstractAnimation::Running; }

    void showEvent(QShowEvent* event) override {
        QMainWindow::showEvent(event);
        if (!firstShow_) return;
        firstShow_ = false;
        // 布局完成后才知道 maxRadius，延后一帧初始化半径。
        QTimer::singleShot(0, this, [this]() {
            if (!root_) return;
            qreal r = root_->maxRadius();
            if (r <= 0) r = 800.0;
            animR_ = r;
            shown_ = true;
            root_->setRadius(r);
            root_->setChromeState(true, locked_, false);
        });
    }

    void hideEvent(QHideEvent* event) override {
        QMainWindow::hideEvent(event);
        const QRect geo = geometry();
        writeConfigValue(geoKey_, QVariantMap{
            {QStringLiteral("x"), geo.x()},
            {QStringLiteral("y"), geo.y()},
            {QStringLiteral("w"), geo.width()},
            {QStringLiteral("h"), geo.height()},
        });
    }

    void closeEvent(QCloseEvent* event) override {
        event->ignore();
        hide();
        if (onClosed_) onClosed_();
    }

    bool nativeEvent(const QByteArray& eventType, void* message, qintptr* result) override {
#ifdef Q_OS_WIN
        if (eventType == QByteArrayLiteral("windows_generic_MSG")
            || eventType == QByteArrayLiteral("windows_dispatcher_MSG")) {
            auto* msg = static_cast<MSG*>(message);
            if (msg->message == WM_NCHITTEST && result) {
                if (!root_) {
                    *result = HTTRANSPARENT;
                    return true;
                }
                const QPoint lp = root_->mapFromGlobal(QCursor::pos());
                if (root_->wantsMouseAt(lp)) {
                    root_->syncHoverCursor(lp);
                    // 半透明分层窗默认按像素 alpha 命中；强制 chrome 钮整块 HTCLIENT，
                    // 否则隐藏态 frame 只画两角线时，正方形空白处会点穿。
                    if (root_->chromeSolidHitAt(lp)) {
                        *result = HTCLIENT;
                        return true;
                    }
                    return QMainWindow::nativeEvent(eventType, message, result);
                }
                *result = HTTRANSPARENT;
                return true;
            }
            if (msg->message == WM_SETCURSOR && result && root_) {
                const QPoint lp = root_->mapFromGlobal(QCursor::pos());
                if (root_->wantsMouseAt(lp)) {
                    root_->syncHoverCursor(lp);
                    *result = TRUE;
                    return true;
                }
            }
        }
#endif
        return QMainWindow::nativeEvent(eventType, message, result);
    }

    void applyTopmost(bool on) {
#ifdef Q_OS_WIN
        const HWND hwnd = reinterpret_cast<HWND>(winId());
        if (!hwnd) return;
        SetWindowPos(hwnd, on ? HWND_TOPMOST : HWND_NOTOPMOST, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
#else
        Qt::WindowFlags f = windowFlags();
        if (on) f |= Qt::WindowStaysOnTopHint;
        else f &= ~Qt::WindowStaysOnTopHint;
        const bool vis = isVisible();
        setWindowFlags(f);
        if (vis) {
            show();
            if (on) raise();
        }
#endif
    }

private:
    QString geoKey_;
    RippleOverlayRoot* root_ = nullptr;
    QVariantAnimation* anim_ = nullptr;
    qreal animR_ = 0.0;
    bool shown_ = true;
    bool frameForced_ = false;
    bool locked_ = false;
    bool pinned_ = false;
    bool firstShow_ = true;
    std::function<void()> onClosed_;
};

class SharedOverlayShell final : public RippleOverlayWindow {
public:
    explicit SharedOverlayShell(const QString& key)
        : RippleOverlayWindow(QStringLiteral("悬浮窗"), key) {}

    void prepare(const QString& title, const QString& geoKey, int minW, int minH, int defW, int defH) {
        resetChromeForOpen();
        setWindowTitle(title);
        setGeometryKey(geoKey);
        setMinimumSize(minW, minH);
        restoreGeometryFromConfig(defW, defH);
    }

    void mountRoot(RippleOverlayRoot* newRoot, WidgetDeferredDestroy& destroyer) {
        if (!newRoot) return;
        RippleOverlayRoot* old = takeRoot();
        if (old && old != newRoot) destroyer.enqueue(old);
        attachRoot(newRoot);
        syncRadiusAfterResize();
    }

    void afterShowContent() {
        syncRadiusAfterResize();
        if (root()) root()->updateGeometry();
    }

    bool hasMountedContent() const { return root() != nullptr; }
};

enum class OverlayToolId { None, Danmu, Overtime, Leaf };

class OverlayHostService final : public QObject {
public:
    static OverlayHostService& instance() {
        static OverlayHostService* host = new OverlayHostService(qApp);
        return *host;
    }

    bool isToolActive(OverlayToolId id) const {
        const Slot* s = slot(id);
        return s && s->shell && s->shell->isVisible() && s->shell->hasMountedContent();
    }

    // 预备壳（不 show）：进设置页 ensure 时调用，不提前挂 Root / 不渲染。
    void prepare(OverlayToolId id) {
        Slot* s = slot(id);
        if (!s) return;
        ensureShell(id, *s);
    }

    SharedOverlayShell* shell(OverlayToolId id) {
        Slot* s = slot(id);
        if (!s) return nullptr;
        ensureShell(id, *s);
        return s->shell;
    }

    void show(OverlayToolId tool, const QString& title, const QString& geoKey, int minW, int minH,
              int defW, int defH, RippleOverlayRoot* root, std::function<void()> onClosed) {
        if (!root) return;

        Slot* s = slot(tool);
        if (!s) return;
        if (s->shell && s->shell->hasMountedContent()) detachContent(*s, s->closedCb);
        s->closedCb = std::move(onClosed);

        ensureShell(tool, *s);
        s->shell->prepare(title, geoKey, minW, minH, defW, defH);
        s->shell->setOnClosed([this, tool]() { teardown(tool); });
        s->shell->mountRoot(root, s->destroyer);
        root->setOnClose([this, tool]() { teardown(tool); });
        s->shell->show();
        s->shell->activateWindow();
        QPointer<SharedOverlayShell> guard(s->shell);
        QTimer::singleShot(0, s->shell, [guard]() {
            if (guard) guard->afterShowContent();
        });
    }

    void teardown(OverlayToolId tool, std::function<void()> done = nullptr) {
        Slot* s = slot(tool);
        if (!s) return;
        std::function<void()> cb = done ? done : s->closedCb;
        s->closedCb = nullptr;
        detachContent(*s, cb);
    }

    bool command(OverlayToolId tool, const QString& action) {
        Slot* s = slot(tool);
        if (!s || !s->shell || !isToolActive(tool)) return false;
        if (action == QStringLiteral("close")) {
            teardown(tool);
            return true;
        }
        return s->shell->applyChromeCommand(action);
    }

    int stateBits(OverlayToolId tool) const {
        const Slot* s = slot(tool);
        if (!s || !s->shell || !isToolActive(tool)) return 0;
        return 1
            | (s->shell->frameShown() ? 2 : 0)
            | (s->shell->locked() ? 4 : 0)
            | (s->shell->pinned() ? 8 : 0);
    }

    // 主窗全退出（非托盘收起）时连锁拆掉所有透明悬浮窗。
    void closeAll() {
        teardown(OverlayToolId::Danmu);
        teardown(OverlayToolId::Overtime);
        teardown(OverlayToolId::Leaf);
    }

private:
    struct Slot {
        explicit Slot(QObject* parent) : destroyer(parent) {}
        SharedOverlayShell* shell = nullptr;
        WidgetDeferredDestroy destroyer;
        std::function<void()> closedCb;
    };

    explicit OverlayHostService(QObject* parent)
        : QObject(parent), danmu_(this), overtime_(this), leaf_(this) {}

    Slot* slot(OverlayToolId id) {
        if (id == OverlayToolId::Danmu) return &danmu_;
        if (id == OverlayToolId::Overtime) return &overtime_;
        if (id == OverlayToolId::Leaf) return &leaf_;
        return nullptr;
    }
    const Slot* slot(OverlayToolId id) const {
        if (id == OverlayToolId::Danmu) return &danmu_;
        if (id == OverlayToolId::Overtime) return &overtime_;
        if (id == OverlayToolId::Leaf) return &leaf_;
        return nullptr;
    }

    // 活跃实例立即注销；轻量壳留在单例宿主池中供再次打开复用。
    // 每个可见顶层窗口仍需要独立原生表面，内容与共享资源不重复常驻。
    void detachContent(Slot& s, std::function<void()> notify) {
        if (s.shell) s.shell->hide();
        RippleOverlayRoot* root = s.shell ? s.shell->takeRoot() : nullptr;
        if (root) root->setOnClose(nullptr);
        if (notify) notify();
        if (root) s.destroyer.enqueue(root);
    }

    void ensureShell(OverlayToolId tool, Slot& s) {
        if (s.shell) return;
        QString key = QStringLiteral("overlay_window_geometry");
        if (tool == OverlayToolId::Danmu) key = QStringLiteral("danmu_window_geometry");
        else if (tool == OverlayToolId::Overtime) key = QStringLiteral("overtime_window_geometry");
        else if (tool == OverlayToolId::Leaf) key = QStringLiteral("leaf_window_geometry");
        s.shell = new SharedOverlayShell(key);
    }

    Slot danmu_;
    Slot overtime_;
    Slot leaf_;
};

// 旧 memo._section：卡片 + 标题 + 若干「文字 / 控件」行。
static QFrame* sectionCard(const QString& title, const QVector<QPair<QString, QWidget*>>& rows,
                           QWidget* parent = nullptr) {
    auto* card = new QFrame(parent);
    card->setObjectName(QStringLiteral("Card"));
    auto* lay = new QVBoxLayout(card);
    lay->setContentsMargins(16, 14, 16, 14);
    lay->setSpacing(10);
    auto* t = new QLabel(title, card);
    t->setObjectName(QStringLiteral("SectionTitle"));
    lay->addWidget(t);
    for (const auto& row : rows) lay->addLayout(labelRow(row.first, row.second));
    return card;
}

}  // namespace liveaio::tools
