package listener

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"liveaio/util/connectdiag"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/fetch"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
)

const (
	defaultUA    = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
	envUseSystem = "LIVEAIO_USE_SYSTEM_BROWSER"
)

// BrowserOptions controls Chromium launch for routes 1/2 and login.
type BrowserOptions struct {
	Root          string
	ForceSystem   bool // 登录等场景强制系统浏览器
	PreferBundled bool // 线路 1/2 采集：只用 browsers/ headless，禁止回退系统
	Headless      bool
	TrimResources bool
	UserDataDir   string
}

// BrowserSession is one chromedp context.
type BrowserSession struct {
	Ctx         context.Context
	System      bool
	Exe         string
	cancel      context.CancelFunc
	allocCancel context.CancelFunc

	trimPhase atomic.Int32 // 1=bootstrap(仅拦流) 2=post-WSS 止血
	trimOnce  sync.Once

	// CDP 旁路任务共用 1 条常驻栈（fetch 放行/拦截 + trim），不再按请求/阶段开 goroutine。
	jobOnce sync.Once
	jobQ    chan sessJob
}

type fetchReply struct {
	id    fetch.RequestID
	allow bool
}

type sessJob struct {
	fetch *fetchReply
	run   func()
}

const jobQueue = 256

func statePath(root string) string { return filepath.Join(root, "state.json") }

func preferSystemFromConfig(root string) bool {
	raw, err := os.ReadFile(filepath.Join(root, "config.json"))
	if err != nil {
		return false
	}
	var m map[string]any
	if json.Unmarshal(raw, &m) != nil {
		return false
	}
	v, _ := m["use_system_browser"].(bool)
	return v
}

func findBundledExe(root string) string {
	browsers := filepath.Join(root, "browsers")
	entries, err := os.ReadDir(browsers)
	if err != nil {
		return ""
	}
	var latestPath, latestName string
	for _, e := range entries {
		if !e.IsDir() || !strings.HasPrefix(e.Name(), "chromium_headless_shell-") {
			continue
		}
		for _, sub := range []string{"chrome-headless-shell-win64", "chrome-headless-shell-win32"} {
			p := filepath.Join(browsers, e.Name(), sub, "chrome-headless-shell.exe")
			if st, err := os.Stat(p); err == nil && !st.IsDir() && e.Name() >= latestName {
				latestName = e.Name()
				latestPath = p
			}
		}
	}
	return latestPath
}

func findSystemBrowser() string {
	local := os.Getenv("LOCALAPPDATA")
	pf := os.Getenv("ProgramFiles")
	pf86 := os.Getenv("ProgramFiles(x86)")
	for _, base := range []string{local, pf, pf86} {
		if base == "" {
			continue
		}
		for _, p := range []string{
			filepath.Join(base, "Google", "Chrome", "Application", "chrome.exe"),
			filepath.Join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
		} {
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				return p
			}
		}
	}
	return ""
}

func launchBrowser(parent context.Context, opt BrowserOptions) (*BrowserSession, error) {
	forceSystem := false
	if !opt.PreferBundled {
		forceSystem = opt.ForceSystem || preferSystemFromConfig(opt.Root) || os.Getenv(envUseSystem) == "1"
	}
	exe := ""
	system := false
	if !forceSystem {
		exe = findBundledExe(opt.Root)
	}
	if exe == "" {
		if opt.PreferBundled {
			return nil, fmt.Errorf(
				"bundled headless shell not found under %s (expect browsers/chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe)",
				filepath.Join(opt.Root, "browsers"),
			)
		}
		exe = findSystemBrowser()
		system = true
		if exe == "" {
			return nil, fmt.Errorf("no Chromium found (browsers/ or system Chrome/Edge)")
		}
	}
	opts := append(chromedp.DefaultExecAllocatorOptions[:],
		chromedp.ExecPath(exe),
		chromedp.Flag("disable-blink-features", "AutomationControlled"),
		chromedp.Flag("no-sandbox", true),
		chromedp.UserAgent(defaultUA),
	)
	if opt.Headless {
		opts = append(opts, chromedp.Flag("headless", "new"))
	} else {
		opts = append(opts, chromedp.Flag("headless", false))
	}
	if opt.UserDataDir != "" {
		opts = append(opts, chromedp.UserDataDir(opt.UserDataDir))
	}
	if opt.TrimResources {
		// 采集只要进房 + WSS/JS 运行时；挡住样式/图/流/礼物插件等。
		opts = append(opts,
			chromedp.WindowSize(800, 450),
			chromedp.Flag("blink-settings", "imagesEnabled=false"),
			chromedp.Flag("autoplay-policy", "document-user-activation-required"),
			chromedp.Flag("disable-gpu", true),
			chromedp.Flag("disable-software-rasterizer", true),
			chromedp.Flag("disable-webgl", true),
			chromedp.Flag("mute-audio", true),
		)
	} else {
		opts = append(opts, chromedp.WindowSize(1920, 1080))
	}
	allocCtx, allocCancel := chromedp.NewExecAllocator(parent, opts...)
	ctx, cancel := chromedp.NewContext(allocCtx)
	s := &BrowserSession{Ctx: ctx, System: system, Exe: exe, cancel: cancel, allocCancel: allocCancel}
	if err := chromedp.Run(ctx); err != nil {
		s.Close()
		return nil, fmt.Errorf("chromedp start: %w", err)
	}
	_ = chromedp.Run(ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		return network.Enable().Do(ctx)
	}))
	if opt.TrimResources {
		s.installBootstrapFilter()
	}
	_ = applyStorageState(ctx, statePath(opt.Root))
	_ = chromedp.Run(ctx, chromedp.Evaluate(`Object.defineProperty(navigator, 'webdriver', { get: () => undefined }); window.chrome = { runtime: {} };`, nil))
	return s, nil
}

