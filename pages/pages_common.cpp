// pages/pages_common.cpp — Pages 侧共享设施：core 客户端、配置读写、Toast。
// 主题与控件统一来自 util/widgets.cpp，页面不再各自定义颜色。

#include <QApplication>
#include <QCloseEvent>
#include <QEventLoop>
#include <QComboBox>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFrame>
#include <QFontMetrics>
#include <QGraphicsDropShadowEffect>
#include <QGraphicsEffect>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLibrary>
#include <QLineEdit>
#include <QMap>
#include <QMessageBox>
#include <QMouseEvent>
#include <QPainter>
#include <QPainterPath>
#include <QRegion>
#include <QScrollArea>
#include <QShowEvent>
#include <QSizePolicy>
#include <QStackedWidget>
#include <QTcpSocket>
#include <QTimer>
#include <QVBoxLayout>
#include <QWidget>

#include <algorithm>
#include <functional>

#include "../util/widgets.cpp"

namespace liveaio::pages {

using liveaio::util::NavItem;
using liveaio::util::Sidebar;
using liveaio::util::StepCard;
using liveaio::util::ThemedComboBox;
using liveaio::util::ThemedToggle;
using liveaio::util::TitleBar;
using liveaio::util::kWindowCornerRadius;
using liveaio::util::kWindowShadowMargin;
using liveaio::util::labelRow;
using liveaio::util::qssAccentLabel;
using liveaio::util::qssBack;
using liveaio::util::qssDanger;
using liveaio::util::qssDisabled;
using liveaio::util::qssErrorLabel;
using liveaio::util::qssLineEdit;
using liveaio::util::qssMutedLabel;
using liveaio::util::qssOutlined;
using liveaio::util::qssOutlinedBesideEdit;
using liveaio::util::qssSuccess;
using liveaio::util::scrollPage;
using liveaio::util::shellQss;
using liveaio::util::stepCard;
using liveaio::util::theme;

static constexpr const char* kCoreHost = "127.0.0.1";
static constexpr quint16 kCorePort = 19877;

static QString g_appRoot;
static bool g_minimizeToTray = true;
static std::function<void(const QString&, const QVariant&)> g_configWriter;

static QVariantMap readConfigMap() {
    QFile file(QDir(g_appRoot).filePath(QStringLiteral("config.json")));
    if (!file.open(QIODevice::ReadOnly)) return {};
    QJsonParseError err;
    const auto doc = QJsonDocument::fromJson(file.readAll(), &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject()) return {};
    return doc.object().toVariantMap();
}

static QVariant configValue(const QString& key, const QVariant& fallback = {}) {
    const QVariantMap map = readConfigMap();
    const auto it = map.constFind(key);
    return it == map.constEnd() ? fallback : it.value();
}

class CoreClient final : public QObject {
public:
    explicit CoreClient(QObject* parent = nullptr) : QObject(parent), socket_(new QTcpSocket(this)) {
        QObject::connect(socket_, &QTcpSocket::readyRead, this, [this]() { onReadyRead(); });
        QObject::connect(socket_, &QTcpSocket::connected, this, [this]() {
            if (statusCb_) statusCb_(true);
        });
        QObject::connect(socket_, &QTcpSocket::disconnected, this, [this]() {
            ready_ = false;
            if (statusCb_) statusCb_(false);
        });
    }

    void connectToCore() { socket_->connectToHost(QString::fromLatin1(kCoreHost), kCorePort); }
    void setPacketCallback(std::function<void(const QJsonObject&)> cb) { packetCb_ = std::move(cb); }
    void setStatusCallback(std::function<void(bool)> cb) { statusCb_ = std::move(cb); }
    bool isReady() const { return ready_; }

    void send(const QJsonObject& packet) {
        if (socket_->state() != QAbstractSocket::ConnectedState) return;
        QByteArray out = QJsonDocument(packet).toJson(QJsonDocument::Compact);
        out.push_back('\n');
        socket_->write(out);
    }

    void connectLive(const QString& route, const QString& liveId, bool forceSystem = false) {
        send(QJsonObject{
            {QStringLiteral("op"), QStringLiteral("connect")},
            {QStringLiteral("route"), route},
            {QStringLiteral("live_id"), liveId},
            {QStringLiteral("force_system"), forceSystem},
        });
    }

    void disconnectLive() {
        send(QJsonObject{{QStringLiteral("op"), QStringLiteral("disconnect")}});
    }

    void uiCommand(const QString& action, const QString& route = {}, const QJsonObject& payload = {}) {
        QJsonObject p{
            {QStringLiteral("op"), QStringLiteral("ui.command")},
            {QStringLiteral("action"), action},
        };
        if (!route.isEmpty()) p.insert(QStringLiteral("route"), route);
        if (!payload.isEmpty()) p.insert(QStringLiteral("payload"), payload);
        send(p);
    }

