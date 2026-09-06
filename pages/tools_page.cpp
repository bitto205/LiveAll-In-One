// pages/tools_page.cpp — 工具页，对齐旧 PySide tools_page.py。
// 工具窗由 LiveAIOTools.dll 在当前 QApplication 内打开（同进程插件，无第二个应用实例）。

namespace liveaio::pages {

struct ToolMeta {
    QString id;
    QString icon;
    QString name;
    QString desc;
};

static const QVector<ToolMeta>& toolCatalog() {
    static const QVector<ToolMeta> catalog = {
        {QStringLiteral("memo"), QStringLiteral("📋"), QStringLiteral("备忘录"),
         QStringLiteral("将礼物、关注、点赞记录为可消除的列表条目")},
        {QStringLiteral("danmu"), QStringLiteral("💬"), QStringLiteral("弹幕机"),
         QStringLiteral("透明悬浮弹幕显示窗口")},
        {QStringLiteral("overtime"), QStringLiteral("⏱"), QStringLiteral("加班机"),
         QStringLiteral("透明悬浮加班显示窗口")},
        {QStringLiteral("leaf"), QStringLiteral("🍃"), QStringLiteral("捡叶子"),
         QStringLiteral("送礼堆叶子，拖到垃圾桶消除")},
    };
    return catalog;
}

// Tools DLL 的生命周期由工具页持有：加载一次，之后只是打开/前置窗口。
class ToolsPlugin {
public:
    static ToolsPlugin& instance() {
        static ToolsPlugin plugin;
        return plugin;
    }

    bool ensureLoaded(QString* error) {
        if (openFn_) return true;
        if (lib_ && !lib_->isLoaded() && !loadError_.isEmpty()) {
            if (error) *error = loadError_;
            return false;
        }
        const QString dll = locateDll();
        if (dll.isEmpty()) {
            loadError_ = QStringLiteral("未找到 LiveAIOTools.dll");
            if (error) *error = loadError_;
            return false;
        }
        // Tools DLL 与 Qt 运行时/插件同目录部署，先让加载器能找到它们。
        const QString dir = QFileInfo(dll).absolutePath();
        qputenv("PATH", (dir + QStringLiteral(";") + qEnvironmentVariable("PATH")).toLocal8Bit());
        qputenv("QT_PLUGIN_PATH", dir.toLocal8Bit());
        qputenv("QT_QPA_PLATFORM_PLUGIN_PATH",
                QDir(dir).filePath(QStringLiteral("platforms")).toLocal8Bit());
        qputenv("LIVEAIO_ROOT", g_appRoot.toLocal8Bit());

        lib_ = new QLibrary(dll);
        if (!lib_->load()) {
            loadError_ = lib_->errorString();
            if (error) *error = loadError_;
            return false;
        }
        openFn_ = reinterpret_cast<OpenFn>(lib_->resolve("LiveAIO_ToolsOpen"));
        overlayFn_ = reinterpret_cast<OverlayFn>(
            lib_->resolve("LiveAIO_ToolsOverlayCommand"));
        overlayStateFn_ = reinterpret_cast<OverlayStateFn>(
            lib_->resolve("LiveAIO_ToolsOverlayState"));
        themeFn_ = reinterpret_cast<ThemeFn>(lib_->resolve("LiveAIO_ToolsApplyTheme"));
        warmFn_ = reinterpret_cast<WarmFn>(lib_->resolve("LiveAIO_ToolsWarm"));
        shutdownFn_ = reinterpret_cast<ShutdownFn>(lib_->resolve("LiveAIO_ToolsShutdown"));
        if (!openFn_) {
            loadError_ = QStringLiteral("LiveAIOTools.dll 缺少 LiveAIO_ToolsOpen 导出");
            if (error) *error = loadError_;
            return false;
        }
        applyTheme(liveaio::util::currentThemeName());
        if (warmFn_) warmFn_();
        return true;
    }

    bool open(const QString& toolId, QString* error) {
        if (!ensureLoaded(error)) return false;
        const int rc = openFn_(toolId.toUtf8().constData());
        if (rc != 0) {
            if (error) *error = QStringLiteral("工具打开失败（错误码 %1）").arg(rc);
            return false;
        }
        return true;
    }

    bool overlayCommand(const QString& toolId, const QString& action, QString* error = nullptr) {
        if (!ensureLoaded(error)) return false;
        if (!overlayFn_) {
            if (error) *error = QStringLiteral("LiveAIOTools.dll 缺少悬浮窗控制导出");
            return false;
        }
        const int rc = overlayFn_(toolId.toUtf8().constData(), action.toUtf8().constData());
        if (rc != 0 && error) *error = QStringLiteral("悬浮窗操作失败（错误码 %1）").arg(rc);
        return rc == 0;
    }

