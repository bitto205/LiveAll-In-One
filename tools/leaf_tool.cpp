// tools/leaf_tool.cpp — 捡叶子小游戏：设置页 + 透明悬浮窗（双碰撞箱 + 共享贴图）。

namespace liveaio::tools::leaf {

static const QString kGeoKey = QStringLiteral("leaf_window_geometry");
// v3：再次重置历史坐标，保证垃圾桶默认右上角（忽略 v2 左上坏档）。
static const QString kTrashPosKey = QStringLiteral("leaf_trash_pos_v3");
static const QString kMaxPercentKey = QStringLiteral("leaf_max_percent");
static const QString kLeafScaleKey = QStringLiteral("leaf_scale_percent");
static const QString kTrashScaleKey = QStringLiteral("leaf_trash_scale_percent");
static const QString kViewportSizeKey = QStringLiteral("leaf_last_viewport_size");
static const QString kFaceShieldEnabledKey = QStringLiteral("leaf_face_shield_enabled");
static const QString kFaceShieldRectKey = QStringLiteral("leaf_face_shield_rect_v1");
static constexpr qreal kShieldPeakFrac = 0.22;
static constexpr qreal kShieldEjectAccel = 420.0;  // 框内缓慢向下挤出
// 旧资源路径：仅作皮肤文件缺失时的回退。
static const QString kLegacyLeafImage = QStringLiteral("resources/image/zaidoopro-painting-8032889.png");
static const QString kLegacyTrashImage = QStringLiteral("resources/image/trash_can.png");
static const QString kLegacyTrashOpenImage = QStringLiteral("resources/image/trash_can_open.png");

// 默认物理/绘制尺寸（cm）；可被 resources/skin/leaf/<id>/skin.json 覆盖。
static constexpr qreal kDefLeafSideCm = 1.0;
static constexpr qreal kDefRigidHalfLengthFrac = 0.45;
static constexpr qreal kDefRigidHalfWidthFrac = 0.14;
static constexpr qreal kDefRigidBevelFrac = 0.055;
static constexpr qreal kDefSoftRadiusFrac = 0.24;
static constexpr qreal kDefSoftHalfLenFrac = 0.34;
static constexpr qreal kDefArtAngleRad = -0.7853981633974483;  // -45°
static constexpr qreal kDefTrashWCm = 2.0;
static constexpr qreal kDefTrashHCm = 2.67;
static constexpr qreal kDefTrashHitPadCm = 0.12;
static constexpr qreal kDefTrashNearPadCm = 0.45;
static constexpr qreal kLidAnimSec = 0.12;
static constexpr qreal kHaloAnimSec = 0.14;
static constexpr qreal kDefLeafHaloOuterCm = 0.22;
static constexpr qreal kDefTrashHaloOuterCm = 0.28;
static constexpr qreal kDefTrashHaloAmount = 0.72;
static constexpr qreal kDefLeafHaloAmount = 0.82;
static constexpr qreal kWorldPadCm = 0.15;
static constexpr int kMinLeaves = 0;
static constexpr int kMaxLeavesHard = 240;
// 容量按理论上限的百分比配置。
static constexpr int kCapPercentMin = 20;
static constexpr int kCapPercentMax = 70;
static constexpr int kCapPercentDefault = 50;
// 出场/退场节奏：排队生成，不一次性刷屏。
static constexpr qreal kSpawnIntervalSec = 0.20;
static constexpr qreal kDespawnIntervalSec = 0.10;
static constexpr qreal kFadeInSec = 0.16;
static constexpr qreal kFadeOutSec = 0.16;
// 待生成/待吊销队列硬顶，防止礼物刷屏把 pending 撑爆。
static constexpr int kPendingQueueCap = 120;
static constexpr int kTickMs = 16;
static constexpr int kPositionIters = 8;
static constexpr int kResizeSettleIters = 18;
static constexpr int kDragPositionIters = 3;
static constexpr int kMaxDragSubsteps = 8;
static constexpr qreal kGravity = 1400.0;
// 阻尼再低一档：叶子滑得更远、转得更久。
static constexpr qreal kDamping = 0.960;
static constexpr qreal kAngDamping = 0.820;
static constexpr qreal kMaxAngVel = 0.9;
static constexpr qreal kMaxSpeed = 165.0;
static constexpr qreal kCollisionTorque = 0.001;
static constexpr qreal kPositionSlopPx = 0.25;
static constexpr qreal kPositionMaxCorrectionFrac = 0.65;
static constexpr qreal kDragStepFrac = 0.70;
static constexpr qreal kDragPushRatio = 0.55;
static constexpr qreal kFriction = 0.32;
static constexpr qreal kRestitution = 0.0;
static constexpr qreal kFloorFriction = 0.72;
static constexpr qreal kPi = 3.14159265358979323846;

static qreal screenDpi() {
    static qreal dpi = 0.0;
    if (dpi <= 0.0) {
        auto* screen = QApplication::primaryScreen();
        dpi = screen ? screen->logicalDotsPerInch() : 96.0;
    }
    return dpi;
}

static qreal cmToPxF(qreal cm) { return screenDpi() / 2.54 * cm; }
static int cmToPx(qreal cm) { return std::max(1, static_cast<int>(std::lround(cmToPxF(cm)))); }

// 皮肤驱动的叶子/垃圾桶形状、碰撞与光晕；缺字段时用编译期默认值。
struct LeafSkinParams {
    enum class RigidShape { Octagon, Hexagon };

    qreal leafSideCm = kDefLeafSideCm;
    qreal rigidHalfLengthFrac = kDefRigidHalfLengthFrac;
    qreal rigidHalfWidthFrac = kDefRigidHalfWidthFrac;
    qreal rigidBevelFrac = kDefRigidBevelFrac;
    qreal softRadiusFrac = kDefSoftRadiusFrac;
    qreal softHalfLenFrac = kDefSoftHalfLenFrac;
    qreal artAngleRad = kDefArtAngleRad;
    qreal trashWCm = kDefTrashWCm;
    qreal trashHCm = kDefTrashHCm;
    qreal trashHitPadCm = kDefTrashHitPadCm;
    qreal trashNearPadCm = kDefTrashNearPadCm;
    qreal leafHaloOuterCm = kDefLeafHaloOuterCm;
    qreal trashHaloOuterCm = kDefTrashHaloOuterCm;
    qreal leafHaloAmount = kDefLeafHaloAmount;
    qreal trashHaloAmount = kDefTrashHaloAmount;
    bool leafHaloSoftInner = true;
    RigidShape rigidShape = RigidShape::Octagon;
    QString leafRender = QStringLiteral("image");
    QString leafEmoji;
    qreal trashHitLeftFrac = 0.0;
    qreal trashHitTopFrac = 0.28;
    qreal trashHitTopOpenFrac = 0.0;
    qreal trashHitRightFrac = 1.0;
    qreal trashHitBottomFrac = 1.0;
    qreal trashGrabTopFrac = 0.28;
    QString rootPath;
    QString leafFile = QStringLiteral("leaf.png");
    QString trashFile = QStringLiteral("trash_can.png");
    QString trashOpenFile = QStringLiteral("trash_can_open.png");

    static LeafSkinParams fromToolSkin(const liveaio::resources::ToolSkin& skin) {
        LeafSkinParams p;
        p.rootPath = skin.rootPath;
        const QJsonObject m = skin.meta.value(QStringLiteral("metrics")).toObject();
        auto num = [&](const QString& key, qreal def) -> qreal {
            const QJsonValue v = m.value(key);
            return v.isUndefined() || v.isNull() ? def : v.toDouble(def);
        };
        p.leafSideCm = num(QStringLiteral("leaf_side_cm"), p.leafSideCm);
        p.rigidHalfLengthFrac = num(QStringLiteral("rigid_half_length_frac"), p.rigidHalfLengthFrac);
        p.rigidHalfWidthFrac = num(QStringLiteral("rigid_half_width_frac"), p.rigidHalfWidthFrac);
        p.rigidBevelFrac = num(QStringLiteral("rigid_bevel_frac"), p.rigidBevelFrac);
        p.softRadiusFrac = num(QStringLiteral("soft_radius_frac"), p.softRadiusFrac);
        p.softHalfLenFrac = num(QStringLiteral("soft_half_len_frac"), p.softHalfLenFrac);
        p.trashWCm = num(QStringLiteral("trash_w_cm"), p.trashWCm);
        p.trashHCm = num(QStringLiteral("trash_h_cm"), p.trashHCm);
        p.trashHitPadCm = num(QStringLiteral("trash_hit_pad_cm"), p.trashHitPadCm);
        p.trashNearPadCm = num(QStringLiteral("trash_near_pad_cm"), p.trashNearPadCm);
        p.leafHaloOuterCm = num(QStringLiteral("leaf_halo_outer_cm"), p.leafHaloOuterCm);
        p.trashHaloOuterCm = num(QStringLiteral("trash_halo_outer_cm"), p.trashHaloOuterCm);
        p.leafHaloAmount = num(QStringLiteral("leaf_halo_amount"), p.leafHaloAmount);
        p.trashHaloAmount = num(QStringLiteral("trash_halo_amount"), p.trashHaloAmount);
        if (m.contains(QStringLiteral("leaf_halo_soft_inner"))) {
            p.leafHaloSoftInner = m.value(QStringLiteral("leaf_halo_soft_inner")).toBool(true);
        }
        if (m.contains(QStringLiteral("art_angle_deg"))) {
            p.artAngleRad = num(QStringLiteral("art_angle_deg"), -45.0) * kPi / 180.0;
        }
        if (m.value(QStringLiteral("rigid_shape")).toString() == QLatin1String("hexagon")) {
            p.rigidShape = RigidShape::Hexagon;
        }
        p.trashHitLeftFrac =
            std::clamp(num(QStringLiteral("trash_hit_left_frac"), p.trashHitLeftFrac), 0.0, 1.0);
        p.trashHitTopFrac =
            std::clamp(num(QStringLiteral("trash_hit_top_frac"), p.trashHitTopFrac), 0.0, 1.0);
        p.trashHitTopOpenFrac = std::clamp(
            num(QStringLiteral("trash_hit_top_open_frac"), p.trashHitTopOpenFrac), 0.0, 1.0);
        p.trashHitRightFrac =
            std::clamp(num(QStringLiteral("trash_hit_right_frac"), p.trashHitRightFrac), 0.0, 1.0);
        p.trashHitBottomFrac =
            std::clamp(num(QStringLiteral("trash_hit_bottom_frac"), p.trashHitBottomFrac), 0.0, 1.0);
        p.trashGrabTopFrac =
            std::clamp(num(QStringLiteral("trash_grab_top_frac"), p.trashGrabTopFrac), 0.0, 1.0);
        const QJsonObject assets = skin.meta.value(QStringLiteral("assets")).toObject();
        auto fileOf = [&](const QString& key, const QString& def) {
            const QString v = assets.value(key).toString();
            return v.isEmpty() ? def : v;
        };
        p.leafFile = fileOf(QStringLiteral("leaf"), p.leafFile);
        p.trashFile = fileOf(QStringLiteral("trash"), p.trashFile);
        p.trashOpenFile = fileOf(QStringLiteral("trash_open"), p.trashOpenFile);
        p.leafRender = skin.meta.value(QStringLiteral("leaf_render")).toString(p.leafRender);
        p.leafEmoji = skin.meta.value(QStringLiteral("leaf_emoji")).toString();
        return p;
    }
};

struct LeafSharedAssets;

static QString privateLeafSkinRoot(const QString& id) {
    return QDir(g_appRoot).filePath(QStringLiteral("resources/private_skin/leaf/%1").arg(id));
}

// 私人皮肤放在 resources/private_skin/leaf/<id>/（不入库），按目录发现，不硬编码皮肤名。
static bool isPrivateLeafSkinId(const QString& id) {
    if (id.isEmpty()) return false;
    return QFile::exists(
        QDir(privateLeafSkinRoot(id)).filePath(QStringLiteral("skin.json")));
}

static QStringList privateLeafSkinIds() {
    const QDir root(QDir(g_appRoot).filePath(QStringLiteral("resources/private_skin/leaf")));
    if (!root.exists()) return {};
    return root.entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name);
}

static liveaio::resources::ToolSkin activeLeafSkin() {
    const QString id = configValue(liveaio::resources::skinConfigKey(QStringLiteral("leaf")),
                                   QStringLiteral("default")).toString();
    if (isPrivateLeafSkinId(id)) {
        liveaio::resources::ToolSkin skin;
        skin.toolId = QStringLiteral("leaf");
        skin.skinId = id;
        skin.rootPath = privateLeafSkinRoot(id);
        QFile file(QDir(skin.rootPath).filePath(QStringLiteral("skin.json")));
        if (file.open(QIODevice::ReadOnly)) {
            const QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
            if (doc.isObject()) skin.meta = doc.object();
        }
        skin.name = skin.meta.value(QStringLiteral("name")).toString(id);
        return skin;
    }
    return liveaio::resources::ToolSkin::load(
        g_appRoot, QStringLiteral("leaf"),
        id.isEmpty() ? QStringLiteral("default") : id);
}

static QVector<liveaio::resources::SkinEntry> listLeafSkins() {
    auto skins = liveaio::resources::listSkins(g_appRoot, QStringLiteral("leaf"));
    for (const QString& id : privateLeafSkinIds()) {
        QFile file(QDir(privateLeafSkinRoot(id)).filePath(QStringLiteral("skin.json")));
        if (!file.open(QIODevice::ReadOnly)) continue;
        const QJsonDocument doc = QJsonDocument::fromJson(file.readAll());
        if (!doc.isObject()) continue;
        const QString name = doc.object().value(QStringLiteral("name")).toString(id);
        const auto sameId = [&id](const liveaio::resources::SkinEntry& e) { return e.id == id; };
        if (std::any_of(skins.cbegin(), skins.cend(), sameId)) continue;
        skins.append(liveaio::resources::SkinEntry{id, name});
    }
    return skins;
}

static const LeafSkinParams& leafParams();

static qreal leafVisualPx(qreal scale = 1.0) { return cmToPxF(leafParams().leafSideCm) * scale; }
static qreal rigidHalfLength(qreal scale = 1.0) {
    return leafVisualPx(scale) * leafParams().rigidHalfLengthFrac;
}
static qreal rigidHalfWidth(qreal scale = 1.0) {
    return leafVisualPx(scale) * leafParams().rigidHalfWidthFrac;
}
static qreal rigidBevel(qreal scale = 1.0) {
    return leafVisualPx(scale) * leafParams().rigidBevelFrac;
}
static qreal rigidRadius(qreal scale = 1.0) {
    return std::hypot(rigidHalfLength(scale), rigidHalfWidth(scale));
}
static qreal softR(qreal scale = 1.0) { return leafVisualPx(scale) * leafParams().softRadiusFrac; }
static qreal softHalf(qreal scale = 1.0) { return leafVisualPx(scale) * leafParams().softHalfLenFrac; }

static qreal leafPackSide(qreal leafScale = 1.0) {
    // 最坏倾角下仍能包住八边形刚体的轴对齐正方形。
    return 2.0 * rigidRadius(leafScale);
}

// lo>hi 时（世界比叶子还窄）退回中点，避免 std::clamp 未定义行为导致溢出。
static qreal clampAxis(qreal v, qreal lo, qreal hi) {
    if (lo > hi) return 0.5 * (lo + hi);
    return std::clamp(v, lo, hi);
}

static int maxLeavesForSize(const QSize& contentSize, qreal leafScale = 1.0,
                            const QRectF* shieldSquare = nullptr,
                            qreal shieldPeakFrac = kShieldPeakFrac) {
    const qreal pad = cmToPxF(kWorldPadCm);
    const qreal w = std::max(0.0, contentSize.width() - 2.0 * pad);
    const qreal h = std::max(0.0, contentSize.height() - 2.0 * pad);
    const qreal cell = std::max(1.0, leafPackSide(leafScale));
    const int cols = static_cast<int>(std::floor(w / cell));
    const int rows = static_cast<int>(std::floor(h / cell));
    int base = cols * rows;
    if (shieldSquare && shieldSquare->width() > 1.0 && shieldSquare->height() > 1.0) {
        const qreal side = std::min(shieldSquare->width(), shieldSquare->height());
        const qreal peak = side * std::clamp(shieldPeakFrac, 0.05, 0.6);
        const qreal area = side * side + 0.5 * side * peak;
        base -= static_cast<int>(std::floor(area / (cell * cell)));
    }
    return std::clamp(base, kMinLeaves, kMaxLeavesHard);
}

static int scalePercent(const QString& key) {
    return std::clamp(configValue(key, 100).toInt(), 50, 200);
}

static int clampCapPercent(int percent) {
    return std::clamp(percent, kCapPercentMin, kCapPercentMax);
}

static int leavesForPercent(int theoretical, int percent) {
    const qreal n = std::max(0, theoretical) * clampCapPercent(percent) / 100.0;
    return std::clamp(static_cast<int>(std::lround(n)), kMinLeaves, kMaxLeavesHard);
}

static QSize savedViewportSize() {
    const QVariantMap m = configValue(kViewportSizeKey).toMap();
    const int defaultSide = cmToPx(12.0);
    return QSize(std::max(1, m.value(QStringLiteral("w"), defaultSide).toInt()),
                 std::max(1, m.value(QStringLiteral("h"),
                                     static_cast<int>(defaultSide * 1.25)).toInt()));
}

static int configuredMaxPercent() {
    return clampCapPercent(configValue(kMaxPercentKey, kCapPercentDefault).toInt());
}

// 贴图已裁到内容外接矩形，按原始长宽比居中放进目标框，避免被拉成框的比例。
static QRectF fitPixmap(const QRectF& box, const QPixmap& pm) {
    if (pm.isNull() || pm.width() <= 0 || pm.height() <= 0) return box;
    const qreal k = std::min(box.width() / pm.width(), box.height() / pm.height());
    const QSizeF sz(pm.width() * k, pm.height() * k);
    return QRectF(box.center().x() - sz.width() * 0.5,
                  box.center().y() - sz.height() * 0.5, sz.width(), sz.height());
}

static QPixmap cropOpaque(const QPixmap& src) {
    if (src.isNull()) return src;
    const QImage img = src.toImage().convertToFormat(QImage::Format_ARGB32);
    int minx = img.width(), miny = img.height(), maxx = -1, maxy = -1;
    for (int y = 0; y < img.height(); ++y) {
        const auto* line = reinterpret_cast<const QRgb*>(img.constScanLine(y));
        for (int x = 0; x < img.width(); ++x) {
            if (qAlpha(line[x]) < 20) continue;
            minx = std::min(minx, x);
            miny = std::min(miny, y);
            maxx = std::max(maxx, x);
            maxy = std::max(maxy, y);
        }
    }
    if (maxx < minx) return src;
    // 紧贴内容裁切，不留呼吸边：绘制时按长宽比 fit，不会被裁死。
    const QRect r(minx, miny, maxx - minx + 1, maxy - miny + 1);
    return QPixmap::fromImage(img.copy(r.intersected(img.rect())));
}

// 关盖图头顶透明很大：去掉大部分，但保留少量原图空间，避免贴边裁死。
static QPixmap softCropTrashPreview(const QPixmap& src) {
    if (src.isNull()) return src;
    const QImage img = src.toImage().convertToFormat(QImage::Format_ARGB32);
    int minx = img.width(), miny = img.height(), maxx = -1, maxy = -1;
    for (int y = 0; y < img.height(); ++y) {
        const auto* line = reinterpret_cast<const QRgb*>(img.constScanLine(y));
        for (int x = 0; x < img.width(); ++x) {
            if (qAlpha(line[x]) < 20) continue;
            minx = std::min(minx, x);
            miny = std::min(miny, y);
            maxx = std::max(maxx, x);
            maxy = std::max(maxy, y);
        }
    }
    if (maxx < minx) return src;
    const int breath = std::max(4, static_cast<int>(std::lround(img.height() * 0.12)));
    const int top = std::max(0, miny - breath);
    const int left = std::max(0, minx - 4);
    const int right = std::min(img.width() - 1, maxx + 4);
    const int bottom = std::min(img.height() - 1, maxy + 4);
    return QPixmap::fromImage(
        img.copy(QRect(QPoint(left, top), QPoint(right, bottom)).intersected(img.rect())));
}

