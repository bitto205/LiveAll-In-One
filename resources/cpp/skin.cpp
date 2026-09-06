#include <QApplication>
#include <QDir>
#include <QFile>
#include <QFont>
#include <QFontMetrics>
#include <QImage>
#include <QImageReader>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QPainter>
#include <QPixmap>
#include <QScreen>

#include <algorithm>
#include <cmath>

namespace liveaio::resources {

struct RoleTextStyle {
    QString fontFamily = QStringLiteral("Microsoft YaHei");
    int pixelSize = 13;
    bool bold = false;
    QString align = QStringLiteral("center");
    QString vAlign = QStringLiteral("vcenter");
    bool wordWrap = false;
    int minPx = 8;
    int maxPx = 13;
    QString fitRef;
    QString styleExtra;
    int minChars = 4;
    int maxChars = 16;
    int slackPx = 2;

    static RoleTextStyle fromObject(const QJsonObject& raw) {
        RoleTextStyle s;
        if (raw.isEmpty()) return s;
        s.fontFamily = raw.value(QStringLiteral("font_family")).toString(s.fontFamily);
        s.pixelSize = raw.value(QStringLiteral("pixel_size")).toInt(s.pixelSize);
        s.bold = raw.value(QStringLiteral("bold")).toBool(false);
        s.align = raw.value(QStringLiteral("align")).toString(s.align);
        s.vAlign = raw.value(QStringLiteral("v_align")).toString(s.vAlign);
        s.wordWrap = raw.value(QStringLiteral("word_wrap")).toBool(false);
        s.minPx = raw.value(QStringLiteral("min_px")).toInt(8);
        s.maxPx = raw.value(QStringLiteral("max_px")).toInt(raw.value(QStringLiteral("pixel_size")).toInt(13));
        s.fitRef = raw.value(QStringLiteral("fit_ref")).toString();
        s.styleExtra = raw.value(QStringLiteral("style_extra")).toString();
        s.minChars = raw.value(QStringLiteral("min_chars")).toInt(4);
        s.maxChars = raw.value(QStringLiteral("max_chars")).toInt(16);
        s.slackPx = raw.value(QStringLiteral("slack_px")).toInt(2);
        return s;
    }

    Qt::Alignment qtAlign() const {
        Qt::Alignment h = Qt::AlignHCenter;
        if (align == QLatin1String("left")) h = Qt::AlignLeft;
        else if (align == QLatin1String("right")) h = Qt::AlignRight;
        Qt::Alignment v = Qt::AlignVCenter;
        if (vAlign == QLatin1String("top")) v = Qt::AlignTop;
        else if (vAlign == QLatin1String("bottom")) v = Qt::AlignBottom;
        return h | v;
    }

