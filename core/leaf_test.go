package core

import "testing"

func TestLeafSimFallbackAndMatch(t *testing.T) {
	var gotGift string
	var gotDelta int
	var gotAction string
	e := NewLeaf(func(gift string, leaves int, user string, action string) {
		gotGift, gotDelta, gotAction = gift, leaves, action
	})
	e.SetActive(true)
	e.SetSettings(LeafSettings{Rules: []LeafRule{
		{Gift: "人气票", Mode: "add", Value: 2},
	}})

	e.HandleGift("u", "1", "人气票", 3)
	if gotDelta != 6 || gotGift != "人气票" || gotAction != "" {
		t.Fatalf("match: gift=%q delta=%d action=%q", gotGift, gotDelta, gotAction)
	}

	gotDelta = 0
	e.HandleGift("u", "1", "小心心", 1)
	if gotDelta != 0 {
		t.Fatalf("live miss should be no-op, got %d", gotDelta)
	}

	e.HandleSimGift("u", "1", "小心心", 4)
	if gotDelta != 4 {
		t.Fatalf("sim fallback: got %d", gotDelta)
	}
}

func TestLeafClearModes(t *testing.T) {
	var gotAction string
	var gotDelta int
	e := NewLeaf(func(gift string, leaves int, user string, action string) {
		gotDelta, gotAction = leaves, action
	})
	e.SetActive(true)
	e.SetSettings(LeafSettings{Rules: []LeafRule{
		{Gift: "清空礼", Mode: "clear"},
		{Gift: "清屏礼", Mode: "clear_screen"},
	}})

	e.HandleGift("u", "1", "清空礼", 5)
	if gotAction != "clear" || gotDelta != 0 {
		t.Fatalf("clear: action=%q delta=%d", gotAction, gotDelta)
	}
	e.HandleGift("u", "1", "清屏礼", 2)
	if gotAction != "clear_screen" || gotDelta != 0 {
		t.Fatalf("clear_screen: action=%q delta=%d", gotAction, gotDelta)
	}
}

func TestLeafNamedRuleCount(t *testing.T) {
	e := NewLeaf(nil)
	e.SetSettings(LeafSettings{Rules: []LeafRule{
		{Gift: "人气票"},
		{Gift: ""},
		{Gift: "  "},
	}})
	if e.NamedRuleCount() != 1 {
		t.Fatalf("NamedRuleCount=%d", e.NamedRuleCount())
	}
}
