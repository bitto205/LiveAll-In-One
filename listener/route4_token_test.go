package listener

import (
	"os"
	"path/filepath"
	"testing"
)

func setTempHome(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	t.Setenv("USERPROFILE", home)
	t.Setenv("HOME", home)
	return home
}

func TestReadToken_CreatesAndReuses(t *testing.T) {
	home := setTempHome(t)
	tok, err := ReadToken()
	if err != nil {
		t.Fatal(err)
	}
	if len(tok) != 48 {
		t.Fatalf("token len=%d", len(tok))
	}
	b, err := os.ReadFile(filepath.Join(home, ".liveaio", "ipc_token"))
	if err != nil {
		t.Fatal(err)
	}
	if got := string(b); got != tok+"\n" {
		t.Fatalf("file=%q token=%q", got, tok)
	}
	again, err := ReadToken()
	if err != nil || again != tok {
		t.Fatalf("reuse: %q %v", again, err)
	}
}

func TestReadToken_MatchesProxyShellDir(t *testing.T) {
	home := setTempHome(t)
	// .liveaio exists (e.g. patch catalog) while stale token sits in legacy dir:
	// proxy_shell validates against .liveaio, so the client must too.
	if err := os.MkdirAll(filepath.Join(home, ".liveaio"), 0o755); err != nil {
		t.Fatal(err)
	}
	legacy := filepath.Join(home, ".livehelper")
	if err := os.MkdirAll(legacy, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacy, "ipc_token"), []byte("old\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	tok, err := ReadToken()
	if err != nil {
		t.Fatal(err)
	}
	if tok == "old" {
		t.Fatal("used legacy token although proxy_shell reads .liveaio")
	}
	if _, err := os.Stat(filepath.Join(home, ".liveaio", "ipc_token")); err != nil {
		t.Fatal(err)
	}
}

func TestReadToken_LegacyOnly(t *testing.T) {
	home := setTempHome(t)
	legacy := filepath.Join(home, ".livehelper")
	if err := os.MkdirAll(legacy, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(legacy, "ipc_token"), []byte("legacy\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	tok, err := ReadToken()
	if err != nil || tok != "legacy" {
		t.Fatalf("got %q %v", tok, err)
	}
}