// Phase 1：只拦拉流，Document/Script/XHR 全放行（进房 + 建 WSS 必需）。
// Phase 2：WSS /push/v2/ 建立后，再拦 CSS/图/字体/礼物 UI 等。
func (s *BrowserSession) ensureJobs() {
	if s == nil {
		return
	}
	s.jobOnce.Do(func() {
		s.jobQ = make(chan sessJob, jobQueue)
		go s.jobWorker()
	})
}

func (s *BrowserSession) jobWorker() {
	for {
		select {
		case <-s.Ctx.Done():
			return
		case job, ok := <-s.jobQ:
			if !ok {
				return
			}
			if job.fetch != nil {
				if job.fetch.allow {
					_ = fetch.ContinueRequest(job.fetch.id).Do(s.Ctx)
				} else {
					_ = fetch.FailRequest(job.fetch.id, network.ErrorReasonBlockedByClient).Do(s.Ctx)
				}
			}
			if job.run != nil {
				job.run()
			}
		}
	}
}

func (s *BrowserSession) enqueueFetch(id fetch.RequestID, allow bool) {
	s.ensureJobs()
	job := sessJob{fetch: &fetchReply{id: id, allow: allow}}
	select {
	case s.jobQ <- job:
	case <-s.Ctx.Done():
	default:
		// 队列满时不得阻塞 ListenTarget；尽量入队拒绝以放行浏览器。
		deny := sessJob{fetch: &fetchReply{id: id, allow: false}}
		select {
		case s.jobQ <- deny:
		default:
		}
	}
}

func (s *BrowserSession) enqueueRun(fn func()) {
	if fn == nil {
		return
	}
	s.ensureJobs()
	select {
	case s.jobQ <- sessJob{run: fn}:
	case <-s.Ctx.Done():
	default:
		// trim 可丢；勿反压 CDP 事件环
	}
}

func (s *BrowserSession) installBootstrapFilter() {
	s.trimPhase.Store(1)
	s.ensureJobs()
	chromedp.ListenTarget(s.Ctx, func(ev any) {
		e, ok := ev.(*fetch.EventRequestPaused)
		if !ok || e == nil {
			return
		}
		u := ""
		if e.Request != nil {
			u = e.Request.URL
		}
		allow := captureAllow(s.trimPhase.Load(), e.ResourceType, u)
		// 不在此 go：页面加载期 RequestPaused 极多，无界 goroutine 会堆栈并把崩溃回溯拖死。
		s.enqueueFetch(e.RequestID, allow)
	})
	_ = chromedp.Run(s.Ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		return fetch.Enable().WithPatterns(captureBootstrapPatterns()).Do(ctx)
	}))
}

func (s *BrowserSession) applyPostWSSTrim(logf func(string, ...any)) {
	if logf == nil {
		logf = func(string, ...any) {}
	}
	s.trimOnce.Do(func() {
		s.enqueueRun(func() {
			if !s.trimPhase.CompareAndSwap(1, 2) {
				return
			}
			logf("wss trim phase2", "desc", "block css/img/font/gift ui, kill video dom")
			_ = chromedp.Run(s.Ctx,
				chromedp.ActionFunc(func(ctx context.Context) error {
					return fetch.Enable().WithPatterns(captureTrimPatterns()).Do(ctx)
				}),
				chromedp.ActionFunc(func(ctx context.Context) error {
					patterns := []*network.BlockPattern{
						{URLPattern: "*://*/webcast/gift/*", Block: true},
						{URLPattern: "*://*/*/exhibition/*", Block: true},
					}
					if err := network.SetBlockedURLs().WithURLPatterns(patterns).Do(ctx); err != nil {
						return cdp.Execute(ctx, network.CommandSetBlockedURLs, map[string]any{
							"urls": []string{"*webcast/gift/*", "*exhibition/*", "*mcs.*", "*snssdk*"},
						}, nil)
					}
					return nil
				}),
				chromedp.Evaluate(killMediaJS, nil),
			)
		})
	})
}

