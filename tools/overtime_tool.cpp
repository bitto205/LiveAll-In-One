// tools/overtime_tool.cpp — 加班机，对齐旧 PySide overtime_tool.py。
// 控制面板（设置 / 加班机两页）+ 用户时长统计窗 + 透明悬浮倒计时窗。

namespace liveaio::tools {
namespace ot {

// ═══════════════════════════════════════════
// 配置 / 规则
// ═══════════════════════════════════════════

static const QString kCfgKey = QStringLiteral("overtime.settings");
static constexpr int kRuleCount = 6;

static const QStringList& units() {
    static const QStringList v = {QStringLiteral("时"), QStringLiteral("分"), QStringLiteral("秒")};
    return v;
}
static const QStringList& modes() {
    static const QStringList v = {QStringLiteral("加"), QStringLiteral("减"), QStringLiteral("随机")};
    return v;
}
static const QStringList& aligns() {
    static const QStringList v = {QStringLiteral("居左"), QStringLiteral("居中"),
                                  QStringLiteral("居右")};
    return v;
}
static const QStringList& defaultGifts() {
    static const QStringList v = {
        QStringLiteral("小心心"), QStringLiteral("玫瑰"), QStringLiteral("大啤酒"),
        QStringLiteral("鲜花"), QStringLiteral("热气球"), QStringLiteral("嘉年华")};
    return v;
}

struct Rule {
    QString gift = QStringLiteral("小心心");
    QString mode = QStringLiteral("加");
    int value = 1;
    QString unit = QStringLiteral("分");
    int randomNeg = 0;
    int randomPos = 1;
    QString randomUnit = QStringLiteral("分");
};

struct Settings {
    int hours = 2;
    int minutes = 0;
    int seconds = 0;
    QVector<Rule> rules;
    QString customText = QStringLiteral("可以输入自定义的文字");
    QString customAlign = QStringLiteral("居中");
};

static int clampInt(const QVariant& raw, int lo, int hi) {
    bool ok = false;
    int n = raw.toInt(&ok);
    if (!ok) n = lo;
    return std::clamp(n, lo, hi);
}

static QVector<Rule> defaultRules() {
    QVector<Rule> out;
    for (const QString& gift : defaultGifts()) {
        Rule r;
        r.gift = gift;
        out.append(r);
    }
    return out;
}

static Rule normalizeRule(const QVariantMap& raw) {
    Rule r;
    const QString gift = raw.value(QStringLiteral("gift")).toString().trimmed();
    if (!gift.isEmpty()) r.gift = gift;
    const QString mode = raw.value(QStringLiteral("mode")).toString();
    if (modes().contains(mode)) r.mode = mode;
    r.value = clampInt(raw.value(QStringLiteral("value")), 0, 999);
    const QString unit = raw.value(QStringLiteral("unit")).toString();
    if (units().contains(unit)) r.unit = unit;
    r.randomNeg = clampInt(raw.value(QStringLiteral("random_neg")), 0, 999);
    r.randomPos = clampInt(raw.value(QStringLiteral("random_pos")), 0, 999);
    const QString ru = raw.value(QStringLiteral("random_unit")).toString();
    r.randomUnit = units().contains(ru) ? ru : r.unit;
    return r;
}

static QVariantMap ruleToMap(const Rule& r) {
    return QVariantMap{
        {QStringLiteral("gift"), r.gift},
        {QStringLiteral("mode"), r.mode},
        {QStringLiteral("value"), r.value},
        {QStringLiteral("unit"), r.unit},
        {QStringLiteral("random_neg"), r.randomNeg},
        {QStringLiteral("random_pos"), r.randomPos},
        {QStringLiteral("random_unit"), r.randomUnit},
    };
}

static QString nextUnusedGift(const QSet<QString>& used, int preferIndex) {
    const QStringList pool = defaultGifts();
    if (preferIndex >= 0 && preferIndex < pool.size() && !used.contains(pool.at(preferIndex))) {
        return pool.at(preferIndex);
    }
    for (const QString& g : pool) {
        if (!used.contains(g)) return g;
    }
    for (const QString& g : liveaio::resources::giftNamesCached(g_appRoot)) {
        if (!used.contains(g)) return g;
    }
    int n = 1;
    while (true) {
        const QString fallback = QStringLiteral("礼物%1").arg(n);
        if (!used.contains(fallback)) return fallback;
        ++n;
    }
}

// 6 格礼物必须互不相同；冲突格自动换成未占用礼物，preferIndex 优先保留。
static QVector<Rule> dedupeRules(const QVector<Rule>& rules, int preferIndex = -1) {
    QVector<Rule> out;
    const QVector<Rule> fallback = defaultRules();
    for (int i = 0; i < kRuleCount; ++i) {
        out.append(i < rules.size() ? rules.at(i) : fallback.at(i));
    }
    QVector<int> order;
    for (int i = 0; i < kRuleCount; ++i) order.append(i);
    if (preferIndex >= 0 && preferIndex < kRuleCount) {
        order.removeAll(preferIndex);
        order.prepend(preferIndex);
    }
    QSet<QString> used;
    for (int i : order) {
        QString gift = out[i].gift.trimmed();
        if (gift.isEmpty() || used.contains(gift)) gift = nextUnusedGift(used, i);
        out[i].gift = gift;
        used.insert(gift);
    }
    return out;
}

static Settings loadSettings() {
    Settings out;
    out.rules = defaultRules();
    const QVariantMap raw = configValue(kCfgKey).toMap();
    if (raw.isEmpty()) return out;
    out.hours = clampInt(raw.value(QStringLiteral("hours")), 0, 999);
    out.minutes = clampInt(raw.value(QStringLiteral("minutes")), 0, 60);
    out.seconds = clampInt(raw.value(QStringLiteral("seconds")), 0, 60);
    const QVariant text = raw.value(QStringLiteral("custom_text"));
    if (text.typeId() == QMetaType::QString) out.customText = text.toString().left(80);
    const QString align = raw.value(QStringLiteral("custom_align")).toString();
    if (aligns().contains(align)) out.customAlign = align;

    const QVariantList rules = raw.value(QStringLiteral("rules")).toList();
    if (!rules.isEmpty()) {
        QVector<Rule> merged;
        for (int i = 0; i < kRuleCount; ++i) {
            if (i < rules.size() && rules.at(i).canConvert<QVariantMap>()) {
                merged.append(normalizeRule(rules.at(i).toMap()));
            } else {
                merged.append(out.rules.at(i));
            }
        }
        out.rules = dedupeRules(merged);
    }
    return out;
}

static QVariantMap settingsToMap(const Settings& s) {
    QVariantList rules;
    for (const Rule& r : s.rules) rules.append(ruleToMap(r));
    return QVariantMap{
        {QStringLiteral("hours"), s.hours},
        {QStringLiteral("minutes"), s.minutes},
        {QStringLiteral("seconds"), s.seconds},
        {QStringLiteral("rules"), rules},
        {QStringLiteral("custom_text"), s.customText},
        {QStringLiteral("custom_align"), s.customAlign},
    };
}

static void saveSettings(const Settings& s) {
    writeConfigValue(kCfgKey, settingsToMap(s));
}

static int totalSeconds(int h, int m, int s) {
    return std::clamp(h, 0, 999) * 3600 + std::clamp(m, 0, 60) * 60 + std::clamp(s, 0, 60);
}

static int unitToSeconds(int value, const QString& unit) {
    const int v = std::clamp(value, 0, 999);
    if (unit == QStringLiteral("时")) return v * 3600;
    if (unit == QStringLiteral("分")) return v * 60;
    return v;
}

static QString formatTimerDisplay(int totalSec) {
    const int total = std::max(0, totalSec);
    const int h = total / 3600;
    const int m = (total % 3600) / 60;
    const int s = total % 60;
    if (h >= 1) {
        return QStringLiteral("%1:%2:%3")
            .arg(h)
            .arg(m, 2, 10, QLatin1Char('0'))
            .arg(s, 2, 10, QLatin1Char('0'));
    }
    return QStringLiteral("%1:%2")
        .arg(m, 2, 10, QLatin1Char('0'))
        .arg(s, 2, 10, QLatin1Char('0'));
}

static QString formatDeltaLog(int deltaSeconds) {
    const QString sign = deltaSeconds >= 0 ? QStringLiteral("+") : QStringLiteral("-");
    const int total = std::abs(deltaSeconds);
    const int h = total / 3600;
    const int m = (total % 3600) / 60;
    const int s = total % 60;
    QStringList parts;
    if (h > 0) parts << QStringLiteral("%1小时").arg(h);
    if (m > 0) parts << QStringLiteral("%1分钟").arg(m);
    if (s > 0 || parts.isEmpty()) parts << QStringLiteral("%1秒").arg(s);
    return sign + parts.join(QString());
}

// 统计窗专用：固定 时/分/秒 三段，便于列宽对齐。
static QString formatStatsDuration(int seconds, const QString& forcedSign = QString(),
                                   bool signed_ = false) {
    if (seconds == 0) return QStringLiteral("—");
    QString sign = forcedSign;
    if (sign.isEmpty() && signed_) {
        sign = seconds > 0 ? QStringLiteral("+") : QStringLiteral("-");
    }
    const int total = std::abs(seconds);
    const int h = std::min(total / 3600, 999);
    const int m = (total % 3600) / 60;
    const int s = total % 60;
    return QStringLiteral("%1%2时%3分%4秒")
        .arg(sign)
        .arg(h)
        .arg(m, 2, 10, QLatin1Char('0'))
        .arg(s, 2, 10, QLatin1Char('0'));
}

static QString ruleSlotLabel(const Rule& r) {
    if (r.mode == QStringLiteral("随机")) {
        return QStringLiteral("随机-%1~+%2%3").arg(r.randomNeg).arg(r.randomPos).arg(r.randomUnit);
    }
    const QString sign = r.mode == QStringLiteral("加") ? QStringLiteral("+") : QStringLiteral("-");
    return QStringLiteral("%1%2%3").arg(sign).arg(r.value).arg(r.unit);
}

static QString giftLogLeft(const QString& user, const QString& gift, int count) {
    const QString u = user.trimmed().isEmpty() ? QStringLiteral("观众") : user.trimmed();
    const QString g = gift.trimmed().isEmpty() ? QStringLiteral("礼物") : gift.trimmed();
    const int n = std::max(1, count);
    if (n > 1) return QStringLiteral("%1 送出了 %2 ×%3").arg(u, g).arg(n);
    return QStringLiteral("%1 送出了 %2").arg(u, g);
}

static Qt::Alignment alignToQt(const QString& align) {
    if (align == QStringLiteral("居左")) return Qt::AlignLeft | Qt::AlignVCenter;
    if (align == QStringLiteral("居右")) return Qt::AlignRight | Qt::AlignVCenter;
    return Qt::AlignHCenter | Qt::AlignVCenter;
}

static ToolSkin activeOvertimeSkin() {
    const QString id = configValue(liveaio::resources::skinConfigKey(QStringLiteral("overtime")),
                                  QStringLiteral("default")).toString();
    return ToolSkin::load(g_appRoot, QStringLiteral("overtime"),
                          id.isEmpty() ? QStringLiteral("default") : id);
}

// ═══════════════════════════════════════════
// 设置页尺寸（贴内容宽度，避免礼物格左右留白）
// ═══════════════════════════════════════════

static constexpr int kGiftIcon = 48;
static constexpr int kGiftPick = 48;
static constexpr int kGiftLeftW = kGiftPick + 3 + kGiftIcon;
static constexpr int kCtrlH = 24;
static constexpr int kCtrlGap = 2;
static constexpr int kModeW = 56;
static constexpr int kUnitW = 52;
static constexpr int kValW = 42;
static constexpr int kValWTime = 52;
static constexpr int kLblNegW = 14;
static constexpr int kLblToW = 24;
static constexpr int kLblPosW = 14;
static constexpr int kRandomRowW = kLblNegW + kValW + kLblToW + kLblPosW + kValW;
static constexpr int kRightW = kRandomRowW > kModeW ? kRandomRowW : kModeW;
static constexpr int kModuleW = kGiftLeftW + kRightW + 8;
static constexpr int kModuleH = kCtrlH * 3 + kCtrlGap * 2 + 8;
static constexpr int kGridGap = 8;
static constexpr int kSectionBodyMx = 16;
static constexpr int kGridContentW = kModuleW * 2 + kGridGap;
static constexpr int kPanelW = kGridContentW + kSectionBodyMx;
static constexpr int kSide = 24;
static constexpr int kScrollGutter = 8;
static constexpr int kPagePad = kSide + kScrollGutter / 2;
static constexpr int kToolWinW = kPanelW + 2 * kPagePad;
static constexpr int kToolWinH = 720;

using IntField = liveaio::util::IntField;
using GiftPickerPopup = liveaio::util::GiftPickerPopup;
using SimGiftWidget = liveaio::util::SimGiftWidget;

inline void setGiftPickerParent(QObject* parent) {
    liveaio::util::setGiftPickerParent(parent);
}
inline GiftPickerPopup* sessionGiftPicker() { return liveaio::util::sessionGiftPicker(); }
inline void hideSessionGiftPicker() { liveaio::util::hideSessionGiftPicker(); }

// ═══════════════════════════════════════════
// 基础控件
// ═══════════════════════════════════════════

// 分区：居中加粗标题 + 无底色内容区。
class SectionBlock final : public QFrame {
public:
    explicit SectionBlock(const QString& title, QWidget* parent = nullptr) : QFrame(parent) {
        setObjectName(QStringLiteral("OvertimeSection"));
        setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
        auto* root = new QVBoxLayout(this);
        root->setContentsMargins(0, 0, 0, 0);
        root->setSpacing(6);

        auto* bar = new QLabel(title, this);
        bar->setObjectName(QStringLiteral("OvertimeSectionTitle"));
        bar->setAlignment(Qt::AlignCenter);
        root->addWidget(bar);

        auto* body = new QFrame(this);
        body->setObjectName(QStringLiteral("OvertimeSectionBody"));
        content = new QVBoxLayout(body);
        content->setContentsMargins(8, 0, 8, 8);
        content->setSpacing(6);
        root->addWidget(body);
    }

