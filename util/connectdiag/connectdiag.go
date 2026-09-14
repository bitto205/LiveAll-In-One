// Package connectdiag — connect failure diagnosis (routes 1/2/4; route 3 uses manual lifecycle).
package connectdiag

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os/exec"
	"strings"
	"time"

	"github.com/chromedp/chromedp"
)

const (
	ProxyShellName      = "proxy_shell.exe"
	ProxyShellIPCPort   = 19098
	ProxyShellProxyPort = 19088

	BadRoomMinWait = 4 * time.Second

	MsgConnected  = "直播间已连接"
	MsgNotLiving  = "直播间未开播"
	// Route 4 (proxy_shell decides live): fail connect when not on-air.
	MsgNotLivingConnectFail = "直播间未开播连接失败"
	MsgProxyShellNoResponse = "proxy_shell未正常响应"
	// Companion / proxy_shell not running — expected until user starts 直播伴侣.
	MsgProxyShellNotRunning = "请先启动直播伴侣并开播（未检测到 proxy_shell）"
	MsgBadRoom    = "房间号错误，请检查房间号"
	MsgTimeoutNet = "连接超时，请检查您的网络连接与配置，可在github提出issue"
	MsgTimeout    = "连接超时，请检查您的网络连接"
)

type ConnectCode string

const (
	CodeConnected  ConnectCode = "connected"
	CodeNotLiving  ConnectCode = "not_living"
	CodeBadRoom    ConnectCode = "bad_room"
	CodeTimeoutNet ConnectCode = "timeout_net"
	CodeTimeout    ConnectCode = "timeout"
)

// ConnectError is a user-facing connect outcome (cases 2–5).
type ConnectError struct {
	Code   ConnectCode
	Detail string
}

func (e *ConnectError) Error() string {
	if e == nil {
		return ""
	}
	// Full user-facing sentences already in Detail (Route 4).
	if e.Detail != "" && (e.Detail == MsgNotLivingConnectFail ||
		strings.HasPrefix(e.Detail, MsgProxyShellNoResponse) ||
		strings.HasPrefix(e.Detail, MsgProxyShellNotRunning)) {
		return e.Detail
	}
	base := MsgTimeout
	switch e.Code {
	case CodeNotLiving:
		base = MsgNotLiving
	case CodeBadRoom:
		base = MsgBadRoom
	case CodeTimeoutNet:
		base = MsgTimeoutNet
	case CodeTimeout:
		base = MsgTimeout
	}
	if e.Detail == "" {
		return base
	}
	return base + "\n" + e.Detail
}

func ErrNotLiving() error  { return &ConnectError{Code: CodeNotLiving} }
func ErrBadRoom() error     { return &ConnectError{Code: CodeBadRoom} }
func ErrTimeoutNet() error  { return &ConnectError{Code: CodeTimeoutNet} }
func ErrTimeout() error     { return &ConnectError{Code: CodeTimeout} }
func ErrWithDetail(code ConnectCode, detail string) error {
	return &ConnectError{Code: code, Detail: strings.TrimSpace(detail)}
}

// ErrNotLivingConnectFail is Route 4 when proxy_shell reports not on-air.
func ErrNotLivingConnectFail() error {
	return &ConnectError{Code: CodeNotLiving, Detail: MsgNotLivingConnectFail}
}

// ErrProxyShellNoResponse is Route 4 when live-query / IPC does not answer.
// Do not classify as network timeout — missing companion is normal until connect.
func ErrProxyShellNoResponse(cause error) error {
	return &ConnectError{Code: CodeTimeout, Detail: formatProxyShellFail(InspectProxyShell(), cause)}
}

func AsConnectError(err error) (*ConnectError, bool) {
	var ce *ConnectError
	if errors.As(err, &ce) {
		return ce, true
	}
	return nil, false
}

// PageHints from a title/body probe at connect failure time.
type PageHints struct {
	Title          string
	AnchorLiveRoom bool
	LandingPage    bool
	EndedLive      bool // page body shows 直播已结束 (common for private/offline rooms)
}

