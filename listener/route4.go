package listener

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"liveaio/util/connectdiag"
)

const (
	ipcPort   = connectdiag.ProxyShellIPCPort
	proxyPort = connectdiag.ProxyShellProxyPort
	shellName = connectdiag.ProxyShellName

	DefaultIPCAddr  = "127.0.0.1:19098"
	CtrlPrefix      = "__LH_CTRL__:"
	CtrlLiveOn      = "LIVE_ON_AIR:true"
	CtrlLiveOff     = "LIVE_ON_AIR:false"
	CtrlWSOpen      = "WS_OPEN"
	CtrlWSConnected = "WS_CONNECTED"
	CtrlWSDown      = "WS_DISCONNECTED"

	ipcQueryLiveOnAir     = "__LH_QUERY__:LIVE_ON_AIR"
	ipcReplyLiveOnAirPref = "__LH_REPLY__:LIVE_ON_AIR:"

	// Shell dial after PrepareR4 already saw ports; keep this short.
	shellDialTimeout = 8 * time.Second
	// One-shot live query (mirrors old listener4.query_live_on_air).
	liveQueryTimeout = 3 * time.Second
)

type catalog struct {
	IndexPath   string `json:"index_path"`
	Spawn       string `json:"spawn"`
	ProxyMode   string `json:"proxy_mode"`
	ProxyInject string `json:"proxy_inject"`
}

type R4 struct{}

func (R4) ID() ID { return Route4 }

func (R4) Run(ctx context.Context, p Params) error {
	if err := PrepareR4(p.Root); err != nil {
		return err
	}
	<-ctx.Done()
	return nil
}

// PrepareR4 checks patch catalog + proxy_shell ports. Frames via Shell IPC.
// Process/port probe only runs on connect (not on route.env page enter).
func PrepareR4(root string) error {
	if err := ensurePatched(root); err != nil {
		return err
	}
	if connectdiag.ProxyShellPortsOpen() {
		return nil
	}
	// Companion not started: fail immediately — do not wait 5s or call network probes.
	st := connectdiag.InspectProxyShell()
	if !st.ProcessRunning {
		return connectdiag.ErrProxyShellConnect(fmt.Errorf("proxy_shell 未运行"))
	}
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if connectdiag.ProxyShellPortsOpen() {
			return nil
		}
		time.Sleep(250 * time.Millisecond)
	}
	return connectdiag.ErrProxyShellConnect(fmt.Errorf("proxy_shell 端口未就绪"))
}

func health() error {
	if connectdiag.ProxyShellPortsOpen() {
		return nil
	}
	return fmt.Errorf("proxy_shell 端口未就绪")
}

// HealthOrConnectError returns a user-facing timeout error with proxy_shell diagnostics.
func HealthOrConnectError() error {
	return connectdiag.ErrProxyShellConnect(health())
}

func catalogPath() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".liveaio", "index_patch_catalog.json")
}

func bundledShell(root string) string {
	return filepath.Join(root, "listener", "proxy_shell.exe")
}

func ensurePatched(root string) error {
	raw, err := os.ReadFile(catalogPath())
	if err != nil {
		return fmt.Errorf("companion not patched (no catalog): %w", err)
	}
	var cat catalog
	if err := json.Unmarshal(raw, &cat); err != nil || cat.IndexPath == "" {
		return fmt.Errorf("invalid patch catalog")
	}
	text, err := os.ReadFile(cat.IndexPath)
	if err != nil {
		return fmt.Errorf("read index.js: %w", err)
	}
	body := string(text)
	if cat.Spawn == "" || !strings.Contains(body, cat.Spawn) {
		return fmt.Errorf("index.js missing spawn injection")
	}
	if !spawnAnchoredToProxy(body, cat.Spawn, cat.ProxyInject) {
		return fmt.Errorf("proxy_shell spawn not anchored to proxy-server switch")
	}
	switch cat.ProxyMode {
	case "inject":
		if cat.ProxyInject == "" || !strings.Contains(body, cat.ProxyInject) {
			return fmt.Errorf("index.js missing proxy inject")
		}
	case "replace":
		if !strings.Contains(body, "127.0.0.1:19088") {
			return fmt.Errorf("index.js missing proxy-server")
		}
	default:
		return fmt.Errorf("unknown proxy_mode %q", cat.ProxyMode)
	}
	deployed := filepath.Join(filepath.Dir(cat.IndexPath), shellName)
	src := bundledShell(root)
	if _, err := os.Stat(deployed); err != nil {
		return fmt.Errorf("deployed %s missing", shellName)
	}
	// Do not hard-fail on byte mismatch: rebuilding listener/proxy_shell.exe is
	// common during dev, and the companion-side process may still be the older
	// copy while ports are healthy. UI can still offer re-patch via PageCheck.
	_ = src
	return nil
}

// Shell reads length-prefixed packets from proxy_shell IPC (routes 3/4).
type Shell struct {
	Addr        string
	PlainErrors bool // route 3: plain errors, no connectdiag
	OnCtrl      func(ctrl string)
	OnFrame     func(raw []byte)
	OnErr       func(error)

	mu     sync.Mutex
	conn   net.Conn
	stopCh chan struct{}
}

// tokenDir must match proxy_shell's aioDir(): it validates against this file.
func tokenDir() (string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	aio := filepath.Join(home, ".liveaio")
	if _, err := os.Stat(aio); err == nil {
		return aio, nil
	}
	legacy := filepath.Join(home, ".livehelper")
	if _, err := os.Stat(legacy); err == nil {
		return legacy, nil
	}
	if err := os.MkdirAll(aio, 0o755); err != nil {
		return "", err
	}
	return aio, nil
}

var tokenMu sync.Mutex