    QVBoxLayout* content = nullptr;
};

// 单个礼物规则（对应悬浮窗上的一格）。
class GiftRuleModule final : public QFrame {
public:
    GiftRuleModule(int index, const Rule& rule, QWidget* parent = nullptr)
        : QFrame(parent), index_(index), rule_(rule) {
        setObjectName(QStringLiteral("OvertimeGiftModule"));
        setFixedSize(kModuleW, kModuleH);
        build();
        loadRule(rule_);
    }

    void setPickerCallback(std::function<void(GiftRuleModule*)> cb) { pickerCb_ = std::move(cb); }
    void setBlockedFn(std::function<QSet<QString>()> fn) { blockedFn_ = std::move(fn); }
    void setOnChanged(std::function<void(int)> cb) { onChanged_ = std::move(cb); }
    QPushButton* pickAnchor() const { return pickBtn_; }
    QString currentGift() const { return rule_.gift; }

    void applyGift(const QString& name) {
        if (blockedFn_ && blockedFn_().contains(name)) return;
        rule_.gift = name;
        showIcon(name);
        emitChange();
    }

    Rule toRule() const {
        Rule r;
        r.gift = rule_.gift.isEmpty() ? QStringLiteral("小心心") : rule_.gift;
        r.mode = mode_->currentText();
        r.value = value_->value();
        r.unit = unit_->currentText();
        r.randomNeg = randNeg_->value();
        r.randomPos = randPos_->value();
        r.randomUnit = r.unit;
        return normalizeRule(ruleToMap(r));
    }

    void loadRule(const Rule& rule) {
        rule_ = rule;
        loading_ = true;
        mode_->setCurrentText(rule.mode);
        value_->setValue(rule.value);
        unit_->setCurrentText(rule.mode == QStringLiteral("随机") ? rule.randomUnit : rule.unit);
        randNeg_->setValue(rule.randomNeg);
        randPos_->setValue(rule.randomPos);
        loading_ = false;
        showIcon(rule.gift);
        syncMode();
    }

    void refreshTheme() {
        const auto& C = theme();
        pickBtn_->setStyleSheet(QStringLiteral(
            "QPushButton { background: transparent; color: %1; border: 1px solid %2;"
            " border-radius: 3px; font-size: 10px; padding: 0 1px; }"
            "QPushButton:hover { background: %3; color: %2; }"
        ).arg(C.text, C.activeLine, C.hover));
        iconLbl_->setStyleSheet(
            QStringLiteral("QLabel#OvertimeGiftIcon { background: transparent; border: none; }"));
        mode_->refreshTheme();
        unit_->refreshTheme();
    }

private:
    static QLabel* smallLabel(const QString& text) {
        auto* lb = new QLabel(text);
        lb->setStyleSheet(QStringLiteral("background: transparent;"));
        int w = 20;
        if (text == QStringLiteral("负")) w = kLblNegW;
        else if (text == QStringLiteral("正")) w = kLblPosW;
        else if (text == QStringLiteral("到 ")) w = kLblToW;
        lb->setFixedSize(w, kCtrlH);
        QFont f(QStringLiteral("Microsoft YaHei"));
        f.setPixelSize(11);
        lb->setFont(f);
        lb->setAlignment(Qt::AlignCenter);
        return lb;
    }

    void build() {
        auto* root = new QHBoxLayout(this);
        root->setContentsMargins(4, 4, 4, 4);
        root->setSpacing(0);

        auto* leftWrap = new QWidget(this);
        leftWrap->setAttribute(Qt::WA_TranslucentBackground);
        leftWrap->setFixedSize(kGiftLeftW, kGiftIcon);
        auto* left = new QHBoxLayout(leftWrap);
        left->setContentsMargins(0, 0, 0, 0);
        left->setSpacing(3);
        pickBtn_ = new QPushButton(QStringLiteral("礼物\n选择"), leftWrap);
        pickBtn_->setFixedSize(kGiftPick, kGiftPick);
        pickBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(pickBtn_, &QPushButton::clicked, this, [this]() {
            if (pickerCb_) pickerCb_(this);
        });
        left->addWidget(pickBtn_);

        iconLbl_ = new QLabel(leftWrap);
        iconLbl_->setObjectName(QStringLiteral("OvertimeGiftIcon"));
        iconLbl_->setFixedSize(kGiftIcon, kGiftIcon);
        iconLbl_->setAlignment(Qt::AlignCenter);
        left->addWidget(iconLbl_);
        root->addWidget(leftWrap, 0, Qt::AlignTop);

        auto* rightWrap = new QWidget(this);
        rightWrap->setAttribute(Qt::WA_TranslucentBackground);
        rightWrap->setFixedSize(kRightW, kCtrlH * 3 + kCtrlGap * 2);
        auto* right = new QVBoxLayout(rightWrap);
        right->setContentsMargins(0, 0, 0, 0);
        right->setSpacing(kCtrlGap);
        right->setAlignment(Qt::AlignTop | Qt::AlignLeft);

        mode_ = new ThemedComboBox(rightWrap);
        mode_->setCompact(true);
        mode_->addItems(modes());
        mode_->setFixedSize(kModeW, kCtrlH);
        mode_->setOnChange([this](const QString&) {
            syncMode();
            emitChange();
        });
        right->addWidget(mode_, 0, Qt::AlignLeft);

        rowStack_ = new QStackedWidget(rightWrap);
        rowStack_->setFixedSize(kValW, kCtrlH);

        auto* simple = new QWidget(rowStack_);
        simple->setFixedSize(kValW, kCtrlH);
        auto* sr = new QHBoxLayout(simple);
        sr->setContentsMargins(0, 0, 0, 0);
        sr->setSpacing(0);
        sr->setAlignment(Qt::AlignLeft);
        value_ = new IntField(999, 3, kCtrlH, kValW, simple);
        value_->setOnCommit([this]() { emitChange(); });
        sr->addWidget(value_, 0, Qt::AlignLeft);
        rowStack_->addWidget(simple);

        auto* random = new QWidget(rowStack_);
        random->setFixedSize(kRandomRowW, kCtrlH);
        auto* rr = new QHBoxLayout(random);
        rr->setContentsMargins(0, 0, 0, 0);
        rr->setSpacing(0);
        rr->setAlignment(Qt::AlignLeft);
        rr->addWidget(smallLabel(QStringLiteral("负")));
        randNeg_ = new IntField(999, 3, kCtrlH, kValW, random);
        randNeg_->setOnCommit([this]() { emitChange(); });
        rr->addWidget(randNeg_, 0, Qt::AlignLeft);
        rr->addWidget(smallLabel(QStringLiteral("到 ")));
        rr->addWidget(smallLabel(QStringLiteral("正")));
        randPos_ = new IntField(999, 3, kCtrlH, kValW, random);
        randPos_->setOnCommit([this]() { emitChange(); });
        rr->addWidget(randPos_, 0, Qt::AlignLeft);
        rowStack_->addWidget(random);
        right->addWidget(rowStack_, 0, Qt::AlignLeft);

        auto* unitRow = new QWidget(rightWrap);
        unitRow->setFixedHeight(kCtrlH);
        auto* ur = new QHBoxLayout(unitRow);
        ur->setContentsMargins(0, 0, 0, 0);
        ur->setSpacing(0);
        unit_ = new ThemedComboBox(unitRow);
        unit_->setCompact(true);
        unit_->addItems(units());
        unit_->setFixedSize(kUnitW, kCtrlH);
        unit_->setOnChange([this](const QString&) { emitChange(); });
        ur->addWidget(unit_, 0, Qt::AlignLeft);
        right->addWidget(unitRow);

        root->addWidget(rightWrap, 0, Qt::AlignTop | Qt::AlignLeft);
        refreshTheme();
        liveaio::util::onThemeChange(this, [this](const QString&) { refreshTheme(); });
    }

    void showIcon(const QString& name) {
        liveaio::resources::setGiftIconOnLabel(iconLbl_, g_appRoot, name, kGiftIcon - 6, false);
    }

    void syncMode() {
        const bool isRand = mode_->currentText() == QStringLiteral("随机");
        rowStack_->setCurrentIndex(isRand ? 1 : 0);
        rowStack_->setFixedSize(isRand ? kRandomRowW : kValW, kCtrlH);
    }

    void emitChange() {
        if (loading_) return;
        rule_ = toRule();
        if (onChanged_) onChanged_(index_);
    }

    int index_ = 0;
    Rule rule_;
    bool loading_ = false;
    QPushButton* pickBtn_ = nullptr;
    QLabel* iconLbl_ = nullptr;
    ThemedComboBox* mode_ = nullptr;
    ThemedComboBox* unit_ = nullptr;
    QStackedWidget* rowStack_ = nullptr;
    IntField* value_ = nullptr;
    IntField* randNeg_ = nullptr;
    IntField* randPos_ = nullptr;
    std::function<void(GiftRuleModule*)> pickerCb_;
    std::function<QSet<QString>()> blockedFn_;
    std::function<void(int)> onChanged_;
};

// 加班机设置面板：剩余时间 / 礼物设置 / 自定义信息三块。
class SettingsPanel final : public QWidget {
public:
    explicit SettingsPanel(QWidget* parent = nullptr) : QWidget(parent) {
        data_ = loadSettings();
        setFixedWidth(kPanelW);
        setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Maximum);
        build();
        liveaio::util::onThemeChange(this, [this](const QString&) { refreshTheme(); });
    }

    void setOnTimeApply(std::function<void(const Settings&)> cb) { onTimeApply_ = std::move(cb); }
    void setOnOtherApply(std::function<void(const Settings&)> cb) { onOtherApply_ = std::move(cb); }
    void setOnSaved(std::function<void(const Settings&)> cb) { onSaved_ = std::move(cb); }
    const Settings& currentSettings() const { return data_; }

    void refreshTheme() {
        styleApplyBtn(timeApplyBtn_);
        styleApplyBtn(otherApplyBtn_);
        for (auto* row : ruleRows_) row->refreshTheme();
        align_->refreshTheme();
        sessionGiftPicker()->refreshTheme();
    }

private:
    void build() {
        auto* root = new QVBoxLayout(this);
        root->setContentsMargins(0, 0, 0, 0);
        root->setSpacing(12);

        timeApplyBtn_ = new QPushButton(QStringLiteral("应用"), this);
        timeApplyBtn_->setFixedSize(72, 34);
        timeApplyBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(timeApplyBtn_, &QPushButton::clicked, this, [this]() {
            saveTime();
            if (onTimeApply_) onTimeApply_(data_);
        });

        otherApplyBtn_ = new QPushButton(QStringLiteral("应用"), this);
        otherApplyBtn_->setFixedSize(72, 34);
        otherApplyBtn_->setCursor(Qt::PointingHandCursor);
        QObject::connect(otherApplyBtn_, &QPushButton::clicked, this, [this]() {
            saveRules(-1);
            saveCustom();
            if (onOtherApply_) onOtherApply_(data_);
        });

        root->addWidget(blockTime());
        root->addWidget(blockGifts());
        root->addWidget(blockCustom());
        refreshTheme();
    }

    void styleApplyBtn(QPushButton* btn) {
        const auto& C = theme();
        btn->setStyleSheet(QStringLiteral(
            "QPushButton { background: %1; color: #fff; border: none; border-radius: 6px;"
            " font-size: 13px; font-weight: 600; }"
            "QPushButton:hover { background: %2; color: %3; }"
        ).arg(C.activeLine, C.hover, C.text));
    }

    SectionBlock* blockTime() {
        auto* sec = new SectionBlock(QStringLiteral("剩余时间设置"), this);
        sec->setFixedWidth(kPanelW);
        auto* row = new QHBoxLayout;
        row->setSpacing(4);
        h_ = new IntField(999, 3, 28, kValWTime);
        m_ = new IntField(60, 2, 28, 40);
        s_ = new IntField(60, 2, 28, 40);
        h_->setValue(data_.hours);
        m_->setValue(data_.minutes);
        s_->setValue(data_.seconds);

        auto* inner = new QHBoxLayout;
        inner->setSpacing(4);
        const QVector<QPair<IntField*, QString>> fields = {
            {h_, QStringLiteral("时")}, {m_, QStringLiteral("分")}, {s_, QStringLiteral("秒")}};
        for (const auto& item : fields) {
            item.first->setOnCommit([this]() { saveTime(); });
            inner->addWidget(item.first);
            auto* lb = new QLabel(item.second);
            lb->setFixedHeight(28);
            lb->setAlignment(Qt::AlignCenter);
            inner->addWidget(lb);
        }
        row->addStretch();
        row->addLayout(inner);
        row->addSpacing(8);
        row->addWidget(timeApplyBtn_);
        row->addStretch();
        sec->content->addLayout(row);
        return sec;
    }

