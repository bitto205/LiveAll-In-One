// Package listener：线路采集与帧解析（纯 Go，平面包）。
package listener

import (
	"bytes"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"strconv"
	"sync"
	"time"

	"liveaio/listener/livepb"

	"google.golang.org/protobuf/proto"
)

// ID is the capture route number.
type ID string

const (
	Route1 ID = "1" // JS hook + chromedp
	Route2 ID = "2" // WSS intercept + chromedp
	Route3 ID = "3" // proxy_shell + system proxy
	Route4 ID = "4" // companion patch + shellipc
)

const DefaultCoreTCP = "127.0.0.1:19877"

func ParseID(s string) (ID, bool) {
	switch ID(s) {
	case Route1, Route2, Route3, Route4:
		return ID(s), true
	default:
		return "", false
	}
}

// Params is per-route connect input.
type Params struct {
	Root        string
	CoreTCP     string
	LiveID      string
	Route       ID
	ForceSystem bool
	OnFrame     func([]byte)
	OnMessage   func(Msg)
	OnStatus    func(connected bool)
	Logf        func(string, ...any)
}

// Driver is one capture route. Run blocks until ctx is cancelled or a fatal error.
type Driver interface {
	ID() ID
	Run(ctx context.Context, p Params) error
}

type Factory func() Driver

type Registry struct {
	factories map[ID]Factory
}

func NewRegistry() *Registry {
	r := &Registry{factories: map[ID]Factory{}}
	r.Register(Route1, func() Driver { return Route1Driver{} })
	r.Register(Route2, func() Driver { return Route2Driver{} })
	r.Register(Route3, func() Driver { return Route3Driver{} })
	r.Register(Route4, func() Driver { return R4{} })
	return r
}

func (r *Registry) Register(id ID, f Factory) {
	if r == nil || f == nil {
		return
	}
	if r.factories == nil {
		r.factories = map[ID]Factory{}
	}
	r.factories[id] = f
}

func (r *Registry) New(id ID) (Driver, error) {
	if r == nil {
		return nil, fmt.Errorf("nil listener registry")
	}
	f, ok := r.factories[id]
	if !ok {
		return nil, fmt.Errorf("unknown route %s", id)
	}
	return f(), nil
}

// Manager owns the active capture route in-process.
type Manager struct {
	mu       sync.Mutex
	stopFn   func()
	stopped  chan struct{}
	root     string
	tcp      string
	logf     func(string, ...any)
	registry *Registry
	current  ID

	OnFrame   func([]byte)
	OnMessage func(Msg)
	OnStatus  func(connected bool)
	OnError   func(error)
}

func NewCapture(root, tcp string, logf func(string, ...any)) *Manager {
	if logf == nil {
		logf = func(string, ...any) {}
	}
	if tcp == "" {
		tcp = DefaultCoreTCP
	}
	return &Manager{root: root, tcp: tcp, logf: logf, registry: NewRegistry()}
}

func (m *Manager) Stop() {
	m.mu.Lock()
	fn := m.stopFn
	done := m.stopped
	m.stopFn = nil
	m.stopped = nil
	m.current = ""
	m.mu.Unlock()
	if fn != nil {
		fn()
	}
	// Leave needs ~2s; Close is hard-capped ~3s. Keep this under the old IPC-blocking
	// budget now that handle runs off the read loop — but still finish before Reap.
	if done != nil {
		select {
		case <-done:
		case <-time.After(4 * time.Second):
		}
	}
}

func (m *Manager) setStop(fn func(), done chan struct{}) {
	m.mu.Lock()
	m.stopFn = fn
	m.stopped = done
	m.mu.Unlock()
}

func (m *Manager) ActiveRoute() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return string(m.current)
}

func (m *Manager) Start(route, liveID string, forceSystem bool) error {
	id, ok := ParseID(route)
	if !ok {
		return fmt.Errorf("unknown route %s", route)
	}
	m.Stop()
	drv, err := m.registry.New(id)
	if err != nil {
		return err
	}
	p := Params{
		Root: m.root, CoreTCP: m.tcp, LiveID: liveID, Route: id, ForceSystem: forceSystem,
		OnFrame: m.OnFrame, OnMessage: m.OnMessage, OnStatus: m.OnStatus,
		Logf: m.logf,
	}
	m.mu.Lock()
	m.current = id
	m.mu.Unlock()

	if id == Route4 {
		if err := PrepareR4(m.root); err != nil {
			m.mu.Lock()
			m.current = ""
			m.mu.Unlock()
			return err
		}
		// Hub owns Shell; register a cancel so Manager.Stop is aligned.
		ctx, cancel := context.WithCancel(context.Background())
		done := make(chan struct{})
		m.setStop(cancel, done)
		go func() {
			<-ctx.Done()
			close(done)
		}()
		m.logf("route ready", "route", string(id))
		return nil
	}

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		if err := drv.Run(ctx, p); err != nil && ctx.Err() == nil {
			m.logf("route stopped", "route", string(id), "err", err)
			if m.OnError != nil {
				m.OnError(err)
			}
		}
	}()
	m.setStop(cancel, done)
	m.logf("route started", "route", string(id), "driver", fmt.Sprintf("%T", drv))
	return nil
}

// Msg is a JSON-ready live message matching core/UI schema field names.
type Msg map[string]any

func userName(u *livepb.User) string {
	if u == nil {
		return ""
	}
	return u.GetNickname()
}

func userID(u *livepb.User) string {
	if u == nil {
		return ""
	}
	if u.GetId() != 0 {
		return strconv.FormatInt(u.GetId(), 10)
	}
	return u.GetIdStr()
}

