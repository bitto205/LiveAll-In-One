package core

import (
	"context"
	"encoding/base64"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"liveaio/listener"
	"liveaio/util/connectdiag"
)

const route4WssGrace = 30 * time.Second

type hub struct {
	mu        sync.Mutex
	conns     map[*Conn]struct{}
	root      string
	sessionMu sync.Mutex
	route     string
	liveID    string
	connected bool
	forceMode bool
	// wantCapture + activeGen gate late capture callbacks after cancel/disconnect.
	wantCapture atomic.Bool
	sessionGen  atomic.Uint64
	activeGen   atomic.Uint64
	captureMu   sync.Mutex
	settleUntil time.Time // next Start should wait until this (set after real Stop)
	overtime    *Engine
	leaf        *LeafEngine
	danmu       *Danmu
	memo        *Memo
	config      *ConfigStore
	log         *slog.Logger
	shutdown    func()
	showUI      func()
	capture     *listener.Manager
	shell       *listener.Shell

	// Route 4 companion WS can flap; defer connected=false until grace expires.
	r4WssGraceMu  sync.Mutex
	r4WssGrace    *time.Timer
	r4WssGraceGen uint64
}

func startHub(ctx context.Context, root, tcp string, log *slog.Logger, shutdown func()) (*hub, *Server) {
	h := &hub{
		root:     root,
		log:      log,
		shutdown: shutdown,
		conns:    map[*Conn]struct{}{},
		config:   NewConfigStore(root),
		capture: listener.NewCapture(root, tcp, func(msg string, kv ...any) {
			log.Info(msg, kv...)
		}),
	}
	h.capture.OnFrame = func(raw []byte) { h.ingestFrame(raw) }
	h.capture.OnMessage = func(m listener.Msg) {
		if !h.wantCapture.Load() {
			return
		}
		h.afterMessage(m)
	}
	// Default callbacks; startListen rebinds them to the connect-time session gen.
	h.capture.OnStatus = func(connected bool) {
		h.publishCaptureStatus(connected, h.activeGen.Load())
	}
	h.capture.OnError = func(err error) {
		h.handleCaptureError(h.activeGen.Load(), err)
	}
	h.overtime = NewOvertime(
		func(rem int, running bool) {
			h.send(Envelope{"op": OpTick, "remaining_seconds": rem, "running": running})
		},
		func(entries []LedgerEntry) {
			h.send(Envelope{"op": OpLedger, "entries": entries})
		},
	)
	h.leaf = NewLeaf(func(gift string, leaves int, user string, action string) {
		h.log.Info("leaf spawn", "gift", gift, "leaves", leaves, "action", action, "user", user)
		env := Envelope{
			"op":    OpLeafSpawn,
			"gift":  gift,
			"count": leaves,
			"user":  user,
		}
		if action != "" {
			env["action"] = action
		}
		h.send(env)
	})
	h.danmu = NewDanmu()
	h.memo = NewMemo()

	srv := &Server{
		TCPAddr: tcp,
		Handler: h.handle,
		Log:     log,
		OnConnect: func(c *Conn) {
			h.addConn(c)
		},
		OnDisconnect: func(c *Conn) {
			h.removeConn(c)
		},
	}
	go func() {
		log.Info("core listening", "root", root, "version", Version, "tcp", tcp)
		if err := srv.Serve(ctx); err != nil {
			log.Error("serve ended", "err", err)
			if shutdown != nil {
				shutdown()
			}
		}
	}()
	return h, srv
}

func (h *hub) addConn(c *Conn) {
	h.mu.Lock()
	if h.conns == nil {
		h.conns = map[*Conn]struct{}{}
	}
	h.conns[c] = struct{}{}
	h.mu.Unlock()
	_ = c.Send(Envelope{"op": OpCapabilities, "capabilities": DefaultCapabilities(), "features": DefaultFeatures()})
	_ = c.Send(h.statusEnvelope())
}

func (h *hub) removeConn(c *Conn) {
	h.mu.Lock()
	delete(h.conns, c)
	h.mu.Unlock()
}

