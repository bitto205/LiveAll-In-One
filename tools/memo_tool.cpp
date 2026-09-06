// tools/memo_tool.cpp — 备忘录，对齐旧 PySide memo_tool.py。
// 380×560 最小 / 420×620 默认；顶部 Tab：主页面 / 设置。

namespace liveaio::tools {

namespace memo_keys {
static const QString kGiftOn = QStringLiteral("memo.gift.enabled");
static const QString kGiftStack = QStringLiteral("memo.gift.stack");
static const QString kGiftMinDia = QStringLiteral("memo.gift.min_diamonds");
static const QString kFollowOn = QStringLiteral("memo.follow.enabled");
static const QString kLikeOn = QStringLiteral("memo.like.enabled");
static const QString kLikeStack = QStringLiteral("memo.like.stack");
}  // namespace memo_keys

// 单条备忘录条目：点击整行或右侧 × 消除；可叠加条目更新数量。
class MemoItem final : public QFrame {
public:
    MemoItem(const QString& text, bool stackable, QWidget* parent = nullptr)
        : QFrame(parent), stackable_(stackable), baseText_(text) {
        setObjectName(QStringLiteral("MemoItem"));
        setCursor(Qt::PointingHandCursor);

        auto* lay = new QHBoxLayout(this);
        lay->setContentsMargins(12, 8, 8, 8);
        lay->setSpacing(8);

        label_ = new QLabel(text, this);
        label_->setWordWrap(true);
        label_->setStyleSheet(QStringLiteral("background: transparent;"));
        lay->addWidget(label_, 1);

        closeBtn_ = new QPushButton(QStringLiteral("×"), this);
        closeBtn_->setFixedSize(24, 24);
        liveaio::util::suppressButtonFocus(closeBtn_);
        QObject::connect(closeBtn_, &QPushButton::clicked, this, [this]() { dismiss(); });
        lay->addWidget(closeBtn_);

        refreshTheme();
        liveaio::util::onThemeChange(this, [this](const QString&) { refreshTheme(); });
    }

    void addCount(int delta) {
        if (!stackable_) return;
        count_ += delta;
        label_->setText(QStringLiteral("%1 x%2").arg(baseText_, QString::number(count_)));
    }

    void refreshTheme() {
        const auto& C = theme();
        setStyleSheet(QStringLiteral(
            "#MemoItem { background: %1; border-radius: 8px; border: 1px solid %2; }"
            "#MemoItem:hover { background: %3; }"
        ).arg(C.card, C.border, C.hover));
        closeBtn_->setStyleSheet(QStringLiteral(
            "QPushButton { background: transparent; border: none; color: %1;"
            " font-size: 16px; border-radius: 4px; }"
            "QPushButton:hover { background: %2; color: %3; }"
        ).arg(C.textMuted, C.hover, C.text));
    }

protected:
    void mousePressEvent(QMouseEvent* event) override {
        if (event->button() == Qt::LeftButton) dismiss();
        QFrame::mousePressEvent(event);
    }

private:
    void dismiss() {
        setParent(nullptr);
        deleteLater();
    }

    bool stackable_ = false;
    QString baseText_;
    int count_ = 1;
    QLabel* label_ = nullptr;
    QPushButton* closeBtn_ = nullptr;
};

// 主页面：顶部「清空全部」+ 条目列表（新条目插到顶部）。
class MemoMainTab final : public QWidget {
public:
    explicit MemoMainTab(QWidget* parent = nullptr) : QWidget(parent) {
        auto* lay = new QVBoxLayout(this);
        lay->setContentsMargins(16, 16, 16, 16);
        lay->setSpacing(8);

        clearBtn_ = new QPushButton(QStringLiteral("清空全部"), this);
        clearBtn_->setFixedHeight(30);
        clearBtn_->setCursor(Qt::PointingHandCursor);
        liveaio::util::suppressButtonFocus(clearBtn_);
        QObject::connect(clearBtn_, &QPushButton::clicked, this, [this]() { clearAll(); });
        auto* top = new QHBoxLayout;
        top->addStretch();
        top->addWidget(clearBtn_);
        lay->addLayout(top);

        scroll_ = new QScrollArea(this);
        scroll_->setWidgetResizable(true);
        scroll_->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        scroll_->setStyleSheet(
            QStringLiteral("QScrollArea { border: none; background: transparent; }"));

        auto* container = new QWidget(scroll_);
        listLay_ = new QVBoxLayout(container);
        listLay_->setContentsMargins(0, 0, 0, 0);
        listLay_->setSpacing(6);
        listLay_->addStretch();
        scroll_->setWidget(container);
        lay->addWidget(scroll_);

        refreshTheme();
    }

    QWidget* listHost() const { return scroll_->widget(); }

