//go:build windows

package listener

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

func scanCompanionInstallDir() string {
	hives := []registry.Key{registry.LOCAL_MACHINE, registry.CURRENT_USER}
	subkeys := []string{
		`SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`,
		`SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall`,
	}
	for _, hive := range hives {
		for _, sub := range subkeys {
			k, err := registry.OpenKey(hive, sub, registry.ENUMERATE_SUB_KEYS|registry.QUERY_VALUE)
			if err != nil {
				continue
			}
			names, _ := k.ReadSubKeyNames(-1)
			for _, name := range names {
				sk, err := registry.OpenKey(k, name, registry.QUERY_VALUE)
				if err != nil {
					continue
				}
				dn, _, _ := sk.GetStringValue("DisplayName")
				loc, _, _ := sk.GetStringValue("InstallLocation")
				sk.Close()
				if strings.Contains(dn, "\u76f4\u64ad\u4f34\u4fa3") && loc != "" {
					if st, err := os.Stat(loc); err == nil && st.IsDir() {
						k.Close()
						return loc
					}
				}
			}
			k.Close()
		}
	}
	return ""
}

func getSystemProxy() (proxySnapshot, error) {
	k, err := registry.OpenKey(registry.CURRENT_USER,
		`Software\Microsoft\Windows\CurrentVersion\Internet Settings`, registry.QUERY_VALUE)
	if err != nil {
		return proxySnapshot{}, err
	}
	defer k.Close()
	enable, _, _ := k.GetIntegerValue("ProxyEnable")
	server, _, _ := k.GetStringValue("ProxyServer")
	return proxySnapshot{Enable: enable != 0, Server: server}, nil
}

func setSystemProxy(server string, enable bool) error {
	k, err := registry.OpenKey(registry.CURRENT_USER,
		`Software\Microsoft\Windows\CurrentVersion\Internet Settings`, registry.SET_VALUE)
	if err != nil {
		return err
	}
	defer k.Close()
	var en uint32
	if enable {
		en = 1
	}
	if err := k.SetDWordValue("ProxyEnable", en); err != nil {
		return err
	}
	if server != "" {
		if err := k.SetStringValue("ProxyServer", server); err != nil {
			return err
		}
	}
	return nil
}

func restoreSystemProxy(prev proxySnapshot) error {
	if err := setSystemProxy(prev.Server, prev.Enable); err != nil {
		return fmt.Errorf("restore proxy: %w", err)
	}
	return nil
}

func hideCmd(cmd *exec.Cmd) {
	if cmd == nil {
		return
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
}

// chromeJob wraps a Windows Job Object with KILL_ON_JOB_CLOSE so the whole
// chrome-headless-shell process tree dies when the job handle is closed (or
// when LiveAIO/Core exits and the OS closes leftover handles).
type chromeJob struct {
	handle windows.Handle
}

func newChromeJob() (*chromeJob, error) {
	// Kill-on-close only. A hard ~450MB JOB_MEMORY cap was aborting chrome
	// during Douyin room boot (enter_reqs=0 / no WSS). Phase-2 fetch trim
	// still cuts RAM after the push socket is up.
	return newChromeJobWithLimits(0)
}

func newChromeJobWithLimits(jobMemBytes uintptr) (*chromeJob, error) {
	h, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		return nil, err
	}
	var info windows.JOBOBJECT_EXTENDED_LIMIT_INFORMATION
	info.BasicLimitInformation.LimitFlags = windows.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
	if jobMemBytes > 0 {
		info.BasicLimitInformation.LimitFlags |= windows.JOB_OBJECT_LIMIT_JOB_MEMORY
		info.JobMemoryLimit = jobMemBytes
	}
	if _, err := windows.SetInformationJobObject(
		h,
		windows.JobObjectExtendedLimitInformation,
		uintptr(unsafe.Pointer(&info)),
		uint32(unsafe.Sizeof(info)),
	); err != nil {
		_ = windows.CloseHandle(h)
		return nil, err
	}
	return &chromeJob{handle: h}, nil
}

func (j *chromeJob) assignPID(pid int) error {
	if j == nil || j.handle == 0 || pid <= 0 {
		return nil
	}
	ph, err := windows.OpenProcess(
		windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE|windows.PROCESS_QUERY_LIMITED_INFORMATION,
		false,
		uint32(pid),
	)
	if err != nil {
		return err
	}
	defer windows.CloseHandle(ph)
	return windows.AssignProcessToJobObject(j.handle, ph)
}

