package listener

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/chromedp"
)

// Manual reconnect diagnosis (needs state.json + network + live room):
//
//	go test ./listener -run TestReconnectCaptureTwice -v -count=1 -timeout 120s
//
// Optional: LIVEAIO_PROBE_LIVE_ID=xxxxx
func TestReconnectCaptureTwice(t *testing.T) {
	root := probeRoot(t)
	liveID := os.Getenv("LIVEAIO_PROBE_LIVE_ID")
	if liveID == "" {
		liveID = "68708188725"
	}
	if _, err := os.Stat(filepath.Join(root, "state.json")); err != nil {
		t.Skip("no state.json under", root)
	}
	if findBundledExe(root) == "" {
		t.Skip("no bundled chrome-headless-shell")
	}

	modes := []struct {
		name string
		trim bool
	}{
		{"trim", true},
		{"no_trim", false},
	}
	for _, mode := range modes {
		t.Run(mode.name, func(t *testing.T) {
			for i := 1; i <= 2; i++ {
				t.Logf("=== attempt %d trim=%v ===", i, mode.trim)
				title, href, body, enters, wss, err := probeOnce(t, root, liveID, mode.trim, 25*time.Second)
				t.Logf("title=%q href=%q enter_reqs=%d wss=%v body_snip=%q err=%v",
					title, href, enters, wss, snip(body, 120), err)
				ReapOwnedBrowsers(root)
				time.Sleep(1500 * time.Millisecond)
				if i == 1 && enters == 0 && !wss {
					t.Fatalf("first attempt failed")
				}
				if i == 2 && enters == 0 && !wss {
					t.Fatalf("second attempt failed: title=%q href=%q snip=%q", title, href, snip(body, 200))
				}
			}
		})
	}
}

func snip(s string, n int) string {
	s = strings.Join(strings.Fields(s), " ")
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

func probeRoot(t *testing.T) string {
	t.Helper()
	if r := os.Getenv("LIVEAIO_ROOT"); r != "" {
		return r
	}
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	return filepath.Clean(filepath.Join(wd, ".."))
}

func probeOnce(t *testing.T, root, liveID string, trim bool, wait time.Duration) (title, href, body string, enters int, wss bool, err error) {
	t.Helper()
	sess, err := launchBrowser(context.Background(), BrowserOptions{
		Root: root, PreferBundled: true, RequireState: true,
		Headless: true, TrimResources: trim,
	})
	if err != nil {
		return "", "", "", 0, false, err
	}
	defer func() {
		sess.leaveLiveBestEffort(func(string, ...any) {}, root)
		sess.Close()
	}()

	var mu sync.Mutex
	chromedp.ListenTarget(sess.Ctx, func(ev any) {
		switch e := ev.(type) {
		case *network.EventResponseReceived:
			if e.Response != nil && isRoomEnterURL(e.Response.URL) {
				mu.Lock()
				enters++
				mu.Unlock()
				t.Logf("enter url: %s", e.Response.URL)
			}
		case *network.EventWebSocketCreated:
			if strings.Contains(e.URL, "/push/v2/") {
				mu.Lock()
				wss = true
				mu.Unlock()
				t.Logf("wss ok")
			}
		}
	})

	url := fmt.Sprintf("https://live.douyin.com/%s", liveID)
	navCtx, cancel := context.WithTimeout(sess.Ctx, 20*time.Second)
	navErr := chromedp.Run(navCtx, chromedp.ActionFunc(func(ctx context.Context) error {
		_, _, errText, _, e := page.Navigate(url).Do(ctx)
		if e != nil {
			return e
		}
		if errText != "" {
			return fmt.Errorf("%s", errText)
		}
		return nil
	}))
	cancel()
	if navErr != nil {
		t.Logf("navigate: %v", navErr)
	}

	deadline := time.Now().Add(wait)
	for time.Now().Before(deadline) {
		mu.Lock()
		e, w := enters, wss
		mu.Unlock()
		if e > 0 || w {
			break
		}
		time.Sleep(400 * time.Millisecond)
	}

	_ = chromedp.Run(sess.Ctx,
		chromedp.Evaluate(`document.title||''`, &title),
		chromedp.Evaluate(`location.href||''`, &href),
		chromedp.Evaluate(`(document.body && document.body.innerText || '').slice(0, 500)`, &body),
	)
	mu.Lock()
	defer mu.Unlock()
	return title, href, body, enters, wss, nil
}