    void addItem(MemoItem* item) {
        listLay_->insertWidget(listLay_->count() - 1, item);
        scroll_->verticalScrollBar()->setValue(0);
    }

    void setOnCleared(std::function<void()> cb) { onCleared_ = std::move(cb); }

    void clearAll() {
        while (listLay_->count() > 1) {
            QLayoutItem* item = listLay_->takeAt(0);
            if (QWidget* w = item->widget()) w->deleteLater();
            delete item;
        }
        if (onCleared_) onCleared_();
    }

    void refreshTheme() {
        const auto& C = theme();
        clearBtn_->setStyleSheet(QStringLiteral(
            "QPushButton { background: transparent; color: %1; border: 1px solid %2;"
            " border-radius: 6px; font-size: 12px; padding: 0 12px; }"
            "QPushButton:hover { background: %4; color: %3; border-color: %3; }"
        ).arg(C.textMuted, C.border, C.closeHover, C.hover));
    }

private:
    QPushButton* clearBtn_ = nullptr;
    QScrollArea* scroll_ = nullptr;
    QVBoxLayout* listLay_ = nullptr;
    std::function<void()> onCleared_;
};

class MemoToolWindow final : public TabbedToolWindow {
public:
    explicit MemoToolWindow(CoreClient* core)
        : TabbedToolWindow(core, {QStringLiteral("主页面"), QStringLiteral("设置")}) {
        setWindowTitle(QStringLiteral("备忘录"));
        setMinimumSize(380, 560);
        resize(420, 620);

        mainTab_ = new MemoMainTab(this);
        mainTab_->setOnCleared([this]() { stackItems_.clear(); });
        addTabPage(mainTab_);
        addTabPlaceholder();
        setTabFactory(1, [this]() { return buildSettingsTab(); });
        switchTab(0);

        refreshTheme();
        liveaio::util::onThemeChange(this, [this](const QString&) { refreshTheme(); });
        pushSettings();
    }

    QString toolId() const override { return QStringLiteral("memo"); }

    void onCorePacket(const QJsonObject& packet) override {
        if (packet.value(QStringLiteral("op")).toString() != QStringLiteral("memo.item")) return;
        const QString user = packet.value(QStringLiteral("user")).toString();
        const QString kind = packet.value(QStringLiteral("kind")).toString();
        QString text = packet.value(QStringLiteral("text")).toString();
        if (text.isEmpty() && !user.isEmpty()) text = QStringLiteral("[%1] %2").arg(user, kind);
        const QString key = packet.value(QStringLiteral("stack_key"))
                                .toString(QStringLiteral("custom"));
        const bool stack = packet.value(QStringLiteral("stack")).toBool();

        if (stack) {
            if (auto* existing = stackItems_.value(key)) {
                existing->addCount(1);
                return;
            }
            stackItems_.remove(key);
        }
        const QString line = text.startsWith(QLatin1Char('['))
            ? text : QStringLiteral("[%1] %2").arg(user, text);
        addItem(key, line, stack);
    }

    void refreshTheme() override {
        mainTab_->refreshTheme();
        applyAddBtnStyle();
    }

private:
    QWidget* buildSettingsTab() {
        auto* inner = new QWidget;
        auto* lay = new QVBoxLayout(inner);
        lay->setContentsMargins(24, 20, 24, 20);
        lay->setSpacing(12);

        auto memoToggle = [this](const QString& key, bool defaultOn) {
            auto* t = new ThemedToggle(key, defaultOn);
            t->setOnToggled([this](bool) { pushSettings(); });
            return t;
        };

        lay->addWidget(sectionCard(QStringLiteral("礼物"), {
            {QStringLiteral("启用"), memoToggle(memo_keys::kGiftOn, true)},
            {QStringLiteral("叠加"), memoToggle(memo_keys::kGiftStack, true)},
            {QStringLiteral("最低钻石数"), buildDiamondInput()},
        }, inner));

        lay->addWidget(sectionCard(QStringLiteral("关注"), {
            {QStringLiteral("启用"), memoToggle(memo_keys::kFollowOn, true)},
        }, inner));

        lay->addWidget(sectionCard(QStringLiteral("点赞"), {
            {QStringLiteral("启用"), memoToggle(memo_keys::kLikeOn, true)},
            {QStringLiteral("叠加"), memoToggle(memo_keys::kLikeStack, true)},
        }, inner));

        auto* customCard = new QFrame(inner);
        customCard->setObjectName(QStringLiteral("Card"));
        auto* cLay = new QVBoxLayout(customCard);
        cLay->setContentsMargins(16, 14, 16, 14);
        cLay->setSpacing(10);
        auto* title = new QLabel(QStringLiteral("自定义"), customCard);
        title->setObjectName(QStringLiteral("SectionTitle"));
        cLay->addWidget(title);
        auto* row = new QHBoxLayout;
        customInput_ = new QLineEdit(customCard);
        customInput_->setFixedHeight(liveaio::util::kControlH);
        customInput_->setPlaceholderText(QStringLiteral("输入备忘内容..."));
        QObject::connect(customInput_, &QLineEdit::returnPressed, this, [this]() { addCustom(); });
        addBtn_ = new liveaio::util::ChromeButton(
            QStringLiteral("添加"), customCard, liveaio::util::kControlH);
        addBtn_->setPadX(14);
        QObject::connect(addBtn_, &QPushButton::clicked, this, [this]() { addCustom(); });
        row->addWidget(customInput_);
        row->addWidget(addBtn_);
        cLay->addLayout(row);
        lay->addWidget(customCard);
        lay->addStretch();

        auto* page = new QWidget;
        auto* root = new QVBoxLayout(page);
        root->setContentsMargins(0, 0, 0, 0);
        root->addWidget(scrollPage(inner));
        liveaio::util::onThemeChange(page, [inner](const QString&) {
            const auto& C = theme();
            inner->setStyleSheet(QStringLiteral(
                "QFrame#Card { background: %1; border-radius: 10px; border: 1px solid %2; }"
                "QLabel#SectionTitle { color: %3; }"
            ).arg(C.card, C.border, C.text));
        });
        return page;
    }

