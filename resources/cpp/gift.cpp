#include <QDir>
#include <QFile>
#include <QHash>
#include <QImageReader>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QObject>
#include <QPixmap>
#include <QPointer>
#include <QString>
#include <QStringList>
#include <QTimer>

#include <algorithm>
#include <functional>

namespace liveaio::resources {

// Gift catalog + icon paths under resources/gift/.
// Go (resources/gift.go) owns business diamonds/id; C++ uses this for UI icons.

inline QString giftDir(const QString& appRoot) {
    return QDir(appRoot).filePath(QStringLiteral("resources/gift"));
}

inline QString giftInfoPath(const QString& appRoot) {
    return QDir(giftDir(appRoot)).filePath(QStringLiteral("gift_info.json"));
}

inline QString giftIconDir(const QString& appRoot) {
    return QDir(giftDir(appRoot)).filePath(QStringLiteral("icon"));
}

struct GiftCatalogState {
    QJsonObject gifts;
    bool loaded = false;
};

inline GiftCatalogState& giftCatalogState() {
    static GiftCatalogState state;
    return state;
}

inline void ensureGiftCatalog(const QString& appRoot) {
    GiftCatalogState& state = giftCatalogState();
    if (state.loaded && !state.gifts.isEmpty()) return;
    QFile info(giftInfoPath(appRoot));
    if (info.open(QIODevice::ReadOnly)) {
        const QJsonObject obj = QJsonDocument::fromJson(info.readAll()).object();
        if (!obj.isEmpty()) {
            state.gifts = obj;
            state.loaded = true;
            return;
        }
    }
    // 路径未就绪时不锁死 loaded，允许下次用正确 appRoot 再读。
    if (!state.loaded) state.loaded = false;
}

inline int giftIdFromCatalog(const QString& appRoot, const QString& giftName) {
    ensureGiftCatalog(appRoot);
    return giftCatalogState().gifts.value(giftName).toObject()
        .value(QStringLiteral("gift_id")).toInt();
}

inline int giftPriceFromCatalog(const QString& appRoot, const QString& giftName) {
    ensureGiftCatalog(appRoot);
    return giftCatalogState().gifts.value(giftName).toObject()
        .value(QStringLiteral("price")).toInt();
}

// ResolveIconPath: name.ext then gift_id.ext (same rule as Go resources.IconPath).
inline QString resolveGiftIconPath(const QString& appRoot, const QString& giftName) {
    const QString dir = giftIconDir(appRoot);
    static const QStringList exts = {
        QStringLiteral(".webp"), QStringLiteral(".png"), QStringLiteral(".jpg"),
        QStringLiteral(".jpeg"), QStringLiteral(".gif"),
    };
    for (const auto& ext : exts) {
        const QString p = QDir(dir).filePath(giftName + ext);
        if (QFile::exists(p)) return p;
    }
    const int id = giftIdFromCatalog(appRoot, giftName);
    if (id > 0) {
        for (const auto& ext : exts) {
            const QString p = QDir(dir).filePath(QString::number(id) + ext);
            if (QFile::exists(p)) return p;
        }
    }
    return {};
}

inline int giftIconFrameCount(const QString& path) {
    if (path.isEmpty()) return 0;
    QImageReader reader(path);
    reader.setAutoTransform(true);
    if (!reader.canRead()) return 0;
    const int count = reader.imageCount();
    return count > 0 ? count : 1;
}

inline bool giftIconIsAnimated(const QString& path) {
    return giftIconFrameCount(path) > 1;
}

// 礼物名列表（gift_info.json 键，按名称排序），首次调用时同步读盘。
inline const QStringList& giftNamesCached(const QString& appRoot) {
    static QStringList names;
    static QString loadedRoot;
    ensureGiftCatalog(appRoot);
    if (loadedRoot == appRoot && !names.isEmpty()) return names;
    names = giftCatalogState().gifts.keys();
    names.sort();
    loadedRoot = appRoot;
    return names;
}

inline bool giftHasIconFile(const QString& appRoot, const QString& giftName) {
    return !resolveGiftIconPath(appRoot, giftName).isEmpty();
}

// 选择器：仅列出磁盘上能解析到图标的礼物（gift_info≈1248，icon 文件≈410）。
inline QStringList giftNamesWithIconsCached(const QString& appRoot) {
    QStringList out;
    for (const QString& n : giftNamesCached(appRoot)) {
        if (giftHasIconFile(appRoot, n)) out.append(n);
    }
    return out;
}

// 异步读 gift_info.json（工作线程读盘，回主线程写 catalog 状态）。
inline void ensureGiftCatalogAsync(const QString& appRoot, QObject* context,
                                   std::function<void()> onReady) {
    if (giftCatalogState().loaded && !giftCatalogState().gifts.isEmpty()) {
        if (onReady) onReady();
        return;
    }
    liveaio::util::readJsonAsync(giftInfoPath(appRoot), context, [onReady](QJsonObject obj) {
        GiftCatalogState& st = giftCatalogState();
        if (!obj.isEmpty()) {
            st.gifts = obj;
            st.loaded = true;
        }
        if (onReady) onReady();
    });
}

inline QHash<QString, QPixmap>& giftHighResCache() {
    static QHash<QString, QPixmap> cache;
    return cache;
}

inline QHash<QString, QPixmap>& giftThumbCache() {
    static QHash<QString, QPixmap> cache;
    return cache;
}

inline QHash<QString, QPixmap>& giftAnimFrameCache() {
    static QHash<QString, QPixmap> cache;
    return cache;
}

// 静态缓存无界会把进程内存顶高；超限时丢掉一半旧项。
static constexpr int kMaxGiftHighRes = 24;
static constexpr int kMaxGiftThumb = 96;
static constexpr int kMaxGiftAnimFrames = 180;

inline void trimGiftCache(QHash<QString, QPixmap>& cache, int maxEntries) {
    if (cache.size() <= maxEntries) return;
    const int drop = cache.size() / 2;
    auto it = cache.begin();
    for (int i = 0; i < drop && it != cache.end(); ++i) it = cache.erase(it);
}

inline QString giftAnimFrameKey(const QString& path, int index, int side) {
    return path + QLatin1Char('@') + QString::number(index) + QLatin1Char('@') + QString::number(side);
}

// 单帧高清解码（动图逐帧懒加载用）。
inline bool decodeGiftAnimFrame(const QString& path, int index, int side, QPixmap* out,
                                int* delayMs = nullptr) {
    if (!out || path.isEmpty() || side <= 0 || index < 0) return false;
    const QString key = giftAnimFrameKey(path, index, side);
    auto& cache = giftAnimFrameCache();
    const auto cached = cache.constFind(key);
    if (cached != cache.constEnd()) {
        *out = cached.value();
        if (delayMs) *delayMs = 100;
        return !out->isNull();
    }

    QImageReader reader(path);
    reader.setAutoTransform(true);
    if (index > 0 && !reader.jumpToImage(index)) return false;
    QImage img = reader.read();
    if (img.isNull()) return false;
    *out = QPixmap::fromImage(img).scaled(side, side, Qt::KeepAspectRatio,
                                          Qt::SmoothTransformation);
    cache.insert(key, *out);
    trimGiftCache(cache, kMaxGiftAnimFrames);
    if (delayMs) {
        *delayMs = std::max(30, reader.nextImageDelay() > 0 ? reader.nextImageDelay() : 100);
    }
    return !out->isNull();
}

struct GiftAnimSequence {
    QVector<QPixmap> frames;
    QVector<int> delays;
};

// 弹幕等场景：只同步解码第一帧，其余帧由播放器后台补齐。
inline GiftAnimSequence loadGiftAnimHigh(const QString& appRoot, const QString& giftName, int side) {
    GiftAnimSequence out;
    if (giftName.isEmpty() || side <= 0) return out;
    const QString path = resolveGiftIconPath(appRoot, giftName);
    if (path.isEmpty() || !giftIconIsAnimated(path)) return out;
    QPixmap frame;
    int delay = 100;
    if (!decodeGiftAnimFrame(path, 0, side, &frame, &delay)) return out;
    out.frames.push_back(frame);
    out.delays.push_back(delay);
    return out;
}

// 规则格 / 悬浮窗：按目标边长高清缩放并缓存（静图）。
inline QPixmap loadGiftPixmapHigh(const QString& appRoot, const QString& giftName, int side) {
    if (giftName.isEmpty() || side <= 0) return {};
    const QString key = giftName + QLatin1Char('@') + QString::number(side);
    auto& cache = giftHighResCache();
    const auto it = cache.constFind(key);
    if (it != cache.constEnd()) return it.value();

    QPixmap out;
    const QString path = resolveGiftIconPath(appRoot, giftName);
    if (!path.isEmpty() && !giftIconIsAnimated(path)) {
        QPixmap raw(path);
        if (!raw.isNull()) {
            out = raw.scaled(side, side, Qt::KeepAspectRatio, Qt::SmoothTransformation);
        }
    }
    cache.insert(key, out);
    trimGiftCache(cache, kMaxGiftHighRes);
    return out;
}

// 礼物选择列表：解码时直接缩到目标尺寸；失败则整图再缩放（动图/部分格式 setScaledSize 会空图）。
inline QPixmap loadGiftPixmapThumb(const QString& appRoot, const QString& giftName, int side) {
    if (giftName.isEmpty() || side <= 0) return {};
    const QString key = giftName + QLatin1Char('@') + QString::number(side);
    auto& cache = giftThumbCache();
    const auto it = cache.constFind(key);
    if (it != cache.constEnd()) return it.value();

    QPixmap out;
    const QString path = resolveGiftIconPath(appRoot, giftName);
    if (!path.isEmpty()) {
        {
            QImageReader reader(path);
            reader.setAutoTransform(true);
            reader.setScaledSize(QSize(side, side));
            const QImage img = reader.read();
            if (!img.isNull()) out = QPixmap::fromImage(img);
        }
        if (out.isNull()) {
            QImageReader reader(path);
            reader.setAutoTransform(true);
            const QImage img = reader.read();
            if (!img.isNull()) {
                out = QPixmap::fromImage(img).scaled(side, side, Qt::KeepAspectRatio,
                                                     Qt::SmoothTransformation);
            }
        }
        if (out.isNull()) {
            QPixmap raw(path);
            if (!raw.isNull()) {
                out = raw.scaled(side, side, Qt::KeepAspectRatio, Qt::SmoothTransformation);
            }
        }
    }
    // 空图不入缓存，避免 appRoot 未就绪时一次失败后永久空白。
    if (!out.isNull()) {
        cache.insert(key, out);
        trimGiftCache(cache, kMaxGiftThumb);
    }
    return out;
}

// 对齐旧 SkinAnimPlayer：首帧立显，其余帧后台解码，不阻塞 UI。
class GiftAnimPlayer final : public QObject {
public:
    explicit GiftAnimPlayer(QLabel* label) : QObject(label), label_(label) {
        animTimer_.setTimerType(Qt::PreciseTimer);
        // Context must be `this` (not the label): if the label is reparented/torn
        // down while a tick is queued, label-context slots can still fire on a
        // half-dead player and AV (seen when leaf rules rebuild mid-session).
        QObject::connect(&animTimer_, &QTimer::timeout, this, [this]() { onAnimTick(); });
        QObject::connect(&loadTimer_, &QTimer::timeout, this, [this]() { decodeNextFrame(); });
    }

