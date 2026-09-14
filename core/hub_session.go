package core

import (
	"time"

	"liveaio/listener"
)

// Session lifecycle for UI↔Core sync:
//
//	disarmCapture — disconnect / fail / control-end: bump gen, want=false, connected=false
//	armCapture    — new OpConnect: new gen, want=true
//	sessionAlive  — callbacks must match active gen + want
//
// Late OnStatus / frames / route4 grace from a previous chrome/shell cannot
// resurrect connected after the user cancelled or started a new session.

func (h *hub) disarmCapture() {
	h.sessionGen.Add(1)
	h.wantCapture.Store(false)
	h.sessionMu.Lock()
	h.connected = false
	h.sessionMu.Unlock()
}

func (h *hub) armCapture(route, liveID string, force bool) uint64 {
	gen := h.sessionGen.Add(1)
	h.activeGen.Store(gen)
	h.sessionMu.Lock()
	h.route = route
	h.liveID = liveID
	h.forceMode = force
	h.connected = false
	h.sessionMu.Unlock()
	h.wantCapture.Store(true)
	return gen
}

func (h *hub) sessionAlive(gen uint64) bool {
	return h.wantCapture.Load() && h.activeGen.Load() == gen
}

func (h *hub) setConnectedIfAlive(gen uint64, connected bool) bool {
	if !h.sessionAlive(gen) {
		return false
	}
	h.sessionMu.Lock()
	defer h.sessionMu.Unlock()
	if !h.wantCapture.Load() || h.activeGen.Load() != gen {
		return false
	}
	if h.connected == connected {
		return false
	}
	h.connected = connected
	return true
}

func (h *hub) snapshotSession() (route, liveID string, connected, force bool, gen uint64) {
	gen = h.activeGen.Load()
	h.sessionMu.Lock()
	defer h.sessionMu.Unlock()
	return h.route, h.liveID, h.connected, h.forceMode, gen
}

func (h *hub) publishCaptureStatus(connected bool, gen uint64) {
	if !h.setConnectedIfAlive(gen, connected) {
		return
	}
	env := h.statusEnvelope()
	route, _, _, _, _ := h.snapshotSession()
	if connected && route != "3" {
		env["msg"] = listener.MsgConnected
	} else if connected {
		env["msg"] = "监听已开启"
	} else if route != "3" {
		env["msg"] = "直播已断开"
	} else {
		env["msg"] = "监听已停止"
	}
	env["session"] = gen
	h.send(env)
}

// runConnect starts capture off the IPC goroutine so disconnect/ping stay responsive.
func (h *hub) runConnect(gen uint64, route string, forceSystem bool) {
	if !h.sessionAlive(gen) {
		return
	}
	h.waitCaptureSettle()
	if !h.sessionAlive(gen) {
		return
	}
	h.captureMu.Lock()
	defer h.captureMu.Unlock()
	if !h.sessionAlive(gen) {
		return
	}

	var err error
	switch route {
	case "4":
		err = h.startListen("4", false)
		if err == nil && h.sessionAlive(gen) {
			err = h.startRoute4(gen)
		}
	case "1", "2":
		err = h.startListen(route, forceSystem)
	case "3":
		err = h.startListen("3", false)
		if err == nil {
			h.log.Info("route3 started")
		}
	default:
		h.disarmCapture()
		h.send(Envelope{"op": OpError, "code": "listen", "route": route, "msg": "unknown route"})
		h.send(h.statusEnvelope())
		return
	}
	if err != nil {
		// startListen/startRoute4 already emitted OpError when applicable.
		if h.sessionAlive(gen) {
			h.disarmCapture()
			h.send(h.statusEnvelope())
		}
		return
	}
	if !h.sessionAlive(gen) {
		h.stopCaptureLocked()
	}
}

func (h *hub) stopCaptureLocked() {
	h.cancelRoute4WssGrace()
	if h.shell != nil {
		h.shell.Stop()
		h.shell = nil
	}
	if h.capture != nil {
		h.capture.Stop()
	}
	listener.ReapOwnedBrowsers(h.root)
}

func (h *hub) stopCapture() {
	h.captureMu.Lock()
	had := h.shell != nil
	if h.capture != nil && h.capture.ActiveRoute() != "" {
		had = true
	}
	h.stopCaptureLocked()
	if had {
		// Douyin may still hold the prior watcher; leave also saves rotated cookies.
		h.settleUntil = time.Now().Add(1500 * time.Millisecond)
	}
	h.captureMu.Unlock()
}

func (h *hub) waitCaptureSettle() {
	for {
		h.captureMu.Lock()
		wait := time.Until(h.settleUntil)
		h.captureMu.Unlock()
		if wait <= 0 {
			return
		}
		if wait > 2*time.Second {
			wait = 2 * time.Second
		}
		time.Sleep(wait)
	}
}
