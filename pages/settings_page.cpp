// pages/settings_page.cpp — 设置页，对齐旧 PySide settings_page.py。
// 顶部二级导航（含上一页/下一页分页）+ 系统 / 账号 / 工具设置三块面板。

namespace liveaio::pages {

using liveaio::util::deferNextTick;

// 旧 BaseSetting.build_section
struct SettingSection {
    QFrame* card = nullptr;
    QVBoxLayout* body = nullptr;
};

static SettingSection settingSection(const QString& title, QWidget* parent) {
    SettingSection out;
    out.card = new QFrame(parent);
    out.card->setObjectName(QStringLiteral("SettingCard"));
    out.body = new QVBoxLayout(out.card);
    out.body->setContentsMargins(20, 16, 20, 16);
    out.body->setSpacing(12);
    auto* lbl = new QLabel(title, out.card);
    lbl->setObjectName(QStringLiteral("SettingCardTitle"));
    out.body->addWidget(lbl);
    return out;
}

class SettingNavBtn final : public QPushButton {
public:
    explicit SettingNavBtn(const QString& label, QWidget* parent = nullptr)
        : QPushButton(label, parent) {
        setObjectName(QStringLiteral("SettingNavBtn"));
        setFixedHeight(46);
        setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
        setCursor(Qt::PointingHandCursor);
        liveaio::util::suppressButtonFocus(this);
    }

    void setActive(bool on) {
        setProperty("active", on);
        style()->unpolish(this);
        style()->polish(this);
    }
};

class SettingPanel : public QWidget {
public:
    explicit SettingPanel(QWidget* parent = nullptr) : QWidget(parent) {}
    virtual QString panelName() const = 0;
    virtual void onCorePacket(const QJsonObject&) {}
    virtual void refreshTheme() {}
};

// ── 系统 ──────────────────────────────────────
class SystemSettingsPanel final : public SettingPanel {
public:
    explicit SystemSettingsPanel(CoreClient* core, QWidget* parent = nullptr)
        : SettingPanel(parent), core_(core) {
        auto* lay = new QVBoxLayout(this);
        lay->setContentsMargins(28, 24, 28, 24);
        lay->setSpacing(16);

        auto* title = new QLabel(QStringLiteral("系统"), this);
        title->setObjectName(QStringLiteral("SettingPageTitle"));
        lay->addWidget(title);

        SettingSection themeCard = settingSection(QStringLiteral("颜色主题"), this);
        auto* row = new QHBoxLayout;
        row->setSpacing(12);
        auto* lbl = new QLabel(QStringLiteral("系统颜色主题"), themeCard.card);
        lbl->setStyleSheet(QStringLiteral("background: transparent; font-size: 14px;"));
        row->addWidget(lbl);
        row->addStretch();
        combo_ = new ThemedComboBox(themeCard.card);
        combo_->addItems(liveaio::util::themeNames());
        combo_->setCurrentText(liveaio::util::currentThemeName());
        combo_->setFixedHeight(kControlH);
        combo_->setMinimumWidth(160);
        combo_->setOnChange([](const QString& name) { liveaio::util::setTheme(name); });
        row->addWidget(combo_);
        themeCard.body->addLayout(row);
        lay->addWidget(themeCard.card);

        SettingSection behaviorCard = settingSection(QStringLiteral("行为"), this);
        auto* row2 = new QHBoxLayout;
        row2->setSpacing(12);
        auto* lbl2 = new QLabel(QStringLiteral("关闭后缩小到托盘"), behaviorCard.card);
        lbl2->setStyleSheet(QStringLiteral("background: transparent; font-size: 14px;"));
        row2->addWidget(lbl2);
        row2->addStretch();
        trayToggle_ = new ThemedToggle(QStringLiteral("minimize_to_tray"), false, behaviorCard.card);
        g_minimizeToTray = trayToggle_->value();
        trayToggle_->setOnToggled([](bool on) { g_minimizeToTray = on; });
        row2->addWidget(trayToggle_);
        behaviorCard.body->addLayout(row2);
        lay->addWidget(behaviorCard.card);

        lay->addStretch();
    }

