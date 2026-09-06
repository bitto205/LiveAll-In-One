package core

import "sync"

type Danmu struct {
	mu      sync.Mutex
	active  bool
	s       DanmuSettings
	likeAcc map[string]int
}

func NewDanmu() *Danmu {
	return &Danmu{
		s: DanmuSettings{
			ChatOn: true, GiftOn: true, FollowOn: true, LikeOn: true,
			LikeThreshold: 1,
		},
		likeAcc: map[string]int{},
	}
}

func (f *Danmu) Active() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.active
}

func (f *Danmu) SetActive(v bool) {
	f.mu.Lock()
	f.active = v
	if !v {
		f.likeAcc = map[string]int{}
	}
	f.mu.Unlock()
}

func (f *Danmu) Set(s DanmuSettings) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.s = s
}

// Accept returns a danmu.show payload or nil.
func (f *Danmu) Accept(msg map[string]any, giftDiamonds int) map[string]any {
	f.mu.Lock()
	defer f.mu.Unlock()
	if !f.active {
		return nil
	}
	t, _ := msg["type"].(string)
	user, _ := msg["user"].(string)
	switch t {
	case "chat":
		if !f.s.ChatOn {
			return nil
		}
		content, _ := msg["content"].(string)
		return map[string]any{"op": "danmu.show", "kind": "chat", "user": user, "text": content}
	case "gift":
		if !f.s.GiftOn {
			return nil
		}
		if giftDiamonds < f.s.GiftMinDiamonds {
			return nil
		}
		gift, _ := msg["gift"].(string)
		return map[string]any{"op": "danmu.show", "kind": "gift", "user": user, "text": gift, "gift": gift}
	case "follow":
		if !f.s.FollowOn {
			return nil
		}
		return map[string]any{"op": "danmu.show", "kind": "follow", "user": user, "text": "关注了"}
	case "like":
		if !f.s.LikeOn {
			return nil
		}
		count := toInt(msg["count"])
		uid, _ := msg["user_id"].(string)
		if uid == "" {
			uid = user
		}
		if f.s.LikeAccumulate {
			f.likeAcc[uid] += count
			if f.likeAcc[uid] < f.s.LikeThreshold {
				return nil
			}
			count = f.likeAcc[uid]
			f.likeAcc[uid] = 0
		} else if count < f.s.LikeThreshold {
			return nil
		}
		return map[string]any{"op": "danmu.show", "kind": "like", "user": user, "text": "点了赞", "count": count}
	}
	return nil
}

func toInt(v any) int {
	switch n := v.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	default:
		return 0
	}
}