func captureBootstrapPatterns() []*fetch.RequestPattern {
	return []*fetch.RequestPattern{
		{ResourceType: network.ResourceTypeMedia},
		{URLPattern: "*.flv*"},
		{URLPattern: "*.m3u8*"},
		{URLPattern: "*.mp4*"},
		{URLPattern: "*flive.douyincdn*"},
		{URLPattern: "*bytefcdn*"},
		{URLPattern: "*douyincdn.com*stream-*"},
	}
}

func captureTrimPatterns() []*fetch.RequestPattern {
	out := append([]*fetch.RequestPattern{}, captureBootstrapPatterns()...)
	out = append(out,
		&fetch.RequestPattern{ResourceType: network.ResourceTypeStylesheet},
		&fetch.RequestPattern{ResourceType: network.ResourceTypeImage},
		&fetch.RequestPattern{ResourceType: network.ResourceTypeFont},
		&fetch.RequestPattern{ResourceType: network.ResourceTypeTextTrack},
		&fetch.RequestPattern{ResourceType: network.ResourceTypeManifest},
		&fetch.RequestPattern{ResourceType: network.ResourceTypePing},
		&fetch.RequestPattern{ResourceType: network.ResourceTypePrefetch},
		&fetch.RequestPattern{ResourceType: network.ResourceTypeCSPViolationReport},
		&fetch.RequestPattern{ResourceType: network.ResourceTypeSignedExchange},
		&fetch.RequestPattern{ResourceType: network.ResourceTypeFedCM},
		&fetch.RequestPattern{URLPattern: "*lottie*"},
		&fetch.RequestPattern{URLPattern: "*GiftEffect*"},
		&fetch.RequestPattern{URLPattern: "*GiftTray*"},
		&fetch.RequestPattern{URLPattern: "*GiftMenu*"},
		&fetch.RequestPattern{URLPattern: "*new-player*"},
		&fetch.RequestPattern{URLPattern: "*player-merged*"},
		&fetch.RequestPattern{URLPattern: "*webcast/gift/*"},
		&fetch.RequestPattern{URLPattern: "*exhibition/*"},
	)
	return out
}

func captureAllow(phase int32, rt network.ResourceType, rawURL string) bool {
	u := strings.ToLower(rawURL)
	for _, bad := range []string{
		".flv", ".m3u8", ".mp4", ".webm",
		"/stream-", "flive.douyincdn", "bytefcdn", "douyincdn.com/thirdgame",
	} {
		if strings.Contains(u, bad) {
			return false
		}
	}
	if rt == network.ResourceTypeMedia {
		return false
	}
	if phase < 2 {
		return true
	}
	return captureTrimAllow(rt, u)
}

func captureTrimAllow(rt network.ResourceType, rawURL string) bool {
	switch rt {
	case network.ResourceTypeStylesheet, network.ResourceTypeImage,
		network.ResourceTypeFont, network.ResourceTypeTextTrack,
		network.ResourceTypeManifest, network.ResourceTypePing,
		network.ResourceTypePrefetch, network.ResourceTypeCSPViolationReport,
		network.ResourceTypeSignedExchange, network.ResourceTypeFedCM:
		return false
	}
	for _, bad := range []string{
		"lottie", "gifteffect", "gifttray", "giftmenu",
		"new-player", "player-merged", "/webcast/gift/", "/exhibition/",
	} {
		if strings.Contains(rawURL, bad) {
			return false
		}
	}
	return true
}

const killMediaJS = `(() => {
  const kill = () => {
    document.querySelectorAll('video,audio').forEach(el => {
      try { el.pause(); el.removeAttribute('src'); el.load(); el.remove(); } catch (e) {}
    });
  };
  kill();
  if (!window.__DY_KILL_MEDIA__) {
    window.__DY_KILL_MEDIA__ = true;
    try {
      new MutationObserver(kill).observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) {}
  }
})()`

func (s *BrowserSession) Close() {
	if s == nil {
		return
	}
	if s.cancel != nil {
		s.cancel()
	}
	if s.allocCancel != nil {
		s.allocCancel()
	}
}

func moduleAckTimeout() time.Duration { return 60 * time.Second }

// ControlEnded is true for WebcastControlMessage 关播 (status=3).
func ControlEnded(status any) bool {
	switch v := status.(type) {
	case float64:
		return int(v) == 3
	case int:
		return v == 3
	case int32:
		return int(v) == 3
	case int64:
		return v == 3
	default:
		return false
	}
}

