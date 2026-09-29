package core

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type failWriter struct{}

func (failWriter) Write([]byte) (int, error) { return 0, errors.New("invalid handle") }

func TestFileFirstWriter_MirrorFailureKeepsFile(t *testing.T) {
	var buf bytes.Buffer
	w := fileFirstWriter{file: &buf, mirror: failWriter{}}
	n, err := w.Write([]byte("hello\n"))
	if err != nil || n != 6 || buf.String() != "hello\n" {
		t.Fatalf("n=%d err=%v buf=%q", n, err, buf.String())
	}
}

func TestFileLogger_WritesUnderRoot(t *testing.T) {
	root := t.TempDir()
	log, f := newFileLogger(root, "t.log")
	log.Info("hello-root")
	_ = f.Close()
	b, err := os.ReadFile(filepath.Join(root, "log", "t.log"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), "hello-root") || !strings.Contains(string(b), "log file") {
		t.Fatalf("log=%q", b)
	}
}

func TestFileLogger_FallbackToLocalAppData(t *testing.T) {
	tmp := t.TempDir()
	// root is a regular file, so root/log cannot be created.
	root := filepath.Join(tmp, "not_a_dir")
	if err := os.WriteFile(root, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	local := filepath.Join(tmp, "local")
	t.Setenv("LOCALAPPDATA", local)
	log, f := newFileLogger(root, "t.log")
	log.Info("hello-fallback")
	_ = f.Close()
	b, err := os.ReadFile(filepath.Join(local, "LiveAIO", "log", "t.log"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(b), "hello-fallback") || !strings.Contains(string(b), "log dir fallback") {
		t.Fatalf("log=%q", b)
	}
}
