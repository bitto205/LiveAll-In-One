// Package resources owns on-disk assets under resources/.
// Gift catalog (price/id) is read here for Go business (danmu/memo/overtime filters).
package resources

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
)

type Gift struct {
	Price  int `json:"price"`
	GiftID int `json:"gift_id"`
}

var (
	giftMu  sync.RWMutex
	catalog map[string]Gift
)

var iconExts = []string{".webp", ".png", ".jpg", ".jpeg", ".gif"}

func GiftDir(root string) string {
	if root == "" {
		root = "."
	}
	return filepath.Join(root, "resources", "gift")
}

// LoadGifts reads resources/gift/gift_info.json. Safe to call again.
func LoadGifts(root string) error {
	path := filepath.Join(GiftDir(root), "gift_info.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("gift catalog: %w", err)
	}
	var next map[string]Gift
	if err := json.Unmarshal(raw, &next); err != nil {
		return fmt.Errorf("gift catalog json: %w", err)
	}
	giftMu.Lock()
	catalog = next
	giftMu.Unlock()
	return nil
}

// ClearCatalog drops the in-memory gift price map (idle → 0%).
func ClearCatalog() {
	giftMu.Lock()
	catalog = nil
	giftMu.Unlock()
}

func CatalogLoaded() bool {
	giftMu.RLock()
	defer giftMu.RUnlock()
	return catalog != nil
}

func lookup(name string) (Gift, bool) {
	giftMu.RLock()
	defer giftMu.RUnlock()
	if catalog == nil {
		return Gift{}, false
	}
	g, ok := catalog[name]
	return g, ok
}

func Diamonds(name string) int {
	g, ok := lookup(name)
	if !ok {
		return 0
	}
	return g.Price
}

func GiftID(name string) int {
	g, ok := lookup(name)
	if !ok {
		return 0
	}
	return g.GiftID
}

func Names() []string {
	giftMu.RLock()
	defer giftMu.RUnlock()
	out := make([]string, 0, len(catalog))
	for k := range catalog {
		out = append(out, k)
	}
	return out
}

// IconPath resolves resources/gift/icon/<name|id>.{webp,png,...}.
func IconPath(root, name string) string {
	dir := filepath.Join(GiftDir(root), "icon")
	for _, ext := range iconExts {
		p := filepath.Join(dir, name+ext)
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p
		}
	}
	if id := GiftID(name); id != 0 {
		idName := fmt.Sprintf("%d", id)
		for _, ext := range iconExts {
			p := filepath.Join(dir, idName+ext)
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				return p
			}
		}
	}
	return ""
}