type storageState struct {
	Cookies []struct {
		Name     string  `json:"name"`
		Value    string  `json:"value"`
		Domain   string  `json:"domain"`
		Path     string  `json:"path"`
		Expires  float64 `json:"expires"`
		HTTPOnly bool    `json:"httpOnly"`
		Secure   bool    `json:"secure"`
		SameSite string  `json:"sameSite"`
	} `json:"cookies"`
}

func applyStorageState(ctx context.Context, path string) error {
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var st storageState
	if err := json.Unmarshal(raw, &st); err != nil {
		return err
	}
	cookies := make([]*network.CookieParam, 0, len(st.Cookies))
	for _, c := range st.Cookies {
		if c.Name == "" {
			continue
		}
		p := &network.CookieParam{
			Name: c.Name, Value: c.Value, Domain: c.Domain, Path: c.Path,
			HTTPOnly: c.HTTPOnly, Secure: c.Secure,
		}
		if p.Path == "" {
			p.Path = "/"
		}
		if c.Expires > 0 {
			exp := cdp.TimeSinceEpoch(time.Unix(int64(c.Expires), 0))
			p.Expires = &exp
		}
		switch c.SameSite {
		case "Strict":
			p.SameSite = network.CookieSameSiteStrict
		case "Lax":
			p.SameSite = network.CookieSameSiteLax
		case "None":
			p.SameSite = network.CookieSameSiteNone
		}
		cookies = append(cookies, p)
	}
	if len(cookies) == 0 {
		return nil
	}
	return chromedp.Run(ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		return network.SetCookies(cookies).Do(ctx)
	}))
}

func saveStorageState(ctx context.Context, path string) error {
	var cookies []*network.Cookie
	if err := chromedp.Run(ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		var err error
		cookies, err = network.GetCookies().Do(ctx)
		return err
	})); err != nil {
		return err
	}
	out := storageState{}
	for _, c := range cookies {
		exp := float64(-1)
		if c.Expires > 0 {
			exp = float64(c.Expires)
		}
		out.Cookies = append(out.Cookies, struct {
			Name     string  `json:"name"`
			Value    string  `json:"value"`
			Domain   string  `json:"domain"`
			Path     string  `json:"path"`
			Expires  float64 `json:"expires"`
			HTTPOnly bool    `json:"httpOnly"`
			Secure   bool    `json:"secure"`
			SameSite string  `json:"sameSite"`
		}{
			Name: c.Name, Value: c.Value, Domain: c.Domain, Path: c.Path,
			Expires: exp, HTTPOnly: c.HTTPOnly, Secure: c.Secure, SameSite: string(c.SameSite),
		})
	}
	b, err := json.MarshalIndent(out, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0644)
}

var (
	enterPathRE    = regexp.MustCompile(`(?i)/webcast/room/(?:web/)?enter`)
	statusInHTMLRE = regexp.MustCompile(`"status"\s*:\s*([24])`)
)

const (
	enterLiving = 2
	enterEnded  = 4
)

// EnterInfo is room enter status from web/enter or RENDER_DATA.
type EnterInfo struct {
	Status     int
	RoomStatus int
	Title      string
	IDStr      string
}

func isRoomEnterURL(u string) bool {
	return u != "" && enterPathRE.MatchString(u)
}

func isLiving(status int) bool { return status == enterLiving }

func describeEnterStatus(status int) string {
	switch status {
	case enterLiving:
		return "开播中"
	case enterEnded:
		return "未开播/已结束"
	default:
		return "未知"
	}
}

func asInt(v any, def int) int {
	switch t := v.(type) {
	case float64:
		return int(t)
	case int:
		return t
	case int64:
		return int(t)
	case string:
		n, err := strconv.Atoi(t)
		if err != nil {
			return def
		}
		return n
	case json.Number:
		n, err := t.Int64()
		if err != nil {
			return def
		}
		return int(n)
	default:
		return def
	}
}

func parseRoomEnterPayload(payload any) *EnterInfo {
	var obj map[string]any
	switch t := payload.(type) {
	case nil:
		return nil
	case []byte:
		if json.Unmarshal(t, &obj) != nil {
			return nil
		}
	case string:
		if json.Unmarshal([]byte(strings.TrimSpace(t)), &obj) != nil {
			return nil
		}
	case map[string]any:
		obj = t
	default:
		return nil
	}
	data, _ := obj["data"].(map[string]any)
	if data == nil {
		return nil
	}
	roomStatus := asInt(data["room_status"], 0)
	var room map[string]any
	switch rooms := data["data"].(type) {
	case []any:
		if len(rooms) > 0 {
			room, _ = rooms[0].(map[string]any)
		}
	case map[string]any:
		room = rooms
	}
	if room == nil {
		return &EnterInfo{Status: enterEnded, RoomStatus: roomStatus}
	}
	st := asInt(room["status"], asInt(room["status_str"], 0))
	return &EnterInfo{
		Status: st, RoomStatus: roomStatus,
		Title: strField(room["title"]), IDStr: strField(room["id_str"], room["id"]),
	}
}

