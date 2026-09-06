// util/widgets.cpp — 共享主题与主题联动控件库（pages 与 tools 共用同一份实现）。
//
// 视觉基线为旧 PySide 版本的 util/theme.py + util/widgets.py + pages/main_page.py。
// 单 TU 编译：pages_main.cpp / tools_main.cpp 直接 include 本文件。
#ifndef LIVEAIO_UTIL_WIDGETS_CPP
#define LIVEAIO_UTIL_WIDGETS_CPP

#include <QAbstractAnimation>
#include <QApplication>
#include <QChildEvent>
#include <QCursor>
#include <QEasingCurve>
#include <QEvent>
#include <QFile>
#include <QFontMetrics>
#include <QFrame>
#include <QHBoxLayout>
#include <QLabel>
#include <QLineEdit>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QPen>
#include <QPointer>
#include <QPropertyAnimation>
#include <QPushButton>
#include <QRectF>
#include <QScrollArea>
#include <QShowEvent>
#include <QSignalBlocker>
#include <QSizePolicy>
#include <QSpinBox>
#include <QStyle>
#include <QSurfaceFormat>
#include <QTimer>
#include <QVBoxLayout>
#include <QVariant>
#include <QFutureWatcher>
#include <QJsonDocument>
#include <QJsonObject>
#include <QVariantAnimation>
#include <QVector>
#include <QWidget>
#include <QtConcurrent>
#include <QtGlobal>

#include <algorithm>
#include <cmath>
#include <functional>
#include <memory>

#if defined(QT_OPENGLWIDGETS_LIB)
#include <QOpenGLWidget>
#define LIVEAIO_HAS_OPENGLWIDGET 1
#endif

