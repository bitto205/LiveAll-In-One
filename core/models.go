package core

import (
	"fmt"
	"strings"
)

// Envelope is one JSONL object.
type Envelope map[string]any

func (e Envelope) Op() string {
	if e == nil {
		return ""
	}
	v, _ := e["op"].(string)
	return v
}

type Rule struct {
	Gift   string `json:"gift"`
	Mode   string `json:"mode"` // add | sub | random
	Value  int    `json:"value"`
	Unit   string `json:"unit"` // s | m | h
	MinVal int    `json:"min"`
	MaxVal int    `json:"max"`
}

type OvertimeSettings struct {
	Hours   int    `json:"hours"`
	Minutes int    `json:"minutes"`
	Seconds int    `json:"seconds"`
	Rules   []Rule `json:"rules"`
}

type LedgerEntry struct {
	User    string `json:"user"`
	UserID  string `json:"user_id"`
	Seconds int    `json:"seconds"`
}

type DanmuSettings struct {
	ChatOn          bool `json:"danmu_chat_on"`
	GiftOn          bool `json:"danmu_gift_on"`
	GiftMinDiamonds int  `json:"danmu_gift_min_diamonds"`
	FollowOn        bool `json:"danmu_follow_on"`
	LikeOn          bool `json:"danmu_like_on"`
	LikeThreshold   int  `json:"danmu_like_threshold"`
	LikeAccumulate  bool `json:"danmu_like_accumulate"`
}

type MemoSettings struct {
	GiftEnabled     bool `json:"memo.gift.enabled"`
	GiftStack       bool `json:"memo.gift.stack"`
	GiftMinDiamonds int  `json:"memo.gift.min_diamonds"`
	FollowEnabled   bool `json:"memo.follow.enabled"`
	LikeEnabled     bool `json:"memo.like.enabled"`
	LikeStack       bool `json:"memo.like.stack"`
}

type RouteStatus struct {
	Route       string `json:"route,omitempty"`
	Connected   bool   `json:"connected"`
	LiveID      string `json:"live_id,omitempty"`
	Driver      string `json:"driver,omitempty"`
	Health      string `json:"health,omitempty"`
	ForceSystem bool   `json:"force_system,omitempty"`
}

type Capabilities struct {
	ProtocolVersion string   `json:"protocol_version"`
	UIOwner         string   `json:"ui_owner"`
	ConfigOwner     string   `json:"config_owner"`
	ListenerOwner   string   `json:"listener_owner"`
	SupportsRoutes  []string `json:"supports_routes"`
	ToolEvents      []string `json:"tool_events"`
	ToolCommands    []string `json:"tool_commands"`
}

func DefaultCapabilities() Capabilities {
	return Capabilities{
		ProtocolVersion: ProtocolVersion,
		UIOwner:         "go_host",
		ConfigOwner:     "go_core",
		ListenerOwner:   "listener_boundary",
		SupportsRoutes:  []string{"1", "2", "3", "4"},
		ToolEvents:      []string{OpTick, OpLedger, OpDanmuShow, OpMemoItem, OpLeafSpawn},
		ToolCommands: []string{
			OpToolOvertimeSet,
			OpToolOvertimeCmd,
			OpToolOvertimeSim,
			OpToolLeafSet,
			OpToolLeafSim,
			OpToolDanmuSet,
			OpToolMemoSet,
			OpUICommand,
			OpConfigSet,
			OpConfigGet,
		},
	}
}

func NormalizeLeafSettings(raw any) LeafSettings {
	m, ok := raw.(map[string]any)
	if !ok {
		if mm, ok2 := raw.(map[string]interface{}); ok2 {
			m = map[string]any(mm)
			ok = true
		}
	}
	if !ok || m == nil {
		return LeafSettings{}
	}
	out := LeafSettings{}
	rules, _ := m["rules"].([]any)
	if rules == nil {
		if rr, ok := m["rules"].([]interface{}); ok {
			rules = append(rules, rr...)
		}
	}
	for _, item := range rules {
		ruleMap, ok := item.(map[string]any)
		if !ok {
			if mm, ok := item.(map[string]interface{}); ok {
				ruleMap = map[string]any(mm)
			}
		}
		if ruleMap == nil {
			continue
		}
		out.Rules = append(out.Rules, normalizeLeafRule(ruleMap))
		if len(out.Rules) >= 10 {
			break
		}
	}
	return out
}

func normalizeLeafRule(m map[string]any) LeafRule {
	mode := normalizeMode(mapString(m, "mode", "add"))
	rule := LeafRule{
		Gift:  strings.TrimSpace(mapString(m, "gift", "")),
		Mode:  mode,
		Value: intValue(m["value"], 1),
	}
	if rule.Value < 0 {
		rule.Value = 0
	}
	if rule.Mode == "random" {
		rule.MinVal = intValue(m["min"], intValue(m["random_min"], 1))
		rule.MaxVal = intValue(m["max"], intValue(m["random_max"], rule.MinVal+1))
		if rule.MaxVal <= rule.MinVal {
			rule.MaxVal = rule.MinVal + 1
		}
	}
	return rule
}