func strField(vs ...any) string {
	for _, v := range vs {
		if s, ok := v.(string); ok && s != "" {
			return s
		}
		if n, ok := v.(float64); ok {
			return strconv.FormatInt(int64(n), 10)
		}
	}
	return ""
}

func walkRoomStatus(obj any, depth int) *EnterInfo {
	if depth > 8 || obj == nil {
		return nil
	}
	switch t := obj.(type) {
	case map[string]any:
		if _, ok := t["status"]; ok {
			if _, ok2 := t["status_str"]; ok2 || t["id_str"] != nil || t["title"] != nil {
				st := asInt(t["status"], asInt(t["status_str"], 0))
				if st == enterLiving || st == enterEnded {
					return &EnterInfo{
						Status: st, RoomStatus: asInt(t["room_status"], 0),
						Title: strField(t["title"]), IDStr: strField(t["id_str"], t["id"]),
					}
				}
			}
		}
		for _, v := range t {
			if found := walkRoomStatus(v, depth+1); found != nil {
				return found
			}
		}
	case []any:
		for i, v := range t {
			if i >= 20 {
				break
			}
			if found := walkRoomStatus(v, depth+1); found != nil {
				return found
			}
		}
	}
	return nil
}

func parseRenderDataText(raw string) *EnterInfo {
	if raw == "" {
		return nil
	}
	text := strings.TrimSpace(raw)
	for _, candidate := range []string{text, mustUnescape(text)} {
		var data any
		if json.Unmarshal([]byte(candidate), &data) == nil {
			if found := walkRoomStatus(data, 0); found != nil {
				return found
			}
		}
		if m := statusInHTMLRE.FindStringSubmatch(candidate); len(m) > 1 {
			st, _ := strconv.Atoi(m[1])
			return &EnterInfo{Status: st}
		}
	}
	if m := statusInHTMLRE.FindStringSubmatch(text); len(m) > 1 {
		st, _ := strconv.Atoi(m[1])
		return &EnterInfo{Status: st}
	}
	return nil
}

func mustUnescape(s string) string {
	u, err := url.QueryUnescape(s)
	if err != nil {
		return s
	}
	return u
}

func extractEnterFromPage(ctx context.Context) (*EnterInfo, error) {
	var raw string
	err := chromedp.Run(ctx, chromedp.Evaluate(`(() => {
		const el = document.getElementById('RENDER_DATA');
		if (el && el.textContent) return el.textContent;
		return '';
	})()`, &raw))
	if err != nil {
		return nil, err
	}
	return parseRenderDataText(raw), nil
}

const loginURL = "https://www.douyin.com/"

const loginConfirmJS = `(() => {
    const inject = () => {
        if (document.getElementById('__dy_login_btn__')) return;
        const btn = document.createElement('div');
        btn.id = '__dy_login_btn__';
        btn.style.cssText = 'position:fixed;bottom:30px;right:30px;z-index:999999;background:#fe2c55;color:#fff;font-size:16px;font-weight:bold;padding:14px 28px;border-radius:8px;cursor:pointer;box-shadow:0 4px 12px rgba(0,0,0,0.3);user-select:none;';
        btn.innerText = '\u2705  \u6211\u5df2\u5b8c\u6210\u767b\u5f55';
        btn.onclick = () => { window.__LOGIN_DONE__ = true; btn.innerText = '\u23f3 \u4fdd\u5b58\u4e2d...'; btn.style.background = '#888'; };
        document.body.appendChild(btn);
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', inject);
    else inject();
})();`

// DoLogin opens a headed system browser for Douyin login and saves state.json.
func DoLogin(root string) bool {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sess, err := launchBrowser(ctx, BrowserOptions{
		Root: root, ForceSystem: true, Headless: false, TrimResources: false,
	})
	if err != nil {
		return false
	}
	defer sess.Close()

	path := statePath(root)
	if err := chromedp.Run(sess.Ctx,
		chromedp.Evaluate(loginConfirmJS, nil),
		chromedp.Navigate(loginURL),
		chromedp.Evaluate(loginConfirmJS, nil),
	); err != nil {
		return false
	}

	for {
		var done bool
		err := chromedp.Run(sess.Ctx, chromedp.Evaluate(`window.__LOGIN_DONE__ === true`, &done))
		if err != nil {
			return false
		}
		if done {
			time.Sleep(time.Second)
			if err := saveStorageState(sess.Ctx, path); err != nil {
				return false
			}
			if sessionOK(path) {
				return true
			}
			_ = chromedp.Run(sess.Ctx, chromedp.Evaluate(`(() => {
				window.__LOGIN_DONE__ = false;
				const btn = document.getElementById('__dy_login_btn__');
				if (btn) { btn.innerText = '\u2705  \u6211\u5df2\u5b8c\u6210\u767b\u5f55'; btn.style.background = '#fe2c55'; }
			})()`, nil))
			continue
		}
		select {
		case <-sess.Ctx.Done():
			return false
		case <-time.After(500 * time.Millisecond):
		}
	}
}