namespace liveaio::util {

// ─────────────────────────────────────────────
// 主题：旧 util/theme.py 的四套精确色值
// ─────────────────────────────────────────────
struct ThemePalette {
    QString bg;
    QString sidebar;
    QString card;
    QString hover;
    QString active;
    QString activeLine;
    QString text;
    QString textMuted;
    QString border;
    QString winEdge;
    QString closeHover;
    QString btnHover;
};

inline const QVector<QPair<QString, ThemePalette>>& themeCatalog() {
    static const QVector<QPair<QString, ThemePalette>> catalog = {
        {QStringLiteral("拿铁奶咖"), ThemePalette{
             QStringLiteral("#eff1f5"), QStringLiteral("#e6e9ef"), QStringLiteral("#ffffff"),
             QStringLiteral("#dce0e8"), QStringLiteral("#ccd0da"), QStringLiteral("#1e66f5"),
             QStringLiteral("#4c4f69"), QStringLiteral("#5c5f77"), QStringLiteral("#ccd0da"),
             QStringLiteral("#bcc0cc"), QStringLiteral("#d20f39"), QStringLiteral("#ccd0da")}},
        {QStringLiteral("深焙摩卡"), ThemePalette{
             QStringLiteral("#1e1e2e"), QStringLiteral("#181825"), QStringLiteral("#313244"),
             QStringLiteral("#45475a"), QStringLiteral("#585b70"), QStringLiteral("#89b4fa"),
             QStringLiteral("#cdd6f4"), QStringLiteral("#a6adc8"), QStringLiteral("#313244"),
             QStringLiteral("#11111b"), QStringLiteral("#f38ba8"), QStringLiteral("#45475a")}},
        {QStringLiteral("极夜深蓝"), ThemePalette{
             QStringLiteral("#2e3440"), QStringLiteral("#252b35"), QStringLiteral("#3b4252"),
             QStringLiteral("#434c5e"), QStringLiteral("#4c566a"), QStringLiteral("#88c0d0"),
             QStringLiteral("#eceff4"), QStringLiteral("#d8dee9"), QStringLiteral("#434c5e"),
             QStringLiteral("#1d2430"), QStringLiteral("#bf616a"), QStringLiteral("#434c5e")}},
        {QStringLiteral("日晷护眼"), ThemePalette{
             QStringLiteral("#fdf6e3"), QStringLiteral("#eee8d5"), QStringLiteral("#ffffff"),
             QStringLiteral("#e5dfc8"), QStringLiteral("#d9d2b5"), QStringLiteral("#268bd2"),
             QStringLiteral("#586e75"), QStringLiteral("#657b83"), QStringLiteral("#e2dcc8"),
             QStringLiteral("#d9d2b5"), QStringLiteral("#dc322f"), QStringLiteral("#e2dcc8")}},
    };
    return catalog;
}

inline QStringList themeNames() {
    QStringList out;
    for (const auto& entry : themeCatalog()) out.append(entry.first);
    return out;
}

inline const QString& defaultThemeName() {
    static const QString name = QStringLiteral("拿铁奶咖");
    return name;
}

// 旧配置里可能存的是 light/dark，迁移到对应主题。
inline QString normalizeThemeName(const QString& name) {
    const QString trimmed = name.trimmed();
    if (trimmed.compare(QStringLiteral("light"), Qt::CaseInsensitive) == 0) {
        return QStringLiteral("拿铁奶咖");
    }
    if (trimmed.compare(QStringLiteral("dark"), Qt::CaseInsensitive) == 0) {
        return QStringLiteral("深焙摩卡");
    }
    for (const auto& entry : themeCatalog()) {
        if (entry.first == trimmed) return trimmed;
    }
    return defaultThemeName();
}

inline const ThemePalette& paletteByName(const QString& name) {
    const QString resolved = normalizeThemeName(name);
    for (const auto& entry : themeCatalog()) {
        if (entry.first == resolved) return entry.second;
    }
    return themeCatalog().first().second;
}

struct ThemeState {
    QString name = defaultThemeName();
    ThemePalette palette = themeCatalog().first().second;
    std::function<void(const QString&)> persist;
    QVector<QPair<QPointer<QObject>, std::function<void(const QString&)>>> callbacks;
};

inline ThemeState& themeState() {
    static ThemeState state;
    return state;
}

inline const ThemePalette& theme() { return themeState().palette; }
inline QString currentThemeName() { return themeState().name; }

// 主题持久化交给宿主：pages 走 core config.set，tools 走本地 config.json。
inline void setThemePersistHook(std::function<void(const QString&)> hook) {
    themeState().persist = std::move(hook);
}

// ctx 为空时回调会被丢弃，避免窗口销毁后回调野指针。
inline void onThemeChange(QObject* ctx, std::function<void(const QString&)> cb) {
    if (!ctx || !cb) return;
    themeState().callbacks.append({QPointer<QObject>(ctx), std::move(cb)});
}

inline void notifyThemeChanged() {
    auto& list = themeState().callbacks;
    const QString name = themeState().name;
    for (int i = list.size() - 1; i >= 0; --i) {
        if (list[i].first.isNull()) {
            list.removeAt(i);
            continue;
        }
        list[i].second(name);
    }
}

// 只切换当前主题并广播，不写配置（用于从 core 同步下来的值）。
inline void applyThemeName(const QString& name) {
    const QString resolved = normalizeThemeName(name);
    themeState().name = resolved;
    themeState().palette = paletteByName(resolved);
    notifyThemeChanged();
}

// 用户主动切换：写配置 + 广播。
inline void setTheme(const QString& name) {
    const QString resolved = normalizeThemeName(name);
    if (resolved == themeState().name) return;
    themeState().name = resolved;
    themeState().palette = paletteByName(resolved);
    if (themeState().persist) themeState().persist(resolved);
    notifyThemeChanged();
}

// ─────────────────────────────────────────────
// 配置读写：由宿主注入，控件层不直接碰 core / 文件
// ─────────────────────────────────────────────
struct ConfigAccess {
    std::function<QVariant(const QString&, const QVariant&)> get;
    std::function<void(const QString&, const QVariant&)> set;
};

inline ConfigAccess& configAccess() {
    static ConfigAccess access;
    return access;
}

inline void setConfigAccessors(std::function<QVariant(const QString&, const QVariant&)> get,
                              std::function<void(const QString&, const QVariant&)> set) {
    configAccess().get = std::move(get);
    configAccess().set = std::move(set);
}

inline QVariant configGet(const QString& key, const QVariant& fallback = {}) {
    if (configAccess().get) return configAccess().get(key, fallback);
    return fallback;
}

inline void configSet(const QString& key, const QVariant& value) {
    if (configAccess().set) configAccess().set(key, value);
}

// ─────────────────────────────────────────────
// QSS 片段：对应旧 theme.py 的 qss_* 辅助
// ─────────────────────────────────────────────
// 统一控件高度口径。Qt QSS 的 height/min-height 指「内容盒」，不含描边与竖向内边距；
// 直接写 34px，1.5px 描边的按钮实际就是 37px：既比 1px 描边的输入框高，
// 又会把固定高度外壳（如下拉框）的下边框顶出去裁掉。
// 所以统一用本函数把「外形高」换算成内容高，组件要多高就是多高。
inline constexpr int kControlH = 34;
// 行内紧凑控件（开关钮、小数字框）唯一的第二档高度，别再另立新值。
inline constexpr int kControlHSmall = 28;
// 描边宽度必须是整数像素：1.5px 会被 Qt 按上取整/下取整拆成上 1px、下 2px，
// 也就是「上边薄下边厚」的根因。所有厚描边控件统一走这个常量。
inline constexpr int kControlBorderW = 2;
inline constexpr int kControlRadius = 8;
inline constexpr int kControlPadX = 16;

inline QString qssBoxHeight(int outerH, qreal borderPx, int vPaddingPx = 0) {
    const int chrome = static_cast<int>(borderPx * 2.0 + 0.5) + vPaddingPx * 2;
    const QString content = QString::number(std::max(0, outerH - chrome));
    return QStringLiteral("height: %1px; min-height: %1px; max-height: %1px;").arg(content);
}

inline QString qssOutlined(int h = kControlH, int padX = kControlPadX) {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QPushButton { background: %1; color: %2; border: %5px solid %2;"
        " border-radius: %6px; font-size: 13px; font-weight: 600;"
        " %3 padding: 0 %7px; }"
        "QPushButton:hover { background: %4; border: %5px solid %2; }"
    ).arg(C.card, C.activeLine, qssBoxHeight(h, kControlBorderW), C.hover)
        .arg(kControlBorderW).arg(kControlRadius).arg(padX);
}

// 次要操作（删除规则、手动掉落等）：灰边灰字，悬停提亮。
inline QString qssNeutral(int h = kControlH, int padX = kControlPadX) {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QPushButton { background: %1; color: %2; border: %6px solid %3;"
        " border-radius: %7px; font-size: 13px; font-weight: 600;"
        " %4 padding: 0 %8px; }"
        "QPushButton:hover { background: %5; color: %9; border: %6px solid %3; }"
    ).arg(C.card, C.textMuted, C.border, qssBoxHeight(h, kControlBorderW), C.hover)
        .arg(kControlBorderW).arg(kControlRadius).arg(padX).arg(C.text);
}

// 描边钮的不可用态：保留同样的外形高与描边宽，只是全部降为边框灰。
inline QString qssOutlinedDim(int h = kControlH, int padX = kControlPadX) {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QPushButton { background: %1; color: %2; border: %4px solid %2;"
        " border-radius: %5px; font-size: 13px; font-weight: 600; %3 padding: 0 %6px; }"
    ).arg(C.card, C.border, qssBoxHeight(h, kControlBorderW))
        .arg(kControlBorderW).arg(kControlRadius).arg(padX);
}

// 与 qssLineEdit 同高同边框：并排输入框+按钮时用这个。
inline QString qssOutlinedBesideEdit(int h = kControlH) {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QPushButton { background: %1; color: %2; border: 1px solid %2;"
        " border-radius: 6px; font-size: 13px; font-weight: 600;"
        " %3 padding: 0 14px; }"
        "QPushButton:hover { background: %4; border: 1px solid %2; }"
    ).arg(C.card, C.activeLine, qssBoxHeight(h, 1.0), C.hover);
}

// 填充类按钮同样留出与描边按钮相同的外形高：无描边时用透明描边占位，
// 这样一排里描边钮与填充钮的外形高、圆角完全一致。
inline QString qssDisabled(int h = kControlH, int padX = kControlPadX) {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QPushButton { background: %1; color: %2; border: %4px solid transparent;"
        " border-radius: %5px; font-size: 13px; %3 padding: 0 %6px; }"
    ).arg(C.border, C.textMuted, qssBoxHeight(h, kControlBorderW))
        .arg(kControlBorderW).arg(kControlRadius).arg(padX);
}

inline QString qssSuccess(int h = kControlH, int padX = kControlPadX) {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QPushButton { background: %1; color: #ffffff; border: %4px solid transparent;"
        " border-radius: %5px; font-size: 13px; font-weight: 600; %2 padding: 0 %6px; }"
        "QPushButton:hover { background: %3; }"
        "QPushButton:disabled { background: %7; color: %8; }"
    ).arg(C.activeLine, qssBoxHeight(h, kControlBorderW), C.active)
        .arg(kControlBorderW).arg(kControlRadius).arg(padX).arg(C.border, C.textMuted);
}

inline QString qssDanger(int h = kControlH, int padX = kControlPadX) {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QPushButton { background: %1; color: #ffffff; border: %4px solid transparent;"
        " border-radius: %5px; font-size: 13px; font-weight: 600; %2 padding: 0 %6px; }"
        "QPushButton:hover { background: %3; }"
        "QPushButton:disabled { background: %7; color: %8; }"
    ).arg(C.closeHover, qssBoxHeight(h, kControlBorderW), C.active)
        .arg(kControlBorderW).arg(kControlRadius).arg(padX).arg(C.border, C.textMuted);
}

inline QString qssBack() {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QPushButton { background: transparent; color: %1; border: none;"
        " border-radius: 6px; font-size: 13px; padding: 4px 8px; }"
        "QPushButton:hover { background: %3; color: %2; }"
    ).arg(C.textMuted, C.activeLine, C.hover);
}

inline QString qssMutedLabel(int size = 13) {
    return QStringLiteral("background: transparent; font-size: %1px; color: %2;")
        .arg(QString::number(size), theme().textMuted);
}

inline QString qssErrorLabel(int size = 13) {
    return QStringLiteral("background: transparent; font-size: %1px; color: %2;")
        .arg(QString::number(size), theme().closeHover);
}

inline QString qssAccentLabel(int size = 13) {
    return QStringLiteral("background: transparent; font-size: %1px; color: %2;")
        .arg(QString::number(size), theme().activeLine);
}

inline QString qssLineEdit(int h = kControlH) {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QLineEdit { background: %1; color: %2; border: 1px solid %3; border-radius: 6px;"
        " padding: 0 10px; font-size: 13px; %5 }"
        "QLineEdit:focus { border-color: %4; }"
    ).arg(C.card, C.text, C.border, C.activeLine, qssBoxHeight(h, 1.0));
}

// 原生 QToolTip 在透明/无边框窗上易渲染成黑块，各窗 QSS 应统一带上。
inline QString qssTooltip() {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QToolTip { color: %1; background-color: %2; border: 1px solid %3;"
        " border-radius: 6px; padding: 6px 10px; font-size: 12px; }"
    ).arg(C.text, C.card, C.border);
}

// 抑制 Windows 下 flat 按钮的原生 hover/focus 黑框。
inline QString qssNativeButtonGuard() {
    return QStringLiteral(
        "QPushButton { outline: none; }"
        "QPushButton:focus { outline: none; }"
        "QPushButton:flat { background: transparent; border: none; }"
        "QPushButton:flat:hover { background: transparent; border: none; }"
        "QPushButton:flat:pressed { background: transparent; border: none; }"
    );
}

inline QString popupChromeQss() { return qssTooltip() + qssNativeButtonGuard(); }

inline void suppressButtonFocus(QPushButton* btn) {
    if (!btn) return;
    btn->setFocusPolicy(Qt::NoFocus);
    btn->setAutoDefault(false);
    btn->setDefault(false);
}

// 控件外观档位：一排控件共用同一外形高与圆角，只有配色/描边不同。
enum class ControlVariant {
    Outlined,  // 描边 + 强调色文字（主操作按钮）
    Field,     // 描边 + 正文色文字（下拉框、只读展示）
    Neutral,   // 灰描边 + 次要文字（删除、教程等次要操作）
    Accent,    // 强调色填充
    Danger,    // 危险色填充
    Muted,     // 灰底不可用观感
};

// 画一圈厚度绝对均匀的描边 + 底色。
// 为什么不能直接 drawRoundedRect + QPen：屏幕缩放 125% 时逻辑 34px 高的控件是
// 42.5 个设备像素，上下两条边落在不同的半像素位置，覆盖率不同，看起来就是
// 「上边比下边淡」。这里先把坐标换算到设备像素、对齐到窗口像素网格，
// 再用「外圈填描边色 + 内圈填底色」的方式画环，环宽恒等于整数设备像素。
inline void paintChromeBox(QPainter& p, const QWidget* w,
                          const QColor& border, const QColor& fill,
                          int borderPx = kControlBorderW, int radiusPx = kControlRadius) {
    if (!w || w->width() <= 0 || w->height() <= 0) return;
    const qreal dpr = w->devicePixelRatioF();
    const QPointF originDev = QPointF(w->mapTo(w->window(), QPoint(0, 0))) * dpr;
    // 往控件内部取下一条网格线：向外取会让外圈落在控件之外被裁掉，
    // 表现就是左边/上边的描边比另外两边薄。
    const qreal shiftX = std::ceil(originDev.x()) - originDev.x();
    const qreal shiftY = std::ceil(originDev.y()) - originDev.y();
    const int bwDev = std::max(1, static_cast<int>(std::lround(borderPx * dpr)));
    const int wDev = static_cast<int>(std::floor(w->width() * dpr - shiftX));
    const int hDev = static_cast<int>(std::floor(w->height() * dpr - shiftY));
    if (wDev <= bwDev * 2 || hDev <= bwDev * 2) return;
    const qreal radiusDev = radiusPx * dpr;

    p.save();
    p.setRenderHint(QPainter::Antialiasing, true);
    p.scale(1.0 / dpr, 1.0 / dpr);   // 之后 1 个单位 = 1 个设备像素
    p.translate(shiftX, shiftY);     // 控件原点对齐到设备像素网格（向内）
    p.setPen(Qt::NoPen);
    p.setBrush(border);
    p.drawRoundedRect(QRectF(0, 0, wDev, hDev), radiusDev, radiusDev);
    if (fill != border) {
        const qreal innerR = std::max(0.0, radiusDev - bwDev);
        p.setBrush(fill);
        p.drawRoundedRect(QRectF(bwDev, bwDev, wDev - bwDev * 2, hDev - bwDev * 2),
                          innerR, innerR);
    }
    p.restore();
}

// 标准控件：边框、圆角、外形高、悬停全部自绘。
// 自绘的三个原因：
//   1. QSS 的小数描边会被拆成上 1px / 下 2px，厚度不均匀；
//   2. QSS 固定高度算的是内容盒，描边会被外壳裁掉（下拉框「下底边没了」）；
//   3. 屏幕缩放非整数时 QSS 描边四边覆盖率不同，会一边深一边淡。
class ChromeButton : public QPushButton {
public:
    explicit ChromeButton(const QString& text, QWidget* parent = nullptr,
                          int h = kControlH, ControlVariant variant = ControlVariant::Outlined)
        : QPushButton(text, parent), outerH_(h), variant_(variant) {
        suppressButtonFocus(this);
        setCursor(Qt::PointingHandCursor);
        setFlat(true);
        setFixedHeight(outerH_);
        setMouseTracking(true);
        setAttribute(Qt::WA_Hover, true);
        setAttribute(Qt::WA_StyledBackground, false);
        setProperty("liveaioChromeButton", true);
        // 自绘不走 QSS 盒模型；这份透明样式只为压掉父级/系统 QSS 的描边与背景。
        setStyleSheet(QStringLiteral(
            "QPushButton { background: transparent; border: none; outline: none;"
            " padding: 0; margin: 0; min-height: 0; max-height: none; }"
            "QPushButton:hover { background: transparent; border: none; }"
            "QPushButton:pressed { background: transparent; border: none; }"
            "QPushButton:disabled { background: transparent; border: none; }"));
        onThemeChange(this, [this](const QString&) { update(); });
    }