    SectionBlock* blockGifts() {
        auto* sec = new SectionBlock(QStringLiteral("礼物设置"), this);
        sec->setFixedWidth(kPanelW);
        auto* gridHost = new QWidget(sec);
        gridHost->setFixedWidth(kGridContentW);
        auto* gh = new QHBoxLayout(gridHost);
        gh->setContentsMargins(0, 0, 0, 0);
        auto* grid = new QGridLayout;
        grid->setHorizontalSpacing(kGridGap);
        grid->setVerticalSpacing(6);
        gh->addLayout(grid);

        for (int i = 0; i < data_.rules.size(); ++i) {
            auto* mod = new GiftRuleModule(i, data_.rules.at(i), gridHost);
            mod->setBlockedFn([this]() { return assignedGifts(); });
            mod->setPickerCallback([this](GiftRuleModule* target) {
                pickTarget_ = target;
                auto* picker = sessionGiftPicker();
                picker->setOnPicked([this](const QString& name) {
                    if (pickTarget_) pickTarget_->applyGift(name);
                });
                picker->openAt(target->pickAnchor(), assignedGifts(), false);
            });
            mod->setOnChanged([this](int idx) { saveRules(idx); });
            ruleRows_.append(mod);
            grid->addWidget(mod, i / 2, i % 2);
        }
        sec->content->addWidget(gridHost);
        return sec;
    }

    SectionBlock* blockCustom() {
        auto* sec = new SectionBlock(QStringLiteral("自定义信息设置"), this);
        sec->setFixedWidth(kPanelW);
        custom_ = new QTextEdit(sec);
        custom_->setPlaceholderText(QStringLiteral("最多两行"));
        custom_->setFixedHeight(48);
        custom_->setAcceptRichText(false);
        custom_->setLineWrapMode(QTextEdit::WidgetWidth);
        custom_->setPlainText(data_.customText);
        QObject::connect(custom_, &QTextEdit::textChanged, this, [this]() { saveCustom(); });
        sec->content->addWidget(custom_);

        auto* ar = new QHBoxLayout;
        ar->addWidget(new QLabel(QStringLiteral("对齐"), sec));
        align_ = new ThemedComboBox(sec);
        align_->setCompact(true);
        align_->addItems(aligns());
        align_->setFixedSize(kUnitW + 20, kCtrlH + 4);
        align_->setCurrentText(data_.customAlign);
        align_->setOnChange([this](const QString&) { saveCustom(); });
        ar->addWidget(align_);
        ar->addStretch();
        sec->content->addLayout(ar);

        auto* applyRow = new QHBoxLayout;
        applyRow->setContentsMargins(0, 6, 0, 0);
        applyRow->addStretch();
        applyRow->addWidget(otherApplyBtn_);
        sec->content->addLayout(applyRow);
        return sec;
    }

    QSet<QString> assignedGifts() const {
        QSet<QString> names;
        for (auto* row : ruleRows_) {
            const QString g = row->currentGift().trimmed();
            if (!g.isEmpty()) names.insert(g);
        }
        return names;
    }

    void saveTime() {
        data_.hours = h_->value();
        data_.minutes = m_->value();
        data_.seconds = s_->value();
        persist();
    }

    void saveRules(int preferIndex) {
        QVector<Rule> raw;
        for (auto* row : ruleRows_) raw.append(row->toRule());
        data_.rules = dedupeRules(raw, preferIndex);
        for (int i = 0; i < ruleRows_.size() && i < data_.rules.size(); ++i) {
            if (ruleRows_[i]->currentGift() != data_.rules[i].gift) {
                ruleRows_[i]->loadRule(data_.rules[i]);
            }
        }
        persist();
    }

    void saveCustom() {
        QString text = custom_->toPlainText();
        QStringList lines = text.split(QLatin1Char('\n'));
        if (lines.size() > 2) {
            text = QStringList(lines.mid(0, 2)).join(QLatin1Char('\n'));
            const QSignalBlocker blocker(custom_);
            custom_->setPlainText(text);
        }
        data_.customText = text.left(80);
        data_.customAlign = align_->currentText();
        persist();
    }

    void persist() {
        saveSettings(data_);
        if (onSaved_) onSaved_(data_);
    }

    Settings data_;
    GiftRuleModule* pickTarget_ = nullptr;
    QVector<GiftRuleModule*> ruleRows_;
    IntField* h_ = nullptr;
    IntField* m_ = nullptr;
    IntField* s_ = nullptr;
    QTextEdit* custom_ = nullptr;
    ThemedComboBox* align_ = nullptr;
    QPushButton* timeApplyBtn_ = nullptr;
    QPushButton* otherApplyBtn_ = nullptr;
    std::function<void(const Settings&)> onTimeApply_;
    std::function<void(const Settings&)> onOtherApply_;
    std::function<void(const Settings&)> onSaved_;
};

// ═══════════════════════════════════════════
// 用户时长统计
// ═══════════════════════════════════════════

// core 的 ledger 只给每人净秒数；这里按快照差分还原增加/减少两列。
class UserLedger {
public:
    struct Row {
        QString key;
        int add = 0;
        int sub = 0;
        int net = 0;
    };

    void setOnChanged(std::function<void()> cb) { onChanged_ = std::move(cb); }

    void clear() {
        add_.clear();
        sub_.clear();
        lastNet_.clear();
        notify();
    }

    // 返回本次快照中变化最大的用户与其变化量，供悬浮窗日志行使用。
    QPair<QString, int> applySnapshot(const QMap<QString, int>& net) {
        QString hotKey;
        int hotDelta = 0;
        for (auto it = net.constBegin(); it != net.constEnd(); ++it) {
            const QString key = it.key().trimmed().isEmpty() ? QStringLiteral("(未知)") : it.key();
            const int delta = it.value() - lastNet_.value(key, 0);
            lastNet_.insert(key, it.value());
            if (delta == 0) continue;
            if (delta > 0) add_[key] += delta;
            else sub_[key] += -delta;
            if (std::abs(delta) > std::abs(hotDelta)) {
                hotKey = key;
                hotDelta = delta;
            }
        }
        if (hotDelta != 0) notify();
        return {hotKey, hotDelta};
    }

    bool isEmpty() const { return add_.isEmpty() && sub_.isEmpty(); }

    QVector<Row> rows() const {
        QSet<QString> keys;
        for (auto it = add_.constBegin(); it != add_.constEnd(); ++it) keys.insert(it.key());
        for (auto it = sub_.constBegin(); it != sub_.constEnd(); ++it) keys.insert(it.key());
        QStringList sorted(keys.constBegin(), keys.constEnd());
        sorted.sort();
        QVector<Row> out;
        for (const QString& key : sorted) {
            Row row;
            row.key = key;
            row.add = add_.value(key, 0);
            row.sub = sub_.value(key, 0);
            row.net = row.add - row.sub;
            if (row.add == 0 && row.sub == 0) continue;
            out.append(row);
        }
        return out;
    }

private:
    void notify() {
        if (onChanged_) onChanged_();
    }

    QMap<QString, int> add_;
    QMap<QString, int> sub_;
    QMap<QString, int> lastNet_;
    std::function<void()> onChanged_;
};

// 只读展示：加班机运行期间各用户的加减时长。
class UserTimeWindow final : public QMainWindow {
public:
    static constexpr int kWinMargin = 14;
    static constexpr int kWinRadius = 10;

    explicit UserTimeWindow(QWidget* parent = nullptr)
        : QMainWindow(parent, Qt::FramelessWindowHint | Qt::Window) {
        setAttribute(Qt::WA_TranslucentBackground);
        setAttribute(Qt::WA_QuitOnClose, false);
        setWindowTitle(QStringLiteral("用户时长统计"));

        const QFontMetrics fm(QFont(QStringLiteral("Microsoft YaHei"), 13));
        const int cellPad = 36;
        colIdW_ = fm.horizontalAdvance(QString(15, QLatin1Char('0'))) + cellPad;
        colDataW_ = fm.horizontalAdvance(QStringLiteral("+999时59分59秒")) + cellPad;
        const int minW = 16 * 2 + colIdW_ + colDataW_ * 3 + 24;
        setMinimumSize(minW, 400 + kWinMargin * 2);
        resize(minW + 40, 460 + kWinMargin * 2);

        buildUi(minW - kWinMargin * 2);
        refreshTheme();
        liveaio::util::onThemeChange(this, [this](const QString&) { refreshTheme(); });
    }

    void bindLedger(UserLedger* ledger) {
        ledger_ = ledger;
        refresh();
    }

    void refresh() {
        if (!ledger_ || ledger_->isEmpty()) {
            hint_->setText(QStringLiteral("当前没有统计数据。请先打开加班机并接收礼物后再查看。"));
            table_->setRowCount(0);
            return;
        }
        hint_->setText(
            QStringLiteral("以下为本次加班机运行期间，各用户礼物触发的加减时长汇总（实时更新）。"));
        const auto rows = ledger_->rows();
        table_->setRowCount(rows.size());
        for (int i = 0; i < rows.size(); ++i) {
            const auto& row = rows.at(i);
            table_->setItem(i, 0, new QTableWidgetItem(row.key));
            const QVector<QString> cells = {
                formatStatsDuration(row.add, QStringLiteral("+")),
                formatStatsDuration(row.sub, QStringLiteral("-")),
                formatStatsDuration(row.net, QString(), true),
            };
            for (int c = 0; c < cells.size(); ++c) {
                auto* item = new QTableWidgetItem(cells.at(c));
                item->setTextAlignment(Qt::AlignCenter);
                table_->setItem(i, c + 1, item);
            }
        }
    }

    void refreshTheme() {
        const auto& C = theme();
        setStyleSheet(QStringLiteral(
            "QWidget { background: transparent; color: %1;"
            " font-family: 'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;"
            " font-size: 13px; }"
            "#UserTimeWindowCard { background: %2; border-radius: %3px; border: 1px solid %4; }"
            "#UserTimeTitleBar { background: transparent; }"
            "#OvertimePageTitle { font-size: 20px; font-weight: 600; color: %1; }"
            "#UserTimeCloseBtn { background: transparent; border: none; color: %5;"
            " font-size: 13px; border-radius: 6px; }"
            "#UserTimeCloseBtn:hover { background: %6; color: #ffffff; }"
            "#UserTimeStatsTable { background: %7; color: %1; border: none;"
            " gridline-color: transparent; alternate-background-color: %8; }"
            "#UserTimeStatsTable::item { padding: 8px 18px; }"
            "#UserTimeStatsTable QHeaderView::section { background: %9; color: %5;"
            " border: none; border-bottom: 1px solid %4; padding: 10px 18px; font-weight: 600; }"
            "#UserTimeStatsTable QScrollBar:vertical { background: %8; width: 8px;"
            " margin: 6px 2px 6px 0; border-radius: 4px; }"
            "#UserTimeStatsTable QScrollBar::handle:vertical { background: %4;"
            " border-radius: 4px; min-height: 28px; }"
            "#UserTimeStatsTable QScrollBar::add-line:vertical,"
            "#UserTimeStatsTable QScrollBar::sub-line:vertical { height: 0; }"
        ).arg(C.text, C.bg, QString::number(kWinRadius), C.winEdge, C.textMuted, C.closeHover,
              C.card, C.hover, C.sidebar) + liveaio::util::popupChromeQss());
        hint_->setStyleSheet(QStringLiteral("color: %1; font-size: 12px; background: transparent;")
                                 .arg(C.textMuted));
    }

protected:
    void resizeEvent(QResizeEvent* event) override {
        QMainWindow::resizeEvent(event);
        QTimer::singleShot(0, this, [this]() { fitColumns(); });
    }

    void showEvent(QShowEvent* event) override {
        QMainWindow::showEvent(event);
        QTimer::singleShot(0, this, [this]() { fitColumns(); });
    }

    void mousePressEvent(QMouseEvent* event) override {
        if (event->button() == Qt::LeftButton) {
            const QRect barRect(titleBar_->mapTo(this, QPoint(0, 0)), titleBar_->size());
            if (barRect.contains(event->position().toPoint())) {
                dragging_ = true;
                dragPos_ = event->globalPosition().toPoint() - frameGeometry().topLeft();
            }
        }
        QMainWindow::mousePressEvent(event);
    }

    void mouseMoveEvent(QMouseEvent* event) override {
        if (dragging_ && (event->buttons() & Qt::LeftButton)) {
            move(event->globalPosition().toPoint() - dragPos_);
        }
        QMainWindow::mouseMoveEvent(event);
    }

    void mouseReleaseEvent(QMouseEvent* event) override {
        dragging_ = false;
        QMainWindow::mouseReleaseEvent(event);
    }

