package listener

import (
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

// Production-path reconnect: Manager Start → wait enter/wss → Stop → Start again.
//
//	go test ./listener -run TestManagerReconnectTwice -v -count=1 -timeout 180s
func TestManagerReconnectTwice(t *testing.T) {
	root := probeRoot(t)
	liveID := os.Getenv("LIVEAIO_PROBE_LIVE_ID")
	if liveID == "" {
		liveID = "68708188725"
	}
	if _, err := os.Stat(filepath.Join(root, "state.json")); err != nil {
		t.Skip("no state.json")
	}
	if findBundledExe(root) == "" {
		t.Skip("no bundled chrome")
	}

	var mu sync.Mutex
	var lastStatus *bool
	var lastErr error
	logs := make([]string, 0, 64)

	m := NewCapture(root, "", func(msg string, kv ...any) {
		line := msg
		for i := 0; i+1 < len(kv); i += 2 {
			line += " " + toStr(kv[i]) + "=" + toStr(kv[i+1])
		}
		mu.Lock()
		logs = append(logs, line)
		mu.Unlock()
		t.Log(line)
	})
	m.OnStatus = func(connected bool) {
		mu.Lock()
		v := connected
		lastStatus = &v
		mu.Unlock()
		t.Logf("OnStatus connected=%v", connected)
	}
	m.OnError = func(err error) {
		mu.Lock()
		lastErr = err
		mu.Unlock()
		t.Logf("OnError: %v", err)
	}

	waitOK := func(timeout time.Duration) bool {
		deadline := time.Now().Add(timeout)
		for time.Now().Before(deadline) {
			mu.Lock()
			ok := lastStatus != nil && *lastStatus
			mu.Unlock()
			if ok {
				return true
			}
			time.Sleep(200 * time.Millisecond)
		}
		return false
	}

	for attempt := 1; attempt <= 2; attempt++ {
		mu.Lock()
		lastStatus = nil
		lastErr = nil
		mu.Unlock()
		t.Logf("=== Manager attempt %d ===", attempt)
		if err := m.Start("1", liveID, false); err != nil {
			t.Fatalf("Start: %v", err)
		}
		ok := waitOK(45 * time.Second)
		t.Logf("attempt %d ok=%v err=%v", attempt, ok, lastErr)
		m.Stop()
		ReapOwnedBrowsers(root)
		time.Sleep(1500 * time.Millisecond)
		if !ok {
			t.Fatalf("attempt %d failed to connect", attempt)
		}
	}
}

func toStr(v any) string { return fmt.Sprint(v) }
