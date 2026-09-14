package core

import (
	"context"
	"log/slog"
	"os"
	"os/signal"
	"runtime"
	"runtime/debug"
	"strings"
	"syscall"
	"time"

	"liveaio/listener"
)

// SupervisorRun is the process lifecycle owner shared by:
//   - production: LiveAIO.exe (shell) → LiveAIO_CoreMain
//   - debug:      go run ./main
//
// Owns: single-instance, optional UAC (unless shell already elevated), hub lifetime,
// graceful shutdown. Tray is a *module* on a side thread — it must not own the process
// or call os.Exit. Qt Pages is lazy: a separate LiveAIO.exe --pages child (never LoadLibrary
// Qt into the Go/Core process — that path hits winthrow / 0xc0000005 under F5).
func SupervisorRun(args []string) int {
	debug.SetTraceback("single")
	if os.Getenv("GOTRACEBACK") == "" {
		_ = os.Setenv("GOTRACEBACK", "single")
	}
	debug.SetMaxStack(64 << 20)

	// 入口 OS 线程留给 Supervisor 等待循环；托盘不得 Lock 走这条线。
	runtime.LockOSThread()

	fs := newFlagSet(args)
	exe, _ := os.Executable()
	root := fs.root
	if root == "" {
		root = ResolveAppRoot(exe)
	}
	_ = ChdirRoot(root)
	_ = os.Setenv("LIVEAIO_ROOT", root)
	if fs.noTray {
		_ = os.Setenv("LIVEAIO_NO_TRAY", "1")
	}

	// 壳已提权时设 LIVEAIO_SHELL_ELEVATED=1，避免重复 UAC。
	if !fs.noAdmin && os.Getenv("LIVEAIO_SHELL_ELEVATED") == "" {
		if err := EnsureAdmin(exe, BuildElevatedParams(args), root); err != nil {
			return 1
		}
	}

	log := FileLogger(root, "liveaio.log")
	openUI := !fs.noUI

	if !TryHoldLauncher() {
		log.Info("already running")
		if openUI {
			if err := RequestShowUI(fs.tcp); err != nil {
				log.Error("request show UI", "err", err)
				return 1
			}
		}
		return 0
	}
	defer ReleaseLauncher()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	h, srv := startHub(ctx, root, fs.tcp, log, cancel)
	bindPagesBridge(h)
	defer func() {
		h.stopCapture()
		listener.ReapOwnedBrowsers(root)
	}()
	defer h.overtime.Stop()
	defer func() {
		StopPagesChild()
		done := make(chan struct{})
		go func() {
			srv.Stop()
			close(done)
		}()
		select {
		case <-done:
		case <-time.After(3 * time.Second):
			log.Warn("hub stop timed out")
		}
	}()

	uiReq := make(chan struct{}, 1)
	requestUI := func() {
		pagesProcMu.Lock()
		running := pagesRunning
		pagesProcMu.Unlock()
		if running {
			if err := OpenPages(root); err != nil {
				log.Error("raise UI", "err", err)
			}
			return
		}
		select {
		case uiReq <- struct{}{}:
		default:
		}
	}
	h.showUI = requestUI

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	go func() {
		select {
		case <-sigCh:
			cancel()
		case <-ctx.Done():
		}
		QuitTray()
	}()

	if !fs.noTray {
		startTrayModule(root, log, h, cancel)
	}

	if openUI {
		select {
		case uiReq <- struct{}{}:
		default:
		}
	}

	log.Info("supervisor running", "root", root, "ui", openUI, "tray", !fs.noTray)

	// 生命周期等待：由 Supervisor 拥有，不是托盘 GetMessage。
	for {
		select {
		case <-ctx.Done():
			StopPagesChild()
			QuitTray()
			return 0
		case <-uiReq:
			log.Info("pages starting (child)", "root", root)
			cmd, err := StartPagesChild(root, fs.noAdmin)
			if err != nil {
				log.Error("pages child", "err", err, "root", root)
				cancel()
				QuitTray()
				return 1
			}
			done := make(chan error, 1)
			go func() { done <- WaitPagesChild(cmd) }()
			select {
			case <-ctx.Done():
				StopPagesChild()
				<-done
				QuitTray()
				return 0
			case waitErr := <-done:
				if waitErr != nil {
					log.Warn("pages child exited", "err", waitErr)
				}
				// Pages 进程退出（完整关 UI）→ Supervisor 收尾。
				cancel()
				QuitTray()
				return 0
			}
		}
	}
}

func startTrayModule(root string, log *slog.Logger, h *hub, cancel context.CancelFunc) {
	go func() {
		runtime.LockOSThread()
		log.Info("tray module starting")
		err := RunTray(TrayConfig{
			Root:     root,
			Tooltip:  "LiveAIO",
			OnShowUI: func() { h.requestShowUI() },
			OnOverlay: func(tool, action string) {
				if err := OverlayCommand(root, tool, action); err != nil {
					log.Error("overlay command", "tool", tool, "action", action, "err", err)
				}
			},
			OverlayState: OverlayState,
			OnQuit: func() {
				// 只通知 Supervisor；禁止 os.Exit（进程退出权在 Supervisor/壳）。
				cancel()
				QuitTray()
			},
		})
		if err != nil {
			log.Error("tray module failed", "err", err)
			cancel()
		}
	}()
	time.Sleep(80 * time.Millisecond)
}

// Run keeps the historical name for DLL / main entry.
func Run(args []string) int { return SupervisorRun(args) }

func QuoteArg(s string) string {
	if s == "" {
		return `""`
	}
	needs := false
	for _, r := range s {
		if r == ' ' || r == '\t' || r == '"' {
			needs = true
			break
		}
	}
	if !needs {
		return s
	}
	out := `"`
	for _, r := range s {
		if r == '"' {
			out += `\`
		}
		out += string(r)
	}
	out += `"`
	return out
}

func BuildElevatedParams(argv []string) string {
	if len(argv) <= 1 {
		return ""
	}
	var parts []string
	for _, a := range argv[1:] {
		parts = append(parts, QuoteArg(a))
	}
	return strings.Join(parts, " ")
}