    void closeEvent(QCloseEvent* event) override {
        ledger_ = nullptr;
        event->ignore();
        hide();
    }

private:
    void buildUi(int contentMinW) {
        table_ = new QTableWidget(0, 4, this);
        table_->setObjectName(QStringLiteral("UserTimeStatsTable"));
        table_->setHorizontalHeaderLabels({QStringLiteral("抖音 ID"), QStringLiteral("增加"),
                                           QStringLiteral("减少"), QStringLiteral("总和")});
        table_->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Fixed);
        table_->setColumnWidth(0, colIdW_);
        for (int col = 1; col <= 3; ++col) {
            table_->horizontalHeader()->setSectionResizeMode(col, QHeaderView::Fixed);
            table_->setColumnWidth(col, colDataW_);
        }
        table_->verticalHeader()->setVisible(false);
        table_->setFrameShape(QFrame::NoFrame);
        table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
        table_->setSelectionMode(QAbstractItemView::NoSelection);
        table_->setFocusPolicy(Qt::NoFocus);
        table_->setShowGrid(false);
        table_->setAlternatingRowColors(true);
        table_->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        table_->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);
        table_->setMinimumWidth(contentMinW);

        auto* outer = new QWidget(this);
        auto* outerLay = new QVBoxLayout(outer);
        outerLay->setContentsMargins(kWinMargin, kWinMargin, kWinMargin, kWinMargin);
        outerLay->setSpacing(0);

        auto* card = new QFrame(outer);
        card->setObjectName(QStringLiteral("UserTimeWindowCard"));
        auto* shadow = new QGraphicsDropShadowEffect(card);
        shadow->setBlurRadius(32);
        shadow->setOffset(0, 4);
        shadow->setColor(QColor(0, 0, 0, 55));
        card->setGraphicsEffect(shadow);

        auto* lay = new QVBoxLayout(card);
        lay->setContentsMargins(16, 12, 16, 16);
        lay->setSpacing(12);

        titleBar_ = new QWidget(card);
        titleBar_->setObjectName(QStringLiteral("UserTimeTitleBar"));
        auto* tb = new QHBoxLayout(titleBar_);
        tb->setContentsMargins(0, 0, 0, 0);
        tb->setSpacing(8);
        auto* title = new QLabel(QStringLiteral("用户时长统计"), titleBar_);
        title->setObjectName(QStringLiteral("OvertimePageTitle"));
        auto* closeBtn = new QPushButton(QStringLiteral("✕"), titleBar_);
        closeBtn->setObjectName(QStringLiteral("UserTimeCloseBtn"));
        closeBtn->setFixedSize(32, 32);
        closeBtn->setCursor(Qt::PointingHandCursor);
        liveaio::util::suppressButtonFocus(closeBtn);
        QObject::connect(closeBtn, &QPushButton::clicked, this, [this]() { close(); });
        tb->addWidget(title);
        tb->addStretch();
        tb->addWidget(closeBtn);
        lay->addWidget(titleBar_);

        hint_ = new QLabel(card);
        hint_->setWordWrap(true);
        lay->addWidget(hint_);
        table_->setParent(card);
        lay->addWidget(table_, 1);

        outerLay->addWidget(card);
        setCentralWidget(outer);
    }

    // 不低于最小列宽的前提下横向铺满视口。
    void fitColumns() {
        const int viewportW = table_->viewport()->width();
        if (viewportW <= 0) return;
        const int minTotal = colIdW_ + colDataW_ * 3;
        if (viewportW <= minTotal) {
            table_->setColumnWidth(0, colIdW_);
            for (int col = 1; col <= 3; ++col) table_->setColumnWidth(col, colDataW_);
            return;
        }
        const int extra = viewportW - minTotal;
        const int idExtra = static_cast<int>(extra * 0.34);
        const int durExtra = (extra - idExtra) / 3;
        const int durRem = extra - idExtra - durExtra * 3;
        table_->setColumnWidth(0, colIdW_ + idExtra);
        for (int i = 0; i < 3; ++i) {
            table_->setColumnWidth(i + 1, colDataW_ + durExtra + (i < durRem ? 1 : 0));
        }
    }

    int colIdW_ = 0;
    int colDataW_ = 0;
    QTableWidget* table_ = nullptr;
    QLabel* hint_ = nullptr;
    QWidget* titleBar_ = nullptr;
    UserLedger* ledger_ = nullptr;
    bool dragging_ = false;
    QPoint dragPos_;
};

// ═══════════════════════════════════════════
// 悬浮窗：大组件按 cm 定尺，随窗口等比缩放
// ═══════════════════════════════════════════

static constexpr int kRefBlockW = 280;
static constexpr qreal kCmSlotH = 1.5;
static constexpr qreal kCmCustomH = 1.2;
static constexpr qreal kCmLogH = 1.2;
static constexpr qreal kCmRowGap = 0.3;
static constexpr qreal kCmTitleTimerGap = 0.1;
static constexpr qreal kCmGridGap = 0.25;
static constexpr qreal kCmColGap = 0.2;

static qreal screenDpi() {
    static qreal dpi = 0;
    if (dpi <= 0) {
        auto* screen = QApplication::primaryScreen();
        dpi = screen ? screen->logicalDotsPerInch() : 96.0;
    }
    return dpi;
}

static int cmToPx(qreal cm) {
    return std::max(1, static_cast<int>(std::lround(screenDpi() / 2.54 * cm)));
}

static int windowMarginPx() {
    static int margin = 0;
    if (margin <= 0) margin = cmToPx(0.5);
    return margin;
}

static int refTitleTimerGap() { return cmToPx(kCmTitleTimerGap); }
static int refSlotRowH() { return cmToPx(kCmSlotH); }
static int refCustomRowH() { return cmToPx(kCmCustomH); }
static int refLogRowH() { return cmToPx(kCmLogH); }
static int refRowGap() { return cmToPx(kCmRowGap); }
static int refGridGap() { return cmToPx(kCmGridGap); }
static int refColGap() { return cmToPx(kCmColGap); }
static int refBlockW() { return kRefBlockW; }
static int refSlotW() { return (refBlockW() - refColGap()) / 2; }

// 皮肤驱动的文字：字号按盒子二分拟合，绘制带描边阴影。
class SkinTextLabel final : public QLabel {
public:
    SkinTextLabel(const QString& role, const QString& text, bool wordWrap = false,
                  QWidget* parent = nullptr)
        : QLabel(text, parent), role_(role) {
        setAttribute(Qt::WA_TranslucentBackground);
        setWordWrap(wordWrap);
        skin_ = activeOvertimeSkin();
        px_ = skin_.roleStyle(kSurface, role_).pixelSize;
        setAlignment(skin_.roleStyle(kSurface, role_).qtAlign());
    }

    void refreshSkin() {
        skin_ = activeOvertimeSkin();
        update();
    }

    void fitToBox(const QString& sample, int w, int h, qreal scale) {
        px_ = skin_.fitRole(kSurface, role_, sample, std::max(1, w), std::max(1, h), scale);
        applyFont();
    }

    // 标题/倒计时：同样受各自角色 max_px×scale 约束，避免无上限撑大。
    void fitToBoxFill(const QString& sample, int w, int h, qreal scale) {
        px_ = skin_.fitRole(kSurface, role_, sample, std::max(1, w), std::max(1, h), scale);
        applyFont();
    }

    // 字号按各自皮肤角色的设计值×缩放，并受该角色自己的 max_px 封顶；
    // 只因横向放不下才回缩——不再用共用盒高把标题/倒计时/礼物绑死。
    void fitToDesign(const QString& sample, int maxW, qreal scale) {
        const auto st = skin_.roleStyle(kSurface, role_);
        const QString probe = sample.isEmpty()
            ? (st.fitRef.isEmpty() ? QStringLiteral("国") : st.fitRef)
            : sample;
        const qreal s = std::max(0.5, scale);
        int px = std::max(st.minPx, static_cast<int>(std::lround(st.pixelSize * s)));
        if (st.maxPx > 0) {
            px = std::min(px, std::max(st.minPx,
                                       static_cast<int>(std::lround(st.maxPx * s))));
        }
        while (px > st.minPx
               && QFontMetrics(st.font(px)).horizontalAdvance(probe) > std::max(1, maxW)) {
            --px;
        }
        px_ = px;
        applyFont();
    }

    // 当前字号下的字形紧框尺寸，供外层按实际墨迹定盒子。
    int inkHeight() const { return std::max(1, inkRect().height()); }
    int inkWidth() const { return std::max(1, inkRect().width()); }

    void setPixelSizeDirect(int px) {
        px_ = std::max(6, px);
        applyFont();
    }

protected:
    void paintEvent(QPaintEvent*) override {
        const auto style = skin_.roleStyle(kSurface, role_);
        QPainter p(this);
        p.setRenderHint(QPainter::TextAntialiasing, true);
        const QFont f = style.font(px_);
        p.setFont(f);
        const QColor shadow = skin_.color(QStringLiteral("text_shadow"), QColor(0, 0, 0, 160));
        const QColor fill = skin_.color(QStringLiteral("text_fill"), QColor(255, 255, 255));
        int flags = static_cast<int>(alignment());
        if (wordWrap() || text().isEmpty()) {
            if (wordWrap()) flags |= Qt::TextWordWrap;
            p.setPen(shadow);
            p.drawText(rect().translated(1, 1), flags, text());
            p.setPen(fill);
            p.drawText(rect(), flags, text());
            return;
        }

        // 字号是按字形紧框拟合的，若仍交给行盒排版，上下会被标签裁掉。
        // 单行文字直接按紧框定位，画多大就占多大。
        const QFontMetricsF fm(f);
        const QRectF ink = fm.tightBoundingRect(text());
        const QRectF box(0, 0, std::max(1, width() - 1), std::max(1, height() - 1));
        qreal x = box.left() - ink.left();
        if (flags & Qt::AlignHCenter) {
            x = box.center().x() - ink.width() / 2.0 - ink.left();
        } else if (flags & Qt::AlignRight) {
            x = box.right() - ink.right();
        }
        qreal baseline = box.center().y() + ink.height() / 2.0 - ink.bottom();
        if (flags & Qt::AlignTop) {
            baseline = box.top() - ink.top();
        } else if (flags & Qt::AlignBottom) {
            baseline = box.bottom() - ink.bottom();
        }
        const qreal minBase = box.top() - ink.top();
        const qreal maxBase = box.bottom() - ink.bottom();
        if (minBase <= maxBase) baseline = std::clamp(baseline, minBase, maxBase);
        else baseline = minBase;

        p.setPen(shadow);
        p.drawText(QPointF(x + 1.0, baseline + 1.0), text());
        p.setPen(fill);
        p.drawText(QPointF(x, baseline), text());
    }

private:
    static const QString kSurface;

    QRect inkRect() const {
        const QFontMetrics fm(skin_.roleStyle(kSurface, role_).font(px_));
        const QString sample = text().isEmpty() ? QStringLiteral("国") : text();
        return fm.tightBoundingRect(sample);
    }

    void applyFont() {
        QFont f = skin_.roleStyle(kSurface, role_).font(px_);
        setFont(f);
        update();
    }

    QString role_;
    ToolSkin skin_;
    int px_ = 13;
};

const QString SkinTextLabel::kSurface = QStringLiteral("overlay");

// 单个礼物格：左 icon，右上行名、下行规则标签。
class GiftSlotWidget final : public QFrame {
public:
    explicit GiftSlotWidget(const Rule& rule, QWidget* parent = nullptr) : QFrame(parent) {
        setAttribute(Qt::WA_TranslucentBackground);
        giftName_ = rule.gift;

        iconLbl_ = new QLabel(this);
        iconLbl_->setAlignment(Qt::AlignCenter);
        iconLbl_->setStyleSheet(QStringLiteral("background: transparent;"));
        nameLbl_ = new SkinTextLabel(QStringLiteral("gift_name"), giftName_, false, this);
        timeLbl_ = new SkinTextLabel(QStringLiteral("gift_time"), ruleSlotLabel(rule), false, this);

        auto* root = new QHBoxLayout(this);
        root->setContentsMargins(0, 0, 0, 0);
        root->setSpacing(4);
        root->addWidget(iconLbl_);
        auto* col = new QVBoxLayout;
        col->setContentsMargins(0, 0, 0, 0);
        col->setSpacing(1);
        col->addWidget(nameLbl_);
        col->addWidget(timeLbl_);
        root->addLayout(col, 1);

        applyScale(1.0, true);
    }

    void setRule(const QString& giftName, const QString& timeLabel) {
        giftName_ = giftName;
        nameLbl_->setText(giftName);
        timeLbl_->setText(timeLabel);
        loadedIconSide_ = -1;
        applyScale(scale_, true);
    }

    void reloadIcon() {
        const qreal s = std::max(0.5, scale_);
        const int slotH = static_cast<int>(std::lround(refSlotRowH() * s));
        const int iconSide = std::max(14, static_cast<int>(std::lround(slotH * 0.82)));
        if (loadedIconSide_ >= 0 && std::abs(iconSide - loadedIconSide_) < 2) return;
        liveaio::resources::setGiftIconOnLabel(iconLbl_, g_appRoot, giftName_, iconSide, false);
        loadedIconSide_ = iconSide;
    }

    void releaseIcon() {
        liveaio::resources::clearGiftIconOnLabel(iconLbl_);
        loadedIconSide_ = -1;
    }

    void refreshSkin() {
        nameLbl_->refreshSkin();
        timeLbl_->refreshSkin();
    }

