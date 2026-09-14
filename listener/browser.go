package listener

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
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
	"github.com/chromedp/cdproto/page"
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
	RequireState  bool // 线路采集：启动浏览器前必须通过 state.json 登录预检
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

	// Windows Job Object + pid so Close/process-exit reaps the whole Chrome tree.
	chromeJob  *chromeJob
	browserPID atomic.Int32
	userDataDir string // per-session temp profile; removed on Close
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
	if opt.RequireState {
		if err := CheckSessionState(opt.Root); err != nil {
			return nil, fmt.Errorf("state.json 预检查失败，请先登录：%w", err)
		}
	}
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
	userDataDir := opt.UserDataDir
	if userDataDir == "" && opt.TrimResources {
		// Fresh profile each capture avoids reconnect flakes (locked Singleton*
		// / half-written prefs after hard kill) that show up as enter_reqs=0.
		userDataDir = filepath.Join(os.TempDir(),
			fmt.Sprintf("liveaio-cap-%d-%d", os.Getpid(), time.Now().UnixNano()))
		if err := os.MkdirAll(userDataDir, 0o700); err != nil {
			return nil, fmt.Errorf("chrome user-data-dir: %w", err)
		}
	}
	if userDataDir != "" {
		opts = append(opts, chromedp.UserDataDir(userDataDir))
	}
	if opt.TrimResources {
		// Capture profile: allow enter-room + WSS/JS to boot, then phase-2 filter
		// trims UI. Aggressive low-end / 480p / 128MB V8 previously left
		// enter_reqs=0 (page never called /webcast/room/enter).
		opts = append(opts,
			chromedp.WindowSize(800, 450),
			chromedp.Flag("blink-settings", "imagesEnabled=false"),
			chromedp.Flag("autoplay-policy", "document-user-activation-required"),
			chromedp.Flag("disable-gpu", true),
			chromedp.Flag("disable-software-rasterizer", true),
			chromedp.Flag("disable-webgl", true),
			chromedp.Flag("mute-audio", true),
			// Headless tabs otherwise throttle timers/WS after a while.
			chromedp.Flag("disable-background-timer-throttling", true),
			chromedp.Flag("disable-backgrounding-occluded-windows", true),
			chromedp.Flag("disable-renderer-backgrounding", true),
		)
	} else {
		opts = append(opts, chromedp.WindowSize(1920, 1080))
	}

	job, jobErr := newChromeJob()
	if jobErr != nil {
		job = nil
	}
	var earlyPID int32
	opts = append(opts, chromedp.ModifyCmdFunc(func(cmd *exec.Cmd) {
		// Register as LiveAIO child process (no detach) + Job Object membership.
		bindChromeChildCmd(cmd, exe)
		if job == nil {
			return
		}
		go attachCmdToJob(cmd, job, &earlyPID)
	}))

	allocCtx, allocCancel := chromedp.NewExecAllocator(parent, opts...)
	ctx, cancel := chromedp.NewContext(allocCtx)
	s := &BrowserSession{
		Ctx: ctx, System: system, Exe: exe,
		cancel: cancel, allocCancel: allocCancel,
		chromeJob: job, userDataDir: userDataDir,
	}
	if err := chromedp.Run(ctx); err != nil {
		s.Close()
		return nil, fmt.Errorf("chromedp start: %w", err)
	}
	if pid := atomic.LoadInt32(&earlyPID); pid > 0 {
		s.browserPID.Store(pid)
	} else {
		// Slow assign path: wait briefly for ModifyCmdFunc goroutine.
		deadline := time.Now().Add(2 * time.Second)
		for time.Now().Before(deadline) {
			if pid := atomic.LoadInt32(&earlyPID); pid > 0 {
				s.browserPID.Store(pid)
				break
			}
			time.Sleep(10 * time.Millisecond)
		}
	}
	_ = chromedp.Run(ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		return network.Enable().Do(ctx)
	}))
	if opt.TrimResources {
		s.installBootstrapFilter()
	}
	if err := applyStorageState(ctx, statePath(opt.Root)); err != nil && opt.RequireState {
		s.Close()
		return nil, fmt.Errorf("加载 state.json：%w", err)
	}
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
			logf("wss trim phase2", "desc", "block css/img/font/gift ui, lean dom")
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
				chromedp.Evaluate(leanDomJS, nil),
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

