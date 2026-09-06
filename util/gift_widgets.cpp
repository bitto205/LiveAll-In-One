// util/gift_widgets.cpp — 礼物选择弹层 / 模拟送礼（tools 专用，依赖 resources/gift）。
// 由 tools_main 在 gift.cpp 之后 include；勿放进 pages。
#ifndef LIVEAIO_UTIL_GIFT_WIDGETS_CPP
#define LIVEAIO_UTIL_GIFT_WIDGETS_CPP

#include <QDir>
#include <QFrame>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QHideEvent>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QScrollArea>
#include <QScrollBar>
#include <QSet>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>

#include <algorithm>
#include <functional>

namespace liveaio::util {

static constexpr int kGiftIcon = 48;
static constexpr int kPickerCols = 4;
static constexpr int kPickerRows = 4;
static constexpr int kPickerCell = 64;
static constexpr int kPickerGutter = 16;
static constexpr int kPickerW =
    10 + kPickerCell * kPickerCols + 6 * (kPickerCols - 1) + 10 + kPickerGutter;
static constexpr int kPickerH = 10 + 34 + 8 + kPickerRows * (kPickerCell + 18) + 10;
static constexpr int kSimPickW = 90;
static constexpr int kSimBtnH = 32;
static constexpr int kSimBtnGap = 12;

inline QString giftResourcesRoot() {
    QString root = toolAppRoot();
    if (root.isEmpty()) root = qEnvironmentVariable(QByteArrayLiteral("LIVEAIO_ROOT"));
    if (root.isEmpty()) root = QDir::currentPath();
    return root;
}

// 礼物选择栏：名单打开时预加载，搜索走全量；网格滚动分批挂载，缩略图延后加载。
class GiftPickerPopup final : public QFrame {
public:
    static constexpr int kPickerBatch = kPickerCols * kPickerRows;

    explicit GiftPickerPopup(QWidget* parent = nullptr)
        : QFrame(parent, Qt::Popup | Qt::FramelessWindowHint) {
        setFixedSize(kPickerW, kPickerH);

        auto* lay = new QVBoxLayout(this);
        lay->setContentsMargins(8, 8, 8, 8);
        lay->setSpacing(8);

        auto* sr = new QHBoxLayout;
        sr->setSpacing(6);
        search_ = new QLineEdit(this);
        search_->setPlaceholderText(QStringLiteral("搜索礼物"));
        search_->setFixedHeight(30);
        QObject::connect(search_, &QLineEdit::textChanged, this, [this]() { refreshGrid(); });
        auto* sbtn = new QPushButton(QStringLiteral("搜索"), this);
        sbtn->setFixedSize(52, 30);
        sbtn->setCursor(Qt::PointingHandCursor);
        suppressButtonFocus(sbtn);
        QObject::connect(sbtn, &QPushButton::clicked, this, [this]() { refreshGrid(); });
        sr->addWidget(search_, 1);
        sr->addWidget(sbtn);
        lay->addLayout(sr);

        scroll_ = new QScrollArea(this);
        scroll_->setWidgetResizable(true);
        scroll_->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        scroll_->setFixedHeight(kPickerRows * (kPickerCell + 18));
        scroll_->setStyleSheet(
            QStringLiteral("QScrollArea { border: none; background: transparent; }"));
        gridHost_ = new QWidget(scroll_);
        grid_ = new QGridLayout(gridHost_);
        grid_->setContentsMargins(0, 0, 2, 0);
        grid_->setHorizontalSpacing(6);
        grid_->setVerticalSpacing(6);
        scroll_->setWidget(gridHost_);
        lay->addWidget(scroll_);

        QObject::connect(scroll_->verticalScrollBar(), &QScrollBar::valueChanged, this,
                         [this](int) { loadMoreIfNeeded(); });

        refreshTheme();
        onThemeChange(this, [this](const QString&) { refreshTheme(); });
    }

    void setOnPicked(std::function<void(const QString&)> cb) { onPicked_ = std::move(cb); }

    // showAll=true：模拟区，展示全部礼物；否则隐藏 blocked 中已占用的礼物。
    void openAt(QWidget* anchor, const QSet<QString>& blocked, bool showAll) {
        ensureGiftNames();
        showAll_ = showAll;
        blocked_ = showAll ? QSet<QString>() : blocked;
        move(anchor->mapToGlobal(QPoint(anchor->width() + 6, 0)));
        search_->clear();
        refreshGrid();
        show();
        search_->setFocus();
    }