    void applyScale(qreal scale, bool heavy) {
        const qreal s = std::max(0.5, scale);
        scale_ = s;
        const int slotW = static_cast<int>(std::lround(refSlotW() * s));
        const int slotH = static_cast<int>(std::lround(refSlotRowH() * s));
        const int iconSide = std::max(14, static_cast<int>(std::lround(slotH * 0.82)));
        setFixedSize(slotW, slotH);
        iconLbl_->setFixedSize(iconSide, iconSide);
        if (heavy) reloadIcon();
        const int textW = std::max(20, slotW - iconSide - int(std::lround(6 * s)));
        const int textH = std::max(8, (slotH - int(std::lround(2 * s))) / 2);
        // 字号始终按盒子拟合；heavy 只控制图标重载，避免 resize 路径把字钉死在 13px。
        nameLbl_->fitToBox(giftName_, textW, textH, s);
        timeLbl_->fitToBox(timeLbl_->text(), textW, textH, s);
    }

private:
    QString giftName_;
    QLabel* iconLbl_ = nullptr;
    SkinTextLabel* nameLbl_ = nullptr;
    SkinTextLabel* timeLbl_ = nullptr;
    qreal scale_ = 1.0;
    int loadedIconSide_ = -1;
};

// 底部日志行：左「谁送了什么」，右「±时长」。
class ResultRow final : public QWidget {
public:
    explicit ResultRow(QWidget* parent = nullptr) : QWidget(parent) {
        setAttribute(Qt::WA_TranslucentBackground);
        leftLbl_ = new SkinTextLabel(QStringLiteral("log_left"), QString(), false, this);
        rightLbl_ = new SkinTextLabel(QStringLiteral("log_right"), QString(), false, this);
        auto* lay = new QHBoxLayout(this);
        lay->setContentsMargins(0, 0, 0, 0);
        lay->setSpacing(6);
        lay->addWidget(leftLbl_, 1);
        lay->addWidget(rightLbl_, 0);
        applyScale(1.0, true);
    }

    void setEntry(const QString& left, int deltaSeconds) {
        leftLbl_->setText(left);
        rightLbl_->setText(deltaSeconds ? formatDeltaLog(deltaSeconds) : QString());
        applyScale(scale_, true);
    }

    void refreshSkin() {
        leftLbl_->refreshSkin();
        rightLbl_->refreshSkin();
    }

    void applyScale(qreal scale, bool /*heavy*/) {
        const qreal s = std::max(0.5, scale);
        scale_ = s;
        const int h = static_cast<int>(std::lround(refLogRowH() * s));
        const int w = static_cast<int>(std::lround(refBlockW() * s));
        setFixedHeight(h);
        const int pad = std::max(2, static_cast<int>(std::lround(2 * s)));
        const int textW = std::max(20, w / 2 - pad);
        leftLbl_->fitToBox(leftLbl_->text().isEmpty() ? QStringLiteral("占位")
                                                      : leftLbl_->text(),
                           textW, h - pad, s);
        rightLbl_->fitToBox(rightLbl_->text().isEmpty() ? QStringLiteral("+0秒")
                                                       : rightLbl_->text(),
                            textW, h - pad, s);
    }

private:
    SkinTextLabel* leftLbl_ = nullptr;
    SkinTextLabel* rightLbl_ = nullptr;
    qreal scale_ = 1.0;
};

// 倒计时：1x 为 4cm×2cm，随大组件同比缩放。
class TimerBox final : public QFrame {
public:
    explicit TimerBox(QWidget* parent = nullptr) : QFrame(parent) {
        setAttribute(Qt::WA_TranslucentBackground);
        lbl_ = new SkinTextLabel(QStringLiteral("timer"), formatTimerDisplay(0), false, this);
        lay_ = new QVBoxLayout(this);
        lay_->setContentsMargins(0, 0, 0, 0);
        lay_->addWidget(lbl_, 0, Qt::AlignHCenter | Qt::AlignTop);
    }

    void setTimerText(const QString& text) {
        lbl_->setText(text);
        update();
    }

    void refreshSkin() { lbl_->refreshSkin(); }

    // 组件优先：字号按皮肤设计值×缩放定，盒子再包住这行字，不再用框反过来压字号。
    // 用固定探针拟合，秒数跳动不会让盒子忽大忽小。
    void applyScale(qreal scale, bool heavy, int maxW) {
        const qreal s = std::max(0.5, scale);
        scale_ = s;
        lastHeavy_ = heavy;
        const int edge = std::max(2, static_cast<int>(std::lround(kTimerEdgePad * s)));
        const int innerW = std::max(1, maxW - edge * 2);
        lbl_->fitToDesign(kProbe, innerW, s);
        const int slack = std::max(2, static_cast<int>(std::lround(2 * s)));
        const int innerH = probeInkHeight() + slack;
        lay_->setContentsMargins(edge, edge, edge, edge);
        lbl_->setFixedSize(innerW, innerH);
        setFixedSize(innerW + edge * 2, innerH + edge * 2);
    }

private:
    static constexpr int kTimerEdgePad = 2;
    // 最长常见形态，作为字号与盒高的稳定基准。
    static const QString kProbe;

    int probeInkHeight() const {
        const QString keep = lbl_->text();
        lbl_->setText(kProbe);
        const int h = lbl_->inkHeight();
        lbl_->setText(keep);
        return h;
    }

    SkinTextLabel* lbl_ = nullptr;
    QVBoxLayout* lay_ = nullptr;
    qreal scale_ = 1.0;
    bool lastHeavy_ = true;
};

const QString TimerBox::kProbe = QStringLiteral("00:00:00");

// 标题（置底）+ 0.1cm + 倒计时（置顶）。
class TitleTimerSection final : public QWidget {
public:
    explicit TitleTimerSection(QWidget* parent = nullptr) : QWidget(parent) {
        setAttribute(Qt::WA_TranslucentBackground);

        titleWrap_ = new QWidget(this);
        titleWrap_->setAttribute(Qt::WA_TranslucentBackground);
        titleLbl_ = new SkinTextLabel(QStringLiteral("title"), QStringLiteral("距离下播时间"),
                                      false, titleWrap_);
        auto* tw = new QVBoxLayout(titleWrap_);
        tw->setContentsMargins(0, 0, 0, 0);
        tw->setSpacing(0);
        tw->addStretch(1);
        tw->addWidget(titleLbl_, 0, Qt::AlignHCenter | Qt::AlignBottom);

        timerBox_ = new TimerBox(this);

        lay_ = new QVBoxLayout(this);
        lay_->setContentsMargins(0, 0, 0, 0);
        lay_->setSpacing(refTitleTimerGap());
        lay_->addWidget(titleWrap_);
        lay_->addWidget(timerBox_, 0, Qt::AlignHCenter);
    }

    TimerBox* timerBox() const { return timerBox_; }

    void refreshSkin() {
        titleLbl_->refreshSkin();
        timerBox_->refreshSkin();
    }

    void applyScale(qreal scale, int blockW, int pad, bool heavy) {
        const qreal s = std::max(0.5, scale);
        const int sectionGap = std::max(1, static_cast<int>(std::lround(refTitleTimerGap() * s)));
        lay_->setSpacing(sectionGap);
        // 标题同样是组件优先：字号按皮肤设计值×缩放，行高再包住这行字。
        const int titlePad = std::max(2, static_cast<int>(std::lround(2 * s)));
        titleLbl_->fitToDesign(titleLbl_->text(), std::max(1, blockW - pad), s);
        const int titleRowH = titleLbl_->inkHeight() + titlePad;
        titleWrap_->setFixedHeight(titleRowH);
        titleLbl_->setFixedWidth(std::max(1, blockW - pad));
        timerBox_->applyScale(s, heavy, blockW - pad);
        setFixedHeight(titleRowH + sectionGap + timerBox_->height());
    }

private:
    QWidget* titleWrap_ = nullptr;
    SkinTextLabel* titleLbl_ = nullptr;
    TimerBox* timerBox_ = nullptr;
    QVBoxLayout* lay_ = nullptr;
};

// 加班机大组件：窗口尺寸只引用本组件。
class OvertimeBlock final : public QWidget {
public:
    explicit OvertimeBlock(QWidget* parent = nullptr) : QWidget(parent) {
        setAttribute(Qt::WA_TranslucentBackground);
        setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
        settings_ = loadSettings();
        skin_ = activeOvertimeSkin();

        rootLay_ = new QVBoxLayout(this);
        rootLay_->setContentsMargins(0, 0, 0, 0);
        rootLay_->setSpacing(refRowGap());

        titleTimer_ = new TitleTimerSection(this);
        rootLay_->addWidget(titleTimer_);

        gridWrap_ = new QWidget(this);
        gridWrap_->setAttribute(Qt::WA_TranslucentBackground);
        grid_ = new QGridLayout(gridWrap_);
        grid_->setContentsMargins(0, 0, 0, 0);
        grid_->setHorizontalSpacing(refColGap());
        grid_->setVerticalSpacing(refGridGap());
        for (int i = 0; i < settings_.rules.size(); ++i) {
            auto* slot = new GiftSlotWidget(settings_.rules.at(i), gridWrap_);
            slots_.append(slot);
            grid_->addWidget(slot, i / 2, i % 2, Qt::AlignLeft);
        }
        rootLay_->addWidget(gridWrap_);

        customLbl_ = new SkinTextLabel(QStringLiteral("custom"), settings_.customText, true, this);
        rootLay_->addWidget(customLbl_);

        resultRow_ = new ResultRow(this);
        rootLay_->addWidget(resultRow_);

        applyChromeMargins();
        applyScale(1.0, true);
    }

    void scheduleDeferredIconLoads() {
        for (int i = 0; i < slots_.size(); ++i) {
            QTimer::singleShot(40 * i, slots_[i], [slot = slots_[i]]() { slot->reloadIcon(); });
        }
    }

    void releaseHeavyResources() {
        for (auto* slot : slots_) slot->releaseIcon();
    }

    void setRemainingDisplay(int totalSec) {
        titleTimer_->timerBox()->setTimerText(formatTimerDisplay(totalSec));
    }

    void setGiftLog(const QString& left, int deltaSeconds) {
        resultRow_->setEntry(left, deltaSeconds);
    }

    void applySettings(const Settings& settings) {
        settings_ = settings;
        for (int i = 0; i < slots_.size() && i < settings_.rules.size(); ++i) {
            const Rule& rule = settings_.rules.at(i);
            slots_[i]->setRule(rule.gift, ruleSlotLabel(rule));
        }
        customLbl_->setText(settings_.customText);
        customLbl_->setAlignment(alignToQt(settings_.customAlign) | Qt::AlignTop);
        applyScale(scale_, true);
        scheduleDeferredIconLoads();
    }

    void refreshSkin() {
        skin_ = activeOvertimeSkin();
        titleTimer_->refreshSkin();
        for (auto* slot : slots_) slot->refreshSkin();
        customLbl_->refreshSkin();
        resultRow_->refreshSkin();
        applyChromeMargins();
        update();
    }

    QMargins chromeMargins() const { return rootLay_->contentsMargins(); }

    void applyChromeMargins() {
        if (!skin_.usesPixelChrome()) {
            chromeOuterMargins_ = {};
            rootLay_->setContentsMargins(0, 0, 0, 0);
            return;
        }
        const auto ch = skin_.chromeMeta();
        const auto deco = ch.value(QStringLiteral("deco")).toObject();
        const int outW = std::max(0, ch.value(QStringLiteral("out_w")).toInt(2));
        const int depth = std::max(0, ch.value(QStringLiteral("depth")).toInt(2));
        const int edge = outW + 2;
        const int logoOverY = deco.value(QStringLiteral("logo_over_y")).toInt(2);
        const auto logo = skin_.loadNamedStill(QStringLiteral("logo"));
        const auto busy = skin_.loadNamedStill(QStringLiteral("busy"));
        const auto corner = skin_.loadNamedStill(QStringLiteral("corner"));
        const auto winMin = skin_.loadNamedStill(QStringLiteral("win_min"));
        const int winH = std::max(18, winMin.logicalH);
        const int top = std::max({edge, logo.logicalH - logoOverY, winH});
        const int left = std::max(edge, int(std::lround(deco.value(QStringLiteral("busy_over_x")).toDouble(10))));
        const int right = std::max(corner.logicalW / 2, outW + depth);
        const int bot = std::max(corner.logicalH / 2, outW + depth);
        chromeOuterMargins_ = QMargins(left, top, right, bot);
        // 装饰占外侧空间，内容还需与框线保持安全距离；否则大字号的墨迹会贴框。
        const int contentPad = std::max(
            6, ch.value(QStringLiteral("content_pad")).toInt(10));
        rootLay_->setContentsMargins(left + contentPad, top + contentPad,
                                     right + contentPad, bot + contentPad);
        // 缓存装饰贴图供 paintEvent 使用
        otBusy_ = skin_.loadNamedAnim(QStringLiteral("busy"));
        otCorner_ = skin_.loadNamedAnim(QStringLiteral("corner"));
        otBusyFrame_ = 0;
        otCornerFrame_ = 0;
        otBusyElapsedMs_ = 0;
        otCornerElapsedMs_ = 0;
        otLogo_ = logo;
        otWinMin_ = winMin;
        otWinMax_ = skin_.loadNamedStill(QStringLiteral("win_max"));
        otWinClose_ = skin_.loadNamedStill(QStringLiteral("win_close"));
        if (!otChromeTimer_) {
            otChromeTimer_ = new QTimer(this);
            otChromeTimer_->setTimerType(Qt::PreciseTimer);
            QObject::connect(otChromeTimer_, &QTimer::timeout, this, [this]() {
                const int stepMs = std::max(1, otChromeTimer_->interval());
                auto advance = [stepMs](const liveaio::resources::LoadedAnim& anim,
                                        int& frame, int& elapsedMs) {
                    if (anim.frames.size() <= 1) return false;
                    elapsedMs += stepMs;
                    bool changed = false;
                    for (int guard = 0; guard < anim.frames.size(); ++guard) {
                        const int delay = std::max(30, anim.delays.value(frame, 100));
                        if (elapsedMs < delay) break;
                        elapsedMs -= delay;
                        frame = (frame + 1) % anim.frames.size();
                        changed = true;
                    }
                    return changed;
                };
                const bool dirty =
                    advance(otBusy_, otBusyFrame_, otBusyElapsedMs_)
                    | advance(otCorner_, otCornerFrame_, otCornerElapsedMs_);
                if (dirty) update();
            });
        }
        if (otBusy_.frames.size() > 1 || otCorner_.frames.size() > 1) {
            otChromeTimer_->start(30);
        } else {
            otChromeTimer_->stop();
        }
    }