    void setVariant(ControlVariant v) {
        if (variant_ == v) return;
        variant_ = v;
        update();
    }
    ControlVariant variant() const { return variant_; }

    void setHoverLit(bool on) {
        if (hoverLit_ == on) return;
        hoverLit_ = on;
        update();
    }
    bool hoverLit() const { return hoverLit_; }

    void setOuterHeight(int h) {
        if (outerH_ == h) return;
        outerH_ = h;
        setFixedHeight(h);
        updateGeometry();
        update();
    }

    // 左对齐 + 尾部箭头：下拉框触发钮用。
    void setTextAlignment(Qt::Alignment align) {
        align_ = align;
        update();
    }
    void setTrailingArrow(bool on) {
        if (arrow_ == on) return;
        arrow_ = on;
        updateGeometry();
        update();
    }
    void setPadX(int px) {
        padX_ = std::max(0, px);
        updateGeometry();
        update();
    }

    QSize sizeHint() const override {
        const int textW = fontMetrics().horizontalAdvance(text());
        const int extra = arrow_ ? kArrowBox : 0;
        return QSize(textW + padX_ * 2 + extra + kControlBorderW * 2, outerH_);
    }
    QSize minimumSizeHint() const override { return sizeHint(); }

protected:
    bool event(QEvent* e) override {
        switch (e->type()) {
        case QEvent::Enter:
        case QEvent::HoverEnter:
            setHoverLit(true);
            break;
        case QEvent::Leave:
        case QEvent::HoverLeave:
            setHoverLit(false);
            break;
        default:
            break;
        }
        return QPushButton::event(e);
    }

    void paintEvent(QPaintEvent*) override {
        const ThemePalette& C = theme();
        const bool on = isEnabled();
        const bool lit = on && (hoverLit_ || testAttribute(Qt::WA_UnderMouse));
        const bool down = on && isDown();

        QColor fill, border, ink;
        switch (on ? variant_ : ControlVariant::Muted) {
        case ControlVariant::Outlined:
            fill = QColor(lit ? C.hover : C.card);
            border = QColor(C.activeLine);
            ink = QColor(C.activeLine);
            break;
        case ControlVariant::Field:
            fill = QColor(lit ? C.hover : C.card);
            border = QColor(C.activeLine);
            ink = QColor(C.text);
            break;
        case ControlVariant::Neutral:
            fill = QColor(lit ? C.hover : C.card);
            border = QColor(C.border);
            ink = QColor(lit ? C.text : C.textMuted);
            break;
        case ControlVariant::Accent:
            fill = QColor(lit ? C.active : C.activeLine);
            border = fill;
            ink = QColor(Qt::white);
            break;
        case ControlVariant::Danger:
            fill = QColor(lit ? C.active : C.closeHover);
            border = fill;
            ink = QColor(Qt::white);
            break;
        case ControlVariant::Muted:
            fill = QColor(C.border);
            border = fill;
            ink = QColor(C.textMuted);
            break;
        }
        if (down) fill = fill.darker(108);

        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing, true);
        paintChromeBox(p, this, border, fill);

        QFont f = font();
        f.setPixelSize(13);
        f.setWeight(variant_ == ControlVariant::Field ? QFont::Medium : QFont::DemiBold);
        p.setFont(f);
        p.setPen(ink);

        QRect textRect = rect().adjusted(padX_, 0, -padX_, 0);
        if (arrow_) {
            const QRect arrowRect(textRect.right() - kArrowBox + 1, textRect.top(),
                                  kArrowBox, textRect.height());
            textRect.setRight(arrowRect.left() - 4);
            drawArrow(p, arrowRect, ink);
        }
        const QString shown =
            p.fontMetrics().elidedText(text(), Qt::ElideRight, textRect.width());
        p.drawText(textRect, align_ | Qt::AlignVCenter, shown);
    }

private:
    static constexpr int kArrowBox = 14;

    static void drawArrow(QPainter& p, const QRect& box, const QColor& ink) {
        const QPointF c(box.center().x() + 0.5, box.center().y() + 1.0);
        QPolygonF tri;
        tri << QPointF(c.x() - 4.0, c.y() - 2.5) << QPointF(c.x() + 4.0, c.y() - 2.5)
            << QPointF(c.x(), c.y() + 2.5);
        p.save();
        p.setPen(Qt::NoPen);
        QColor a = ink;
        a.setAlpha(200);
        p.setBrush(a);
        p.drawPolygon(tri);
        p.restore();
    }

    int outerH_ = kControlH;
    ControlVariant variant_ = ControlVariant::Outlined;
    Qt::Alignment align_ = Qt::AlignHCenter;
    int padX_ = kControlPadX;
    bool arrow_ = false;
    bool hoverLit_ = false;
};

// 旧名保留：工具页「打开」等已按描边钮使用。
using OutlinedButton = ChromeButton;

// 给普通 QPushButton 套标准描边样式（外形高 = h，描边不裁切）。
inline void applyOutlinedButton(QPushButton* btn, int h = kControlH) {
    if (!btn) return;
    suppressButtonFocus(btn);
    btn->setCursor(Qt::PointingHandCursor);
    btn->setFixedHeight(h);
    btn->setStyleSheet(qssOutlined(h));
}

// QSS 的 :hover 认的是 WA_UnderMouse；直接改这个属性，全仓已有的 :hover 规则都能生效。
inline void forceHoverState(QWidget* w, bool on) {
    if (!w) return;
    // ChromeButton 无 Q_OBJECT，qobject_cast 不可用；用属性标记。
    if (w->property("liveaioChromeButton").toBool()) {
        static_cast<ChromeButton*>(w)->setHoverLit(on);
        return;
    }
    const bool cur = w->testAttribute(Qt::WA_UnderMouse);
    if (cur == on) return;
    w->setAttribute(Qt::WA_UnderMouse, on);
    if (w->style()) {
        w->style()->unpolish(w);
        w->style()->polish(w);
    }
    w->update();
}

// 由父容器代理判定：容器拿到 hover/move 后，按真实光标坐标决定按钮悬停态。
// 按钮自身 Enter 被上方标签或兄弟控件截走时（「从上方划入」），这条路径仍然有效。
// 同一 target 可挂多个 host（如 card + scroll viewport）。
inline void wireHoverProxy(QWidget* host, QWidget* target) {
    if (!host || !target) return;
    const QByteArray pairKey =
        QByteArrayLiteral("liveaioHoverProxy_") + QByteArray::number(quintptr(host));
    if (target->property(pairKey).toBool()) return;
    target->setProperty(pairKey, true);
    host->setAttribute(Qt::WA_Hover, true);
    host->setMouseTracking(true);
    target->setAttribute(Qt::WA_Hover, true);
    target->setMouseTracking(true);
    class HoverProxyFilter final : public QObject {
    public:
        HoverProxyFilter(QWidget* host, QWidget* target)
            : QObject(host), host_(host), target_(target) {}
    protected:
        bool eventFilter(QObject*, QEvent* e) override {
            switch (e->type()) {
            case QEvent::HoverEnter:
            case QEvent::HoverMove:
            case QEvent::HoverLeave:
            case QEvent::Enter:
            case QEvent::Leave:
            case QEvent::MouseMove:
                sync();
                break;
            default:
                break;
            }
            return false;
        }
    private:
        // 一律按当前光标全局位置重算：scroll / 透明标签穿透时 mapTo(host) 易偏。
        void sync() {
            if (!host_ || !target_) return;
            if (!target_->isVisible() || !target_->isEnabled()) {
                forceHoverState(target_, false);
                return;
            }
            const QRect globalRect(target_->mapToGlobal(QPoint(0, 0)), target_->size());
            const bool hit = globalRect.contains(QCursor::pos());
            forceHoverState(target_, hit);
        }
        QPointer<QWidget> host_;
        QPointer<QWidget> target_;
    };
    auto* filter = new HoverProxyFilter(host, target);
    host->installEventFilter(filter);
    // 按钮自身 Enter/Leave 也同步，避免只靠父容器时从上方划入丢态。
    target->installEventFilter(filter);
}

// 一个 host（通常是 scroll viewport）批量代理多个目标悬停。
inline void wireHoverBatch(QWidget* host, const QVector<QWidget*>& targets) {
    if (!host || targets.isEmpty()) return;
    host->setAttribute(Qt::WA_Hover, true);
    host->setMouseTracking(true);
    class HoverBatchFilter final : public QObject {
    public:
        HoverBatchFilter(QWidget* host, QVector<QPointer<QWidget>> targets)
            : QObject(host), targets_(std::move(targets)) {}
    protected:
        bool eventFilter(QObject*, QEvent* e) override {
            switch (e->type()) {
            case QEvent::HoverEnter:
            case QEvent::HoverMove:
            case QEvent::HoverLeave:
            case QEvent::Enter:
            case QEvent::Leave:
            case QEvent::MouseMove:
                sync();
                break;
            default:
                break;
            }
            return false;
        }
    private:
        void sync() {
            const QPoint gp = QCursor::pos();
            for (const QPointer<QWidget>& t : targets_) {
                if (!t) continue;
                if (!t->isVisible() || !t->isEnabled()) {
                    forceHoverState(t, false);
                    continue;
                }
                const QRect globalRect(t->mapToGlobal(QPoint(0, 0)), t->size());
                forceHoverState(t, globalRect.contains(gp));
            }
        }
        QVector<QPointer<QWidget>> targets_;
    };
    QVector<QPointer<QWidget>> ptrs;
    ptrs.reserve(targets.size());
    for (QWidget* t : targets) {
        if (!t) continue;
        t->setAttribute(Qt::WA_Hover, true);
        t->setMouseTracking(true);
        ptrs.append(t);
    }
    host->installEventFilter(new HoverBatchFilter(host, std::move(ptrs)));
}