// Pause A/V without removing <video>: Douyin WSS signature / room boot can
// depend on a video node existing. Stripping it mid-session also hurts reconnect.
const killMediaJS = `(() => {
  const kill = () => {
    document.querySelectorAll('video,audio').forEach(el => {
      try {
        if (typeof el.pause === 'function') el.pause();
        el.removeAttribute('src');
        if (typeof el.load === 'function') el.load();
      } catch (e) {}
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

// leanDomJS strips visual-heavy media after WSS is up. Keep <video>/<audio> nodes
// (paused only). Do NOT wipe nodes whose class contains "gift"/"player" — Douyin
// nests IM dispatch under those shells, and removing them empties the route-1
// JS hook while WSS itself stays fine.
const leanDomJS = `(() => {
  const lean = () => {
    document.querySelectorAll('video,audio').forEach(el => {
      try {
        if (typeof el.pause === 'function') el.pause();
        el.removeAttribute('src');
        if (typeof el.load === 'function') el.load();
      } catch (e) {}
    });
    document.querySelectorAll('canvas,iframe,img,picture,source,svg,lottie-player').forEach(el => {
      try {
        el.removeAttribute('src');
        el.remove();
      } catch (e) {}
    });
    try {
      if (window.gc) window.gc();
    } catch (e) {}
  };
  lean();
  if (!window.__DY_LEAN_DOM__) {
    window.__DY_LEAN_DOM__ = true;
    try {
      let t = 0;
      new MutationObserver(() => {
        const now = Date.now();
        if (now - t < 1500) return;
        t = now;
        lean();
      }).observe(document.documentElement, { childList: true, subtree: true });
    } catch (e) {}
    try { setInterval(lean, 8000); } catch (e) {}
  }
})()`

func (s *BrowserSession) leaveLiveBestEffort(logf func(string, ...any), root string) {
	if s == nil || s.Ctx == nil || s.Ctx.Err() != nil {
		return
	}
	if logf == nil {
		logf = func(string, ...any) {}
	}
	// Persist cookies refreshed during this capture (ttwid etc.). Reusing a
	// stale state.json on the next Start is a common cause of enter_reqs=0.
	if root != "" {
		saveCtx, saveCancel := context.WithTimeout(s.Ctx, 800*time.Millisecond)
		if err := saveStorageState(saveCtx, statePath(root)); err != nil {
			logf("leave save state", "err", err)
		} else {
			logf("leave save state", "ok", true)
		}
		saveCancel()
	}
	// Navigate away while the page/WSS are still alive so Douyin can drop the
	// watcher presence. Hard-killing Chrome leaves the account "in room" for a while.
	leaveCtx, cancel := context.WithTimeout(s.Ctx, 2500*time.Millisecond)
	defer cancel()
	err := chromedp.Run(leaveCtx,
		chromedp.Evaluate(`(() => {
  try { window.stop(); } catch (e) {}
  try {
    for (const k of Object.keys(window)) {
      const v = window[k];
      if (v && v.constructor && v.constructor.name === 'WebSocket') {
        try { v.close(); } catch (e2) {}
      }
    }
  } catch (e) {}
  return true;
})()`, nil),
		// Homepage first so the site can clear room presence; blank alone is weaker.
		chromedp.Navigate("https://live.douyin.com/"),
		chromedp.Sleep(400*time.Millisecond),
		chromedp.Navigate("about:blank"),
	)
	if err != nil {
		logf("leave live", "err", err)
		return
	}
	logf("leave live", "ok", true)
}

func (s *BrowserSession) Close() {
	if s == nil {
		return
	}
	var once sync.Once
	reap := func() {
		once.Do(func() {
			pid := int(s.browserPID.Load())
			if s.chromeJob != nil {
				s.chromeJob.close()
				s.chromeJob = nil
			}
			if processAlive(pid) {
				killProcessTree(pid)
			}
			untrackOwnedBrowser(pid)
			s.browserPID.Store(0)
			if s.userDataDir != "" {
				_ = os.RemoveAll(s.userDataDir)
				s.userDataDir = ""
			}
		})
	}

	done := make(chan struct{})
	go func() {
		defer close(done)
		defer reap()
		if s.Ctx != nil && s.Ctx.Err() == nil {
			tctx, tcancel := context.WithTimeout(s.Ctx, 1500*time.Millisecond)
			_ = chromedp.Cancel(tctx)
			tcancel()
		}
		if s.cancel != nil {
			s.cancel()
			s.cancel = nil
		}
		if s.allocCancel != nil {
			allocDone := make(chan struct{})
			go func() {
				s.allocCancel()
				close(allocDone)
			}()
			select {
			case <-allocDone:
			case <-time.After(1500 * time.Millisecond):
			}
			s.allocCancel = nil
		}
	}()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		reap()
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
func DoLogin(root string) error {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	sess, err := launchBrowser(ctx, BrowserOptions{
		Root: root, ForceSystem: true, Headless: false, TrimResources: false,
	})
	if err != nil {
		return fmt.Errorf("启动登录浏览器：%w", err)
	}
	defer sess.Close()

	path := statePath(root)
	if err := chromedp.Run(sess.Ctx,
		chromedp.Evaluate(loginConfirmJS, nil),
		chromedp.Navigate(loginURL),
		chromedp.Evaluate(loginConfirmJS, nil),
	); err != nil {
		return fmt.Errorf("打开抖音登录页：%w", err)
	}

	for {
		var done bool
		err := chromedp.Run(sess.Ctx, chromedp.Evaluate(`window.__LOGIN_DONE__ === true`, &done))
		if err != nil {
			return fmt.Errorf("登录页面已关闭或无法访问：%w", err)
		}
		if done {
			time.Sleep(time.Second)
			if err := saveStorageState(sess.Ctx, path); err != nil {
				return fmt.Errorf("保存 state.json：%w", err)
			}
			if sessionOK(path) {
				return nil
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
			return fmt.Errorf("登录页面已关闭")
		case <-time.After(500 * time.Millisecond):
		}
	}
}

// CheckSessionState validates the local login credential before routes 1/2 start.
func CheckSessionState(root string) error {
	path := statePath(root)
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("未找到 state.json")
	}
	var st struct {
		Cookies []struct {
			Name    string  `json:"name"`
			Value   string  `json:"value"`
			Expires float64 `json:"expires"`
		} `json:"cookies"`
	}
	if err := json.Unmarshal(raw, &st); err != nil {
		return fmt.Errorf("state.json 格式错误：%w", err)
	}
	now := float64(time.Now().Unix())
	for _, c := range st.Cookies {
		if (c.Name != "sessionid" && c.Name != "sessionid_ss") || c.Value == "" {
			continue
		}
		if c.Expires > 0 && c.Expires < now {
			continue
		}
		return nil
	}
	return fmt.Errorf("登录凭证缺失或已过期")
}

func sessionOK(path string) bool {
	return CheckSessionState(filepath.Dir(path)) == nil
}

// Route2Driver: chromedp WSS /push/v2/ → OnFrame.
type Route2Driver struct{}

func (Route2Driver) ID() ID { return Route2 }

func (d Route2Driver) Run(ctx context.Context, p Params) error {
	return runBrowserCapture(ctx, p, false)
}

// Route1Driver: chromedp live page + WSS /push/v2/ → OnFrame (same as route 2).
// JS hook remains a pre-WSS backup only; after push socket is up, frames are authoritative.
type Route1Driver struct{}

func (Route1Driver) ID() ID { return Route1 }

func (d Route1Driver) Run(ctx context.Context, p Params) error {
	return runBrowserCapture(ctx, p, true)
}

// Hook pushes into __DY_MSG_Q for Go drain (avoids fragile console parsing).
// Gift fields: Douyin web payload is usually camelCase (comboCount/repeatEnd);
// some builds also emit snake_case — read both. Track peak combo until repeatEnd
// so a final frame with comboCount=1 still settles the full burst (e.g. 66×小心心).
const hookJS = `(() => {
    window.__DY_MSG_Q = window.__DY_MSG_Q || [];
    window.__DY_GIFT_COMBO__ = window.__DY_GIFT_COMBO__ || Object.create(null);
    if (!window.__DY_PUSH_ORIG__) {
        window.__DY_PUSH_ORIG__ = Array.prototype.push;
    }
    const origPush = window.__DY_PUSH_ORIG__;
    const num = (v) => {
        const n = Number(v);
        return Number.isFinite(n) ? n : 0;
    };
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
                    const gift_id = payload?.gift?.id ?? payload?.gift?.id_str ?? 0;
                    const repeat_end = payload?.repeat_end ?? payload?.repeatEnd;
                    const combo = Math.max(
                        num(payload?.combo_count), num(payload?.comboCount),
                        num(payload?.repeat_count), num(payload?.repeatCount),
                        num(payload?.group_count), num(payload?.groupCount));
                    const key = String(user_id || user) + "|" + String(gift_id || gift);
                    const ended = String(repeat_end).trim() === "1" || repeat_end === 1 || repeat_end === true;
                    if (user && gift) {
                        const prev = num(window.__DY_GIFT_COMBO__[key]);
                        const peak = Math.max(prev, combo, 1);
                        if (!ended) {
                            window.__DY_GIFT_COMBO__[key] = peak;
                        } else {
                            delete window.__DY_GIFT_COMBO__[key];
                            const count = Math.max(peak, combo, 1);
                            data = { type: "gift", user, user_id, gift, gift_id, count, repeat_end: 1 };
                        }
                    }
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
                if (data) { try { origPush.call(window.__DY_MSG_Q, data); } catch(e) {} }
            }
        } catch(e) {}
        return origPush.apply(this, args);
    };
    window.__DY_HOOK__ = true;
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
	// Own the browser on a detached allocator so parent cancel does not SIGKILL
	// Chrome mid-room — we leave via about:blank first, then Close gracefully.
	sess, err := launchBrowser(context.Background(), BrowserOptions{
		Root: p.Root, PreferBundled: true, RequireState: true,
		Headless: true, TrimResources: true,
	})
	if err != nil {
		return err
	}
	defer func() {
		// Bound leave so a wedged CDP cannot skip Close / job kill forever.
		left := make(chan struct{})
		go func() {
			defer close(left)
			sess.leaveLiveBestEffort(logf, p.Root)
		}()
		select {
		case <-left:
		case <-time.After(2 * time.Second):
			logf("leave live", "hard_timeout", true)
		}
		sess.Close()
	}()
	logf("browser launched",
		"exe", sess.Exe, "system", sess.System, "bundled", !sess.System,
		"headless", true, "child_pid", sess.browserPID.Load(),
		"user_data", sess.userDataDir != "", "live_id", p.LiveID)

	var (
		mu          sync.Mutex
		liveOK      bool
		enterSeen   bool
		stopped     bool
		wssOK       bool
		offlineAt   time.Time
		wssDownAt   time.Time
		lastHookAt  time.Time
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
		frame    []byte
		enter    network.RequestID
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
				_, known := pushSockets[e.RequestID]
				pushSockets[e.RequestID] = true
				reconnected := wssOK || !wssDownAt.IsZero()
				wssOK = true
				wssDownAt = time.Time{}
				mu.Unlock()
				if !known {
					if reconnected {
						logf("wss reconnected", "url", e.URL)
					} else {
						logf("wss created", "url", e.URL)
					}
				}
				sess.applyPostWSSTrim(logf)
				if jsHook {
					sess.enqueueRun(func() {
						_ = chromedp.Run(sess.Ctx, chromedp.Evaluate(hookJS, nil))
					})
				}
			}
		case *network.EventWebSocketFrameReceived:
			mu.Lock()
			okSock := pushSockets[e.RequestID]
			okLive := liveOK && !stopped
			mu.Unlock()
			// Always ingest /push/v2/ binary frames (same as route 2). Route 1 used to
			// skip this when jsHook was on and rely solely on Array.prototype.push;
			// leanDom / page changes routinely leave that hook dry while WSS still
			// delivers WebcastGiftMessage — gifts never reached Core/tools.
			if !okSock || !okLive {
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
			// Douyin rotates push sockets; do not end the live session immediately.
			mu.Lock()
			if pushSockets[e.RequestID] {
				delete(pushSockets, e.RequestID)
				if liveOK && !stopped && len(pushSockets) == 0 {
					wssOK = false
					wssDownAt = time.Now()
					// Douyin often rotates push sockets; short grace false-ends the room.
					logf("wss down", "grace_s", 30)
				}
			}
			mu.Unlock()
		case *runtime.EventConsoleAPICalled:
			_ = e // queue drain path preferred
		}
	})

	// Route 1: install hook on every new document *before* Navigate so it survives
	// the live page load. A one-shot Evaluate before Navigate only hits about:blank
	// and is wiped; re-injecting only after chromedp.Navigate's load wait (often 45s
	// with resource trim) delayed all gifts/chat until "navigate incomplete".
	if jsHook {
		_ = chromedp.Run(sess.Ctx, chromedp.ActionFunc(func(ctx context.Context) error {
			_, err := page.AddScriptToEvaluateOnNewDocument(hookJS).Do(ctx)
			return err
		}))
	}

	url := fmt.Sprintf("https://live.douyin.com/%s", p.LiveID)
	logf("navigate live page", "url", url)
	navStarted := time.Now()
	go func() {
		navCtx, navCancel := context.WithTimeout(sess.Ctx, 20*time.Second)
		defer navCancel()
		// page.Navigate returns when the navigation is accepted — do not wait for
		// window loadEvent (trim blocks css/img so load may never complete).
		if err := chromedp.Run(navCtx, chromedp.ActionFunc(func(ctx context.Context) error {
			_, _, errText, _, err := page.Navigate(url).Do(ctx)
			if err != nil {
				return err
			}
			if errText != "" {
				return fmt.Errorf("%s", errText)
			}
			return nil
		})); err != nil {
			logf("navigate incomplete", "err", err)
		} else {
			logf("navigate issued")
		}
		if jsHook {
			// Belt-and-suspenders if on-new-document raced the first paint.
			_ = chromedp.Run(sess.Ctx, chromedp.Evaluate(hookJS, nil))
		}
	}()

	// 线路 1/2：只认开播/关播，不对「是否开播」设超时；仅在页面/模块完全无应答时超时。
	ackDeadline := time.Now().Add(moduleAckTimeout())
	enterHangN := 0
	badRoomProbed := false
	lastWaitLog := time.Time{}
	ticker := time.NewTicker(400 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			// Do not emitStatus(false) on cancel. Hub OpConnect/Disconnect already
			// publishes authoritative status; a late false here races the next
			// connect and flashed 连接失败 while enter status=2 was succeeding.
			return ctx.Err()
		case <-ticker.C:
			if ctx.Err() != nil {
				return ctx.Err()
			}
			// Keep CDP probes short so Manager.Stop cancel is observed quickly;
			// otherwise a wedged Evaluate can delay leave/Close by tens of seconds.
			probeCtx, probeCancel := context.WithTimeout(sess.Ctx, 800*time.Millisecond)
			mu.Lock()
			doneEnter := enterSeen || liveOK || stopped
			alive := liveOK && !stopped
			offline := enterSeen && !liveOK && !stopped
			offAt := offlineAt
			wss := wssOK
			wssDown := wssDownAt
			seenEnter := enterSeen
			enterReqN := len(enterReqs)
			mu.Unlock()

			if alive && !wss && !wssDown.IsZero() && time.Since(wssDown) > 30*time.Second {
				probeCancel()
				endLive("wss_closed")
				continue
			}

			if offline && (wss || (!offAt.IsZero() && time.Since(offAt) > 8*time.Second)) {
				probeCancel()
				logf("capture idle", "reason", "not_living", "wss", wss)
				emitStatus(false)
				return errNotLiving()
			}

			if !doneEnter {
				if fb, _ := extractEnterFromPage(probeCtx); fb != nil {
					applyEnter(fb)
				}
			}

			if connectdiag.BrowserStuck(enterReqN, seenEnter, wss) &&
				time.Since(navStarted) >= connectdiag.BadRoomMinWait {
				since := time.Since(navStarted)
				if !badRoomProbed {
					badRoomProbed = true
					if err := connectdiag.TryBadRoom(probeCtx, enterReqN, seenEnter, since); err != nil {
						probeCancel()
						logf("page signal", "bad_room", true, "reason", "landing_title")
						emitStatus(false)
						return err
					}
				}
				// Private/offline: title still "…的抖音直播间", no RENDER_DATA / room/enter,
				// body shows 直播已结束 — treat as not_living instead of hanging → timeout_net.
				if err := connectdiag.TryEndedLive(probeCtx, enterReqN, seenEnter, since); err != nil {
					probeCancel()
					logf("page signal", "not_living", true, "reason", "ended_body")
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
				fb, _ := extractEnterFromPage(probeCtx)
				if fb != nil {
					applyEnter(fb)
				} else if enterReqN == 0 {
					probeCancel()
					fail()
					return connectdiag.ClassifyBrowserFault(sess.Ctx, enterReqN, seenEnter, nil)
				} else {
					enterHangN++
					if enterHangN > 2 {
						probeCancel()
						fail()
						return connectdiag.ClassifyBrowserFault(sess.Ctx, enterReqN, seenEnter, nil)
					}
					// 已有 enter 网络应答，继续等 status（开播/关播）
					ackDeadline = time.Now().Add(moduleAckTimeout())
					logf("waiting enter status", "enter_reqs", enterReqN)
				}
			} else if !doneEnter && time.Since(navStarted) >= 8*time.Second &&
				(lastWaitLog.IsZero() || time.Since(lastWaitLog) >= 8*time.Second) {
				lastWaitLog = time.Now()
				var title, href string
				_ = chromedp.Run(probeCtx,
					chromedp.Evaluate(`document.title||''`, &title),
					chromedp.Evaluate(`location.href||''`, &href),
				)
				logf("waiting room enter", "elapsed_s", int(time.Since(navStarted).Seconds()),
					"enter_reqs", enterReqN, "wss", wss, "title", title, "href", href)
			}
			if jsHook && alive {
				if lastHookAt.IsZero() || time.Since(lastHookAt) >= 12*time.Second {
					lastHookAt = time.Now()
					_ = chromedp.Run(probeCtx, chromedp.Evaluate(hookJS, nil))
				}
				// Prefer WSS protobuf once the push socket is up to avoid double
				// gift/chat from hook + frame. Hook remains a pre-WSS backup.
				if !wss {
					if drainHookQueue(probeCtx, p) {
						probeCancel()
						endLive("control")
						continue
					}
				}
			}
			probeCancel()
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