func ReadPageTitle(ctx context.Context) (string, error) {
	var title string
	err := chromedp.Run(ctx, chromedp.Evaluate(`document.title || ''`, &title))
	return strings.TrimSpace(title), err
}

func ReadPageBodyText(ctx context.Context, maxRunes int) (string, error) {
	if maxRunes <= 0 {
		maxRunes = 2000
	}
	var body string
	err := chromedp.Run(ctx, chromedp.Evaluate(
		fmt.Sprintf(`(document.body && document.body.innerText || '').slice(0, %d)`, maxRunes),
		&body,
	))
	return body, err
}

// LooksEndedLiveBody reports offline UI text on live.douyin.com room pages.
// Private / inaccessible rooms often keep an anchor title but never call room/enter,
// and show "直播已结束" (+ "聊天功能不可用") with empty RENDER_DATA.
func LooksEndedLiveBody(body string) bool {
	return strings.Contains(body, "直播已结束")
}

func PageHintsFromTitle(title string) PageHints {
	return PageHints{
		Title:          title,
		AnchorLiveRoom: isAnchorLiveTitle(title),
		LandingPage:    isLandingTitle(title),
	}
}

func PageHintsFromTitleAndBody(title, body string) PageHints {
	h := PageHintsFromTitle(title)
	h.EndedLive = LooksEndedLiveBody(body)
	return h
}

func isAnchorLiveTitle(title string) bool {
	title = strings.TrimSpace(title)
	if title == "" || isLandingTitle(title) {
		return false
	}
	return strings.Contains(title, "的抖音直播间")
}

func isLandingTitle(title string) bool {
	title = strings.TrimSpace(title)
	return strings.Contains(title, "抖音直播电脑版") ||
		strings.Contains(title, "抖音直播网页版入口")
}

// BrowserStuck reports no WSS and no enter progress — safe to run failure diagnosis.
func BrowserStuck(enterReqN int, enterSeen, wssOK bool) bool {
	return !wssOK && !enterSeen && enterReqN == 0
}

// TryBadRoom probes title once when stuck; returns ErrBadRoom or nil.
func TryBadRoom(ctx context.Context, enterReqN int, enterSeen bool, sinceNav time.Duration) error {
	if enterSeen || enterReqN > 0 || sinceNav < BadRoomMinWait {
		return nil
	}
	title, err := ReadPageTitle(ctx)
	if err != nil {
		return nil
	}
	if isLandingTitle(title) {
		return ErrBadRoom()
	}
	return nil
}

// TryEndedLive probes body text when stuck with no enter/WSS.
// Private / offline rooms often keep "…的抖音直播间" title but never hit room/enter.
func TryEndedLive(ctx context.Context, enterReqN int, enterSeen bool, sinceNav time.Duration) error {
	if enterSeen || enterReqN > 0 || sinceNav < BadRoomMinWait {
		return nil
	}
	body, err := ReadPageBodyText(ctx, 2000)
	if err != nil {
		return nil
	}
	if LooksEndedLiveBody(body) {
		return ErrNotLiving()
	}
	return nil
}

// ClassifyBrowserFault maps a final timeout (no WSS / hung enter) to case 3/4/5.
// Runs InternetReachable only here — not during normal capture.
func ClassifyBrowserFault(ctx context.Context, enterReqN int, enterSeen bool, hints *PageHints) error {
	h := PageHints{}
	if hints != nil {
		h = *hints
	}
	if h.Title == "" && !h.LandingPage && !h.AnchorLiveRoom && !h.EndedLive {
		if title, err := ReadPageTitle(ctx); err == nil {
			h = PageHintsFromTitle(title)
		}
	}
	if !h.EndedLive {
		if body, err := ReadPageBodyText(ctx, 2000); err == nil {
			h.EndedLive = LooksEndedLiveBody(body)
		}
	}
	if !enterSeen && enterReqN == 0 && h.LandingPage {
		return ErrBadRoom()
	}
	// Prefer not_living over timeout_net when the room page explicitly says ended.
	if h.EndedLive && !enterSeen {
		return ErrNotLiving()
	}
	if h.AnchorLiveRoom || enterReqN > 0 {
		if InternetReachable() {
			return ErrTimeoutNet()
		}
		return ErrTimeout()
	}
	if InternetReachable() {
		return ErrTimeoutNet()
	}
	return ErrTimeout()
}