// 给整棵子树里的按钮统一接上代理判定，并跟踪后续新建控件（懒加载 Tab / 动态卡片）。
inline void installHoverAutoWiring(QWidget* root) {
    if (!root) return;
    class HoverAutoWirer final : public QObject {
    public:
        explicit HoverAutoWirer(QWidget* root) : QObject(root) { attach(root); }
    protected:
        bool eventFilter(QObject*, QEvent* e) override {
            if (e->type() != QEvent::ChildAdded) return false;
            // ChildAdded 时派生类构造还没跑完，qobject_cast 会失手，延后一轮再接。
            QPointer<QObject> child(static_cast<QChildEvent*>(e)->child());
            QTimer::singleShot(0, this, [this, child]() {
                if (auto* w = qobject_cast<QWidget*>(child.data())) attach(w);
            });
            return false;
        }
    private:
        void attach(QWidget* w) {
            if (!w || w->property("liveaioHoverWired").toBool()) return;
            w->setProperty("liveaioHoverWired", true);
            w->installEventFilter(this);
            if (qobject_cast<QPushButton*>(w)) {
                if (QWidget* host = w->parentWidget()) wireHoverProxy(host, w);
            }
            for (QObject* child : w->children()) {
                if (auto* cw = qobject_cast<QWidget*>(child)) attach(cw);
            }
        }
    };
    new HoverAutoWirer(root);
}

// 自绘顶栏/悬浮窗图标钮：关焦点并禁止系统 hover 底纹。
inline void polishFlatChromeButton(QPushButton* btn) {
    if (!btn) return;
    suppressButtonFocus(btn);
    btn->setFlat(true);
    btn->setStyleSheet(QStringLiteral(
        "QPushButton { background: transparent; border: none; outline: none;"
        " padding: 0; margin: 0; }"
        "QPushButton:hover { background: transparent; border: none; }"
        "QPushButton:pressed { background: transparent; border: none; }"));
}

// 主窗口壳几何常量（圆角半径）。
inline constexpr int kWindowShadowMargin = 0;
inline constexpr int kWindowCornerRadius = 10;

// 主窗口壳 QSS：旧 main_page.build_qss + settings_page.build_setting_qss。
inline QString shellQss() {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QWidget { background: transparent; color: %1;"
        " font-family: 'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;"
        " font-size: 14px; outline: none; }"
        "#WindowCard { background: %2; border-radius: %12px; border: 1px solid %3; }"
        "#TitleBar { background: transparent; border-radius: %12px %12px 0 0; }"
        "#AppTitle { color: %5; font-size: 12px; background: transparent; }"
        "#WinBtn { background: transparent; border: none; border-radius: 0px; color: %5;"
        " font-size: 13px; min-width: 46px; max-width: 46px;"
        " min-height: 36px; max-height: 36px; }"
        "#WinBtn:hover { background: %6; color: %1; border-radius: 0px; }"
        "#Sidebar { background: transparent; }"
        "#SidebarDivider { background: %3; min-width: 1px; max-width: 1px; }"
        "#ToggleBtn, #NavBtn, #SettingsBtn { background: transparent; border: none;"
        " border-radius: 0px; text-align: left; color: %5; }"
        "#ToggleBtn:hover { background: %8; color: %1; }"
        "#NavBtn:hover, #SettingsBtn:hover { background: %8; color: %1; }"
        "#NavBtn[active=\"true\"], #SettingsBtn[active=\"true\"] { background: %9; color: %1; }"
        "#ScrollContainer { background: transparent; }"
        "QScrollArea { background: transparent; border: none; }"
        "QScrollBar:vertical { background: transparent; width: 4px; }"
        "QScrollBar::handle:vertical { background: %4; border-radius: 2px; min-height: 20px; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        "#ContentArea { background: transparent; }"
        "#Card { background: %10; border-radius: 10px; border: 1px solid %4; }"
        "#PageTitle { font-size: 22px; font-weight: 600; color: %1; background: transparent; }"
        "#PageSubtitle { font-size: 13px; color: %5; background: transparent; }"
        "#SettingsSidebar { background: %7; border-bottom: 1px solid %4; }"
        "#SettingNavBtn { background: transparent; border: none;"
        " border-bottom: 2px solid transparent; padding: 0 16px; color: %5; font-size: 13px; }"
        "#SettingNavBtn:hover { background: %8; color: %1;"
        " border-bottom: 2px solid transparent; }"
        "#SettingNavBtn[active=\"true\"] { background: transparent; color: %1;"
        " font-weight: 600; border-bottom: 2px solid %11; }"
        "#SettingContent { background: transparent; }"
        "#SettingCard { background: %10; border-radius: 10px; border: 1px solid %4; }"
        "#SettingCardTitle { font-size: 13px; font-weight: 600; color: %5; background: transparent; }"
        "#SettingPageTitle { font-size: 20px; font-weight: 600; color: %1; background: transparent; }"
    ).arg(C.text, C.bg, C.winEdge, C.border, C.textMuted, C.btnHover,
          C.sidebar, C.hover, C.active, C.card, C.activeLine,
          QString::number(kWindowCornerRadius))
        + qssTooltip() + qssNativeButtonGuard();
}

// 紧凑 QSpinBox：右侧窄条仅边框；箭头由 ThemedSpinBox 自绘置顶（QSS 三角在 Win 上常被 LineEdit 盖住）。
// h 必须等于数字框真实高度，否则 QSS 盒会比控件高、下边框被裁到窗外。
inline QString spinBoxQss(int h = kControlH) {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QSpinBox { background: %1; color: %2; border: 1px solid %3; border-radius: 5px;"
        " font-size: 12px; padding: 0 16px 0 4px; %5 }"
        "QSpinBox QLineEdit { background: transparent; color: %2; border: none; padding: 0 2px;"
        " selection-background-color: %4; margin-right: 14px; }"
        "QSpinBox::up-button, QSpinBox::down-button {"
        " subcontrol-origin: border; width: 14px; background: transparent;"
        " border: none; border-left: 1px solid %3; margin: 0; padding: 0; }"
        "QSpinBox::up-button { subcontrol-position: top right;"
        " border-top-right-radius: 4px; border-bottom: 1px solid %3; }"
        "QSpinBox::down-button { subcontrol-position: bottom right;"
        " border-bottom-right-radius: 4px; }"
        "QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: transparent; }"
        "QSpinBox::up-arrow, QSpinBox::down-arrow { width: 0; height: 0; image: none; border: none; }"
    ).arg(C.card, C.text, C.border, C.activeLine, qssBoxHeight(h, 1.0));
}

// 自绘上下三角并画在最上层，避免被内部 QLineEdit 空白盖住。
class ThemedSpinBox final : public QSpinBox {
public:
    explicit ThemedSpinBox(QWidget* parent = nullptr) : QSpinBox(parent) {
        setButtonSymbols(QAbstractSpinBox::UpDownArrows);
        setAlignment(Qt::AlignCenter);
    }

protected:
    void paintEvent(QPaintEvent* event) override {
        QSpinBox::paintEvent(event);
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing, true);
        const int bw = 14;
        const QRect up(width() - bw, 1, bw - 1, height() / 2 - 1);
        const QRect down(width() - bw, height() / 2, bw - 1, height() / 2 - 2);
        const QColor arrow = QColor(theme().textMuted);
        auto drawTri = [&](const QRect& r, bool upDir) {
            if (r.width() < 5 || r.height() < 5) return;
            const qreal cx = r.center().x();
            const qreal cy = r.center().y();
            QPainterPath path;
            if (upDir) {
                path.moveTo(cx, cy - 2.2);
                path.lineTo(cx + 3.2, cy + 1.6);
                path.lineTo(cx - 3.2, cy + 1.6);
            } else {
                path.moveTo(cx, cy + 2.2);
                path.lineTo(cx + 3.2, cy - 1.6);
                path.lineTo(cx - 3.2, cy - 1.6);
            }
            path.closeSubpath();
            p.fillPath(path, arrow);
        };
        drawTri(up, true);
        drawTri(down, false);
    }
};

// 工具窗 QSS：旧 memo/danmu/overtime 设置窗共用的一套。
inline QString toolQss() {
    const ThemePalette& C = theme();
    return QStringLiteral(
        "QWidget { background: transparent; color: %1;"
        " font-family: 'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;"
        " font-size: 13px; }"
        "#ToolRoot { background: %2; }"
        "#TopBar { background: %3; border-bottom: 1px solid %4; }"
        "#Card { background: %8; border-radius: 10px; border: 1px solid %4; padding: 1px; }"
        "#LeafRuleRow { background: %8; border: 1px solid %4; border-radius: 8px; padding: 2px; }"
        "#SectionTitle { font-size: 13px; font-weight: 600; color: %5; background: transparent; }"
        "#CardTitle { font-size: 13px; font-weight: 600; color: %5; background: transparent; }"
        "#ToolPageTitle { font-size: 20px; font-weight: 600; color: %1; background: transparent; }"
        "#ToolTip { font-size: 12px; color: %5; background: transparent; }"
        "QLabel { background: transparent; }"
        "QPushButton { background: %8; color: %1; border: 2px solid %7; border-radius: 8px;"
        " font-size: 13px; font-weight: 600; %9 padding: 0 12px; }"
        "QPushButton:hover { background: %6; }"
        "QPushButton:disabled { color: %5; border-color: %4; }"
        "#TabBtn { background: transparent; border: none; border-bottom: 2px solid transparent;"
        " border-radius: 0; padding: 0 16px; color: %5; font-size: 13px;"
        " font-weight: 400; min-height: 0; max-height: none; height: auto; }"
        "#TabBtn:hover { background: %6; border: none; border-bottom: 2px solid transparent; }"
        "#TabBtn[active=\"true\"] { color: %1; font-weight: 600;"
        " border: none; border-bottom: 2px solid %7; }"
        "QLineEdit { background: %8; color: %1; border: 1px solid %4; border-radius: 6px;"
        " padding: 0 10px; font-size: 13px; %10 }"
        "QLineEdit:focus { border-color: %7; }"
        "QScrollArea { background: transparent; border: none; }"
        "QScrollBar:vertical { background: transparent; width: 4px; }"
        "QScrollBar::handle:vertical { background: %4; border-radius: 2px; }"
        "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
    ).arg(C.text, C.bg, C.sidebar, C.border, C.textMuted, C.hover, C.activeLine, C.card)
        .arg(qssBoxHeight(kControlH, kControlBorderW), qssBoxHeight(kControlH, 1.0))
        + spinBoxQss() + qssTooltip() + qssNativeButtonGuard();
}

