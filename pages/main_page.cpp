// pages/main_page.cpp — 主窗口壳，对齐旧 PySide MainPage。
// 无边框圆角卡片（不自绘阴影）+ 36px 标题栏 + 220/64 侧栏动画；侧栏页首次进入才建。

namespace liveaio::pages {

using liveaio::util::deferNextTick;

struct PageMeta {
    QString icon;
    QString name;
    std::function<BasePage*(CoreClient*, QWidget*)> factory;
};

static QVector<PageMeta> pageCatalog() {
    return {
        {QStringLiteral("🏠"), QStringLiteral("主页"),
         [](CoreClient* core, QWidget* parent) -> BasePage* { return new HomePage(core, parent); }},
        {QStringLiteral("⚒"), QStringLiteral("工具"),
         [](CoreClient* core, QWidget* parent) -> BasePage* { return new ToolsPage(core, parent); }},
    };
}

class MainWindow final : public QWidget {
public:
    explicit MainWindow(CoreClient* core, QWidget* parent = nullptr)
        : QWidget(parent), core_(core), metas_(pageCatalog()) {
        setWindowTitle(QStringLiteral("LiveAIO"));
        setWindowFlags(Qt::FramelessWindowHint | Qt::Window);
        setAttribute(Qt::WA_TranslucentBackground, true);
        setMinimumSize(900, 600);
        resize(1200, 750);

        pages_.resize(metas_.size());
        pages_.fill(nullptr);
        settingsIndex_ = metas_.size();

        buildUi();
        connectNav();
        ensureMainPage(0);
        navigateMain(0);

        applyTheme();
        liveaio::util::onThemeChange(this, [this](const QString&) { applyTheme(); });
    }

    void raiseFromTray() { restoreWindowChrome(); }

    void onCorePacket(const QJsonObject& packet) {
        const QString op = packet.value(QStringLiteral("op")).toString();
        // 托盘或第二次启动请求显示界面：本进程已在跑，直接抬起窗口。
        if (op == QStringLiteral("ui.focus")) {
            restoreWindowChrome();
            return;
        }
        // 托盘经 Core 广播；Pages 在独立进程里，不能再走 Go LoadLibrary 调导出。
        if (op == QStringLiteral("ui.overlay")) {
            const QString tool = packet.value(QStringLiteral("tool")).toString().trimmed();
            const QString action = packet.value(QStringLiteral("action")).toString().trimmed();
            if (!tool.isEmpty() && !action.isEmpty()) {
                QString error;
                ToolsPlugin::instance().overlayCommand(tool, action, &error);
                if (core_) {
                    core_->send(QJsonObject{
                        {QStringLiteral("op"), QStringLiteral("ui.overlay_state")},
                        {QStringLiteral("tool"), tool},
                        {QStringLiteral("state"), ToolsPlugin::instance().overlayState(tool)},
                    });
                }
            }
            return;
        }
        if (op == QStringLiteral("config.value")) {
            const QJsonObject values = packet.value(QStringLiteral("values")).toObject();
            if (values.contains(QStringLiteral("minimize_to_tray"))) {
                g_minimizeToTray = values.value(QStringLiteral("minimize_to_tray")).toBool(true);
            }
        } else if (op == QStringLiteral("config.ok")) {
            if (packet.value(QStringLiteral("key")).toString() == QStringLiteral("minimize_to_tray")) {
                g_minimizeToTray = packet.value(QStringLiteral("value")).toBool(true);
            }
        }
        for (auto* page : pages_) {
            if (page) page->onCorePacket(packet);
        }
        if (settingsPage_) settingsPage_->onCorePacket(packet);
    }

    void onCoreStatus(bool connected) {
        for (auto* page : pages_) {
            if (page) page->onStatusChange(connected);
        }
        if (settingsPage_) settingsPage_->onStatusChange(connected);
    }

    void requestInitialState() {
        if (!core_) return;
        core_->configGet();
        core_->uiCommand(QStringLiteral("login.query"));
    }

protected:
    void closeEvent(QCloseEvent* event) override {
        // 托盘由 Go core 托管。收进托盘时只隐藏窗口：QApplication 必须活到进程结束，
        // 否则托盘再次显示界面就得重建 QApplication，Qt 不支持。
        if (g_minimizeToTray) {
            event->ignore();
            hide();
            if (core_) core_->uiCommand(QStringLiteral("quit.detach_ui"));
            return;
        }
        // 未开托盘：主窗退出须连锁关掉工具设置窗/悬浮窗（它们 WA_QuitOnClose=false）。
        ToolsPlugin::instance().shutdown();
        event->accept();
        if (core_) core_->requestFullShutdown();
        else qApp->quit();
        // quit 由 requestFullShutdown 在 bye/超时后触发，避免 closeEvent 嵌套等 bye。
    }