// InternetReachable reports whether outbound HTTP to common hosts works.
func InternetReachable() bool {
	client := &http.Client{Timeout: 4 * time.Second}
	for _, url := range []string{
		"https://www.douyin.com/",
		"https://www.baidu.com/",
	} {
		resp, err := client.Head(url)
		if err != nil {
			continue
		}
		_ = resp.Body.Close()
		if resp.StatusCode > 0 && resp.StatusCode < 500 {
			return true
		}
	}
	return false
}

// ProxyShellStatus is a point-in-time proxy_shell / IPC health snapshot.
type ProxyShellStatus struct {
	ProcessRunning bool
	IPCListening   bool
	ProxyListening bool
	IPCPort        int
	ProxyPort      int
}

// ProxyShellPortsOpen is a lightweight poll (TCP only, no process scan).
func ProxyShellPortsOpen() bool {
	return TCPOpen("127.0.0.1", ProxyShellIPCPort) &&
		TCPOpen("127.0.0.1", ProxyShellProxyPort)
}

// InspectProxyShell checks process + TCP ports (call on connect failure only).
func InspectProxyShell() ProxyShellStatus {
	return ProxyShellStatus{
		ProcessRunning: proxyShellRunning(),
		IPCListening:   TCPOpen("127.0.0.1", ProxyShellIPCPort),
		ProxyListening: TCPOpen("127.0.0.1", ProxyShellProxyPort),
		IPCPort:        ProxyShellIPCPort,
		ProxyPort:      ProxyShellProxyPort,
	}
}

func (s ProxyShellStatus) Diagnostic() string {
	proc := "未运行"
	if s.ProcessRunning {
		proc = "运行中"
	}
	ipc := "未监听"
	if s.IPCListening {
		ipc = "正常"
	}
	proxy := "未监听"
	if s.ProxyListening {
		proxy = "正常"
	}
	return strings.Join([]string{
		fmt.Sprintf("proxy_shell.exe: %s", proc),
		fmt.Sprintf("IPC %d: %s", s.IPCPort, ipc),
		fmt.Sprintf("代理 %d: %s", s.ProxyPort, proxy),
	}, "\n")
}

func (s ProxyShellStatus) OK() bool {
	return s.ProcessRunning && s.IPCListening && s.ProxyListening
}

func formatProxyShellFail(st ProxyShellStatus, cause error) string {
	var b strings.Builder
	if !st.ProcessRunning {
		b.WriteString(MsgProxyShellNotRunning)
	} else {
		b.WriteString(MsgProxyShellNoResponse)
	}
	b.WriteByte('\n')
	b.WriteString(st.Diagnostic())
	if cause != nil && strings.TrimSpace(cause.Error()) != "" {
		b.WriteByte('\n')
		b.WriteString(strings.TrimSpace(cause.Error()))
	}
	return b.String()
}

// ErrProxyShellConnect wraps proxy_shell IPC / health failures.
// Only call from connect path. Never frames "shell not running" as network timeout.
func ErrProxyShellConnect(cause error) error {
	return &ConnectError{Code: CodeTimeout, Detail: formatProxyShellFail(InspectProxyShell(), cause)}
}

func TCPOpen(host string, port int) bool {
	c, err := net.DialTimeout("tcp", fmt.Sprintf("%s:%d", host, port), 800*time.Millisecond)
	if err != nil {
		return false
	}
	_ = c.Close()
	return true
}

func proxyShellRunning() bool {
	cmd := exec.Command("tasklist", "/FI", "IMAGENAME eq "+ProxyShellName, "/NH")
	hideConsoleWindow(cmd)
	out, err := cmd.Output()
	if err != nil {
		return false
	}
	return strings.Contains(strings.ToLower(string(out)), strings.ToLower(ProxyShellName))
}
