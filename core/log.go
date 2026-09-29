package core

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"
)

// fileFirstWriter: GUI-subsystem LiveAIO.exe has no valid stderr handle, and
// io.MultiWriter aborts on the first failing writer — the file must not depend on it.
type fileFirstWriter struct {
	file   io.Writer
	mirror io.Writer
}

func (w fileFirstWriter) Write(p []byte) (int, error) {
	n, err := w.file.Write(p)
	if w.mirror != nil {
		_, _ = w.mirror.Write(p)
	}
	return n, err
}

func openLogFile(dir, name string) (*os.File, string, error) {
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, "", err
	}
	path := filepath.Join(dir, name)
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return nil, "", err
	}
	return f, path, nil
}

func FileLogger(root, name string) *slog.Logger {
	log, _ := newFileLogger(root, name)
	return log
}

func newFileLogger(root, name string) (*slog.Logger, *os.File) {
	dirs := []string{filepath.Join(root, "log")}
	if local := os.Getenv("LOCALAPPDATA"); local != "" {
		dirs = append(dirs, filepath.Join(local, "LiveAIO", "log"))
	}
	var (
		f        *os.File
		path     string
		firstErr error
	)
	for _, dir := range dirs {
		var err error
		f, path, err = openLogFile(dir, name)
		if err == nil {
			break
		}
		if firstErr == nil {
			firstErr = err
		}
	}
	if f == nil {
		return slog.New(slog.NewTextHandler(os.Stderr, nil)), nil
	}
	w := fileFirstWriter{file: f, mirror: os.Stderr}
	log := slog.New(slog.NewTextHandler(w, &slog.HandlerOptions{Level: slog.LevelInfo}))
	if firstErr != nil {
		log.Warn("log dir fallback", "path", path, "err", firstErr)
	} else {
		log.Info("log file", "path", path)
	}
	return log, f
}