func NormalizeOvertimeSettings(raw any) OvertimeSettings {
	m, ok := raw.(map[string]any)
	if !ok {
		if mm, ok := raw.(map[string]interface{}); ok {
			m = map[string]any(mm)
		}
	}
	if !ok || m == nil {
		return OvertimeSettings{}
	}
	out := OvertimeSettings{
		Hours:   intValue(m["hours"], 0),
		Minutes: intValue(m["minutes"], 0),
		Seconds: intValue(m["seconds"], 0),
	}
	rules, _ := m["rules"].([]any)
	if rules == nil {
		if rr, ok := m["rules"].([]interface{}); ok {
			rules = append(rules, rr...)
		}
	}
	for _, item := range rules {
		ruleMap, ok := item.(map[string]any)
		if !ok {
			if mm, ok := item.(map[string]interface{}); ok {
				ruleMap = map[string]any(mm)
			}
		}
		if ruleMap == nil {
			continue
		}
		out.Rules = append(out.Rules, normalizeRule(ruleMap))
	}
	return out
}

func NormalizeDanmuSettings(raw any) DanmuSettings {
	m, ok := raw.(map[string]any)
	if !ok {
		if mm, ok := raw.(map[string]interface{}); ok {
			m = map[string]any(mm)
		}
	}
	if !ok || m == nil {
		return DanmuSettings{}
	}
	return DanmuSettings{
		ChatOn:          boolValue(m["danmu_chat_on"], true),
		GiftOn:          boolValue(m["danmu_gift_on"], true),
		GiftMinDiamonds: intValue(m["danmu_gift_min_diamonds"], 0),
		FollowOn:        boolValue(m["danmu_follow_on"], true),
		LikeOn:          boolValue(m["danmu_like_on"], true),
		LikeThreshold:   intValue(m["danmu_like_threshold"], 1),
		LikeAccumulate:  boolValue(m["danmu_like_accumulate"], false),
	}
}

func NormalizeMemoSettings(raw any) MemoSettings {
	m, ok := raw.(map[string]any)
	if !ok {
		if mm, ok := raw.(map[string]interface{}); ok {
			m = map[string]any(mm)
		}
	}
	if !ok || m == nil {
		return MemoSettings{}
	}
	return MemoSettings{
		GiftEnabled:     boolValue(m["memo.gift.enabled"], true),
		GiftStack:       boolValue(m["memo.gift.stack"], true),
		GiftMinDiamonds: intValue(m["memo.gift.min_diamonds"], 0),
		FollowEnabled:   boolValue(m["memo.follow.enabled"], true),
		LikeEnabled:     boolValue(m["memo.like.enabled"], true),
		LikeStack:       boolValue(m["memo.like.stack"], true),
	}
}

func normalizeRule(m map[string]any) Rule {
	mode := mapString(m, "mode", "add")
	unit := mapString(m, "unit", "m")
	rule := Rule{
		Gift:  mapString(m, "gift", ""),
		Mode:  normalizeMode(mode),
		Unit:  normalizeUnit(unit),
		Value: intValue(m["value"], 0),
	}
	if rule.Mode == "random" {
		rule.Unit = normalizeUnit(mapString(m, "random_unit", unit))
		rule.MinVal = intValue(m["min"], intValue(m["random_neg"], 0))
		rule.MaxVal = intValue(m["max"], intValue(m["random_pos"], 0))
		if _, ok := m["min"]; !ok && rule.MinVal > 0 {
			rule.MinVal = -rule.MinVal
		}
	} else {
		rule.MinVal = intValue(m["min"], 0)
		rule.MaxVal = intValue(m["max"], 0)
	}
	return rule
}

func normalizeMode(v string) string {
	switch v {
	case "加", "add", "+", "":
		return "add"
	case "减", "sub", "subtract", "-":
		return "sub"
	case "随机", "random", "rand":
		return "random"
	case "清空", "clear":
		return "clear"
	case "清屏", "clear_screen", "clearscreen":
		return "clear_screen"
	default:
		return "add"
	}
}

func normalizeUnit(v string) string {
	switch v {
	case "时", "h", "hour", "hours":
		return "h"
	case "秒", "s", "sec", "second", "seconds":
		return "s"
	case "分", "m", "min", "minute", "minutes", "":
		return "m"
	default:
		return "m"
	}
}

func mapString(m map[string]any, key, fallback string) string {
	v, ok := m[key]
	if !ok || v == nil {
		return fallback
	}
	return fmt.Sprint(v)
}

func intValue(v any, fallback int) int {
	switch n := v.(type) {
	case int:
		return n
	case int32:
		return int(n)
	case int64:
		return int(n)
	case float64:
		return int(n)
	case float32:
		return int(n)
	case string:
		var out int
		_, err := fmt.Sscanf(n, "%d", &out)
		if err == nil {
			return out
		}
	}
	return fallback
}

func boolValue(v any, fallback bool) bool {
	switch b := v.(type) {
	case bool:
		return b
	case string:
		if b == "true" || b == "1" {
			return true
		}
		if b == "false" || b == "0" {
			return false
		}
	case float64:
		return b != 0
	case int:
		return b != 0
	case int64:
		return b != 0
	}
	return fallback
}
