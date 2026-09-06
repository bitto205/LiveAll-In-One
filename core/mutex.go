package core

import "golang.org/x/sys/windows"

const launcherMutexName = `Local\LiveAIO-launcher`

var launcherMu windows.Handle

// TryHoldLauncher is single-instance for the shell/supervisor process.
// False = another LiveAIO already owns the process lifetime.
func TryHoldLauncher() bool {
	name, err := windows.UTF16PtrFromString(launcherMutexName)
	if err != nil {
		return false
	}
	h, err := windows.CreateMutex(nil, true, name)
	if err != nil {
		return false
	}
	if windows.GetLastError() == windows.ERROR_ALREADY_EXISTS {
		_ = windows.CloseHandle(h)
		return false
	}
	launcherMu = h
	return true
}

func ReleaseLauncher() {
	if launcherMu == 0 {
		return
	}
	_ = windows.ReleaseMutex(launcherMu)
	_ = windows.CloseHandle(launcherMu)
	launcherMu = 0
}