// ─────────────────────────────────────────────
// 布局小工具（旧 home_page._step_card / _scroll_page / memo._row）
// ─────────────────────────────────────────────
inline QScrollArea* scrollPage(QWidget* content) {
    auto* scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    scroll->setStyleSheet(QStringLiteral("QScrollArea { border: none; background: transparent; }"));
    scroll->setWidget(content);
    return scroll;
}

inline QHBoxLayout* labelRow(const QString& text, QWidget* widget) {
    auto* row = new QHBoxLayout;
    auto* lbl = new QLabel(text);
    lbl->setStyleSheet(QStringLiteral("background: transparent;"));
    row->addWidget(lbl);
    row->addStretch();
    row->addWidget(widget);
    return row;
}

struct StepCard {
    QFrame* card = nullptr;
    QVBoxLayout* body = nullptr;
    QLabel* badge = nullptr;
    QLabel* title = nullptr;

    void refreshTheme() const {
        const ThemePalette& C = theme();
        if (badge) {
            badge->setStyleSheet(QStringLiteral(
                "background: %1; color: #fff; border-radius: 12px;"
                " font-size: 12px; font-weight: 700;"
            ).arg(C.activeLine));
        }
        if (title) {
            title->setStyleSheet(QStringLiteral(
                "font-size: 14px; font-weight: 600; color: %1; background: transparent;"
            ).arg(C.text));
        }
    }
};

inline StepCard stepCard(int num, const QString& titleText) {
    StepCard out;
    out.card = new QFrame;
    out.card->setObjectName(QStringLiteral("Card"));
    out.body = new QVBoxLayout(out.card);
    out.body->setContentsMargins(20, 16, 20, 16);
    out.body->setSpacing(12);

    auto* header = new QHBoxLayout;
    out.badge = new QLabel(QString::number(num));
    out.badge->setFixedSize(24, 24);
    out.badge->setAlignment(Qt::AlignCenter);
    out.title = new QLabel(titleText);
    header->addWidget(out.badge);
    header->addSpacing(8);
    header->addWidget(out.title);
    header->addStretch();
    out.body->addLayout(header);
    out.refreshTheme();
    return out;
}

// ─────────────────────────────────────────────
// ThemedComboBox — 自绘圆角下拉，主题即时联动
// ─────────────────────────────────────────────
class DropPopup final : public QFrame {
public:
    // 置顶：悬浮窗（捡叶子/加班机等）是 StaysOnTop 窗口，
    // 普通 Popup 会被它压住，看起来就是下拉被挡掉一块。
    DropPopup() : QFrame(nullptr, Qt::Popup | Qt::FramelessWindowHint
                                      | Qt::NoDropShadowWindowHint
                                      | Qt::WindowStaysOnTopHint) {
        setAttribute(Qt::WA_TranslucentBackground);
        auto* outer = new QVBoxLayout(this);
        outer->setContentsMargins(kPad, kPad, kPad, kPad);
        outer->setSpacing(0);

        scroll_ = new QScrollArea(this);
        scroll_->setWidgetResizable(true);
        scroll_->setFrameShape(QFrame::NoFrame);
        scroll_->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        scroll_->viewport()->setAutoFillBackground(false);
        outer->addWidget(scroll_);

        host_ = new QWidget(scroll_);
        host_->setAttribute(Qt::WA_TranslucentBackground);
        lay_ = new QVBoxLayout(host_);
        lay_->setContentsMargins(0, 0, 0, 0);
        lay_->setSpacing(kItemGap);
        scroll_->setWidget(host_);
    }

    // maxHeight：可用空间上限，超出则内部滚动，绝不越过屏幕边界。
    void setItems(const QStringList& items, const QString& current,
                  const std::function<void(const QString&)>& onSelect, int width,
                  int maxHeight) {
        while (QLayoutItem* item = lay_->takeAt(0)) {
            if (QWidget* w = item->widget()) w->deleteLater();
            delete item;
        }
        const ThemePalette& C = theme();
        QStringList ordered;
        ordered << current;
        for (const QString& t : items) {
            if (t != current) ordered << t;
        }
        for (const QString& text : ordered) {
            auto* btn = new QPushButton(text, host_);
            btn->setFlat(true);
            btn->setCursor(Qt::PointingHandCursor);
            suppressButtonFocus(btn);
            const bool isCurrent = (text == current);
            btn->setStyleSheet(QStringLiteral(
                "QPushButton { background: transparent; color: %1; border: none;"
                " border-radius: 5px; text-align: left; padding: 0 10px;"
                " font-weight: %2; }"
                "QPushButton:hover { background: %3; }"
            ).arg(C.text, isCurrent ? QStringLiteral("600") : QStringLiteral("400"), C.hover));
            btn->setFixedHeight(kItemH);
            QObject::connect(btn, &QPushButton::clicked, this, [this, text, onSelect]() {
                hide();
                if (onSelect) onSelect(text);
            });
            lay_->addWidget(btn);
        }
        const int count = std::max(1, static_cast<int>(ordered.size()));
        const int content = count * kItemH + (count - 1) * kItemGap;
        const int room = std::max(kItemH, maxHeight - 2 * kPad);
        setFixedSize(width, std::min(content, room) + 2 * kPad);
        setStyleSheet(popupChromeQss() + QStringLiteral(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 4px; }"
            "QScrollBar::handle:vertical { background: %1; border-radius: 2px;"
            " min-height: 20px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }"
        ).arg(C.border));
    }

protected:
    void paintEvent(QPaintEvent*) override {
        const ThemePalette& C = theme();
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing);
        // 与按钮/下拉共用同一套画法：四边等厚，不受屏幕缩放影响。
        paintChromeBox(p, this, QColor(C.activeLine), QColor(C.card), kBorder, kRadius);
    }

private:
    // 与按钮/下拉共用同一套圆角、描边宽、行高。
    static constexpr int kRadius = kControlRadius;
    static constexpr int kBorder = kControlBorderW;
    static constexpr int kPad = 4;
    static constexpr int kItemGap = 2;
    static constexpr int kItemH = kControlH;
    QVBoxLayout* lay_ = nullptr;
    QScrollArea* scroll_ = nullptr;
    QWidget* host_ = nullptr;
};

class ThemedComboBox final : public QWidget {
public:
    explicit ThemedComboBox(QWidget* parent = nullptr) : QWidget(parent) {
        popup_ = new DropPopup;
        // 触发钮走自绘 ChromeButton：外形高即控件高，四边描边同厚且不会被裁。
        btn_ = new ChromeButton(QString(), this, kControlH, ControlVariant::Field);
        btn_->setTextAlignment(Qt::AlignLeft);
        btn_->setTrailingArrow(true);
        btn_->setPadX(10);
        QObject::connect(btn_, &QPushButton::clicked, this, [this]() { togglePopup(); });

        auto* lay = new QHBoxLayout(this);
        lay->setContentsMargins(0, 0, 0, 0);
        lay->addWidget(btn_);
        setFixedHeight(kControlH);  // 默认与普通按钮同高，调用方可再覆盖

        onThemeChange(this, [this](const QString&) { refreshTheme(); });
    }

    ~ThemedComboBox() override {
        delete popup_;
        popup_ = nullptr;
    }

    void addItems(const QStringList& items) {
        items_ = items;
        lazyLoaded_ = true;
        if (!items_.isEmpty()) setCurrent(items_.first(), false);
    }

    // 首次展开下拉时再读盘（如 listSkins），避免工具窗构造期扫目录。
    void setLazyItemsLoader(std::function<QStringList()> loader) {
        lazyLoader_ = std::move(loader);
        lazyLoaded_ = false;
        items_.clear();
        current_.clear();
        btn_->setText(QString());
    }

    QString currentText() const { return current_; }

    void setCurrentText(const QString& text) {
        if (items_.contains(text)) setCurrent(text, false);
    }

    void setOnChange(std::function<void(const QString&)> cb) { onChange_ = std::move(cb); }

    void setFixedHeight(int h) {
        outerH_ = h;
        btn_->setOuterHeight(h);
        QWidget::setFixedHeight(h);
    }

    void setMinimumWidth(int w) {
        btn_->setMinimumWidth(w);
        QWidget::setMinimumWidth(w);
    }

    void setFixedSize(int w, int h) {
        outerH_ = h;
        btn_->setOuterHeight(h);
        btn_->setFixedWidth(w);
        QWidget::setFixedSize(w, h);
    }

    // 紧凑档：选择礼物那类小行内下拉。
    void setCompact(bool compact) {
        compact_ = compact;
        btn_->setPadX(compact_ ? 6 : 10);
        btn_->update();
    }

    void refreshTheme() { btn_->update(); }

private:
    static constexpr int kOvershoot = 2;
    static constexpr int kGap = 2;
    static constexpr int kMinPopupH = 120;
    int outerH_ = kControlH;

    void setCurrent(const QString& text, bool emitChange) {
        const QString old = current_;
        current_ = text;
        btn_->setText(text);
        if (emitChange && old != text && onChange_) onChange_(text);
    }

