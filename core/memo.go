package core

import (
	"fmt"
	"sync"
)

type Memo struct {
	mu     sync.Mutex
	active bool
	s      MemoSettings
}

func NewMemo() *Memo {
	return &Memo{s: MemoSettings{
		GiftEnabled: true, FollowEnabled: true, LikeEnabled: true,
	}}
}

func (f *Memo) Active() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.active
}

func (f *Memo) SetActive(v bool) {
	f.mu.Lock()
	f.active = v
	f.mu.Unlock()
}

func (f *Memo) Set(s MemoSettings) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.s = s
}

func (f *Memo) Accept(msg map[string]any, giftDiamonds int) map[string]any {
	f.mu.Lock()
	defer f.mu.Unlock()
	if !f.active {
		return nil
	}
	t, _ := msg["type"].(string)
	user, _ := msg["user"].(string)
	uid, _ := msg["user_id"].(string)
	switch t {
	case "gift":
		if !f.s.GiftEnabled {
			return nil
		}
		if giftDiamonds < f.s.GiftMinDiamonds {
			return nil
		}
		gift, _ := msg["gift"].(string)
		count := toInt(msg["count"])
		key := fmt.Sprintf("gift:%s:%s", uid, gift)
		if !f.s.GiftStack {
			key = fmt.Sprintf("gift:%s:%s:%d", uid, gift, count)
		}
		return map[string]any{
			"op": "memo.item", "kind": "gift", "user": user, "user_id": uid,
			"text": fmt.Sprintf("送出%s x%d", gift, count), "stack_key": key, "stack": f.s.GiftStack,
		}
	case "follow":
		if !f.s.FollowEnabled {
			return nil
		}
		return map[string]any{
			"op": "memo.item", "kind": "follow", "user": user, "user_id": uid,
			"text": "关注了主播", "stack_key": fmt.Sprintf("follow:%s", uid),
		}
	case "like":
		if !f.s.LikeEnabled {
			return nil
		}
		count := toInt(msg["count"])
		key := fmt.Sprintf("like:%s", uid)
		if !f.s.LikeStack {
			key = fmt.Sprintf("like:%s:%d", uid, count)
		}
		return map[string]any{
			"op": "memo.item", "kind": "like", "user": user, "user_id": uid,
			"text": fmt.Sprintf("点了%d个赞", count), "stack_key": key, "stack": f.s.LikeStack,
		}
	case "fansclub":
		if !f.s.FollowEnabled {
			return nil
		}
		content, _ := msg["content"].(string)
		return map[string]any{
			"op": "memo.item", "kind": "fansclub", "user": user, "user_id": uid,
			"text": content, "stack_key": fmt.Sprintf("fansclub:%s", uid),
		}
	}
	return nil
}