    QWidget* buildDiamondInput() {
        auto* wrap = new QWidget;
        wrap->setStyleSheet(QStringLiteral("background: transparent;"));
        auto* row = new QHBoxLayout(wrap);
        row->setContentsMargins(0, 0, 0, 0);
        row->setSpacing(6);

        diamondInput_ = new QLineEdit(
            QString::number(configValue(memo_keys::kGiftMinDia, 0).toInt()), wrap);
        diamondInput_->setFixedSize(80, 30);
        diamondInput_->setAlignment(Qt::AlignCenter);
        diamondInput_->setPlaceholderText(QStringLiteral("0"));
        QObject::connect(diamondInput_, &QLineEdit::editingFinished, this, [this]() {
            bool ok = false;
            int value = diamondInput_->text().trimmed().toInt(&ok);
            if (!ok || value < 0) value = 0;
            diamondInput_->setText(QString::number(value));
            writeConfigValue(memo_keys::kGiftMinDia, value);
            pushSettings();
        });

        diamondHint_ = new QLabel(QStringLiteral("0 = 不过滤"), wrap);
        row->addWidget(diamondInput_);
        row->addWidget(diamondHint_);
        row->addStretch();
        diamondHint_->setStyleSheet(qssMutedLabel(11));
        return wrap;
    }

    void applyAddBtnStyle() {
        if (!addBtn_) return;
        addBtn_->update();  // 自绘描边钮，跟随主题重画即可
        if (diamondHint_) diamondHint_->setStyleSheet(qssMutedLabel(11));
    }

    void addCustom() {
        const QString text = customInput_->text().trimmed();
        if (text.isEmpty()) return;
        addItem(QStringLiteral("custom"), text, false);
        customInput_->clear();
        switchTab(0);
    }

    void addItem(const QString& key, const QString& text, bool stack) {
        auto* item = new MemoItem(text, stack, mainTab_->listHost());
        if (stack) {
            stackItems_.insert(key, item);
            QObject::connect(item, &QObject::destroyed, this, [this, key]() {
                stackItems_.remove(key);
            });
        }
        mainTab_->addItem(item);
    }

    void pushSettings() {
        const QJsonObject settings{
            {memo_keys::kGiftOn, configValue(memo_keys::kGiftOn, true).toBool()},
            {memo_keys::kGiftStack, configValue(memo_keys::kGiftStack, true).toBool()},
            {memo_keys::kGiftMinDia, configValue(memo_keys::kGiftMinDia, 0).toInt()},
            {memo_keys::kFollowOn, configValue(memo_keys::kFollowOn, true).toBool()},
            {memo_keys::kLikeOn, configValue(memo_keys::kLikeOn, true).toBool()},
            {memo_keys::kLikeStack, configValue(memo_keys::kLikeStack, true).toBool()},
        };
        sendCore(QJsonObject{
            {QStringLiteral("op"), QStringLiteral("tool.memo.set")},
            {QStringLiteral("settings"), settings},
        });
    }

    MemoMainTab* mainTab_ = nullptr;
    QLineEdit* customInput_ = nullptr;
    liveaio::util::ChromeButton* addBtn_ = nullptr;
    QLineEdit* diamondInput_ = nullptr;
    QLabel* diamondHint_ = nullptr;
    QMap<QString, MemoItem*> stackItems_;
};

static ToolWindowBase* createMemoTool(CoreClient* core) {
    return new MemoToolWindow(core);
}

}  // namespace liveaio::tools
