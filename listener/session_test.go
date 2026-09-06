package listener

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func writeState(t *testing.T, root string, cookies []map[string]any) {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"cookies": cookies})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "state.json"), raw, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestCheckSessionState(t *testing.T) {
	root := t.TempDir()
	if err := CheckSessionState(root); err == nil {
		t.Fatal("expected missing state.json")
	}

	writeState(t, root, []map[string]any{
		{"name": "sessionid", "value": "", "expires": float64(time.Now().Add(24 * time.Hour).Unix())},
	})
	if err := CheckSessionState(root); err == nil {
		t.Fatal("expected empty sessionid to fail")
	}

	writeState(t, root, []map[string]any{
		{"name": "sessionid", "value": "abc", "expires": float64(time.Now().Add(-time.Hour).Unix())},
	})
	if err := CheckSessionState(root); err == nil {
		t.Fatal("expected expired sessionid to fail")
	}

	writeState(t, root, []map[string]any{
		{"name": "sessionid_ss", "value": "tok", "expires": float64(time.Now().Add(48 * time.Hour).Unix())},
	})
	if err := CheckSessionState(root); err != nil {
		t.Fatalf("sessionid_ss should pass: %v", err)
	}

	writeState(t, root, []map[string]any{
		{"name": "sessionid", "value": "tok", "expires": float64(-1)},
	})
	if err := CheckSessionState(root); err != nil {
		t.Fatalf("session cookie should pass: %v", err)
	}
}
