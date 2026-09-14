package listener

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	"liveaio/util/connectdiag"
)

// Route3Driver: bundled proxy_shell MITM + temporary system proxy.
// Manual lifecycle: starts on connect, ends only on disconnect or route mutex.
type Route3Driver struct{}

func (Route3Driver) ID() ID { return Route3 }

func (d Route3Driver) Run(ctx context.Context, p Params) error {
	logf := p.Logf
	if logf == nil {
		logf = func(string, ...any) {}
	}

	shell := bundledShell(p.Root)
	if _, err := os.Stat(shell); err != nil {
		return fmt.Errorf("proxy_shell.exe missing: %w", err)
	}
	prev, err := getSystemProxy()
	if err != nil {
		return err
	}
	if err := setSystemProxy(fmt.Sprintf("127.0.0.1:%d", proxyPort), true); err != nil {
		return fmt.Errorf("set system proxy: %w", err)
	}
	defer func() { _ = restoreSystemProxy(prev) }()

	cmd := exec.CommandContext(ctx, shell)
	cmd.Dir = filepath.Dir(shell)
	hideCmd(cmd)
	if err := cmd.Start(); err != nil {
		return err
	}
	defer func() {
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
		}
	}()

	deadline := time.Now().Add(8 * time.Second)
	for time.Now().Before(deadline) {
		if connectdiag.ProxyShellPortsOpen() {
			break
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(200 * time.Millisecond):
		}
	}
	if !connectdiag.ProxyShellPortsOpen() {
		return fmt.Errorf("proxy_shell 未就绪")
	}

	errCh := make(chan error, 1)
	cl := &Shell{
		PlainErrors: true,
		OnCtrl: func(ctrl string) {
			switch ctrl {
			case CtrlLiveOn, CtrlWSOpen, CtrlWSConnected:
				logf("route3 live on", "ctrl", ctrl)
				if p.OnStatus != nil {
					p.OnStatus(true)
				}
			case CtrlLiveOff, CtrlWSDown:
				logf("route3 live off", "ctrl", ctrl)
				if p.OnStatus != nil {
					p.OnStatus(false)
				}
			}
		},
		OnFrame: func(raw []byte) {
			if p.OnFrame != nil {
				p.OnFrame(raw)
			}
		},
		OnErr: func(err error) {
			logf("route3 shell err", "err", err)
			if p.OnStatus != nil {
				p.OnStatus(false)
			}
			select {
			case errCh <- err:
			default:
			}
		},
	}
	if err := cl.Start(); err != nil {
		return err
	}
	defer cl.Stop()

	logf("route3 lifecycle on")
	if p.OnStatus != nil {
		p.OnStatus(true)
	}

	select {
	case <-ctx.Done():
		logf("route3 lifecycle off")
		if p.OnStatus != nil {
			p.OnStatus(false)
		}
		return ctx.Err()
	case err := <-errCh:
		logf("route3 lifecycle off", "err", err)
		if p.OnStatus != nil {
			p.OnStatus(false)
		}
		return err
	}
}
