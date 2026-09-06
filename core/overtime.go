package core

import (
	"math/rand"
	"strings"
	"sync"
	"time"
)

type Engine struct {
	mu        sync.Mutex
	active    bool
	tickerOn  bool
	remaining int
	running   bool
	settings  OvertimeSettings
	ledger    map[string]int // user_id -> seconds
	stopTick  chan struct{}
	tickWG    sync.WaitGroup
	onTick    func(remaining int, running bool)
	onLedger  func(entries []LedgerEntry)
}

func NewOvertime(onTick func(int, bool), onLedger func([]LedgerEntry)) *Engine {
	return &Engine{
		ledger:   map[string]int{},
		onTick:   onTick,
		onLedger: onLedger,
		stopTick: make(chan struct{}),
	}
}

func (e *Engine) Active() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.active
}

func (e *Engine) SetActive(v bool) {
	if v {
		e.mu.Lock()
		e.active = true
		e.mu.Unlock()
		e.ensureTicker()
		return
	}
	e.mu.Lock()
	e.active = false
	e.mu.Unlock()
	e.stopTicker()
	done := make(chan struct{})
	go func() {
		e.tickWG.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
	}
}

func unitToSeconds(v int, unit string) int {
	switch strings.ToLower(unit) {
	case "h", "hour", "hours":
		return v * 3600
	case "m", "min", "minute", "minutes":
		return v * 60
	default:
		return v
	}
}

func ruleToSeconds(r Rule, count int) int {
	if count < 1 {
		count = 1
	}
	switch strings.ToLower(r.Mode) {
	case "sub", "subtract", "-":
		return -unitToSeconds(r.Value, r.Unit) * count
	case "random", "rand":
		lo, hi := r.MinVal, r.MaxVal
		if hi < lo {
			lo, hi = hi, lo
		}
		if hi == lo {
			return unitToSeconds(lo, r.Unit) * count
		}
		n := lo + rand.Intn(hi-lo+1)
		return unitToSeconds(n, r.Unit) * count
	default: // add
		return unitToSeconds(r.Value, r.Unit) * count
	}
}

func (e *Engine) SetSettings(s OvertimeSettings) {
	e.mu.Lock()
	e.settings = s
	total := s.Hours*3600 + s.Minutes*60 + s.Seconds
	if total > 0 {
		e.remaining = total
	}
	rem, run := e.remaining, e.running
	cb := e.onTick
	e.mu.Unlock()
	if cb != nil {
		cb(rem, run)
	}
}

func (e *Engine) Cmd(cmd string) {
	e.mu.Lock()
	switch cmd {
	case "pause":
		e.running = false
	case "resume", "start":
		e.running = true
	case "reset":
		s := e.settings
		e.remaining = s.Hours*3600 + s.Minutes*60 + s.Seconds
		e.running = true
	case "clear_ledger":
		e.ledger = map[string]int{}
		entries := e.copyLedgerLocked()
		cbL := e.onLedger
		rem, run := e.remaining, e.running
		cbT := e.onTick
		e.mu.Unlock()
		if cbL != nil {
			cbL(entries)
		}
		if cbT != nil {
			cbT(rem, run)
		}
		return
	}
	rem, run := e.remaining, e.running
	cb := e.onTick
	e.mu.Unlock()
	if cb != nil {
		cb(rem, run)
	}
}

func (e *Engine) ensureTicker() {
	e.mu.Lock()
	if e.tickerOn {
		e.mu.Unlock()
		return
	}
	select {
	case <-e.stopTick:
		e.stopTick = make(chan struct{})
	default:
	}
	e.tickerOn = true
	stopCh := e.stopTick
	e.mu.Unlock()
	e.tickWG.Add(1)
	go func() {
		defer e.tickWG.Done()
		defer func() {
			e.mu.Lock()
			e.tickerOn = false
			e.mu.Unlock()
		}()
		t := time.NewTicker(time.Second)
		defer t.Stop()
		for {
			select {
			case <-stopCh:
				return
			case <-t.C:
				e.mu.Lock()
				if !e.active || !e.running || e.remaining <= 0 {
					e.mu.Unlock()
					continue
				}
				e.remaining--
				rem, run := e.remaining, e.running
				cb := e.onTick
				e.mu.Unlock()
				if cb != nil {
					cb(rem, run)
				}
			}
		}
	}()
}

// StartTicker keeps the old name for callers; prefer SetActive(true).
func (e *Engine) StartTicker() { e.ensureTicker() }

func (e *Engine) stopTicker() {
	e.mu.Lock()
	select {
	case <-e.stopTick:
	default:
		close(e.stopTick)
	}
	e.mu.Unlock()
}

func (e *Engine) Stop() {
	e.SetActive(false)
	e.stopTicker()
	done := make(chan struct{})
	go func() {
		e.tickWG.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
	}
}

func (e *Engine) HandleGift(user, userID, gift string, count int) {
	e.mu.Lock()
	if !e.active {
		e.mu.Unlock()
		return
	}
	var hit *Rule
	for i := range e.settings.Rules {
		r := &e.settings.Rules[i]
		if r.Gift != "" && r.Gift == gift {
			hit = r
			break
		}
	}
	if hit == nil {
		e.mu.Unlock()
		return
	}
	delta := ruleToSeconds(*hit, count)
	e.remaining += delta
	if e.remaining < 0 {
		e.remaining = 0
	}
	if userID == "" {
		userID = user
	}
	e.ledger[userID] += delta
	e.running = true
	rem, run := e.remaining, e.running
	entries := e.copyLedgerLocked()
	cbT, cbL := e.onTick, e.onLedger
	e.mu.Unlock()
	if cbT != nil {
		cbT(rem, run)
	}
	if cbL != nil {
		cbL(entries)
	}
}

func (e *Engine) copyLedgerLocked() []LedgerEntry {
	out := make([]LedgerEntry, 0, len(e.ledger))
	for uid, sec := range e.ledger {
		out = append(out, LedgerEntry{User: uid, UserID: uid, Seconds: sec})
	}
	return out
}