    int overlayState(const QString& toolId) {
        QString error;
        if (!ensureLoaded(&error) || !overlayStateFn_) return 0;
        return overlayStateFn_(toolId.toUtf8().constData());
    }

    void applyTheme(const QString& name) {
        if (themeFn_) themeFn_(name.toUtf8().constData());
    }

    // 主窗全关（未开托盘）时调用；Tools 未加载则 no-op。
    void shutdown() {
        if (shutdownFn_) shutdownFn_();
    }

    bool loaded() const { return openFn_ != nullptr; }

private:
    using OpenFn = int (*)(const char*);
    using OverlayFn = int (*)(const char*, const char*);
    using OverlayStateFn = int (*)(const char*);
    using ThemeFn = void (*)(const char*);
    using WarmFn = void (*)();
    using ShutdownFn = void (*)();

    static QStringList artifactCandidates(const QString& root, const QString& name) {
        QStringList out;
        auto add = [&](const QString& p) {
            if (p.isEmpty() || out.contains(p)) return;
            out.append(p);
        };
        // 与 core/paths.go ArtifactCandidates 对齐：包目录 / exe 旁优先，再 build_work。
        add(QDir(QCoreApplication::applicationDirPath()).filePath(name));
        if (!root.isEmpty()) {
            add(QDir(root).filePath(name));
            add(QDir(root).filePath(QStringLiteral("build/build_work/custom/%1").arg(name)));
            const QDir ver(QDir(root).filePath(QStringLiteral("build/build_work")));
            if (ver.exists()) {
                QStringList dirs = ver.entryList(QDir::Dirs | QDir::NoDotAndDotDot);
                std::sort(dirs.begin(), dirs.end(),
                          [](const QString& a, const QString& b) { return a > b; });
                for (const QString& d : dirs) {
                    if (d == QLatin1String("custom") || d.startsWith(QLatin1String("stage_"))) continue;
                    add(ver.filePath(d + QLatin1Char('/') + name));
                }
            }
        }
        return out;
    }

    static QString locateDll() {
        const QStringList candidates = artifactCandidates(g_appRoot, QStringLiteral("LiveAIOTools.dll"));
        for (const QString& path : candidates) {
            if (QFileInfo::exists(path)) return path;
        }
        return {};
    }

    QLibrary* lib_ = nullptr;
    OpenFn openFn_ = nullptr;
    OverlayFn overlayFn_ = nullptr;
    OverlayStateFn overlayStateFn_ = nullptr;
    ThemeFn themeFn_ = nullptr;
    WarmFn warmFn_ = nullptr;
    ShutdownFn shutdownFn_ = nullptr;
    QString loadError_;
};

class ToolsPage final : public BasePage {
public:
    explicit ToolsPage(CoreClient* core, QWidget* parent = nullptr)
        : BasePage(parent), core_(core) {
        auto* inner = new QWidget;
        auto* lay = new QVBoxLayout(inner);
        lay->setContentsMargins(32, 32, 32, 32);
        lay->setSpacing(16);

        auto* title = new QLabel(QStringLiteral("工具"), inner);
        title->setObjectName(QStringLiteral("PageTitle"));
        title->setAttribute(Qt::WA_TransparentForMouseEvents);
        auto* sub = new QLabel(QStringLiteral("直播辅助工具集"), inner);
        sub->setObjectName(QStringLiteral("PageSubtitle"));
        sub->setAttribute(Qt::WA_TransparentForMouseEvents);
        lay->addWidget(title);
        lay->addWidget(sub);

        error_ = new QLabel(QString(), inner);
        error_->setWordWrap(true);
        error_->setVisible(false);
        error_->setAttribute(Qt::WA_TransparentForMouseEvents);
        lay->addWidget(error_);
        lay->addSpacing(8);

        for (const ToolMeta& meta : toolCatalog()) lay->addWidget(makeCard(meta, inner));
        lay->addStretch();

        auto* root = new QVBoxLayout(this);
        root->setContentsMargins(0, 0, 0, 0);
        auto* scroll = scrollPage(inner);
        scroll->viewport()->setMouseTracking(true);
        scroll->viewport()->setAttribute(Qt::WA_Hover, true);
        inner->setMouseTracking(true);
        inner->setAttribute(Qt::WA_Hover, true);
        root->addWidget(scroll);

        // 滚动视口级批量悬停：从卡片上方/缝隙划入「打开」也能亮。
        QVector<QWidget*> hoverTargets;
        hoverTargets.reserve(openBtns_.size());
        for (auto* btn : openBtns_) hoverTargets.append(btn);
        wireHoverBatch(scroll->viewport(), hoverTargets);
        wireHoverBatch(inner, hoverTargets);

        toast_ = new Toast(this);
        refreshTheme();
    }

