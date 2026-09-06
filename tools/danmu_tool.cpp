// tools/danmu_tool.cpp — 弹幕机，对齐旧 PySide danmu_tool.py。
// DanmuToolWindow：448×520 控制面板，5 个顶部标签页。
// DanmuOverlayWindow：透明悬浮弹幕窗，顶栏/三侧边框以左上圆圈为圆心做波纹展开。

namespace liveaio::tools {

static constexpr int kDanmuFadeInMs = 300;
static constexpr int kDanmuStayMs = 3000;
static constexpr int kDanmuFadeOutMs = 500;

static const QString kDanmuGeoKey = QStringLiteral("danmu_window_geometry");

static ToolSkin activeDanmuSkin() {
    const QString id = configValue(liveaio::resources::skinConfigKey(QStringLiteral("danmu")),
                                  QStringLiteral("default")).toString();
    return ToolSkin::load(g_appRoot, QStringLiteral("danmu"),
                          id.isEmpty() ? QStringLiteral("default") : id);
}

static void pushSavedDanmuSettings() {
    if (!g_sendPacket) return;
    g_sendPacket(QJsonObject{
        {QStringLiteral("op"), QStringLiteral("tool.danmu.set")},
        {QStringLiteral("settings"), QJsonObject{
            {QStringLiteral("danmu_chat_on"),
             configValue(QStringLiteral("danmu_chat_on"), true).toBool()},
            {QStringLiteral("danmu_gift_on"),
             configValue(QStringLiteral("danmu_gift_on"), true).toBool()},
            {QStringLiteral("danmu_gift_min_diamonds"),
             configValue(QStringLiteral("danmu_gift_min_diamonds"), 0).toInt()},
            {QStringLiteral("danmu_follow_on"),
             configValue(QStringLiteral("danmu_follow_on"), true).toBool()},
            {QStringLiteral("danmu_like_on"),
             configValue(QStringLiteral("danmu_like_on"), true).toBool()},
            {QStringLiteral("danmu_like_threshold"),
             configValue(QStringLiteral("danmu_like_threshold"), 10).toInt()},
            {QStringLiteral("danmu_like_accumulate"),
             configValue(QStringLiteral("danmu_like_accumulate"), true).toBool()},
        }},
    });
}

static void showTutorialDialog(QWidget* parent) {
    QDir imageDir(QDir(g_appRoot).filePath(QStringLiteral("resources/image")));
    if (!imageDir.exists()) {
        imageDir.setPath(QDir(g_appRoot).filePath(QStringLiteral("image")));
    }
    const QStringList files = imageDir.entryList(
        {QStringLiteral("*.png"), QStringLiteral("*.jpg")}, QDir::Files, QDir::Name);
    if (files.isEmpty()) {
        QMessageBox::warning(parent, QStringLiteral("教程"),
                             QStringLiteral("未找到 resources/image 中的教程图片。"));
        return;
    }
    const QString path = imageDir.filePath(files.first());
    QPixmap pix(path);
    if (pix.isNull()) {
        QMessageBox::warning(parent, QStringLiteral("教程"),
                             QStringLiteral("无法加载教程图片：%1").arg(path));
        return;
    }

    auto* dlg = new QDialog(parent);
    dlg->setAttribute(Qt::WA_DeleteOnClose, true);
    dlg->setWindowTitle(QStringLiteral("教程"));
    dlg->setModal(false);
    auto* lay = new QVBoxLayout(dlg);
    lay->setContentsMargins(12, 12, 12, 12);
    lay->setSpacing(10);

    int maxW = 720;
    int maxH = 480;
    if (auto* screen = dlg->screen()) {
        const QRect avail = screen->availableGeometry();
        maxW = qMin(static_cast<int>(avail.width() * 0.68), 760);
        maxH = qMin(static_cast<int>(avail.height() * 0.62), 520);
    }
    const QPixmap scaled = pix.scaled(maxW, maxH, Qt::KeepAspectRatio, Qt::SmoothTransformation);

    auto* img = new QLabel(dlg);
    img->setAlignment(Qt::AlignCenter);
    img->setPixmap(scaled);
    img->setFixedSize(scaled.size());
    lay->addWidget(img, 0, Qt::AlignCenter);

    auto* closeBtn = new QPushButton(QStringLiteral("关闭"), dlg);
    QObject::connect(closeBtn, &QPushButton::clicked, dlg, &QDialog::close);
    lay->addWidget(closeBtn, 0, Qt::AlignCenter);

    dlg->adjustSize();
    dlg->show();
}

// ─────────────────────────────────────────────
// 弹幕气泡（皮肤驱动绘制）
// ─────────────────────────────────────────────
class DanmuBubble final : public QWidget {
public:
    DanmuBubble(const QString& kind, const QString& user, const QString& text,
                const QString& gift, const ToolSkin& skin, QWidget* parent,
                std::function<void(DanmuBubble*)> onRelease = nullptr)
        : QWidget(parent), skin_(skin), kind_(kind), user_(user), text_(text), gift_(gift),
          onRelease_(std::move(onRelease)) {
        setAttribute(Qt::WA_TransparentForMouseEvents);
        loadGiftIcon();

        fade_ = new QVariantAnimation(this);
        fade_->setDuration(kDanmuFadeInMs);
        fade_->setStartValue(0.0);
        fade_->setEndValue(1.0);
        QObject::connect(fade_, &QVariantAnimation::valueChanged, this, [this](const QVariant& v) {
            opacity_ = v.toReal();
            update();
        });
        QObject::connect(fade_, &QVariantAnimation::finished, this, [this]() {
            if (phase_ < 0) return;
            if (phase_ == 0) {
                phase_ = 1;
                QTimer::singleShot(kDanmuStayMs, this, [this]() {
                    if (phase_ != 1) return;
                    phase_ = 2;
                    fade_->setDuration(kDanmuFadeOutMs);
                    fade_->setStartValue(1.0);
                    fade_->setEndValue(0.0);
                    fade_->start();
                });
            } else if (phase_ == 2) {
                if (onRelease_) onRelease_(this);
                else deleteLater();
            }
        });
        relayout();
        fade_->start();
    }

    void reuse(const QString& kind, const QString& user, const QString& text,
               const QString& gift, const ToolSkin& skin) {
        kind_ = kind;
        user_ = user;
        text_ = text;
        gift_ = gift;
        skin_ = skin;
        prepareForPool();
        phase_ = 0;
        opacity_ = 0;
        loadGiftIcon();
        relayout();
        fade_->stop();
        fade_->setDuration(kDanmuFadeInMs);
        fade_->setStartValue(0.0);
        fade_->setEndValue(1.0);
        show();
        fade_->start();
    }