static QVector<int> boxBlurAlpha(const QVector<int>& src, int w, int h, int radius) {
    if (radius <= 0 || w <= 0 || h <= 0) return src;
    QVector<int> horizontal(w * h);
    QVector<int> out(w * h);
    for (int y = 0; y < h; ++y) {
        int sum = 0;
        for (int x = -radius; x <= radius; ++x) {
            sum += src[y * w + std::clamp(x, 0, w - 1)];
        }
        for (int x = 0; x < w; ++x) {
            horizontal[y * w + x] = sum / (radius * 2 + 1);
            sum -= src[y * w + std::clamp(x - radius, 0, w - 1)];
            sum += src[y * w + std::clamp(x + radius + 1, 0, w - 1)];
        }
    }
    for (int x = 0; x < w; ++x) {
        int sum = 0;
        for (int y = -radius; y <= radius; ++y) {
            sum += horizontal[std::clamp(y, 0, h - 1) * w + x];
        }
        for (int y = 0; y < h; ++y) {
            out[y * w + x] = sum / (radius * 2 + 1);
            sum -= horizontal[std::clamp(y - radius, 0, h - 1) * w + x];
            sum += horizontal[std::clamp(y + radius + 1, 0, h - 1) * w + x];
        }
    }
    return out;
}

static QPixmap makeHaloPixmap(const QPixmap& src, qreal finalMinSidePx, qreal outerPx,
                              bool softInnerRamp = false) {
    if (src.isNull() || finalMinSidePx <= 0.0 || outerPx <= 0.0) return {};
    const QImage input = src.toImage().convertToFormat(QImage::Format_ARGB32);
    const qreal sourceScale = std::min(input.width(), input.height()) / finalMinSidePx;
    const int pad = std::max(2, static_cast<int>(std::ceil(outerPx * sourceScale)));
    const int blur = std::max(1, static_cast<int>(std::ceil(pad * 0.72)));
    const int w = input.width() + pad * 2;
    const int h = input.height() + pad * 2;
    QVector<int> alpha(w * h, 0);
    for (int y = 0; y < input.height(); ++y) {
        const auto* line = reinterpret_cast<const QRgb*>(input.constScanLine(y));
        for (int x = 0; x < input.width(); ++x) {
            alpha[(y + pad) * w + x + pad] = qAlpha(line[x]);
        }
    }
    // 两次方框模糊近似柔和高斯，资源加载时只计算一次。广度由 pad/blur 决定，勿改。
    alpha = boxBlurAlpha(alpha, w, h, blur);
    alpha = boxBlurAlpha(alpha, w, h, std::max(1, blur / 2));
    QImage halo(w, h, QImage::Format_ARGB32);
    halo.fill(Qt::transparent);
    const qreal peak = softInnerRamp ? 0.68 : 0.82;
    const int aMax = softInnerRamp ? 188 : 220;
    for (int y = 0; y < h; ++y) {
        auto* line = reinterpret_cast<QRgb*>(halo.scanLine(y));
        for (int x = 0; x < w; ++x) {
            qreal t = std::clamp(alpha[y * w + x] / 255.0, 0.0, 1.0);
            if (softInnerRamp && t > 0.0) {
                // 只柔化靠近叶子的高 alpha「第一段」：smoothstep 混线性，再略压高峰，
                // 外圈低 alpha 几乎不动，视觉广度不变。
                const qreal eased = t * t * (3.0 - 2.0 * t);
                t = t * 0.38 + eased * 0.62;
                t = std::pow(t, 1.12);
            }
            const int a = std::clamp(static_cast<int>(std::lround(t * peak * 255.0)), 0, aMax);
            line[x] = qRgba(255, 226, 166, a);
        }
    }
    return QPixmap::fromImage(halo);
}

static void stepToward(qreal* v, qreal target, qreal dt, qreal sec) {
    const qreal step = dt / std::max(0.001, sec);
    if (*v < target) *v = std::min(target, *v + step);
    else *v = std::max(target, *v - step);
}

static void drawCenteredHalo(QPainter& p, const QRectF& dst, const QPixmap& haloPm,
                             qreal amount, qreal expandPx) {
    if (amount <= 0.01 || haloPm.isNull() || expandPx <= 0.5) return;
    p.save();
    p.setOpacity(amount);
    p.drawPixmap(dst.adjusted(-expandPx, -expandPx, expandPx, expandPx).toRect(), haloPm);
    p.restore();
}

static QPixmap makeEmojiPixmap(const QString& emoji, int side) {
    side = std::max(32, side);
    QImage image(side, side, QImage::Format_ARGB32_Premultiplied);
    image.fill(Qt::transparent);
    QPainter p(&image);
    p.setRenderHint(QPainter::Antialiasing, true);
    p.setRenderHint(QPainter::TextAntialiasing, true);
    QFont font(QStringLiteral("Segoe UI Emoji"));
    font.setPixelSize(static_cast<int>(std::lround(side * 0.78)));
    p.setFont(font);
    p.drawText(image.rect(), Qt::AlignCenter, emoji);
    p.end();
    return cropOpaque(QPixmap::fromImage(image));
}

struct LeafSharedAssets {
    LeafSkinParams params;
    QPixmap leafPm;
    QPixmap leafHaloPm;
    QPixmap trashPm;
    QPixmap trashOpenPm;
    QPixmap trashHaloPm;
    QPixmap trashOpenHaloPm;
    // 关盖图为开盖预留了头顶透明区；设置页预览用裁切后贴图，避免框内大片空白。
    QPixmap trashPreviewPm;

    static LeafSharedAssets& instance() {
        static LeafSharedAssets* g = nullptr;
        if (!g) {
            g = new LeafSharedAssets;
            g->load();
        }
        return *g;
    }

    static void reload() { instance().load(); }

    static QString resolvePath(const QString& rel) {
        const QString primary = QDir(g_appRoot).filePath(rel);
        if (QFile::exists(primary)) return primary;
        // 兼容旧布局 image/...
        const QString alt = QDir(g_appRoot).filePath(rel.section(QLatin1Char('/'), 1));
        if (QFile::exists(alt)) return alt;
        return primary;
    }

    QString assetPath(const QString& fileName, const QString& legacyRel) const {
        if (!params.rootPath.isEmpty() && !fileName.isEmpty()) {
            const QString skinPath = QDir(params.rootPath).filePath(fileName);
            if (QFile::exists(skinPath)) return skinPath;
        }
        return resolvePath(legacyRel);
    }

    void load() {
        params = LeafSkinParams::fromToolSkin(activeLeafSkin());
        if (params.leafRender == QLatin1String("emoji") && !params.leafEmoji.isEmpty()) {
            const int side =
                std::max(64, static_cast<int>(std::lround(cmToPxF(params.leafSideCm) * 8.0)));
            leafPm = makeEmojiPixmap(params.leafEmoji, side);
        } else {
            leafPm = QPixmap(assetPath(params.leafFile, kLegacyLeafImage));
        }
        trashPm = QPixmap(assetPath(params.trashFile, kLegacyTrashImage));
        trashOpenPm = QPixmap(assetPath(params.trashOpenFile, kLegacyTrashOpenImage));
        if (leafPm.isNull()) {
            leafPm = QPixmap(64, 64);
            leafPm.fill(QColor(80, 160, 60));
        } else {
            leafPm = cropOpaque(leafPm);
            const int side = std::max(64, static_cast<int>(std::lround(cmToPxF(params.leafSideCm) * 8.0)));
            if (leafPm.width() > side || leafPm.height() > side) {
                leafPm = leafPm.scaled(side, side, Qt::KeepAspectRatio, Qt::SmoothTransformation);
            }
        }
        const int tw = std::max(64, static_cast<int>(std::lround(cmToPxF(params.trashWCm) * 4.0)));
        const int th = std::max(80, static_cast<int>(std::lround(cmToPxF(params.trashHCm) * 4.0)));
        auto scaleTrash = [tw, th](QPixmap& pm, const QColor& fallback) {
            if (pm.isNull()) {
                pm = QPixmap(tw, th);
                pm.fill(fallback);
                return;
            }
            pm = pm.scaled(tw, th, Qt::KeepAspectRatio, Qt::SmoothTransformation);
        };
        scaleTrash(trashPm, QColor(210, 205, 195));
        scaleTrash(trashOpenPm, QColor(210, 205, 195));
        trashPreviewPm = softCropTrashPreview(trashPm);
        if (trashPreviewPm.isNull()) trashPreviewPm = trashPm;
        leafHaloPm = makeHaloPixmap(leafPm, cmToPxF(params.leafSideCm),
                                    cmToPxF(params.leafHaloOuterCm),
                                    params.leafHaloSoftInner);
        trashHaloPm = makeHaloPixmap(
            trashPm, cmToPxF(std::min(params.trashWCm, params.trashHCm)),
            cmToPxF(params.trashHaloOuterCm));
        trashOpenHaloPm = makeHaloPixmap(
            trashOpenPm, cmToPxF(std::min(params.trashWCm, params.trashHCm)),
            cmToPxF(params.trashHaloOuterCm));
    }
};

static const LeafSkinParams& leafParams() { return LeafSharedAssets::instance().params; }

class LeafScalePreview final : public QWidget {
public:
    // 两格同尺寸：宽按 200% 设计宽；高按软裁贴图，并留适量呼吸边。
    static constexpr qreal kPreviewFrameScale = 2.0;
    static constexpr qreal kPreviewFill = 0.90;
    // 预览格上限（按默认皮肤的垃圾桶尺寸），大贴图皮肤等比缩到框内而不是撑开设置页。
    static constexpr qreal kPreviewMaxWCm = 2.0;
    static constexpr qreal kPreviewMaxHCm = 2.7;
    static constexpr int kFramePad = 5;
    static constexpr int kLabelH = 14;
    static constexpr int kLabelGap = 3;  // 文字下沿到下边框
    static constexpr int kGap = 8;
    static constexpr int kOuterPad = 2;

    explicit LeafScalePreview(QWidget* parent = nullptr) : QWidget(parent) {
        setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
        refreshFixedSize();
    }

    void setScales(int leafPercent, int trashPercent) {
        leafScale_ = std::clamp(leafPercent, 50, 200) / 100.0;
        trashScale_ = std::clamp(trashPercent, 50, 200) / 100.0;
        update();
    }

    void reloadFromSkin() {
        refreshFixedSize();
        update();
    }

protected:
    void paintEvent(QPaintEvent*) override {
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing, true);
        p.setRenderHint(QPainter::SmoothPixmapTransform, true);

        const auto& assets = LeafSharedAssets::instance();
        const QSizeF cell = frameInnerSize(assets);
        const qreal cellW = cell.width();
        const qreal cellH = cell.height();
        const qreal boxW = cellW + 2 * kFramePad;
        const qreal boxH = cellH + kFramePad + kLabelH;

        QColor panel = QColor(theme().card);
        panel.setAlpha(140);
        QColor border = QColor(theme().border);

        const QRectF leafBox(kOuterPad, kOuterPad, boxW, boxH);
        const QRectF trashBox(kOuterPad + boxW + kGap, kOuterPad, boxW, boxH);
        auto drawFrame = [&](const QRectF& r) {
            p.setPen(QPen(border, 1.0));
            p.setBrush(panel);
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 8.0, 8.0);
        };
        drawFrame(leafBox);
        drawFrame(trashBox);

        const QRectF leafCell(leafBox.left() + kFramePad, leafBox.top() + kFramePad,
                              cellW, cellH);
        const QRectF trashCell(trashBox.left() + kFramePad, trashBox.top() + kFramePad,
                               cellW, cellH);

        // 最大比例时约占框 90%；更小则居中。
        const qreal unit = previewUnitScale(assets);
        const qreal leafSide = cmToPxF(leafParams().leafSideCm) * leafScale_ * kPreviewFill * unit;
        const QRectF leafRect(leafCell.center().x() - leafSide * 0.5,
                              leafCell.center().y() - leafSide * 0.5,
                              leafSide, leafSide);
        const QSizeF trashVis = trashVisualSize(assets, trashScale_ * kPreviewFill * unit);
        const QRectF trashRect(trashCell.center().x() - trashVis.width() * 0.5,
                               trashCell.center().y() - trashVis.height() * 0.5,
                               trashVis.width(), trashVis.height());

        if (!assets.leafPm.isNull()) {
            p.drawPixmap(fitPixmap(leafRect, assets.leafPm).toRect(), assets.leafPm);
        }
        const QPixmap& trashDraw =
            assets.trashPreviewPm.isNull() ? assets.trashPm : assets.trashPreviewPm;
        if (!trashDraw.isNull()) p.drawPixmap(trashRect.toRect(), trashDraw);

        p.setPen(QColor(theme().textMuted));
        QFont f = p.font();
        f.setPixelSize(10);
        p.setFont(f);
        // 尺寸文字贴框底排：框内高仅约 20mm，留大间距会把整框顶空。
        const qreal labelBottom = leafBox.bottom() - kLabelGap;
        p.drawText(QRectF(leafBox.left(), labelBottom - kLabelH, boxW, kLabelH),
                   Qt::AlignHCenter | Qt::AlignVCenter,
                   QStringLiteral("%1mm").arg(qRound(leafParams().leafSideCm * 10.0 * leafScale_)));
        p.drawText(QRectF(trashBox.left(), labelBottom - kLabelH, boxW, kLabelH),
                   Qt::AlignHCenter | Qt::AlignVCenter,
                   QStringLiteral("%1×%2mm")
                       .arg(qRound(leafParams().trashWCm * 10.0 * trashScale_))
                       .arg(qRound(leafParams().trashHCm * 10.0 * trashScale_)));
    }

private:
    static QSizeF trashVisualSize(const LeafSharedAssets& assets, qreal scale) {
        const qreal w = cmToPxF(leafParams().trashWCm) * scale;
        const QPixmap& pm =
            assets.trashPreviewPm.isNull() ? assets.trashPm : assets.trashPreviewPm;
        if (pm.isNull() || pm.width() <= 0) {
            return QSizeF(w, cmToPxF(leafParams().trashHCm) * scale);
        }
        return QSizeF(w, w * qreal(pm.height()) / qreal(pm.width()));
    }

    // 叶子与垃圾桶共用同一个缩放系数，保证预览里两者的相对大小仍然可信。
    static qreal previewUnitScale(const LeafSharedAssets& assets) {
        const QSizeF natural = trashVisualSize(assets, kPreviewFrameScale);
        if (natural.width() <= 0.0 || natural.height() <= 0.0) return 1.0;
        const qreal maxW = cmToPxF(kPreviewMaxWCm) * kPreviewFrameScale;
        const qreal maxH = cmToPxF(kPreviewMaxHCm) * kPreviewFrameScale;
        return std::min({1.0, maxW / natural.width(), maxH / natural.height()});
    }

    static QSizeF frameInnerSize(const LeafSharedAssets& assets) {
        // 框按满比例可视尺寸；绘制时再乘 kPreviewFill 留边。
        return trashVisualSize(assets, kPreviewFrameScale * previewUnitScale(assets));
    }

    void refreshFixedSize() {
        const QSizeF cell = frameInnerSize(LeafSharedAssets::instance());
        const int boxW = static_cast<int>(std::ceil(cell.width())) + 2 * kFramePad;
        const int boxH = static_cast<int>(std::ceil(cell.height())) + kFramePad + kLabelH;
        setFixedSize(2 * boxW + kGap + 2 * kOuterPad, boxH + 2 * kOuterPad);
    }

    qreal leafScale_ = 1.0;
    qreal trashScale_ = 1.0;
};

static const QString kGiftRulesKey = QStringLiteral("leaf.settings");
static constexpr int kMaxGiftRules = 10;
static const QString kNullGift = QStringLiteral("Null");

struct LeafGiftRule {
    QString gift;  // empty / Null → ignored
    QString mode = QStringLiteral("加");
    int value = 1;
    int randomMin = 1;
    int randomMax = 3;
};

static QStringList leafGiftModes() {
    return {QStringLiteral("加"), QStringLiteral("减"), QStringLiteral("随机")};
}

static QString normalizeLeafGiftName(const QString& gift) {
    const QString t = gift.trimmed();
    if (t.isEmpty() || t.compare(kNullGift, Qt::CaseInsensitive) == 0) return {};
    return t;
}

static QString leafGiftDisplayName(const QString& gift) {
    const QString t = normalizeLeafGiftName(gift);
    return t.isEmpty() ? kNullGift : t;
}

static QString leafRuleCompactLabel(const LeafGiftRule& r) {
    if (r.mode == QStringLiteral("随机")) {
        return QStringLiteral("随机 %1~%2片叶子").arg(r.randomMin).arg(r.randomMax);
    }
    return QStringLiteral("%1 %2片叶子").arg(r.mode).arg(r.value);
}

static LeafGiftRule leafRuleFromMap(const QVariantMap& m) {
    LeafGiftRule r;
    r.gift = normalizeLeafGiftName(m.value(QStringLiteral("gift")).toString());
    const QString mode = m.value(QStringLiteral("mode"), QStringLiteral("加")).toString();
    if (mode == QStringLiteral("add") || mode == QStringLiteral("+")) r.mode = QStringLiteral("加");
    else if (mode == QStringLiteral("sub") || mode == QStringLiteral("-")
             || mode == QStringLiteral("减")) r.mode = QStringLiteral("减");
    else if (mode == QStringLiteral("random") || mode == QStringLiteral("随机"))
        r.mode = QStringLiteral("随机");
    else r.mode = QStringLiteral("加");
    r.value = std::max(0, m.value(QStringLiteral("value"), 1).toInt());
    r.randomMin = std::max(0, m.value(QStringLiteral("min"),
                                      m.value(QStringLiteral("random_min"), 1)).toInt());
    r.randomMax = std::max(0, m.value(QStringLiteral("max"),
                                      m.value(QStringLiteral("random_max"),
                                              std::max(r.randomMin, 3))).toInt());
    if (r.randomMax < r.randomMin) std::swap(r.randomMin, r.randomMax);
    return r;
}

static QVariantMap leafRuleToMap(const LeafGiftRule& r) {
    QString mode = QStringLiteral("add");
    if (r.mode == QStringLiteral("减")) mode = QStringLiteral("sub");
    else if (r.mode == QStringLiteral("随机")) mode = QStringLiteral("random");
    return {
        {QStringLiteral("gift"), r.gift},
        {QStringLiteral("mode"), mode},
        {QStringLiteral("value"), r.value},
        {QStringLiteral("min"), r.randomMin},
        {QStringLiteral("max"), r.randomMax},
    };
}

static QVector<LeafGiftRule> loadLeafGiftRules() {
    QVector<LeafGiftRule> out;
    const QVariantMap root = configValue(kGiftRulesKey).toMap();
    const QVariantList rules = root.value(QStringLiteral("rules")).toList();
    for (const QVariant& item : rules) {
        out.append(leafRuleFromMap(item.toMap()));
        if (out.size() >= kMaxGiftRules) break;
    }
    return out;
}

static void saveLeafGiftRules(const QVector<LeafGiftRule>& rules) {
    QVariantList list;
    for (const LeafGiftRule& r : rules) list.append(leafRuleToMap(r));
    writeConfigValue(kGiftRulesKey, QVariantMap{{QStringLiteral("rules"), list}});
}

static QJsonObject leafSettingsPacket(const QVector<LeafGiftRule>& rules) {
    QJsonArray arr;
    for (const LeafGiftRule& r : rules) {
        arr.append(QJsonObject::fromVariantMap(leafRuleToMap(r)));
    }
    return QJsonObject{
        {QStringLiteral("op"), QStringLiteral("tool.leaf.set")},
        {QStringLiteral("settings"), QJsonObject{{QStringLiteral("rules"), arr}}},
    };
}

static void pushLeafGiftRulesToCore(const QVector<LeafGiftRule>& rules) {
    if (g_sendPacket) g_sendPacket(leafSettingsPacket(rules));
}

class LeafGiftRuleCard final : public QFrame {
public:
    explicit LeafGiftRuleCard(const LeafGiftRule& rule, QWidget* parent = nullptr)
        : QFrame(parent), rule_(rule) {
        setObjectName(QStringLiteral("LeafRuleRow"));
        setFixedHeight(76);
        build();
        loadRule(rule_);
        liveaio::util::onThemeChange(this, [this](const QString&) { refreshTheme(); });
        refreshTheme();
    }

