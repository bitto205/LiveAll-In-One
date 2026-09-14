package core

import (
	"math/rand"
	"strings"
	"sync"
)

// LeafRule maps one gift to a leaf-count delta or clear action.
type LeafRule struct {
	Gift   string `json:"gift"`
	Mode   string `json:"mode"` // add | sub | random | clear | clear_screen
	Value  int    `json:"value"`
	MinVal int    `json:"min"`
	MaxVal int    `json:"max"`
}

type LeafSettings struct {
	Rules []LeafRule `json:"rules"`
}

// onSpawn action: "" = numeric delta in leaves; "clear" / "clear_screen" ignore leaves.
type LeafEngine struct {
	mu       sync.Mutex
	active   bool
	settings LeafSettings
	onSpawn  func(gift string, leaves int, user string, action string)
}

func NewLeaf(onSpawn func(gift string, leaves int, user string, action string)) *LeafEngine {
	return &LeafEngine{onSpawn: onSpawn}
}

func (e *LeafEngine) Active() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.active
}

func (e *LeafEngine) SetActive(v bool) {
	e.mu.Lock()
	e.active = v
	e.mu.Unlock()
}

func (e *LeafEngine) SetSettings(s LeafSettings) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.settings = s
}

// NamedRuleCount is rules with a non-empty gift name (Null/blank rows ignored).
func (e *LeafEngine) NamedRuleCount() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	n := 0
	for _, r := range e.settings.Rules {
		if strings.TrimSpace(r.Gift) != "" {
			n++
		}
	}
	return n
}

func ruleToLeaves(r LeafRule, count int) int {
	if count < 1 {
		count = 1
	}
	switch strings.ToLower(r.Mode) {
	case "sub", "subtract", "-":
		return -r.Value * count
	case "random", "rand":
		lo, hi := r.MinVal, r.MaxVal
		if hi < lo {
			lo, hi = hi, lo
		}
		if hi <= lo {
			hi = lo + 1
		}
		n := lo + rand.Intn(hi-lo+1)
		return n * count
	default: // add
		return r.Value * count
	}
}

func (e *LeafEngine) HandleGift(user, userID, gift string, count int) {
	e.handleGift(user, userID, gift, count, false)
}

// HandleSimGift runs the same rule table as live gifts, but if nothing matches it
// still spawns `count` leaves so the settings "模拟送礼" button is never a no-op
// when rules are empty / gift names differ.
func (e *LeafEngine) HandleSimGift(user, userID, gift string, count int) {
	e.handleGift(user, userID, gift, count, true)
}

func (e *LeafEngine) handleGift(user, userID, gift string, count int, simFallback bool) {
	e.mu.Lock()
	if !e.active {
		e.mu.Unlock()
		return
	}
	gift = strings.TrimSpace(gift)
	var hit *LeafRule
	for i := range e.settings.Rules {
		r := &e.settings.Rules[i]
		if r.Gift == "" {
			continue
		}
		if strings.EqualFold(strings.TrimSpace(r.Gift), gift) {
			hit = r
			break
		}
	}
	var delta int
	action := ""
	if hit != nil {
		switch strings.ToLower(hit.Mode) {
		case "clear":
			action = "clear"
		case "clear_screen":
			action = "clear_screen"
		default:
			delta = ruleToLeaves(*hit, count)
		}
	} else if simFallback {
		if count < 1 {
			count = 1
		}
		delta = count
	}
	cb := e.onSpawn
	e.mu.Unlock()
	if cb == nil {
		return
	}
	if action == "" && delta == 0 {
		return
	}
	if user == "" {
		user = userID
	}
	cb(gift, delta, user, action)
}
