//go:build ignore

package main

import (
	"fmt"
	"os"

	"liveaio/listener"
)

// One-shot repair: re-apply companion patch so proxy_shell spawn sits next to
// the proxy-server switch (old patches could land inside logger factories).
func main() {
	root := `D:\livehelper`
	if v := os.Getenv("LIVEAIO_ROOT"); v != "" {
		root = v
	}
	fmt.Println("before IsCompanionPatched=", listener.IsCompanionPatched(root))
	ok, msg := listener.PatchCompanion(root)
	fmt.Printf("PatchCompanion ok=%v msg=%s\n", ok, msg)
	fmt.Println("after IsCompanionPatched=", listener.IsCompanionPatched(root))
}