// ReadToken returns the proxy_shell IPC token, creating it on first use.
func ReadToken() (string, error) {
	tokenMu.Lock()
	defer tokenMu.Unlock()
	dir, err := tokenDir()
	if err != nil {
		return "", err
	}
	p := filepath.Join(dir, "ipc_token")
	if b, err := os.ReadFile(p); err == nil {
		if tok := strings.TrimSpace(string(b)); tok != "" {
			return tok, nil
		}
	} else if !os.IsNotExist(err) {
		return "", err
	}
	var raw [24]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	tok := hex.EncodeToString(raw[:])
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, []byte(tok+"\n"), 0o600); err != nil {
		return "", err
	}
	if err := os.Rename(tmp, p); err != nil {
		_ = os.Remove(tmp)
		return "", err
	}
	return tok, nil
}

func (c *Shell) addr() string {
	if c.Addr != "" {
		return c.Addr
	}
	return DefaultIPCAddr
}

func (c *Shell) connectFail(cause error) error {
	if c.PlainErrors {
		return cause
	}
	return connectdiag.ErrProxyShellConnect(cause)
}

func (c *Shell) Start() error {
	c.mu.Lock()
	if c.stopCh != nil {
		c.mu.Unlock()
		return fmt.Errorf("already started")
	}
	c.stopCh = make(chan struct{})
	c.mu.Unlock()

	token, err := ReadToken()
	if err != nil {
		return c.connectFail(fmt.Errorf("ipc token: %w", err))
	}

	var conn net.Conn
	deadline := time.Now().Add(shellDialTimeout)
	for {
		conn, err = net.DialTimeout("tcp", c.addr(), 2*time.Second)
		if err == nil {
			break
		}
		if time.Now().After(deadline) {
			return c.connectFail(fmt.Errorf("连接 IPC %s 超时", c.addr()))
		}
		select {
		case <-c.stopCh:
			return io.ErrClosedPipe
		case <-time.After(300 * time.Millisecond):
		}
	}

	c.mu.Lock()
	c.conn = conn
	c.mu.Unlock()

	if _, err := conn.Write([]byte(token + "\n")); err != nil {
		_ = conn.Close()
		return err
	}

	go c.readLoop(conn)
	return nil
}

// QueryLiveOnAir asks proxy_shell whether the companion room is on-air.
// Uses a one-shot IPC connection (does not start the streaming Shell).
// Mirrors old Python listener4.query_live_on_air.
func QueryLiveOnAir(addr string) (onAir bool, err error) {
	if addr == "" {
		addr = DefaultIPCAddr
	}
	token, err := ReadToken()
	if err != nil {
		return false, connectdiag.ErrProxyShellNoResponse(fmt.Errorf("ipc token: %w", err))
	}
	conn, err := net.DialTimeout("tcp", addr, 2*time.Second)
	if err != nil {
		return false, connectdiag.ErrProxyShellNoResponse(fmt.Errorf("dial IPC: %w", err))
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(liveQueryTimeout))
	if _, err := conn.Write([]byte(token + "\n")); err != nil {
		return false, connectdiag.ErrProxyShellNoResponse(fmt.Errorf("write token: %w", err))
	}
	// Must arrive within proxy_shell's 500ms query window after token.
	if _, err := conn.Write([]byte(ipcQueryLiveOnAir + "\n")); err != nil {
		return false, connectdiag.ErrProxyShellNoResponse(fmt.Errorf("write query: %w", err))
	}
	r := bufio.NewReader(conn)
	line, err := r.ReadString('\n')
	if err != nil {
		return false, connectdiag.ErrProxyShellNoResponse(fmt.Errorf("read reply: %w", err))
	}
	reply := strings.TrimSpace(line)
	if !strings.HasPrefix(reply, ipcReplyLiveOnAirPref) {
		return false, connectdiag.ErrProxyShellNoResponse(fmt.Errorf("bad reply %q", reply))
	}
	switch strings.TrimPrefix(reply, ipcReplyLiveOnAirPref) {
	case "true":
		return true, nil
	case "false":
		return false, nil
	default:
		return false, connectdiag.ErrProxyShellNoResponse(fmt.Errorf("bad reply %q", reply))
	}
}

func (c *Shell) Stop() {
	c.mu.Lock()
	if c.stopCh != nil {
		select {
		case <-c.stopCh:
		default:
			close(c.stopCh)
		}
	}
	if c.conn != nil {
		_ = c.conn.Close()
		c.conn = nil
	}
	c.mu.Unlock()
}

func (c *Shell) readLoop(conn net.Conn) {
	defer conn.Close()
	r := bufio.NewReader(conn)
	for {
		select {
		case <-c.stopCh:
			return
		default:
		}
		var hdr [4]byte
		if _, err := io.ReadFull(r, hdr[:]); err != nil {
			if c.OnErr != nil {
				if err == io.EOF {
					c.OnErr(fmt.Errorf("proxy_shell IPC closed"))
				} else {
					c.OnErr(err)
				}
			}
			return
		}
		n := binary.BigEndian.Uint32(hdr[:])
		if n == 0 || n > 32<<20 {
			if c.OnErr != nil {
				c.OnErr(fmt.Errorf("bad packet len %d", n))
			}
			return
		}
		buf := make([]byte, n)
		if _, err := io.ReadFull(r, buf); err != nil {
			if c.OnErr != nil {
				c.OnErr(err)
			}
			return
		}
		s := string(buf)
		if strings.HasPrefix(s, CtrlPrefix) {
			ctrl := strings.TrimSpace(strings.TrimPrefix(s, CtrlPrefix))
			if c.OnCtrl != nil {
				c.OnCtrl(ctrl)
			}
			continue
		}
		if c.OnFrame != nil {
			c.OnFrame(buf)
		}
	}
}
