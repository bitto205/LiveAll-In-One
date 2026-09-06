// pages/home_page.cpp — 首页，对齐旧 PySide home_page.py。
// 第一屏为 2×2 线路选择卡片；线路 1/2、3、4 各有独立详情页。

namespace liveaio::pages {

using liveaio::util::deferNextTick;

class HomePage;

enum class BtnState { Idle, Connecting, Connected, Error };

struct RouteMeta {
    QString title;
    QString badge;
    QString desc;       // 详情页完整说明
    QString pickerDesc; // 选择卡片：固定短文案，单行不换行，避免侧栏伸缩时撑高
};

static const QMap<QString, RouteMeta>& routeMetaTable() {
    static const QMap<QString, RouteMeta> table = {
        {QStringLiteral("1"), {QStringLiteral("线路一"), QStringLiteral("JS Hook"),
             QStringLiteral("该线路建议登录抖音（可选）。在直播间连接期间请勿用登录账号进入任何直播间"),
             QStringLiteral("建议登录抖音（可选）")}},
        {QStringLiteral("2"), {QStringLiteral("线路二"), QStringLiteral("WSS"),
             QStringLiteral("该线路建议登录抖音（可选）。在直播间连接期间请勿用登录账号进入任何直播间"),
             QStringLiteral("建议登录抖音（可选）")}},
        {QStringLiteral("3"), {QStringLiteral("线路三"), QStringLiteral("WinDivert"),
             QStringLiteral("该线路需要监听直播伴侣，无需登录抖音，但不能同时运行任何代理软件。"
                            "在连接直播间以后，请勿关闭直播伴侣或者关播，否则需要重新开播才能连接"),
             QStringLiteral("监听直播伴侣，勿同时开代理")}},
        {QStringLiteral("4"), {QStringLiteral("线路四"), QStringLiteral("Proxy Shell"),
             QStringLiteral("该线路需要监听直播伴侣，无需登录抖音。但是需要patch直播伴侣"),
             QStringLiteral("监听直播伴侣，需 patch")}},
    };
    return table;
}

static bool route3Enabled() {
    return configValue(QStringLiteral("route3_enabled"), true).toBool();
}

static QStringList pickerRoutes() {
    QStringList routes{QStringLiteral("1"), QStringLiteral("2")};
    if (route3Enabled()) routes << QStringLiteral("3");
    routes << QStringLiteral("4");
    return routes;
}

// 旧 listener3/4 get_page_status() 的字段，现在由 Go 的 route.env 下发。
struct RouteEnv {
    bool valid = false;
    bool companionInRegistry = false;
    bool companionInstalled = false;
    bool manualPathInvalid = false;
    bool indexJsFound = false;
    bool isPatched = false;
    bool indexPatched = false;
    bool indexModified = false;
    bool exeIdentical = false;
    bool patchNeeded = false;
    bool systemProxy = false;

    static RouteEnv fromPacket(const QJsonObject& p) {
        RouteEnv e;
        e.valid = true;
        e.companionInRegistry = p.value(QStringLiteral("companion_in_registry")).toBool();
        e.companionInstalled = p.value(QStringLiteral("companion_installed")).toBool();
        e.manualPathInvalid = p.value(QStringLiteral("manual_path_invalid")).toBool();
        e.indexJsFound = p.value(QStringLiteral("index_js_found")).toBool();
        e.isPatched = p.value(QStringLiteral("is_patched")).toBool();
        e.indexPatched = p.value(QStringLiteral("index_patched")).toBool();
        e.indexModified = p.value(QStringLiteral("index_modified")).toBool();
        e.exeIdentical = p.value(QStringLiteral("exe_identical")).toBool();
        e.patchNeeded = p.value(QStringLiteral("patch_needed")).toBool();
        e.systemProxy = p.value(QStringLiteral("system_proxy")).toBool();
        return e;
    }
};

// 旧 _refresh_companion_path_btn：注册表里能找到伴侣时隐藏按钮。
static void refreshCompanionPathBtn(QPushButton* btn, const RouteEnv& env) {
    if (env.companionInRegistry) {
        btn->hide();
        return;
    }
    btn->show();
    btn->setText(env.companionInstalled ? QStringLiteral("更换路径") : QStringLiteral("指定路径"));
    btn->setStyleSheet(qssOutlined(kControlH));
}

// ─────────────────────────────────────────────
// 线路选择页（旧 _RoutePickerPage）
// ─────────────────────────────────────────────
class RoutePickerPage final : public QWidget {
public:
    explicit RoutePickerPage(std::function<void(const QString&)> onPick, QWidget* parent = nullptr)
        : QWidget(parent), onPick_(std::move(onPick)) {
        auto* outer = new QVBoxLayout(this);
        outer->setContentsMargins(32, 32, 32, 32);
        outer->setSpacing(20);

        auto* title = new QLabel(QStringLiteral("选择线路"), this);
        title->setObjectName(QStringLiteral("PageTitle"));
        outer->addWidget(title);

        hint_ = new QLabel(QStringLiteral("请先选择监听方案，再进入对应配置页完成连接。"), this);
        hint_->setWordWrap(true);
        outer->addWidget(hint_);
        outer->addSpacing(8);

        auto* gridHost = new QWidget(this);
        auto* grid = new QGridLayout(gridHost);
        grid->setSpacing(16);
        grid->setContentsMargins(0, 0, 0, 0);
        // 两列等宽：否则长说明文字的 sizeHint 会把一列撑满整页。
        grid->setColumnStretch(0, 1);
        grid->setColumnStretch(1, 1);

        const QStringList routes = pickerRoutes();
        for (int i = 0; i < routes.size(); ++i) {
            const QString& route = routes.at(i);
            auto* card = makeCard(route, gridHost);
            cards_.insert(route, card);
            grid->addWidget(card, i / 2, i % 2);
        }
        outer->addWidget(gridHost);
        outer->addStretch();
        refreshTheme();
    }

    void refreshTheme() {
        const auto& C = theme();
        hint_->setStyleSheet(qssMutedLabel(13));
        const QString nameStyle = QStringLiteral(
            "font-size: 16px; font-weight: 700; color: %1; background: transparent;").arg(C.text);
        const QString badgeStyle = QStringLiteral(
            "background: transparent; color: %1; font-size: 11px; font-weight: 600;").arg(C.textMuted);
        const QString descStyle = QStringLiteral(
            "font-size: 12px; color: %1; background: transparent;").arg(C.textMuted);
        for (auto it = labels_.constBegin(); it != labels_.constEnd(); ++it) {
            it.value().name->setStyleSheet(nameStyle);
            it.value().badge->setStyleSheet(badgeStyle);
            it.value().desc->setStyleSheet(descStyle);
        }
        const QString last = configValue(QStringLiteral("route"), QStringLiteral("1")).toString();
        for (auto it = cards_.constBegin(); it != cards_.constEnd(); ++it) {
            const bool selected = it.key() == last;
            const QString border = selected ? C.activeLine : C.border;
            it.value()->setStyleSheet(QStringLiteral(
                "QFrame#Card { background: %1; border: 2px solid %2; border-radius: 10px; }"
                "QFrame#Card:hover { background: %3; border-color: %4; }"
            ).arg(C.card, border, C.hover, C.activeLine));
        }
    }

private:
    struct CardLabels {
        QLabel* name = nullptr;
        QLabel* badge = nullptr;
        QLabel* desc = nullptr;
    };