func (h *hub) send(env Envelope) {
	h.mu.Lock()
	conns := make([]*Conn, 0, len(h.conns))
	for c := range h.conns {
		conns = append(conns, c)
	}
	h.mu.Unlock()
	var dead []*Conn
	for _, c := range conns {
		if err := c.Send(env); err != nil {
			dead = append(dead, c)
		}
	}
	for _, c := range dead {
		h.removeConn(c)
		_ = c.Close()
	}
}

func (h *hub) ensureGiftCatalog() {
	if GiftCatalogLoaded() {
		return
	}
	if err := LoadGifts(h.root); err != nil {
		h.log.Warn("gift catalog", "err", err)
		return
	}
	h.log.Info("gift catalog loaded", "count", len(Names()))
}

func (h *hub) setToolDemand(tool string, active bool) {
	switch tool {
	case "overtime":
		h.overtime.SetActive(active)
		if active {
			h.ensureGiftCatalog()
		}
	case "leaf":
		h.leaf.SetActive(active)
		if active {
			h.ensureGiftCatalog()
			h.hydrateLeafSettings()
		}
	case "danmu":
		h.danmu.SetActive(active)
		if active {
			h.ensureGiftCatalog()
		}
	case "memo":
		h.memo.SetActive(active)
		if active {
			h.ensureGiftCatalog()
		}
	default:
		return
	}
	if !active && !h.overtime.Active() && !h.leaf.Active() && !h.danmu.Active() && !h.memo.Active() {
		ClearGiftCatalog()
	}
}

// hydrateLeafSettings loads leaf.settings from config when the in-memory table is empty.
// Tools normally push tool.leaf.set; this covers demand-before-set and Core restart races.
func (h *hub) hydrateLeafSettings() {
	if h.leaf == nil || h.config == nil {
		return
	}
	if h.leaf.NamedRuleCount() > 0 {
		return
	}
	raw, ok, err := h.config.Get("leaf.settings")
	if err != nil || !ok || raw == nil {
		return
	}
	s := NormalizeLeafSettings(raw)
	h.leaf.SetSettings(s)
	if n := h.leaf.NamedRuleCount(); n > 0 {
		h.log.Info("leaf settings hydrated", "rules", n)
	}
}

func (h *hub) handleCaptureError(gen uint64, err error) {
	if err == nil || !h.sessionAlive(gen) {
		return
	}
	h.disarmCapture()
	code := "listen"
	msg := err.Error()
	route, _, _, _, _ := h.snapshotSession()
	if route != "3" {
		if ce, ok := listener.AsConnectError(err); ok {
			code = string(ce.Code)
			msg = ce.Error()
		}
	}
	h.send(Envelope{"op": OpError, "code": code, "route": route, "msg": msg})
	h.send(h.statusEnvelope())
	go h.stopCapture()
}

func (h *hub) ingestFrame(raw []byte) {
	gen := h.activeGen.Load()
	h.ingestFrameFor(gen, raw)
}

func (h *hub) ingestFrameFor(gen uint64, raw []byte) {
	if !h.sessionAlive(gen) {
		return
	}
	ok, msgs := listener.TryParseFrame(raw)
	if !ok {
		return
	}
	// Frames still flowing ⇒ cancel route4 "WSS down" grace (socket flap, not real end).
	h.cancelRoute4WssGrace()
	h.publishCaptureStatus(true, gen)
	for _, m := range msgs {
		if !h.sessionAlive(gen) {
			return
		}
		out := Envelope{"op": OpMessage}
		for k, v := range m {
			out[k] = v
		}
		h.send(out)
		h.afterMessage(m)
	}
}

func (h *hub) cancelRoute4WssGrace() {
	h.r4WssGraceMu.Lock()
	defer h.r4WssGraceMu.Unlock()
	if h.r4WssGrace != nil {
		h.r4WssGrace.Stop()
		h.r4WssGrace = nil
	}
	h.r4WssGraceGen = 0
}

