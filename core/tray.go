//go:build windows

package core

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	shell32           = windows.NewLazySystemDLL("shell32.dll")
	user32            = windows.NewLazySystemDLL("user32.dll")
	procShellNotify   = shell32.NewProc("Shell_NotifyIconW")
	procLoadImage     = user32.NewProc("LoadImageW")
	procCreatePopup   = user32.NewProc("CreatePopupMenu")
	procAppendMenu    = user32.NewProc("AppendMenuW")
	procTrackPopup    = user32.NewProc("TrackPopupMenu")
	procDestroyMenu   = user32.NewProc("DestroyMenu")
	procDefWindowProc = user32.NewProc("DefWindowProcW")
	procRegisterClass = user32.NewProc("RegisterClassExW")
	procCreateWindow  = user32.NewProc("CreateWindowExW")
	procGetMessage    = user32.NewProc("GetMessageW")
	procTranslate     = user32.NewProc("TranslateMessage")
	procDispatch      = user32.NewProc("DispatchMessageW")
	procPostQuit      = user32.NewProc("PostQuitMessage")
	procSetForeground = user32.NewProc("SetForegroundWindow")
	procGetCursorPos  = user32.NewProc("GetCursorPos")
	procShowWindow    = user32.NewProc("ShowWindow")
	procPostMessage   = user32.NewProc("PostMessageW")
)

const (
	nimAdd     = 0x00000000
	nimModify  = 0x00000001
	nimDelete  = 0x00000002
	nifMessage = 0x00000001
	nifIcon    = 0x00000002
	nifTip     = 0x00000004

	wmApp        = 0x8000
	wmTray       = wmApp + 1
	wmLButtonUp  = 0x0202
	wmLButtonDbl = 0x0203
	wmRButtonUp  = 0x0205
	wmCommand    = 0x0111
	wmClose      = 0x0010
	wmDestroy    = 0x0002

	idShowUI         = 1001
	idQuit           = 1002
	idDanmuToggle    = 1101
	idDanmuFrame     = 1102
	idDanmuLock      = 1103
	idOvertimeToggle = 1201
	idOvertimeFrame  = 1202
	idOvertimeLock   = 1203
	idLeafToggle     = 1301
	idLeafFrame      = 1302
	idLeafLock       = 1303

	tpmRightButton = 0x0002
	mfGray         = 0x0001
	mfPopup        = 0x0010
	mfSeparator    = 0x0800
	wsExToolwindow = 0x00000080
	wsPopup        = 0x80000000
	swHide         = 0
)

// Vista+ NOTIFYICONDATAW. HWND_MESSAGE windows cannot host tray icons.
type notifyIconData struct {
	CbSize           uint32
	HWnd             windows.Handle
	UID              uint32
	UFlags           uint32
	UCallbackMessage uint32
	HIcon            windows.Handle
	SzTip            [128]uint16
	DwState          uint32
	DwStateMask      uint32
	SzInfo           [256]uint16
	UTimeoutOrVer    uint32
	SzInfoTitle      [64]uint16
	DwInfoFlags      uint32
	GuidItem         [16]byte
	HBalloonIcon     windows.Handle
}

type wndClassEx struct {
	CbSize        uint32
	Style         uint32
	LpfnWndProc   uintptr
	CbClsExtra    int32
	CbWndExtra    int32
	HInstance     windows.Handle
	HIcon         windows.Handle
	HCursor       windows.Handle
	HbrBackground windows.Handle
	LpszMenuName  *uint16
	LpszClassName *uint16
	HIconSm       windows.Handle
}

type point struct{ X, Y int32 }

type msg struct {
	HWnd    windows.Handle
	Message uint32
	WParam  uintptr
	LParam  uintptr
	Time    uint32
	Pt      point
}

// Config for native tray.
type TrayConfig struct {
	Root         string
	Tooltip      string
	OnShowUI     func()
	OnOverlay    func(tool, action string)
	OverlayState func(tool string) int
	OnQuit       func()
}

var (
	cfgMu sync.Mutex
	cfg   TrayConfig
	hwnd  windows.Handle
)

func utf16Tip(s string) (out [128]uint16) {
	u, _ := windows.UTF16FromString(s)
	n := len(u)
	if n > 127 {
		n = 127
	}
	copy(out[:], u[:n])
	return
}

func loadAppIcon(root string) windows.Handle {
	candidates := []string{
		filepath.Join(root, "resources", "image", "icon.ico"),
		filepath.Join(root, "resources", "image", "icon.png"),
	}
	for _, p := range candidates {
		if _, err := os.Stat(p); err != nil {
			continue
		}
		pathPtr, _ := windows.UTF16PtrFromString(p)
		r, _, _ := procLoadImage.Call(
			0,
			uintptr(unsafe.Pointer(pathPtr)),
			1, // IMAGE_ICON
			0, 0,
			0x00000010, // LR_LOADFROMFILE
		)
		if r != 0 {
			return windows.Handle(r)
		}
	}
	// IDI_APPLICATION
	r, _, _ := user32.NewProc("LoadIconW").Call(0, 32512)
	return windows.Handle(r)
}

