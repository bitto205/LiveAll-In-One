package core

import (
	"os"
	"path/filepath"
	"testing"
)

func TestIsPackagedRootRequiresResources(t *testing.T) {
	dir := t.TempDir()
	_ = os.WriteFile(filepath.Join(dir, "LiveAIOCore.dll"), []byte("x"), 0o644)
	if isPackagedRoot(dir) {
		t.Fatal("Core alone must not be packaged")
	}
	_ = os.Mkdir(filepath.Join(dir, "browsers"), 0o755)
	if isPackagedRoot(dir) {
		t.Fatal("Core+browsers without resources must not be packaged")
	}
	_ = os.Mkdir(filepath.Join(dir, "resources"), 0o755)
	if !isPackagedRoot(dir) {
		t.Fatal("Core+resources should be packaged")
	}
}

func TestArtifactCandidatesOrder(t *testing.T) {
	root := t.TempDir()
	custom := filepath.Join(root, "build", "build_work", "custom")
	_ = os.MkdirAll(custom, 0o755)
	dll := filepath.Join(custom, "LiveAIOPages.dll")
	_ = os.WriteFile(dll, []byte("x"), 0o644)

	got, err := FindArtifact(root, "LiveAIOPages.dll")
	if err != nil {
		t.Fatal(err)
	}
	if got != dll {
		t.Fatalf("want %s got %s", dll, got)
	}

	flat := filepath.Join(root, "LiveAIOPages.dll")
	_ = os.WriteFile(flat, []byte("y"), 0o644)
	got, err = FindArtifact(root, "LiveAIOPages.dll")
	if err != nil {
		t.Fatal(err)
	}
	if got != flat {
		t.Fatalf("flat package should win: want %s got %s", flat, got)
	}
}
