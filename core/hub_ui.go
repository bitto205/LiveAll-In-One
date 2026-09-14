package core

import (
	"liveaio/listener"
)

func (h *hub) handleUICommand(c *Conn, env Envelope) {
	action, _ := env["action"].(string)
	route, _ := env["route"].(string)
	payload, _ := env["payload"].(map[string]any)
	if payload == nil {
		if p, ok := env["payload"].(map[string]interface{}); ok {
			payload = map[string]any(p)
		}
	}

	switch action {
	case "login.query":
		text, can := LoginUIState(h.root)
		_ = c.Send(Envelope{
			"op": OpLoginState, "text": text, "can_login": can,
		})

	case "login.start":
		go h.runLoginThenNotify()

	case "route.env":
		if route == "" {
			route, _ = env["route"].(string)
		}
		_ = c.Send(Envelope(h.queryRouteEnv(route)))

	case "route4.set_companion_path":
		pathStr, _ := payload["path"].(string)
		if pathStr == "" {
			pathStr, _ = env["path"].(string)
		}
		ok, msg := listener.SetManualCompanionDir(h.root, pathStr)
		if ok {
			_ = h.config.Set("companion_install_dir", pathStr)
		}
		_ = c.Send(Envelope{
			"op": OpRouteEnv, "route": "4", "ok": ok, "message": msg,
			"action": action,
		})
		// Always refresh env so the Patch button leaves "…中" and reflects reality.
		_ = c.Send(Envelope(h.queryRouteEnv("3")))
		_ = c.Send(Envelope(h.queryRouteEnv("4")))

	case "route4.patch", "route4.unpatch", "route3.unpatch":
		// Patch/unpatch can take seconds; keep Pages IPC free for connect/disconnect.
		go func(action, route string) {
			ok, msg := h.runListenerAction(action)
			h.send(Envelope{
				"op": OpRouteEnv, "route": routeOrFromAction(action),
				"ok": ok, "message": msg, "action": action,
			})
			h.send(Envelope(h.queryRouteEnv("3")))
			h.send(Envelope(h.queryRouteEnv("4")))
			_ = route
		}(action, route)

	case "ui.show":
		h.requestShowUI()
		_ = c.Send(Envelope{"op": OpStatus, "ack": "ui.show"})

	case "quit.detach_ui":
		_ = c.Send(Envelope{"op": OpStatus, "ack": "detach_ui"})

	case "quit.shutdown_all":
		_ = c.Send(Envelope{"op": OpReady, "bye": true})
		h.stopCapture()
		QuitTray()
		if h.shutdown != nil {
			h.shutdown()
		}

	default:
		_ = c.Send(Envelope{
			"op": OpError, "code": "bad_ui_command",
			"msg": "unknown action: " + action,
		})
	}
}

// 已连接的 Pages 先自己抬起窗口；没有存活的 Pages 时才重新加载一次。
// 无论触发来源（托盘 / 第二次启动），都只在持有实例里开这一套 QApplication。
func (h *hub) requestShowUI() {
	h.send(Envelope{"op": OpFocusUI})
	if h.showUI != nil {
		go h.showUI()
	}
}

func routeOrFromAction(action string) string {
	if len(action) >= 6 && action[:6] == "route3" {
		return "3"
	}
	if len(action) >= 6 && action[:6] == "route4" {
		return "4"
	}
	return ""
}

func (h *hub) runLoginThenNotify() {
	if err := listener.DoLogin(h.root); err != nil {
		h.log.Warn("login failed", "err", err)
		h.send(Envelope{
			"op": OpError, "code": "login", "msg": err.Error(),
		})
	}
	text, can := LoginUIState(h.root)
	h.send(Envelope{"op": OpLoginState, "text": text, "can_login": can})
}

func (h *hub) queryRouteEnv(route string) map[string]any {
	var out map[string]any
	switch route {
	case "3":
		out = listener.PageCheckRoute3(h.root)
	case "4":
		out = listener.PageCheckRoute4(h.root)
	default:
		out = DefaultRouteEnv(route)
	}
	if out == nil {
		out = map[string]any{}
	}
	// PageCheck* maps omit op; without it Pages ignore the packet.
	out["op"] = OpRouteEnv
	out["route"] = route
	return out
}

func (h *hub) runListenerAction(action string) (bool, string) {
	switch action {
	case "route4.patch":
		return listener.PatchCompanion(h.root)
	case "route4.unpatch":
		return listener.UnpatchCompanion(h.root)
	case "route3.unpatch":
		return listener.UnpatchLocal(h.root)
	default:
		return false, "unsupported"
	}
}