    void refreshTheme() {
        const auto& C = theme();
        setStyleSheet(QStringLiteral(
            "QFrame { background: %1; border: 1px solid %2; border-radius: 4px; }"
            "QLineEdit { background: %1; color: %3; border: 1px solid %4;"
            " border-radius: 6px; padding: 0 8px; font-size: 12px; }"
            "QPushButton { background: transparent; color: %3; border: 1px solid %4;"
            " border-radius: 4px; font-size: 12px; }"
            "QPushButton:hover { background: %5; border-color: %2; }"
            "QLabel { background: transparent; border: none; color: %3; }"
        ).arg(C.card, C.activeLine, C.text, C.border, C.hover) + popupChromeQss());
    }

protected:
    void hideEvent(QHideEvent* event) override {
        QFrame::hideEvent(event);
        clearGrid();
        // 不在此清空全局 thumb 缓存：关弹层再开时大量空图/闪烁，且会丢掉刚解好的图。
    }

private:
    static bool fuzzyMatch(const QString& name, const QString& query) {
        const QString q = query.trimmed().toLower();
        if (q.isEmpty()) return true;
        const QString n = name.toLower();
        if (n.contains(q)) return true;
        int i = 0;
        for (const QChar& c : n) {
            if (i < q.size() && c == q.at(i)) ++i;
        }
        return i == q.size();
    }

    void clearGrid() {
        while (grid_->count() > 0) {
            QLayoutItem* item = grid_->takeAt(0);
            if (QWidget* w = item->widget()) w->deleteLater();
            delete item;
        }
        filteredNames_.clear();
        builtCount_ = 0;
    }

    void refreshGrid() {
        ensureGiftNames();
        const QString q = search_ ? search_->text() : QString();
        clearGrid();
        for (const QString& n : allNames_) {
            if (!blocked_.contains(n) && fuzzyMatch(n, q)) filteredNames_ << n;
        }
        if (filteredNames_.isEmpty()) {
            auto* hint = new QLabel(
                showAll_ || !q.trimmed().isEmpty() ? QStringLiteral("无匹配礼物")
                                                   : QStringLiteral("暂无其它礼物可选"));
            hint->setAlignment(Qt::AlignCenter);
            QFont f(QStringLiteral("Microsoft YaHei"));
            f.setPixelSize(11);
            hint->setFont(f);
            hint->setStyleSheet(QStringLiteral("color: %1; background: transparent;")
                                    .arg(theme().textMuted));
            grid_->addWidget(hint, 0, 0, 1, kPickerCols);
            return;
        }
        appendCells(std::min(kPickerBatch, static_cast<int>(filteredNames_.size())));
        scroll_->verticalScrollBar()->setValue(0);
    }

    void appendCells(int count) {
        const int end = std::min(builtCount_ + count, static_cast<int>(filteredNames_.size()));
        for (int i = builtCount_; i < end; ++i) {
            grid_->addWidget(makeCell(filteredNames_.at(i)), i / kPickerCols, i % kPickerCols);
        }
        builtCount_ = end;
        gridHost_->adjustSize();
    }

    void loadMoreIfNeeded() {
        if (builtCount_ >= filteredNames_.size()) return;
        const int contentBottom = scroll_->verticalScrollBar()->value()
                                  + scroll_->viewport()->height();
        if (gridHost_->height() - contentBottom > kPickerCell + 24) return;
        if (chunkLoading_) return;
        const int remain = filteredNames_.size() - builtCount_;
        if (remain <= 0) return;
        chunkLoading_ = true;
        if (!chunkBuilder_) {
            chunkBuilder_ = new ChunkBuilder(this, kPickerCols, 24);
        }
        chunkBuilder_->start(std::min(kPickerBatch, remain), [this](int) { appendCells(1); },
                             [this]() { chunkLoading_ = false; });
    }