func (h *hub) armRoute4WssGrace(ctrl string) {
	gen := h.activeGen.Load()
	if !h.sessionAlive(gen) {
		return
	}
	h.r4WssGraceMu.Lock()
	defer h.r4WssGraceMu.Unlock()
	if h.r4WssGrace != nil {
		h.r4WssGrace.Stop()
	}
	h.r4WssGraceGen = gen
	h.log.Info("route4 wss down grace", "ctrl", ctrl, "grace_s", int(route4WssGrace.Seconds()), "session", gen)
	h.r4WssGrace = time.AfterFunc(route4WssGrace, func() {
		h.r4WssGraceMu.Lock()
		armed := h.r4WssGraceGen
		h.r4WssGrace = nil
		h.r4WssGraceGen = 0
		h.r4WssGraceMu.Unlock()
		if armed == 0 || !h.sessionAlive(armed) {
			return
		}
		h.disarmCapture()
		env := h.statusEnvelope()
		env["msg"] = "直播已断开"
		env["session"] = armed
		h.send(env)
		h.log.Info("route4 live off", "via", "wss_grace", "session", armed)
		go h.stopCapture()
	})
}

func (h *hub) startRoute4(gen uint64) error {
	// Live status is decided by proxy_shell (old Python listener4 path), not by
	// waiting forever for a ctrl push after IPC dial.
	onAir, err := listener.QueryLiveOnAir("")
	if err != nil {
		h.log.Error("route4 live query", "err", err)
		code := "timeout"
		msg := err.Error()
		if ce, ok := listener.AsConnectError(err); ok {
			code = string(ce.Code)
			msg = ce.Error()
		}
		if h.sessionAlive(gen) {
			h.send(Envelope{"op": OpError, "code": code, "route": "4", "msg": msg})
		}
		return err
	}
	if !onAir {
		err = connectdiag.ErrNotLivingConnectFail()
		h.log.Info("route4 not living", "session", gen)
		if h.sessionAlive(gen) {
			h.send(Envelope{"op": OpError, "code": string(listener.CodeNotLiving), "route": "4", "msg": err.Error()})
		}
		return err
	}

	cl := &listener.Shell{
		OnCtrl: func(ctrl string) {
			if !h.sessionAlive(gen) {
				return
			}
			switch ctrl {
			case listener.CtrlLiveOn, listener.CtrlWSOpen, listener.CtrlWSConnected:
				h.cancelRoute4WssGrace()
				h.publishCaptureStatus(true, gen)
				h.log.Info("route4 live on", "ctrl", ctrl, "session", gen)
			case listener.CtrlLiveOff, listener.CtrlWSDown:
				h.armRoute4WssGrace(ctrl)
			}
		},
		OnFrame: func(raw []byte) {
			if !h.sessionAlive(gen) {
				return
			}
			h.ingestFrame(raw)
		},
		OnErr: func(err error) {
			h.log.Warn("shellipc", "err", err)
			if !h.sessionAlive(gen) {
				return
			}
			// Drop while "connected" — surface like a capture fault (not silent).
			h.handleCaptureError(gen, connectdiag.ErrProxyShellNoResponse(err))
		},
	}
	if err := cl.Start(); err != nil {
		h.log.Error("shellipc start", "err", err)
		code := "shellipc"
		msg := err.Error()
		if ce, ok := listener.AsConnectError(err); ok {
			code = string(ce.Code)
			msg = ce.Error()
		}
		if h.sessionAlive(gen) {
			h.send(Envelope{"op": OpError, "code": code, "route": "4", "msg": msg})
		}
		return err
	}
	if !h.sessionAlive(gen) {
		cl.Stop()
		return nil
	}
	h.shell = cl
	// Query already said on-air; mark connected now (ctrl replay is belt-and-suspenders).
	h.publishCaptureStatus(true, gen)
	h.log.Info("route4 shellipc listening", "session", gen, "on_air", true)
	return nil
}

func (h *hub) startListen(route string, forceSystem bool) error {
	if h.capture == nil {
		return nil
	}
	gen := h.activeGen.Load()
	// Bind callbacks to this connect gen so a dying shell cannot publish into
	// a newer session via activeGen.Load().
	h.capture.OnStatus = func(connected bool) {
		h.publishCaptureStatus(connected, gen)
	}
	h.capture.OnError = func(err error) {
		h.handleCaptureError(gen, err)
	}
	h.capture.OnFrame = func(raw []byte) {
		h.ingestFrameFor(gen, raw)
	}
	h.capture.OnMessage = func(m listener.Msg) {
		if !h.sessionAlive(gen) {
			return
		}
		h.afterMessage(m)
	}
	if err := h.capture.Start(route, h.liveID, forceSystem); err != nil {
		h.log.Error("listen", "route", route, "err", err)
		code := "listen"
		msg := err.Error()
		if ce, ok := listener.AsConnectError(err); ok {
			code = string(ce.Code)
			msg = ce.Error()
		}
		h.send(Envelope{"op": OpError, "code": code, "route": route, "msg": msg})
		return err
	}
	return nil
}

