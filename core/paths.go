package core

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Layouts (do not conflate):
//
//  1. Repo / F5: LIVEAIO_ROOT = repo root. Data at {root}/resources, {root}/browsers.
//     Binaries under {root}/build/build_work/custom/ (or <version>/).
//  2. Packaged flat: exe dir has LiveAIOCore.dll + resources/ (custom/, version/, NSIS).
//     Binaries, Qt plugins, resources, optional browsers all beside the exe.
//  3. Bare cmake custom/ with Core+browsers but NO resources is NOT packaged —
//     walk up to the repo so gifts/skins still resolve.
//
// Prefer LIVEAIO_ROOT / --root when set.

// ResolveAppRoot finds the LiveAIO app root from the running executable.
func ResolveAppRoot(exePath string) string {
	if env := os.Getenv("LIVEAIO_ROOT"); env != "" {
		return env
	}
	if exePath == "" {
		var err error
		exePath, err = os.Executable()
		if err != nil {
			return "."
		}
	}
	dir := filepath.Dir(exePath)
	// 完整发布目录优先，避免包落在仓库 build/ 下时误爬到仓库根（browsers 旁路修复）。
	if isPackagedRoot(dir) {
		return dir
	}
	for d := dir; ; d = filepath.Dir(d) {
		if isAppRoot(d) {
			return d
		}
		parent := filepath.Dir(d)
		if parent == d {
			break
		}
	}
	return dir
}

// isPackagedRoot: Core + resources 才算完整包。仅有 browsers 不够（否则 custom/ 会
// 钉死 root，礼物/皮肤/托盘图标全部找不到）。
func isPackagedRoot(root string) bool {
	if _, err := os.Stat(filepath.Join(root, "LiveAIOCore.dll")); err != nil {
		return false
	}
	_, err := os.Stat(filepath.Join(root, "resources"))
	return err == nil
}

func isAppRoot(root string) bool {
	if _, err := os.Stat(filepath.Join(root, "go.mod")); err == nil {
		if _, err := os.Stat(filepath.Join(root, "main")); err == nil {
			if _, err := os.Stat(filepath.Join(root, "resources")); err == nil {
				return true
			}
		}
	}
	for _, probe := range []string{
		filepath.Join(root, "build", "build_work", "custom", "LiveAIOCore.dll"),
		filepath.Join(root, "build", "build_work", "custom", "LiveAIO.exe"),
		filepath.Join(root, "build", "liveaio.mod"),
	} {
		if _, err := os.Stat(probe); err == nil {
			return true
		}
	}
	return false
}

func ChdirRoot(root string) error {
	if root == "" || root == "." {
		return nil
	}
	return os.Chdir(root)
}

// ArtifactCandidates lists paths for a named file under packaged or repo roots.
// Order: flat beside root → beside exe → build_work/custom → build_work/<ver> (name desc).
func ArtifactCandidates(root, name string) []string {
	var out []string
	seen := map[string]struct{}{}
	add := func(p string) {
		if p == "" {
			return
		}
		p = filepath.Clean(p)
		if _, ok := seen[p]; ok {
			return
		}
		seen[p] = struct{}{}
		out = append(out, p)
	}

	if root != "" {
		add(filepath.Join(root, name))
	}
	if exe, err := os.Executable(); err == nil {
		add(filepath.Join(filepath.Dir(exe), name))
	}
	if root != "" {
		add(filepath.Join(root, "build", "build_work", "custom", name))
		verDir := filepath.Join(root, "build", "build_work")
		if st, err := os.Stat(verDir); err == nil && st.IsDir() {
			entries, _ := os.ReadDir(verDir)
			names := make([]string, 0, len(entries))
			for _, e := range entries {
				n := e.Name()
				if !e.IsDir() || n == "custom" || strings.HasPrefix(n, "stage_") {
					continue
				}
				names = append(names, n)
			}
			sort.Sort(sort.Reverse(sort.StringSlice(names)))
			for _, n := range names {
				add(filepath.Join(verDir, n, name))
			}
		}
	}
	return out
}

// FindArtifact returns the first existing candidate for name.
func FindArtifact(root, name string) (string, error) {
	cands := ArtifactCandidates(root, name)
	for _, p := range cands {
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p, nil
		}
	}
	return "", fmt.Errorf("%s not found (root=%s; tried %d paths)", name, root, len(cands))
}