    QPushButton* makeCell(const QString& name) {
        const auto& C = theme();
        auto* btn = new QPushButton;
        btn->setFixedSize(kPickerCell, kPickerCell + 16);
        btn->setCursor(Qt::PointingHandCursor);
        btn->setAccessibleName(name);
        btn->setStyleSheet(QStringLiteral(
            "QPushButton { background: transparent; border: 1px solid transparent;"
            " border-radius: 4px; padding: 0; }"
            "QPushButton:hover { background: %2; border-color: %1; }"
        ).arg(C.activeLine, C.hover));

        auto* lay = new QVBoxLayout(btn);
        lay->setContentsMargins(2, 2, 2, 2);
        lay->setSpacing(0);

        auto* icon = new QLabel(btn);
        icon->setFixedSize(kPickerCell - 4, kPickerCell - 4);
        icon->setAlignment(Qt::AlignCenter);
        icon->setStyleSheet(QStringLiteral("background: transparent; border: none;"));

        auto* nameLbl = new QLabel(name, btn);
        nameLbl->setFixedSize(kPickerCell - 4, 14);
        nameLbl->setAlignment(Qt::AlignCenter);
        QFont f(QStringLiteral("Microsoft YaHei"));
        f.setPixelSize(10);
        nameLbl->setFont(f);
        nameLbl->setStyleSheet(QStringLiteral("background: transparent; border: none;"));

        lay->addWidget(icon, 0, Qt::AlignHCenter);
        lay->addWidget(nameLbl, 0, Qt::AlignHCenter);
        QObject::connect(btn, &QPushButton::clicked, this, [this, name]() {
            if (onPicked_) onPicked_(name);
            hide();
        });
        QTimer::singleShot(0, icon, [icon, name]() {
            if (!icon) return;
            const QPixmap px = liveaio::resources::loadGiftPixmapThumb(
                giftResourcesRoot(), name, kPickerCell - 8);
            if (!px.isNull()) {
                icon->setPixmap(px);
                icon->setText(QString());
            }
        });
        return btn;
    }

    void ensureGiftNames() {
        if (!allNames_.isEmpty()) return;
        // 无图标文件的礼物占多数，选择器只列可显示项，避免大片空白格。
        allNames_ = liveaio::resources::giftNamesWithIconsCached(giftResourcesRoot());
        if (allNames_.isEmpty()) {
            allNames_ = liveaio::resources::giftNamesCached(giftResourcesRoot());
        }
    }

    QStringList allNames_;
    QStringList filteredNames_;
    QSet<QString> blocked_;
    bool showAll_ = false;
    int builtCount_ = 0;
    QLineEdit* search_ = nullptr;
    QScrollArea* scroll_ = nullptr;
    QWidget* gridHost_ = nullptr;
    QGridLayout* grid_ = nullptr;
    ChunkBuilder* chunkBuilder_ = nullptr;
    bool chunkLoading_ = false;
    std::function<void(const QString&)> onPicked_;
};

inline GiftPickerPopup*& sessionGiftPickerSlot() {
    static GiftPickerPopup* picker = nullptr;
    return picker;
}

inline QObject*& giftPickerParentSlot() {
    static QObject* parent = nullptr;
    return parent;
}

inline void setGiftPickerParent(QObject* parent) { giftPickerParentSlot() = parent; }

inline GiftPickerPopup* sessionGiftPicker() {
    auto*& picker = sessionGiftPickerSlot();
    QObject* parent = giftPickerParentSlot();
    if (!picker && parent) {
        picker = new GiftPickerPopup(nullptr);
        QObject::connect(parent, &QObject::destroyed, picker, &QObject::deleteLater);
        QObject::connect(parent, &QObject::destroyed, []() { sessionGiftPickerSlot() = nullptr; });
    }
    return picker;
}

inline void hideSessionGiftPicker() {
    if (auto* picker = sessionGiftPickerSlot()) picker->hide();
}

// 设置页：模拟推送礼物（礼物选择 + 数量 + 推送）。
class SimGiftWidget final : public QFrame {
public:
    explicit SimGiftWidget(QWidget* parent = nullptr) : QFrame(parent) {
        setObjectName(QStringLiteral("OvertimeSimGift"));
        setAttribute(Qt::WA_TranslucentBackground);
        gift_ = QStringLiteral("小心心");
        build();
        showIcon(gift_);
        onThemeChange(this, [this](const QString&) { refreshTheme(); });
    }

    void setOnPush(std::function<void(const QString&, int)> cb) { onPush_ = std::move(cb); }

    void setClosedTip(const QString& tip) { closedTip_ = tip; }

    void setPushEnabled(bool enabled) {
        pushBtn_->setEnabled(enabled);
        pushBtn_->setToolTip(enabled ? QString() : closedTip_);
    }