    QString panelName() const override { return QStringLiteral("系统"); }

    void onCorePacket(const QJsonObject& packet) override {
        const QString op = packet.value(QStringLiteral("op")).toString();
        if (op == QStringLiteral("config.value")) {
            const QJsonObject values = packet.value(QStringLiteral("values")).toObject();
            if (values.contains(QStringLiteral("theme"))) {
                applyThemeFromCore(values.value(QStringLiteral("theme")).toString());
            }
            if (values.contains(QStringLiteral("minimize_to_tray"))) {
                applyTrayFromCore(values.value(QStringLiteral("minimize_to_tray")).toBool());
            }
        } else if (op == QStringLiteral("config.ok")) {
            const QString key = packet.value(QStringLiteral("key")).toString();
            if (key == QStringLiteral("theme")) {
                applyThemeFromCore(packet.value(QStringLiteral("value")).toString());
            } else if (key == QStringLiteral("minimize_to_tray")) {
                applyTrayFromCore(packet.value(QStringLiteral("value")).toBool());
            }
        }
    }

    void refreshTheme() override {
        if (combo_) {
            combo_->refreshTheme();
            const QString name = liveaio::util::currentThemeName();
            if (combo_->currentText() != name) combo_->setCurrentText(name);
        }
    }

private:
    void applyThemeFromCore(const QString& raw) {
        const QString name = liveaio::util::normalizeThemeName(raw);
        if (name == liveaio::util::currentThemeName()) return;
        liveaio::util::applyThemeName(name);
        if (combo_) combo_->setCurrentText(name);
    }

    void applyTrayFromCore(bool enabled) {
        g_minimizeToTray = enabled;
        if (trayToggle_) trayToggle_->setValue(enabled, false);
    }

    CoreClient* core_ = nullptr;
    ThemedComboBox* combo_ = nullptr;
    ThemedToggle* trayToggle_ = nullptr;
};

// ── 账号 ──────────────────────────────────────
class AccountSettingsPanel final : public SettingPanel {
public:
    explicit AccountSettingsPanel(CoreClient* core, QWidget* parent = nullptr)
        : SettingPanel(parent), core_(core) {
        auto* lay = new QVBoxLayout(this);
        lay->setContentsMargins(28, 24, 28, 24);
        lay->setSpacing(16);

        auto* title = new QLabel(QStringLiteral("账号"), this);
        title->setObjectName(QStringLiteral("SettingPageTitle"));
        lay->addWidget(title);

        SettingSection card = settingSection(QStringLiteral("登录状态"), this);
        status_ = new QLabel(QStringLiteral("检测中..."), card.card);
        status_->setObjectName(QStringLiteral("PageSubtitle"));
        card.body->addWidget(status_);

        btn_ = new QPushButton(QStringLiteral("重新登录"), card.card);
        btn_->setFixedHeight(kControlH);
        btn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(btn_, &QPushButton::clicked, this, [this]() { startLogin(); });
        card.body->addWidget(btn_);
        lay->addWidget(card.card);
        lay->addStretch();

        refreshTheme();
    }

    QString panelName() const override { return QStringLiteral("账号"); }

    void onCorePacket(const QJsonObject& packet) override {
        if (packet.value(QStringLiteral("op")).toString() != QStringLiteral("login.state")) return;
        status_->setText(packet.value(QStringLiteral("text")).toString());
        btn_->setText(QStringLiteral("重新登录"));
        enabled_ = packet.value(QStringLiteral("can_login")).toBool(true);
        applyBtnStyle();
    }

    void queryLogin() {
        if (core_) core_->uiCommand(QStringLiteral("login.query"));
    }

    void refreshTheme() override { applyBtnStyle(); }

private:
    void applyBtnStyle() {
        btn_->setEnabled(enabled_);
        btn_->setStyleSheet(enabled_ ? qssSuccess(34) : qssDisabled(34));
    }

