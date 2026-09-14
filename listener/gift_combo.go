package listener

import (
	"strconv"
	"sync"

	"google.golang.org/protobuf/encoding/protowire"
)

// Douyin gift bursts often emit intermediate WebcastGiftMessage frames with rising
// combo/repeat counts, then a final repeatEnd=1 frame that resets comboCount to 1.
// Track the peak until settle (mirrors browser.js __DY_GIFT_COMBO__).
var (
	giftComboMu   sync.Mutex
	giftComboPeak = map[string]int64{}
)

func giftComboKey(userID, giftID string, giftName string, gid int64) string {
	if userID == "" {
		userID = "?"
	}
	if giftID != "" {
		return userID + "|" + giftID
	}
	if gid != 0 {
		return userID + "|" + strconv.FormatInt(gid, 10)
	}
	return userID + "|" + giftName
}

func noteGiftComboPeak(key string, n int64) {
	if n < 1 {
		n = 1
	}
	giftComboMu.Lock()
	if n > giftComboPeak[key] {
		giftComboPeak[key] = n
	}
	giftComboMu.Unlock()
}

func takeGiftComboPeak(key string) int64 {
	giftComboMu.Lock()
	n := giftComboPeak[key]
	delete(giftComboPeak, key)
	giftComboMu.Unlock()
	return n
}

func maxInt64(vals ...int64) int64 {
	var m int64
	for _, v := range vals {
		if v > m {
			m = v
		}
	}
	return m
}

// scanGiftCountFields reads group/repeat/combo/total/repeatEnd from the raw
// GiftMessage wire. Live.pb GiftMessage historically omitted groupCount(4) /
// repeatCount(5) / totalCount(29); batch sends (e.g. 10×小心心) put the size in
// those fields while comboCount stays 1.
func scanGiftCountFields(payload []byte) (group, repeat, combo, total int64, repeatEnd uint32) {
	b := payload
	for len(b) > 0 {
		num, typ, n := protowire.ConsumeTag(b)
		if n < 0 {
			break
		}
		b = b[n:]
		switch typ {
		case protowire.VarintType:
			v, n := protowire.ConsumeVarint(b)
			if n < 0 {
				return
			}
			b = b[n:]
			switch num {
			case 4:
				group = int64(v)
			case 5:
				repeat = int64(v)
			case 6:
				combo = int64(v)
			case 9:
				repeatEnd = uint32(v)
			case 29:
				total = int64(v)
			}
		default:
			n := protowire.ConsumeFieldValue(num, typ, b)
			if n < 0 {
				return
			}
			b = b[n:]
		}
	}
	return
}

func settleGiftCount(group, repeat, combo, total, peak int64) int64 {
	n := maxInt64(group, repeat, combo, total, peak)
	if n < 1 {
		return 1
	}
	return n
}