    QFont font(int px = -1) const {
        QFont f(fontFamily);
        f.setPixelSize(px > 0 ? px : pixelSize);
        if (bold) f.setBold(true);
        return f;
    }
};

struct SkinMetrics {
    int fadeW = 22;
    int padH = 12;
    int padV = 7;
    int giftIconSize = 32;
    int giftIconGap = 8;
};

static qreal screenDpr() {
    if (auto* s = QApplication::primaryScreen()) return s->devicePixelRatio();
    return 1.0;
}

static QColor parseColor(const QJsonValue& raw, const QColor& fallback = QColor(255, 255, 255)) {
    if (raw.isString()) {
        QColor c(raw.toString());
        return c.isValid() ? c : fallback;
    }
    if (raw.isArray()) {
        const auto a = raw.toArray();
        if (a.size() >= 3) {
            return QColor(a[0].toInt(), a[1].toInt(), a[2].toInt(),
                          a.size() > 3 ? a[3].toInt(255) : 255);
        }
    }
    return fallback;
}

static int fitFontPixelSize(const QString& text, int maxW, int maxH,
                            const QString& family, bool bold, int maxPx, int minPx = 8) {
    maxW = std::max(1, maxW - 2);
    maxH = std::max(1, maxH - 2);
    int lo = minPx;
    int hi = std::max(8, maxH);
    if (maxPx > 0) hi = std::min(hi, maxPx);
    if (lo > hi) return std::max(minPx, hi);
    int best = lo;
    const QString sample = text.isEmpty() ? QStringLiteral("国") : text;
    while (lo <= hi) {
        const int mid = (lo + hi) / 2;
        QFont f(family);
        f.setPixelSize(mid);
        if (bold) f.setBold(true);
        QFontMetrics fm(f);
        // height() 含 leading，YaHei 会把字压到明显偏小；用字形紧框。
        const QRect tight = fm.tightBoundingRect(sample);
        if (fm.horizontalAdvance(sample) <= maxW && tight.height() <= maxH) {
            best = mid;
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    return best;
}

struct LoadedStill {
    QPixmap pixmap;
    int logicalW = 0;
    int logicalH = 0;
};

struct LoadedAnim {
    QVector<QPixmap> frames;
    QVector<int> delays;
    int logicalW = 0;
    int logicalH = 0;
};

static LoadedStill loadStillPath(const QString& path, int logicalH, qreal dpr = 0, qreal uiScale = 1.0) {
    if (dpr <= 0) dpr = screenDpr();
    const int lh = std::max(1, int(std::lround(std::max(1, logicalH) * std::max(0.25, uiScale))));
    LoadedStill out;
    out.logicalH = lh;
    out.logicalW = lh;
    QImageReader reader(path);
    reader.setAutoTransform(true);
    QImage img = reader.read();
    if (img.isNull()) return out;
    const int lw = std::max(1, int(std::lround(img.width() * double(lh) / std::max(1, img.height()))));
    const int pw = std::max(1, int(std::lround(lw * dpr)));
    const int ph = std::max(1, int(std::lround(lh * dpr)));
    img = img.scaled(pw, ph, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
    out.pixmap = QPixmap::fromImage(img);
    out.pixmap.setDevicePixelRatio(dpr);
    out.logicalW = lw;
    return out;
}

static LoadedAnim loadAnimPath(const QString& path, int logicalH, qreal dpr = 0, qreal uiScale = 1.0) {
    if (dpr <= 0) dpr = screenDpr();
    const int lh = std::max(1, int(std::lround(std::max(1, logicalH) * std::max(0.25, uiScale))));
    LoadedAnim out;
    out.logicalH = lh;
    out.logicalW = lh;
    QImageReader reader(path);
    reader.setAutoTransform(true);
    if (!reader.canRead()) {
        // Fallback to still.
        auto still = loadStillPath(path, logicalH, dpr, uiScale);
        if (!still.pixmap.isNull()) {
            out.frames = {still.pixmap};
            out.delays = {100};
            out.logicalW = still.logicalW;
        }
        return out;
    }
    int i = 0;
    // Animated WebP advances to the next frame as part of read(). Calling
    // jumpToImage/jumpToNextImage afterwards makes Qt's WebP handler fail
    // immediately, leaving callers with a one-frame "animation".
    while (reader.canRead() && i <= 256) {
        QImage img = reader.read();
        if (img.isNull()) break;
        const int lw = std::max(1, int(std::lround(img.width() * double(lh) / std::max(1, img.height()))));
        const int pw = std::max(1, int(std::lround(lw * dpr)));
        const int ph = std::max(1, int(std::lround(lh * dpr)));
        img = img.scaled(pw, ph, Qt::IgnoreAspectRatio, Qt::SmoothTransformation);
        QPixmap pm = QPixmap::fromImage(img);
        pm.setDevicePixelRatio(dpr);
        out.frames.push_back(pm);
        out.delays.push_back(std::max(30, reader.nextImageDelay() > 0 ? reader.nextImageDelay() : 100));
        out.logicalW = lw;
        ++i;
    }
    if (out.frames.isEmpty()) {
        auto still = loadStillPath(path, logicalH, dpr, uiScale);
        if (!still.pixmap.isNull()) {
            out.frames = {still.pixmap};
            out.delays = {100};
            out.logicalW = still.logicalW;
        }
    }
    return out;
}

class ToolSkin {
public:
    QString toolId;
    QString skinId = QStringLiteral("default");
    QString name = QStringLiteral("默认");
    QString rootPath;
    QJsonObject meta;

    static ToolSkin load(const QString& appRoot, const QString& tool, const QString& skin = QStringLiteral("default")) {
        ToolSkin s;
        s.toolId = tool;
        s.skinId = skin;
        s.rootPath = QDir(appRoot).filePath(QStringLiteral("resources/skin/%1/%2").arg(tool, skin));
        QFile f(QDir(s.rootPath).filePath(QStringLiteral("skin.json")));
        if (f.open(QIODevice::ReadOnly)) {
            const auto doc = QJsonDocument::fromJson(f.readAll());
            if (doc.isObject()) s.meta = doc.object();
        }
        s.name = s.meta.value(QStringLiteral("name")).toString(skin);
        return s;
    }

    SkinMetrics metrics() const {
        SkinMetrics m;
        const auto raw = meta.value(QStringLiteral("metrics")).toObject();
        m.fadeW = raw.value(QStringLiteral("fade_w")).toInt(22);
        m.padH = raw.value(QStringLiteral("pad_h")).toInt(12);
        m.padV = raw.value(QStringLiteral("pad_v")).toInt(7);
        m.giftIconSize = raw.value(QStringLiteral("gift_icon_size")).toInt(32);
        m.giftIconGap = raw.value(QStringLiteral("gift_icon_gap")).toInt(8);
        return m;
    }

    QJsonObject surfaceLayout(const QString& surface) const {
        return meta.value(QStringLiteral("layouts")).toObject().value(surface).toObject();
    }

    RoleTextStyle roleStyle(const QString& surface, const QString& role) const {
        const auto roles = surfaceLayout(surface).value(QStringLiteral("roles")).toObject();
        return RoleTextStyle::fromObject(roles.value(role).toObject());
    }

    int fitRole(const QString& surface, const QString& role, const QString& text,
                int maxW, int maxH, qreal scale = 1.0) const {
        const auto st = roleStyle(surface, role);
        // 皮肤 max_px×scale 为硬顶（礼物名等）。
        const int cap = st.maxPx > 0
            ? std::max(st.minPx, int(std::lround(st.maxPx * std::max(0.5, scale))))
            : std::max(st.minPx, maxH);
        const QString sample = text.isEmpty()
            ? (st.fitRef.isEmpty() ? QStringLiteral("国") : st.fitRef)
            : text;
        return fitFontPixelSize(sample, maxW, maxH, st.fontFamily, st.bold, cap, st.minPx);
    }

    // 按盒子高度填满（倒计时/标题）；不受皮肤 max_px 牵制，避免和礼物字号绑死。
    int fitRoleFill(const QString& surface, const QString& role, const QString& text,
                    int maxW, int maxH) const {
        const auto st = roleStyle(surface, role);
        const int cap = std::max(st.minPx, maxH);
        const QString sample = text.isEmpty()
            ? (st.fitRef.isEmpty() ? QStringLiteral("国") : st.fitRef)
            : text;
        return fitFontPixelSize(sample, maxW, maxH, st.fontFamily, st.bold, cap, st.minPx);
    }

    QColor color(const QString& key, const QColor& fallback = QColor(255, 255, 255)) const {
        return parseColor(meta.value(QStringLiteral("colors")).toObject().value(key), fallback);
    }

    void paintTextShadow(QPainter* p, const QRect& rect, const QString& text, const RoleTextStyle& style, int px) const {
        const QColor fill = color(QStringLiteral("text_fill"), QColor(255, 255, 255));
        const QColor shadow = color(QStringLiteral("text_shadow"), QColor(0, 0, 0, 160));
        p->setFont(style.font(px));
        p->setPen(shadow);
        p->drawText(rect.translated(1, 1), style.qtAlign(), text);
        p->setPen(fill);
        p->drawText(rect, style.qtAlign(), text);
    }

    LoadedStill presentImage(const QString& path, int boxH, qreal uiScale = 1.0) const {
        QImageReader probe(path);
        probe.setAutoTransform(true);
        if (probe.canRead() && probe.imageCount() > 1) {
            const LoadedAnim anim = loadAnimPath(path, boxH, screenDpr(), uiScale);
            if (!anim.frames.isEmpty()) {
                LoadedStill out;
                out.pixmap = anim.frames.first();
                out.logicalW = anim.logicalW;
                out.logicalH = anim.logicalH;
                return out;
            }
        }
        return loadStillPath(path, boxH, screenDpr(), uiScale);
    }

    LoadedAnim presentAnimation(const QString& path, int boxH, qreal uiScale = 1.0) const {
        return loadAnimPath(path, boxH, screenDpr(), uiScale);
    }

    QJsonObject chromeMeta() const {
        return meta.value(QStringLiteral("chrome")).toObject();
    }

    // 像素立体框皮肤（Nemuru 等）：JSON chrome.style=pixel_frame。
    bool usesPixelChrome() const {
        return chromeMeta().value(QStringLiteral("style")).toString()
            == QLatin1String("pixel_frame");
    }

    QJsonObject imageSpec(const QString& key) const {
        return meta.value(QStringLiteral("images")).toObject().value(key).toObject();
    }

    QString namedImagePath(const QString& key) const {
        const QString rel = imageSpec(key).value(QStringLiteral("file")).toString();
        if (rel.isEmpty()) return {};
        return QDir(rootPath).filePath(rel);
    }

    LoadedStill loadNamedStill(const QString& key, qreal uiScale = 1.0) const {
        const auto spec = imageSpec(key);
        const QString path = namedImagePath(key);
        if (path.isEmpty()) return {};
        const int lh = std::max(1, spec.value(QStringLiteral("logical_h")).toInt(16));
        return presentImage(path, lh, uiScale);
    }

    LoadedAnim loadNamedAnim(const QString& key, qreal uiScale = 1.0) const {
        const auto spec = imageSpec(key);
        const QString path = namedImagePath(key);
        if (path.isEmpty()) return {};
        const int lh = std::max(1, spec.value(QStringLiteral("logical_h")).toInt(16));
        return presentAnimation(path, lh, uiScale);
    }

    // 直角像素框：外黑阴影 + 高光/白边 + 描边 + 底色 + 内角线（对齐旧 util/nemuru_chrome）。
    void paintPixelChrome(QPainter* p, const QRect& r, qreal opacity = 1.0) const {
        if (!p || r.isEmpty()) return;
        const auto ch = chromeMeta();
        const int outW = std::max(0, ch.value(QStringLiteral("out_w")).toInt(2));
        const int depth = std::max(0, ch.value(QStringLiteral("depth")).toInt(2));
        const int bw = std::max(1, ch.value(QStringLiteral("border_w")).toInt(1));
        const QColor bg = color(QStringLiteral("chrome_bg"), QColor(162, 209, 236));
        const QColor border = color(QStringLiteral("chrome_border"), QColor(104, 167, 210));
        const QColor hi = color(QStringLiteral("chrome_hi"), QColor(210, 235, 250));
        const QColor white = color(QStringLiteral("chrome_white"), QColor(255, 255, 255));
        const QColor black = color(QStringLiteral("chrome_black"), QColor(0, 0, 0));

        p->save();
        p->setOpacity(opacity);
        p->setRenderHint(QPainter::Antialiasing, false);
        p->setPen(Qt::NoPen);
        const int x = r.x(), y = r.y(), w = r.width(), h = r.height();

        p->setBrush(black);
        p->drawRect(x - outW, y - outW, w + 2 * outW + depth, h + 2 * outW + depth);
        p->setBrush(hi);
        p->drawRect(x - 2, y - 2, w + 1, h + 1);
        p->setBrush(white);
        p->drawRect(x - 1, y - 1, w, h);
        p->setBrush(border);
        p->drawRect(x, y, w, h);
        p->setBrush(bg);
        p->drawRect(x + bw, y + bw, std::max(0, w - 2 * bw), std::max(0, h - 2 * bw));
        p->setBrush(white);
        p->drawRect(x + bw, y + bw, std::max(0, w - 2 * bw), 1);
        p->drawRect(x + bw, y + bw, 1, std::max(0, h - 2 * bw));
        p->setBrush(black);
        p->drawRect(x + bw, y + h - bw - 1, std::max(0, w - 2 * bw), 1);
        p->drawRect(x + w - bw - 1, y + bw, 1, std::max(0, h - 2 * bw));
        p->restore();
    }
};

struct SkinEntry {
    QString id;
    QString name;
};

// 皮肤目录：resources/skin/<tool>/<id>/skin.json。始终保证有 default 一项。
static QVector<SkinEntry> listSkins(const QString& appRoot, const QString& tool) {
    QVector<SkinEntry> out;
    const QDir toolDir(QDir(appRoot).filePath(QStringLiteral("resources/skin/%1").arg(tool)));
    const QStringList ids = toolDir.entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name);
    for (const QString& id : ids) {
        // 没有 skin.json 的目录不是皮肤（例如残留的空目录），忽略以免出现幽灵条目。
        QFile f(toolDir.filePath(id + QStringLiteral("/skin.json")));
        if (!f.open(QIODevice::ReadOnly)) continue;
        const auto doc = QJsonDocument::fromJson(f.readAll());
        if (!doc.isObject()) continue;
        out.append(SkinEntry{id, doc.object().value(QStringLiteral("name")).toString(id)});
    }
    if (out.isEmpty()) out.append(SkinEntry{QStringLiteral("default"), QStringLiteral("默认")});
    return out;
}

static QString skinConfigKey(const QString& tool) {
    return QStringLiteral("skin.%1").arg(tool);
}

}  // namespace liveaio::resources