    void setAnimated(const QString& path, int side) {
        stop();
        if (!label_ || path.isEmpty() || side <= 0) return;

        animPath_ = path;
        side_ = side;
        frameCount_ = giftIconFrameCount(path);
        if (frameCount_ <= 1) return;

        decodeFrameAt(0);
        if (frames_.isEmpty()) {
            label_->setPixmap(QPixmap());
            label_->setText(QStringLiteral("·"));
            return;
        }
        label_->setText(QString());
        label_->setPixmap(frames_.first());
        index_ = 0;
        if (frames_.size() > 1) {
            animTimer_.start(std::max(30, delays_.value(0, 100)));
        }
        if (static_cast<int>(frames_.size()) < frameCount_) {
            loadTimer_.start(30);
        }
    }

    void stop() {
        animTimer_.stop();
        loadTimer_.stop();
        frames_.clear();
        delays_.clear();
        animPath_.clear();
        side_ = 0;
        frameCount_ = 0;
        index_ = 0;
    }

private:
    void decodeFrameAt(int index) {
        if (animPath_.isEmpty() || side_ <= 0 || index < 0) return;
        const QString key = giftAnimFrameKey(animPath_, index, side_);
        auto& cache = giftAnimFrameCache();
        if (cache.contains(key)) {
            frames_.push_back(cache.value(key));
            delays_.push_back(100);
            return;
        }

        QPixmap frame;
        int delay = 100;
        if (!decodeGiftAnimFrame(animPath_, index, side_, &frame, &delay)) {
            frameCount_ = static_cast<int>(frames_.size());
            return;
        }
        frames_.push_back(frame);
        delays_.push_back(delay);
    }