    void setCallbacks(std::function<void()> onChanged,
                      std::function<void(LeafGiftRuleCard*)> onPick,
                      std::function<void(LeafGiftRuleCard*)> onRemove,
                      std::function<QSet<QString>()> blocked) {
        onChanged_ = std::move(onChanged);
        onPick_ = std::move(onPick);
        onRemove_ = std::move(onRemove);
        blockedFn_ = std::move(blocked);
    }

    QPushButton* pickAnchor() const { return pickBtn_; }
    QString currentGift() const { return rule_.gift; }

    void applyGift(const QString& name) {
        if (blockedFn_ && blockedFn_().contains(name)) return;
        rule_.gift = normalizeLeafGiftName(name);
        showIcon(rule_.gift, false);
        emitChange();
    }

    void refreshTheme() {
        // 固定宽按钮用更小左右 padding，避免「选择礼物」被挤扁。
        // 描边钮已自绘，主题变更只需重画。
        if (pickBtn_) pickBtn_->update();
        if (removeBtn_) removeBtn_->update();
        if (mode_) {
            mode_->setCompact(false);
            mode_->setFixedSize(88, liveaio::util::kControlH);
            mode_->refreshTheme();
        }
        if (normalizeLeafGiftName(rule_.gift).isEmpty()) showIcon({}, false);
    }

    LeafGiftRule toRule() const {
        LeafGiftRule r = rule_;
        r.mode = mode_->currentText();
        r.value = valueSpin_->value();
        r.randomMin = minSpin_->value();
        r.randomMax = maxSpin_->value();
        if (r.randomMax < r.randomMin) std::swap(r.randomMin, r.randomMax);
        return r;
    }

    void releaseIcon() {
        ++iconGen_;
        liveaio::resources::clearGiftIconOnLabel(iconLbl_);
        if (normalizeLeafGiftName(rule_.gift).isEmpty()) {
            iconLbl_->setText(kNullGift);
        } else {
            iconLbl_->setText(QStringLiteral("·"));
        }
    }

    void reloadIconDeferred(int delayMs) {
        showIcon(rule_.gift, true, delayMs);
    }

private:
    void build() {
        const int ctrlH = liveaio::util::kControlH;
        auto* root = new QHBoxLayout(this);
        // 边距略大于描边，避免圆角父级/本卡裁掉子按钮下边框。
        root->setContentsMargins(12, 12, 12, 12);
        root->setSpacing(8);
        // 行内一律按中线居中：礼物图标比控件高，靠顶对齐会显得一高一矮。
        root->setAlignment(Qt::AlignVCenter);

        pickBtn_ = new liveaio::util::ChromeButton(QStringLiteral("选择礼物"), this, ctrlH);
        pickBtn_->setFixedWidth(96);
        pickBtn_->setPadX(8);
        QObject::connect(pickBtn_, &QPushButton::clicked, this, [this]() {
            if (onPick_) onPick_(this);
        });
        root->addWidget(pickBtn_, 0, Qt::AlignVCenter);

        iconLbl_ = new QLabel(this);
        iconLbl_->setFixedSize(52, 52);
        iconLbl_->setAlignment(Qt::AlignCenter);
        iconLbl_->setScaledContents(false);
        root->addWidget(iconLbl_, 0, Qt::AlignVCenter);

        mode_ = new ThemedComboBox(this);
        mode_->setCompact(false);
        mode_->addItems(leafGiftModes());
        mode_->setFixedSize(88, ctrlH);
        mode_->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
        mode_->setOnChange([this](const QString&) {
            syncMode();
            emitChange();
        });
        root->addWidget(mode_, 0, Qt::AlignVCenter);

        valueHost_ = new QWidget(this);
        valueHost_->setFixedHeight(ctrlH);
        auto* valueLay = new QHBoxLayout(valueHost_);
        valueLay->setContentsMargins(0, 0, 0, 0);
        valueLay->setSpacing(6);
        valueLay->setAlignment(Qt::AlignVCenter);
        valueSpin_ = new liveaio::util::ThemedSpinBox(valueHost_);
        valueSpin_->setRange(0, 999);
        valueSpin_->setFixedSize(78, ctrlH);
        QObject::connect(valueSpin_, qOverload<int>(&QSpinBox::valueChanged),
                         this, [this](int) { emitChange(); });
        valueLay->addWidget(valueSpin_, 0, Qt::AlignVCenter);
        valueUnit_ = new QLabel(QStringLiteral("片叶子"), valueHost_);
        valueUnit_->setFixedHeight(ctrlH);
        valueUnit_->setMinimumWidth(48);
        valueUnit_->setAlignment(Qt::AlignVCenter | Qt::AlignLeft);
        valueLay->addWidget(valueUnit_, 0, Qt::AlignVCenter);
        root->addWidget(valueHost_, 0, Qt::AlignVCenter);

        randomHost_ = new QWidget(this);
        randomHost_->setFixedHeight(ctrlH);
        auto* randomLay = new QHBoxLayout(randomHost_);
        randomLay->setContentsMargins(0, 0, 0, 0);
        randomLay->setSpacing(6);
        randomLay->setAlignment(Qt::AlignVCenter);
        minSpin_ = new liveaio::util::ThemedSpinBox(randomHost_);
        maxSpin_ = new liveaio::util::ThemedSpinBox(randomHost_);
        minSpin_->setRange(0, 999);
        maxSpin_->setRange(0, 999);
        minSpin_->setFixedSize(72, ctrlH);
        maxSpin_->setFixedSize(72, ctrlH);
        QObject::connect(minSpin_, qOverload<int>(&QSpinBox::valueChanged),
                         this, [this](int) { emitChange(); });
        QObject::connect(maxSpin_, qOverload<int>(&QSpinBox::valueChanged),
                         this, [this](int) { emitChange(); });
        auto* tilde = new QLabel(QStringLiteral("~"), randomHost_);
        tilde->setFixedHeight(ctrlH);
        tilde->setAlignment(Qt::AlignCenter);
        auto* randUnit = new QLabel(QStringLiteral("片叶子"), randomHost_);
        randUnit->setFixedHeight(ctrlH);
        randUnit->setMinimumWidth(48);
        randUnit->setAlignment(Qt::AlignVCenter | Qt::AlignLeft);
        randomLay->addWidget(minSpin_, 0, Qt::AlignVCenter);
        randomLay->addWidget(tilde, 0, Qt::AlignVCenter);
        randomLay->addWidget(maxSpin_, 0, Qt::AlignVCenter);
        randomLay->addWidget(randUnit, 0, Qt::AlignVCenter);
        root->addWidget(randomHost_, 0, Qt::AlignVCenter);

        root->addStretch(1);
        removeBtn_ = new liveaio::util::ChromeButton(QStringLiteral("删除"), this, ctrlH,
                                                     liveaio::util::ControlVariant::Neutral);
        removeBtn_->setFixedWidth(72);
        removeBtn_->setPadX(10);
        QObject::connect(removeBtn_, &QPushButton::clicked, this, [this]() {
            if (onRemove_) onRemove_(this);
        });
        root->addWidget(removeBtn_, 0, Qt::AlignVCenter);
    }

    void loadRule(const LeafGiftRule& rule) {
        loading_ = true;
        rule_ = rule;
        mode_->setCurrentText(rule.mode);
        valueSpin_->setValue(rule.value);
        minSpin_->setValue(rule.randomMin);
        maxSpin_->setValue(rule.randomMax);
        loading_ = false;
        syncMode();
        if (normalizeLeafGiftName(rule.gift).isEmpty()) {
            showIcon({}, false);
        } else {
            iconLbl_->setText(QStringLiteral("·"));
        }
    }

    void showIcon(const QString& gift, bool deferred, int delayMs = 0) {
        const QString name = normalizeLeafGiftName(gift);
        ++iconGen_;
        const QString root = g_appRoot;
        if (name.isEmpty()) {
            liveaio::resources::clearGiftIconOnLabel(iconLbl_);
            iconLbl_->setText(kNullGift);
            iconLbl_->setStyleSheet(QStringLiteral(
                "color: %1; background: transparent; font-size: 11px;").arg(theme().textMuted));
            return;
        }
        iconLbl_->setText(QStringLiteral("·"));
        iconLbl_->setStyleSheet(QStringLiteral("background: transparent; border: none;"));
        // 设置页用缩略静图，避免动图播放器抬内存；与选择器同源解码。
        auto load = [this, name, root]() {
            liveaio::resources::setGiftIconOnLabel(iconLbl_, root, name, 48, true);
        };
        if (!deferred) {
            load();
            return;
        }
        const quint64 gen = iconGen_;
        QPointer<LeafGiftRuleCard> guard(this);
        QTimer::singleShot(std::max(0, delayMs), this, [guard, gen, load]() {
            if (!guard || gen != guard->iconGen_) return;
            load();
        });
    }

    void syncMode() {
        const bool isRand = mode_->currentText() == QStringLiteral("随机");
        valueHost_->setVisible(!isRand);
        randomHost_->setVisible(isRand);
    }

    void emitChange() {
        if (loading_) return;
        rule_ = toRule();
        if (onChanged_) onChanged_();
    }

    LeafGiftRule rule_;
    bool loading_ = false;
    quint64 iconGen_ = 0;
    liveaio::util::ChromeButton* pickBtn_ = nullptr;
    QLabel* iconLbl_ = nullptr;
    ThemedComboBox* mode_ = nullptr;
    QWidget* valueHost_ = nullptr;
    QWidget* randomHost_ = nullptr;
    QSpinBox* valueSpin_ = nullptr;
    QSpinBox* minSpin_ = nullptr;
    QSpinBox* maxSpin_ = nullptr;
    QLabel* valueUnit_ = nullptr;
    liveaio::util::ChromeButton* removeBtn_ = nullptr;
    std::function<void()> onChanged_;
    std::function<void(LeafGiftRuleCard*)> onPick_;
    std::function<void(LeafGiftRuleCard*)> onRemove_;
    std::function<QSet<QString>()> blockedFn_;
};

class LeafOverlayRuleCard final : public QFrame {
public:
    explicit LeafOverlayRuleCard(QWidget* parent = nullptr) : QFrame(parent) {
        // 半透明悬浮窗上 QSS 背景经常不画，必须自绘 + StyledBackground。
        setAttribute(Qt::WA_StyledBackground, true);
        setAutoFillBackground(false);
        setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);
    }

protected:
    void paintEvent(QPaintEvent*) override {
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing, true);
        p.setPen(QPen(QColor(255, 255, 255, 52), 1.0));
        p.setBrush(QColor(96, 98, 106, 150));
        p.drawRoundedRect(QRectF(rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6.0, 6.0);
    }
};

class LeafRulesStrip final : public QWidget {
public:
    explicit LeafRulesStrip(QWidget* parent = nullptr) : QWidget(parent) {
        setAttribute(Qt::WA_TransparentForMouseEvents);
        setAttribute(Qt::WA_TranslucentBackground);
        auto* lay = new QVBoxLayout(this);
        // 宽度缩约 1/4；字号/行高保持可读，不跟着压扁。
        lay->setContentsMargins(6, 6, 6, 6);
        lay->setSpacing(5);
        lay_ = lay;
        setRules({});
    }

    void setRules(const QVector<LeafGiftRule>& rules) {
        releaseHeavyResources();
        while (QLayoutItem* item = lay_->takeAt(0)) {
            if (QWidget* w = item->widget()) {
                w->hide();
                w->setParent(nullptr);
                w->deleteLater();
            }
            delete item;
        }
        iconRows_.clear();
        ++loadGen_;
        int shown = 0;
        for (const LeafGiftRule& r : rules) {
            if (normalizeLeafGiftName(r.gift).isEmpty()) continue;
            auto* card = new LeafOverlayRuleCard(this);
            auto* hl = new QHBoxLayout(card);
            hl->setContentsMargins(8, 5, 8, 5);
            hl->setSpacing(6);
            auto* icon = new QLabel(card);
            icon->setFixedSize(22, 22);
            icon->setAlignment(Qt::AlignCenter);
            icon->setAttribute(Qt::WA_TranslucentBackground);
            icon->setStyleSheet(QStringLiteral("background: transparent; border: none;"));
            icon->setText(QStringLiteral("·"));
            auto* col = new QVBoxLayout;
            col->setContentsMargins(0, 0, 0, 0);
            col->setSpacing(1);
            auto* name = new QLabel(leafGiftDisplayName(r.gift), card);
            name->setAttribute(Qt::WA_TranslucentBackground);
            name->setStyleSheet(QStringLiteral(
                "background: transparent; border: none;"
                " color: rgba(255,255,255,235); font-size: 11px; font-weight: 600;"));
            auto* detail = new QLabel(leafRuleCompactLabel(r), card);
            detail->setAttribute(Qt::WA_TranslucentBackground);
            detail->setStyleSheet(QStringLiteral(
                "background: transparent; border: none;"
                " color: rgba(210,210,215,200); font-size: 10px;"));
            col->addWidget(name);
            col->addWidget(detail);
            hl->addWidget(icon, 0, Qt::AlignVCenter);
            hl->addLayout(col, 1);
            card->setMinimumHeight(32);
            lay_->addWidget(card, 0, Qt::AlignTop);
            iconRows_.append({icon, r.gift});
            ++shown;
        }
        if (shown == 0) {
            auto* tip = new LeafOverlayRuleCard(this);
            auto* tipLay = new QVBoxLayout(tip);
            tipLay->setContentsMargins(8, 6, 8, 6);
            auto* tipLbl = new QLabel(QStringLiteral("未配置有效礼物规则"), tip);
            tipLbl->setAttribute(Qt::WA_TranslucentBackground);
            tipLbl->setStyleSheet(QStringLiteral(
                "background: transparent; border: none;"
                " color: rgba(255,255,255,200); font-size: 10px;"));
            tipLay->addWidget(tipLbl);
            tip->setMinimumHeight(28);
            lay_->addWidget(tip, 0, Qt::AlignTop);
        }
        lay_->addStretch(1);
        updateGeometry();
        scheduleDeferredIconLoads();
    }

    void scheduleDeferredIconLoads() {
        const quint64 gen = loadGen_;
        for (int i = 0; i < iconRows_.size(); ++i) {
            QPointer<QLabel> icon = iconRows_[i].icon;
            const QString gift = iconRows_[i].gift;
            QTimer::singleShot(40 * i, this, [this, gen, icon, gift]() {
                if (gen != loadGen_ || !icon) return;
                liveaio::resources::setGiftIconOnLabel(icon, g_appRoot, gift, 20, true);
            });
        }
    }

    void releaseHeavyResources() {
        ++loadGen_;
        for (const IconRow& row : iconRows_) {
            if (row.icon) liveaio::resources::clearGiftIconOnLabel(row.icon);
        }
    }

    QSize sizeHint() const override {
        if (!lay_) return QSize(160, 40);
        const QMargins m = lay_->contentsMargins();
        int h = m.top() + m.bottom();
        int rows = 0;
        for (int i = 0; i < lay_->count(); ++i) {
            QLayoutItem* item = lay_->itemAt(i);
            if (!item || item->spacerItem() || !item->widget()) continue;
            h += std::max(item->widget()->minimumHeight(),
                          item->widget()->sizeHint().height());
            ++rows;
        }
        if (rows > 1) h += lay_->spacing() * (rows - 1);
        return QSize(width() > 0 ? width() : 160, std::max(40, h));
    }

protected:
    void paintEvent(QPaintEvent*) override {
        // 规则条本身透明，只靠卡片自绘底色。
    }

private:
    struct IconRow {
        QPointer<QLabel> icon;
        QString gift;
    };

    QVBoxLayout* lay_ = nullptr;
    QVector<IconRow> iconRows_;
    quint64 loadGen_ = 0;
};

static QPointF closestPointOnSegment(const QPointF& a, const QPointF& b, const QPointF& p) {
    const QPointF ab = b - a;
    const qreal len2 = QPointF::dotProduct(ab, ab);
    if (len2 < 1e-8) return a;
    const qreal t = std::clamp(QPointF::dotProduct(p - a, ab) / len2, 0.0, 1.0);
    return a + ab * t;
}

// Free：池内空槽，可复用。Spawning/Removing 不参与物理。
enum class LeafState { Free, Spawning, Idle, Dragging, Removing };

struct LeafBody {
    QPointF pos;
    QPointF vel;
    qreal angle = 0.0;
    qreal angVel = 0.0;
    qreal alpha = 1.0;
    qreal removeT = 0.0;
    qreal spawnT = 0.0;
    qreal fadeFrom = 1.0;
    QPointF birthVel;
    qreal birthAngVel = 0.0;
    LeafState state = LeafState::Free;
    quint64 id = 0;
};

static bool isPhysical(const LeafState s) {
    return s == LeafState::Idle || s == LeafState::Dragging;
}

static bool isAlive(const LeafState s) {
    return s != LeafState::Free && s != LeafState::Removing;
}

static void resetLeafBody(LeafBody* L) {
    *L = LeafBody{};
    L->state = LeafState::Free;
    L->alpha = 0.0;
}

class LeafCanvas final : public QWidget {
public:
    explicit LeafCanvas(QWidget* parent = nullptr) : QWidget(parent) {
        leafScale_ = scalePercent(kLeafScaleKey) / 100.0;
        trashScale_ = scalePercent(kTrashScaleKey) / 100.0;
        preferredPercent_ = configuredMaxPercent();
        setAttribute(Qt::WA_TranslucentBackground);
        setMouseTracking(true);
        setFocusPolicy(Qt::NoFocus);
        LeafSharedAssets::instance();
        loadTrashPos();
        loadFaceShield();
        tick_ = new QTimer(this);
        tick_->setInterval(kTickMs);
        QObject::connect(tick_, &QTimer::timeout, this, [this]() { onTick(); });
        tick_->start();
    }

    bool faceShieldEnabled() const { return faceShieldEnabled_; }
    bool faceShieldEditing() const { return faceShieldEditing_; }

    void setFaceShieldEnabled(bool on) {
        if (faceShieldEnabled_ == on) return;
        faceShieldEnabled_ = on;
        writeConfigValue(kFaceShieldEnabledKey, on);
        if (!faceShieldEditing_) notifyGeometryChanged();
        update();
    }

    void setFaceShieldEditing(bool on) {
        if (faceShieldEditing_ == on) return;
        faceShieldEditing_ = on;
        if (on) {
            ensureFaceShieldRect();
            int reclaim = 0;
            for (LeafBody& L : leaves_) {
                if (!isAlive(L.state)) continue;
                ++reclaim;
                resetLeafBody(&L);
            }
            leaves_.clear();
            pendingSpawns_ = std::min(kPendingQueueCap, pendingSpawns_ + reclaim);
            pendingRemovals_ = 0;
            dragId_ = 0;
            dragTargetValid_ = false;
            draggingTrash_ = false;
            maxLeaves_ = 0;
            emitStats();
        } else {
            saveFaceShield();
            notifyGeometryChanged();
        }
        update();
    }

    int aliveCount() const {
        int n = 0;
        for (const LeafBody& L : leaves_) {
            if (isAlive(L.state)) ++n;
        }
        return n;
    }

    int maxLeaves() const { return maxLeaves_; }
    int theoreticalCapacity() const { return theoreticalCapacity_; }
    int maxPercent() const { return preferredPercent_; }
    int pendingCount() const { return pendingSpawns_ + pendingRemovals_; }
    int leafScalePercent() const { return qRound(leafScale_ * 100.0); }
    int trashScalePercent() const { return qRound(trashScale_ * 100.0); }

    void applySettings(int maxPercent, int leafPercent, int trashPercent) {
        leafPercent = std::clamp(leafPercent, 50, 200);
        trashPercent = std::clamp(trashPercent, 50, 200);
        leafScale_ = leafPercent / 100.0;
        trashScale_ = trashPercent / 100.0;
        preferredPercent_ = clampCapPercent(maxPercent);
        writeConfigValue(kLeafScaleKey, leafPercent);
        writeConfigValue(kTrashScaleKey, trashPercent);
        writeConfigValue(kMaxPercentKey, preferredPercent_);
        notifyGeometryChanged();
    }