    void togglePopup() {
        if (popup_->isVisible()) {
            popup_->hide();
            return;
        }
        if (!lazyLoaded_ && lazyLoader_) {
            addItems(lazyLoader_());
        }
        QScreen* screen = QGuiApplication::screenAt(mapToGlobal(rect().center()));
        if (!screen) screen = QGuiApplication::primaryScreen();
        const QRect avail = screen ? screen->availableGeometry() : QRect();

        const QPoint topLeft = mapToGlobal(QPoint(0, 0));
        const int belowY = topLeft.y() + height() + kGap;
        const int spaceBelow = avail.isValid() ? avail.bottom() + 1 - belowY : 0;
        const int spaceAbove = avail.isValid() ? topLeft.y() - kGap - avail.top() : 0;
        const int maxHeight =
            avail.isValid() ? std::max(kMinPopupH, std::max(spaceBelow, spaceAbove)) : 400;

        popup_->setItems(items_, current_, [this](const QString& t) { setCurrent(t, true); },
                         width() + kOvershoot * 2, maxHeight);

        // 默认贴着控件下方展开，不覆盖控件；下方放不下才整体翻到上方。
        const int popupH = popup_->height();
        int x = topLeft.x() - kOvershoot;
        int y = belowY;
        if (avail.isValid()) {
            if (popupH > spaceBelow && popupH <= spaceAbove) {
                y = topLeft.y() - kGap - popupH;
            }
            y = std::clamp(y, avail.top(),
                           std::max(avail.top(), avail.bottom() + 1 - popupH));
            x = std::clamp(x, avail.left(),
                           std::max(avail.left(), avail.right() + 1 - popup_->width()));
        }
        popup_->move(x, y);
        popup_->show();
    }

    QStringList items_;
    QString current_;
    ChromeButton* btn_ = nullptr;
    DropPopup* popup_ = nullptr;
    bool compact_ = false;
    bool lazyLoaded_ = true;
    std::function<QStringList()> lazyLoader_;
    std::function<void(const QString&)> onChange_;
};

// ─────────────────────────────────────────────
// ThemedToggle — 48×26 自绘滑条开关，读写 config
// ─────────────────────────────────────────────
class ThemedToggle final : public QWidget {
public:
    explicit ThemedToggle(const QString& cfgKey, bool defaultValue = true, QWidget* parent = nullptr)
        : QWidget(parent), key_(cfgKey) {
        value_ = configGet(cfgKey, defaultValue).toBool();
        pos_ = value_ ? 1.0 : 0.0;
        setFixedSize(48, 26);
        setCursor(Qt::PointingHandCursor);
        setAttribute(Qt::WA_Hover);

        anim_ = new QVariantAnimation(this);
        anim_->setDuration(160);
        anim_->setEasingCurve(QEasingCurve::InOutCubic);
        QObject::connect(anim_, &QVariantAnimation::valueChanged, this, [this](const QVariant& v) {
            pos_ = v.toReal();
            update();
        });
        onThemeChange(this, [this](const QString&) { update(); });
    }

    bool value() const { return value_; }

    void setValue(bool v, bool persist = true) {
        if (v == value_) return;
        value_ = v;
        if (persist) configSet(key_, v);
        pos_ = v ? 1.0 : 0.0;
        update();
    }

    void setOnToggled(std::function<void(bool)> cb) { onToggled_ = std::move(cb); }

protected:
    void paintEvent(QPaintEvent*) override {
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing);

        const ThemePalette& C = theme();
        const int w = width();
        const int h = height();
        const int m = 3;
        const int d = h - m * 2;
        const qreal r = h / 2.0;

        const QColor on(C.activeLine);
        const QColor off(C.border);
        const QColor bg(
            static_cast<int>(off.red() + (on.red() - off.red()) * pos_),
            static_cast<int>(off.green() + (on.green() - off.green()) * pos_),
            static_cast<int>(off.blue() + (on.blue() - off.blue()) * pos_));

        p.setBrush(bg);
        p.setPen(Qt::NoPen);
        p.drawRoundedRect(0, 0, w, h, r, r);

        const qreal x = m + pos_ * (w - m * 2 - d);
        p.setBrush(QColor(QStringLiteral("#ffffff")));
        p.drawEllipse(static_cast<int>(x), m, d, d);
    }

    void mousePressEvent(QMouseEvent* event) override {
        if (event->button() != Qt::LeftButton) {
            QWidget::mousePressEvent(event);
            return;
        }
        value_ = !value_;
        configSet(key_, value_);
        anim_->stop();
        anim_->setStartValue(pos_);
        anim_->setEndValue(value_ ? 1.0 : 0.0);
        anim_->start();
        if (onToggled_) onToggled_(value_);
    }

private:
    QString key_;
    bool value_ = false;
    qreal pos_ = 0.0;
    QVariantAnimation* anim_ = nullptr;
    std::function<void(bool)> onToggled_;
};

// ─────────────────────────────────────────────
// 窗口壳：关闭按钮 / 标题栏 / 导航按钮 / 侧栏
// ─────────────────────────────────────────────
inline constexpr int kSidebarExpanded = 220;
inline constexpr int kSidebarCollapsed = 64;
inline constexpr int kSidebarAnimMs = 220;

// 自绘：QSS 的 border-radius 对 hover 背景裁剪不可靠，这里手绘右上角圆弧。
class CloseButton final : public QPushButton {
public:
    explicit CloseButton(QWidget* parent = nullptr)
        : QPushButton(QStringLiteral("✕"), parent) {
        setObjectName(QStringLiteral("WinBtn_close"));
        setFixedSize(kW, kH);
        setCursor(Qt::PointingHandCursor);
        polishFlatChromeButton(this);
    }

protected:
    bool event(QEvent* e) override {
        if (e->type() == QEvent::Enter || e->type() == QEvent::Leave
            || e->type() == QEvent::HoverEnter || e->type() == QEvent::HoverLeave) {
            update();
        }
        return QPushButton::event(e);
    }

    void paintEvent(QPaintEvent*) override {
        const ThemePalette& C = theme();
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing);

        const bool hovered = underMouse();
        if (hovered) {
            p.setPen(Qt::NoPen);
            p.setBrush(QColor(C.closeHover));
            QPainterPath path;
            path.moveTo(0, 0);
            path.lineTo(kW - kR, 0);
            path.arcTo(kW - kR * 2, 0, kR * 2, kR * 2, 90, -90);
            path.lineTo(kW, kH);
            path.lineTo(0, kH);
            path.closeSubpath();
            p.drawPath(path);
        }

        p.setPen(hovered ? QColor(QStringLiteral("#ffffff")) : QColor(C.textMuted));
        QFont f = font();
        f.setPointSize(11);
        p.setFont(f);
        p.drawText(rect(), Qt::AlignCenter, QStringLiteral("✕"));
    }

private:
    static constexpr int kW = 46;
    static constexpr int kH = 36;
    static constexpr int kR = 10;
};

class TitleBar final : public QWidget {
public:
    static constexpr int kHeight = 36;

    explicit TitleBar(QWidget* parent = nullptr) : QWidget(parent) {
        setObjectName(QStringLiteral("TitleBar"));
        setAttribute(Qt::WA_StyledBackground, true);
        setFixedHeight(kHeight);

        auto* lay = new QHBoxLayout(this);
        lay->setContentsMargins(14, 0, 0, 0);
        lay->setSpacing(0);

        auto* title = new QLabel(QStringLiteral("LiveAIO"), this);
        title->setObjectName(QStringLiteral("AppTitle"));
        lay->addWidget(title);
        lay->addStretch();

        auto* minBtn = new QPushButton(QStringLiteral("─"), this);
        minBtn->setObjectName(QStringLiteral("WinBtn"));
        minBtn->setCursor(Qt::PointingHandCursor);
        suppressButtonFocus(minBtn);
        QObject::connect(minBtn, &QPushButton::clicked, this, [this]() {
            if (window()) window()->showMinimized();
        });
        lay->addWidget(minBtn);

        auto* closeBtn = new CloseButton(this);
        QObject::connect(closeBtn, &QPushButton::clicked, this, [this]() {
            if (onClose_) {
                onClose_();
            } else if (window()) {
                window()->close();
            }
        });
        lay->addWidget(closeBtn);
    }

    void setOnClose(std::function<void()> cb) { onClose_ = std::move(cb); }

protected:
    void mousePressEvent(QMouseEvent* event) override {
        if (event->button() == Qt::LeftButton && window()) {
            dragging_ = true;
            dragOffset_ = event->globalPosition().toPoint() - window()->frameGeometry().topLeft();
        }
    }

    void mouseMoveEvent(QMouseEvent* event) override {
        if (dragging_ && (event->buttons() & Qt::LeftButton) && window()) {
            window()->move(event->globalPosition().toPoint() - dragOffset_);
        }
    }

    void mouseReleaseEvent(QMouseEvent*) override { dragging_ = false; }

    void mouseDoubleClickEvent(QMouseEvent* event) override {
        if (event->button() != Qt::LeftButton || !window()) return;
        if (window()->isMaximized()) window()->showNormal();
        else window()->showMaximized();
    }

private:
    bool dragging_ = false;
    QPoint dragOffset_;
    std::function<void()> onClose_;
};

class NavButton final : public QPushButton {
public:
    static constexpr int kHeight = 44;
    static constexpr int kIconPx = 18;

    NavButton(const QString& icon, const QString& label,
              const QString& objName = QStringLiteral("NavBtn"), QWidget* parent = nullptr)
        : QPushButton(parent) {
        setObjectName(objName);
        setFixedHeight(kHeight);
        setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        setCursor(Qt::PointingHandCursor);
        suppressButtonFocus(this);

        auto* lay = new QHBoxLayout(this);
        lay->setContentsMargins(0, 0, 0, 0);
        lay->setSpacing(0);

        bar_ = new QFrame(this);
        bar_->setFixedWidth(3);

        icon_ = new QLabel(icon, this);
        icon_->setFixedWidth(kSidebarCollapsed - 3);
        icon_->setAlignment(Qt::AlignCenter);

        label_ = new QLabel(label, this);

        lay->addWidget(bar_);
        lay->addWidget(icon_);
        lay->addWidget(label_);
        lay->addStretch();

        setActive(false);
    }

    void setActive(bool on) {
        active_ = on;
        setProperty("active", on);
        style()->unpolish(this);
        style()->polish(this);
        const ThemePalette& C = theme();
        const QString barColor = on ? C.activeLine : QStringLiteral("transparent");
        const QString txtColor = on ? C.text : C.textMuted;
        bar_->setStyleSheet(QStringLiteral("background: %1;").arg(barColor));
        icon_->setStyleSheet(QStringLiteral(
            "font-size: %1px; background: transparent; color: %2;"
        ).arg(QString::number(kIconPx), txtColor));
        label_->setStyleSheet(QStringLiteral(
            "background: transparent; color: %1; font-size: 14px;"
        ).arg(txtColor));
    }

