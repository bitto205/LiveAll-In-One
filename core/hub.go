package core

import (
	"context"
	"encoding/base64"
	"log/slog"
	"sync"

	"liveaio/listener"
)

type hub struct {
	mu        sync.Mutex
	conns     map[*Conn]struct{}
	root      string
	route     string
	liveID    string
	connected bool
	forceMode bool
	overtime  *Engine
	leaf      *LeafEngine
	danmu     *Danmu
	memo      *Memo
	config    *ConfigStore
	log       *slog.Logger
	shutdown  func()
	showUI    func()
	capture   *listener.Manager
	shell     *listener.Shell
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
	h.capture.OnMessage = func(m listener.Msg) { h.afterMessage(m) }
	h.capture.OnStatus = func(connected bool) {
		h.connected = connected
		env := h.statusEnvelope()
		if connected && h.route != "3" {
			env["msg"] = listener.MsgConnected
		} else if connected {
			env["msg"] = "监听已开启"
		}
		h.send(env)
	}
	h.capture.OnError = func(err error) {
		h.connected = false
		code := "listen"
		msg := err.Error()
		if h.route != "3" {
			if ce, ok := listener.AsConnectError(err); ok {
				code = string(ce.Code)
				msg = ce.Error()
			}
		}
		h.send(Envelope{"op": OpError, "code": code, "route": h.route, "msg": msg})
		h.send(h.statusEnvelope())
	}
	h.overtime = NewOvertime(
		func(rem int, running bool) {
			h.send(Envelope{"op": OpTick, "remaining_seconds": rem, "running": running})
		},
		func(entries []LedgerEntry) {
			h.send(Envelope{"op": OpLedger, "entries": entries})
		},
	)
	h.leaf = NewLeaf(func(gift string, leaves int, user string) {
		h.send(Envelope{
			"op":    OpLeafSpawn,
			"gift":  gift,
			"count": leaves,
			"user":  user,
		})
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

func (h *hub) ingestFrame(raw []byte) {
	ok, msgs := listener.TryParseFrame(raw)
	if !ok {
		return
	}
	if !h.connected {
		h.connected = true
		h.send(h.statusEnvelope())
	}
	for _, m := range msgs {
		out := Envelope{"op": OpMessage}
		for k, v := range m {
			out[k] = v
		}
		h.send(out)
		h.afterMessage(m)
	}
}

func (h *hub) stopCapture() {
	if h.shell != nil {
		h.shell.Stop()
		h.shell = nil
	}
	if h.capture != nil {
		h.capture.Stop()
	}
}

func (h *hub) startRoute4() {
	cl := &listener.Shell{
		OnCtrl: func(ctrl string) {
			switch ctrl {
			case listener.CtrlLiveOn:
				h.connected = true
				h.send(h.statusEnvelope())
				h.log.Info("route4 live on")
			case listener.CtrlLiveOff, listener.CtrlWSDown:
				h.connected = false
				h.send(h.statusEnvelope())
				h.log.Info("route4 live off", "ctrl", ctrl)
			}
		},
		OnFrame: func(raw []byte) {
			if !h.connected {
				return
			}
			h.ingestFrame(raw)
		},
		OnErr: func(err error) {
			h.log.Warn("shellipc", "err", err)
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
		h.send(Envelope{"op": OpError, "code": code, "route": h.route, "msg": msg})
		h.send(h.statusEnvelope())
		return
	}
	h.shell = cl
	h.log.Info("route4 shellipc listening")
}

func (h *hub) startListen(route string, forceSystem bool) error {
	if h.capture == nil {
		return nil
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
		h.liveID = liveID
		h.route = route
		h.connected = false
		h.forceMode = forceSystem
		h.log.Info("connect", "live_id", liveID, "route", route)
		h.stopCapture()

		switch route {
		case "4":
			if err := h.startListen("4", false); err != nil {
				return
			}
			h.startRoute4()
		case "1", "2":
			_ = h.startListen(route, forceSystem)
		case "3":
			if err := h.startListen("3", false); err != nil {
				return
			}
			h.log.Info("route3 started")
		}

	case OpDisconnect:
		h.stopCapture()
		h.connected = false
		h.send(h.statusEnvelope())

	case OpStatus:
		conn := false
		if v, ok := env["connected"].(bool); ok {
			conn = v
		}
		h.connected = conn
		out := h.statusEnvelope()
		out["connected"] = conn
		if r, ok := env["route"].(string); ok && r != "" {
			out["route"] = r
		}
		h.send(out)

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
		h.leaf.HandleGift(user, user, gift, count)

	case OpToolDanmuSet:
		s := NormalizeDanmuSettings(env["settings"])
		h.danmu.Set(s)

	case OpToolMemoSet:
		s := NormalizeMemoSettings(env["settings"])
		h.memo.Set(s)

	case OpToolDemand:
		tool, _ := env["tool"].(string)
		active, _ := env["active"].(bool)
		h.setToolDemand(tool, active)

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
	driver := "listener"
	if h.route == "4" {
		driver = "shellipc"
	}
	status := RouteStatus{
		Route:       h.route,
		Connected:   h.connected,
		LiveID:      h.liveID,
		Driver:      driver,
		Health:      "ok",
		ForceSystem: h.forceMode,
	}
	return Envelope{
		"op":           OpStatus,
		"connected":    status.Connected,
		"route":        status.Route,
		"live_id":      status.LiveID,
		"driver":       status.Driver,
		"health":       status.Health,
		"force_system": status.ForceSystem,
		"status":       status,
	}
}

func (h *hub) afterMessage(m listener.Msg) {
	t, _ := m["type"].(string)
	if t == "control" && listener.ControlEnded(m["status"]) {
		if h.route == "3" {
			h.log.Info("live ended", "via", "control", "route", "3")
			return
		}
		if h.connected {
			h.connected = false
			h.send(h.statusEnvelope())
			h.log.Info("live ended", "via", "control")
			h.stopCapture()
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
			h.leaf.HandleGift(user, uid, gift, count)
		}
	}
	if h.danmu.Active() {
		if show := h.danmu.Accept(m, diamonds); show != nil {
			h.send(Envelope(show))
		}
	}
	if h.memo.Active() {
		if item := h.memo.Accept(m, diamonds); item != nil {
			h.send(Envelope(item))
		}
	}
}