    void reloadSkin() {
        notifyGeometryChanged();
        update();
    }

    void enqueueSpawn(int count = 1) {
        int left = std::max(1, count);
        const int cancel = std::min(left, pendingRemovals_);
        pendingRemovals_ -= cancel;
        left -= cancel;
        pendingSpawns_ = std::min(kPendingQueueCap, pendingSpawns_ + left);
        flushSpawns();
        emitStats();
        update();
    }

    void enqueueRemove(int count = 1) {
        int left = std::max(1, count);
        const int cancel = std::min(left, pendingSpawns_);
        pendingSpawns_ -= cancel;
        left -= cancel;
        pendingRemovals_ = std::min(kPendingQueueCap, pendingRemovals_ + left);
        flushRemovals();
        emitStats();
        update();
    }

    void applyLeafDelta(int delta) {
        if (delta > 0) enqueueSpawn(delta);
        else if (delta < 0) enqueueRemove(-delta);
    }

    void beginResizePause() {
        if (resizePaused_) return;
        resizePaused_ = true;
        if (tick_->isActive()) tick_->stop();
    }

    void commitViewport(const QRect& geometry) {
        suppressResizeCommit_ = true;
        setGeometry(geometry);
        suppressResizeCommit_ = false;
        notifyGeometryChanged();
    }

    void endResizePause() {
        resizePaused_ = false;
        if (!tick_->isActive()) tick_->start();
    }

    bool resizePaused() const { return resizePaused_; }

    bool hitInteractive(const QPointF& pos) const {
        if (faceShieldEditing_) {
            if (shieldHitRect().adjusted(-6, -6, 6, 6).contains(pos)) return true;
            return true;  // 编辑态整块 content 吃鼠标（暗化遮罩）
        }
        if (draggingTrash_ || dragId_ != 0) return true;
        if (trashNearRect().contains(pos)) return true;
        for (int i = leaves_.size() - 1; i >= 0; --i) {
            const LeafBody& L = leaves_[i];
            if (!isPhysical(L.state)) continue;
            if (hitSoft(L, pos)) return true;
        }
        return false;
    }

    void setStatsCallback(std::function<void()> cb) { statsCb_ = std::move(cb); }

    // 关窗/卸挂时清队列与实例，停表，避免残留定时器与幽灵叶子。
    void shutdown() {
        if (tick_) tick_->stop();
        pendingSpawns_ = 0;
        pendingRemovals_ = 0;
        spawnCooldown_ = 0.0;
        removeCooldown_ = 0.0;
        dragId_ = 0;
        dragTargetValid_ = false;
        draggingTrash_ = false;
        for (LeafBody& L : leaves_) resetLeafBody(&L);
        leaves_.clear();
        statsCb_ = {};
    }

    void notifyGeometryChanged() {
        ensureTrashPosition();
        ensureFaceShieldRect();
        clampFaceShieldToWorld();
        updateWorld();
        if (faceShieldEditing_) {
            maxLeaves_ = 0;
            update();
            emitStats();
            return;
        }
        cullOverflowImmediate();
        clampTrashToWorld();
        squeezeIntoBounds();
        update();
        emitStats();
    }

protected:
    void resizeEvent(QResizeEvent* event) override {
        QWidget::resizeEvent(event);
        if (!resizePaused_ && !suppressResizeCommit_) notifyGeometryChanged();
    }

    void paintEvent(QPaintEvent*) override {
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing, true);
        p.setRenderHint(QPainter::SmoothPixmapTransform, true);

        if (faceShieldEditing_) {
            // 暗化只盖边框内 content，不含外圈阴影带；与框同帧高质量绘制。
            p.setRenderHint(QPainter::Antialiasing, true);
            const int sw = RippleOverlayRoot::kShadowW;
            p.fillRect(QRect(sw, 0, std::max(0, width() - 2 * sw),
                             std::max(0, height() - sw)),
                       QColor(0, 0, 0, 110));
            drawFaceShieldEdit(p);
            return;
        }

        const auto& assets = LeafSharedAssets::instance();
        const qreal side = leafVisualPx(leafScale_);

        for (const LeafBody& L : leaves_) {
            if (L.state == LeafState::Free || L.state == LeafState::Dragging) continue;
            drawLeaf(p, L, side, assets.leafPm, assets.leafHaloPm, 0.0);
        }

        drawTrash(p, assets);

        for (const LeafBody& L : leaves_) {
            if (L.state != LeafState::Dragging) continue;
            drawLeaf(p, L, side, assets.leafPm, assets.leafHaloPm, leafHalo_);
        }
    }

    void mousePressEvent(QMouseEvent* event) override {
        if (event->button() != Qt::LeftButton) return;
        const QPointF pos = event->position();
        if (faceShieldEditing_) {
            beginFaceShieldDrag(pos);
            return;
        }
        // 垃圾桶图层在闲置叶子之上：先点垃圾桶。
        if (trashGrabRect().contains(pos)) {
            draggingTrash_ = true;
            dragOffset_ = trashCenter() - pos;
            setCursor(Qt::ClosedHandCursor);
            update();
            return;
        }
        for (int i = leaves_.size() - 1; i >= 0; --i) {
            LeafBody& L = leaves_[i];
            if (!isPhysical(L.state)) continue;
            if (!hitSoft(L, pos)) continue;
            L.state = LeafState::Dragging;
            L.vel = {};
            L.angVel = 0.0;
            dragId_ = L.id;
            dragOffset_ = L.pos - pos;
            dragTarget_ = L.pos;
            dragVelocity_ = {};
            dragTargetValid_ = true;
            update();
            return;
        }
    }

    void mouseMoveEvent(QMouseEvent* event) override {
        const QPointF pos = event->position();
        if (faceShieldEditing_) {
            if (event->buttons() & Qt::LeftButton) {
                updateFaceShieldDrag(pos);
            } else {
                setCursor(faceShieldCursorAt(pos));
            }
            return;
        }
        const bool hover = trashNearRect().contains(pos);
        if (hoverTrash_ != hover) {
            hoverTrash_ = hover;
            if (!(event->buttons() & Qt::LeftButton)) update();
        }
        if (!(event->buttons() & Qt::LeftButton)) {
            setCursor(hover ? Qt::OpenHandCursor : Qt::ArrowCursor);
            return;
        }
        if (draggingTrash_) {
            setTrashCenter(pos + dragOffset_);
            update();
            return;
        }
        if (dragId_ == 0) return;
        dragTarget_ = pos + dragOffset_;
        dragTargetValid_ = true;
        update();
    }

    void mouseReleaseEvent(QMouseEvent* event) override {
        if (event->button() != Qt::LeftButton) return;
        if (faceShieldEditing_) {
            endFaceShieldDrag();
            setCursor(faceShieldCursorAt(event->position()));
            return;
        }
        if (draggingTrash_) {
            draggingTrash_ = false;
            saveTrashPos();
            hoverTrash_ = trashNearRect().contains(event->position());
            setCursor(hoverTrash_ ? Qt::OpenHandCursor : Qt::ArrowCursor);
            update();
            return;
        }
        if (dragId_ == 0) return;
        for (LeafBody& L : leaves_) {
            if (L.id != dragId_) continue;
            if (trashHitRect().contains(L.pos)) {
                // 垃圾桶吊销：立即退出物理，随后只播淡出。
                beginRemoval(L);
            } else {
                L.state = LeafState::Idle;
                L.vel = dragVelocity_ * 0.35;
                clampSpeed(L);
            }
            break;
        }
        dragId_ = 0;
        dragTargetValid_ = false;
        dragVelocity_ = {};
        hoverTrash_ = trashNearRect().contains(event->position());
        emitStats();
        update();
    }

    void leaveEvent(QEvent* event) override {
        QWidget::leaveEvent(event);
        if (draggingTrash_) return;
        if (hoverTrash_) {
            hoverTrash_ = false;
            update();
        }
    }