func sessionOK(path string) bool {
	raw, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	var st struct {
		Cookies []struct {
			Name    string  `json:"name"`
			Value   string  `json:"value"`
			Expires float64 `json:"expires"`
		} `json:"cookies"`
	}
	if json.Unmarshal(raw, &st) != nil {
		return false
	}
	now := float64(time.Now().Unix())
	for _, c := range st.Cookies {
		if c.Name != "sessionid" || c.Value == "" {
			continue
		}
		if c.Expires > 0 && c.Expires < now {
			return false
		}
		return true
	}
	return false
}

// Route2Driver: chromedp WSS /push/v2/ → OnFrame.
type Route2Driver struct{}

func (Route2Driver) ID() ID { return Route2 }

func (d Route2Driver) Run(ctx context.Context, p Params) error {
	return runBrowserCapture(ctx, p, false)
}

// Route1Driver: JS hook → OnMessage (message.ingest semantics).
type Route1Driver struct{}

func (Route1Driver) ID() ID { return Route1 }

func (d Route1Driver) Run(ctx context.Context, p Params) error {
	return runBrowserCapture(ctx, p, true)
}

// Hook pushes into __DY_MSG_Q for Go drain (avoids fragile console parsing).
const hookJS = `(() => {
    if (window.__DY_HOOK__) return;
    window.__DY_HOOK__ = true;
    window.__DY_MSG_Q = window.__DY_MSG_Q || [];
    const oldPush = Array.prototype.push;
    Array.prototype.push = function (...args) {
        try {
            for (const msg of args) {
                if (!msg || typeof msg !== "object") continue;
                const method = msg.method;
                if (!method) continue;
                const payload = msg.payload || {};
                const user    = payload.user?.desensitized_nickname || payload.user?.nickname || "";
                const user_id = String(payload.user?.id || payload.user?.id_str || "");
                let data = null;
                if (method === "WebcastChatMessage") {
                    const content = payload.content || "";
                    if (user && content) data = { type: "chat", user, user_id, content };
                } else if (method === "WebcastGiftMessage") {
                    const gift = payload?.gift?.name || "";
                    const gift_id = payload?.gift?.id ?? 0;
                    const repeat_end = payload?.repeat_end;
                    const count = payload?.combo_count ? Number(payload.combo_count) : 1;
                    if (user && gift && String(repeat_end).trim() === "1")
                        data = { type: "gift", user, user_id, gift, gift_id, count, repeat_end: 1 };
                } else if (method === "WebcastLikeMessage") {
                    const count = Number(payload?.count || 1);
                    if (user) data = { type: "like", user, user_id, count };
                } else if (method === "WebcastMemberMessage") {
                    if (user) data = { type: "enter", user, user_id };
                } else if (method === "WebcastSocialMessage") {
                    if (user) data = { type: "follow", user, user_id };
                } else if (method === "WebcastRoomUserSeqMessage") {
                    data = { type: "online", current: Number(payload?.total || 0), total: Number(payload?.total_pv_for_anchor || 0) };
                } else if (method === "WebcastFansclubMessage") {
                    data = { type: "fansclub", user, user_id, content: payload?.content || "" };
                } else if (method === "WebcastEmojiChatMessage") {
                    data = { type: "emoji", user, user_id, emoji_id: String(payload?.emoji_id || ""), default_content: payload?.default_content || "" };
                } else if (method === "WebcastRoomStatsMessage") {
                    const display_long = payload?.display_long || "";
                    if (display_long) data = { type: "room_stats", display_long };
                } else if (method === "WebcastRoomRankMessage") {
                    const ranks = (payload?.ranks_list || []).map(r => ({
                        user_id: String(r?.user?.id || ""), nickname: r?.user?.nickname || r?.user?.nick_name || "", rank: Number(r?.rank || 0)
                    }));
                    data = { type: "rank", ranks };
                } else if (method === "WebcastControlMessage") {
                    data = { type: "control", status: Number(payload?.status || 0) };
                }
                if (data) { try { window.__DY_MSG_Q.push(data); } catch(e) {} }
            }
        } catch(e) {}
        return oldPush.apply(this, args);
    };
})();`