    void refreshTheme() {
        const auto& C = theme();
        // 悬停与其他按钮统一：底色变 hover、文字转正色，而不是只换文字颜色。
        pickBtn_->setStyleSheet(QStringLiteral(
            "QPushButton#OvertimeSimPickBtn { background: transparent; color: %1;"
            " border: 1.5px solid %1; border-radius: 6px; font-size: 12px; font-weight: 600;"
            " padding: 0 10px; min-height: %2px; max-height: %2px; }"
            "QPushButton#OvertimeSimPickBtn:hover { background: %3; color: %4; }"
        ).arg(C.activeLine, QString::number(kSimBtnH), C.hover, C.text));
        iconLbl_->setStyleSheet(QStringLiteral(
            "QLabel#OvertimeSimGiftIcon { background: transparent; border: none; }"));
        // 实心强调钮统一：悬停转 C.active，文字保持白色。
        pushBtn_->setStyleSheet(QStringLiteral(
            "QPushButton { background: %1; color: #fff; border: none; border-radius: 6px;"
            " font-size: 13px; font-weight: 600; }"
            "QPushButton:hover { background: %2; }"
            "QPushButton:disabled { background: %3; color: %4; }"
        ).arg(C.activeLine, C.active, C.border, C.textMuted));
        if (auto* picker = sessionGiftPicker()) picker->refreshTheme();
    }

private:
    void build() {
        setFixedHeight(kGiftIcon);
        auto* root = new QHBoxLayout(this);
        root->setContentsMargins(0, 0, 0, 0);
        root->setSpacing(10);

        auto* leftWrap = new QWidget(this);
        leftWrap->setObjectName(QStringLiteral("OvertimeSimLeft"));
        leftWrap->setAttribute(Qt::WA_TranslucentBackground);
        leftWrap->setFixedSize(kSimPickW + kSimBtnGap + kGiftIcon, kGiftIcon);
        auto* left = new QHBoxLayout(leftWrap);
        left->setContentsMargins(0, 0, 0, 0);
        left->setSpacing(kSimBtnGap);

        pickBtn_ = new QPushButton(QStringLiteral("选择礼物"), leftWrap);
        pickBtn_->setObjectName(QStringLiteral("OvertimeSimPickBtn"));
        pickBtn_->setFlat(true);
        pickBtn_->setFixedSize(kSimPickW, kSimBtnH);
        pickBtn_->setCursor(Qt::PointingHandCursor);
        suppressButtonFocus(pickBtn_);
        QObject::connect(pickBtn_, &QPushButton::clicked, this, [this]() {
            auto* picker = sessionGiftPicker();
            picker->setOnPicked([this](const QString& name) {
                gift_ = name;
                showIcon(name);
            });
            picker->openAt(pickBtn_, {}, true);
        });
        left->addWidget(pickBtn_, 0, Qt::AlignVCenter);

        iconLbl_ = new QLabel(leftWrap);
        iconLbl_->setObjectName(QStringLiteral("OvertimeSimGiftIcon"));
        iconLbl_->setFixedSize(kGiftIcon, kGiftIcon);
        iconLbl_->setAlignment(Qt::AlignCenter);
        iconLbl_->setAttribute(Qt::WA_TranslucentBackground);
        left->addWidget(iconLbl_, 0, Qt::AlignVCenter);
        root->addWidget(leftWrap);

        auto* qtyRow = new QHBoxLayout;
        qtyRow->setSpacing(6);
        auto* qtyLbl = new QLabel(QStringLiteral("数量"), this);
        qtyLbl->setFixedHeight(kGiftIcon);
        qtyLbl->setAlignment(Qt::AlignCenter);
        count_ = new IntField(9999, 4, 28, 52, this);
        count_->setValue(1);
        qtyRow->addWidget(qtyLbl);
        qtyRow->addWidget(count_);
        root->addLayout(qtyRow);
        root->addStretch();

        pushBtn_ = new QPushButton(QStringLiteral("推送"), this);
        pushBtn_->setFixedSize(72, 34);
        pushBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(pushBtn_, &QPushButton::clicked, this, [this]() {
            const int count = count_->value();
            if (count <= 0) return;
            if (onPush_) onPush_(gift_, count);
        });
        root->addWidget(pushBtn_);
        refreshTheme();
    }

    void showIcon(const QString& name) {
        liveaio::resources::setGiftIconOnLabel(iconLbl_, giftResourcesRoot(), name, kGiftIcon - 6, false);
    }

    QString gift_;
    QString closedTip_ = QStringLiteral("请先打开加班机悬浮窗");
    QPushButton* pickBtn_ = nullptr;
    QLabel* iconLbl_ = nullptr;
    IntField* count_ = nullptr;
    QPushButton* pushBtn_ = nullptr;
    std::function<void(const QString&, int)> onPush_;
};

}  // namespace liveaio::util

#endif  // LIVEAIO_UTIL_GIFT_WIDGETS_CPP