    void configSet(const QString& key, const QJsonValue& value) {
        send(QJsonObject{
            {QStringLiteral("op"), QStringLiteral("config.set")},
            {QStringLiteral("key"), key},
            {QStringLiteral("value"), value},
        });
    }

    void configGet(const QString& key = {}) {
        QJsonObject p{{QStringLiteral("op"), QStringLiteral("config.get")}};
        if (!key.isEmpty()) p.insert(QStringLiteral("key"), key);
        send(p);
    }

    void shutdownCore() {
        send(QJsonObject{{QStringLiteral("op"), QStringLiteral("shutdown")}});
    }

    // 关主窗且不收进托盘：发 quit.shutdown_all；bye/超时后再 quit，避免 closeEvent 嵌套 QEventLoop。
    void requestFullShutdown(int timeoutMs = 2500) {
        if (socket_->state() != QAbstractSocket::ConnectedState) {
            QTimer::singleShot(0, qApp, &QCoreApplication::quit);
            return;
        }
        uiCommand(QStringLiteral("quit.shutdown_all"));
        socket_->flush();
        if (waitingBye_) return;
        waitingBye_ = true;
        QTimer::singleShot(timeoutMs, qApp, [this]() {
            if (!waitingBye_) return;
            waitingBye_ = false;
            qApp->quit();
        });
    }

private:
    void onReadyRead() {
        buffer_.append(socket_->readAll());
        while (true) {
            const int idx = buffer_.indexOf('\n');
            if (idx < 0) break;
            QByteArray line = buffer_.left(idx).trimmed();
            buffer_.remove(0, idx + 1);
            if (line.isEmpty()) continue;
            QJsonParseError err;
            const auto doc = QJsonDocument::fromJson(line, &err);
            if (err.error != QJsonParseError::NoError || !doc.isObject()) continue;
            QJsonObject packet = doc.object();
            const QString op = packet.value(QStringLiteral("op")).toString();
            if (op == QStringLiteral("ready")) {
                ready_ = true;
                if (waitingBye_ && packet.value(QStringLiteral("bye")).toBool()) {
                    waitingBye_ = false;
                    QTimer::singleShot(0, qApp, &QCoreApplication::quit);
                }
            }
            if (packetCb_) packetCb_(packet);
        }
    }

    QTcpSocket* socket_;
    QByteArray buffer_;
    bool ready_ = false;
    bool waitingBye_ = false;
    std::function<void(const QJsonObject&)> packetCb_;
    std::function<void(bool)> statusCb_;
};

// core 是 config.json 的唯一写者；UI 只发 config.set。
static void installConfigBridge(CoreClient* core) {
    g_configWriter = [core](const QString& key, const QVariant& value) {
        if (core) core->configSet(key, QJsonValue::fromVariant(value));
    };
    liveaio::util::setConfigAccessors(
        [](const QString& key, const QVariant& fallback) { return configValue(key, fallback); },
        [](const QString& key, const QVariant& value) {
            if (g_configWriter) g_configWriter(key, value);
        });
    liveaio::util::setThemePersistHook([](const QString& name) {
        if (g_configWriter) g_configWriter(QStringLiteral("theme"), name);
    });
}

// 与旧 pages.BasePage 一致：页面只需按需覆写消息 / 状态 / 主题三个钩子。
class BasePage : public QWidget {
public:
    explicit BasePage(QWidget* parent = nullptr) : QWidget(parent) {}
    virtual void onCorePacket(const QJsonObject&) {}
    virtual void onStatusChange(bool) {}
    virtual void refreshTheme() {}
};

class Toast final : public QLabel {
public:
    explicit Toast(QWidget* parent) : QLabel(parent) {
        setAlignment(Qt::AlignCenter);
        hide();
        timer_.setSingleShot(true);
        QObject::connect(&timer_, &QTimer::timeout, this, [this]() { hide(); });
    }

    void showMsg(const QString& msg, bool error = false, int ms = 2500) {
        const QString bg = error ? theme().closeHover : theme().activeLine;
        setStyleSheet(QStringLiteral(
            "background: %1; color: #ffffff; border-radius: 8px; padding: 8px 20px;"
            " font-size: 13px; font-weight: 600;"
        ).arg(bg));
        setText(msg);
        adjustSize();
        reposition();
        show();
        raise();
        timer_.start(ms);
    }

    void reposition() {
        if (!parentWidget()) return;
        move(qMax(0, (parentWidget()->width() - width()) / 2), 12);
    }

protected:
    void resizeEvent(QResizeEvent* e) override {
        QLabel::resizeEvent(e);
        if (isVisible()) reposition();
    }

private:
    QTimer timer_;
};

}  // namespace liveaio::pages