    class RouteCard final : public QFrame {
    public:
        RouteCard(const QString& route, std::function<void(const QString&)> onPick, QWidget* parent)
            : QFrame(parent), route_(route), onPick_(std::move(onPick)) {
            setObjectName(QStringLiteral("Card"));
            setCursor(Qt::PointingHandCursor);
        }

    protected:
        void mousePressEvent(QMouseEvent* event) override {
            if (event->button() == Qt::LeftButton && onPick_) onPick_(route_);
            QFrame::mousePressEvent(event);
        }

    private:
        QString route_;
        std::function<void(const QString&)> onPick_;
    };

    QFrame* makeCard(const QString& route, QWidget* parent) {
        const RouteMeta meta = routeMetaTable().value(route);
        auto* card = new RouteCard(route, onPick_, parent);
        auto* lay = new QVBoxLayout(card);
        lay->setContentsMargins(20, 18, 20, 18);
        lay->setSpacing(10);

        auto* top = new QHBoxLayout;
        auto* name = new QLabel(meta.title, card);
        auto* badge = new QLabel(meta.badge, card);
        badge->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
        top->addWidget(name);
        top->addStretch();
        top->addWidget(badge);
        lay->addLayout(top);

        auto* desc = new QLabel(meta.pickerDesc, card);
        desc->setWordWrap(false);
        desc->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Fixed);
        desc->setFixedHeight(QFontMetrics(desc->font()).height());
        lay->addWidget(desc);

        labels_.insert(route, CardLabels{name, badge, desc});
        return card;
    }

    std::function<void(const QString&)> onPick_;
    QLabel* hint_ = nullptr;
    QMap<QString, QFrame*> cards_;
    QMap<QString, CardLabels> labels_;
};

// ─────────────────────────────────────────────
// 线路详情页公共部分
// ─────────────────────────────────────────────
class RouteDetailPage : public QWidget {
public:
    RouteDetailPage(const QString& route, HomePage* home, QWidget* parent)
        : QWidget(parent), route_(route), home_(home) {}

    const QString& route() const { return route_; }
    BtnState btnState() const { return state_; }

    virtual void setConnState(BtnState state) = 0;
    virtual void refreshTheme() = 0;

    void markPreempted() {
        preempted_ = true;
        wasConnecting_ = false;
        setConnState(BtnState::Idle);
    }

    void resetIdle() {
        wasConnecting_ = false;
        preempted_ = false;
        setConnState(BtnState::Idle);
    }

protected:
    QPushButton* makeBackButton(QWidget* parent);

    QString route_;
    HomePage* home_ = nullptr;
    BtnState state_ = BtnState::Idle;
    bool wasConnecting_ = false;
    bool preempted_ = false;
    QPushButton* backBtn_ = nullptr;
};

// ─────────────────────────────────────────────
// 线路一 / 二（网页登录流程，旧 _WebRoutePage）
// ─────────────────────────────────────────────
class WebRoutePage final : public RouteDetailPage {
public:
    WebRoutePage(const QString& route, HomePage* home, CoreClient* core, QWidget* parent = nullptr)
        : RouteDetailPage(route, home, parent), core_(core) {
        const RouteMeta meta = routeMetaTable().value(route);

        auto* inner = new QWidget;
        auto* lay = new QVBoxLayout(inner);
        lay->setContentsMargins(32, 24, 32, 32);
        lay->setSpacing(16);

        backBtn_ = makeBackButton(inner);

        auto* title = new QLabel(meta.title, inner);
        title->setObjectName(QStringLiteral("PageTitle"));
        lay->addWidget(title);

        sub_ = new QLabel(meta.desc, inner);
        sub_->setWordWrap(true);
        lay->addWidget(sub_);
        lay->addSpacing(4);

        step1_ = stepCard(1, QStringLiteral("登录"));
        loginDesc_ = new QLabel(
            QStringLiteral("请用小号/非直播号登录，连接过程中请勿用该账号进入任何直播间。\n"
                           "无登录无法拿到礼物数据，同时有可能会被限流"), step1_.card);
        loginDesc_->setWordWrap(true);
        step1_.body->addWidget(loginDesc_);
        loginStatus_ = new QLabel(QStringLiteral("进入线路后检测登录状态"), step1_.card);
        loginBtn_ = new QPushButton(QStringLiteral("登录"), step1_.card);
        loginBtn_->setFixedHeight(kControlH);
        loginBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(loginBtn_, &QPushButton::clicked, this, [this]() { doLogin(); });
        auto* row1 = new QHBoxLayout;
        row1->addWidget(loginStatus_);
        row1->addStretch();
        row1->addWidget(loginBtn_);
        step1_.body->addLayout(row1);
        lay->addWidget(step1_.card);

        step2_ = stepCard(2, QStringLiteral("直播间 ID"));
        roomInput_ = new QLineEdit(step2_.card);
        roomInput_->setPlaceholderText(
            QStringLiteral("请输入直播间 ID（抖音号，可在抖音「我」界面查看）"));
        roomInput_->setFixedHeight(kControlH);
        roomInput_->setText(configValue(QStringLiteral("live_id"), QString()).toString());
        saveBtn_ = new QPushButton(QStringLiteral("保存"), step2_.card);
        saveBtn_->setFixedHeight(kControlH);
        saveBtn_->setMinimumWidth(80);
        saveBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(saveBtn_, &QPushButton::clicked, this, [this]() { saveLiveId(); });
        auto* row2 = new QHBoxLayout;
        row2->setSpacing(8);
        row2->setAlignment(Qt::AlignVCenter);
        row2->addWidget(roomInput_, 1);
        row2->addWidget(saveBtn_, 0);
        step2_.body->addLayout(row2);
        lay->addWidget(step2_.card);

        step3_ = stepCard(3, QStringLiteral("连接直播间"));
        connBtn_ = new QPushButton(QStringLiteral("连接直播间"), step3_.card);
        connBtn_->setFixedHeight(44);
        connBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(connBtn_, &QPushButton::clicked, this, [this]() { onConnClicked(); });
        connLabel_ = new QLabel(QString(), step3_.card);
        connLabel_->setAlignment(Qt::AlignCenter);
        connLabel_->setVisible(false);
        step3_.body->addWidget(connBtn_);
        step3_.body->addWidget(connLabel_);
        lay->addWidget(step3_.card);

        lay->addSpacing(8);
        lay->addWidget(backBtn_, 0, Qt::AlignLeft);
        lay->addStretch();

        auto* root = new QVBoxLayout(this);
        root->setContentsMargins(0, 0, 0, 0);
        root->addWidget(scrollPage(inner));

        refreshTheme();
    }