    void startLogin() {
        btn_->setText(QStringLiteral("登录中..."));
        enabled_ = false;
        applyBtnStyle();
        if (core_) core_->uiCommand(QStringLiteral("login.start"));
    }

    CoreClient* core_ = nullptr;
    QLabel* status_ = nullptr;
    QPushButton* btn_ = nullptr;
    bool enabled_ = true;
};

// ── 工具设置 ──────────────────────────────────
class ToolsSettingsPanel final : public SettingPanel {
public:
    explicit ToolsSettingsPanel(QWidget* parent = nullptr) : SettingPanel(parent) {
        auto* lay = new QVBoxLayout(this);
        lay->setContentsMargins(28, 24, 28, 24);
        lay->setSpacing(16);
        auto* title = new QLabel(QStringLiteral("工具设置"), this);
        title->setObjectName(QStringLiteral("SettingPageTitle"));
        lay->addWidget(title);

        SettingSection card = settingSection(QStringLiteral("备忘录"), this);
        card.body->addWidget(new QLabel(QStringLiteral("详细设置请在工具内调整"), card.card));
        lay->addWidget(card.card);
        lay->addStretch();
    }

    QString panelName() const override { return QStringLiteral("工具设置"); }
};

// ── SettingsPage ──────────────────────────────
class SettingsPage final : public BasePage {
public:
    static constexpr int kPageSize = 5;

    explicit SettingsPage(CoreClient* core, QWidget* parent = nullptr)
        : BasePage(parent), core_(core) {
        auto* lay = new QVBoxLayout(this);
        lay->setContentsMargins(0, 0, 0, 0);
        lay->setSpacing(0);

        auto* topbar = new QWidget(this);
        topbar->setObjectName(QStringLiteral("SettingsSidebar"));
        topbar->setFixedHeight(46);
        auto* tbLay = new QHBoxLayout(topbar);
        tbLay->setContentsMargins(8, 0, 8, 0);
        tbLay->setSpacing(0);

        prevBtn_ = new QPushButton(QStringLiteral("← 上一页"), topbar);
        prevBtn_->setObjectName(QStringLiteral("SettingNavBtn"));
        prevBtn_->setFixedHeight(46);
        prevBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(prevBtn_, &QPushButton::clicked, this, [this]() {
            if (curPage_ > 0) {
                --curPage_;
                updatePage();
            }
        });
        tbLay->addWidget(prevBtn_);

        tabContainer_ = new QWidget(topbar);
        tabLayout_ = new QHBoxLayout(tabContainer_);
        tabLayout_->setContentsMargins(0, 0, 0, 0);
        tabLayout_->setSpacing(0);
        tbLay->addWidget(tabContainer_);
        tbLay->addStretch();

        nextBtn_ = new QPushButton(QStringLiteral("下一页 →"), topbar);
        nextBtn_->setObjectName(QStringLiteral("SettingNavBtn"));
        nextBtn_->setFixedHeight(46);
        nextBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(nextBtn_, &QPushButton::clicked, this, [this]() {
            if (curPage_ < totalPages() - 1) {
                ++curPage_;
                updatePage();
            }
        });
        tbLay->addWidget(nextBtn_);
        lay->addWidget(topbar);

        auto* scroll = new QScrollArea(this);
        scroll->setWidgetResizable(true);
        scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        scroll->setObjectName(QStringLiteral("SettingContent"));

        stack_ = new QStackedWidget(scroll);
        stack_->setObjectName(QStringLiteral("SettingContent"));

        static const QStringList kPanelNames = {
            QStringLiteral("系统"), QStringLiteral("账号"), QStringLiteral("工具设置"),
        };
        panels_.resize(kPanelNames.size());
        panelFactories_ = {
            [this]() -> SettingPanel* { return new SystemSettingsPanel(core_, stack_); },
            [this]() -> SettingPanel* { return new AccountSettingsPanel(core_, stack_); },
            [this]() -> SettingPanel* { return new ToolsSettingsPanel(stack_); },
        };
        for (int i = 0; i < kPanelNames.size(); ++i) {
            auto* btn = new SettingNavBtn(kPanelNames.at(i), tabContainer_);
            QObject::connect(btn, &QPushButton::clicked, this, [this, i]() { navigate(i); });
            navBtns_.append(btn);
            auto* ph = new QWidget(stack_);
            ph->setObjectName(QStringLiteral("SettingPlaceholder"));
            panelPlaceholders_.append(ph);
            stack_->addWidget(ph);
        }

        scroll->setWidget(stack_);
        lay->addWidget(scroll);

        updatePage();
        navigate(0);
    }