    bool active() const { return active_; }
    void refreshTheme() { setActive(active_); }

private:
    QFrame* bar_ = nullptr;
    QLabel* icon_ = nullptr;
    QLabel* label_ = nullptr;
    bool active_ = false;
};

struct NavItem {
    QString icon;
    QString name;
};

// 收缩动画：内部 panel 宽度锁在展开值，外壳做宽度动画由 Qt 自动裁剪，
// 因此文字静止、边界像遮板一样扫过；不能对外壳 setFixedWidth 否则会瞬跳。
class Sidebar final : public QWidget {
public:
    Sidebar(const QVector<NavItem>& items, const NavItem& settingsItem, QWidget* parent = nullptr)
        : QWidget(parent) {
        setObjectName(QStringLiteral("Sidebar"));
        setAttribute(Qt::WA_StyledBackground, true);
        setFixedWidth(kSidebarExpanded);
        setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Expanding);
        build(items, settingsItem);
        setupAnim();
    }

    const QVector<NavButton*>& navButtons() const { return navBtns_; }
    NavButton* settingsButton() const { return settingsBtn_; }

    void toggle() {
        expanded_ = !expanded_;
        const int start = width();
        const int end = expanded_ ? kSidebarExpanded : kSidebarCollapsed;
        // 一次回调同时改 min/max，避免双动画不同步导致每帧两次布局。
        anim_->stop();
        anim_->setStartValue(start);
        anim_->setEndValue(end);
        anim_->start();
        toggleIcon_->setText(expanded_ ? QStringLiteral("◀") : QStringLiteral("▶"));
    }

    void setOnAnimActive(std::function<void(bool)> cb) { onAnimActive_ = std::move(cb); }

    void setActiveMain(int index) {
        for (int i = 0; i < navBtns_.size(); ++i) navBtns_[i]->setActive(i == index);
        if (settingsBtn_) settingsBtn_->setActive(false);
    }

    void setActiveSettings() {
        for (auto* btn : navBtns_) btn->setActive(false);
        if (settingsBtn_) settingsBtn_->setActive(true);
    }

    void refreshTheme() {
        const ThemePalette& C = theme();
        toggleIcon_->setStyleSheet(QStringLiteral(
            "font-size: 13px; background: transparent; color: %1;").arg(C.textMuted));
        toggleLabel_->setStyleSheet(QStringLiteral(
            "background: transparent; color: %1; font-size: 13px;").arg(C.textMuted));
        for (auto* btn : navBtns_) btn->refreshTheme();
        if (settingsBtn_) settingsBtn_->refreshTheme();
    }

protected:
    void resizeEvent(QResizeEvent* event) override {
        QWidget::resizeEvent(event);
        if (panel_) panel_->setGeometry(0, 0, kSidebarExpanded, height());
    }

private:
    void build(const QVector<NavItem>& items, const NavItem& settingsItem) {
        panel_ = new QWidget(this);
        panel_->setGeometry(0, 0, kSidebarExpanded, 600);

        auto* outer = new QVBoxLayout(panel_);
        outer->setContentsMargins(0, 12, 0, 12);
        outer->setSpacing(0);

        auto* toggleBtn = new QPushButton(panel_);
        toggleBtn->setObjectName(QStringLiteral("ToggleBtn"));
        toggleBtn->setFixedHeight(44);
        toggleBtn->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
        toggleBtn->setCursor(Qt::PointingHandCursor);
        suppressButtonFocus(toggleBtn);
        QObject::connect(toggleBtn, &QPushButton::clicked, this, [this]() { toggle(); });

        auto* tLay = new QHBoxLayout(toggleBtn);
        tLay->setContentsMargins(0, 0, 0, 0);
        tLay->setSpacing(0);
        toggleIcon_ = new QLabel(QStringLiteral("◀"), toggleBtn);
        toggleIcon_->setFixedWidth(kSidebarCollapsed);
        toggleIcon_->setAlignment(Qt::AlignCenter);
        toggleLabel_ = new QLabel(QStringLiteral("收起"), toggleBtn);
        tLay->addWidget(toggleIcon_);
        tLay->addWidget(toggleLabel_);
        tLay->addStretch();

        outer->addWidget(toggleBtn);
        outer->addSpacing(4);

        auto* scroll = new QScrollArea(panel_);
        scroll->setWidgetResizable(false);
        scroll->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        scroll->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);

        auto* container = new QWidget(scroll);
        container->setObjectName(QStringLiteral("ScrollContainer"));
        container->setFixedWidth(kSidebarExpanded);
        auto* cLay = new QVBoxLayout(container);
        cLay->setContentsMargins(0, 0, 0, 0);
        cLay->setSpacing(1);
        for (const NavItem& item : items) {
            auto* btn = new NavButton(item.icon, item.name, QStringLiteral("NavBtn"), container);
            navBtns_.append(btn);
            cLay->addWidget(btn);
        }
        cLay->addStretch();
        scroll->setWidget(container);
        outer->addWidget(scroll, 1);
        outer->addSpacing(4);

        settingsBtn_ = new NavButton(settingsItem.icon, settingsItem.name,
                                     QStringLiteral("SettingsBtn"), panel_);
        outer->addWidget(settingsBtn_);

        refreshTheme();
    }

    void setupAnim() {
        anim_ = new QVariantAnimation(this);
        anim_->setDuration(kSidebarAnimMs);
        anim_->setEasingCurve(QEasingCurve::InOutQuart);
        QObject::connect(anim_, &QVariantAnimation::valueChanged, this, [this](const QVariant& v) {
            const int w = qRound(v.toReal());
            setMinimumWidth(w);
            setMaximumWidth(w);
        });
        QObject::connect(anim_, &QVariantAnimation::stateChanged, this,
                         [this](QAbstractAnimation::State neu, QAbstractAnimation::State) {
                             if (onAnimActive_) {
                                 onAnimActive_(neu == QAbstractAnimation::Running);
                             }
                         });
    }

    QWidget* panel_ = nullptr;
    QLabel* toggleIcon_ = nullptr;
    QLabel* toggleLabel_ = nullptr;
    QVector<NavButton*> navBtns_;
    NavButton* settingsBtn_ = nullptr;
    QVariantAnimation* anim_ = nullptr;
    std::function<void(bool)> onAnimActive_;
    bool expanded_ = true;
};

// ─────────────────────────────────────────────
// 悬浮窗共享件：截图透明、软最小化、拖拽/缩放冻结
// ─────────────────────────────────────────────
class OverlayResizeFreeze {
public:
    explicit OverlayResizeFreeze(QWidget* host, std::function<void()> onResume = {})
        : host_(host), onResume_(std::move(onResume)) {}

    bool frozen() const { return live_; }

    void begin() {
        if (live_ || !host_) return;
        live_ = true;
        paused_.clear();
        for (auto* anim : host_->findChildren<QAbstractAnimation*>()) {
            if (anim->state() == QAbstractAnimation::Running) {
                anim->pause();
                paused_.push_back(anim);
            }
        }
        // 不关 setUpdatesEnabled：边框需高帧率重绘，内容由调用方节流。
    }

    void end() {
        if (!live_ || !host_) return;
        live_ = false;
        for (auto* anim : paused_) {
            if (anim && anim->state() == QAbstractAnimation::Paused) anim->resume();
        }
        paused_.clear();
        if (onResume_) onResume_();
        host_->update();
    }

private:
    QWidget* host_ = nullptr;
    std::function<void()> onResume_;
    bool live_ = false;
    QList<QAbstractAnimation*> paused_;
};

inline bool& appAlphaPrepared() {
    static bool prepared = false;
    return prepared;
}

inline void prepareAppAlphaFormat() {
    if (appAlphaPrepared()) return;
    QSurfaceFormat fmt;
    fmt.setAlphaBufferSize(8);
    QSurfaceFormat::setDefaultFormat(fmt);
    appAlphaPrepared() = true;
}

class AlphaBootstrap final : public QObject {
public:
    explicit AlphaBootstrap(QWidget* window) : QObject(window), window_(window) {
        window->installEventFilter(this);
    }

    bool eventFilter(QObject* obj, QEvent* event) override {
        if (obj == window_ && event->type() == QEvent::Show && !done_) {
            done_ = true;
            QTimer::singleShot(0, this, [this]() { bootstrap(); });
        }
        return false;
    }

private:
    void bootstrap() {
#ifdef Q_OS_WIN
#if defined(LIVEAIO_HAS_OPENGLWIDGET)
        if (!window_ || !window_->isVisible()) return;
        auto* gl = new QOpenGLWidget(window_);
        gl->setFixedSize(1, 1);
        gl->setAttribute(Qt::WA_TransparentForMouseEvents);
        gl->move(-10, -10);
        gl->show();
        QTimer::singleShot(0, gl, &QObject::deleteLater);
#endif
#endif
    }

    QWidget* window_ = nullptr;
    bool done_ = false;
};

inline void enableCaptureTransparency(QWidget* window) {
    prepareAppAlphaFormat();
#ifdef Q_OS_WIN
    if (!window || window->property("_capture_alpha_ready").toBool()) return;
    window->setProperty("_capture_alpha_ready", true);
    new AlphaBootstrap(window);
#else
    Q_UNUSED(window);
#endif
}

class OverlayHost : public QWidget {
public:
    explicit OverlayHost(QWidget* parent = nullptr) : QWidget(parent) {
        setWindowFlags(Qt::Tool | Qt::FramelessWindowHint | Qt::WindowStaysOnTopHint);
        setAttribute(Qt::WA_TranslucentBackground, true);
        setAttribute(Qt::WA_ShowWithoutActivating, false);
        setMouseTracking(true);
        enableCaptureTransparency(this);
        freeze_ = std::make_unique<OverlayResizeFreeze>(this, [this]() { if (onResume_) onResume_(); });

        borderAnim_ = new QVariantAnimation(this);
        borderAnim_->setDuration(180);
        QObject::connect(borderAnim_, &QVariantAnimation::valueChanged, this, [this](const QVariant& v) {
            animR_ = v.toReal();
            update();
        });
        animR_ = 12.0;
        borderShown_ = true;
    }