func (h *hub) handle(c *Conn, env Envelope) {
	switch env.Op() {
	case OpPing:
		out := Envelope{"op": OpPong}
		if id, ok := env["id"]; ok {
			out["id"] = id
		}
		_ = c.Send(out)

	case OpShutdown:
		_ = c.Send(Envelope{"op": OpReady, "bye": true})
		h.stopCapture()
		if h.shutdown != nil {
			h.shutdown()
		}

	case OpConnect:
		liveID, _ := env["live_id"].(string)
		route, _ := env["route"].(string)
		forceSystem := false
		if v, ok := env["force_system"].(bool); ok {
			forceSystem = v
		}
		// Never stopCapture on the IPC goroutine — browser teardown can block for
		// seconds and make every UI click look like "Core 不响应".
		h.disarmCapture()
		gen := h.armCapture(route, liveID, forceSystem)
		h.log.Info("connect", "live_id", liveID, "route", route, "session", gen)
		out := h.statusEnvelope()
		out["connecting"] = true
		out["session"] = gen
		h.send(out)
		go func(gen uint64, route string, forceSystem bool) {
			h.stopCapture()
			if !h.sessionAlive(gen) {
				return
			}
			h.runConnect(gen, route, forceSystem)
		}(gen, route, forceSystem)

	case OpDisconnect:
		route, _, _, _, _ := h.snapshotSession()
		h.disarmCapture()
		h.send(h.statusEnvelope())
		h.log.Info("disconnect", "route", route)
		go h.stopCapture()

	case OpStatus:
		// Snapshot only — clients must not mutate hub.connected via inbound status.
		_ = c.Send(h.statusEnvelope())

	case OpFramePush:
		b64, _ := env["payload_b64"].(string)
		raw, err := base64.StdEncoding.DecodeString(b64)
		if err != nil {
			_ = c.Send(Envelope{"op": OpError, "code": "bad_b64", "msg": err.Error()})
			return
		}
		h.ingestFrame(raw)

	case OpMessageIngest:
		m := listener.Msg{}
		for k, v := range env {
			if k == "op" {
				continue
			}
			m[k] = v
		}
		if _, ok := m["type"].(string); !ok {
			_ = c.Send(Envelope{"op": OpError, "code": "bad_ingest", "msg": "missing type"})
			return
		}
		h.afterMessage(m)

	case OpToolOvertimeSet:
		s := NormalizeOvertimeSettings(env["settings"])
		h.overtime.SetSettings(s)

	case OpToolOvertimeCmd:
		cmd, _ := env["cmd"].(string)
		h.overtime.Cmd(cmd)

	case OpToolOvertimeSim:
		gift, _ := env["gift"].(string)
		count := 1
		switch v := env["count"].(type) {
		case float64:
			count = int(v)
		case int:
			count = v
		}
		user, _ := env["user"].(string)
		if user == "" {
			user = "sim"
		}
		h.overtime.HandleGift(user, user, gift, count)

	case OpToolLeafSet:
		s := NormalizeLeafSettings(env["settings"])
		h.leaf.SetSettings(s)

	case OpToolLeafSim:
		gift, _ := env["gift"].(string)
		count := 1
		switch v := env["count"].(type) {
		case float64:
			count = int(v)
		case int:
			count = v
		}
		user, _ := env["user"].(string)
		if user == "" {
			user = "sim"
		}
		// Sim from the settings panel implies the leaf engine should run even if
		// tool.demand was dropped; live gifts still require Active via demand.
		if !h.leaf.Active() {
			h.leaf.SetActive(true)
		}
		h.hydrateLeafSettings()
		h.leaf.HandleSimGift(user, user, gift, count)

	case OpToolDanmuSet:
		s := NormalizeDanmuSettings(env["settings"])
		h.danmu.Set(s)

	case OpToolMemoSet:
		s := NormalizeMemoSettings(env["settings"])
		h.memo.Set(s)

	case OpToolDemand:
		tool, _ := env["tool"].(string)
		h.setToolDemand(tool, boolValue(env["active"], false))

	case OpConfigSet:
		key, _ := env["key"].(string)
		if key == "" {
			_ = c.Send(Envelope{"op": OpError, "code": "bad_config_key", "msg": "missing key"})
			return
		}
		if err := h.config.Set(key, env["value"]); err != nil {
			_ = c.Send(Envelope{"op": OpError, "code": "config_set_failed", "msg": err.Error(), "key": key})
			return
		}
		_ = c.Send(Envelope{"op": OpConfigOk, "key": key, "value": env["value"]})

	case OpConfigGet:
		key, _ := env["key"].(string)
		if key == "" {
			all, err := h.config.ReadAll()
			if err != nil {
				_ = c.Send(Envelope{"op": OpError, "code": "config_get_failed", "msg": err.Error()})
				return
			}
			_ = c.Send(Envelope{"op": OpConfigValue, "values": all})
			return
		}
		value, ok, err := h.config.Get(key)
		if err != nil {
			_ = c.Send(Envelope{"op": OpError, "code": "config_get_failed", "msg": err.Error(), "key": key})
			return
		}
		_ = c.Send(Envelope{"op": OpConfigValue, "key": key, "value": value, "exists": ok})

	case OpUIOverlayState:
		tool, _ := env["tool"].(string)
		state := 0
		switch v := env["state"].(type) {
		case float64:
			state = int(v)
		case int:
			state = v
		case int64:
			state = int(v)
		}
		if tool != "" {
			setOverlayStateCache(tool, state)
		}

	case OpUICommand:
		h.handleUICommand(c, env)

	default:
		_ = c.Send(Envelope{
			"op": OpError, "code": "unsupported",
			"msg": "unknown op: " + env.Op(),
		})
	}
}