    void onCorePacket(const QJsonObject& packet) override {
        for (SettingPanel* panel : panels_) {
            if (panel) panel->onCorePacket(packet);
        }
    }

    void refreshTheme() override {
        for (SettingPanel* panel : panels_) {
            if (panel) panel->refreshTheme();
        }
        updatePagerStyle();
    }

private:
    void ensurePanel(int index) {
        if (index < 0 || index >= panels_.size() || panels_[index]) return;
        if (index >= panelFactories_.size()) return;
        panels_[index] = panelFactories_[index]();
        QWidget* ph = panelPlaceholders_.value(index);
        if (!ph) return;
        const int idx = stack_->indexOf(ph);
        stack_->removeWidget(ph);
        ph->deleteLater();
        panelPlaceholders_[index] = nullptr;
        stack_->insertWidget(idx, panels_[index]);
    }
    int totalPages() const {
        return qMax(1, (navBtns_.size() + kPageSize - 1) / kPageSize);
    }

    void updatePage() {
        while (QLayoutItem* item = tabLayout_->takeAt(0)) {
            if (QWidget* w = item->widget()) w->setParent(tabContainer_);
            delete item;
        }
        for (auto* btn : navBtns_) btn->hide();
        const int start = curPage_ * kPageSize;
        for (int i = start; i < qMin(start + kPageSize, navBtns_.size()); ++i) {
            tabLayout_->addWidget(navBtns_[i]);
            navBtns_[i]->show();
        }
        prevBtn_->setEnabled(curPage_ > 0);
        nextBtn_->setEnabled(curPage_ < totalPages() - 1);
        updatePagerStyle();
    }

    void updatePagerStyle() {
        const auto& C = theme();
        const auto styleFor = [&C](bool active) {
            return QStringLiteral(
                "QPushButton { background: transparent; border: none;"
                " border-bottom: 2px solid transparent; padding: 0 10px; font-size: 13px;"
                " color: %1; }"
                "QPushButton:hover { background: %2; }"
            ).arg(active ? C.text : C.textMuted, active ? C.hover : QStringLiteral("transparent"));
        };
        prevBtn_->setStyleSheet(styleFor(prevBtn_->isEnabled()));
        nextBtn_->setStyleSheet(styleFor(nextBtn_->isEnabled()));
    }

    void navigate(int index) {
        ensurePanel(index);
        if (!panels_[index]) return;
        stack_->setCurrentIndex(stack_->indexOf(panels_[index]));
        for (int i = 0; i < navBtns_.size(); ++i) navBtns_[i]->setActive(i == index);
        if (panels_[index] && panels_[index]->panelName() == QStringLiteral("账号")) {
            if (auto* account = dynamic_cast<AccountSettingsPanel*>(panels_[index])) {
                account->queryLogin();
            }
        }
    }

    CoreClient* core_ = nullptr;
    QPushButton* prevBtn_ = nullptr;
    QPushButton* nextBtn_ = nullptr;
    QWidget* tabContainer_ = nullptr;
    QHBoxLayout* tabLayout_ = nullptr;
    QStackedWidget* stack_ = nullptr;
    QVector<SettingNavBtn*> navBtns_;
    QVector<SettingPanel*> panels_;
    QVector<QWidget*> panelPlaceholders_;
    QVector<std::function<SettingPanel*()>> panelFactories_;
    int curPage_ = 0;
};

}  // namespace liveaio::pages