func (j *chromeJob) close() {
	if j == nil || j.handle == 0 {
		return
	}
	_ = windows.CloseHandle(j.handle)
	j.handle = 0
}

func killProcessTree(pid int) {
	if pid <= 0 {
		return
	}
	// Bound taskkill: under load/debug it can hang and hold captureMu forever,
	// which made reconnect + config.set look dead after a successful disconnect.
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "taskkill", "/F", "/T", "/PID", strconv.Itoa(pid))
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	_ = cmd.Run()
}

var (
	ownedMu   sync.Mutex
	ownedPIDs = map[int]string{} // pid → exe path
)

func trackOwnedBrowser(pid int, exe string) {
	if pid <= 0 {
		return
	}
	ownedMu.Lock()
	ownedPIDs[pid] = exe
	ownedMu.Unlock()
}

func untrackOwnedBrowser(pid int) {
	if pid <= 0 {
		return
	}
	ownedMu.Lock()
	delete(ownedPIDs, pid)
	ownedMu.Unlock()
}

// ReapOwnedBrowsers force-kills tracked chrome sessions and any leftover
// bundled chrome-headless-shell.exe started from root/browsers.
func ReapOwnedBrowsers(root string) {
	ownedMu.Lock()
	snapshot := make(map[int]string, len(ownedPIDs))
	for pid, exe := range ownedPIDs {
		snapshot[pid] = exe
	}
	ownedPIDs = map[int]string{}
	ownedMu.Unlock()

	for pid := range snapshot {
		killProcessTree(pid)
	}

	bundled := findBundledExe(root)
	if bundled == "" {
		return
	}
	killMatchingImage(filepath.Clean(bundled))
}

func killMatchingImage(exePath string) {
	exePath = filepath.Clean(exePath)
	if exePath == "" {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	out, err := exec.CommandContext(
		ctx,
		"tasklist",
		"/FI", "IMAGENAME eq "+filepath.Base(exePath),
		"/FO", "CSV",
		"/NH",
	).CombinedOutput()
	if err != nil || len(out) == 0 {
		return
	}
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "INFO:") {
			continue
		}
		// "chrome-headless-shell.exe","1234","Session Name","Session#","Mem Usage"
		parts := strings.Split(line, ",")
		if len(parts) < 2 {
			continue
		}
		pidStr := strings.Trim(parts[1], "\" ")
		pid, err := strconv.Atoi(pidStr)
		if err != nil || pid <= 0 {
			continue
		}
		if !processExeEquals(pid, exePath) {
			continue
		}
		killProcessTree(pid)
	}
}

func processExeEquals(pid int, want string) bool {
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(h)
	var buf [windows.MAX_PATH]uint16
	size := uint32(len(buf))
	if err := windows.QueryFullProcessImageName(h, 0, &buf[0], &size); err != nil {
		return false
	}
	got := windows.UTF16ToString(buf[:size])
	return strings.EqualFold(filepath.Clean(got), filepath.Clean(want))
}

// attachCmdToJob watches cmd.Process after Start and assigns it to the job.
func attachCmdToJob(cmd *exec.Cmd, job *chromeJob, pidOut *int32) {
	if cmd == nil || job == nil {
		return
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if p := cmd.Process; p != nil && p.Pid > 0 {
			_ = job.assignPID(p.Pid)
			if pidOut != nil {
				atomic.StoreInt32(pidOut, int32(p.Pid))
			}
			path := cmd.Path
			if path == "" {
				path = cmd.Args[0]
			}
			trackOwnedBrowser(p.Pid, path)
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
}

func processAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	defer windows.CloseHandle(h)
	var code uint32
	if err := windows.GetExitCodeProcess(h, &code); err != nil {
		return false
	}
	const stillActive = 259
	return code == stillActive
}

func ensureCmdPath(cmd *exec.Cmd, fallback string) {
	if cmd != nil && cmd.Path == "" {
		cmd.Path = fallback
	}
}

// bindChromeChildCmd marks the process as a normal LiveAIO child (no detach /
// new process group) and hides the console window.
func bindChromeChildCmd(cmd *exec.Cmd, exe string) {
	if cmd == nil {
		return
	}
	ensureCmdPath(cmd, exe)
	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: 0, // stay in LiveAIO process tree; do not CREATE_NEW_PROCESS_GROUP
	}
}
