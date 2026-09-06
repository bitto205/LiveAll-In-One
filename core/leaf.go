package core

import (
	"math/rand"
	"strings"
	"sync"
)

// LeafRule maps one gift to a leaf-count delta.
type LeafRule struct {
	Gift   string `json:"gift"`
	Mode   string `json:"mode"` // add | sub | random
	Value  int    `json:"value"`
	MinVal int    `json:"min"`
	MaxVal int    `json:"max"`
}

type LeafSettings struct {
	Rules []LeafRule `json:"rules"`
}

type LeafEngine struct {
	mu       sync.Mutex
	active   bool
	settings LeafSettings
	onSpawn  func(gift string, leaves int, user string)
}

func NewLeaf(onSpawn func(gift string, leaves int, user string)) *LeafEngine {
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
		if hi == lo {
			return lo * count
		}
		n := lo + rand.Intn(hi-lo+1)
		return n * count
	default: // add
		return r.Value * count
	}
}

func (e *LeafEngine) HandleGift(user, userID, gift string, count int) {
	e.mu.Lock()
	if !e.active {
		e.mu.Unlock()
		return
	}
	var hit *LeafRule
	for i := range e.settings.Rules {
		r := &e.settings.Rules[i]
		if r.Gift == "" {
			continue
		}
		if r.Gift == gift {
			hit = r
			break
		}
	}
	var delta int
	if hit != nil {
		delta = ruleToLeaves(*hit, count)
	}
	cb := e.onSpawn
	e.mu.Unlock()
	if hit == nil || delta == 0 || cb == nil {
		return
	}
	if user == "" {
		user = userID
	}
	cb(gift, delta, user)
}