    void prepareForPool() {
        phase_ = -1;
        if (fade_) fade_->stop();
        stopGiftAnim();
        stopChromeAnim();
        giftPm_ = QPixmap();
        giftW_ = 0;
        giftH_ = 0;
        opacity_ = 0;
    }

    void refreshSkin(const ToolSkin& skin) {
        skin_ = skin;
        chromeReady_ = false;
        loadGiftIcon();
        relayout();
    }

    void relayout() {
        const auto m = skin_.metrics();
        const auto userSt = skin_.roleStyle(QStringLiteral("bubble"), QStringLiteral("user"));
        const auto bodySt = skin_.roleStyle(
            kind_ == QLatin1String("gift") ? QStringLiteral("gift_bubble") : QStringLiteral("bubble"),
            QStringLiteral("body"));
        const int maxChars = std::max(4, bodySt.maxChars);
        const int minChars = std::max(1, bodySt.minChars);
        const int chars = std::clamp(static_cast<int>(text_.size()), minChars, maxChars);
        QFontMetrics ufm(userSt.font());
        QFontMetrics bfm(bodySt.font());
        const int textW = std::max(ufm.horizontalAdvance(user_),
                                   bfm.horizontalAdvance(QString(chars, QChar(0x4E2D))))
                          + bodySt.slackPx;
        const int contentW = textW + (giftPm_.isNull() ? 0 : (giftW_ + m.giftIconGap));
        int innerW = contentW + m.padH * 2 + m.fadeW;
        int innerH = std::max(bfm.height() + ufm.height() + 4, giftH_) + m.padV * 2;

        if (!skin_.usesPixelChrome()) {
            stopChromeAnim();
            contentRect_ = QRect(0, 0, innerW, innerH);
            setFixedSize(innerW, innerH);
            update();
            return;
        }

        ensureChromeMedia();
        const auto ch = skin_.chromeMeta();
        const auto deco = ch.value(QStringLiteral("deco")).toObject();
        const int outW = std::max(0, ch.value(QStringLiteral("out_w")).toInt(2));
        const int depth = std::max(0, ch.value(QStringLiteral("depth")).toInt(2));
        const int edge = outW + 2;
        const int busyOverX = std::max(1, int(std::lround(deco.value(QStringLiteral("busy_over_x")).toDouble(10))));
        const int logoOverY = deco.value(QStringLiteral("logo_over_y")).toInt(2);
        const int winGap = deco.value(QStringLiteral("win_gap")).toInt(2);
        const int busyW = std::max(1, busyAnim_.logicalW);
        const int busyH = std::max(1, busyAnim_.logicalH);
        const int logoW = std::max(1, logoStill_.logicalW);
        const int logoH = std::max(1, logoStill_.logicalH);
        const int cornerW = std::max(1, cornerAnim_.logicalW);
        const int cornerH = std::max(1, cornerAnim_.logicalH);
        const int winH = std::max({winMin_.logicalH, winMax_.logicalH, winClose_.logicalH, 18});
        const int winTotalW = winMin_.logicalW + winMax_.logicalW + winClose_.logicalW
            + winGap * 2;
        innerW = std::max(innerW, busyW + logoW + winTotalW);
        const int topPad = std::max({edge, logoH - logoOverY, winH});
        const int leftExtra = std::max(edge, busyOverX);
        const int rightExtra = std::max(cornerW / 2, outW + depth);
        const int botExtra = std::max(cornerH / 2, outW + depth);
        const int totalW = leftExtra + innerW + rightExtra;
        const int totalH = topPad + innerH + botExtra;
        contentRect_ = QRect(leftExtra, topPad, innerW, innerH);
        busyRect_ = QRect(leftExtra - busyOverX, std::max(0, topPad + logoOverY - logoH),
                          busyW, busyH);
        logoRect_ = QRect(busyRect_.right() + 1, busyRect_.y(), logoW, logoH);
        winRects_[0] = QRect(contentRect_.right() - winTotalW + 1,
                             topPad - winH + (winH - winMin_.logicalH),
                             winMin_.logicalW, winMin_.logicalH);
        winRects_[1] = QRect(winRects_[0].right() + 1 + winGap,
                             topPad - winH + (winH - winMax_.logicalH),
                             winMax_.logicalW, winMax_.logicalH);
        winRects_[2] = QRect(winRects_[1].right() + 1 + winGap,
                             topPad - winH + (winH - winClose_.logicalH),
                             winClose_.logicalW, winClose_.logicalH);
        cornerRect_ = QRect(contentRect_.right() - cornerW / 2,
                            contentRect_.bottom() - cornerH / 2,
                            cornerW, cornerH);
        setFixedSize(totalW, totalH);
        startChromeAnim();
        update();
    }

protected:
    void paintEvent(QPaintEvent*) override {
        QPainter p(this);
        p.setOpacity(opacity_);
        const auto m = skin_.metrics();

        if (skin_.usesPixelChrome()) {
            p.setRenderHint(QPainter::Antialiasing, false);
            skin_.paintPixelChrome(&p, contentRect_, 1.0);
            auto blit = [&](const QPixmap& pm, const QRect& r) {
                if (pm.isNull() || r.isEmpty()) return;
                p.drawPixmap(r, pm);
            };
            if (!busyAnim_.frames.isEmpty()) {
                blit(busyAnim_.frames.value(busyFrame_ % busyAnim_.frames.size()), busyRect_);
            }
            blit(logoStill_.pixmap, logoRect_);
            blit(winMin_.pixmap, winRects_[0]);
            blit(winMax_.pixmap, winRects_[1]);
            blit(winClose_.pixmap, winRects_[2]);
            if (!cornerAnim_.frames.isEmpty()) {
                blit(cornerAnim_.frames.value(cornerFrame_ % cornerAnim_.frames.size()), cornerRect_);
            }
        } else {
            p.setRenderHint(QPainter::Antialiasing, true);
            QLinearGradient g(0, 0, width(), 0);
            const QColor mid = skin_.color(QStringLiteral("gradient_mid"), QColor(0, 0, 0, 128));
            const QColor edge = skin_.color(QStringLiteral("gradient_edge"), QColor(0, 0, 0, 0));
            g.setColorAt(0.0, edge);
            g.setColorAt(0.5, mid);
            g.setColorAt(1.0, edge);
            p.fillRect(rect(), g);
            contentRect_ = rect();
        }

        int x = contentRect_.x() + m.padH + m.fadeW / 2;
        int y = contentRect_.y() + m.padV;
        if (!giftPm_.isNull()) {
            p.drawPixmap(QRect(x, y + (contentRect_.height() - m.padV * 2 - giftH_) / 2,
                               giftW_, giftH_), giftPm_);
            x += giftW_ + m.giftIconGap;
        }
        const auto userSt = skin_.roleStyle(QStringLiteral("bubble"), QStringLiteral("user"));
        const auto bodySt = skin_.roleStyle(
            kind_ == QLatin1String("gift") ? QStringLiteral("gift_bubble") : QStringLiteral("bubble"),
            QStringLiteral("body"));
        p.setFont(userSt.font());
        p.setPen(skin_.color(QStringLiteral("user"), QColor(255, 255, 255, 200)));
        p.drawText(x, y + QFontMetrics(userSt.font()).ascent(), user_);
        y += QFontMetrics(userSt.font()).height() + 2;
        p.setFont(bodySt.font());
        p.setPen(skin_.color(QStringLiteral("body"), QColor(255, 255, 255)));
        p.drawText(x, y + QFontMetrics(bodySt.font()).ascent(), text_);
    }