    void setOnResume(std::function<void()> cb) { onResume_ = std::move(cb); }
    OverlayResizeFreeze* freeze() const { return freeze_.get(); }
    bool softMinimized() const { return softMinimized_; }

    void setBorderShown(bool shown, bool animate = true) {
        if (shown == borderShown_) return;
        borderShown_ = shown;
        borderAnim_->stop();
        const qreal target = shown ? 12.0 : 0.0;
        if (animate) {
            borderAnim_->setStartValue(animR_);
            borderAnim_->setEndValue(target);
            borderAnim_->start();
        } else {
            animR_ = target;
            update();
        }
    }

    void softMinimize() {
        setBorderShown(false, true);
        softMinimized_ = true;
        lower();
    }

    void restoreFromMinimize() {
        if (!softMinimized_) return;
        softMinimized_ = false;
        setBorderShown(true, true);
        raise();
    }

protected:
    void changeEvent(QEvent* event) override {
        if (event->type() == QEvent::WindowStateChange) {
            if (windowState() & Qt::WindowMinimized) {
                QTimer::singleShot(0, this, [this]() {
                    showNormal();
                    setBorderShown(false, false);
                    softMinimized_ = true;
                    lower();
                });
            } else if (softMinimized_) {
                QTimer::singleShot(0, this, [this]() { restoreFromMinimize(); });
            }
        } else if (event->type() == QEvent::ActivationChange && isActiveWindow()) {
            QTimer::singleShot(0, this, [this]() { restoreFromMinimize(); });
        }
        QWidget::changeEvent(event);
    }

    void showEvent(QShowEvent* event) override {
        QWidget::showEvent(event);
        if (softMinimized_) QTimer::singleShot(0, this, [this]() { restoreFromMinimize(); });
    }

    void mousePressEvent(QMouseEvent* event) override {
        if (event->button() == Qt::LeftButton) {
            if (freeze_) freeze_->begin();
            dragging_ = true;
            dragOffset_ = event->globalPosition().toPoint() - frameGeometry().topLeft();
        }
        QWidget::mousePressEvent(event);
    }

    void mouseMoveEvent(QMouseEvent* event) override {
        if (dragging_) move(event->globalPosition().toPoint() - dragOffset_);
        QWidget::mouseMoveEvent(event);
    }

    void mouseReleaseEvent(QMouseEvent* event) override {
        if (dragging_ && freeze_) freeze_->end();
        dragging_ = false;
        QWidget::mouseReleaseEvent(event);
    }

    void mouseDoubleClickEvent(QMouseEvent* event) override {
        if (event->button() == Qt::LeftButton) {
            if (softMinimized_) restoreFromMinimize();
            else softMinimize();
        }
        QWidget::mouseDoubleClickEvent(event);
    }

    void paintEvent(QPaintEvent* event) override {
        Q_UNUSED(event);
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing, true);
        if (animR_ > 0.5) {
            p.setPen(QPen(QColor(114, 150, 255, softMinimized_ ? 60 : 160), 2));
            p.setBrush(Qt::NoBrush);
            p.drawRoundedRect(rect().adjusted(1, 1, -1, -1), animR_, animR_);
        }
    }

private:
    std::unique_ptr<OverlayResizeFreeze> freeze_;
    std::function<void()> onResume_;
    QVariantAnimation* borderAnim_ = nullptr;
    qreal animR_ = 12.0;
    bool borderShown_ = true;
    bool softMinimized_ = false;
    bool dragging_ = false;
    QPoint dragOffset_;
};

// ─────────────────────────────────────────────
// Async-by-default：主线程分帧 / 工作线程读盘（不碰 QWidget）
// ─────────────────────────────────────────────
inline void deferNextTick(QObject* context, std::function<void()> fn) {
    if (!context) return;
    QTimer::singleShot(0, context, std::move(fn));
}

class ChunkBuilder final : public QObject {
public:
    ChunkBuilder(QObject* parent, int batchSize, int intervalMs)
        : QObject(parent), batchSize_(batchSize), intervalMs_(intervalMs) {
        timer_ = new QTimer(this);
        timer_->setSingleShot(true);
        QObject::connect(timer_, &QTimer::timeout, this, [this]() { runBatch(); });
    }

    void start(int total, std::function<void(int)> buildOne, std::function<void()> onDone) {
        total_ = total;
        index_ = 0;
        buildOne_ = std::move(buildOne);
        onDone_ = std::move(onDone);
        runBatch();
    }

private:
    void runBatch() {
        if (!buildOne_) return;
        const int end = std::min(index_ + batchSize_, total_);
        for (int i = index_; i < end; ++i) buildOne_(i);
        index_ = end;
        if (index_ >= total_) {
            if (onDone_) onDone_();
            buildOne_ = nullptr;
            onDone_ = nullptr;
            return;
        }
        timer_->start(intervalMs_);
    }

    QTimer* timer_ = nullptr;
    int batchSize_ = 8;
    int intervalMs_ = 24;
    int total_ = 0;
    int index_ = 0;
    std::function<void(int)> buildOne_;
    std::function<void()> onDone_;
};

class WidgetDeferredDestroy final : public QObject {
public:
    explicit WidgetDeferredDestroy(QObject* parent = nullptr) : QObject(parent) {
        timer_ = new QTimer(this);
        timer_->setSingleShot(true);
        QObject::connect(timer_, &QTimer::timeout, this, [this]() { drainBatch(); });
    }

    void enqueue(QWidget* widget) {
        if (!widget) return;
        queue_.append(widget);
        schedule();
    }

    void enqueueBatch(const QVector<QWidget*>& widgets) {
        for (QWidget* w : widgets) {
            if (w) queue_.append(w);
        }
        schedule();
    }

    void setBatchSize(int n) { batchSize_ = std::max(1, n); }
    void setIntervalMs(int ms) { intervalMs_ = std::max(8, ms); }

private:
    void schedule() {
        if (!timer_->isActive() && !queue_.isEmpty()) timer_->start(intervalMs_);
    }

    void drainBatch() {
        int n = 0;
        while (!queue_.isEmpty() && n < batchSize_) {
            QPointer<QWidget> w = queue_.takeFirst();
            if (w) w->deleteLater();
            ++n;
        }
        if (!queue_.isEmpty()) timer_->start(intervalMs_);
    }

    QTimer* timer_ = nullptr;
    QVector<QPointer<QWidget>> queue_;
    int batchSize_ = 8;
    int intervalMs_ = 24;
};

inline void readJsonAsync(const QString& path, QObject* context,
                          std::function<void(QJsonObject)> onReady) {
    if (!context || !onReady) return;
    auto* watcher = new QFutureWatcher<QJsonObject>(context);
    QObject::connect(watcher, &QFutureWatcher<QJsonObject>::finished, context,
                     [watcher, onReady]() {
                         onReady(watcher->result());
                         watcher->deleteLater();
                     });
    watcher->setFuture(QtConcurrent::run([path]() {
        QFile file(path);
        if (!file.open(QIODevice::ReadOnly)) return QJsonObject{};
        QJsonParseError err;
        const QJsonDocument doc = QJsonDocument::fromJson(file.readAll(), &err);
        return (err.error == QJsonParseError::NoError && doc.isObject()) ? doc.object()
                                                                         : QJsonObject{};
    }));
}

// 非负整数输入：失焦 clamp。加班机 / 捡叶子等工具共用。
inline QString sanitizeDigits(const QString& text, int maxVal, int width = 3) {
    QString digits;
    for (const QChar& c : text) {
        if (c.isDigit()) digits.append(c);
    }
    if (digits.isEmpty()) digits = QStringLiteral("0");
    bool ok = false;
    int n = digits.toInt(&ok);
    if (!ok) n = 0;
    n = std::clamp(n, 0, maxVal);
    Q_UNUSED(width);
    return QString::number(n);
}

class IntField final : public QLineEdit {
public:
    IntField(int maxVal, int charW, int height = 24, int minWidth = -1,
             QWidget* parent = nullptr)
        : QLineEdit(parent), max_(maxVal) {
        const QFontMetrics fm(QFont(QStringLiteral("Microsoft YaHei"), 11));
        int w = fm.horizontalAdvance(QString(charW, QLatin1Char('8'))) + 16;
        if (minWidth > 0) w = std::max(w, minWidth);
        else if (charW >= 3) w = std::max(w, 42);
        else w = std::max(w, 36);
        setFixedSize(w, height);
        setAlignment(Qt::AlignCenter);
        setText(QStringLiteral("0"));
        QObject::connect(this, &QLineEdit::textChanged, this, [this](const QString& text) {
            QString cleaned;
            for (const QChar& c : text) {
                if (c.isDigit()) cleaned.append(c);
            }
            if (cleaned != text) {
                const QSignalBlocker blocker(this);
                setText(cleaned.isEmpty() ? QStringLiteral("0") : cleaned);
            }
        });
        QObject::connect(this, &QLineEdit::editingFinished, this, [this]() {
            const QSignalBlocker blocker(this);
            setText(sanitizeDigits(text(), max_));
            if (onCommit_) onCommit_();
        });
    }

    void setOnCommit(std::function<void()> cb) { onCommit_ = std::move(cb); }
    int value() const { return sanitizeDigits(text(), max_).toInt(); }
    void setValue(int n) { setText(sanitizeDigits(QString::number(n), max_)); }

private:
    int max_;
    std::function<void()> onCommit_;
};

inline QLabel* formLabel(const QString& text, QWidget* parent = nullptr, int width = 108) {
    auto* lb = new QLabel(text, parent);
    lb->setFixedWidth(width);
    lb->setAlignment(Qt::AlignVCenter | Qt::AlignLeft);
    lb->setStyleSheet(QStringLiteral("background: transparent;"));
    return lb;
}

// tools / pages 可写入的应用根路径，供礼物控件解析资源。
inline QString& toolAppRoot() {
    static QString root;
    return root;
}

inline void setToolAppRoot(const QString& root) { toolAppRoot() = root; }

}  // namespace liveaio::util

#endif  // LIVEAIO_UTIL_WIDGETS_CPP