func wndProc(h windows.Handle, msgU uint32, w, l uintptr) uintptr {
	// 回调线程上的 panic 会进 Windows SEH / winthrow，拖死进程。
	defer func() { _ = recover() }()
	switch msgU {
	case wmTray:
		switch l {
		case wmLButtonUp, wmLButtonDbl:
			// 单击或双击都打开界面（仅双击时用户会感觉「托盘按钮失效」）。
			cfgMu.Lock()
			fn := cfg.OnShowUI
			cfgMu.Unlock()
			if fn != nil {
				go fn()
			}
		case wmRButtonUp:
			showMenu(h)
		}
		return 0
	case wmCommand:
		switch w & 0xffff {
		case idShowUI:
			cfgMu.Lock()
			fn := cfg.OnShowUI
			cfgMu.Unlock()
			if fn != nil {
				go fn()
			}
		case idQuit:
			cfgMu.Lock()
			fn := cfg.OnQuit
			cfgMu.Unlock()
			removeIcon(h)
			if fn != nil {
				go fn()
			}
			procPostQuit.Call(0)
		case idDanmuToggle:
			runOverlayCommand("danmu", "toggle")
		case idDanmuFrame:
			runOverlayCommand("danmu", "frame.toggle")
		case idDanmuLock:
			runOverlayCommand("danmu", "lock.toggle")
		case idOvertimeToggle:
			runOverlayCommand("overtime", "toggle")
		case idOvertimeFrame:
			runOverlayCommand("overtime", "frame.toggle")
		case idOvertimeLock:
			runOverlayCommand("overtime", "lock.toggle")
		case idLeafToggle:
			runOverlayCommand("leaf", "toggle")
		case idLeafFrame:
			runOverlayCommand("leaf", "frame.toggle")
		case idLeafLock:
			runOverlayCommand("leaf", "lock.toggle")
		}
		return 0
	case wmClose:
		removeIcon(h)
		procPostQuit.Call(0)
		return 0
	case wmDestroy:
		removeIcon(h)
		procPostQuit.Call(0)
		return 0
	}
	r, _, _ := procDefWindowProc.Call(uintptr(h), uintptr(msgU), w, l)
	return r
}

func runOverlayCommand(tool, action string) {
	cfgMu.Lock()
	fn := cfg.OnOverlay
	cfgMu.Unlock()
	if fn != nil {
		go fn(tool, action)
	}
}

func currentOverlayState(tool string) int {
	cfgMu.Lock()
	fn := cfg.OverlayState
	cfgMu.Unlock()
	if fn == nil {
		return 0
	}
	return fn(tool)
}

func appendOverlaySubmenu(parent uintptr, title, tool string, toggleID, frameID, lockID uintptr) {
	sub, _, _ := procCreatePopup.Call()
	if sub == 0 {
		return
	}
	state := currentOverlayState(tool)
	open := state&1 != 0
	frameShown := state&2 != 0
	locked := state&4 != 0

	toggleText := "打开悬浮窗"
	if open {
		toggleText = "关闭悬浮窗"
	}
	frameText := "显示边框"
	if frameShown {
		frameText = "隐藏边框"
	}
	lockText := "锁定窗口"
	if locked {
		lockText = "解锁窗口"
	}
	toggle, _ := windows.UTF16PtrFromString(toggleText)
	frame, _ := windows.UTF16PtrFromString(frameText)
	lock, _ := windows.UTF16PtrFromString(lockText)
	frameFlags := uintptr(0)
	lockFlags := uintptr(0)
	if !open {
		frameFlags |= mfGray
	}
	if !open {
		lockFlags |= mfGray
	}
	procAppendMenu.Call(sub, 0, toggleID, uintptr(unsafe.Pointer(toggle)))
	if !locked {
		procAppendMenu.Call(sub, frameFlags, frameID, uintptr(unsafe.Pointer(frame)))
	}
	procAppendMenu.Call(sub, lockFlags, lockID, uintptr(unsafe.Pointer(lock)))

	label, _ := windows.UTF16PtrFromString(title)
	procAppendMenu.Call(parent, mfPopup, sub, uintptr(unsafe.Pointer(label)))
}