    void refreshTheme() override {
        const auto& C = theme();
        const QString nameStyle = QStringLiteral(
            "background: transparent; font-size: 15px; font-weight: 600; color: %1;").arg(C.text);
        const QString descStyle = QStringLiteral(
            "background: transparent; font-size: 12px; color: %1;").arg(C.textMuted);
        for (auto* lbl : nameLabels_) lbl->setStyleSheet(nameStyle);
        for (auto* lbl : descLabels_) lbl->setStyleSheet(descStyle);
        // OutlinedButton 自绘，主题变更靠 onThemeChange；无需再套 QSS。
        error_->setStyleSheet(qssErrorLabel(13));
        // 工具窗与主界面同主题：把主题名推进 Tools DLL。
        if (ToolsPlugin::instance().loaded()) {
            ToolsPlugin::instance().applyTheme(liveaio::util::currentThemeName());
        }
    }

protected:
    void resizeEvent(QResizeEvent* event) override {
        BasePage::resizeEvent(event);
        if (toast_) toast_->reposition();
    }

private:
    QFrame* makeCard(const ToolMeta& meta, QWidget* parent) {
        auto* card = new QFrame(parent);
        card->setObjectName(QStringLiteral("Card"));
        auto* lay = new QVBoxLayout(card);
        lay->setContentsMargins(20, 16, 20, 16);
        lay->setSpacing(8);

        auto* row = new QHBoxLayout;
        auto* name = new QLabel(QStringLiteral("%1  %2").arg(meta.icon, meta.name), card);
        // 名称/描述不抢鼠标，避免从上方划入「打开」时悬停进不了态。
        name->setAttribute(Qt::WA_TransparentForMouseEvents);
        auto* btn = new OutlinedButton(QStringLiteral("打开"), card, kControlH);
        card->setMouseTracking(true);
        card->setAttribute(Qt::WA_Hover, true);
        wireHoverProxy(card, btn);
        QObject::connect(btn, &QPushButton::clicked, this, [this, id = meta.id]() { openTool(id); });
        row->addWidget(name);
        row->addStretch();
        row->addWidget(btn);
        lay->addLayout(row);

        auto* desc = new QLabel(meta.desc, card);
        desc->setWordWrap(true);
        desc->setAttribute(Qt::WA_TransparentForMouseEvents);
        lay->addWidget(desc);

        nameLabels_.append(name);
        descLabels_.append(desc);
        openBtns_.append(btn);
        return card;
    }

    void openTool(const QString& id) {
        auto& plugin = ToolsPlugin::instance();
        if (plugin.loaded()) {
            QString error;
            if (plugin.open(id, &error)) {
                error_->setVisible(false);
                return;
            }
            error_->setText(QStringLiteral("工具打开失败：%1").arg(error));
            error_->setVisible(true);
            toast_->showMsg(QStringLiteral("工具打开失败"), true);
            return;
        }
        if (loadingTools_) {
            pendingOpenId_ = id;
            return;
        }
        loadingTools_ = true;
        pendingOpenId_ = id;
        toast_->showMsg(QStringLiteral("正在加载工具…"), false, 4000);
        // 拆到下一事件循环，先让 toast 上屏，避免同步 LoadLibrary 堵死首帧。
        QTimer::singleShot(0, this, [this]() {
            QString error;
            const bool ok = ToolsPlugin::instance().ensureLoaded(&error);
            loadingTools_ = false;
            const QString id = pendingOpenId_;
            pendingOpenId_.clear();
            if (!ok) {
                error_->setText(QStringLiteral("工具加载失败：%1").arg(error));
                error_->setVisible(true);
                toast_->showMsg(QStringLiteral("工具加载失败"), true);
                return;
            }
            ToolsPlugin::instance().applyTheme(liveaio::util::currentThemeName());
            if (!id.isEmpty()) openTool(id);
        });
    }

    CoreClient* core_ = nullptr;
    Toast* toast_ = nullptr;
    QLabel* error_ = nullptr;
    QVector<QLabel*> nameLabels_;
    QVector<QLabel*> descLabels_;
    QVector<OutlinedButton*> openBtns_;
    bool loadingTools_ = false;
    QString pendingOpenId_;
};

}  // namespace liveaio::pages