    void ensureChromeMedia() {
        if (chromeReady_ && chromeSkinKey_ == skin_.skinId) return;
        stopChromeAnim();
        chromeSkinKey_ = skin_.skinId;
        busyAnim_ = skin_.loadNamedAnim(QStringLiteral("busy"));
        cornerAnim_ = skin_.loadNamedAnim(QStringLiteral("corner"));
        logoStill_ = skin_.loadNamedStill(QStringLiteral("logo"));
        winMin_ = skin_.loadNamedStill(QStringLiteral("win_min"));
        winMax_ = skin_.loadNamedStill(QStringLiteral("win_max"));
        winClose_ = skin_.loadNamedStill(QStringLiteral("win_close"));
        busyFrame_ = 0;
        cornerFrame_ = 0;
        chromeReady_ = true;
    }

    void startChromeAnim() {
        stopChromeAnim();
        if (!skin_.usesPixelChrome()) return;
        if (busyAnim_.frames.size() > 1 || cornerAnim_.frames.size() > 1) {
            chromeAnimTimer_ = new QTimer(this);
            chromeAnimTimer_->setTimerType(Qt::PreciseTimer);
            QObject::connect(chromeAnimTimer_, &QTimer::timeout, this, [this]() {
                bool dirty = false;
                if (busyAnim_.frames.size() > 1) {
                    busyFrame_ = (busyFrame_ + 1) % busyAnim_.frames.size();
                    dirty = true;
                }
                if (cornerAnim_.frames.size() > 1) {
                    cornerFrame_ = (cornerFrame_ + 1) % cornerAnim_.frames.size();
                    dirty = true;
                }
                if (dirty) update();
            });
            const int delay = std::max(30, busyAnim_.delays.value(0, 100));
            chromeAnimTimer_->start(delay);
        }
    }

    void stopChromeAnim() {
        if (chromeAnimTimer_) {
            chromeAnimTimer_->stop();
            chromeAnimTimer_->deleteLater();
            chromeAnimTimer_ = nullptr;
        }
    }

    void stopGiftAnim() {
        if (giftAnimTimer_) {
            giftAnimTimer_->stop();
            giftAnimTimer_->deleteLater();
            giftAnimTimer_ = nullptr;
        }
        if (giftLoadTimer_) {
            giftLoadTimer_->stop();
            giftLoadTimer_->deleteLater();
            giftLoadTimer_ = nullptr;
        }
        giftFrames_.clear();
        giftDelays_.clear();
        giftFrameIdx_ = 0;
        giftAnimPath_.clear();
        giftFrameCount_ = 0;
        giftAnimSide_ = 0;
    }

    void decodeNextGiftFrame() {
        if (giftAnimPath_.isEmpty() || giftAnimSide_ <= 0) {
            if (giftLoadTimer_) giftLoadTimer_->stop();
            return;
        }
        const int next = giftFrames_.size();
        if (next >= giftFrameCount_) {
            if (giftLoadTimer_) giftLoadTimer_->stop();
            return;
        }
        QPixmap frame;
        int delay = 100;
        if (!liveaio::resources::decodeGiftAnimFrame(
                giftAnimPath_, next, giftAnimSide_, &frame, &delay)) {
            giftFrameCount_ = giftFrames_.size();
            if (giftLoadTimer_) giftLoadTimer_->stop();
            return;
        }
        giftFrames_.push_back(frame);
        giftDelays_.push_back(delay);
        if (giftFrames_.size() >= giftFrameCount_ && giftLoadTimer_) giftLoadTimer_->stop();
    }

    void loadGiftIcon() {
        stopGiftAnim();
        giftPm_ = QPixmap();
        giftW_ = 0;
        giftH_ = 0;
        if (kind_ != QLatin1String("gift") || gift_.isEmpty()) return;
        const QString icon = resolveGiftIconPath(g_appRoot, gift_);
        if (icon.isEmpty()) return;

        const int side = skin_.metrics().giftIconSize;
        if (liveaio::resources::giftIconIsAnimated(icon)) {
            giftAnimPath_ = icon;
            giftAnimSide_ = side;
            giftFrameCount_ = liveaio::resources::giftIconFrameCount(icon);

            QPixmap frame;
            int delay = 100;
            if (liveaio::resources::decodeGiftAnimFrame(icon, 0, side, &frame, &delay)) {
                giftFrames_.push_back(frame);
                giftDelays_.push_back(delay);
                giftPm_ = frame;
                const qreal dpr = giftPm_.devicePixelRatio();
                giftW_ = std::max(1, int(std::lround(giftPm_.width() / dpr)));
                giftH_ = std::max(1, int(std::lround(giftPm_.height() / dpr)));

                if (giftFrameCount_ > 1) {
                    giftAnimTimer_ = new QTimer(this);
                    giftAnimTimer_->setTimerType(Qt::PreciseTimer);
                    QObject::connect(giftAnimTimer_, &QTimer::timeout, this, [this]() {
                        if (giftFrames_.size() <= 1) return;
                        giftFrameIdx_ = (giftFrameIdx_ + 1) % giftFrames_.size();
                        giftPm_ = giftFrames_.at(giftFrameIdx_);
                        update();
                        if (giftAnimTimer_) {
                            giftAnimTimer_->start(
                                std::max(30, giftDelays_.value(giftFrameIdx_, 100)));
                        }
                    });
                    giftAnimTimer_->start(std::max(30, giftDelays_.value(0, 100)));

                    giftLoadTimer_ = new QTimer(this);
                    QObject::connect(giftLoadTimer_, &QTimer::timeout, this, [this]() {
                        decodeNextGiftFrame();
                    });
                    giftLoadTimer_->start(30);
                }
                return;
            }
        }

        const auto still = skin_.presentImage(icon, side);
        giftPm_ = still.pixmap;
        giftW_ = still.logicalW;
        giftH_ = still.logicalH;
    }