    void decodeNextFrame() {
        if (static_cast<int>(frames_.size()) >= frameCount_) {
            loadTimer_.stop();
            return;
        }
        decodeFrameAt(static_cast<int>(frames_.size()));
        if (static_cast<int>(frames_.size()) >= frameCount_) loadTimer_.stop();
    }

    void onAnimTick() {
        if (!label_ || frames_.size() <= 1) return;
        index_ = (index_ + 1) % frames_.size();
        label_->setPixmap(frames_.at(index_));
        animTimer_.start(std::max(30, delays_.value(index_, 100)));
    }

    QPointer<QLabel> label_;
    QTimer animTimer_;
    QTimer loadTimer_;
    QString animPath_;
    int side_ = 0;
    int frameCount_ = 0;
    QVector<QPixmap> frames_;
    QVector<int> delays_;
    int index_ = 0;
};

inline GiftAnimPlayer* findGiftAnimPlayer(QLabel* label) {
    if (!label) return nullptr;
    for (QObject* child : label->children()) {
        if (auto* player = dynamic_cast<GiftAnimPlayer*>(child)) return player;
    }
    return nullptr;
}

inline GiftAnimPlayer* giftAnimPlayerFor(QLabel* label) {
    if (!label) return nullptr;
    if (auto* existing = findGiftAnimPlayer(label)) return existing;
    return new GiftAnimPlayer(label);
}

// 静图高清 / 动图逐帧播放；缩略图仅静图解码。
inline void setGiftIconOnLabel(QLabel* label, const QString& appRoot, const QString& giftName,
                               int side, bool thumb) {
    if (!label) return;
    GiftAnimPlayer* player = giftAnimPlayerFor(label);

    if (giftName.isEmpty() || side <= 0) {
        if (player) player->stop();
        label->setPixmap(QPixmap());
        label->setText(QStringLiteral("·"));
        return;
    }

    if (thumb) {
        if (player) player->stop();
        const QPixmap px = loadGiftPixmapThumb(appRoot, giftName, side);
        if (!px.isNull()) {
            label->setPixmap(px);
            label->setText(QString());
        } else {
            label->setPixmap(QPixmap());
            label->setText(QStringLiteral("·"));
        }
        return;
    }

    const QString path = resolveGiftIconPath(appRoot, giftName);
    if (path.isEmpty()) {
        if (player) player->stop();
        label->setPixmap(QPixmap());
        label->setText(QStringLiteral("·"));
        return;
    }

    if (giftIconIsAnimated(path)) {
        label->setText(QString());
        if (player) player->setAnimated(path, side);
        return;
    }

    if (player) player->stop();
    const QPixmap px = loadGiftPixmapHigh(appRoot, giftName, side);
    if (!px.isNull()) {
        label->setPixmap(px);
        label->setText(QString());
    } else {
        label->setPixmap(QPixmap());
        label->setText(QStringLiteral("·"));
    }
}

// 释放标签上的动图播放器与像素图（关悬浮窗时用）。
inline void clearGiftIconOnLabel(QLabel* label) {
    if (!label) return;
    if (auto* player = findGiftAnimPlayer(label)) player->stop();
    label->setPixmap(QPixmap());
    label->setText(QString());
}

inline void releaseGiftThumbCache() { giftThumbCache().clear(); }

inline void releaseGiftPixmapCaches() {
    giftHighResCache().clear();
    giftThumbCache().clear();
    giftAnimFrameCache().clear();
}

}  // namespace liveaio::resources