    void applyScale(qreal scale, bool heavy) {
        const qreal s = std::max(0.5, scale);
        scale_ = s;
        rootLay_->setSpacing(std::max(1, static_cast<int>(std::lround(refRowGap() * s))));
        grid_->setHorizontalSpacing(std::max(1, static_cast<int>(std::lround(refColGap() * s))));
        grid_->setVerticalSpacing(std::max(1, static_cast<int>(std::lround(refGridGap() * s))));

        const int bw = static_cast<int>(std::lround(refBlockW() * s));
        const int pad = std::max(2, static_cast<int>(std::lround(2 * s)));
        titleTimer_->applyScale(s, bw, pad, heavy);

        const int gridH = static_cast<int>(
            std::lround((3 * refSlotRowH() + 2 * refGridGap()) * s));
        gridWrap_->setFixedHeight(gridH);
        for (auto* slot : slots_) slot->applyScale(s, heavy);

        const int customH = static_cast<int>(std::lround(refCustomRowH() * s));
        customLbl_->setFixedHeight(customH);
        customLbl_->fitToBox(customLbl_->text(), bw - pad, customH - pad, s);
        resultRow_->applyScale(s, heavy);

        // 块高由各行实际高度相加得出，再叠加像素框装饰边距。
        const int gap = rootLay_->spacing();
        const QMargins cm = rootLay_->contentsMargins();
        const int bodyH = titleTimer_->height() + gap + gridH + gap + customH
            + gap + resultRow_->height();
        setFixedSize(bw + cm.left() + cm.right(), bodyH + cm.top() + cm.bottom());
    }

protected:
    void paintEvent(QPaintEvent*) override {
        QPainter p(this);
        if (skin_.usesPixelChrome()) {
            p.setRenderHint(QPainter::Antialiasing, false);
            // 框体向内偏移，四周留给 Busy/Logo/窗口按钮/右下角装饰。
            // 旧实现把框画在 QWidget 外沿，所有负坐标装饰都会被控件自身裁掉。
            const QRect inner = contentsRect().adjusted(
                chromeOuterMargins_.left(), chromeOuterMargins_.top(),
                -chromeOuterMargins_.right(), -chromeOuterMargins_.bottom());
            skin_.paintPixelChrome(&p, inner, 1.0);
            auto blit = [&](const QPixmap& pm, const QRect& r) {
                if (pm.isNull() || r.isEmpty()) return;
                p.drawPixmap(r, pm);
            };
            const auto ch = skin_.chromeMeta();
            const auto deco = ch.value(QStringLiteral("deco")).toObject();
            const int busyOverX = std::max(1, int(std::lround(
                deco.value(QStringLiteral("busy_over_x")).toDouble(10))));
            const int logoOverY = deco.value(QStringLiteral("logo_over_y")).toInt(2);
            const int winGap = deco.value(QStringLiteral("win_gap")).toInt(2);
            const int busyW = std::max(1, otBusy_.logicalW);
            const int busyH = std::max(1, otBusy_.logicalH);
            const int logoW = std::max(1, otLogo_.logicalW);
            const int logoH = std::max(1, otLogo_.logicalH);
            const int cornerW = std::max(1, otCorner_.logicalW);
            const int cornerH = std::max(1, otCorner_.logicalH);
            const int winH = std::max({otWinMin_.logicalH, otWinMax_.logicalH, otWinClose_.logicalH, 18});
            const int winTotalW = otWinMin_.logicalW + otWinMax_.logicalW + otWinClose_.logicalW + winGap * 2;
            const QRect busyR(inner.x() - busyOverX,
                              std::max(0, inner.y() + logoOverY - logoH),
                              busyW, busyH);
            const QRect logoR(busyR.right() + 1, busyR.y(), logoW, logoH);
            QRect win0(inner.right() - winTotalW + 1,
                       inner.y() - winH + (winH - otWinMin_.logicalH),
                       otWinMin_.logicalW, otWinMin_.logicalH);
            QRect win1(win0.right() + 1 + winGap,
                       inner.y() - winH + (winH - otWinMax_.logicalH),
                       otWinMax_.logicalW, otWinMax_.logicalH);
            QRect win2(win1.right() + 1 + winGap,
                       inner.y() - winH + (winH - otWinClose_.logicalH),
                       otWinClose_.logicalW, otWinClose_.logicalH);
            const QRect cornerR(inner.right() - cornerW / 2,
                                inner.bottom() - cornerH / 2,
                                cornerW, cornerH);
            if (!otBusy_.frames.isEmpty()) {
                blit(otBusy_.frames.value(otBusyFrame_ % otBusy_.frames.size()), busyR);
            }
            blit(otLogo_.pixmap, logoR);
            blit(otWinMin_.pixmap, win0);
            blit(otWinMax_.pixmap, win1);
            blit(otWinClose_.pixmap, win2);
            if (!otCorner_.frames.isEmpty()) {
                blit(otCorner_.frames.value(otCornerFrame_ % otCorner_.frames.size()), cornerR);
            }
            return;
        }
        const QColor bg = skin_.color(QStringLiteral("block_bg"), QColor(0, 0, 0, 0));
        if (bg.alpha() == 0) return;
        p.setRenderHint(QPainter::Antialiasing, true);
        p.setPen(Qt::NoPen);
        p.setBrush(bg);
        const int radius = std::max(0, skin_.metrics().padH);
        p.drawRoundedRect(rect(), radius, radius);
    }

private:
    Settings settings_;
    ToolSkin skin_;
    QVBoxLayout* rootLay_ = nullptr;
    TitleTimerSection* titleTimer_ = nullptr;
    QWidget* gridWrap_ = nullptr;
    QGridLayout* grid_ = nullptr;
    QVector<GiftSlotWidget*> slots_;
    SkinTextLabel* customLbl_ = nullptr;
    ResultRow* resultRow_ = nullptr;
    qreal scale_ = 1.0;
    QMargins chromeOuterMargins_;
    liveaio::resources::LoadedAnim otBusy_;
    liveaio::resources::LoadedAnim otCorner_;
    liveaio::resources::LoadedStill otLogo_, otWinMin_, otWinMax_, otWinClose_;
    int otBusyFrame_ = 0;
    int otCornerFrame_ = 0;
    int otBusyElapsedMs_ = 0;
    int otCornerElapsedMs_ = 0;
    QTimer* otChromeTimer_ = nullptr;
};

class OvertimeRoot final : public RippleOverlayRoot {
public:
    OvertimeRoot(QMainWindow* win, std::function<void()> onResume)
        : RippleOverlayRoot(win), onResume_(std::move(onResume)) {
        block_ = new OvertimeBlock(content());
        referenceBlockSize_ = block_->size();
    }

    OvertimeBlock* block() const { return block_; }

    QSize preferredWindowSize() const {
        const int m = windowMarginPx();
        return QSize(referenceBlockSize_.width() + 2 * m,
                     kTopbarH + referenceBlockSize_.height() + 2 * m);
    }

    qreal preferredWindowAspect() const {
        const QSize s = preferredWindowSize();
        return qreal(s.width()) / std::max(1, s.height());
    }

    void refreshSkin() {
        block_->refreshSkin();
        block_->applyScale(1.0, true);
        referenceBlockSize_ = block_->size();
        normalizeHostAspect();
        layoutBlock(true);
    }

    void normalizeHostAspect() {
        const qreal aspect = preferredWindowAspect();
        const int w = std::max(hostWindow()->minimumWidth(), hostWindow()->width());
        const int h = std::max(hostWindow()->minimumHeight(),
                               static_cast<int>(std::lround(w / aspect)));
        if (std::abs(hostWindow()->height() - h) > 1) hostWindow()->resize(w, h);
    }

    // 大组件按 0.5cm 边距居中。字号只跟宽度走（各角色自己的 pixel_size/max_px），
    // 不再因高度不够二次缩小整块——那会把标题/倒计时/礼物再次绑死。
    void layoutBlock(bool heavy = true) {
        const int cw = content()->width();
        const int ch = content()->height();
        if (cw <= 0 || ch <= 0) return;
        const int m = windowMarginPx();
        const int availW = std::max(1, cw - 2 * m);
        const int availH = std::max(1, ch - 2 * m);
        // 皮肤装饰边距不随内容缩放；先从可用宽度扣掉，否则 Nemuru 框会越界，
        // 缩到最小时文字和礼物格就会被悬浮窗外框盖住。
        const QMargins chrome = block_->chromeMargins();
        const int bodyAvailW = std::max(1, availW - chrome.left() - chrome.right());
        const qreal s = std::max(0.5, qreal(bodyAvailW) / refBlockW());
        block_->applyScale(s, heavy);
        const int bw = block_->width();
        const int bh = block_->height();
        // 过高时垂直贴顶并略微上移居中余量，不回缩字号。
        const int y = m + std::max(0, (availH - bh) / 2);
        block_->move(m + (availW - bw) / 2, y);
    }

protected:
    void onContentGeometryChanged() override { layoutBlock(true); }
    void onContentGeometryWhileResizing() override { layoutBlock(false); }

    void onResizeResume() override {
        if (onResume_) onResume_();
    }

    // 整窗按初始比例缩放，避免大组件留白。
    QRect resizeGeometry(Edge edge, const QPoint& delta, const QRect& start) const override {
        const qreal aspect = preferredWindowAspect();
        const int minW = hostWindow()->minimumWidth();
        const int minH = hostWindow()->minimumHeight();
        QRect geo = start;
        int newW = 0;
        int newH = 0;
        if (edge == Edge::Right || edge == Edge::BottomRight) {
            newW = std::max(minW, start.width() + delta.x());
            newH = std::max(minH, static_cast<int>(std::lround(newW / aspect)));
            newW = static_cast<int>(std::lround(newH * aspect));
        } else if (edge == Edge::Left || edge == Edge::BottomLeft) {
            newW = std::max(minW, start.width() - delta.x());
            newH = std::max(minH, static_cast<int>(std::lround(newW / aspect)));
            newW = static_cast<int>(std::lround(newH * aspect));
            geo.setLeft(geo.right() - newW);
            geo.setBottom(geo.top() + newH);
            return geo;
        } else if (edge == Edge::Bottom) {
            newH = std::max(minH, start.height() + delta.y());
            newW = std::max(minW, static_cast<int>(std::lround(newH * aspect)));
            newH = static_cast<int>(std::lround(newW / aspect));
        } else {
            return start;
        }
        geo.setRight(geo.left() + newW);
        geo.setBottom(geo.top() + newH);
        return geo;
    }

private:
    OvertimeBlock* block_ = nullptr;
    QSize referenceBlockSize_;
    std::function<void()> onResume_;
};

static QJsonObject overtimeRuleToWire(const Rule& r) {
    const bool isRandom = r.mode == QStringLiteral("随机");
    QString mode = QStringLiteral("add");
    if (r.mode == QStringLiteral("减")) mode = QStringLiteral("sub");
    else if (isRandom) mode = QStringLiteral("random");
    const QString unit = isRandom ? r.randomUnit : r.unit;
    QString wireUnit = QStringLiteral("s");
    if (unit == QStringLiteral("时")) wireUnit = QStringLiteral("h");
    else if (unit == QStringLiteral("分")) wireUnit = QStringLiteral("m");
    return QJsonObject{
        {QStringLiteral("gift"), r.gift},
        {QStringLiteral("mode"), mode},
        {QStringLiteral("value"), r.value},
        {QStringLiteral("unit"), wireUnit},
        {QStringLiteral("min"), isRandom ? -r.randomNeg : 0},
        {QStringLiteral("max"), isRandom ? r.randomPos : 0},
    };
}

static void pushOvertimeSettingsToCore(const Settings& s) {
    if (!g_sendPacket) return;
    QJsonArray rules;
    for (const Rule& r : s.rules) rules.push_back(overtimeRuleToWire(r));
    g_sendPacket(QJsonObject{
        {QStringLiteral("op"), QStringLiteral("tool.overtime.set")},
        {QStringLiteral("settings"), QJsonObject{
            {QStringLiteral("hours"), s.hours},
            {QStringLiteral("minutes"), s.minutes},
            {QStringLiteral("seconds"), s.seconds},
            {QStringLiteral("rules"), rules},
        }},
    });
}