    ToolSkin skin_;
    QString kind_, user_, text_, gift_;
    QPixmap giftPm_;
    QVector<QPixmap> giftFrames_;
    QVector<int> giftDelays_;
    int giftFrameIdx_ = 0;
    QString giftAnimPath_;
    int giftFrameCount_ = 0;
    int giftAnimSide_ = 0;
    QTimer* giftAnimTimer_ = nullptr;
    QTimer* giftLoadTimer_ = nullptr;
    int giftW_ = 0;
    int giftH_ = 0;
    QVariantAnimation* fade_ = nullptr;
    qreal opacity_ = 0.0;
    int phase_ = 0;
    std::function<void(DanmuBubble*)> onRelease_;

    // Nemuru 等像素框装饰
    bool chromeReady_ = false;
    QString chromeSkinKey_;
    QRect contentRect_;
    QRect busyRect_, logoRect_, cornerRect_;
    QRect winRects_[3];
    liveaio::resources::LoadedAnim busyAnim_;
    liveaio::resources::LoadedAnim cornerAnim_;
    liveaio::resources::LoadedStill logoStill_, winMin_, winMax_, winClose_;
    int busyFrame_ = 0;
    int cornerFrame_ = 0;
    QTimer* chromeAnimTimer_ = nullptr;
};

// 弹幕内容区：气泡随机落点，无额外绘制。
class DanmuRoot final : public RippleOverlayRoot {
public:
    DanmuRoot(QMainWindow* win, std::function<void()> onResume)
        : RippleOverlayRoot(win), onResume_(std::move(onResume)) {}

protected:
    void onResizeResume() override {
        if (onResume_) onResume_();
    }

private:
    std::function<void()> onResume_;
};

class DanmuOverlayController final : public QObject {
public:
    static constexpr int kPoolCap = 16;

    explicit DanmuOverlayController(QObject* parent = nullptr) : QObject(parent) {}

    bool isMounted() const {
        return OverlayHostService::instance().isToolActive(OverlayToolId::Danmu);
    }

    void show(std::function<void()> onClosed) {
        auto& host = OverlayHostService::instance();
        root_ = nullptr;
        skin_ = activeDanmuSkin();
        auto* shell = host.shell(OverlayToolId::Danmu);
        root_ = new DanmuRoot(shell, [this]() { onFrameResumed(); });
        host.show(OverlayToolId::Danmu, QStringLiteral("弹幕机"), kDanmuGeoKey, 200, 150, 420, 320,
                  root_, [this, onClosed]() {
                      unmount();
                      if (onClosed) onClosed();
                  });
    }

    void unmount() {
        clearAllBubbles();
        drainBubblePool();
        liveaio::resources::releaseGiftPixmapCaches();
        root_ = nullptr;
    }

    void refreshSkin() {
        skin_ = activeDanmuSkin();
        for (auto* b : bubbles_) {
            if (b) b->refreshSkin(skin_);
        }
        if (root_) root_->update();
    }

    void addMessage(const QString& kind, const QString& user, const QString& text,
                    const QString& gift) {
        if (!root_) return;
        if (root_->resizeFrozen()) {
            pending_.append({kind, user, text, gift});
            if (pending_.size() > 80) pending_.remove(0, pending_.size() - 80);
            return;
        }
        auto* bubble = acquireBubble(kind, user, text, gift);
        if (!bubble) return;
        placeBubble(bubble);
    }

    void flushPending() {
        const auto pending = pending_;
        pending_.clear();
        for (const auto& msg : pending) addMessage(msg.kind, msg.user, msg.text, msg.gift);
    }

    void clearAllBubbles() {
        pending_.clear();
        const auto alive = bubbles_;
        bubbles_.clear();
        for (auto* b : alive) releaseBubble(b);
    }

private:
    struct PendingMsg {
        QString kind;
        QString user;
        QString text;
        QString gift;
    };

    DanmuBubble* acquireBubble(const QString& kind, const QString& user, const QString& text,
                               const QString& gift) {
        if (!root_) return nullptr;
        QWidget* content = root_->content();
        while (!pool_.isEmpty()) {
            auto* b = pool_.takeLast();
            if (b) {
                b->setParent(content);
                b->reuse(kind, user, text, gift, skin_);
                return b;
            }
        }
        return new DanmuBubble(kind, user, text, gift, skin_, content,
                               [this](DanmuBubble* bub) { releaseBubble(bub); });
    }

    void releaseBubble(DanmuBubble* bubble) {
        if (!bubble) return;
        bubble->prepareForPool();
        bubble->hide();
        bubble->setParent(nullptr);
        bubbles_.removeAll(bubble);
        if (pool_.size() < kPoolCap) pool_.append(bubble);
        else bubble->deleteLater();
    }

    void drainBubblePool() {
        for (auto* b : pool_) {
            if (b) b->deleteLater();
        }
        pool_.clear();
    }

    void onFrameResumed() {
        if (!root_) return;
        auto* shell = OverlayHostService::instance().shell(OverlayToolId::Danmu);
        if (shell) {
            shell->syncRadiusAfterResize();
        }
        flushPending();
    }

    static int bubbleMargin() {
        if (auto* screen = QApplication::primaryScreen()) {
            return std::max(20, static_cast<int>(screen->logicalDotsPerInch() / 2.54));
        }
        return 20;
    }

    void placeBubble(DanmuBubble* bubble) {
        if (!root_) return;
        QWidget* area = root_->content();
        const int m = bubbleMargin();
        const int bw = bubble->width();
        const int bh = bubble->height();
        const int xMin = m;
        const int yMin = m;
        const int xMax = area->width() - m - bw;
        const int yMax = area->height() - m - bh;
        if (xMax < xMin || yMax < yMin) {
            releaseBubble(bubble);
            return;
        }

        QVector<QPoint> candidates;
        const int step = 20;
        QVector<int> xs;
        QVector<int> ys;
        for (int x = xMin; x <= xMax; x += step) xs.append(x);
        if (xs.isEmpty() || xs.last() != xMax) xs.append(xMax);
        for (int y = yMin; y <= yMax; y += step) ys.append(y);
        if (ys.isEmpty() || ys.last() != yMax) ys.append(yMax);
        for (int x : xs) {
            for (int y : ys) candidates.append(QPoint(x, y));
        }
        for (int i = candidates.size() - 1; i > 0; --i) {
            std::swap(candidates[i], candidates[QRandomGenerator::global()->bounded(i + 1)]);
        }

        QVector<QRect> occupied;
        for (auto* b : bubbles_) {
            if (b && b->isVisible()) occupied.append(b->geometry());
        }

        for (const QPoint& pos : candidates) {
            const QRect candidate(pos.x(), pos.y(), bw, bh);
            bool overlaps = false;
            for (const QRect& taken : occupied) {
                if (candidate.intersects(taken)) {
                    overlaps = true;
                    break;
                }
            }
            if (overlaps) continue;
            bubble->move(pos);
            bubbles_.append(bubble);
            bubble->show();
            return;
        }
        releaseBubble(bubble);
    }