    void showEvent(QShowEvent* event) override {
        QWidget::showEvent(event);
        restoreWindowChrome(false);
    }

private:
    void restoreWindowChrome(bool activate = true) {
        showNormal();
        if (activate) {
            raise();
            activateWindow();
        }
        applyTheme();
    }
    void buildUi() {
        // 全窗按钮统一走容器代理判定悬停，含懒加载的页面与弹层。
        liveaio::util::installHoverAutoWiring(this);
        auto* rootLay = new QVBoxLayout(this);
        rootLay->setContentsMargins(0, 0, 0, 0);

        card_ = new QFrame(this);
        card_->setObjectName(QStringLiteral("WindowCard"));
        card_->setAttribute(Qt::WA_StyledBackground, true);
        rootLay->addWidget(card_);

        auto* cardLay = new QVBoxLayout(card_);
        cardLay->setContentsMargins(0, 0, 0, 0);
        cardLay->setSpacing(0);

        titleBar_ = new TitleBar(card_);
        cardLay->addWidget(titleBar_);

        auto* body = new QWidget(card_);
        auto* bodyLay = new QHBoxLayout(body);
        bodyLay->setContentsMargins(0, 0, 0, 0);
        bodyLay->setSpacing(0);

        QVector<NavItem> navItems;
        for (const PageMeta& meta : metas_) navItems.append(NavItem{meta.icon, meta.name});
        sidebar_ = new Sidebar(navItems, NavItem{QStringLiteral("⚙"), QStringLiteral("设置")}, body);
        bodyLay->addWidget(sidebar_);

        // 物理分隔线：1px 宽，不会被侧栏子控件遮挡。
        auto* divider = new QFrame(body);
        divider->setObjectName(QStringLiteral("SidebarDivider"));
        bodyLay->addWidget(divider);

        stack_ = new QStackedWidget(body);
        stack_->setObjectName(QStringLiteral("ContentArea"));
        stack_->setAttribute(Qt::WA_StyledBackground, true);
        for (int i = 0; i < metas_.size(); ++i) {
            auto* ph = new QWidget(stack_);
            ph->setObjectName(QStringLiteral("PagePlaceholder"));
            placeholders_.append(ph);
            stack_->addWidget(ph);
        }
        settingsPlaceholder_ = new QWidget(stack_);
        settingsPlaceholder_->setObjectName(QStringLiteral("PagePlaceholder"));
        stack_->addWidget(settingsPlaceholder_);
        settingsIndex_ = stack_->count() - 1;
        bodyLay->addWidget(stack_, 1);

        cardLay->addWidget(body, 1);
    }

    void connectNav() {
        const auto& navBtns = sidebar_->navButtons();
        for (int i = 0; i < navBtns.size(); ++i) {
            QObject::connect(navBtns[i], &QPushButton::clicked, this, [this, i]() { navigateMain(i); });
        }
        if (auto* settingsBtn = sidebar_->settingsButton()) {
            QObject::connect(settingsBtn, &QPushButton::clicked, this, [this]() { navigateSettings(); });
        }
    }

    void ensureMainPage(int index) {
        if (index < 0 || index >= pages_.size() || pages_[index]) return;
        QWidget* ph = placeholders_.value(index);
        if (!ph) return;
        pages_[index] = metas_[index].factory(core_, stack_);
        if (!pages_[index]) return;
        const int idx = stack_->indexOf(ph);
        stack_->removeWidget(ph);
        ph->deleteLater();
        placeholders_[index] = nullptr;
        stack_->insertWidget(idx, pages_[index]);
    }

    void ensureSettingsPage() {
        if (settingsPage_) return;
        settingsPage_ = new SettingsPage(core_, stack_);
        if (!settingsPlaceholder_) return;
        const int idx = stack_->indexOf(settingsPlaceholder_);
        stack_->removeWidget(settingsPlaceholder_);
        settingsPlaceholder_->deleteLater();
        settingsPlaceholder_ = nullptr;
        stack_->insertWidget(idx, settingsPage_);
        settingsIndex_ = idx;
    }

    void navigateMain(int index) {
        if (index < 0 || index >= pages_.size()) return;
        ensureMainPage(index);
        if (!pages_[index]) return;
        stack_->setCurrentIndex(stack_->indexOf(pages_[index]));
        sidebar_->setActiveMain(index);
    }

    void navigateSettings() {
        ensureSettingsPage();
        stack_->setCurrentIndex(settingsIndex_);
        sidebar_->setActiveSettings();
    }

    void applyTheme() {
        qApp->setStyleSheet(shellQss());
        sidebar_->refreshTheme();
        for (auto* page : pages_) {
            if (page) page->refreshTheme();
        }
        if (settingsPage_) settingsPage_->refreshTheme();
    }

    CoreClient* core_ = nullptr;
    QVector<PageMeta> metas_;
    QFrame* card_ = nullptr;
    TitleBar* titleBar_ = nullptr;
    Sidebar* sidebar_ = nullptr;
    QStackedWidget* stack_ = nullptr;
    QVector<BasePage*> pages_;
    QVector<QWidget*> placeholders_;
    SettingsPage* settingsPage_ = nullptr;
    QWidget* settingsPlaceholder_ = nullptr;
    int settingsIndex_ = 0;
};

}  // namespace liveaio::pages
