package core

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"time"
)

var (
	pagesProcMu   sync.Mutex
	pagesRunning  bool
	pagesEverRan  bool
	pagesChild    *exec.Cmd
	pagesBridgeMu sync.Mutex
	pagesHub      *hub
	overlayStates = map[string]int{}
)

// bindPagesBridge wires hub broadcast for out-of-process Pages (overlay / focus).
func bindPagesBridge(h *hub) {
	pagesBridgeMu.Lock()
	pagesHub = h
	pagesBridgeMu.Unlock()
}

func pagesHubSend(env Envelope) {
	pagesBridgeMu.Lock()
	h := pagesHub
	pagesBridgeMu.Unlock()
	if h != nil {
		h.send(env)
	}
}

func setOverlayStateCache(tool string, state int) {
	pagesProcMu.Lock()
	overlayStates[tool] = state
	pagesProcMu.Unlock()
}

// OpenPages raises an already-running Pages UI via IPC (ui.focus already sent by hub).
// First start must use StartPagesChild — Qt must not load into the Go/Core process.
func OpenPages(root string) error {
	_ = root
	pagesProcMu.Lock()
	running := pagesRunning
	ever := pagesEverRan
	pagesProcMu.Unlock()
	if running {
		return nil
	}
	if ever {
		return fmt.Errorf("pages UI already exited")
	}
	return fmt.Errorf("pages UI not started yet")
}

// OverlayCommand asks the Pages child (via hub broadcast) to toggle overlay chrome.
func OverlayCommand(root, tool, action string) error {
	_ = root
	pagesProcMu.Lock()
	running := pagesRunning
	pagesProcMu.Unlock()
	if !running {
		return fmt.Errorf("pages UI not running (open UI first)")
	}
	pagesHubSend(Envelope{
		"op":     OpUIOverlay,
		"tool":   tool,
		"action": action,
	})
	return nil
}

// OverlayState returns cached bits 1=open, 2=frame shown, 4=locked (updated by Pages).
func OverlayState(tool string) int {
	pagesProcMu.Lock()
	defer pagesProcMu.Unlock()
	if !pagesRunning {
		return 0
	}
	return overlayStates[tool]
}

func findHostExe(root string) (string, error) {
	return FindArtifact(root, "LiveAIO.exe")
}

// StartPagesChild launches LiveAIO.exe --pages in a separate process so Qt never
// shares an address space with the Go runtime (avoids winthrow / 0xc0000005).
func StartPagesChild(root string, noAdmin bool) (*exec.Cmd, error) {
	exe, err := findHostExe(root)
	if err != nil {
		return nil, err
	}
	pagesProcMu.Lock()
	if pagesRunning {
		cmd := pagesChild
		pagesProcMu.Unlock()
		return cmd, nil
	}
	if pagesEverRan {
		pagesProcMu.Unlock()
		return nil, fmt.Errorf("pages UI already exited")
	}
	pagesProcMu.Unlock()

	args := []string{"--pages"}
	if noAdmin {
		args = append(args, "--no-admin")
	}
	cmd := exec.Command(exe, args...)
	cmd.Dir = filepath.Dir(exe)
	cmd.Env = append(os.Environ(),
		"LIVEAIO_ROOT="+root,
		"LIVEAIO_SHELL_ELEVATED=1",
	)

	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("start %s --pages: %w", exe, err)
	}

	pagesProcMu.Lock()
	pagesRunning = true
	pagesEverRan = true
	pagesChild = cmd
	pagesProcMu.Unlock()
	return cmd, nil
}

// WaitPagesChild blocks until the Pages child exits, then clears running state.
func WaitPagesChild(cmd *exec.Cmd) error {
	if cmd == nil {
		return nil
	}
	err := cmd.Wait()
	pagesProcMu.Lock()
	if pagesChild == cmd {
		pagesRunning = false
		pagesChild = nil
	}
	pagesProcMu.Unlock()
	return err
}

// StopPagesChild kills the Pages child if still running.
func StopPagesChild() {
	pagesProcMu.Lock()
	cmd := pagesChild
	pagesProcMu.Unlock()
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
}

// LoginUIState reads state.json and returns (display text, login button enabled).
// Pages query via IPC (ui.command login.query); they must not read files directly.
func LoginUIState(root string) (text string, canLogin bool) {
	path := filepath.Join(root, "state.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return "未登录", true
	}
	var state map[string]any
	if err := json.Unmarshal(raw, &state); err != nil {
		return "登录状态读取失败", true
	}
	cookies, _ := state["cookies"].([]any)
	now := time.Now().Unix()
	for _, c := range cookies {
		m, ok := c.(map[string]any)
		if !ok {
			continue
		}
		name, _ := m["name"].(string)
		if name != "sessionid" {
			continue
		}
		val, _ := m["value"].(string)
		if val == "" {
			return "未找到登录凭证", true
		}
		exp := int64(-1)
		switch v := m["expires"].(type) {
		case float64:
			exp = int64(v)
		case int64:
			exp = v
		case int:
			exp = int64(v)
		}
		if exp > 0 && exp < now {
			return "登录已过期", true
		}
		if exp <= 0 {
			return "已登录", false
		}
		days := (exp - now) / 86400
		if days < 0 {
			days = 0
		}
		return fmt.Sprintf("已登录，还剩约 %d 天", days), false
	}
	return "未找到登录凭证", true
}

// DefaultRouteEnv is a UI-safe env snapshot when listener helpers are unavailable.
func DefaultRouteEnv(route string) map[string]any {
	return map[string]any{
		"route":   route,
		"ok":      true,
		"ready":   true,
		"message": "",
		"details": map[string]any{},
	}
}