    void applyLoginState(const QString& text, bool canLogin) {
        loginStatus_->setText(text);
        loginBtn_->setText(canLogin ? QStringLiteral("登录") : QStringLiteral("重新登录"));
        // Even a locally valid cookie may have been revoked remotely; never lock
        // the user out of explicitly refreshing the login state.
        loginEnabled_ = true;
        applyLoginBtnStyle();
    }

    void setLoginChecking() {
        loginStatus_->setText(QStringLiteral("正在预检查 state.json…"));
        loginBtn_->setText(QStringLiteral("检查中…"));
        loginEnabled_ = false;
        applyLoginBtnStyle();
    }

    void setConnState(BtnState state) override {
        state_ = state;
        switch (state) {
        case BtnState::Idle:
            connBtn_->setText(QStringLiteral("连接直播间"));
            connBtn_->setEnabled(true);
            connBtn_->setStyleSheet(qssOutlined(44));
            connLabel_->setVisible(false);
            break;
        case BtnState::Connecting:
            connBtn_->setText(waitingForSwitch() ? QStringLiteral("等待其他线路退出…")
                                                 : QStringLiteral("连接中..."));
            connBtn_->setEnabled(false);
            connBtn_->setStyleSheet(qssDisabled(44));
            connLabel_->setVisible(false);
            break;
        case BtnState::Connected:
            connBtn_->setText(QStringLiteral("断开连接"));
            connBtn_->setEnabled(true);
            connBtn_->setStyleSheet(qssDanger(44));
            connLabel_->setText(QStringLiteral("✅  已连接"));
            connLabel_->setStyleSheet(qssAccentLabel());
            connLabel_->setVisible(true);
            break;
        case BtnState::Error:
            connBtn_->setText(QStringLiteral("连接直播间"));
            connBtn_->setEnabled(true);
            connBtn_->setStyleSheet(qssOutlined(44));
            break;
        }
    }

    void onStatusChange(bool connected);

    void refreshTheme() override {
        backBtn_->setStyleSheet(qssBack());
        sub_->setStyleSheet(qssMutedLabel(13));
        loginDesc_->setStyleSheet(qssMutedLabel(12));
        loginStatus_->setStyleSheet(qssMutedLabel(13));
        roomInput_->setStyleSheet(qssLineEdit());
        saveBtn_->setStyleSheet(qssOutlinedBesideEdit(36));
        step1_.refreshTheme();
        step2_.refreshTheme();
        step3_.refreshTheme();
        applyLoginBtnStyle();
        setConnState(state_);
    }

private:
    bool waitingForSwitch() const;
    void toast(const QString& msg, bool error = false) const;

    void applyLoginBtnStyle() {
        loginBtn_->setEnabled(loginEnabled_);
        loginBtn_->setStyleSheet(loginEnabled_ ? qssOutlined(kControlH) : qssDisabled(kControlH));
    }

    void doLogin() {
        loginBtn_->setText(QStringLiteral("登录中..."));
        loginEnabled_ = false;
        applyLoginBtnStyle();
        if (core_) core_->uiCommand(QStringLiteral("login.start"));
    }

    void saveLiveId() {
        const QString text = roomInput_->text().trimmed();
        if (text.isEmpty()) {
            toast(QStringLiteral("请先输入直播间 ID"), true);
            return;
        }
        liveaio::util::configSet(QStringLiteral("live_id"), text);
        saveBtn_->setText(QStringLiteral("已保存"));
        toast(QStringLiteral("已保存"));
        QTimer::singleShot(2000, saveBtn_, [this]() { saveBtn_->setText(QStringLiteral("保存")); });
    }

    void onConnClicked();

    CoreClient* core_ = nullptr;
    QLabel* sub_ = nullptr;
    StepCard step1_;
    StepCard step2_;
    StepCard step3_;
    QLabel* loginDesc_ = nullptr;
    QLabel* loginStatus_ = nullptr;
    QPushButton* loginBtn_ = nullptr;
    bool loginEnabled_ = true;
    QLineEdit* roomInput_ = nullptr;
    QPushButton* saveBtn_ = nullptr;
    QPushButton* connBtn_ = nullptr;
    QLabel* connLabel_ = nullptr;
};

// ─────────────────────────────────────────────
// 线路三（Unpatch + 代理检测，旧 _Route3Page）
// ─────────────────────────────────────────────
class Route3Page final : public RouteDetailPage {
public:
    Route3Page(HomePage* home, CoreClient* core, QWidget* parent = nullptr)
        : RouteDetailPage(QStringLiteral("3"), home, parent), core_(core) {
        const RouteMeta meta = routeMetaTable().value(QStringLiteral("3"));

        auto* inner = new QWidget;
        auto* lay = new QVBoxLayout(inner);
        lay->setContentsMargins(32, 24, 32, 32);
        lay->setSpacing(16);

        backBtn_ = makeBackButton(inner);

        auto* title = new QLabel(meta.title, inner);
        title->setObjectName(QStringLiteral("PageTitle"));
        lay->addWidget(title);

        sub_ = new QLabel(meta.desc, inner);
        sub_->setWordWrap(true);
        lay->addWidget(sub_);

        auto* card = new QFrame(inner);
        card->setObjectName(QStringLiteral("Card"));
        auto* cardLay = new QVBoxLayout(card);
        cardLay->setContentsMargins(20, 16, 20, 20);
        cardLay->setSpacing(12);

        hint_ = new QLabel(QStringLiteral("处于 patch 或者代理状态中时线路 3 不可用，"
                                          "在连接直播间后请勿在中途打开任何代理软件"), card);
        hint_->setWordWrap(true);
        cardLay->addWidget(hint_);

        auto* btnRow = new QHBoxLayout;
        btnRow->setSpacing(12);
        actionBtn_ = new QPushButton(QStringLiteral("Unpatch"), card);
        actionBtn_->setFixedHeight(kControlH);
        actionBtn_->setMinimumWidth(120);
        actionBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(actionBtn_, &QPushButton::clicked, this, [this]() { doUnpatch(); });
        btnRow->addWidget(actionBtn_);

        pathBtn_ = new QPushButton(QStringLiteral("指定路径"), card);
        pathBtn_->setFixedHeight(kControlH);
        pathBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(pathBtn_, &QPushButton::clicked, this, [this]() { pickCompanionDir(); });
        btnRow->addWidget(pathBtn_);

        warn_ = new QLabel(QStringLiteral("当前系统代理占用，请关闭代理后重新进入该页面"), card);
        warn_->setWordWrap(true);
        warn_->hide();
        btnRow->addWidget(warn_, 1);
        btnRow->addStretch();
        cardLay->addLayout(btnRow);

        statusLbl_ = new QLabel(QString(), card);
        statusLbl_->setWordWrap(true);
        cardLay->addWidget(statusLbl_);
        lay->addWidget(card);

        auto* connCard = new QFrame(inner);
        connCard->setObjectName(QStringLiteral("Card"));
        auto* connLay = new QVBoxLayout(connCard);
        connLay->setContentsMargins(20, 16, 20, 20);
        connLay->setSpacing(12);

        connHint_ = new QLabel(QStringLiteral("请在直播开始之前点击连接直播间"), connCard);
        connHint_->setWordWrap(true);
        connLay->addWidget(connHint_);

        connBtn_ = new QPushButton(QStringLiteral("连接直播间"), connCard);
        connBtn_->setFixedHeight(44);
        connBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(connBtn_, &QPushButton::clicked, this, [this]() { onConnClicked(); });
        connLay->addWidget(connBtn_);

        connLabel_ = new QLabel(QString(), connCard);
        connLabel_->setAlignment(Qt::AlignCenter);
        connLabel_->setVisible(false);
        connLay->addWidget(connLabel_);

        lay->addWidget(connCard);
        lay->addSpacing(8);
        lay->addWidget(backBtn_, 0, Qt::AlignLeft);
        lay->addStretch();

        auto* root = new QVBoxLayout(this);
        root->setContentsMargins(0, 0, 0, 0);
        root->addWidget(scrollPage(inner));

        refreshTheme();
    }