func showMenu(h windows.Handle) {
	menu, _, _ := procCreatePopup.Call()
	if menu == 0 {
		return
	}
	defer procDestroyMenu.Call(menu)
	appendOverlaySubmenu(menu, "弹幕机", "danmu",
		idDanmuToggle, idDanmuFrame, idDanmuLock)
	appendOverlaySubmenu(menu, "加班机", "overtime",
		idOvertimeToggle, idOvertimeFrame, idOvertimeLock)
	appendOverlaySubmenu(menu, "捡叶子", "leaf",
		idLeafToggle, idLeafFrame, idLeafLock)
	procAppendMenu.Call(menu, mfSeparator, 0, 0)
	show, _ := windows.UTF16PtrFromString("打开界面")
	quit, _ := windows.UTF16PtrFromString("退出")
	procAppendMenu.Call(menu, 0, idShowUI, uintptr(unsafe.Pointer(show)))
	procAppendMenu.Call(menu, 0, idQuit, uintptr(unsafe.Pointer(quit)))

	var pt point
	procGetCursorPos.Call(uintptr(unsafe.Pointer(&pt)))
	procSetForeground.Call(uintptr(h))
	procTrackPopup.Call(menu, tpmRightButton, uintptr(pt.X), uintptr(pt.Y), 0, uintptr(h), 0)
}

func addIcon(h windows.Handle, tip string, icon windows.Handle) error {
	if icon == 0 {
		return fmt.Errorf("tray icon handle is null")
	}
	var nid notifyIconData
	nid.CbSize = uint32(unsafe.Sizeof(nid))
	nid.HWnd = h
	nid.UID = 1
	nid.UFlags = nifMessage | nifIcon | nifTip
	nid.UCallbackMessage = wmTray
	nid.HIcon = icon
	nid.SzTip = utf16Tip(tip)
	ok, _, err := procShellNotify.Call(nimAdd, uintptr(unsafe.Pointer(&nid)))
	if ok == 0 {
		return fmt.Errorf("Shell_NotifyIcon NIM_ADD: %v", err)
	}
	return nil
}

func removeIcon(h windows.Handle) {
	var nid notifyIconData
	nid.CbSize = uint32(unsafe.Sizeof(nid))
	nid.HWnd = h
	nid.UID = 1
	procShellNotify.Call(nimDelete, uintptr(unsafe.Pointer(&nid)))
}

// Run blocks on the Win32 message loop (call from main / dedicated OS thread).
func RunTray(c TrayConfig) error {
	runtime.LockOSThread()
	cfgMu.Lock()
	cfg = c
	cfgMu.Unlock()

	hInst := windows.Handle(0)
	r0, _, _ := windows.NewLazySystemDLL("kernel32.dll").NewProc("GetModuleHandleW").Call(0)
	if r0 != 0 {
		hInst = windows.Handle(r0)
	}

	className, _ := windows.UTF16PtrFromString("LiveAIOCoreTrayWnd")
	var wcx wndClassEx
	wcx.CbSize = uint32(unsafe.Sizeof(wcx))
	wcx.LpfnWndProc = syscall.NewCallback(wndProc)
	wcx.HInstance = hInst
	wcx.LpszClassName = className
	atom, _, regErr := procRegisterClass.Call(uintptr(unsafe.Pointer(&wcx)))
	if atom == 0 {
		// already registered is ok
		if errno, ok := regErr.(syscall.Errno); !ok || errno != 1410 { // ERROR_CLASS_ALREADY_EXISTS
			if regErr != windows.ERROR_CLASS_ALREADY_EXISTS && regErr != syscall.Errno(1410) {
				// still try create; some hosts return confusing errs
			}
		}
	}

	title, _ := windows.UTF16PtrFromString("LiveAIO")
	// Must be a real top-level HWND. HWND_MESSAGE is ignored by the notification area.
	h, _, createErr := procCreateWindow.Call(
		wsExToolwindow,
		uintptr(unsafe.Pointer(className)),
		uintptr(unsafe.Pointer(title)),
		wsPopup,
		0, 0, 0, 0,
		0,
		0,
		uintptr(hInst),
		0,
	)
	if h == 0 {
		return fmt.Errorf("CreateWindowEx tray: %v", createErr)
	}
	cfgMu.Lock()
	hwnd = windows.Handle(h)
	cfgMu.Unlock()
	procShowWindow.Call(h, swHide)
	icon := loadAppIcon(c.Root)
	tip := c.Tooltip
	if tip == "" {
		tip = "LiveAIO"
	}
	if err := addIcon(hwnd, tip, icon); err != nil {
		return err
	}

	var m msg
	for {
		r, _, _ := procGetMessage.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
		if int32(r) <= 0 {
			break
		}
		procTranslate.Call(uintptr(unsafe.Pointer(&m)))
		procDispatch.Call(uintptr(unsafe.Pointer(&m)))
	}
	cfgMu.Lock()
	hwnd = 0
	cfgMu.Unlock()
	return nil
}

// QuitTray asks the tray message loop to exit. Safe from any goroutine:
// PostQuitMessage would target the caller thread, so we post WM_CLOSE to the tray HWND.
func QuitTray() {
	cfgMu.Lock()
	h := hwnd
	cfgMu.Unlock()
	if h == 0 {
		return
	}
	procPostMessage.Call(uintptr(h), wmClose, 0, 0)
}