static void sendOvertimeCommand(const QString& cmd) {
    if (g_sendPacket) g_sendPacket(QJsonObject{
        {QStringLiteral("op"), QStringLiteral("tool.overtime.cmd")},
        {QStringLiteral("cmd"), cmd},
    });
}

class OvertimeOverlayController final : public QObject {
public:
    explicit OvertimeOverlayController(QObject* parent = nullptr) : QObject(parent) {}

    bool isMounted() const {
        return OverlayHostService::instance().isToolActive(OverlayToolId::Overtime);
    }

    UserLedger* userLedger() { return &ledger_; }

    void show(const Settings& settings, std::function<void()> onClosed) {
        auto& host = OverlayHostService::instance();
        root_ = nullptr;
        auto* shell = host.shell(OverlayToolId::Overtime);
        root_ = new OvertimeRoot(shell, [this]() { onFrameResumed(); });
        const QSize def = root_->preferredWindowSize();
        constexpr qreal kMinWindowScale = 0.90;
        const int minW = static_cast<int>(std::ceil(def.width() * kMinWindowScale));
        const int minH = static_cast<int>(std::ceil(def.height() * kMinWindowScale));
        host.show(OverlayToolId::Overtime, QStringLiteral("加班机"),
                  QStringLiteral("overtime_window_geometry"),
                  minW, minH,
                  def.width(), def.height(), root_, [this, onClosed]() {
                      unmount();
                      if (onClosed) onClosed();
                  });
        root_->normalizeHostAspect();
        applySettings(settings);
        if (root_) root_->layoutBlock(true);
    }

    void unmount() {
        ledger_.setOnChanged(nullptr);
        ledger_.clear();
        if (root_) root_->block()->releaseHeavyResources();
        liveaio::resources::releaseGiftPixmapCaches();
        root_ = nullptr;
        settings_ = Settings{};
        remaining_ = 0;
        lastLogLeft_.clear();
        lastLogDelta_ = 0;
    }

    void applySettings(const Settings& settings) {
        settings_ = settings;
        remaining_ = totalSeconds(settings.hours, settings.minutes, settings.seconds);
        if (!root_) return;
        root_->block()->applySettings(settings_);
        root_->block()->setRemainingDisplay(remaining_);
        root_->layoutBlock(true);
    }

    void applyTimeOnly(const Settings& settings) {
        settings_.hours = settings.hours;
        settings_.minutes = settings.minutes;
        settings_.seconds = settings.seconds;
        remaining_ = totalSeconds(settings_.hours, settings_.minutes, settings_.seconds);
        if (root_ && !root_->resizeFrozen()) root_->block()->setRemainingDisplay(remaining_);
    }

    void applyOtherOnly(const Settings& settings) {
        settings_.rules = settings.rules;
        settings_.customText = settings.customText;
        settings_.customAlign = settings.customAlign;
        if (root_) root_->block()->applySettings(settings_);
        if (root_) root_->layoutBlock(true);
    }

    void refreshSkin() {
        if (root_) root_->refreshSkin();
    }

    void applyCoreRemaining(int seconds) {
        remaining_ = std::max(0, seconds);
        if (!root_ || root_->resizeFrozen()) return;
        root_->block()->setRemainingDisplay(remaining_);
    }

    bool applyLedger(const QMap<QString, int>& net, const QString& simGift, int simCount) {
        const auto hot = ledger_.applySnapshot(net);
        if (hot.second == 0) return false;
        const QString gift = simGift.isEmpty() ? QStringLiteral("礼物") : simGift;
        lastLogLeft_ = giftLogLeft(hot.first, gift, std::max(1, simCount));
        lastLogDelta_ = hot.second;
        if (root_ && !root_->resizeFrozen()) {
            root_->block()->setGiftLog(lastLogLeft_, lastLogDelta_);
        }
        return true;
    }

private:
    void onFrameResumed() {
        if (!root_) return;
        if (auto* shell = OverlayHostService::instance().shell(OverlayToolId::Overtime)) {
            shell->syncRadiusAfterResize();
        }
        root_->block()->setRemainingDisplay(remaining_);
        if (!lastLogLeft_.isEmpty()) root_->block()->setGiftLog(lastLogLeft_, lastLogDelta_);
    }

    OvertimeRoot* root_ = nullptr;
    Settings settings_;
    UserLedger ledger_;
    int remaining_ = 0;
    QString lastLogLeft_;
    int lastLogDelta_ = 0;
};

class OvertimeToolRuntime final : public ToolRuntimeBase {
public:
    explicit OvertimeToolRuntime(QObject* parent, std::function<void()> tryRelease)
        : ToolRuntimeBase(parent), tryRelease_(std::move(tryRelease)) {}

    QString toolId() const override { return QStringLiteral("overtime"); }

    bool isOverlayActive() const override {
        return overlayCtrl_ && overlayCtrl_->isMounted();
    }

    void setLedgerSyncCallback(std::function<void()> cb) { ledgerSync_ = std::move(cb); }

    void setPendingSim(const QString& gift, int count) {
        pendingSimGift_ = gift;
        pendingSimCount_ = count;
    }

    UserLedger* activeLedger() {
        if (overlayCtrl_ && overlayCtrl_->isMounted()) return overlayCtrl_->userLedger();
        return nullptr;
    }

    void onCorePacket(const QJsonObject& packet) override {
        const QString op = packet.value(QStringLiteral("op")).toString();
        if (op == QStringLiteral("tick")) {
            if (overlayCtrl_ && overlayCtrl_->isMounted()) {
                overlayCtrl_->applyCoreRemaining(
                    packet.value(QStringLiteral("remaining_seconds")).toInt());
            }
            return;
        }
        if (op != QStringLiteral("ledger")) return;
        if (!overlayCtrl_ || !overlayCtrl_->isMounted()) return;
        QMap<QString, int> net;
        for (const QJsonValue& item : packet.value(QStringLiteral("entries")).toArray()) {
            const QJsonObject entry = item.toObject();
            QString key = entry.value(QStringLiteral("user_id")).toString();
            if (key.isEmpty()) key = entry.value(QStringLiteral("user")).toString();
            if (key.isEmpty()) continue;
            net.insert(key, entry.value(QStringLiteral("seconds")).toInt());
        }
        if (net.isEmpty()) {
            overlayCtrl_->userLedger()->clear();
            if (ledgerSync_) ledgerSync_();
            return;
        }
        if (overlayCtrl_->applyLedger(net, pendingSimGift_, pendingSimCount_)) {
            pendingSimGift_.clear();
            pendingSimCount_ = 0;
        }
        if (ledgerSync_) ledgerSync_();
    }

    void toggleOverlay(const Settings& settings, std::function<void()> onClosed) {
        auto& host = OverlayHostService::instance();
        if (host.isToolActive(OverlayToolId::Overtime)) {
            host.teardown(OverlayToolId::Overtime);
            return;
        }
        pushOvertimeSettingsToCore(settings);
        sendOvertimeCommand(QStringLiteral("clear_ledger"));
        sendOvertimeCommand(QStringLiteral("reset"));
        if (!overlayCtrl_) overlayCtrl_ = new OvertimeOverlayController(this);
        overlayCtrl_->userLedger()->setOnChanged([this]() {
            if (ledgerSync_) ledgerSync_();
        });
        overlayCtrl_->show(settings, [this, onClosed]() {
            if (overlayCtrl_) overlayCtrl_->userLedger()->setOnChanged(nullptr);
            sendOvertimeCommand(QStringLiteral("pause"));
            if (onClosed) onClosed();
            if (tryRelease_) tryRelease_();
        });
    }

    void refreshOverlaySkin() {
        if (overlayCtrl_) overlayCtrl_->refreshSkin();
    }

    void applyTimeOnly(const Settings& settings) {
        if (overlayCtrl_) overlayCtrl_->applyTimeOnly(settings);
    }

    void applyOtherOnly(const Settings& settings) {
        if (overlayCtrl_) overlayCtrl_->applyOtherOnly(settings);
    }

private:
    OvertimeOverlayController* overlayCtrl_ = nullptr;
    std::function<void()> tryRelease_;
    std::function<void()> ledgerSync_;
    QString pendingSimGift_;
    int pendingSimCount_ = 0;
};

// ═══════════════════════════════════════════
// 控制面板
// ═══════════════════════════════════════════

class OvertimeToolWindow final : public ToolWindowBase {
public:
    explicit OvertimeToolWindow(CoreClient* core, OvertimeToolRuntime* runtime)
        : ToolWindowBase(core), runtime_(runtime) {
        setWindowTitle(QStringLiteral("设置"));
        setFixedSize(kToolWinW, kToolWinH);
        build();
        setStyleSheet(panelQss());
        refreshTheme();
        liveaio::util::onThemeChange(this, [this](const QString&) { refreshTheme(); });
        pushSettings(loadSettings());
        if (runtime_) {
            runtime_->setLedgerSyncCallback([this]() { syncUserTimeLedger(); });
        }
    }

    QString toolId() const override { return QStringLiteral("overtime"); }

    void onCorePacket(const QJsonObject&) override {}

    void onPanelClosing() override {
        hideSessionGiftPicker();
        if (userTimeWin_) {
            userTimeWin_->hide();
            userTimeWin_->deleteLater();
            userTimeWin_ = nullptr;
        }
    }

    void refreshTheme() override {
        setStyleSheet(panelQss());
        navigate(curNav_);
        refreshOpenBtn();
        styleUserTimeBtn();
        styleTutorialBtn();
        if (skinCombo_) skinCombo_->refreshTheme();
        if (simWidget_) simWidget_->refreshTheme();
        if (settingsPanel_) settingsPanel_->refreshTheme();
        if (userTimeWin_) userTimeWin_->refreshTheme();
    }

    void applyChromeStyle() override { setStyleSheet(panelQss()); }

private:
    static QString panelQss() {
        const auto& C = theme();
        return QStringLiteral(
            "#OvertimeRoot { background: %1; color: %2;"
            " font-family: 'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;"
            " font-size: 13px; }"
            "QWidget#OvertimeContent, QWidget#OvertimeNavBar, QFrame#OvertimeCard,"
            " QWidget#OvertimeSection, QWidget#OvertimeSectionBody,"
            " QWidget#OvertimeSimGift, QWidget#OvertimeSimLeft,"
            " QFrame#OvertimeGiftModule { color: %2; }"
            "#OvertimeNavBar { background: %3; border-bottom: 1px solid %4; }"
            "#OvertimeNavBtn { background: transparent; border: none;"
            " border-bottom: 2px solid transparent; padding: 0 16px; color: %5;"
            " font-size: 13px; }"
            "#OvertimeNavBtn:hover { background: %6; color: %2; }"
            "#OvertimeNavBtn[active=\"true\"] { background: transparent; color: %2;"
            " font-weight: 600; border-bottom: 2px solid %7; }"
            "#OvertimeContent { background: %1; border: none; }"
            "#OvertimeCard { background: %8; border-radius: 10px; border: 1px solid %4; }"
            "#OvertimeSection { background: transparent; border: none; }"
            "#OvertimeSectionTitle { background: transparent; color: %2; font-size: 13px;"
            " font-weight: 600; padding: 4px 0; }"
            "#OvertimeSectionBody { background: transparent; border: none; }"
            "#OvertimeSimGift, #OvertimeSimLeft { background: transparent; border: none; }"
            "#OvertimeSimGiftIcon { background: transparent; border: none; }"
            "#OvertimeGiftModule { background: %1; border: 1px solid %4; border-radius: 4px; }"
            "#OvertimeGiftModule > QWidget { background: transparent; border: none; }"
            "#OvertimePageTitle { font-size: 20px; font-weight: 600; color: %2; }"
            "QLabel { background: transparent; }"
            "QScrollBar:vertical { background: transparent; width: 4px; }"
            "QScrollBar::handle:vertical { background: %4; border-radius: 2px; min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
            "QLineEdit, QTextEdit { background: %8; color: %2; border: 1px solid %4;"
            " border-radius: 6px; padding: 4px 8px; font-size: 13px; }"
            "QLineEdit:focus, QTextEdit:focus { border-color: %7; }"
        ).arg(C.bg, C.text, C.sidebar, C.border, C.textMuted, C.hover, C.activeLine, C.card)
            + liveaio::util::popupChromeQss();
    }

    static QLabel* pageTitle(const QString& text) {
        auto* lbl = new QLabel(text);
        lbl->setObjectName(QStringLiteral("OvertimePageTitle"));
        lbl->setAlignment(Qt::AlignHCenter);
        return lbl;
    }