    DanmuRoot* root_ = nullptr;
    ToolSkin skin_;
    QVector<DanmuBubble*> bubbles_;
    QVector<DanmuBubble*> pool_;
    QVector<PendingMsg> pending_;
};

class DanmuToolRuntime final : public ToolRuntimeBase {
public:
    explicit DanmuToolRuntime(QObject* parent, std::function<void()> tryRelease)
        : ToolRuntimeBase(parent), tryRelease_(std::move(tryRelease)) {}

    QString toolId() const override { return QStringLiteral("danmu"); }

    bool isOverlayActive() const override {
        return overlayCtrl_ && overlayCtrl_->isMounted();
    }

    void onCorePacket(const QJsonObject& packet) override {
        if (packet.value(QStringLiteral("op")).toString() != QStringLiteral("danmu.show")) return;
        if (!overlayCtrl_ || !overlayCtrl_->isMounted()) return;
        const QString kind = packet.value(QStringLiteral("kind")).toString();
        const QString user = packet.value(QStringLiteral("user")).toString();
        QString text = packet.value(QStringLiteral("text")).toString();
        const QString gift = packet.value(QStringLiteral("gift")).toString();
        if (kind == QLatin1String("like")) {
            text = QStringLiteral("点了%1个赞")
                       .arg(qMax(1, packet.value(QStringLiteral("count")).toInt(1)));
        } else if (kind == QLatin1String("follow")) {
            text = QStringLiteral("关注了");
        }
        const QString suffix =
            configValue(QStringLiteral("danmu_%1_suffix").arg(kind), QString()).toString();
        overlayCtrl_->addMessage(kind, user, text + suffix, gift);
    }

    void toggleOverlay(std::function<void()> onClosed) {
        auto& host = OverlayHostService::instance();
        if (host.isToolActive(OverlayToolId::Danmu)) {
            host.teardown(OverlayToolId::Danmu);
            return;
        }
        pushSavedDanmuSettings();
        if (!overlayCtrl_) overlayCtrl_ = new DanmuOverlayController(this);
        overlayCtrl_->show([this, onClosed]() {
            if (onClosed) onClosed();
            if (tryRelease_) tryRelease_();
        });
    }

    void refreshOverlaySkin() {
        if (overlayCtrl_) overlayCtrl_->refreshSkin();
    }

private:
    DanmuOverlayController* overlayCtrl_ = nullptr;
    std::function<void()> tryRelease_;
};

// ─────────────────────────────────────────────
// 控制面板
// ─────────────────────────────────────────────
class DanmuToolWindow final : public ToolWindowBase {
public:
    static constexpr int kWidth = 448;
    static constexpr int kHeight = 520;
    static constexpr int kSide = 24;
    static constexpr int kScrollGutter = 8;

    explicit DanmuToolWindow(CoreClient* core, DanmuToolRuntime* runtime)
        : ToolWindowBase(core), runtime_(runtime) {
        setWindowTitle(QStringLiteral("设置"));
        setFixedSize(kWidth, kHeight);

        switchKeys_ = {
            {QStringLiteral("chat"), QStringLiteral("danmu_chat_on")},
            {QStringLiteral("gift"), QStringLiteral("danmu_gift_on")},
            {QStringLiteral("follow"), QStringLiteral("danmu_follow_on")},
            {QStringLiteral("like"), QStringLiteral("danmu_like_on")},
        };

        build();
        refreshTheme();
        liveaio::util::onThemeChange(this, [this](const QString&) { refreshTheme(); });
        pushSettings();
    }

    QString toolId() const override { return QStringLiteral("danmu"); }

    void onCorePacket(const QJsonObject&) override {}

    void refreshTheme() override {
        refreshSwitchStyles(false);
        refreshOpenBtn();
        styleTutorialBtn();
        navigate(curNav_);
    }

private:
    void build() {
        auto* root = new QWidget(this);
        root->setObjectName(QStringLiteral("ToolRoot"));
        setCentralWidget(root);
        auto* mainLay = new QVBoxLayout(root);
        mainLay->setContentsMargins(0, 0, 0, 0);
        mainLay->setSpacing(0);

        auto* topbar = new QWidget(root);
        topbar->setObjectName(QStringLiteral("TopBar"));
        topbar->setFixedHeight(46);
        auto* tbLay = new QHBoxLayout(topbar);
        tbLay->setContentsMargins(8, 0, 8, 0);
        tbLay->setSpacing(0);

        stack_ = new QStackedWidget(root);

        const QVector<QPair<QString, std::function<void(QVBoxLayout*)>>> tabs = {
            {QStringLiteral("设置"), [this](QVBoxLayout* l) { buildSettingsPanel(l); }},
            {QStringLiteral("弹幕"), [this](QVBoxLayout* l) { buildChatPanel(l); }},
            {QStringLiteral("礼物"), [this](QVBoxLayout* l) { buildGiftPanel(l); }},
            {QStringLiteral("关注"), [this](QVBoxLayout* l) { buildFollowPanel(l); }},
            {QStringLiteral("点赞"), [this](QVBoxLayout* l) { buildLikePanel(l); }},
        };
        tabBuilders_ = tabs;
        tabBuilt_.resize(tabs.size());
        for (int i = 0; i < tabs.size(); ++i) {
            auto* navBtn = new QPushButton(tabs[i].first, topbar);
            navBtn->setObjectName(QStringLiteral("TabBtn"));
            navBtn->setFixedHeight(46);
            navBtn->setCursor(Qt::PointingHandCursor);
            navBtn->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
            liveaio::util::suppressButtonFocus(navBtn);
            QObject::connect(navBtn, &QPushButton::clicked, this, [this, i]() { navigate(i); });
            navBtns_.append(navBtn);
            tbLay->addWidget(navBtn);

            auto* ph = new QWidget;
            ph->setObjectName(QStringLiteral("TabPlaceholder"));
            tabPlaceholders_.append(ph);
            stack_->addWidget(ph);
        }
        tbLay->addStretch();
        mainLay->addWidget(topbar);
        mainLay->addWidget(stack_);

        navigate(0);
    }