    void applyEnv(const RouteEnv& env) {
        env_ = env;
        refreshStatus();
    }

    void setConnState(BtnState state) override {
        state_ = state;
        refreshConnBtn();
    }

    void onStatusChange(bool connected);

    void refreshTheme() override {
        backBtn_->setStyleSheet(qssBack());
        sub_->setStyleSheet(qssMutedLabel(13));
        hint_->setStyleSheet(qssMutedLabel(13));
        warn_->setStyleSheet(qssErrorLabel(13));
        statusLbl_->setStyleSheet(qssMutedLabel(12));
        connHint_->setStyleSheet(qssMutedLabel(13));
        refreshConnBtn();
        if (env_.valid) refreshStatus();
    }

private:
    bool ready() const {
        if (!env_.valid) return false;
        if (env_.manualPathInvalid) return false;
        if (!env_.companionInstalled || !env_.indexJsFound) return false;
        if (env_.systemProxy || env_.indexModified) return false;
        return true;
    }

    void refreshStatus() {
        refreshCompanionPathBtn(pathBtn_, env_);
        actionBtn_->setText(QStringLiteral("Unpatch"));

        if (env_.manualPathInvalid) {
            setActionEnabled(false);
            warn_->hide();
            statusLbl_->setText(QStringLiteral("该指定目录无效"));
            refreshConnBtn();
            return;
        }
        if (!env_.companionInstalled || !env_.indexJsFound) {
            setActionEnabled(false);
            warn_->hide();
            statusLbl_->setText(QStringLiteral("未检测到直播伴侣，请指定安装路径"));
            refreshConnBtn();
            return;
        }
        if (env_.systemProxy) {
            setActionEnabled(false);
            warn_->show();
            statusLbl_->setText(QString());
        } else if (env_.indexModified) {
            setActionEnabled(true);
            warn_->hide();
            statusLbl_->setText(QString());
        } else {
            setActionEnabled(false);
            warn_->hide();
            statusLbl_->setText(QStringLiteral("当前未 patch，无需 Unpatch"));
        }
        refreshConnBtn();
    }

    void setActionEnabled(bool on) {
        actionBtn_->setEnabled(on);
        actionBtn_->setStyleSheet(on ? qssOutlined(kControlH) : qssDisabled(kControlH));
    }

    void doUnpatch() {
        actionBtn_->setText(QStringLiteral("还原中..."));
        setActionEnabled(false);
        if (core_) core_->uiCommand(QStringLiteral("route3.unpatch"), QStringLiteral("3"));
    }

    void pickCompanionDir();
    void onConnClicked();
    bool waitingForSwitch() const;
    void toast(const QString& msg, bool error = false) const;

    void refreshConnBtn() {
        const bool isReady = ready();
        switch (state_) {
        case BtnState::Idle:
            connBtn_->setText(QStringLiteral("连接直播间"));
            connBtn_->setEnabled(isReady);
            connBtn_->setStyleSheet(isReady ? qssOutlined(44) : qssDisabled(44));
            connLabel_->setVisible(false);
            break;
        case BtnState::Connecting: {
            const bool waiting = waitingForSwitch();
            connBtn_->setText(waiting ? QStringLiteral("等待其他线路退出…")
                                      : QStringLiteral("连接中,等待开播...点击取消连接"));
            connBtn_->setEnabled(!waiting);
            connBtn_->setStyleSheet(waiting ? qssDisabled(44) : qssOutlined(44));
            connLabel_->setVisible(false);
            break;
        }
        case BtnState::Connected:
            connBtn_->setText(QStringLiteral("断开连接"));
            connBtn_->setEnabled(true);
            connBtn_->setStyleSheet(qssDanger(44));
            connLabel_->setText(QStringLiteral("✅  监听中"));
            connLabel_->setStyleSheet(qssAccentLabel());
            connLabel_->setVisible(true);
            break;
        case BtnState::Error:
            connBtn_->setText(QStringLiteral("连接直播间"));
            connBtn_->setEnabled(isReady);
            connBtn_->setStyleSheet(isReady ? qssOutlined(44) : qssDisabled(44));
            break;
        }
    }

    CoreClient* core_ = nullptr;
    RouteEnv env_;
    QLabel* sub_ = nullptr;
    QLabel* hint_ = nullptr;
    QPushButton* actionBtn_ = nullptr;
    QPushButton* pathBtn_ = nullptr;
    QLabel* warn_ = nullptr;
    QLabel* statusLbl_ = nullptr;
    QLabel* connHint_ = nullptr;
    QPushButton* connBtn_ = nullptr;
    QLabel* connLabel_ = nullptr;
};