    static QFrame* makeCard() {
        auto* card = new QFrame;
        card->setObjectName(QStringLiteral("OvertimeCard"));
        card->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Preferred);
        auto* lay = new QVBoxLayout(card);
        lay->setContentsMargins(20, 16, 20, 16);
        lay->setSpacing(14);
        return card;
    }

    static QLabel* cardTitle(const QString& text) {
        auto* lbl = new QLabel(text);
        lbl->setStyleSheet(QStringLiteral("font-size: 14px; font-weight: 600;"));
        return lbl;
    }

    QLabel* cardDesc(const QString& text) {
        auto* lbl = new QLabel(text);
        lbl->setWordWrap(true);
        descLabels_.append(lbl);
        return lbl;
    }

    void build() {
        auto* root = new QWidget(this);
        root->setObjectName(QStringLiteral("OvertimeRoot"));
        setCentralWidget(root);
        auto* mainLay = new QVBoxLayout(root);
        mainLay->setContentsMargins(0, 0, 0, 0);
        mainLay->setSpacing(0);

        auto* topbar = new QWidget(root);
        topbar->setObjectName(QStringLiteral("OvertimeNavBar"));
        topbar->setFixedHeight(46);
        auto* tb = new QHBoxLayout(topbar);
        tb->setContentsMargins(8, 0, 8, 0);
        tb->setSpacing(0);

        stack_ = new QStackedWidget(root);
        const QStringList tabs = {QStringLiteral("设置"), QStringLiteral("加班机")};
        for (int i = 0; i < tabs.size(); ++i) {
            auto* btn = new QPushButton(tabs.at(i), topbar);
            btn->setObjectName(QStringLiteral("OvertimeNavBtn"));
            btn->setFixedHeight(46);
            btn->setCursor(Qt::PointingHandCursor);
            btn->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
            liveaio::util::suppressButtonFocus(btn);
            QObject::connect(btn, &QPushButton::clicked, this, [this, i]() { navigate(i); });
            navBtns_.append(btn);
            tb->addWidget(btn);
        }
        tb->addStretch();

        stack_->addWidget(buildPage([this](QVBoxLayout* lay) { buildGeneralPanel(lay); }));
        overtimePlaceholder_ = new QWidget;
        stack_->addWidget(overtimePlaceholder_);

        mainLay->addWidget(topbar);
        mainLay->addWidget(stack_);
        navigate(0);
    }

    void ensureOvertimeTab() {
        if (overtimeTabBuilt_) return;
        overtimeTabBuilt_ = true;
        const int idx = stack_->indexOf(overtimePlaceholder_);
        auto* page = buildPage([this](QVBoxLayout* lay) { buildOvertimePanel(lay); });
        stack_->removeWidget(overtimePlaceholder_);
        overtimePlaceholder_->deleteLater();
        overtimePlaceholder_ = nullptr;
        stack_->insertWidget(idx, page);
    }

    QWidget* buildPage(const std::function<void(QVBoxLayout*)>& fill) {
        auto* inner = new QWidget;
        auto* lay = new QVBoxLayout(inner);
        lay->setContentsMargins(kPagePad, 20, kPagePad, 20);
        lay->setSpacing(16);
        fill(lay);
        auto* scroll = new QScrollArea;
        scroll->setObjectName(QStringLiteral("OvertimeContent"));
        scroll->setWidgetResizable(true);
        scroll->setFrameShape(QFrame::NoFrame);
        scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        scroll->setWidget(inner);
        return scroll;
    }

    void buildGeneralPanel(QVBoxLayout* lay) {
        lay->addWidget(pageTitle(QStringLiteral("设置")));

        auto* card = makeCard();
        auto* cl = qobject_cast<QVBoxLayout*>(card->layout());
        auto* row = new QHBoxLayout;
        row->addWidget(cardTitle(QStringLiteral("悬浮加班窗")));
        row->addStretch();
        tutorialBtn_ = new liveaio::util::ChromeButton(
            QStringLiteral("教程"), card, liveaio::util::kControlH,
            liveaio::util::ControlVariant::Neutral);
        tutorialBtn_->setPadX(14);
        QObject::connect(tutorialBtn_, &QPushButton::clicked, this, [this]() {
            showTutorialDialog(this);
        });
        row->addWidget(tutorialBtn_);
        row->addSpacing(8);
        openBtn_ = new liveaio::util::ChromeButton(
            QStringLiteral("打开加班机"), card, liveaio::util::kControlH);
        QObject::connect(openBtn_, &QPushButton::clicked, this, [this]() { toggleOverlay(); });
        row->addWidget(openBtn_);
        cl->addLayout(row);
        cl->addWidget(cardDesc(
            QStringLiteral("透明悬浮窗，叠加在直播软件上方显示加班倒计时。"
                           "窗口采集请在直播伴侣素材里开启透明背景。")));
        lay->addWidget(card);

        auto* themeCard = makeCard();
        auto* tcl = qobject_cast<QVBoxLayout*>(themeCard->layout());
        auto* trow = new QHBoxLayout;
        trow->setSpacing(12);
        trow->addWidget(cardTitle(QStringLiteral("加班外观主题")));
        trow->addStretch();
        skinCombo_ = new ThemedComboBox(themeCard);
        QStringList skinNames;
        const auto skins = liveaio::resources::listSkins(g_appRoot, QStringLiteral("overtime"));
        for (const auto& entry : skins) {
            skinNameToId_.insert(entry.name, entry.id);
            skinNames << entry.name;
        }
        skinCombo_->addItems(skinNames);
        const QString activeId = configValue(
            liveaio::resources::skinConfigKey(QStringLiteral("overtime")),
            QStringLiteral("default")).toString();
        for (const auto& entry : skins) {
            if (entry.id == activeId) skinCombo_->setCurrentText(entry.name);
        }
        skinCombo_->setFixedHeight(liveaio::util::kControlH);
        skinCombo_->setMinimumWidth(160);
        skinCombo_->setOnChange([this](const QString& name) {
            writeConfigValue(liveaio::resources::skinConfigKey(QStringLiteral("overtime")),
                             skinNameToId_.value(name, QStringLiteral("default")));
            if (runtime_) runtime_->refreshOverlaySkin();
        });
        trow->addWidget(skinCombo_);
        tcl->addLayout(trow);
        lay->addWidget(themeCard);

        auto* simCard = makeCard();
        auto* scl = qobject_cast<QVBoxLayout*>(simCard->layout());
        scl->addWidget(cardTitle(QStringLiteral("模拟送礼")));
        simWidget_ = new SimGiftWidget(simCard);
        simWidget_->setOnPush([this](const QString& gift, int count) { pushSimGift(gift, count); });
        scl->addWidget(simWidget_);
        scl->addWidget(cardDesc(
            QStringLiteral("向已打开的加班机推送模拟礼物，用于本地测试规则与倒计时")));
        lay->addWidget(simCard);

        auto* utCard = makeCard();
        auto* utl = qobject_cast<QVBoxLayout*>(utCard->layout());
        utl->addWidget(cardTitle(QStringLiteral("用户时长统计")));
        auto* utRow = new QHBoxLayout;
        userTimeBtn_ = new liveaio::util::ChromeButton(
            QStringLiteral("查看用户时长统计"), utCard, liveaio::util::kControlH);
        QObject::connect(userTimeBtn_, &QPushButton::clicked, this, [this]() {
            openUserTimeWindow();
        });
        utRow->addWidget(userTimeBtn_);
        utRow->addStretch();
        utl->addLayout(utRow);
        utl->addWidget(cardDesc(
            QStringLiteral("统计本次加班机运行期间，各用户礼物带来的加减时长")));
        lay->addWidget(utCard);
        lay->addStretch();
    }

    void buildOvertimePanel(QVBoxLayout* lay) {
        lay->addWidget(pageTitle(QStringLiteral("加班机")));
        auto* center = new QHBoxLayout;
        center->addStretch(1);
        settingsPanel_ = new SettingsPanel;
        settingsPanel_->setOnSaved([this](const Settings& s) { pushSettings(s); });
        settingsPanel_->setOnTimeApply([this](const Settings& s) {
            if (runtime_) runtime_->applyTimeOnly(s);
        });
        settingsPanel_->setOnOtherApply([this](const Settings& s) {
            if (runtime_) runtime_->applyOtherOnly(s);
        });
        center->addWidget(settingsPanel_);
        center->addStretch(1);
        lay->addLayout(center);
    }

    void navigate(int index) {
        if (index == 1) ensureOvertimeTab();
        curNav_ = index;
        stack_->setCurrentIndex(index);
        for (int i = 0; i < navBtns_.size(); ++i) {
            navBtns_[i]->setProperty("active", i == index);
            navBtns_[i]->style()->unpolish(navBtns_[i]);
            navBtns_[i]->style()->polish(navBtns_[i]);
        }
    }

    void sendCommand(const QString& cmd) {
        sendCore(QJsonObject{
            {QStringLiteral("op"), QStringLiteral("tool.overtime.cmd")},
            {QStringLiteral("cmd"), cmd},
        });
    }

    void pushSimGift(const QString& gift, int count) {
        if (runtime_) runtime_->setPendingSim(gift, count);
        sendCore(QJsonObject{
            {QStringLiteral("op"), QStringLiteral("tool.overtime.sim_gift")},
            {QStringLiteral("gift"), gift},
            {QStringLiteral("count"), count},
            {QStringLiteral("user"), QStringLiteral("LiveAIO")},
        });
    }

    // core.Rule 用 add/sub/random 与 s/m/h，随机区间为 [min, max]。
    static QJsonObject ruleToWire(const Rule& r) {
        const bool isRandom = r.mode == QStringLiteral("随机");
        QString mode = QStringLiteral("add");
        if (r.mode == QStringLiteral("减")) mode = QStringLiteral("sub");
        else if (isRandom) mode = QStringLiteral("random");
        const QString unit = isRandom ? r.randomUnit : r.unit;
        QString wireUnit = QStringLiteral("s");
        if (unit == QStringLiteral("时")) wireUnit = QStringLiteral("h");
        else if (unit == QStringLiteral("分")) wireUnit = QStringLiteral("m");
        return QJsonObject{
            {QStringLiteral("gift"), r.gift},
            {QStringLiteral("mode"), mode},
            {QStringLiteral("value"), r.value},
            {QStringLiteral("unit"), wireUnit},
            {QStringLiteral("min"), isRandom ? -r.randomNeg : 0},
            {QStringLiteral("max"), isRandom ? r.randomPos : 0},
        };
    }

    void pushSettings(const Settings& s) {
        QJsonArray rules;
        for (const Rule& r : s.rules) rules.push_back(ruleToWire(r));
        sendCore(QJsonObject{
            {QStringLiteral("op"), QStringLiteral("tool.overtime.set")},
            {QStringLiteral("settings"), QJsonObject{
                {QStringLiteral("hours"), s.hours},
                {QStringLiteral("minutes"), s.minutes},
                {QStringLiteral("seconds"), s.seconds},
                {QStringLiteral("rules"), rules},
            }},
        });
    }

    void toggleOverlay() {
        if (!runtime_) return;
        if (!OverlayHostService::instance().isToolActive(OverlayToolId::Overtime)) {
            publishToolDemand(QStringLiteral("overtime"), true);
        }
        const Settings s = loadSettings();
        runtime_->toggleOverlay(s, [this]() {
            refreshOpenBtn();
            syncUserTimeLedger();
        });
        refreshOpenBtn();
        syncUserTimeLedger();
    }

    UserLedger* activeLedger() const {
        return runtime_ ? runtime_->activeLedger() : nullptr;
    }

    void syncUserTimeLedger() {
        if (userTimeWin_ && userTimeWin_->isVisible()) {
            userTimeWin_->bindLedger(activeLedger());
        }
    }

    void openUserTimeWindow() {
        if (!userTimeWin_) userTimeWin_ = new UserTimeWindow(this);
        userTimeWin_->bindLedger(activeLedger());
        userTimeWin_->show();
        userTimeWin_->raise();
        userTimeWin_->activateWindow();
    }

    void refreshOpenBtn() {
        const bool isOpen = OverlayHostService::instance().isToolActive(OverlayToolId::Overtime);
        if (simWidget_) simWidget_->setPushEnabled(isOpen);
        if (!openBtn_) return;
        openBtn_->setText(isOpen ? QStringLiteral("关闭加班机") : QStringLiteral("打开加班机"));
        openBtn_->setVariant(isOpen ? liveaio::util::ControlVariant::Danger
                                    : liveaio::util::ControlVariant::Outlined);
        openBtn_->update();
    }

    void styleUserTimeBtn() {
        if (userTimeBtn_) userTimeBtn_->update();
    }

    void styleTutorialBtn() {
        const auto& C = theme();
        if (tutorialBtn_) tutorialBtn_->update();
        for (auto* lbl : descLabels_) {
            lbl->setStyleSheet(QStringLiteral("font-size: 12px; color: %1;").arg(C.textMuted));
        }
    }

    QStackedWidget* stack_ = nullptr;
    QWidget* overtimePlaceholder_ = nullptr;
    bool overtimeTabBuilt_ = false;
    QVector<QPushButton*> navBtns_;
    QVector<QLabel*> descLabels_;
    int curNav_ = 0;
    liveaio::util::ChromeButton* openBtn_ = nullptr;
    liveaio::util::ChromeButton* tutorialBtn_ = nullptr;
    liveaio::util::ChromeButton* userTimeBtn_ = nullptr;
    ThemedComboBox* skinCombo_ = nullptr;
    QMap<QString, QString> skinNameToId_;
    SimGiftWidget* simWidget_ = nullptr;
    SettingsPanel* settingsPanel_ = nullptr;
    OvertimeToolRuntime* runtime_ = nullptr;
    UserTimeWindow* userTimeWin_ = nullptr;
};

}  // namespace ot

static ToolRuntimeBase* createOvertimeRuntime(QObject* parent, std::function<void()> tryRelease) {
    return new ot::OvertimeToolRuntime(parent, std::move(tryRelease));
}

static ToolWindowBase* createOvertimeTool(CoreClient* core, ot::OvertimeToolRuntime* runtime) {
    return new ot::OvertimeToolWindow(core, runtime);
}

}  // namespace liveaio::tools
