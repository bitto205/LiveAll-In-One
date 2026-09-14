package listener

import (
	"os"
	"path/filepath"
	"testing"
)

func TestBundledProxyShellPresent(t *testing.T) {
	root, err := filepath.Abs("..")
	if err != nil {
		t.Fatal(err)
	}
	src := bundledShell(root)
	st, err := os.Stat(src)
	if err != nil {
		t.Fatalf("missing bundled proxy_shell.exe at %s: %v", src, err)
	}
	if st.Size() < 1024 {
		t.Fatalf("proxy_shell.exe too small: %d", st.Size())
	}
}

func TestPatchCompanionReportsBundledMissingClearly(t *testing.T) {
	root := t.TempDir()
	ok, msg := PatchCompanion(root)
	if ok {
		t.Fatal("expected failure without companion")
	}
	// Without companion install dir, message is about companion — not shell.
	if msg == "" {
		t.Fatal("empty message")
	}
}