func (h *hub) statusEnvelope() Envelope {
	route, liveID, connected, force, gen := h.snapshotSession()
	driver := "listener"
	if route == "4" {
		driver = "shellipc"
	}
	status := RouteStatus{
		Route:       route,
		Connected:   connected,
		LiveID:      liveID,
		Driver:      driver,
		Health:      "ok",
		ForceSystem: force,
	}
	return Envelope{
		"op":           OpStatus,
		"connected":    status.Connected,
		"route":        status.Route,
		"live_id":      status.LiveID,
		"driver":       status.Driver,
		"health":       status.Health,
		"force_system": status.ForceSystem,
		"session":      gen,
		"status":       status,
	}
}

func (h *hub) afterMessage(m listener.Msg) {
	t, _ := m["type"].(string)
	if t == "control" && listener.ControlEnded(m["status"]) {
		route, _, connected, _, _ := h.snapshotSession()
		if route == "3" {
			h.log.Info("live ended", "via", "control", "route", "3")
			return
		}
		if connected {
			h.disarmCapture()
			env := h.statusEnvelope()
			env["msg"] = "直播已断开"
			h.send(env)
			h.log.Info("live ended", "via", "control")
			go h.stopCapture()
		}
		return
	}
	diamonds := 0
	if t == "gift" {
		gift, _ := m["gift"].(string)
		user, _ := m["user"].(string)
		uid, _ := m["user_id"].(string)
		count := 1
		switch v := m["count"].(type) {
		case float64:
			count = int(v)
		case int:
			count = v
		case int64:
			count = int(v)
		}
		if h.overtime.Active() || h.leaf.Active() || h.danmu.Active() || h.memo.Active() {
			h.ensureGiftCatalog()
			diamonds = Diamonds(gift)
		}
		if h.overtime.Active() {
			h.overtime.HandleGift(user, uid, gift, count)
		}
		if h.leaf.Active() {
			h.hydrateLeafSettings()
			h.log.Info("leaf gift", "gift", gift, "gift_count", count, "user", user)
			h.leaf.HandleGift(user, uid, gift, count)
		}
	}
	if h.danmu.Active() {
		if show := h.danmu.Accept(m, diamonds); show != nil {
			if t == "gift" {
				h.log.Info("danmu show", "kind", "gift", "gift", show["gift"],
					"count", show["count"], "user", show["user"])
			}
			h.send(Envelope(show))
		}
	}
	if h.memo.Active() {
		if item := h.memo.Accept(m, diamonds); item != nil {
			h.send(Envelope(item))
		}
	}
}