// ─────────────────────────────────────────────
// 线路四（Patch，旧 _Route4Page）
// ─────────────────────────────────────────────
class Route4Page final : public RouteDetailPage {
public:
    Route4Page(HomePage* home, CoreClient* core, QWidget* parent = nullptr)
        : RouteDetailPage(QStringLiteral("4"), home, parent), core_(core) {
        const RouteMeta meta = routeMetaTable().value(QStringLiteral("4"));

        auto* inner = new QWidget;
        auto* lay = new QVBoxLayout(inner);
        lay->setContentsMargins(32, 24, 32, 32);
        lay->setSpacing(16);

        backBtn_ = makeBackButton(inner);

        auto* title = new QLabel(meta.title, inner);
        title->setObjectName(QStringLiteral("PageTitle"));
        lay->addWidget(title);

        sub_ = new QLabel(meta.desc, inner);
        sub_->setWordWrap(true);
        lay->addWidget(sub_);

        auto* card = new QFrame(inner);
        card->setObjectName(QStringLiteral("Card"));
        auto* cardLay = new QVBoxLayout(card);
        cardLay->setContentsMargins(20, 16, 20, 20);
        cardLay->setSpacing(12);

        hint_ = new QLabel(QStringLiteral("线路 4 需要 patch 后才能使用"), card);
        hint_->setWordWrap(true);
        cardLay->addWidget(hint_);

        auto* btnRow = new QHBoxLayout;
        btnRow->setSpacing(12);
        actionBtn_ = new QPushButton(QStringLiteral("Patch"), card);
        actionBtn_->setFixedHeight(kControlH);
        actionBtn_->setMinimumWidth(120);
        actionBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(actionBtn_, &QPushButton::clicked, this, [this]() { onActionClicked(); });
        btnRow->addWidget(actionBtn_);

        pathBtn_ = new QPushButton(QStringLiteral("指定路径"), card);
        pathBtn_->setFixedHeight(kControlH);
        pathBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(pathBtn_, &QPushButton::clicked, this, [this]() { pickCompanionDir(); });
        btnRow->addWidget(pathBtn_);
        btnRow->addStretch();
        cardLay->addLayout(btnRow);

        statusLbl_ = new QLabel(QString(), card);
        statusLbl_->setWordWrap(true);
        cardLay->addWidget(statusLbl_);
        lay->addWidget(card);

        auto* connCard = new QFrame(inner);
        connCard->setObjectName(QStringLiteral("Card"));
        auto* connLay = new QVBoxLayout(connCard);
        connLay->setContentsMargins(20, 16, 20, 20);
        connLay->setSpacing(12);

        connHint_ = new QLabel(QStringLiteral("请先启动直播伴侣并开播后再连接"), connCard);
        connHint_->setWordWrap(true);
        connLay->addWidget(connHint_);

        connBtn_ = new QPushButton(QStringLiteral("连接直播间"), connCard);
        connBtn_->setFixedHeight(44);
        connBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(connBtn_, &QPushButton::clicked, this, [this]() { onConnClicked(); });
        connLay->addWidget(connBtn_);

        connLabel_ = new QLabel(QString(), connCard);
        connLabel_->setAlignment(Qt::AlignCenter);
        connLabel_->setVisible(false);
        connLay->addWidget(connLabel_);

        lay->addWidget(connCard);
        lay->addSpacing(8);
        lay->addWidget(backBtn_, 0, Qt::AlignLeft);
        lay->addStretch();

        auto* root = new QVBoxLayout(this);
        root->setContentsMargins(0, 0, 0, 0);
        root->addWidget(scrollPage(inner));

        refreshTheme();
    }

    void applyEnv(const RouteEnv& env) {
        env_ = env;
        refreshStatus();
    }

    void setConnState(BtnState state) override {
        state_ = state;
        refreshConnBtn();
    }

    void onStatusChange(bool connected);

    void refreshTheme() override {
        backBtn_->setStyleSheet(qssBack());
        sub_->setStyleSheet(qssMutedLabel(13));
        hint_->setStyleSheet(qssMutedLabel(13));
        statusLbl_->setStyleSheet(qssMutedLabel(12));
        connHint_->setStyleSheet(qssMutedLabel(13));
        refreshConnBtn();
        if (env_.valid) refreshStatus();
    }

private:
    bool ready() const {
        if (!env_.valid) return false;
        if (env_.manualPathInvalid) return false;
        if (!env_.companionInstalled || !env_.indexJsFound) return false;
        if (env_.patchNeeded || !env_.isPatched) return false;
        return true;
    }

    void refreshStatus() {
        refreshCompanionPathBtn(pathBtn_, env_);

        if (env_.manualPathInvalid) {
            setActionEnabled(false);
            statusLbl_->setText(QStringLiteral("该指定目录无效"));
            refreshConnBtn();
            return;
        }
        if (!env_.companionInstalled || !env_.indexJsFound) {
            setActionEnabled(false);
            statusLbl_->setText(QStringLiteral("未检测到直播伴侣，请指定安装路径"));
            refreshConnBtn();
            return;
        }
        if (env_.isPatched) {
            actionBtn_->setText(QStringLiteral("Unpatch"));
            setActionEnabled(true);
            statusLbl_->setText(QStringLiteral("Patch 已完成，如果是首次Patch，请重启直播伴侣"));
        } else if (env_.patchNeeded) {
            actionBtn_->setText(QStringLiteral("Patch"));
            setActionEnabled(true);
            QStringList parts;
            if (!env_.exeIdentical) parts << QStringLiteral("exe 与发布包不一致或缺失");
            if (!env_.indexPatched) parts << QStringLiteral("index.js patch 注入与 catalog 不一致");
            statusLbl_->setText(parts.join(QStringLiteral("；")));
        } else {
            actionBtn_->setText(QStringLiteral("Patch"));
            setActionEnabled(false);
            statusLbl_->setText(QString());
        }
        refreshConnBtn();
    }

    void setActionEnabled(bool on) {
        actionBtn_->setEnabled(on);
        actionBtn_->setStyleSheet(on ? qssOutlined(kControlH) : qssDisabled(kControlH));
    }

    void onActionClicked() {
        const bool unpatch = env_.isPatched;
        actionBtn_->setText(unpatch ? QStringLiteral("还原中...") : QStringLiteral("Patch 中..."));
        setActionEnabled(false);
        if (core_) {
            core_->uiCommand(unpatch ? QStringLiteral("route4.unpatch")
                                     : QStringLiteral("route4.patch"),
                             QStringLiteral("4"));
        }
    }

    void pickCompanionDir();
    void onConnClicked();
    bool waitingForSwitch() const;
    void toast(const QString& msg, bool error = false) const;

    void refreshConnBtn() {
        const bool isReady = ready();
        switch (state_) {
        case BtnState::Idle:
            connBtn_->setText(QStringLiteral("连接直播间"));
            connBtn_->setEnabled(isReady);
            connBtn_->setStyleSheet(isReady ? qssOutlined(44) : qssDisabled(44));
            connLabel_->setVisible(false);
            break;
        case BtnState::Connecting: {
            const bool waiting = waitingForSwitch();
            connBtn_->setText(waiting ? QStringLiteral("等待其他线路退出…")
                                      : QStringLiteral("连接中...点击取消连接"));
            connBtn_->setEnabled(!waiting);
            connBtn_->setStyleSheet(waiting ? qssDisabled(44) : qssOutlined(44));
            connLabel_->setVisible(false);
            break;
        }
        case BtnState::Connected:
            connBtn_->setText(QStringLiteral("断开连接"));
            connBtn_->setEnabled(true);
            connBtn_->setStyleSheet(qssDanger(44));
            connLabel_->setText(QStringLiteral("✅  已连接"));
            connLabel_->setStyleSheet(qssAccentLabel());
            connLabel_->setVisible(true);
            break;
        case BtnState::Error:
            connBtn_->setText(QStringLiteral("连接直播间"));
            connBtn_->setEnabled(isReady);
            connBtn_->setStyleSheet(isReady ? qssOutlined(44) : qssDisabled(44));
            break;
        }
    }