    void ensureTab(int index) {
        if (index < 0 || index >= tabBuilders_.size() || tabBuilt_.value(index)) return;
        tabBuilt_[index] = true;
        auto* inner = new QWidget;
        auto* innerLay = new QVBoxLayout(inner);
        innerLay->setContentsMargins(kSide + kScrollGutter / 2, 20,
                                     kSide + kScrollGutter / 2, 20);
        innerLay->setSpacing(16);
        tabBuilders_[index].second(innerLay);
        innerLay->addStretch();
        QWidget* page = scrollPage(inner);
        QWidget* ph = tabPlaceholders_.value(index);
        if (ph) {
            const int idx = stack_->indexOf(ph);
            stack_->removeWidget(ph);
            ph->deleteLater();
            tabPlaceholders_[index] = nullptr;
            stack_->insertWidget(idx, page);
        } else {
            stack_->addWidget(page);
        }
    }

    void navigate(int index) {
        ensureTab(index);
        curNav_ = index;
        stack_->setCurrentIndex(index);
        for (int i = 0; i < navBtns_.size(); ++i) {
            navBtns_[i]->setProperty("active", i == index);
            navBtns_[i]->style()->unpolish(navBtns_[i]);
            navBtns_[i]->style()->polish(navBtns_[i]);
        }
    }

    QFrame* makeCard() {
        auto* card = new QFrame;
        card->setObjectName(QStringLiteral("Card"));
        card->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Preferred);
        auto* lay = new QVBoxLayout(card);
        lay->setContentsMargins(20, 16, 20, 16);
        lay->setSpacing(14);
        return card;
    }

    QFrame* makeSeparator() {
        auto* sep = new QFrame;
        sep->setFrameShape(QFrame::HLine);
        seps_.append(sep);
        return sep;
    }

    static QLabel* pageTitle(const QString& text) {
        auto* lbl = new QLabel(text);
        lbl->setObjectName(QStringLiteral("ToolPageTitle"));
        lbl->setAlignment(Qt::AlignHCenter);
        return lbl;
    }

    void addToggleRow(QVBoxLayout* cardLay, const QString& key, const QString& name,
                      const QString& tip) {
        auto* row = new QHBoxLayout;
        row->setSpacing(0);
        auto* left = new QVBoxLayout;
        left->setSpacing(3);
        auto* n = new QLabel(name);
        n->setStyleSheet(QStringLiteral("font-size: 14px; font-weight: 600;"));
        auto* t = new QLabel(tip);
        t->setObjectName(QStringLiteral("ToolTip"));
        left->addWidget(n);
        left->addWidget(t);
        auto* btn = new liveaio::util::ChromeButton(QString(), nullptr,
                                                    liveaio::util::kControlHSmall);
        btn->setPadX(14);
        btn->setText(configValue(switchKeys_.value(key), true).toBool()
                         ? QStringLiteral("已开启") : QStringLiteral("已关闭"));
        QObject::connect(btn, &QPushButton::clicked, this, [this, key]() { toggleSwitch(key); });
        switchBtns_.insert(key, btn);
        row->addLayout(left);
        row->addStretch();
        row->addWidget(btn);
        cardLay->addLayout(row);
    }

    void addSuffixRow(QVBoxLayout* cardLay, const QString& key) {
        auto* row = new QHBoxLayout;
        auto* lbl = new QLabel(QStringLiteral("弹幕后缀"));
        auto* edit = new QLineEdit;
        edit->setMaxLength(10);
        edit->setPlaceholderText(QStringLiteral("最多10字"));
        edit->setFixedWidth(150);
        edit->setText(configValue(QStringLiteral("danmu_%1_suffix").arg(key), QString()).toString());
        suffixEdits_.insert(key, edit);
        row->addWidget(lbl);
        row->addStretch();
        row->addWidget(edit);
        cardLay->addLayout(row);
    }

    void addApplyRow(QVBoxLayout* lay, std::function<void()> handler) {
        auto* row = new QHBoxLayout;
        row->setContentsMargins(0, 8, 0, 0);
        row->addStretch(1);
        auto* btn = new liveaio::util::ChromeButton(QStringLiteral("应用"), nullptr,
                                                    liveaio::util::kControlH,
                                                    liveaio::util::ControlVariant::Accent);
        btn->setFixedWidth(72);
        btn->setPadX(10);
        QObject::connect(btn, &QPushButton::clicked, this, [handler]() { handler(); });
        applyBtns_.append(btn);
        row->addWidget(btn);
        lay->addLayout(row);
    }

    void buildSettingsPanel(QVBoxLayout* lay) {
        lay->addWidget(pageTitle(QStringLiteral("设置")));

        auto* card = makeCard();
        auto* cl = qobject_cast<QVBoxLayout*>(card->layout());
        auto* row = new QHBoxLayout;
        auto* lbl = new QLabel(QStringLiteral("悬浮弹幕窗"));
        lbl->setStyleSheet(QStringLiteral("font-size: 14px; font-weight: 600;"));
        row->addWidget(lbl);
        row->addStretch();
        tutorialBtn_ = new liveaio::util::ChromeButton(
            QStringLiteral("教程"), nullptr, liveaio::util::kControlH,
            liveaio::util::ControlVariant::Neutral);
        tutorialBtn_->setPadX(14);
        QObject::connect(tutorialBtn_, &QPushButton::clicked, this, [this]() {
            showTutorialDialog(this);
        });
        row->addWidget(tutorialBtn_);
        row->addSpacing(8);
        openBtn_ = new liveaio::util::ChromeButton(
            QStringLiteral("打开弹幕窗"), nullptr, liveaio::util::kControlH);
        QObject::connect(openBtn_, &QPushButton::clicked, this, [this]() { toggleOverlay(); });
        row->addWidget(openBtn_);
        cl->addLayout(row);

        auto* desc = new QLabel(
            QStringLiteral("透明悬浮窗，叠加在直播软件上方显示弹幕。"
                           "窗口采集请在直播伴侣素材里开启透明背景。"));
        desc->setWordWrap(true);
        desc->setObjectName(QStringLiteral("ToolTip"));
        cl->addWidget(desc);
        lay->addWidget(card);

        auto* themeCard = makeCard();
        auto* tcl = qobject_cast<QVBoxLayout*>(themeCard->layout());
        auto* trow = new QHBoxLayout;
        trow->setSpacing(12);
        auto* tlbl = new QLabel(QStringLiteral("弹幕外观主题"));
        tlbl->setStyleSheet(QStringLiteral("font-size: 14px; font-weight: 600;"));
        trow->addWidget(tlbl);
        trow->addStretch();

        skinCombo_ = new liveaio::util::ThemedComboBox;
        QStringList skinNames;
        const auto skins = liveaio::resources::listSkins(g_appRoot, QStringLiteral("danmu"));
        for (const auto& entry : skins) {
            skinNameToId_.insert(entry.name, entry.id);
            skinNames << entry.name;
        }
        skinCombo_->addItems(skinNames);
        const QString activeId = configValue(
            liveaio::resources::skinConfigKey(QStringLiteral("danmu")),
            QStringLiteral("default")).toString();
        for (const auto& entry : skins) {
            if (entry.id == activeId) skinCombo_->setCurrentText(entry.name);
        }
        skinCombo_->setFixedHeight(liveaio::util::kControlH);
        skinCombo_->setMinimumWidth(160);
        skinCombo_->setOnChange([this](const QString& name) { onSkinChanged(name); });
        trow->addWidget(skinCombo_);
        tcl->addLayout(trow);
        lay->addWidget(themeCard);
    }