private:
    static void drawLeaf(QPainter& p, const LeafBody& L, qreal side, const QPixmap& pm,
                         const QPixmap& haloPm, qreal halo) {
        if (L.alpha <= 0.01) return;
        p.save();
        p.translate(L.pos);
        p.rotate(L.angle * 180.0 / kPi);
        // 淡入淡出为主，仅配一点轻微缩放，避免"啪"地跳出跳没。
        qreal scale = 1.0;
        if (L.state == LeafState::Removing) {
            scale = 1.0 - L.removeT * (2.0 - L.removeT) * 0.12;
        } else if (L.state == LeafState::Spawning) {
            scale = 0.92 + L.spawnT * 0.08;
        }
        const qreal drawSide = side * scale;
        const QRectF dst(-drawSide * 0.5, -drawSide * 0.5, drawSide, drawSide);
        if (halo > 0.01) {
            drawCenteredHalo(p, dst, haloPm, halo * leafParams().leafHaloAmount * L.alpha,
                             cmToPxF(leafParams().leafHaloOuterCm) * side / leafVisualPx());
        }
        p.setOpacity(L.alpha);
        p.drawPixmap(fitPixmap(dst, pm).toRect(), pm);
        p.restore();
    }

    void drawTrash(QPainter& p, const LeafSharedAssets& assets) const {
        const QRectF trash = trashDrawRect();
        if (trashHalo_ > 0.01) {
            const qreal expand = cmToPxF(leafParams().trashHaloOuterCm) * trashScale_;
            const qreal amt = trashHalo_ * leafParams().trashHaloAmount;
            if (lidOpen_ < 0.999) {
                drawCenteredHalo(p, trash, assets.trashHaloPm, amt * (1.0 - lidOpen_),
                                 expand);
            }
            if (lidOpen_ > 0.001) {
                drawCenteredHalo(p, trash, assets.trashOpenHaloPm, amt * lidOpen_,
                                 expand);
            }
        }
        if (lidOpen_ < 0.999 && !assets.trashPm.isNull()) {
            p.setOpacity(1.0 - lidOpen_);
            p.drawPixmap(fitPixmap(trash, assets.trashPm).toRect(), assets.trashPm);
        }
        if (lidOpen_ > 0.001 && !assets.trashOpenPm.isNull()) {
            p.setOpacity(lidOpen_);
            p.drawPixmap(fitPixmap(trash, assets.trashOpenPm).toRect(), assets.trashOpenPm);
        }
        p.setOpacity(1.0);
    }

    void emitStats() {
        if (statsCb_) statsCb_();
    }

    QRectF worldRect() const {
        const qreal pad = cmToPxF(kWorldPadCm);
        return QRectF(pad, pad, width() - 2.0 * pad, height() - 2.0 * pad);
    }

    QSizeF trashSize() const {
        return QSizeF(cmToPxF(leafParams().trashWCm) * trashScale_,
                      cmToPxF(leafParams().trashHCm) * trashScale_);
    }

    QPointF trashCenter() const {
        const QRectF w = worldRect();
        const QSizeF sz = trashSize();
        if (w.width() <= 1.0 || w.height() <= 1.0) {
            return QPointF(width() * 0.5, height() * 0.75);
        }
        QPointF c = trashPosReady_
            ? trashCenterPx_
            : QPointF(w.right() - sz.width() * 0.5, w.top() + sz.height() * 0.5);
        const qreal hx = sz.width() * 0.5;
        const qreal hy = sz.height() * 0.5;
        if (w.width() < sz.width()) c.setX(w.center().x());
        else c.setX(clampAxis(c.x(), w.left() + hx, w.right() - hx));
        if (w.height() < sz.height()) c.setY(w.center().y());
        else c.setY(clampAxis(c.y(), w.top() + hy, w.bottom() - hy));
        return c;
    }

    void setTrashCenter(const QPointF& cIn) {
        const QRectF w = worldRect();
        const QSizeF sz = trashSize();
        if (w.width() <= 1.0 || w.height() <= 1.0) return;
        const qreal hx = sz.width() * 0.5;
        const qreal hy = sz.height() * 0.5;
        QPointF c = cIn;
        if (w.width() < sz.width()) c.setX(w.center().x());
        else c.setX(clampAxis(c.x(), w.left() + hx, w.right() - hx));
        if (w.height() < sz.height()) c.setY(w.center().y());
        else c.setY(clampAxis(c.y(), w.top() + hy, w.bottom() - hy));
        trashCenterPx_ = c;
        trashPosReady_ = true;
    }

    void clampTrashToWorld() {
        ensureTrashPosition();
        setTrashCenter(trashCenterPx_);
    }

    QRectF trashDrawRect() const {
        const QPointF c = trashCenter();
        const QSizeF sz = trashSize();
        return QRectF(c.x() - sz.width() * 0.5, c.y() - sz.height() * 0.5,
                      sz.width(), sz.height());
    }

    QRectF trashGrabRect() const {
        QRectF r = trashDrawRect();
        if (lidOpen_ < 0.45) {
            r.setTop(r.top() + r.height() * leafParams().trashGrabTopFrac);
        }
        return r;
    }

    QRectF trashHitRect() const {
        const QRectF draw = trashDrawRect();
        const auto& params = leafParams();
        const qreal topFrac =
            lidOpen_ < 0.45 ? params.trashHitTopFrac : params.trashHitTopOpenFrac;
        QRectF r(QPointF(draw.left() + draw.width() * params.trashHitLeftFrac,
                         draw.top() + draw.height() * topFrac),
                 QPointF(draw.left() + draw.width() * params.trashHitRightFrac,
                         draw.top() + draw.height() * params.trashHitBottomFrac));
        r = r.normalized();
        const qreal pad = cmToPxF(leafParams().trashHitPadCm);
        return r.adjusted(-pad, -pad, pad, pad);
    }

    QRectF trashNearRect() const {
        const qreal pad = cmToPxF(leafParams().trashNearPadCm);
        return trashDrawRect().adjusted(-pad, -pad, pad, pad);
    }

    bool trashArmed() const {
        if (draggingTrash_ || hoverTrash_) return true;
        if (dragId_ == 0) return false;
        for (const LeafBody& L : leaves_) {
            if (L.id != dragId_) continue;
            return trashNearRect().contains(L.pos);
        }
        return false;
    }

    void stepLid(qreal dt) {
        stepToward(&lidOpen_, trashArmed() ? 1.0 : 0.0, dt, kLidAnimSec);
    }

    void stepHalo(qreal dt) {
        const qreal armed = trashArmed() ? 1.0 : 0.0;
        stepToward(&trashHalo_, armed, dt, kHaloAnimSec);
        stepToward(&leafHalo_, (dragId_ != 0) ? 1.0 : 0.0, dt, kHaloAnimSec);
    }

    void loadTrashPos() {
        const QVariantMap m = configValue(kTrashPosKey).toMap();
        if (m.value(QStringLiteral("mode")).toString() == QStringLiteral("px")) {
            trashCenterPx_ = QPointF(m.value(QStringLiteral("x")).toDouble(),
                                     m.value(QStringLiteral("y")).toDouble());
            trashPosReady_ = true;
            return;
        }
        if (!m.isEmpty()) {
            legacyTrashNorm_ = QPointF(
                std::clamp(m.value(QStringLiteral("x"), 0.5).toDouble(), 0.0, 1.0),
                std::clamp(m.value(QStringLiteral("y"), 1.0).toDouble(), 0.0, 1.0));
            hasLegacyTrashNorm_ = true;
        }
    }

    void ensureTrashPosition() {
        if (trashPosReady_) return;
        const QRectF w = worldRect();
        const QSizeF sz = trashSize();
        if (w.width() <= 1.0 || w.height() <= 1.0) return;
        const bool migrateLegacy = hasLegacyTrashNorm_;
        if (hasLegacyTrashNorm_) {
            trashCenterPx_ = QPointF(w.left() + legacyTrashNorm_.x() * w.width(),
                                     w.top() + legacyTrashNorm_.y() * w.height());
        } else {
            // 首次：右上角。
            trashCenterPx_ = QPointF(w.right() - sz.width() * 0.5,
                                     w.top() + sz.height() * 0.5);
        }
        trashPosReady_ = true;
        setTrashCenter(trashCenterPx_);
        hasLegacyTrashNorm_ = false;
        if (migrateLegacy) saveTrashPos();
    }

    void saveTrashPos() const {
        writeConfigValue(kTrashPosKey, QVariantMap{
            {QStringLiteral("mode"), QStringLiteral("px")},
            {QStringLiteral("x"), trashCenterPx_.x()},
            {QStringLiteral("y"), trashCenterPx_.y()},
        });
    }

    qreal faceShieldMinSide() const {
        return std::max(24.0, leafPackSide(leafScale_) * 2.0);
    }

    QRectF shieldHitRect() const {
        if (!faceShieldReady_) return {};
        return QRectF(faceShieldOrigin_, QSizeF(faceShieldSide_, faceShieldSide_));
    }

    QPolygonF housePolygon(const QRectF& square) const {
        const qreal side = square.width();
        const qreal peak = side * faceShieldPeakFrac_;
        QPolygonF poly;
        poly << QPointF(square.center().x(), square.top() - peak)
             << QPointF(square.right(), square.top())
             << QPointF(square.right(), square.bottom())
             << QPointF(square.left(), square.bottom())
             << QPointF(square.left(), square.top());
        return poly;
    }

    QPointF peakPoint() const {
        const QRectF r = shieldHitRect();
        return QPointF(r.center().x(), r.top() - r.width() * faceShieldPeakFrac_);
    }

    QPointF houseCentroid(const QRectF& square) const {
        const QPolygonF poly = housePolygon(square);
        QPointF sum;
        for (const QPointF& p : poly) sum += p;
        return sum / qreal(poly.size());
    }

    bool shieldActivePlay() const {
        return faceShieldEnabled_ && faceShieldReady_ && !faceShieldEditing_;
    }

    // 圆 vs 屋脊多边形：返回指向外侧的法线与穿透深度。
    bool queryHouseContact(const QPointF& c, qreal radius, const QRectF& square,
                           QPointF* normal, qreal* penetration, bool* insideOut) const {
        const QPolygonF poly = housePolygon(square);
        const QPointF centroid = houseCentroid(square);
        const bool inside = poly.containsPoint(c, Qt::OddEvenFill);
        if (insideOut) *insideOut = inside;

        qreal bestDist = 1.0e30;
        QPointF bestClosest;
        QPointF bestOutN;
        const int n = poly.size();
        for (int i = 0; i < n; ++i) {
            const QPointF a = poly[i];
            const QPointF b = poly[(i + 1) % n];
            const QPointF closest = closestPointOnSegment(a, b, c);
            QPointF edge = b - a;
            const qreal elen = std::hypot(edge.x(), edge.y());
            if (elen < 1e-6) continue;
            edge /= elen;
            QPointF outN(edge.y(), -edge.x());
            if (QPointF::dotProduct(outN, centroid - a) > 0.0) outN = -outN;
            const qreal d = QLineF(c, closest).length();
            if (d < bestDist) {
                bestDist = d;
                bestClosest = closest;
                bestOutN = outN;
            }
        }
        if (bestDist >= 1.0e29) return false;

        if (inside) {
            if (normal) *normal = bestOutN;
            if (penetration) *penetration = bestDist + radius;
            return true;
        }
        if (bestDist >= radius) return false;
        QPointF out = c - bestClosest;
        const qreal nl = std::hypot(out.x(), out.y());
        if (nl > 1e-6) out /= nl;
        else out = bestOutN;
        if (normal) *normal = out;
        if (penetration) *penetration = radius - bestDist;
        return true;
    }

    void loadFaceShield() {
        faceShieldEnabled_ = configValue(kFaceShieldEnabledKey, false).toBool();
        const QVariantMap m = configValue(kFaceShieldRectKey).toMap();
        if (m.value(QStringLiteral("mode")).toString() == QStringLiteral("px")) {
            faceShieldOrigin_ = QPointF(m.value(QStringLiteral("x")).toDouble(),
                                        m.value(QStringLiteral("y")).toDouble());
            faceShieldSide_ = m.value(QStringLiteral("side")).toDouble();
            faceShieldPeakFrac_ = std::clamp(
                m.value(QStringLiteral("peak"), kShieldPeakFrac).toDouble(), 0.05, 0.55);
            faceShieldReady_ = faceShieldSide_ > 1.0;
        }
    }

    void saveFaceShield() const {
        if (!faceShieldReady_) return;
        writeConfigValue(kFaceShieldRectKey, QVariantMap{
            {QStringLiteral("mode"), QStringLiteral("px")},
            {QStringLiteral("x"), faceShieldOrigin_.x()},
            {QStringLiteral("y"), faceShieldOrigin_.y()},
            {QStringLiteral("side"), faceShieldSide_},
            {QStringLiteral("peak"), faceShieldPeakFrac_},
        });
    }

    void ensureFaceShieldRect() {
        const QRectF w = worldRect();
        if (w.width() <= 1.0 || w.height() <= 1.0) return;
        if (!faceShieldReady_) {
            const qreal side = std::max(faceShieldMinSide(),
                                        std::min(w.width(), w.height()) * 0.25);
            faceShieldSide_ = side;
            faceShieldOrigin_ = QPointF(w.center().x() - side * 0.5,
                                        w.center().y() - side * 0.5);
            faceShieldReady_ = true;
        }
        clampFaceShieldToWorld();
    }

    void clampFaceShieldToWorld() {
        if (!faceShieldReady_) return;
        const QRectF w = worldRect();
        if (w.width() <= 1.0 || w.height() <= 1.0) return;
        faceShieldPeakFrac_ = std::clamp(faceShieldPeakFrac_, 0.05, 0.55);
        const qreal peak = faceShieldSide_ * faceShieldPeakFrac_;
        const qreal minSide = std::min(faceShieldMinSide(),
                                       std::min(w.width(), std::max(8.0, w.height() - peak)));
        faceShieldSide_ = std::clamp(faceShieldSide_, minSide,
                                     std::min(w.width(), std::max(minSide, w.height() - peak)));
        const qreal maxX = w.right() - faceShieldSide_;
        const qreal maxY = w.bottom() - faceShieldSide_;
        const qreal minY = w.top() + faceShieldSide_ * faceShieldPeakFrac_;
        faceShieldOrigin_.setX(clampAxis(faceShieldOrigin_.x(), w.left(), maxX));
        faceShieldOrigin_.setY(clampAxis(faceShieldOrigin_.y(), minY, maxY));
    }

    void setFaceShieldSquare(const QRectF& square) {
        faceShieldSide_ = std::max(faceShieldMinSide(),
                                   std::min(square.width(), square.height()));
        faceShieldOrigin_ = square.topLeft();
        faceShieldReady_ = true;
        clampFaceShieldToWorld();
    }

    enum class ShieldDragMode { None, Move, ResizeNW, ResizeNE, ResizeSW, ResizeSE,
                                ResizeN, ResizeS, ResizeW, ResizeE, Peak };

    ShieldDragMode hitFaceShieldHandle(const QPointF& pos) const {
        const QRectF r = shieldHitRect();
        if (r.isEmpty()) return ShieldDragMode::None;
        const qreal hs = 8.0;
        auto nearPt = [&](const QPointF& pt) {
            return QLineF(pos, pt).length() <= hs;
        };
        // 尖顶优先于边/角，便于上下拖。
        if (nearPt(peakPoint())) return ShieldDragMode::Peak;
        if (nearPt(r.topLeft())) return ShieldDragMode::ResizeNW;
        if (nearPt(r.topRight())) return ShieldDragMode::ResizeNE;
        if (nearPt(r.bottomLeft())) return ShieldDragMode::ResizeSW;
        if (nearPt(r.bottomRight())) return ShieldDragMode::ResizeSE;
        if (std::abs(pos.y() - r.top()) <= hs && pos.x() >= r.left() && pos.x() <= r.right())
            return ShieldDragMode::ResizeN;
        if (std::abs(pos.y() - r.bottom()) <= hs && pos.x() >= r.left() && pos.x() <= r.right())
            return ShieldDragMode::ResizeS;
        if (std::abs(pos.x() - r.left()) <= hs && pos.y() >= r.top() && pos.y() <= r.bottom())
            return ShieldDragMode::ResizeW;
        if (std::abs(pos.x() - r.right()) <= hs && pos.y() >= r.top() && pos.y() <= r.bottom())
            return ShieldDragMode::ResizeE;
        if (r.contains(pos)) return ShieldDragMode::Move;
        return ShieldDragMode::None;
    }

    Qt::CursorShape faceShieldCursorAt(const QPointF& pos) const {
        switch (hitFaceShieldHandle(pos)) {
        case ShieldDragMode::Peak:
        case ShieldDragMode::ResizeN:
        case ShieldDragMode::ResizeS:
            return Qt::SizeVerCursor;
        case ShieldDragMode::ResizeNW:
        case ShieldDragMode::ResizeSE:
            return Qt::SizeFDiagCursor;
        case ShieldDragMode::ResizeNE:
        case ShieldDragMode::ResizeSW:
            return Qt::SizeBDiagCursor;
        case ShieldDragMode::ResizeW:
        case ShieldDragMode::ResizeE:
            return Qt::SizeHorCursor;
        case ShieldDragMode::Move:
            return Qt::SizeAllCursor;
        default:
            return Qt::ArrowCursor;
        }
    }

    void beginFaceShieldDrag(const QPointF& pos) {
        ensureFaceShieldRect();
        shieldDragMode_ = hitFaceShieldHandle(pos);
        if (shieldDragMode_ == ShieldDragMode::None) {
            // 点在框外：仍允许点空白（暗化遮罩吃鼠标），不开始拖。
            return;
        }
        shieldDragStartPos_ = pos;
        shieldDragStartOrigin_ = faceShieldOrigin_;
        shieldDragStartSide_ = faceShieldSide_;
        shieldDragStartPeak_ = faceShieldPeakFrac_;
        setCursor(faceShieldCursorAt(pos));
        update();
    }

    void updateFaceShieldDrag(const QPointF& pos) {
        if (shieldDragMode_ == ShieldDragMode::None) return;
        if (shieldDragMode_ == ShieldDragMode::Peak) {
            const qreal side = std::max(1.0, faceShieldSide_);
            const QRectF w = worldRect();
            const qreal maxPeakByWorld =
                std::max(0.05, (faceShieldOrigin_.y() - w.top()) / side);
            const qreal peak = (faceShieldOrigin_.y() - pos.y()) / side;
            faceShieldPeakFrac_ = std::clamp(peak, 0.05, std::min(0.55, maxPeakByWorld));
            clampFaceShieldToWorld();
            update();
            return;
        }
        const QPointF delta = pos - shieldDragStartPos_;
        QRectF r(shieldDragStartOrigin_, QSizeF(shieldDragStartSide_, shieldDragStartSide_));
        const qreal minS = faceShieldMinSide();
        switch (shieldDragMode_) {
        case ShieldDragMode::Move:
            r.translate(delta);
            break;
        case ShieldDragMode::ResizeSE: {
            const qreal s = std::max(minS, shieldDragStartSide_ + std::max(delta.x(), delta.y()));
            r.setWidth(s);
            r.setHeight(s);
            break;
        }
        case ShieldDragMode::ResizeSW: {
            const qreal s = std::max(minS, shieldDragStartSide_ + std::max(-delta.x(), delta.y()));
            r.setLeft(r.right() - s);
            r.setHeight(s);
            r.setWidth(s);
            break;
        }
        case ShieldDragMode::ResizeNE: {
            const qreal s = std::max(minS, shieldDragStartSide_ + std::max(delta.x(), -delta.y()));
            r.setTop(r.bottom() - s);
            r.setWidth(s);
            r.setHeight(s);
            break;
        }
        case ShieldDragMode::ResizeNW: {
            const qreal s = std::max(minS, shieldDragStartSide_ + std::max(-delta.x(), -delta.y()));
            r.setRight(r.left() + shieldDragStartSide_);
            r.setBottom(r.top() + shieldDragStartSide_);
            r.setLeft(r.right() - s);
            r.setTop(r.bottom() - s);
            break;
        }
        case ShieldDragMode::ResizeE: {
            const qreal s = std::max(minS, shieldDragStartSide_ + delta.x());
            r.setWidth(s);
            r.setHeight(s);
            break;
        }
        case ShieldDragMode::ResizeW: {
            const qreal s = std::max(minS, shieldDragStartSide_ - delta.x());
            r.setLeft(r.right() - s);
            r.setWidth(s);
            r.setHeight(s);
            break;
        }
        case ShieldDragMode::ResizeS: {
            const qreal s = std::max(minS, shieldDragStartSide_ + delta.y());
            r.setWidth(s);
            r.setHeight(s);
            break;
        }
        case ShieldDragMode::ResizeN: {
            const qreal s = std::max(minS, shieldDragStartSide_ - delta.y());
            r.setTop(r.bottom() - s);
            r.setWidth(s);
            r.setHeight(s);
            break;
        }
        default:
            break;
        }
        // 保持正方形：取边长。
        const qreal side = std::max(minS, std::min(r.width(), r.height()));
        setFaceShieldSquare(QRectF(r.topLeft(), QSizeF(side, side)));
        update();
    }

    void endFaceShieldDrag() {
        shieldDragMode_ = ShieldDragMode::None;
        update();
    }

    void drawFaceShieldEdit(QPainter& p) const {
        if (!faceShieldReady_) return;
        const QRectF r = shieldHitRect();
        QPen pen(QColor(255, 255, 255, 220), 1.5, Qt::DashLine);
        pen.setCosmetic(true);
        p.setPen(pen);
        p.setBrush(QColor(255, 255, 255, 28));
        p.drawRect(r);
        // 尖顶示意（浅虚线）
        const QPolygonF roof = housePolygon(r);
        QPen roofPen(QColor(255, 220, 160, 200), 1.2, Qt::DashLine);
        roofPen.setCosmetic(true);
        p.setPen(roofPen);
        p.setBrush(Qt::NoBrush);
        p.drawPolygon(roof);
        // 角点 + 尖顶手柄
        p.setPen(Qt::NoPen);
        p.setBrush(QColor(255, 255, 255, 230));
        const qreal hs = 5.0;
        for (const QPointF& pt : {r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()}) {
            p.drawRect(QRectF(pt.x() - hs * 0.5, pt.y() - hs * 0.5, hs, hs));
        }
        const QPointF peak = peakPoint();
        p.setBrush(QColor(255, 210, 120, 240));
        p.drawEllipse(peak, hs * 0.65, hs * 0.65);
    }

    void solveFaceShieldPositions() {
        if (!shieldActivePlay()) return;
        const QRectF square = shieldHitRect();
        const qreal radius = softR(leafScale_);
        for (LeafBody& L : leaves_) {
            if (!isPhysical(L.state)) continue;
            QPointF n;
            qreal pen = 0.0;
            bool inside = false;
            if (!queryHouseContact(L.pos, radius, square, &n, &pen, &inside)) continue;
            // 半导：只允许外向位置修正。
            if (pen > kPositionSlopPx) {
                L.pos += n * std::min(pen, rigidHalfWidth(leafScale_) * kPositionMaxCorrectionFrac);
            }
        }
    }

    void applyFaceShieldEject(qreal dt) {
        if (!shieldActivePlay()) return;
        const QRectF square = shieldHitRect();
        const qreal radius = softR(leafScale_) * 0.35;
        for (LeafBody& L : leaves_) {
            if (L.state != LeafState::Idle && L.state != LeafState::Dragging) continue;
            QPointF n;
            qreal pen = 0.0;
            bool inside = false;
            if (!queryHouseContact(L.pos, radius, square, &n, &pen, &inside)) continue;
            if (!inside) continue;
            // 框内：缓慢向下 + 略偏外侧挤出。
            L.vel.setY(L.vel.y() + kShieldEjectAccel * dt);
            L.vel.setX(L.vel.x() + n.x() * 120.0 * dt);
            if (L.state != LeafState::Dragging) clampSpeed(L);
        }
    }

    qreal worldAngle(const LeafBody& L) const { return leafParams().artAngleRad + L.angle; }

    void softSegmentEnds(const LeafBody& L, QPointF* a, QPointF* b) const {
        const qreal ang = worldAngle(L);
        const QPointF axis(std::cos(ang), std::sin(ang));
        *a = L.pos - axis * softHalf(leafScale_);
        *b = L.pos + axis * softHalf(leafScale_);
    }

    struct RigidPolygon {
        int count = 0;
        QPointF v[8];
    };

    RigidPolygon rigidPolygon(const LeafBody& L) const {
        const qreal hx = rigidHalfLength(leafScale_);
        const qreal hy = rigidHalfWidth(leafScale_);
        QPointF local[8];
        int count = 8;
        if (leafParams().rigidShape == LeafSkinParams::RigidShape::Hexagon) {
            // 💩 的轮廓：顶部略窄、腰部最宽、底部收一点，共六边。
            count = 6;
            local[0] = QPointF(-hx * 0.36, -hy);
            local[1] = QPointF(hx * 0.36, -hy);
            local[2] = QPointF(hx, -hy * 0.05);
            local[3] = QPointF(hx * 0.66, hy);
            local[4] = QPointF(-hx * 0.66, hy);
            local[5] = QPointF(-hx, -hy * 0.05);
        } else {
            const qreal bevel = std::min(rigidBevel(leafScale_), hy * 0.85);
            const QPointF oct[8] = {
                {-hx + bevel, -hy}, {hx - bevel, -hy},
                {hx, -hy + bevel},  {hx, hy - bevel},
                {hx - bevel, hy},   {-hx + bevel, hy},
                {-hx, hy - bevel},  {-hx, -hy + bevel},
            };
            std::copy(std::begin(oct), std::end(oct), std::begin(local));
        }
        const qreal ang = worldAngle(L);
        const qreal c = std::cos(ang);
        const qreal s = std::sin(ang);
        RigidPolygon out;
        out.count = count;
        for (int i = 0; i < count; ++i) {
            out.v[i] = L.pos + QPointF(local[i].x() * c - local[i].y() * s,
                                      local[i].x() * s + local[i].y() * c);
        }
        return out;
    }

    void rigidExtents(const LeafBody& L, qreal* extX, qreal* extY) const {
        const RigidPolygon poly = rigidPolygon(L);
        *extX = 0.0;
        *extY = 0.0;
        for (int i = 0; i < poly.count; ++i) {
            const QPointF& p = poly.v[i];
            *extX = std::max(*extX, std::abs(p.x() - L.pos.x()));
            *extY = std::max(*extY, std::abs(p.y() - L.pos.y()));
        }
    }

    bool polygonContact(const LeafBody& A, const LeafBody& B, qreal padding,
                        QPointF* normal, qreal* overlap) const {
        const QPointF centerDelta = B.pos - A.pos;
        const qreal maxDistance = rigidRadius(leafScale_) * 2.0 + padding;
        if (QPointF::dotProduct(centerDelta, centerDelta) >= maxDistance * maxDistance) {
            return false;
        }

        const RigidPolygon a = rigidPolygon(A);
        const RigidPolygon b = rigidPolygon(B);
        qreal best = 1.0e30;
        QPointF bestAxis;
        auto testAxes = [&](const RigidPolygon& source) {
            for (int edge = 0; edge < source.count; ++edge) {
                const QPointF d =
                    source.v[(edge + 1) % source.count] - source.v[edge];
                const qreal len = std::hypot(d.x(), d.y());
                if (len <= 1e-6) continue;
                const QPointF axis(-d.y() / len, d.x() / len);
                qreal minA = QPointF::dotProduct(a.v[0], axis);
                qreal maxA = minA;
                qreal minB = QPointF::dotProduct(b.v[0], axis);
                qreal maxB = minB;
                for (int i = 1; i < a.count; ++i) {
                    const qreal pa = QPointF::dotProduct(a.v[i], axis);
                    minA = std::min(minA, pa);
                    maxA = std::max(maxA, pa);
                }
                for (int i = 1; i < b.count; ++i) {
                    const qreal pb = QPointF::dotProduct(b.v[i], axis);
                    minB = std::min(minB, pb);
                    maxB = std::max(maxB, pb);
                }
                const qreal axisOverlap =
                    std::min(maxA, maxB) - std::max(minA, minB) + padding;
                if (axisOverlap <= 0.0) return false;
                if (axisOverlap < best) {
                    best = axisOverlap;
                    bestAxis = axis;
                }
            }
            return true;
        };
        if (!testAxes(a) || !testAxes(b)) return false;
        if (QPointF::dotProduct(centerDelta, bestAxis) < 0.0) bestAxis = -bestAxis;
        if (normal) *normal = bestAxis;
        if (overlap) *overlap = best;
        return true;
    }

    bool hitSoft(const LeafBody& L, const QPointF& pos) const {
        QPointF a, b;
        softSegmentEnds(L, &a, &b);
        return QLineF(pos, closestPointOnSegment(a, b, pos)).length()
            <= softR(leafScale_);
    }

    void updateWorld() {
        const QRectF shield = (faceShieldEnabled_ && faceShieldReady_)
            ? shieldHitRect() : QRectF{};
        const QRectF* shieldPtr = (shield.width() > 1.0) ? &shield : nullptr;
        theoreticalCapacity_ = maxLeavesForSize(size(), leafScale_, shieldPtr, faceShieldPeakFrac_);
        maxLeaves_ = leavesForPercent(theoreticalCapacity_, preferredPercent_);
        if (faceShieldEditing_) maxLeaves_ = 0;
        writeConfigValue(kViewportSizeKey, QVariantMap{
            {QStringLiteral("w"), width()},
            {QStringLiteral("h"), height()},
        });
    }

    void clampToWorld(LeafBody& L) const {
        const QRectF w = worldRect();
        if (w.width() <= 1.0 || w.height() <= 1.0) {
            L.pos = w.center();
            return;
        }
        qreal ex = 0.0, ey = 0.0;
        rigidExtents(L, &ex, &ey);
        // 世界比刚体还窄时，ex/ey 可能超过半宽，必须用安全 clamp。
        L.pos.setX(clampAxis(L.pos.x(), w.left() + ex, w.right() - ex));
        L.pos.setY(clampAxis(L.pos.y(), w.top() + ey, w.bottom() - ey));
    }

    // 每次只放一片，靠 spawnCooldown_ 拉开 kSpawnIntervalSec 的间隔。
    void flushSpawns() {
        if (resizePaused_) return;
        if (pendingSpawns_ <= 0 || spawnCooldown_ > 0.0) return;
        if (aliveCount() >= maxLeaves_) return;
        if (!trySpawnOne()) return;
        --pendingSpawns_;
        spawnCooldown_ = kSpawnIntervalSec;
        emitStats();
        update();
    }

    // 吊销同理：每 kDespawnIntervalSec 收一片，优先收生命最长（id 最小）的。
    void flushRemovals() {
        if (resizePaused_) return;
        if (pendingRemovals_ <= 0 || removeCooldown_ > 0.0) return;
        if (!removeOldest()) {
            pendingRemovals_ = 0;
            return;
        }
        --pendingRemovals_;
        removeCooldown_ = kDespawnIntervalSec;
        emitStats();
        update();
    }

    void beginRemoval(LeafBody& L) {
        if (L.state == LeafState::Removing) return;
        L.state = LeafState::Removing;
        L.vel = {};
        L.angVel = 0.0;
        L.removeT = 0.0;
        L.fadeFrom = L.alpha;
    }

    bool removeOldest() {
        int victim = -1;
        quint64 oldest = ~quint64(0);
        for (int i = 0; i < leaves_.size(); ++i) {
            const LeafState s = leaves_[i].state;
            if (s == LeafState::Free || s == LeafState::Removing
                || s == LeafState::Dragging) continue;
            if (leaves_[i].id < oldest) {
                oldest = leaves_[i].id;
                victim = i;
            }
        }
        if (victim < 0) return false;
        beginRemoval(leaves_[victim]);
        return true;
    }

    bool rigidOverlap(const LeafBody& A, const LeafBody& B) const {
        // 刷新时留 1px 余量，第一帧也不会生成成相交状态。
        return polygonContact(A, B, 1.0, nullptr, nullptr);
    }

    int acquireLeafSlot() {
        for (int i = 0; i < leaves_.size(); ++i) {
            if (leaves_[i].state == LeafState::Free) return i;
        }
        leaves_.append(LeafBody{});
        return leaves_.size() - 1;
    }

    bool trySpawnOne() {
        const QRectF w = worldRect();
        const qreal margin = rigidRadius(leafScale_);
        if (w.width() < margin * 4.0 || w.height() < margin * 4.0) return false;
        const qreal spawnHeight = std::min(w.height() * 0.28, w.height() - 2.0 * margin);
        if (spawnHeight <= 0.0) return false;
        for (int attempt = 0; attempt < 36; ++attempt) {
            LeafBody cand;
            cand.pos = QPointF(
                w.left() + margin + QRandomGenerator::global()->generateDouble()
                                        * std::max(1.0, w.width() - 2.0 * margin),
                w.top() + margin + QRandomGenerator::global()->generateDouble()
                                       * spawnHeight);
            cand.angle = (QRandomGenerator::global()->generateDouble() - 0.5) * 1.6;
            clampToWorld(cand);
            if (shieldActivePlay()) {
                bool inside = false;
                if (queryHouseContact(cand.pos, softR(leafScale_), shieldHitRect(),
                                      nullptr, nullptr, &inside)
                    && inside) {
                    continue;
                }
            }
            bool overlaps = false;
            for (const LeafBody& o : leaves_) {
                if (o.state == LeafState::Free || o.state == LeafState::Removing) continue;
                if (rigidOverlap(cand, o)) {
                    overlaps = true;
                    break;
                }
            }
            if (overlaps) continue;
            // 淡入期间静止，动画结束时才接管这份初速度。
            cand.birthVel =
                QPointF((QRandomGenerator::global()->generateDouble() - 0.5) * 40.0, 20.0);
            cand.birthAngVel = (QRandomGenerator::global()->generateDouble() - 0.5) * 2.4;
            cand.state = LeafState::Spawning;
            cand.spawnT = 0.0;
            cand.alpha = 0.0;
            cand.vel = {};
            cand.angVel = 0.0;
            cand.id = ++nextId_;
            LeafBody& slot = leaves_[acquireLeafSlot()];
            slot = cand;
            return true;
        }
        // 顶部刷新区暂时没有空位：保留 pending，等叶子落下后再重试。
        return false;
    }

    void cullOverflowImmediate() {
        while (aliveCount() > maxLeaves_) {
            if (!removeOldest()) break;
        }
    }

    static void clampSpeed(LeafBody& L) {
        const qreal sp = std::hypot(L.vel.x(), L.vel.y());
        if (sp > kMaxSpeed) L.vel *= (kMaxSpeed / sp);
    }

    bool contactData(const LeafBody& A, const LeafBody& B, QPointF* normal,
                     qreal* overlap) const {
        return polygonContact(A, B, 0.0, normal, overlap);
    }

    void solvePositions(int iters, qreal maxCorrection, bool draggedContactsOnly = false) {
        for (int it = 0; it < iters; ++it) {
            for (int i = 0; i < leaves_.size(); ++i) {
                LeafBody& A = leaves_[i];
                if (!isPhysical(A.state)) continue;
                for (int j = i + 1; j < leaves_.size(); ++j) {
                    LeafBody& B = leaves_[j];
                    if (!isPhysical(B.state)) continue;
                    const bool aDrag = A.state == LeafState::Dragging;
                    const bool bDrag = B.state == LeafState::Dragging;
                    // 拖叶无体积：任一端在拖就不做叶-叶接触。
                    if (aDrag || bDrag) continue;
                    if (draggedContactsOnly) continue;
                    QPointF n;
                    qreal overlap = 0.0;
                    if (!contactData(A, B, &n, &overlap)) continue;
                    const qreal corr = std::min(
                        std::max(0.0, overlap - kPositionSlopPx), maxCorrection);
                    if (corr <= 0.0) continue;
                    A.pos -= n * (corr * 0.5);
                    B.pos += n * (corr * 0.5);
                    if (it == 0) {
                        const qreal turn = corr * kCollisionTorque;
                        A.angVel -= turn;
                        B.angVel += turn;
                    }
                }
            }
            solveFaceShieldPositions();
            for (LeafBody& L : leaves_) {
                if (isPhysical(L.state)) clampToWorld(L);
            }
        }
    }

    void solveVelocities() {
        for (int i = 0; i < leaves_.size(); ++i) {
            LeafBody& A = leaves_[i];
            if (!isPhysical(A.state)) continue;
            for (int j = i + 1; j < leaves_.size(); ++j) {
                LeafBody& B = leaves_[j];
                if (!isPhysical(B.state)) continue;
                // 拖叶无体积。
                if (A.state == LeafState::Dragging || B.state == LeafState::Dragging) continue;
                QPointF n;
                qreal overlap = 0.0;
                if (!contactData(A, B, &n, &overlap)) continue;
                const QPointF rel = B.vel - A.vel;
                const qreal vn = QPointF::dotProduct(rel, n);
                const QPointF vt = rel - n * vn;
                if (vn < 0.0) {
                    // 轻微耗散法向速度，避免密集堆叠把冲量来回传递成颤动。
                    const qreal impulse = -vn * (1.0 + kRestitution) * 0.42;
                    A.vel -= n * impulse;
                    B.vel += n * impulse;
                }
                A.vel += vt * (kFriction * 0.5);
                B.vel -= vt * (kFriction * 0.5);
                clampSpeed(A);
                clampSpeed(B);
            }
        }
    }

    void solveBoundaryVelocities() {
        const QRectF w = worldRect();
        for (LeafBody& L : leaves_) {
            if (L.state != LeafState::Idle) continue;
            qreal ex = 0.0, ey = 0.0;
            rigidExtents(L, &ex, &ey);
            if (L.pos.x() <= w.left() + ex + 0.5 && L.vel.x() < 0.0) {
                L.vel.setX(-L.vel.x() * kRestitution);
                L.vel.setY(L.vel.y() * kFloorFriction);
            }
            if (L.pos.x() >= w.right() - ex - 0.5 && L.vel.x() > 0.0) {
                L.vel.setX(-L.vel.x() * kRestitution);
                L.vel.setY(L.vel.y() * kFloorFriction);
            }
            if (L.pos.y() <= w.top() + ey + 0.5 && L.vel.y() < 0.0) {
                L.vel.setY(-L.vel.y() * kRestitution);
                L.vel.setX(L.vel.x() * kFloorFriction);
            }
            if (L.pos.y() >= w.bottom() - ey - 0.5 && L.vel.y() > 0.0) {
                L.vel.setY(-L.vel.y() * kRestitution);
                L.vel.setX(L.vel.x() * kFloorFriction);
                if (std::abs(L.vel.y()) < 18.0) L.vel.setY(0.0);
                if (std::abs(L.vel.x()) < 6.0) L.vel.setX(0.0);
            }
            clampSpeed(L);
        }
    }

    qreal maxOverlapWith(const LeafBody& dragged) const {
        qreal worst = 0.0;
        for (const LeafBody& other : leaves_) {
            if (&other == &dragged || !isPhysical(other.state)) continue;
            QPointF n;
            qreal overlap = 0.0;
            if (contactData(dragged, other, &n, &overlap)) worst = std::max(worst, overlap);
        }
        return worst;
    }

    void advanceDragged(qreal dt) {
        if (!dragTargetValid_ || dragId_ == 0) return;
        for (LeafBody& L : leaves_) {
            if (L.id != dragId_) continue;
            const QPointF start = L.pos;
            QPointF delta = dragTarget_ - L.pos;
            const qreal distance = std::hypot(delta.x(), delta.y());
            const qreal maxStep =
                std::max(1.0, rigidHalfWidth(leafScale_) * kDragStepFrac);
            const int steps = std::clamp(
                static_cast<int>(std::ceil(distance / maxStep)), 1, kMaxDragSubsteps);
            if (distance > maxStep * steps) delta *= (maxStep * steps / distance);
            const QPointF step = delta / steps;
            for (int s = 0; s < steps; ++s) {
                L.pos += step;
                clampToWorld(L);
                // 拖叶不推挤他叶；仍与护脸区做外向分离。
                solveFaceShieldPositions();
            }
            const QPointF actualVel = (L.pos - start) / std::max(0.001, dt);
            dragVelocity_ = dragVelocity_ * 0.65 + actualVel * 0.35;
            L.vel = dragVelocity_;
            return;
        }
    }

    void squeezeIntoBounds() {
        for (LeafBody& L : leaves_) {
            if (L.state == LeafState::Free || L.state == LeafState::Removing) continue;
            clampToWorld(L);
        }
        solvePositions(kResizeSettleIters,
                       rigidHalfWidth(leafScale_) * kPositionMaxCorrectionFrac);
        solveVelocities();
        solveBoundaryVelocities();
    }

    void onTick() {
        if (resizePaused_) return;
        if (faceShieldEditing_) return;
        const qreal dt = kTickMs / 1000.0;
        bool dirty = pendingSpawns_ > 0 || pendingRemovals_ > 0;
        bool statsDirty = false;
        spawnCooldown_ = std::max(0.0, spawnCooldown_ - dt);
        removeCooldown_ = std::max(0.0, removeCooldown_ - dt);
        // 已满时丢掉多余待生成，避免队列无限堆积。
        if (aliveCount() >= maxLeaves_ && pendingSpawns_ > 0) {
            pendingSpawns_ = 0;
            statsDirty = true;
        }
        flushSpawns();
        flushRemovals();
        advanceDragged(dt);
        applyFaceShieldEject(dt);

        for (LeafBody& L : leaves_) {
            if (L.state == LeafState::Free) continue;
            if (L.state == LeafState::Spawning) {
                L.spawnT = std::min(1.0, L.spawnT + dt / kFadeInSec);
                L.alpha = L.spawnT;
                if (L.spawnT >= 1.0) {
                    L.state = LeafState::Idle;
                    L.alpha = 1.0;
                    L.vel = L.birthVel;
                    L.angVel = L.birthAngVel;
                    clampToWorld(L);
                }
                dirty = true;
                continue;
            }
            if (L.state == LeafState::Removing) {
                L.removeT = std::min(1.0, L.removeT + dt / kFadeOutSec);
                L.alpha = L.fadeFrom * (1.0 - L.removeT);
                dirty = true;
                continue;
            }
            if (L.state == LeafState::Dragging) {
                dirty = true;
                continue;
            }
            L.vel.setY(L.vel.y() + kGravity * dt);
            L.pos += L.vel * dt;
            L.angle += L.angVel * dt;
            L.vel *= kDamping;
            L.angVel *= kAngDamping;
            L.angVel = std::clamp(L.angVel, -kMaxAngVel, kMaxAngVel);
            clampSpeed(L);
            if (std::hypot(L.vel.x(), L.vel.y()) < 20.0) {
                L.vel *= 0.90;
                L.angVel *= 0.88;
                if (std::hypot(L.vel.x(), L.vel.y()) < 6.0) L.vel = {};
                if (std::abs(L.angVel) < 0.02) L.angVel = 0.0;
            }
            dirty = true;
        }

        solvePositions(kPositionIters,
                       rigidHalfWidth(leafScale_) * kPositionMaxCorrectionFrac);
        solveVelocities();
        solveBoundaryVelocities();

        const qreal prevLid = lidOpen_;
        const qreal prevTrashHalo = trashHalo_;
        const qreal prevLeafHalo = leafHalo_;
        stepLid(dt);
        stepHalo(dt);
        if (std::abs(lidOpen_ - prevLid) > 0.002
            || std::abs(trashHalo_ - prevTrashHalo) > 0.002
            || std::abs(leafHalo_ - prevLeafHalo) > 0.002) {
            dirty = true;
        }

        for (LeafBody& L : leaves_) {
            if (L.state == LeafState::Removing && L.removeT >= 1.0) {
                resetLeafBody(&L);
                dirty = true;
                statsDirty = true;
            }
        }
        // 收掉尾部空槽，池不会只涨不缩。
        while (!leaves_.isEmpty() && leaves_.last().state == LeafState::Free) {
            leaves_.removeLast();
        }

        if (dirty) {
            update();
        }
        if (statsDirty) emitStats();
    }

    QTimer* tick_ = nullptr;
    QVector<LeafBody> leaves_;
    int pendingSpawns_ = 0;
    int pendingRemovals_ = 0;
    qreal spawnCooldown_ = 0.0;
    qreal removeCooldown_ = 0.0;
    int maxLeaves_ = kMinLeaves;
    int theoreticalCapacity_ = 0;
    int preferredPercent_ = kCapPercentDefault;
    qreal leafScale_ = 1.0;
    qreal trashScale_ = 1.0;
    quint64 nextId_ = 1;
    quint64 dragId_ = 0;
    bool draggingTrash_ = false;
    bool hoverTrash_ = false;
    qreal lidOpen_ = 0.0;
    qreal trashHalo_ = 0.0;
    qreal leafHalo_ = 0.0;
    QPointF dragOffset_;
    QPointF dragTarget_;
    QPointF dragVelocity_;
    bool dragTargetValid_ = false;
    QPointF trashCenterPx_;
    QPointF legacyTrashNorm_{0.5, 1.0};
    bool hasLegacyTrashNorm_ = false;
    bool trashPosReady_ = false;
    bool resizePaused_ = false;
    bool suppressResizeCommit_ = false;
    std::function<void()> statsCb_;

    bool faceShieldEnabled_ = false;
    bool faceShieldEditing_ = false;
    bool faceShieldReady_ = false;
    QPointF faceShieldOrigin_;
    qreal faceShieldSide_ = 0.0;
    qreal faceShieldPeakFrac_ = kShieldPeakFrac;
    ShieldDragMode shieldDragMode_ = ShieldDragMode::None;
    QPointF shieldDragStartPos_;
    QPointF shieldDragStartOrigin_;
    qreal shieldDragStartSide_ = 0.0;
    qreal shieldDragStartPeak_ = kShieldPeakFrac;
};