func ParseItem(method string, payload []byte) Msg {
	switch method {
	case "WebcastChatMessage":
		var pb livepb.ChatMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		u, c := userName(pb.GetUser()), pb.GetContent()
		if u == "" || c == "" {
			return nil
		}
		return Msg{"type": "chat", "user": u, "user_id": userID(pb.GetUser()), "content": c}

	case "WebcastGiftMessage":
		var pb livepb.GiftMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		group, repeat, combo, total, wireEnd := scanGiftCountFields(payload)
		if combo == 0 {
			combo = pb.GetComboCount()
		}
		repeatEnd := wireEnd
		if repeatEnd == 0 {
			repeatEnd = pb.GetRepeatEnd()
		}
		u := userName(pb.GetUser())
		uid := userID(pb.GetUser())
		gname := ""
		var gid int64
		if pb.GetGift() != nil {
			gname = pb.GetGift().GetName()
			gid = pb.GetGift().GetId()
		}
		if u == "" || gname == "" {
			return nil
		}
		instant := settleGiftCount(group, repeat, combo, total, 0)
		key := giftComboKey(uid, pb.GetGiftId(), gname, gid)
		if repeatEnd != 1 {
			// Intermediate combo tick — remember peak, wait for repeatEnd.
			noteGiftComboPeak(key, instant)
			return nil
		}
		count := settleGiftCount(group, repeat, combo, total, takeGiftComboPeak(key))
		return Msg{
			"type": "gift", "user": u, "user_id": uid,
			"gift": gname, "gift_id": gid, "count": count, "repeat_end": 1,
		}

	case "WebcastLikeMessage":
		var pb livepb.LikeMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		u := userName(pb.GetUser())
		if u == "" {
			return nil
		}
		c := pb.GetCount()
		if c == 0 {
			c = 1
		}
		return Msg{"type": "like", "user": u, "user_id": userID(pb.GetUser()), "count": c}

	case "WebcastMemberMessage":
		var pb livepb.MemberMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		u := userName(pb.GetUser())
		if u == "" {
			return nil
		}
		return Msg{"type": "enter", "user": u, "user_id": userID(pb.GetUser())}

	case "WebcastSocialMessage":
		var pb livepb.SocialMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		u := userName(pb.GetUser())
		if u == "" {
			return nil
		}
		return Msg{
			"type": "follow", "user": u, "user_id": userID(pb.GetUser()),
			"action": pb.GetAction(), "share_type": pb.GetShareType(),
			"share_target": pb.GetShareTarget(), "follow_count": pb.GetFollowCount(),
		}

	case "WebcastRoomUserSeqMessage":
		var pb livepb.RoomUserSeqMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		totalPv, _ := strconv.ParseInt(pb.GetTotalPvForAnchor(), 10, 64)
		return Msg{"type": "online", "current": pb.GetTotal(), "total": totalPv}

	case "WebcastFansclubMessage":
		var pb livepb.FansclubMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		return Msg{
			"type": "fansclub", "user": userName(pb.GetUser()),
			"user_id": userID(pb.GetUser()), "content": pb.GetContent(),
		}

	case "WebcastEmojiChatMessage":
		var pb livepb.EmojiChatMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		return Msg{
			"type": "emoji", "user": userName(pb.GetUser()), "user_id": userID(pb.GetUser()),
			"emoji_id": strconv.FormatInt(pb.GetEmojiId(), 10), "default_content": pb.GetDefaultContent(),
		}

	case "WebcastRoomStatsMessage":
		var pb livepb.RoomStatsMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		return Msg{
			"type":         "room_stats",
			"display_long": pb.GetDisplayLong(), "display_short": pb.GetDisplayShort(),
			"display_middle": pb.GetDisplayMiddle(), "display_value": pb.GetDisplayValue(),
			"total": pb.GetTotal(), "display_type": pb.GetDisplayType(),
		}

	case "WebcastRoomRankMessage":
		var pb livepb.RoomRankMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		ranks := make([]any, 0, len(pb.GetRanksList()))
		for _, r := range pb.GetRanksList() {
			ranks = append(ranks, map[string]any{
				"user": userName(r.GetUser()), "user_id": userID(r.GetUser()), "score": r.GetScoreStr(),
			})
		}
		return Msg{"type": "rank", "ranks": ranks}

	case "WebcastControlMessage":
		var pb livepb.ControlMessage
		if err := proto.Unmarshal(payload, &pb); err != nil {
			return nil
		}
		return Msg{"type": "control", "status": pb.GetStatus()}
	}
	return nil
}

func gunzip(b []byte) []byte {
	r, err := gzip.NewReader(bytes.NewReader(b))
	if err != nil {
		return b
	}
	defer r.Close()
	out, err := io.ReadAll(r)
	if err != nil {
		return b
	}
	return out
}

// TryParseFrame mirrors listener.LiveProtobuf.try_parse_frame.
func TryParseFrame(payload []byte) (ok bool, msgs []Msg) {
	var frame livepb.PushFrame
	if err := proto.Unmarshal(payload, &frame); err != nil {
		return false, nil
	}
	if len(frame.GetPayload()) == 0 {
		return false, nil
	}
	body := gunzip(frame.GetPayload())
	var resp livepb.LiveResponse
	if err := proto.Unmarshal(body, &resp); err != nil {
		return false, nil
	}
	for _, item := range resp.GetMessagesList() {
		if m := ParseItem(item.GetMethod(), item.GetPayload()); m != nil {
			msgs = append(msgs, m)
		}
	}
	return true, msgs
}