    void buildChatPanel(QVBoxLayout* lay) {
        lay->addWidget(pageTitle(QStringLiteral("弹幕")));
        auto* card = makeCard();
        auto* cl = qobject_cast<QVBoxLayout*>(card->layout());
        addToggleRow(cl, QStringLiteral("chat"), QStringLiteral("消息弹幕"),
                     QStringLiteral("显示观众发送的聊天弹幕"));
        cl->addWidget(makeSeparator());
        addSuffixRow(cl, QStringLiteral("chat"));
        lay->addWidget(card);
        addApplyRow(lay, [this]() { applyKind(QStringLiteral("chat")); });
    }

    void buildGiftPanel(QVBoxLayout* lay) {
        lay->addWidget(pageTitle(QStringLiteral("礼物")));
        auto* card = makeCard();
        auto* cl = qobject_cast<QVBoxLayout*>(card->layout());
        addToggleRow(cl, QStringLiteral("gift"), QStringLiteral("礼物弹幕"),
                     QStringLiteral("显示观众送出礼物的弹幕"));
        cl->addWidget(makeSeparator());

        auto* row = new QHBoxLayout;
        row->addWidget(new QLabel(QStringLiteral("最低金额")));
        giftSpin_ = new QSpinBox;
        giftSpin_->setRange(0, 999999);
        giftSpin_->setSuffix(QStringLiteral(" 钻"));
        giftSpin_->setFixedSize(110, 28);
        giftSpin_->setValue(configValue(QStringLiteral("danmu_gift_min_diamonds"), 0).toInt());
        row->addStretch();
        row->addWidget(giftSpin_);
        cl->addLayout(row);
        cl->addWidget(makeSeparator());
        addSuffixRow(cl, QStringLiteral("gift"));
        lay->addWidget(card);
        addApplyRow(lay, [this]() { applyKind(QStringLiteral("gift")); });
    }

    void buildFollowPanel(QVBoxLayout* lay) {
        lay->addWidget(pageTitle(QStringLiteral("关注")));
        auto* card = makeCard();
        auto* cl = qobject_cast<QVBoxLayout*>(card->layout());
        addToggleRow(cl, QStringLiteral("follow"), QStringLiteral("关注弹幕"),
                     QStringLiteral("显示新关注通知弹幕"));
        cl->addWidget(makeSeparator());
        addSuffixRow(cl, QStringLiteral("follow"));
        lay->addWidget(card);
        addApplyRow(lay, [this]() { applyKind(QStringLiteral("follow")); });
    }

    void buildLikePanel(QVBoxLayout* lay) {
        lay->addWidget(pageTitle(QStringLiteral("点赞")));
        auto* card = makeCard();
        auto* cl = qobject_cast<QVBoxLayout*>(card->layout());
        addToggleRow(cl, QStringLiteral("like"), QStringLiteral("点赞弹幕"),
                     QStringLiteral("显示观众点赞弹幕"));
        cl->addWidget(makeSeparator());

        auto* row = new QHBoxLayout;
        row->addWidget(new QLabel(QStringLiteral("数量阈值")));
        likeSpin_ = new QSpinBox;
        likeSpin_->setRange(1, 99999);
        likeSpin_->setFixedSize(90, 28);
        likeSpin_->setValue(qMax(1, configValue(QStringLiteral("danmu_like_threshold"), 1).toInt()));
        row->addStretch();
        row->addWidget(likeSpin_);
        cl->addLayout(row);
        cl->addWidget(makeSeparator());

        auto* row2 = new QHBoxLayout;
        auto* left2 = new QVBoxLayout;
        left2->setSpacing(3);
        auto* lbl2 = new QLabel(QStringLiteral("累加模式"));
        lbl2->setStyleSheet(QStringLiteral("font-size: 14px; font-weight: 600;"));
        auto* tip2 = new QLabel(QStringLiteral("按用户累计点赞数，达到阈值后发送弹幕"));
        tip2->setObjectName(QStringLiteral("ToolTip"));
        left2->addWidget(lbl2);
        left2->addWidget(tip2);
        likeAccumBtn_ = new liveaio::util::ChromeButton(QString(), nullptr,
                                                        liveaio::util::kControlHSmall);
        likeAccumBtn_->setPadX(14);
        likeAccumBtn_->setText(configValue(QStringLiteral("danmu_like_accumulate"), false).toBool()
                                   ? QStringLiteral("累加：已开启") : QStringLiteral("累加：已关闭"));
        QObject::connect(likeAccumBtn_, &QPushButton::clicked, this, [this]() {
            const bool on = likeAccumBtn_->text() == QStringLiteral("累加：已开启");
            likeAccumBtn_->setText(on ? QStringLiteral("累加：已关闭")
                                      : QStringLiteral("累加：已开启"));
            writeConfigValue(QStringLiteral("danmu_like_accumulate"), !on);
            refreshSwitchStyles(false);
            pushSettings();
        });
        row2->addLayout(left2);
        row2->addStretch();
        row2->addWidget(likeAccumBtn_);
        cl->addLayout(row2);
        cl->addWidget(makeSeparator());
        addSuffixRow(cl, QStringLiteral("like"));
        lay->addWidget(card);
        addApplyRow(lay, [this]() { applyKind(QStringLiteral("like")); });
    }

    void toggleSwitch(const QString& key) {
        auto* btn = switchBtns_.value(key);
        if (!btn) return;
        const bool on = btn->text() == QStringLiteral("已开启");
        btn->setText(on ? QStringLiteral("已关闭") : QStringLiteral("已开启"));
        refreshSwitchStyles(false);
    }

