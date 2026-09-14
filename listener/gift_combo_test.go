package listener

import "testing"

func TestSettleGiftCountBatch(t *testing.T) {
	// 一次性送 10：groupCount=10, comboCount=1 on the end frame.
	if n := settleGiftCount(10, 1, 1, 0, 0); n != 10 {
		t.Fatalf("batch: got %d", n)
	}
}

func TestSettleGiftCountComboPeak(t *testing.T) {
	// Combo spam: peak 10 from intermediates, end frame combo=1.
	if n := settleGiftCount(0, 0, 1, 0, 10); n != 10 {
		t.Fatalf("peak: got %d", n)
	}
}

func TestGiftComboPeakTracker(t *testing.T) {
	key := giftComboKey("u1", "", "小心心", 1)
	noteGiftComboPeak(key, 3)
	noteGiftComboPeak(key, 7)
	noteGiftComboPeak(key, 5)
	if n := takeGiftComboPeak(key); n != 7 {
		t.Fatalf("peak=%d", n)
	}
	if n := takeGiftComboPeak(key); n != 0 {
		t.Fatalf("cleared peak=%d", n)
	}
}

func TestScanGiftCountFields(t *testing.T) {
	// Encoded GiftMessage-ish: groupCount=10 (field 4), comboCount=1 (field 6), repeatEnd=1 (field 9).
	// Hand-built protobuf varints.
	payload := []byte{
		0x20, 0x0a, // field 4, varint 10
		0x30, 0x01, // field 6, varint 1
		0x48, 0x01, // field 9, varint 1
	}
	group, repeat, combo, total, end := scanGiftCountFields(payload)
	if group != 10 || combo != 1 || end != 1 || repeat != 0 || total != 0 {
		t.Fatalf("scan: group=%d repeat=%d combo=%d total=%d end=%d", group, repeat, combo, total, end)
	}
	if n := settleGiftCount(group, repeat, combo, total, 0); n != 10 {
		t.Fatalf("settle scanned batch: %d", n)
	}
}
