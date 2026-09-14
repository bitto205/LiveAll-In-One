package listener

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestApplyPatchAnchorsSpawnToProxySwitch(t *testing.T) {
	// Minified shape: earlier ";" lives inside an unrelated factory; proxy inject
	// is later near app.on("ready") with no semicolon immediately before it.
	src := `const o=g.getLogger();return{info:(...e)=>o.info(e)};` +
		`b(),N(),i.on("ready",(async()=>{A()}));`
	dest := `D:\app\resources\app\proxy_shell.exe`
	out, cat, errMsg := applyPatch(src, dest, `D:\app\resources\app\index.js`)
	if errMsg != "" {
		t.Fatalf("applyPatch: %s", errMsg)
	}
	if cat.ProxyMode != "inject" {
		t.Fatalf("mode=%s", cat.ProxyMode)
	}
	if !strings.Contains(out, cat.ProxyInject) {
		t.Fatal("missing proxy inject")
	}
	if !spawnAnchoredToProxy(out, cat.Spawn, cat.ProxyInject) {
		t.Fatalf("spawn not anchored; out snippet around spawn:\n%s", out)
	}
	// Must not leave spawn inside the early logger factory (before b(),N()).
	early := out
	if i := strings.Index(out, `b(),N()`); i >= 0 {
		early = out[:i]
	}
	if strings.Contains(early, "child_process") {
		t.Fatalf("spawn still inside early factory:\n%s", early)
	}
	// Expected order: ...N(),<proxyInject><spawn>i.on("ready"...
	pi := strings.Index(out, cat.ProxyInject)
	si := strings.Index(out, cat.Spawn)
	if pi < 0 || si < 0 || si < pi {
		t.Fatalf("spawn should follow proxy inject; pi=%d si=%d", pi, si)
	}
	joined := out[pi : si+len(cat.Spawn)]
	if joined != cat.ProxyInject+cat.Spawn {
		t.Fatalf("expected inject+spawn contiguous, got %q", joined)
	}
	// Broken form that crashed companion boot: comma then leading-semicolon IIFE.
	if strings.Contains(out, `,;(function(){var c=require("child_process")`) {
		t.Fatal("illegal comma-then-semicolon spawn placement")
	}
	_ = filepath.Clean(dest)
}