    CoreClient* core_ = nullptr;
    RouteEnv env_;
    QLabel* sub_ = nullptr;
    QLabel* hint_ = nullptr;
    QPushButton* actionBtn_ = nullptr;
    QPushButton* pathBtn_ = nullptr;
    QLabel* statusLbl_ = nullptr;
    QLabel* connHint_ = nullptr;
    QPushButton* connBtn_ = nullptr;
    QLabel* connLabel_ = nullptr;
};

// ─────────────────────────────────────────────
// HomePage
// ─────────────────────────────────────────────
class HomePage final : public BasePage {
public:
    explicit HomePage(CoreClient* core, QWidget* parent = nullptr)
        : BasePage(parent), core_(core) {
        if (configValue(QStringLiteral("route"), QStringLiteral("2")).toString() == QStringLiteral("3")
            && !route3Enabled()) {
            liveaio::util::configSet(QStringLiteral("route"), QStringLiteral("2"));
        }

        toast_ = new Toast(this);

        stack_ = new QStackedWidget(this);
        picker_ = new RoutePickerPage([this](const QString& route) { enterRoute(route); }, stack_);
        stack_->addWidget(picker_);

        for (const QString& route : {QStringLiteral("1"), QStringLiteral("2")}) {
            auto* ph = new QWidget(stack_);
            ph->setObjectName(QStringLiteral("RoutePlaceholder"));
            routePlaceholders_.insert(route, ph);
            stack_->addWidget(ph);
            routeIndex_.insert(route, stack_->count() - 1);
        }
        if (route3Enabled()) {
            auto* ph = new QWidget(stack_);
            ph->setObjectName(QStringLiteral("RoutePlaceholder"));
            routePlaceholders_.insert(QStringLiteral("3"), ph);
            stack_->addWidget(ph);
            routeIndex_.insert(QStringLiteral("3"), stack_->count() - 1);
        }
        auto* ph4 = new QWidget(stack_);
        ph4->setObjectName(QStringLiteral("RoutePlaceholder"));
        routePlaceholders_.insert(QStringLiteral("4"), ph4);
        stack_->addWidget(ph4);
        routeIndex_.insert(QStringLiteral("4"), stack_->count() - 1);

        auto* root = new QVBoxLayout(this);
        root->setContentsMargins(0, 0, 0, 0);
        root->addWidget(stack_);

        refreshTheme();
        showPicker();

        const QString warmRoute =
            configValue(QStringLiteral("route"), QStringLiteral("2")).toString();
        if (routeIndex_.contains(warmRoute)) {
            liveaio::util::deferNextTick(this, [this, warmRoute]() { ensureRoutePage(warmRoute); });
        }
    }

    Toast* toast() const { return toast_; }
    CoreClient* core() const { return core_; }
    bool switchingListener() const { return switching_; }

    QString activeListenerRoute() const {
        for (auto it = webPages_.constBegin(); it != webPages_.constEnd(); ++it) {
            const BtnState s = it.value()->btnState();
            if (s == BtnState::Connecting || s == BtnState::Connected) return it.key();
        }
        if (route3_ && (route3_->btnState() == BtnState::Connecting
                        || route3_->btnState() == BtnState::Connected)) {
            return QStringLiteral("3");
        }
        if (route4_ && (route4_->btnState() == BtnState::Connecting
                        || route4_->btnState() == BtnState::Connected)) {
            return QStringLiteral("4");
        }
        return {};
    }

    void showPicker() {
        stack_->setCurrentIndex(0);
        picker_->refreshTheme();
    }

    void requestConnect(const QString& route, const QString& liveId) {
        const QString active = activeListenerRoute();
        // 四线路互斥：目标线路先显示“等待其他线路退出”，由 core 完成切换。
        switching_ = !active.isEmpty() && active != route;
        preemptOtherListeners(route);
        connectedRoute_ = route;
        if (auto* target = pageForRoute(route)) {
            if (target->btnState() == BtnState::Connecting) target->setConnState(BtnState::Connecting);
        }
        if (core_) core_->connectLive(route, liveId, false);
    }

    RouteDetailPage* pageForRoute(const QString& route) const {
        if (auto* page = webPages_.value(route)) return page;
        if (route == QStringLiteral("3")) return route3_;
        if (route == QStringLiteral("4")) return route4_;
        return nullptr;
    }

    void requestDisconnect() {
        connectedRoute_.clear();
        switching_ = false;
        if (core_) core_->disconnectLive();
    }

    void onCorePacket(const QJsonObject& packet) override {
        const QString op = packet.value(QStringLiteral("op")).toString();
        if (op == QStringLiteral("status")) {
            if (!packet.contains(QStringLiteral("connected"))) return;
            handleStatus(packet.value(QStringLiteral("connected")).toBool(),
                         packet.value(QStringLiteral("route")).toString());
        } else if (op == QStringLiteral("route.env")) {
            handleRouteEnv(packet);
        } else if (op == QStringLiteral("login.state")) {
            loginText_ = packet.value(QStringLiteral("text")).toString();
            loginCan_ = packet.value(QStringLiteral("can_login")).toBool(true);
            loginStateKnown_ = true;
            for (auto* page : webPages_) page->applyLoginState(loginText_, loginCan_);
        } else if (op == QStringLiteral("error")) {
            toast_->showMsg(packet.value(QStringLiteral("msg"))
                                .toString(QStringLiteral("错误")), true);
        }
    }

    void refreshTheme() override {
        picker_->refreshTheme();
        for (auto* page : webPages_) page->refreshTheme();
        if (route3_) route3_->refreshTheme();
        if (route4_) route4_->refreshTheme();
    }