func runBrowserCapture(ctx context.Context, p Params, jsHook bool) error {
	logf := p.Logf
	if logf == nil {
		logf = func(string, ...any) {}
	}
	if p.LiveID == "" {
		return fmt.Errorf("live_id required")
	}
	logf("capture begin", "route", string(p.Route), "live_id", p.LiveID, "js_hook", jsHook)
	sess, err := launchBrowser(ctx, BrowserOptions{
		Root: p.Root, PreferBundled: true, Headless: true, TrimResources: true,
	})
	if err != nil {
		return err
	}
	defer sess.Close()
	logf("browser launched", "exe", sess.Exe, "system", sess.System, "bundled", !sess.System, "headless", true, "live_id", p.LiveID)

	var (
		mu          sync.Mutex
		liveOK      bool
		enterSeen   bool
		stopped     bool
		wssOK       bool
		offlineAt   time.Time
		pushSockets = map[network.RequestID]bool{}
		enterReqs   = map[network.RequestID]bool{}
	)

	emitStatus := func(v bool) {
		if p.OnStatus != nil {
			p.OnStatus(v)
		}
	}
	fail := func() {
		mu.Lock()
		if stopped || liveOK {
			mu.Unlock()
			return
		}
		stopped = true
		mu.Unlock()
		emitStatus(false)
	}
	confirm := func() {
		mu.Lock()
		if liveOK || stopped {
			mu.Unlock()
			return
		}
		liveOK = true
		mu.Unlock()
		emitStatus(true)
	}
	// endLive: 开播后关播（control / WSS），与进房失败区分。
	endLive := func(reason string) {
		mu.Lock()
		if stopped || !liveOK {
			mu.Unlock()
			return
		}
		stopped = true
		mu.Unlock()
		logf("live ended", "reason", reason)
		emitStatus(false)
	}
	applyEnter := func(enter *EnterInfo) {
		if enter == nil {
			return
		}
		mu.Lock()
		if enterSeen || stopped {
			mu.Unlock()
			return
		}
		enterSeen = true
		mu.Unlock()
		logf("enter status",
			"status", enter.Status, "desc", describeEnterStatus(enter.Status),
			"room_status", enter.RoomStatus, "title", enter.Title)
		if isLiving(enter.Status) {
			confirm()
		} else {
			mu.Lock()
			offlineAt = time.Now()
			mu.Unlock()
			logf("not living", "status", enter.Status, "desc", describeEnterStatus(enter.Status))
			emitStatus(false)
		}
	}

	// 采集旁路：帧解析 + 进房 body 共用 1 条栈。
	type capJob struct {
		frame []byte
		enter network.RequestID
		hasEnter bool
	}
	capCh := make(chan capJob, 128)
	go func() {
		for {
			select {
			case <-ctx.Done():
				return
			case job, ok := <-capCh:
				if !ok {
					return
				}
				if job.hasEnter {
					var body []byte
					errB := chromedp.Run(sess.Ctx, chromedp.ActionFunc(func(c context.Context) error {
						b, er := network.GetResponseBody(job.enter).Do(c)
						if er != nil {
							return er
						}
						body = b
						return nil
					}))
					if errB != nil || len(body) == 0 {
						continue
					}
					applyEnter(parseRoomEnterPayload(body))
					continue
				}
				if len(job.frame) == 0 {
					continue
				}
				if p.OnFrame != nil {
					p.OnFrame(job.frame)
				}
				if parsed, msgs := TryParseFrame(job.frame); parsed {
					for _, m := range msgs {
						if t, _ := m["type"].(string); t == "control" && ControlEnded(m["status"]) {
							endLive("control")
							break
						}
					}
				}
			}
		}
	}()

	chromedp.ListenTarget(sess.Ctx, func(ev any) {
		switch e := ev.(type) {
		case *network.EventResponseReceived:
			if isRoomEnterURL(e.Response.URL) {
				mu.Lock()
				enterReqs[e.RequestID] = true
				mu.Unlock()
			}
		case *network.EventLoadingFinished:
			mu.Lock()
			want := enterReqs[e.RequestID]
			mu.Unlock()
			if !want {
				return
			}
			// Never chromedp.Run inside ListenTarget synchronously — it deadlocks the CDP event loop.
			select {
			case capCh <- capJob{hasEnter: true, enter: e.RequestID}:
			case <-ctx.Done():
			default:
			}
		case *network.EventWebSocketCreated:
			if strings.Contains(e.URL, "/push/v2/") {
				mu.Lock()
				pushSockets[e.RequestID] = true
				wssOK = true
				mu.Unlock()
				sess.applyPostWSSTrim(logf)
			}
		case *network.EventWebSocketFrameReceived:
			mu.Lock()
			okSock := pushSockets[e.RequestID]
			okLive := liveOK && !stopped
			mu.Unlock()
			if !okSock || !okLive || jsHook {
				return
			}
			if e.Response.Opcode != 2 {
				return
			}
			raw, err := base64.StdEncoding.DecodeString(e.Response.PayloadData)
			if err != nil || len(raw) == 0 {
				raw = []byte(e.Response.PayloadData)
			}
			if len(raw) == 0 {
				return
			}
			select {
			case capCh <- capJob{frame: raw}:
			default:
			}
		case *network.EventWebSocketClosed:
			mu.Lock()
			was := pushSockets[e.RequestID] && liveOK && !stopped
			if was {
				stopped = true
			}
			mu.Unlock()
			if was {
				logf("live ended", "reason", "wss_closed")
				emitStatus(false)
			}
		case *runtime.EventConsoleAPICalled:
			_ = e // queue drain path preferred
		}
	})

	if jsHook {
		_ = chromedp.Run(sess.Ctx, chromedp.Evaluate(hookJS, nil))
	}

	url := fmt.Sprintf("https://live.douyin.com/%s", p.LiveID)
	logf("navigate live page", "url", url)
	navStarted := time.Now()
	go func() {
		navCtx, navCancel := context.WithTimeout(sess.Ctx, 45*time.Second)
		defer navCancel()
		if err := chromedp.Run(navCtx, chromedp.Navigate(url)); err != nil {
			logf("navigate incomplete", "err", err)
		}
		if jsHook {
			_ = chromedp.Run(sess.Ctx, chromedp.Evaluate(hookJS, nil))
		}
	}()

	// 线路 1/2：只认开播/关播，不对「是否开播」设超时；仅在页面/模块完全无应答时超时。
	ackDeadline := time.Now().Add(moduleAckTimeout())
	enterHangN := 0
	badRoomProbed := false
	ticker := time.NewTicker(400 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			emitStatus(false)
			return ctx.Err()
		case <-ticker.C:
			mu.Lock()
			doneEnter := enterSeen || liveOK || stopped
			alive := liveOK && !stopped
			offline := enterSeen && !liveOK && !stopped
			offAt := offlineAt
			wss := wssOK
			seenEnter := enterSeen
			enterReqN := len(enterReqs)
			mu.Unlock()

			if offline && (wss || (!offAt.IsZero() && time.Since(offAt) > 3*time.Second)) {
				logf("capture idle", "reason", "not_living", "wss", wss)
				emitStatus(false)
				return errNotLiving()
			}

			if !doneEnter {
				if fb, _ := extractEnterFromPage(sess.Ctx); fb != nil {
					applyEnter(fb)
				}
			}

			if connectdiag.BrowserStuck(enterReqN, seenEnter, wss) && !badRoomProbed &&
				time.Since(navStarted) >= connectdiag.BadRoomMinWait {
				badRoomProbed = true
				if err := connectdiag.TryBadRoom(sess.Ctx, enterReqN, seenEnter, time.Since(navStarted)); err != nil {
					logf("page signal", "bad_room", true, "reason", "landing_title")
					emitStatus(false)
					return err
				}
			}

			mu.Lock()
			doneEnter = enterSeen || liveOK || stopped
			seenEnter = enterSeen
			enterReqN = len(enterReqs)
			mu.Unlock()

			if !doneEnter && time.Now().After(ackDeadline) {
				fb, _ := extractEnterFromPage(sess.Ctx)
				if fb != nil {
					applyEnter(fb)
				} else if enterReqN == 0 {
					fail()
					return connectdiag.ClassifyBrowserFault(sess.Ctx, enterReqN, seenEnter, nil)
				} else {
					enterHangN++
					if enterHangN > 2 {
						fail()
						return connectdiag.ClassifyBrowserFault(sess.Ctx, enterReqN, seenEnter, nil)
					}
					// 已有 enter 网络应答，继续等 status（开播/关播）
					ackDeadline = time.Now().Add(moduleAckTimeout())
					logf("waiting enter status", "enter_reqs", enterReqN)
				}
			}
			if jsHook && alive {
				if drainHookQueue(sess.Ctx, p) {
					endLive("control")
				}
			}
			mu.Lock()
			ended := stopped && liveOK
			mu.Unlock()
			if ended {
				return nil
			}
		}
	}
}

func drainHookQueue(ctx context.Context, p Params) (ended bool) {
	var rawJSON string
	err := chromedp.Run(ctx, chromedp.Evaluate(`(() => {
		const q = window.__DY_MSG_Q;
		if (!q || !q.length) return '';
		return JSON.stringify(q.splice(0, q.length));
	})()`, &rawJSON))
	if err != nil || rawJSON == "" || rawJSON == "null" {
		return false
	}
	var items []map[string]any
	if json.Unmarshal([]byte(rawJSON), &items) != nil {
		return false
	}
	for _, it := range items {
		m := Msg{}
		for k, v := range it {
			m[k] = v
		}
		if t, _ := m["type"].(string); t == "control" && ControlEnded(m["status"]) {
			ended = true
		}
		if p.OnMessage != nil {
			p.OnMessage(m)
		}
	}
	return ended
}