    void refreshSwitchStyles(bool syncFromConfig) {
        const auto& C = theme();
        const int smallH = liveaio::util::kControlHSmall;
        // 开/关只切换外观档位，描边宽与外形高由 ChromeButton 统一保证。
        const auto onVar = liveaio::util::ControlVariant::Accent;
        const auto offVar = liveaio::util::ControlVariant::Neutral;

        for (auto it = switchBtns_.constBegin(); it != switchBtns_.constEnd(); ++it) {
            bool on;
            if (syncFromConfig) {
                on = configValue(switchKeys_.value(it.key()), true).toBool();
                it.value()->setText(on ? QStringLiteral("已开启") : QStringLiteral("已关闭"));
            } else {
                on = it.value()->text() == QStringLiteral("已开启");
            }
            it.value()->setVariant(on ? onVar : offVar);
        }
        if (likeAccumBtn_) {
            if (syncFromConfig) {
                const bool on = configValue(QStringLiteral("danmu_like_accumulate"), false).toBool();
                likeAccumBtn_->setText(on ? QStringLiteral("累加：已开启")
                                          : QStringLiteral("累加：已关闭"));
            }
            const bool on = likeAccumBtn_->text() == QStringLiteral("累加：已开启");
            likeAccumBtn_->setVariant(on ? onVar : offVar);
        }
        for (auto* sep : seps_) {
            sep->setStyleSheet(QStringLiteral("background: %1; max-height: 1px;").arg(C.border));
        }
        for (auto* btn : applyBtns_) btn->update();
        if (giftSpin_) giftSpin_->setStyleSheet(liveaio::util::spinBoxQss(smallH));
        if (likeSpin_) likeSpin_->setStyleSheet(liveaio::util::spinBoxQss(smallH));
        if (skinCombo_) skinCombo_->refreshTheme();
    }

    void styleTutorialBtn() {
        if (tutorialBtn_) tutorialBtn_->update();
    }

    void applyKind(const QString& kind) {
        const bool on = switchBtns_.value(kind)
            && switchBtns_.value(kind)->text() == QStringLiteral("已开启");
        writeConfigValue(switchKeys_.value(kind), on);
        if (auto* edit = suffixEdits_.value(kind)) {
            writeConfigValue(QStringLiteral("danmu_%1_suffix").arg(kind), edit->text());
        }
        if (kind == QStringLiteral("gift") && giftSpin_) {
            writeConfigValue(QStringLiteral("danmu_gift_min_diamonds"), giftSpin_->value());
        }
        if (kind == QStringLiteral("like")) {
            if (likeSpin_) {
                writeConfigValue(QStringLiteral("danmu_like_threshold"),
                                 qMax(1, likeSpin_->value()));
            }
            if (likeAccumBtn_) {
                writeConfigValue(QStringLiteral("danmu_like_accumulate"),
                                 likeAccumBtn_->text() == QStringLiteral("累加：已开启"));
            }
        }
        pushSettings();
    }

    QString suffixFor(const QString& kind) const {
        if (auto* edit = suffixEdits_.value(kind)) return edit->text();
        return configValue(QStringLiteral("danmu_%1_suffix").arg(kind), QString()).toString();
    }

    void onSkinChanged(const QString& name) {
        const QString id = skinNameToId_.value(name, QStringLiteral("default"));
        writeConfigValue(liveaio::resources::skinConfigKey(QStringLiteral("danmu")), id);
        if (runtime_) runtime_->refreshOverlaySkin();
    }

    void toggleOverlay() {
        if (!runtime_) return;
        if (!OverlayHostService::instance().isToolActive(OverlayToolId::Danmu)) {
            publishToolDemand(QStringLiteral("danmu"), true);
        }
        runtime_->toggleOverlay([this]() { refreshOpenBtn(); });
        refreshOpenBtn();
    }

    void refreshOpenBtn() {
        if (!openBtn_) return;
        const bool open = OverlayHostService::instance().isToolActive(OverlayToolId::Danmu);
        openBtn_->setText(open ? QStringLiteral("关闭弹幕窗") : QStringLiteral("打开弹幕窗"));
        openBtn_->setVariant(open ? liveaio::util::ControlVariant::Danger
                                  : liveaio::util::ControlVariant::Outlined);
        openBtn_->update();
    }

    void pushSettings() {
        sendCore(QJsonObject{
            {QStringLiteral("op"), QStringLiteral("tool.danmu.set")},
            {QStringLiteral("settings"), QJsonObject{
                {QStringLiteral("danmu_chat_on"), configValue(QStringLiteral("danmu_chat_on"), true).toBool()},
                {QStringLiteral("danmu_gift_on"), configValue(QStringLiteral("danmu_gift_on"), true).toBool()},
                {QStringLiteral("danmu_gift_min_diamonds"), configValue(QStringLiteral("danmu_gift_min_diamonds"), 0).toInt()},
                {QStringLiteral("danmu_follow_on"), configValue(QStringLiteral("danmu_follow_on"), true).toBool()},
                {QStringLiteral("danmu_like_on"), configValue(QStringLiteral("danmu_like_on"), true).toBool()},
                {QStringLiteral("danmu_like_threshold"), qMax(1, configValue(QStringLiteral("danmu_like_threshold"), 1).toInt())},
                {QStringLiteral("danmu_like_accumulate"), configValue(QStringLiteral("danmu_like_accumulate"), false).toBool()},
            }},
        });
    }

    QVector<QWidget*> tabPlaceholders_;
    QVector<bool> tabBuilt_;
    QVector<QPair<QString, std::function<void(QVBoxLayout*)>>> tabBuilders_;
    QStackedWidget* stack_ = nullptr;
    QVector<QPushButton*> navBtns_;
    int curNav_ = 0;
    QMap<QString, QString> switchKeys_;
    QMap<QString, liveaio::util::ChromeButton*> switchBtns_;
    QMap<QString, QLineEdit*> suffixEdits_;
    QMap<QString, QString> skinNameToId_;
    QVector<QFrame*> seps_;
    QVector<liveaio::util::ChromeButton*> applyBtns_;
    QSpinBox* giftSpin_ = nullptr;
    QSpinBox* likeSpin_ = nullptr;
    liveaio::util::ChromeButton* likeAccumBtn_ = nullptr;
    liveaio::util::ChromeButton* openBtn_ = nullptr;
    liveaio::util::ChromeButton* tutorialBtn_ = nullptr;
    liveaio::util::ThemedComboBox* skinCombo_ = nullptr;
    DanmuToolRuntime* runtime_ = nullptr;
};

static ToolRuntimeBase* createDanmuRuntime(QObject* parent, std::function<void()> tryRelease) {
    return new DanmuToolRuntime(parent, std::move(tryRelease));
}

static ToolWindowBase* createDanmuTool(CoreClient* core, DanmuToolRuntime* runtime) {
    return new DanmuToolWindow(core, runtime);
}

}  // namespace liveaio::tools