    void preemptOtherListeners(const QString& newRoute) {
        for (auto it = webPages_.constBegin(); it != webPages_.constEnd(); ++it) {
            if (it.key() == newRoute) continue;
            const BtnState s = it.value()->btnState();
            if (s == BtnState::Connecting || s == BtnState::Connected) {
                it.value()->markPreempted();
                toast_->showMsg(QStringLiteral("listener%1停止连接直播间").arg(newRoute));
            }
        }
        auto preemptDetail = [&](RouteDetailPage* page, const QString& id) {
            if (!page || id == newRoute) return;
            const BtnState s = page->btnState();
            if (s == BtnState::Connecting || s == BtnState::Connected) {
                page->markPreempted();
                toast_->showMsg(QStringLiteral("listener%1停止连接直播间").arg(newRoute));
            }
        };
        preemptDetail(route3_, QStringLiteral("3"));
        preemptDetail(route4_, QStringLiteral("4"));
        const QString active = activeListenerRoute();
        if (!active.isEmpty() && active != newRoute) connectedRoute_.clear();
    }

protected:
    void resizeEvent(QResizeEvent* event) override {
        BasePage::resizeEvent(event);
        if (toast_) toast_->reposition();
    }

private:
    void ensureRoutePage(const QString& route) {
        if (route == QStringLiteral("3") && !route3Enabled()) return;
        if (webPages_.contains(route)) return;
        if (route == QStringLiteral("3") && route3_) return;
        if (route == QStringLiteral("4") && route4_) return;

        QWidget* page = nullptr;
        if (route == QStringLiteral("1") || route == QStringLiteral("2")) {
            auto* wp = new WebRoutePage(route, this, core_, stack_);
            webPages_.insert(route, wp);
            if (loginStateKnown_) wp->applyLoginState(loginText_, loginCan_);
            else wp->setLoginChecking();
            page = wp;
        } else if (route == QStringLiteral("3")) {
            route3_ = new Route3Page(this, core_, stack_);
            page = route3_;
        } else if (route == QStringLiteral("4")) {
            route4_ = new Route4Page(this, core_, stack_);
            page = route4_;
        }
        QWidget* ph = routePlaceholders_.value(route);
        if (!page || !ph) return;
        const int idx = stack_->indexOf(ph);
        stack_->removeWidget(ph);
        ph->deleteLater();
        routePlaceholders_.remove(route);
        stack_->insertWidget(idx, page);
        routeIndex_.insert(route, idx);
    }

    bool routePageReady(const QString& route) const {
        if (route == QStringLiteral("1") || route == QStringLiteral("2")) return webPages_.contains(route);
        if (route == QStringLiteral("3")) return route3_ != nullptr;
        if (route == QStringLiteral("4")) return route4_ != nullptr;
        return false;
    }

    void enterRoute(const QString& route) {
        if (route == QStringLiteral("3") && !route3Enabled()) {
            showPicker();
            return;
        }
        if (!routeIndex_.contains(route)) {
            showPicker();
            return;
        }
        if (!routePageReady(route)) {
            ensureRoutePage(route);
        }
        if (!routePageReady(route)) {
            showPicker();
            return;
        }
        liveaio::util::configSet(QStringLiteral("route"), route);
        stack_->setCurrentIndex(routeIndex_.value(route));
        if (webPages_.contains(route)) {
            auto* wp = webPages_.value(route);
            // Keep last known login state visible while refreshing; only show
            // "checking" when we have never received login.state yet.
            if (loginStateKnown_) wp->applyLoginState(loginText_, loginCan_);
            else wp->setLoginChecking();
            if (core_) core_->uiCommand(QStringLiteral("login.query"));
        } else if (core_) {
            core_->uiCommand(QStringLiteral("route.env"), route);
        }
    }

    void handleRouteEnv(const QJsonObject& packet) {
        const QString route = packet.value(QStringLiteral("route")).toString();
        if (packet.contains(QStringLiteral("action"))) {
            const QString msg = packet.value(QStringLiteral("message")).toString();
            if (!msg.isEmpty()) {
                toast_->showMsg(msg, !packet.value(QStringLiteral("ok")).toBool(true));
            }
            return;
        }
        const RouteEnv env = RouteEnv::fromPacket(packet);
        if (route == QStringLiteral("3") && route3_) route3_->applyEnv(env);
        else if (route == QStringLiteral("4") && route4_) route4_->applyEnv(env);
    }

    void handleStatus(bool connected, const QString& routeHint) {
        QString route = routeHint;
        if (route.isEmpty()) {
            route = connected
                ? configValue(QStringLiteral("route"), QStringLiteral("2")).toString()
                : (!connectedRoute_.isEmpty() ? connectedRoute_ : activeListenerRoute());
        }
        switching_ = false;

        if (connected) {
            connectedRoute_ = route;
            dispatchStatus(route, true);
            resetOtherPages(route);
            return;
        }
        dispatchStatus(route, false);
        connectedRoute_.clear();
        resetOtherPages(activeListenerRoute());
    }

    void dispatchStatus(const QString& route, bool connected) {
        if (auto* page = webPages_.value(route)) {
            page->onStatusChange(connected);
        } else if (route == QStringLiteral("3") && route3_) {
            route3_->onStatusChange(connected);
        } else if (route == QStringLiteral("4") && route4_) {
            route4_->onStatusChange(connected);
        }
    }

    void resetOtherPages(const QString& activeRoute) {
        for (auto it = webPages_.constBegin(); it != webPages_.constEnd(); ++it) {
            if (it.key() != activeRoute && it.value()->btnState() != BtnState::Error) {
                it.value()->resetIdle();
            }
        }
        if (route3_ && activeRoute != QStringLiteral("3") && route3_->btnState() != BtnState::Error) {
            route3_->resetIdle();
        }
        if (route4_ && activeRoute != QStringLiteral("4") && route4_->btnState() != BtnState::Error) {
            route4_->resetIdle();
        }
    }

    CoreClient* core_ = nullptr;
    Toast* toast_ = nullptr;
    QStackedWidget* stack_ = nullptr;
    RoutePickerPage* picker_ = nullptr;
    QMap<QString, WebRoutePage*> webPages_;
    QMap<QString, QWidget*> routePlaceholders_;
    Route3Page* route3_ = nullptr;
    Route4Page* route4_ = nullptr;
    QMap<QString, int> routeIndex_;
    QString connectedRoute_;
    QString loginText_;
    bool loginCan_ = true;
    bool loginStateKnown_ = false;
    bool switching_ = false;
};

// ── RouteDetailPage / 各线路页依赖 HomePage 的实现放在这里 ──
inline QPushButton* RouteDetailPage::makeBackButton(QWidget* parent) {
    auto* btn = new QPushButton(QStringLiteral("← 返回选择线路"), parent);
    btn->setCursor(Qt::PointingHandCursor);
    QObject::connect(btn, &QPushButton::clicked, this, [this]() {
        if (home_) home_->showPicker();
    });
    return btn;
}

inline bool WebRoutePage::waitingForSwitch() const {
    return home_ && home_->switchingListener() && home_->activeListenerRoute() == route_;
}

inline void WebRoutePage::toast(const QString& msg, bool error) const {
    if (home_) home_->toast()->showMsg(msg, error);
}