class FaceShieldEditButton final : public QPushButton {
public:
    explicit FaceShieldEditButton(QWidget* parent) : QPushButton(parent) {
        setFixedSize(RippleOverlayRoot::kBtnW,
                     RippleOverlayRoot::kTopbarH - RippleOverlayRoot::kShadowW);
        setCursor(Qt::PointingHandCursor);
        setCheckable(true);
        setFlat(true);
        RippleOverlayRoot::polishChromeButton(this);

        hoverAnim_ = new QVariantAnimation(this);
        hoverAnim_->setDuration(RippleOverlayRoot::kHoverAnimMs);
        hoverAnim_->setEasingCurve(QEasingCurve::OutCubic);
        QObject::connect(hoverAnim_, &QVariantAnimation::valueChanged, this,
                         [this](const QVariant& v) {
            hoverT_ = v.toReal();
            update();
        });

        checkAnim_ = new QVariantAnimation(this);
        checkAnim_->setDuration(RippleOverlayRoot::kLockAnimMs);
        checkAnim_->setEasingCurve(QEasingCurve::OutCubic);
        QObject::connect(checkAnim_, &QVariantAnimation::valueChanged, this,
                         [this](const QVariant& v) {
            checkT_ = v.toReal();
            update();
        });
        QObject::connect(this, &QPushButton::toggled, this, [this](bool on) {
            checkAnim_->stop();
            checkAnim_->setStartValue(checkT_);
            checkAnim_->setEndValue(on ? 1.0 : 0.0);
            checkAnim_->start();
        });
    }

    void setAccentProgress(qreal progress) {
        accentT_ = std::clamp(progress, 0.0, 1.0);
        update();
    }

    void setInteractionEnabled(bool on) {
        if (interactionEnabled_ == on) return;
        interactionEnabled_ = on;
        setCursor(on ? Qt::PointingHandCursor : Qt::ArrowCursor);
        if (!on) {
            RippleOverlayRoot::startHoverAnimation(hoverAnim_, hoverT_, 0.0);
        }
        update();
    }
    bool interactionEnabled() const { return interactionEnabled_; }

    void forceHover(bool on) {
        RippleOverlayRoot::startHoverAnimation(hoverAnim_, hoverT_, on ? 1.0 : 0.0);
    }

protected:
    bool event(QEvent* e) override {
        if (!interactionEnabled_
            && (e->type() == QEvent::Enter || e->type() == QEvent::Leave
                || e->type() == QEvent::MouseButtonPress
                || e->type() == QEvent::MouseButtonRelease)) {
            return true;
        }
        if (e->type() == QEvent::Enter) {
            RippleOverlayRoot::startHoverAnimation(hoverAnim_, hoverT_, 1.0);
        } else if (e->type() == QEvent::Leave) {
            RippleOverlayRoot::startHoverAnimation(hoverAnim_, hoverT_, 0.0);
        }
        return QPushButton::event(e);
    }

    void paintEvent(QPaintEvent*) override {
        QPainter p(this);
        p.fillRect(rect(), QColor(0, 0, 0, 1));
        if (interactionEnabled_) {
            RippleOverlayRoot::paintChromeHover(p, rect(), hoverT_, accentT_);
        }

        RippleOverlayRoot::beginCrispIconPaint(p, this);
        QColor iconColor = RippleOverlayRoot::accentIconColor(accentT_);
        if (!interactionEnabled_) {
            iconColor = QColor(148, 148, 148, 120);
        }
        const QRectF box = RippleOverlayRoot::iconBox();
        const qreal s = std::min(box.width(), box.height());
        // 未选中：单空心方。选中：同心缩放（同中心、对角线重合），一大一小。
        if (checkT_ < 0.02) {
            RippleOverlayRoot::drawCrispRoundBox(p, box, RippleOverlayRoot::kIconBoxRadius,
                                                 iconColor, 0.0);
            return;
        }
        const qreal t = checkT_;
        const qreal sBig = s * (1.0 + 0.18 * t);
        const qreal sSmall = s * (1.0 - 0.28 * t);
        const QPointF c = box.center();
        const QRectF big(c.x() - sBig * 0.5, c.y() - sBig * 0.5, sBig, sBig);
        const QRectF small(c.x() - sSmall * 0.5, c.y() - sSmall * 0.5, sSmall, sSmall);
        QColor outerColor = iconColor;
        outerColor.setAlphaF(iconColor.alphaF() * (0.55 + 0.45 * t));
        RippleOverlayRoot::drawCrispRoundBox(p, big, RippleOverlayRoot::kIconBoxRadius,
                                             outerColor, 0.0);
        RippleOverlayRoot::drawCrispRoundBox(p, small, 1.5, iconColor, 0.0);
    }

private:
    QVariantAnimation* hoverAnim_ = nullptr;
    QVariantAnimation* checkAnim_ = nullptr;
    qreal hoverT_ = 0.0;
    qreal accentT_ = 1.0;
    qreal checkT_ = 0.0;
    bool interactionEnabled_ = true;
};

class FaceShieldSwitch final : public QWidget {
public:
    explicit FaceShieldSwitch(QWidget* parent) : QWidget(parent) {
        setFixedSize(kSwitchW, RippleOverlayRoot::kTopbarH - RippleOverlayRoot::kShadowW);
        setCursor(Qt::PointingHandCursor);
        setMouseTracking(true);

        slideAnim_ = new QVariantAnimation(this);
        slideAnim_->setDuration(RippleOverlayRoot::kLockAnimMs);
        slideAnim_->setEasingCurve(QEasingCurve::InOutCubic);
        QObject::connect(slideAnim_, &QVariantAnimation::valueChanged, this,
                         [this](const QVariant& v) {
            posT_ = v.toReal();
            update();
        });

        hoverAnim_ = new QVariantAnimation(this);
        hoverAnim_->setDuration(RippleOverlayRoot::kHoverAnimMs);
        hoverAnim_->setEasingCurve(QEasingCurve::OutCubic);
        QObject::connect(hoverAnim_, &QVariantAnimation::valueChanged, this,
                         [this](const QVariant& v) {
            hoverT_ = v.toReal();
            update();
        });
    }

    static constexpr int kSwitchW = 40;

    bool isOn() const { return on_; }
    void setOn(bool on, bool notify = true, bool animate = true) {
        if (on_ == on) {
            if (!animate) {
                slideAnim_->stop();
                posT_ = on ? 1.0 : 0.0;
                update();
            }
            return;
        }
        on_ = on;
        if (!animate) {
            slideAnim_->stop();
            posT_ = on ? 1.0 : 0.0;
            update();
        } else {
            slideAnim_->stop();
            slideAnim_->setStartValue(posT_);
            slideAnim_->setEndValue(on ? 1.0 : 0.0);
            slideAnim_->start();
        }
        if (notify && onToggled_) onToggled_(on_);
    }