inline void WebRoutePage::onConnClicked() {
    if (state_ == BtnState::Idle || state_ == BtnState::Error) {
        const QString liveId = roomInput_->text().trimmed();
        if (liveId.isEmpty()) {
            toast(QStringLiteral("请先填写直播间 ID"), true);
            return;
        }
        setConnState(BtnState::Connecting);
        wasConnecting_ = true;
        liveaio::util::configSet(QStringLiteral("route"), route_);
        if (home_) home_->requestConnect(route_, liveId);
    } else if (state_ == BtnState::Connected) {
        setConnState(BtnState::Idle);
        wasConnecting_ = false;
        if (home_) home_->requestDisconnect();
        toast(QStringLiteral("已断开连接"));
    }
}

inline void WebRoutePage::onStatusChange(bool connected) {
    if (connected) {
        preempted_ = false;
        wasConnecting_ = false;
        setConnState(BtnState::Connected);
        toast(QStringLiteral("直播间已连接"));
        return;
    }
    if (preempted_) {
        preempted_ = false;
        return;
    }
    const bool wasConnected = state_ == BtnState::Connected;
    if (wasConnecting_ && !wasConnected) {
        setConnState(BtnState::Error);
        connLabel_->setText(QStringLiteral("⚠️  直播间已断开或没有连接"));
        connLabel_->setStyleSheet(qssErrorLabel());
        connLabel_->setVisible(true);
        toast(QStringLiteral("直播间已断开"), true);
    } else {
        setConnState(BtnState::Idle);
        connLabel_->setVisible(false);
        if (wasConnected) toast(QStringLiteral("直播已断开"), true);
    }
    wasConnecting_ = false;
}

inline bool Route3Page::waitingForSwitch() const {
    return home_ && home_->switchingListener();
}

inline void Route3Page::toast(const QString& msg, bool error) const {
    if (home_) home_->toast()->showMsg(msg, error);
}

inline void Route3Page::pickCompanionDir() {
    const QString path = QFileDialog::getExistingDirectory(
        this, QStringLiteral("选择直播伴侣安装目录"));
    if (path.isEmpty() || !core_) return;
    core_->uiCommand(QStringLiteral("route4.set_companion_path"), QStringLiteral("4"),
                     QJsonObject{{QStringLiteral("path"), path}});
}

inline void Route3Page::onConnClicked() {
    if (state_ == BtnState::Idle || state_ == BtnState::Error) {
        if (!ready()) {
            toast(QStringLiteral("请先完成环境检测（Unpatch / 关闭代理 / 指定伴侣路径）"), true);
            return;
        }
        setConnState(BtnState::Connecting);
        wasConnecting_ = true;
        liveaio::util::configSet(QStringLiteral("route"), QStringLiteral("3"));
        if (home_) home_->requestConnect(QStringLiteral("3"), QString());
    } else if (state_ == BtnState::Connecting) {
        setConnState(BtnState::Idle);
        wasConnecting_ = false;
        if (home_) home_->requestDisconnect();
        toast(QStringLiteral("已取消连接"));
    } else if (state_ == BtnState::Connected) {
        setConnState(BtnState::Idle);
        wasConnecting_ = false;
        if (home_) home_->requestDisconnect();
        toast(QStringLiteral("已断开连接"));
    }
}

inline void Route3Page::onStatusChange(bool connected) {
    if (connected) {
        preempted_ = false;
        wasConnecting_ = false;
        setConnState(BtnState::Connected);
        toast(QStringLiteral("监听已开启，等待伴侣开播"));
        return;
    }
    if (preempted_) {
        preempted_ = false;
        return;
    }
    const bool wasConnected = state_ == BtnState::Connected;
    if (wasConnecting_ && !wasConnected) {
        setConnState(BtnState::Error);
        connLabel_->setText(QStringLiteral("⚠️  启动失败，请检查环境后重试"));
        connLabel_->setStyleSheet(qssErrorLabel());
        connLabel_->setVisible(true);
        toast(QStringLiteral("启动失败"), true);
    } else {
        setConnState(BtnState::Idle);
        connLabel_->setVisible(false);
        if (wasConnected) toast(QStringLiteral("已断开连接"));
    }
    wasConnecting_ = false;
}

inline bool Route4Page::waitingForSwitch() const {
    return home_ && home_->switchingListener();
}

inline void Route4Page::toast(const QString& msg, bool error) const {
    if (home_) home_->toast()->showMsg(msg, error);
}

inline void Route4Page::pickCompanionDir() {
    const QString path = QFileDialog::getExistingDirectory(
        this, QStringLiteral("选择直播伴侣安装目录"));
    if (path.isEmpty() || !core_) return;
    core_->uiCommand(QStringLiteral("route4.set_companion_path"), QStringLiteral("4"),
                     QJsonObject{{QStringLiteral("path"), path}});
}

inline void Route4Page::onConnClicked() {
    if (state_ == BtnState::Idle || state_ == BtnState::Error) {
        if (!ready()) {
            if (env_.manualPathInvalid) {
                toast(QStringLiteral("该指定目录无效"), true);
            } else if (env_.patchNeeded || !env_.isPatched) {
                toast(QStringLiteral("请先完成 Patch 并重启直播伴侣"), true);
            } else {
                toast(QStringLiteral("环境未就绪"), true);
            }
            return;
        }
        setConnState(BtnState::Connecting);
        wasConnecting_ = true;
        liveaio::util::configSet(QStringLiteral("route"), QStringLiteral("4"));
        if (home_) home_->requestConnect(QStringLiteral("4"), QString());
    } else if (state_ == BtnState::Connecting) {
        setConnState(BtnState::Idle);
        wasConnecting_ = false;
        if (home_) home_->requestDisconnect();
        toast(QStringLiteral("已取消连接"));
    } else if (state_ == BtnState::Connected) {
        setConnState(BtnState::Idle);
        wasConnecting_ = false;
        if (home_) home_->requestDisconnect();
        toast(QStringLiteral("已断开连接"));
    }
}

inline void Route4Page::onStatusChange(bool connected) {
    if (connected) {
        preempted_ = false;
        wasConnecting_ = false;
        setConnState(BtnState::Connected);
        toast(QStringLiteral("直播间已连接"));
        return;
    }
    if (preempted_) {
        preempted_ = false;
        return;
    }
    const bool wasConnected = state_ == BtnState::Connected;
    if (wasConnecting_ && !wasConnected) {
        setConnState(BtnState::Error);
        connLabel_->setText(QStringLiteral("⚠️  未连接到直播间"));
        connLabel_->setStyleSheet(qssErrorLabel());
        connLabel_->setVisible(true);
        toast(QStringLiteral("未连接到直播间"), true);
    } else {
        setConnState(BtnState::Idle);
        connLabel_->setVisible(false);
        if (wasConnected) toast(QStringLiteral("直播间下播，已断开连接"), true);
    }
    wasConnecting_ = false;
}

}  // namespace liveaio::pages