    void setAccentProgress(qreal progress) {
        accentT_ = std::clamp(progress, 0.0, 1.0);
        update();
    }

    void setOnToggled(std::function<void(bool)> cb) { onToggled_ = std::move(cb); }

    // 刚显示时抑制悬停，避免点击方钮后悬停样式「跳」到划钮。
    void resetHoverArming() {
        hoverArmed_ = false;
        hoverT_ = 0.0;
        if (hoverAnim_) hoverAnim_->stop();
        update();
    }

protected:
    void showEvent(QShowEvent* event) override {
        QWidget::showEvent(event);
        resetHoverArming();
    }

    bool event(QEvent* e) override {
        if (e->type() == QEvent::Enter) {
            if (!hoverArmed_) return QWidget::event(e);
            RippleOverlayRoot::startHoverAnimation(hoverAnim_, hoverT_, 1.0);
        } else if (e->type() == QEvent::Leave) {
            hoverArmed_ = true;
            RippleOverlayRoot::startHoverAnimation(hoverAnim_, hoverT_, 0.0);
        }
        return QWidget::event(e);
    }

    void mousePressEvent(QMouseEvent* event) override {
        if (event->button() == Qt::LeftButton) setOn(!on_);
    }

    void paintEvent(QPaintEvent*) override {
        QPainter p(this);
        p.setRenderHint(QPainter::Antialiasing, true);
        p.fillRect(rect(), QColor(0, 0, 0, 1));
        if (hoverArmed_) {
            RippleOverlayRoot::paintChromeHover(p, rect(), hoverT_, accentT_);
        }

        const QRectF track(5, height() * 0.5 - 6.5, width() - 10, 13);
        const QColor offC(theme().border);
        const QColor onC(theme().activeLine);
        const QColor trackC(
            static_cast<int>(offC.red() + (onC.red() - offC.red()) * posT_),
            static_cast<int>(offC.green() + (onC.green() - offC.green()) * posT_),
            static_cast<int>(offC.blue() + (onC.blue() - offC.blue()) * posT_),
            static_cast<int>(110 + 90 * posT_));
        p.setPen(Qt::NoPen);
        p.setBrush(trackC);
        p.drawRoundedRect(track, 6.5, 6.5);

        const qreal knobR = 5.0;
        const qreal x0 = track.left() + knobR + 1.5;
        const qreal x1 = track.right() - knobR - 1.5;
        const qreal knobX = x0 + (x1 - x0) * posT_;
        p.setBrush(QColor(255, 255, 255, 240));
        p.drawEllipse(QPointF(knobX, track.center().y()), knobR, knobR);
    }

private:
    bool on_ = false;
    bool hoverArmed_ = false;
    qreal posT_ = 0.0;
    qreal hoverT_ = 0.0;
    qreal accentT_ = 1.0;
    QVariantAnimation* slideAnim_ = nullptr;
    QVariantAnimation* hoverAnim_ = nullptr;
    std::function<void(bool)> onToggled_;
};

class LeafRoot final : public RippleOverlayRoot {
public:
    explicit LeafRoot(QMainWindow* win) : RippleOverlayRoot(win) {
        content()->setAttribute(Qt::WA_TransparentForMouseEvents, false);
        canvas_ = new LeafCanvas(content());
        rulesStrip_ = new LeafRulesStrip(content());
        rulesStrip_->raise();

        extraBox_ = new QWidget(this);
        extraBox_->setStyleSheet(QStringLiteral("background: transparent;"));
        auto* lay = new QHBoxLayout(extraBox_);
        lay->setContentsMargins(0, 0, 0, 0);
        lay->setSpacing(0);

        editBtn_ = new FaceShieldEditButton(extraBox_);
        lay->addWidget(editBtn_);
        shieldSwitch_ = new FaceShieldSwitch(extraBox_);
        lay->addWidget(shieldSwitch_);
        shieldSwitch_->hide();
        extraBox_->setFixedSize(extraChromeWidth(), kTopbarH - kShadowW);

        QObject::connect(editBtn_, &QPushButton::toggled, this, [this](bool on) {
            if (on && !canEnterFaceShieldEdit()) {
                QSignalBlocker block(editBtn_);
                editBtn_->setChecked(false);
                return;
            }
            setFaceShieldEditMode(on);
        });
        shieldSwitch_->setOnToggled([this](bool on) {
            if (canvas_) canvas_->setFaceShieldEnabled(on);
        });
        if (canvas_) {
            shieldSwitch_->setOn(canvas_->faceShieldEnabled(), false, false);
        }

        canvas_->commitViewport(content()->rect());
        setGiftRules(loadLeafGiftRules());
        refreshEditMutex();
    }

    LeafCanvas* canvas() const { return canvas_; }

    void setGiftRules(const QVector<LeafGiftRule>& rules) {
        if (rulesStrip_) {
            rulesStrip_->setRules(rules);
            layoutRulesStrip();
        }
    }

    void releaseHeavyResources() {
        if (canvas_) canvas_->shutdown();
        if (rulesStrip_) rulesStrip_->releaseHeavyResources();
    }

protected:
    // 护脸编辑：暗化/框/阴影与窗口同帧；平时也跟手，避免缩放后碰撞延迟。
    bool wantsSyncResizeLayout() const override { return true; }
    bool wantsHighQualityResizePaint() const override {
        return canvas_ && canvas_->faceShieldEditing();
    }

    int extraChromeWidth() const override {
        // 划钮仅编辑态出现；平时只占编辑方钮宽度。
        const bool showSwitch = canvas_ && canvas_->faceShieldEditing();
        return kBtnW + (showSwitch ? FaceShieldSwitch::kSwitchW : 0);
    }

    void layoutExtraChrome(const QRect& leftGeo) override {
        if (!extraBox_) return;
        extraBox_->setFixedSize(extraChromeWidth(), leftGeo.height());
        extraBox_->setGeometry(leftGeo.right(), leftGeo.y(),
                               extraChromeWidth(), leftGeo.height());
        extraBox_->raise();
    }

    bool extraChromeContains(const QPoint& local) const override {
        return extraBox_ && extraBox_->isVisible() && extraBox_->geometry().contains(local);
    }

    void setExtraChromeVisible(bool vis) override {
        if (extraBox_) {
            extraBox_->setVisible(vis);
            if (vis) extraBox_->raise();
        }
    }

    void onChromeAccentProgress(qreal progress) override {
        if (editBtn_) editBtn_->setAccentProgress(progress);
        if (shieldSwitch_) shieldSwitch_->setAccentProgress(progress);
    }

    void onChromeVisualSync(bool borderShown, bool locked, bool /*transitioning*/) override {
        chromeBorderShown_ = borderShown;
        chromeLocked_ = locked;
        onChromeAccentProgress(borderShown ? 1.0 : 0.0);
        refreshEditMutex();
    }

    bool contentWantsMouse(const QPoint& contentLocal) const override {
        if (!canvas_ || resizing() || canvas_->resizePaused()) return false;
        return canvas_->hitInteractive(canvas_->mapFrom(content(), contentLocal));
    }

    void onResizeBegin() override {
        if (canvas_) canvas_->beginResizePause();
    }

    void onContentGeometryChanged() override {
        if (canvas_) canvas_->commitViewport(content()->rect());
        layoutRulesStrip();
    }

    void onContentGeometryWhileResizing() override {
        if (canvas_) canvas_->commitViewport(content()->rect());
    }

    void onResizeResume() override {
        if (canvas_) canvas_->endResizePause();
        layoutRulesStrip();
    }

private:
    bool canEnterFaceShieldEdit() const {
        // 隐藏或锁定时不可进入编辑。
        return chromeBorderShown_ && !chromeLocked_;
    }

    void refreshEditMutex() {
        const bool editing = canvas_ && canvas_->faceShieldEditing();
        if (editBtn_) {
            // 编辑中可点退出；否则仅边框展开且未锁定时可进。
            editBtn_->setInteractionEnabled(editing || canEnterFaceShieldEdit());
        }
        setFrameLockInteractionEnabled(!editing);
    }

    void setFaceShieldEditMode(bool on) {
        if (on && !canEnterFaceShieldEdit()) {
            if (editBtn_) {
                QSignalBlocker block(editBtn_);
                editBtn_->setChecked(false);
            }
            return;
        }
        if (canvas_) canvas_->setFaceShieldEditing(on);
        if (shieldSwitch_) {
            shieldSwitch_->setVisible(on);
            if (on) {
                shieldSwitch_->resetHoverArming();
                shieldSwitch_->setOn(canvas_ && canvas_->faceShieldEnabled(), false, false);
            }
        }
        // 编辑态出现划钮后重排热区宽度。
        if (width() > 0) {
            const QRect leftGeo(kShadowW, kShadowW, kCircleOff + kBtnW * 3, kTopbarH - kShadowW);
            layoutExtraChrome(leftGeo);
            update(QRect(leftGeo.right(), leftGeo.y(), kBtnW + FaceShieldSwitch::kSwitchW + 8,
                         leftGeo.height()).adjusted(-1, -1, 1, 1));
        }
        if (auto* win = dynamic_cast<RippleOverlayWindow*>(hostWindow())) {
            win->setFrameForced(on);
        } else {
            setFrameForced(on);
        }
        // 划钮弹出后：悬停仍留在方钮上（若光标还在方钮内），不要串到划钮。
        QTimer::singleShot(0, this, [this]() {
            if (!editBtn_) return;
            const QPoint local = editBtn_->mapFromGlobal(QCursor::pos());
            editBtn_->forceHover(editBtn_->rect().contains(local));
            if (shieldSwitch_ && shieldSwitch_->isVisible()) {
                shieldSwitch_->resetHoverArming();
            }
        });
        refreshEditMutex();
        layoutRulesStrip();
        update();
    }

    void layoutRulesStrip() {
        if (!rulesStrip_ || !content()) return;
        if (canvas_ && canvas_->faceShieldEditing()) {
            rulesStrip_->hide();
            return;
        }
        // 相对原先宽约缩 1/4。
        const int w = std::min(210, std::max(135, static_cast<int>(content()->width() * 0.375)));
        rulesStrip_->setFixedWidth(w);
        // 高度按卡片内容估，避免 adjustSize 在半透明父窗上算出 0。
        const int h = std::max(40, rulesStrip_->sizeHint().height());
        const int maxH = std::max(40, content()->height() - 12);
        rulesStrip_->setFixedHeight(std::min(h, maxH));
        rulesStrip_->move(6, 6);
        rulesStrip_->raise();
        rulesStrip_->show();
    }

    LeafCanvas* canvas_ = nullptr;
    LeafRulesStrip* rulesStrip_ = nullptr;
    QWidget* extraBox_ = nullptr;
    FaceShieldEditButton* editBtn_ = nullptr;
    FaceShieldSwitch* shieldSwitch_ = nullptr;
    bool chromeBorderShown_ = true;
    bool chromeLocked_ = false;
};

class LeafOverlayController final : public QObject {
public:
    explicit LeafOverlayController(QObject* parent = nullptr) : QObject(parent) {}

    bool isMounted() const {
        return OverlayHostService::instance().isToolActive(OverlayToolId::Leaf);
    }

    LeafCanvas* canvas() const { return root_ ? root_->canvas() : nullptr; }

    void setGiftRules(const QVector<LeafGiftRule>& rules) {
        if (root_) root_->setGiftRules(rules);
    }

    void show(std::function<void()> onClosed) {
        auto& host = OverlayHostService::instance();
        root_ = nullptr;
        auto* shell = host.shell(OverlayToolId::Leaf);
        root_ = new LeafRoot(shell);
        const QVector<LeafGiftRule> rules = loadLeafGiftRules();
        root_->setGiftRules(rules);
        // 最小高度：顶栏 + 10 张规则卡完整渲染空间 + 少许游玩边距。
        constexpr int kOverlayCardH = 32;
        constexpr int kOverlayCardGap = 4;
        constexpr int kOverlayStripPad = 12;
        const int rulesBlockH = kOverlayStripPad
            + kMaxGiftRules * kOverlayCardH
            + (kMaxGiftRules - 1) * kOverlayCardGap;
        const int minW = std::max(cmToPx(11.0), 320);
        const int minH = RippleOverlayRoot::kTopbarH + rulesBlockH + cmToPx(2.5);
        const int defW = std::max(minW, cmToPx(14.0));
        const int defH = std::max(minH, static_cast<int>(defW * 1.15));
        host.show(OverlayToolId::Leaf, QStringLiteral("捡叶子"), kGeoKey,
                  minW, minH, defW, defH, root_,
                  [this, onClosed]() {
                      unmount();
                      if (onClosed) onClosed();
                  });
    }

private:
    void unmount() {
        if (root_) root_->releaseHeavyResources();
        // 只卸本窗图标，不清全局礼物缓存，避免设置页/选择器随后加载失败。
        root_ = nullptr;
    }

    LeafRoot* root_ = nullptr;
};

class LeafToolRuntime final : public ToolRuntimeBase {
public:
    explicit LeafToolRuntime(QObject* parent, std::function<void()> tryRelease)
        : ToolRuntimeBase(parent), tryRelease_(std::move(tryRelease)) {}

    QString toolId() const override { return QStringLiteral("leaf"); }

    bool isOverlayActive() const override {
        return overlayCtrl_ && overlayCtrl_->isMounted();
    }

    LeafCanvas* canvas() const {
        return overlayCtrl_ ? overlayCtrl_->canvas() : nullptr;
    }

    LeafOverlayController* controller() {
        if (!overlayCtrl_) overlayCtrl_ = new LeafOverlayController(this);
        return overlayCtrl_;
    }

    void toggleOverlay(std::function<void()> onClosed) {
        auto& host = OverlayHostService::instance();
        if (host.isToolActive(OverlayToolId::Leaf)) {
            host.teardown(OverlayToolId::Leaf);
            return;
        }
        pushLeafGiftRulesToCore(loadLeafGiftRules());
        controller()->show([this, onClosed]() {
            if (onClosed) onClosed();
            if (tryRelease_) tryRelease_();
        });
    }

    void spawnTest(int count = 1) {
        if (!isOverlayActive()) toggleOverlay(nullptr);
        if (auto* c = controller()->canvas()) c->enqueueSpawn(count);
    }

    void applySettings(int maxPercent, int leafPercent, int trashPercent) {
        if (auto* c = canvas()) c->applySettings(maxPercent, leafPercent, trashPercent);
    }

    void refreshOverlaySkin() {
        LeafSharedAssets::reload();
        if (auto* c = canvas()) c->reloadSkin();
    }

    void applyGiftRules(const QVector<LeafGiftRule>& rules) {
        if (overlayCtrl_) overlayCtrl_->setGiftRules(rules);
    }

    void applyLeafDelta(int delta) {
        if (!isOverlayActive()) return;
        if (auto* c = canvas()) c->applyLeafDelta(delta);
    }

    void onCorePacket(const QJsonObject& packet) override {
        if (packet.value(QStringLiteral("op")).toString() != QStringLiteral("leaf.spawn")) return;
        if (!isOverlayActive()) return;
        applyLeafDelta(packet.value(QStringLiteral("count")).toInt());
    }

private:
    LeafOverlayController* overlayCtrl_ = nullptr;
    std::function<void()> tryRelease_;
};

class LeafToolWindow final : public TabbedToolWindow {
public:
    static constexpr int kWidth = 720;
    static constexpr int kHeight = 700;

    explicit LeafToolWindow(CoreClient* core, LeafToolRuntime* runtime)
        : TabbedToolWindow(core, {QStringLiteral("设置"), QStringLiteral("礼物规则")}),
          runtime_(runtime) {
        setWindowTitle(QStringLiteral("捡叶子"));
        setFixedSize(kWidth, kHeight);
        giftRules_ = loadLeafGiftRules();
        mainTab_ = buildMainTab();
        addTabPage(mainTab_);
        addTabPlaceholder();
        setTabFactory(1, [this]() {
            QWidget* page = buildGiftTab();
            refreshTheme();
            return page;
        });
        switchTab(0);
        refreshTheme();
        pushGiftRulesToCore();
        liveaio::util::onThemeChange(this, [this](const QString&) { refreshTheme(); });
        statsTimer_ = new QTimer(this);
        statsTimer_->setInterval(200);
        QObject::connect(statsTimer_, &QTimer::timeout, this, [this]() { refreshStats(); });
        statsTimer_->start();
    }

    QString toolId() const override { return QStringLiteral("leaf"); }
    void onCorePacket(const QJsonObject&) override {}

    void onPanelClosing() override {
        liveaio::util::hideSessionGiftPicker();
        releaseGiftTabIcons();
    }

    void refreshTheme() override {
        applyChromeStyle();
        refreshOpenBtn();
        styleSpawnBtn();
        if (simWidget_) simWidget_->refreshTheme();
        if (addRuleBtn_) styleAddRuleBtn();
        if (hint_) {
            hint_->setStyleSheet(QStringLiteral("color: %1; background: transparent;")
                                     .arg(theme().textMuted));
        }
        if (stats_) {
            stats_->setStyleSheet(QStringLiteral("color: %1; background: transparent; font-size: 13px;")
                                      .arg(theme().text));
        }
        if (simDesc_) {
            simDesc_->setStyleSheet(QStringLiteral("color: %1; background: transparent; font-size: 12px;")
                                        .arg(theme().textMuted));
        }
        if (rulesHint_) {
            rulesHint_->setStyleSheet(QStringLiteral("color: %1; background: transparent; font-size: 12px;")
                                          .arg(theme().textMuted));
        }
        for (LeafGiftRuleCard* card : ruleCards_) {
            if (card) card->refreshTheme();
        }
    }

    void applyChromeStyle() override { setStyleSheet(liveaio::util::toolQss()); }

private:
    QLabel* formLabel(const QString& text, QWidget* parent, int width = 108) {
        return liveaio::util::formLabel(text, parent, width);
    }

    QWidget* buildMainTab() {
        auto* page = new QWidget;
        page->setObjectName(QStringLiteral("ToolRoot"));
        auto* lay = new QVBoxLayout(page);
        lay->setContentsMargins(20, 12, 20, 12);
        lay->setSpacing(10);

        auto* title = new QLabel(QStringLiteral("捡叶子"), page);
        title->setObjectName(QStringLiteral("ToolPageTitle"));
        title->setAttribute(Qt::WA_TransparentForMouseEvents);
        {
            QFont f = title->font();
            f.setPixelSize(20);
            f.setWeight(QFont::DemiBold);
            title->setFont(f);
        }
        lay->addWidget(title, 0, Qt::AlignTop);

        hint_ = new QLabel(
            QStringLiteral("打开悬浮窗后可用「模拟送礼」或「投放测试」生成叶子，拖到垃圾桶消除。"),
            page);
        hint_->setWordWrap(true);
        hint_->setObjectName(QStringLiteral("ToolTip"));
        hint_->setContentsMargins(0, 0, 0, 0);
        hint_->setAttribute(Qt::WA_TransparentForMouseEvents);
        {
            QFont f = hint_->font();
            f.setPixelSize(12);
            hint_->setFont(f);
        }
        hint_->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Maximum);
        lay->addWidget(hint_, 0, Qt::AlignTop);

        auto* card = new QFrame(page);
        card->setObjectName(QStringLiteral("Card"));
        auto* cardLay = new QVBoxLayout(card);
        cardLay->setContentsMargins(16, 12, 16, 12);
        cardLay->setSpacing(8);

        // 与弹幕/加班机一致：打开钮与标题同行，避免上方标签挡悬停命中。
        auto* row = new QHBoxLayout;
        row->setSpacing(10);
        auto* cardTitle = new QLabel(QStringLiteral("悬浮窗"), card);
        cardTitle->setObjectName(QStringLiteral("CardTitle"));
        cardTitle->setAttribute(Qt::WA_TransparentForMouseEvents);
        {
            QFont f = cardTitle->font();
            f.setPixelSize(13);
            f.setWeight(QFont::DemiBold);
            cardTitle->setFont(f);
        }
        spawnBtn_ = new liveaio::util::ChromeButton(QStringLiteral("投放测试"), card,
                                                    liveaio::util::kControlH,
                                                    liveaio::util::ControlVariant::Neutral);
        spawnBtn_->setPadX(14);
        QObject::connect(spawnBtn_, &QPushButton::clicked, this, [this]() {
            if (!runtime_) return;
            runtime_->spawnTest(1);
            refreshOpenBtn();
            refreshStats();
        });
        openBtn_ = new liveaio::util::ChromeButton(QStringLiteral("打开悬浮窗"), card,
                                                   liveaio::util::kControlH);
        QObject::connect(openBtn_, &QPushButton::clicked, this, [this]() { toggleOverlay(); });
        row->addWidget(cardTitle, 0, Qt::AlignVCenter);
        row->addStretch();
        row->addWidget(spawnBtn_);
        row->addWidget(openBtn_);
        cardLay->addLayout(row);

        stats_ = new QLabel(QStringLiteral("场上 0 / 上限 — · 队列 0"), card);
        stats_->setAttribute(Qt::WA_TransparentForMouseEvents);
        cardLay->addWidget(stats_);
        lay->addWidget(card);

        auto* themeCard = new QFrame(page);
        themeCard->setObjectName(QStringLiteral("Card"));
        auto* themeLay = new QVBoxLayout(themeCard);
        themeLay->setContentsMargins(16, 12, 16, 12);
        themeLay->setSpacing(8);
        auto* themeRow = new QHBoxLayout;
        themeRow->setSpacing(12);
        auto* themeLbl = new QLabel(QStringLiteral("叶子外观主题"), themeCard);
        themeLbl->setStyleSheet(QStringLiteral("font-size: 14px; font-weight: 600;"));
        themeRow->addWidget(themeLbl);
        themeRow->addStretch();
        skinCombo_ = new liveaio::util::ThemedComboBox(themeCard);
        QStringList skinNames;
        const auto skins = listLeafSkins();
        for (const auto& entry : skins) {
            skinNameToId_.insert(entry.name, entry.id);
            skinNames << entry.name;
        }
        skinCombo_->addItems(skinNames);
        const QString activeId = configValue(
            liveaio::resources::skinConfigKey(QStringLiteral("leaf")),
            QStringLiteral("default")).toString();
        for (const auto& entry : skins) {
            if (entry.id == activeId) skinCombo_->setCurrentText(entry.name);
        }
        skinCombo_->setFixedHeight(liveaio::util::kControlH);
        skinCombo_->setMinimumWidth(160);
        skinCombo_->setOnChange([this](const QString& name) { onSkinChanged(name); });
        themeRow->addWidget(skinCombo_);
        themeLay->addLayout(themeRow);
        lay->addWidget(themeCard);

        auto* settingsCard = new QFrame(page);
        settingsCard->setObjectName(QStringLiteral("Card"));
        auto* settingsLay = new QVBoxLayout(settingsCard);
        settingsLay->setContentsMargins(16, 10, 16, 12);
        settingsLay->setSpacing(6);
        auto* settingsTitle = new QLabel(QStringLiteral("容量与尺寸"), settingsCard);
        settingsTitle->setObjectName(QStringLiteral("CardTitle"));
        {
            QFont f = settingsTitle->font();
            f.setPixelSize(13);
            f.setWeight(QFont::DemiBold);
            settingsTitle->setFont(f);
        }
        settingsTitle->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Maximum);
        settingsLay->addWidget(settingsTitle, 0, Qt::AlignTop);

        // 预览在左、尺寸控件在右；预览固定尺寸，不拉伸。
        auto* sizeRow = new QHBoxLayout;
        sizeRow->setSpacing(12);
        sizeRow->setContentsMargins(0, 0, 0, 0);
        sizeRow->setAlignment(Qt::AlignTop);
        preview_ = new LeafScalePreview(settingsCard);
        sizeRow->addWidget(preview_, 0, Qt::AlignTop);

        auto* controls = new QWidget(settingsCard);
        controls->setMinimumWidth(200);
        auto* settingsGrid = new QGridLayout(controls);
        settingsGrid->setContentsMargins(0, 0, 0, 0);
        settingsGrid->setHorizontalSpacing(8);
        settingsGrid->setVerticalSpacing(8);
        settingsGrid->setColumnStretch(1, 1);
        maxLeavesSpin_ = new liveaio::util::ThemedSpinBox(controls);
        leafScaleSpin_ = new liveaio::util::ThemedSpinBox(controls);
        trashScaleSpin_ = new liveaio::util::ThemedSpinBox(controls);
        for (QSpinBox* spin : {maxLeavesSpin_, leafScaleSpin_, trashScaleSpin_}) {
            spin->setFixedHeight(liveaio::util::kControlH);
            spin->setMinimumWidth(100);
            spin->setAlignment(Qt::AlignCenter);
        }
        leafScaleSpin_->setRange(50, 200);
        trashScaleSpin_->setRange(50, 200);
        leafScaleSpin_->setSingleStep(5);
        trashScaleSpin_->setSingleStep(5);
        leafScaleSpin_->setSuffix(QStringLiteral("%"));
        trashScaleSpin_->setSuffix(QStringLiteral("%"));
        leafScaleSpin_->setValue(scalePercent(kLeafScaleKey));
        trashScaleSpin_->setValue(scalePercent(kTrashScaleKey));
        // 容量按理论上限的百分比给，20%~70%。
        maxLeavesSpin_->setRange(kCapPercentMin, kCapPercentMax);
        maxLeavesSpin_->setSingleStep(5);
        maxLeavesSpin_->setSuffix(QStringLiteral("%"));
        maxLeavesSpin_->setValue(configuredMaxPercent());
        preview_->setScales(leafScaleSpin_->value(), trashScaleSpin_->value());
        settingsGrid->addWidget(formLabel(QStringLiteral("最多同时出现"), controls, 96), 0, 0);
        settingsGrid->addWidget(maxLeavesSpin_, 0, 1, Qt::AlignLeft);
        settingsGrid->addWidget(formLabel(QStringLiteral("叶子尺寸"), controls, 96), 1, 0);
        settingsGrid->addWidget(leafScaleSpin_, 1, 1, Qt::AlignLeft);
        settingsGrid->addWidget(formLabel(QStringLiteral("垃圾桶尺寸"), controls, 96), 2, 0);
        settingsGrid->addWidget(trashScaleSpin_, 2, 1, Qt::AlignLeft);
        sizeRow->addWidget(controls, 0, Qt::AlignTop);
        settingsLay->addLayout(sizeRow);
        lay->addWidget(settingsCard);

        QObject::connect(maxLeavesSpin_, &QSpinBox::valueChanged,
                         this, [this](int) { applySettingsFromControls(); });
        QObject::connect(leafScaleSpin_, &QSpinBox::valueChanged,
                         this, [this](int) { applySettingsFromControls(); });
        QObject::connect(trashScaleSpin_, &QSpinBox::valueChanged,
                         this, [this](int) { applySettingsFromControls(); });

        auto* simCard = new QFrame(page);
        simCard->setObjectName(QStringLiteral("Card"));
        auto* simLay = new QVBoxLayout(simCard);
        simLay->setContentsMargins(16, 10, 16, 12);
        simLay->setSpacing(6);
        auto* simTitle = new QLabel(QStringLiteral("模拟送礼"), simCard);
        simTitle->setObjectName(QStringLiteral("CardTitle"));
        {
            QFont f = simTitle->font();
            f.setPixelSize(13);
            f.setWeight(QFont::DemiBold);
            simTitle->setFont(f);
        }
        simTitle->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Maximum);
        simLay->addWidget(simTitle, 0, Qt::AlignTop);
        simWidget_ = new liveaio::util::SimGiftWidget(simCard);
        simWidget_->setClosedTip(QStringLiteral("请先打开捡叶子悬浮窗"));
        simWidget_->setOnPush([this](const QString& gift, int count) { pushSimGift(gift, count); });
        simLay->addWidget(simWidget_);
        simDesc_ = new QLabel(
            QStringLiteral("按「礼物规则」页匹配后增减叶子；未配置对应礼物则无效果。"),
            simCard);
        simDesc_->setWordWrap(true);
        simDesc_->setObjectName(QStringLiteral("ToolTip"));
        simLay->addWidget(simDesc_);
        lay->addWidget(simCard);
        // 只让底部吸收窗口剩余高度，避免标题、卡片被布局强行拉高。
        lay->addStretch(1);
        auto* scroll = liveaio::util::scrollPage(page);
        scroll->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
        return scroll;
    }

    QWidget* buildGiftTab() {
        auto* page = new QWidget;
        page->setObjectName(QStringLiteral("ToolRoot"));
        auto* outer = new QVBoxLayout(page);
        outer->setContentsMargins(20, 16, 20, 16);
        outer->setSpacing(10);

        auto* giftCard = new QFrame(page);
        giftCard->setObjectName(QStringLiteral("Card"));
        auto* giftLay = new QVBoxLayout(giftCard);
        giftLay->setContentsMargins(16, 14, 16, 14);
        giftLay->setSpacing(8);
        auto* giftTitle = new QLabel(QStringLiteral("礼物加叶子"), giftCard);
        giftTitle->setObjectName(QStringLiteral("CardTitle"));
        giftLay->addWidget(giftTitle);
        rulesHint_ = new QLabel(
            QStringLiteral("单列规则最多 10 条；礼物为 Null 的条目会被忽略。"), giftCard);
        rulesHint_->setWordWrap(true);
        giftLay->addWidget(rulesHint_);

        auto* scroll = new QScrollArea(giftCard);
        scroll->setWidgetResizable(true);
        scroll->setAlignment(Qt::AlignTop | Qt::AlignLeft);
        scroll->setFrameShape(QFrame::NoFrame);
        scroll->setStyleSheet(QStringLiteral(
            "QScrollArea { border: none; background: transparent; }"));
        rulesHost_ = new QWidget(scroll);
        rulesHost_->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Minimum);
        rulesLay_ = new QVBoxLayout(rulesHost_);
        rulesLay_->setContentsMargins(0, 0, 0, 0);
        rulesLay_->setSpacing(8);
        rulesLay_->setAlignment(Qt::AlignTop);
        scroll->setWidget(rulesHost_);
        giftLay->addWidget(scroll, 1);

        addRuleBtn_ = new liveaio::util::ChromeButton(QStringLiteral("添加规则"), giftCard,
                                                      liveaio::util::kControlH);
        addRuleBtn_->setPadX(14);
        QObject::connect(addRuleBtn_, &QPushButton::clicked, this, [this]() { addGiftRule(); });
        giftLay->addWidget(addRuleBtn_, 0, Qt::AlignLeft);
        outer->addWidget(giftCard, 1);
        rebuildGiftRuleCards();
        return page;
    }

    QSet<QString> assignedGifts(const LeafGiftRuleCard* except = nullptr) const {
        QSet<QString> out;
        for (LeafGiftRuleCard* card : ruleCards_) {
            if (!card || card == except) continue;
            const QString g = normalizeLeafGiftName(card->currentGift());
            if (!g.isEmpty()) out.insert(g);
        }
        return out;
    }

    void rebuildGiftRuleCards() {
        if (!rulesLay_) return;
        while (QLayoutItem* item = rulesLay_->takeAt(0)) {
            if (QWidget* w = item->widget()) w->deleteLater();
            delete item;
        }
        ruleCards_.clear();
        if (giftRules_.isEmpty()) giftRules_.append(LeafGiftRule{});
        for (int i = 0; i < giftRules_.size(); ++i) {
            appendGiftRuleCard(giftRules_.at(i), 40 * i);
        }
        updateAddRuleBtn();
    }

    void appendGiftRuleCard(const LeafGiftRule& rule, int iconDelayMs = 0) {
        if (!rulesLay_) return;
        // 尾部弹性占位可能不止一个，全部清掉，新卡片才会紧跟上一条。
        while (rulesLay_->count() > 0) {
            QLayoutItem* last = rulesLay_->itemAt(rulesLay_->count() - 1);
            if (!last || !last->spacerItem()) break;
            delete rulesLay_->takeAt(rulesLay_->count() - 1);
        }
        auto* card = new LeafGiftRuleCard(rule, rulesHost_);
        card->setCallbacks(
            [this]() { persistGiftRulesFromCards(); },
            [this](LeafGiftRuleCard* target) {
                auto* picker = liveaio::util::sessionGiftPicker();
                if (!picker) return;
                picker->setOnPicked([this, target](const QString& name) {
                    if (target) target->applyGift(name);
                });
                picker->openAt(target->pickAnchor(), assignedGifts(target), false);
            },
            [this](LeafGiftRuleCard* target) { removeGiftRuleCard(target); },
            [this, card]() { return assignedGifts(card); });
        rulesLay_->addWidget(card, 0, Qt::AlignTop);
        ruleCards_.append(card);
        card->reloadIconDeferred(iconDelayMs);
        rulesLay_->addStretch(1);
    }

    void addGiftRule() {
        if (ruleCards_.size() >= kMaxGiftRules) return;
        appendGiftRuleCard(LeafGiftRule{}, 0);
        persistGiftRulesFromCards();
        updateAddRuleBtn();
    }

    void removeGiftRuleCard(LeafGiftRuleCard* card) {
        if (!card) return;
        ruleCards_.removeAll(card);
        card->deleteLater();
        if (ruleCards_.isEmpty()) appendGiftRuleCard(LeafGiftRule{}, 0);
        persistGiftRulesFromCards();
        updateAddRuleBtn();
    }

    void releaseGiftTabIcons() {
        for (LeafGiftRuleCard* card : ruleCards_) {
            if (card) card->releaseIcon();
        }
    }

    void persistGiftRulesFromCards() {
        giftRules_.clear();
        for (LeafGiftRuleCard* card : ruleCards_) {
            if (card) giftRules_.append(card->toRule());
        }
        if (giftRules_.size() > kMaxGiftRules) giftRules_.resize(kMaxGiftRules);
        saveLeafGiftRules(giftRules_);
        pushGiftRulesToCore();
        if (runtime_) runtime_->applyGiftRules(giftRules_);
    }

    void pushGiftRulesToCore() {
        pushLeafGiftRulesToCore(giftRules_);
    }

    void updateAddRuleBtn() {
        if (!addRuleBtn_) return;
        addRuleBtn_->setEnabled(ruleCards_.size() < kMaxGiftRules);
        styleAddRuleBtn();
    }

    void styleAddRuleBtn() {
        // 不可用态由 ChromeButton 自己降色，这里只需重画。
        if (addRuleBtn_) addRuleBtn_->update();
    }

    void pushSimGift(const QString& gift, int count) {
        if (!runtime_ || !runtime_->isOverlayActive()) return;
        const int n = std::max(1, count);
        pushGiftRulesToCore();
        sendCore(QJsonObject{
            {QStringLiteral("op"), QStringLiteral("tool.leaf.sim_gift")},
            {QStringLiteral("gift"), gift},
            {QStringLiteral("count"), n},
            {QStringLiteral("user"), QStringLiteral("LiveAIO")},
        });
        refreshStats();
    }

    void toggleOverlay() {
        if (!runtime_) return;
        if (!OverlayHostService::instance().isToolActive(OverlayToolId::Leaf)) {
            publishToolDemand(QStringLiteral("leaf"), true);
        }
        pushGiftRulesToCore();
        QPointer<LeafToolWindow> guard(this);
        runtime_->toggleOverlay([guard]() {
            if (!guard) return;
            guard->refreshOpenBtn();
            guard->refreshStats();
        });
        if (runtime_->isOverlayActive()) runtime_->applyGiftRules(giftRules_);
        refreshOpenBtn();
        refreshStats();
    }

    void refreshOpenBtn() {
        const bool open = runtime_ && runtime_->isOverlayActive();
        openBtn_->setText(open ? QStringLiteral("关闭悬浮窗") : QStringLiteral("打开悬浮窗"));
        if (simWidget_) simWidget_->setPushEnabled(open);
        openBtn_->setVariant(open ? liveaio::util::ControlVariant::Danger
                                  : liveaio::util::ControlVariant::Outlined);
        openBtn_->update();
    }

    void styleSpawnBtn() {
        if (spawnBtn_) spawnBtn_->update();
    }

    QSize capacityReferenceSize() const {
        if (runtime_) {
            if (auto* c = runtime_->canvas(); c && c->size().isValid()) return c->size();
        }
        return savedViewportSize();
    }

    int currentMaxPercent() const {
        return maxLeavesSpin_ ? clampCapPercent(maxLeavesSpin_->value())
                              : kCapPercentDefault;
    }

    void applySettingsFromControls() {
        if (!maxLeavesSpin_ || !leafScaleSpin_ || !trashScaleSpin_) return;
        const int maxPercent = currentMaxPercent();
        const int leafPercent = leafScaleSpin_->value();
        const int trashPercent = trashScaleSpin_->value();
        writeConfigValue(kMaxPercentKey, maxPercent);
        writeConfigValue(kLeafScaleKey, leafPercent);
        writeConfigValue(kTrashScaleKey, trashPercent);
        if (preview_) preview_->setScales(leafPercent, trashPercent);
        if (runtime_) runtime_->applySettings(maxPercent, leafPercent, trashPercent);
        refreshStats();
    }

    void onSkinChanged(const QString& name) {
        const QString id = skinNameToId_.value(name, QStringLiteral("default"));
        writeConfigValue(liveaio::resources::skinConfigKey(QStringLiteral("leaf")), id);
        LeafSharedAssets::reload();
        if (preview_) preview_->reloadFromSkin();
        if (runtime_) runtime_->refreshOverlaySkin();
        refreshStats();
    }

    void refreshStats() {
        int alive = 0;
        int pending = 0;
        // 悬浮窗没开时按上次视口尺寸估算上限，面板不会显示成 0。
        int maxN = leavesForPercent(
            maxLeavesForSize(capacityReferenceSize(),
                             leafScaleSpin_ ? leafScaleSpin_->value() / 100.0 : 1.0),
            currentMaxPercent());
        if (runtime_ && runtime_->isOverlayActive()) {
            if (auto* c = runtime_->controller()->canvas()) {
                alive = c->aliveCount();
                maxN = c->maxLeaves();
                pending = c->pendingCount();
                QPointer<LeafToolWindow> guard(this);
                c->setStatsCallback([guard]() {
                    if (guard) guard->refreshStats();
                });
            }
        }
        if (stats_) {
            const QString text = QStringLiteral("场上 %1 / 上限 %2 · 队列 %3")
                                     .arg(alive)
                                     .arg(maxN)
                                     .arg(pending);
            if (stats_->text() != text) stats_->setText(text);
        }
        // setPushEnabled 只在开合状态变化时随 refreshOpenBtn 走，避免 200ms 空转。
    }

    LeafToolRuntime* runtime_ = nullptr;
    QWidget* mainTab_ = nullptr;
    QLabel* hint_ = nullptr;
    QLabel* stats_ = nullptr;
    QLabel* simDesc_ = nullptr;
    QLabel* rulesHint_ = nullptr;
    liveaio::util::SimGiftWidget* simWidget_ = nullptr;
    LeafScalePreview* preview_ = nullptr;
    QWidget* rulesHost_ = nullptr;
    QVBoxLayout* rulesLay_ = nullptr;
    liveaio::util::ChromeButton* spawnBtn_ = nullptr;
    liveaio::util::ChromeButton* openBtn_ = nullptr;
    liveaio::util::ChromeButton* addRuleBtn_ = nullptr;
    QSpinBox* maxLeavesSpin_ = nullptr;
    QSpinBox* leafScaleSpin_ = nullptr;
    QSpinBox* trashScaleSpin_ = nullptr;
    liveaio::util::ThemedComboBox* skinCombo_ = nullptr;
    QMap<QString, QString> skinNameToId_;
    QTimer* statsTimer_ = nullptr;
    QVector<LeafGiftRule> giftRules_;
    QVector<LeafGiftRuleCard*> ruleCards_;
};

static ToolRuntimeBase* createLeafRuntime(QObject* parent, std::function<void()> tryRelease) {
    return new LeafToolRuntime(parent, std::move(tryRelease));
}

static ToolWindowBase* createLeafTool(CoreClient* core, LeafToolRuntime* runtime) {
    return new LeafToolWindow(core, runtime);
}

}  // namespace liveaio::tools::leaf
