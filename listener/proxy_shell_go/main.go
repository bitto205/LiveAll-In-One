package main

import (
	"bufio"
	"bytes"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/binary"
	"encoding/pem"
	"fmt"
	"io"
	"log"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const (
	proxyPort = 19088
	ipcPort   = 19098
)

const (
	ipcCtrlPrefix  = "__LH_CTRL__:"
	ctrlWSConnected = "WS_CONNECTED"
	ctrlWSDisconnected = "WS_DISCONNECTED"
)

// ─── Paths ────────────────────────────────────────────────────────────────────

func exeDir() string {
	exe, err := os.Executable()
	if err != nil {
		return "."
	}
	return filepath.Dir(exe)
}

func livehelperDir() string {
	home, _ := os.UserHomeDir()
	return filepath.Join(home, ".livehelper")
}

// ─── Logging ──────────────────────────────────────────────────────────────────

var logger = log.New(os.Stdout, "", log.LstdFlags)

func setupLogging() {
	logDir := filepath.Join(exeDir(), "proxy_shell_log")
	os.MkdirAll(logDir, 0755)
	ts := time.Now().Format("20060102_150405")
	f, err := os.Create(filepath.Join(logDir, "proxy_shell_"+ts+".log"))
	if err != nil {
		return
	}
	logger = log.New(f, "", log.LstdFlags)
}

// ─── CA Cert (read-only) ──────────────────────────────────────────────────────
// CA cert is created by listener4.py during patch. Go only reads it.

var (
	caKey   *rsa.PrivateKey
	caCert  *x509.Certificate
	leafMu  sync.Map // hostname → *tls.Certificate
)

func loadCA() error {
	dir := livehelperDir()
	certPath := filepath.Join(dir, "proxy_shell_ca.crt")
	keyPath := filepath.Join(dir, "proxy_shell_ca.key")

	certPEM, err := os.ReadFile(certPath)
	if err != nil {
		return fmt.Errorf("CA cert not found at %s (run patch first): %w", certPath, err)
	}
	keyPEM, err := os.ReadFile(keyPath)
	if err != nil {
		return fmt.Errorf("CA key not found at %s: %w", keyPath, err)
	}

	block, _ := pem.Decode(certPEM)
	if block == nil {
		return fmt.Errorf("invalid CA cert PEM")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return err
	}

	keyBlock, _ := pem.Decode(keyPEM)
	if keyBlock == nil {
		return fmt.Errorf("invalid CA key PEM")
	}
	// Support both PKCS8 and PKCS1 (TraditionalOpenSSL) formats
	var rsaKey *rsa.PrivateKey
	if k, err8 := x509.ParsePKCS8PrivateKey(keyBlock.Bytes); err8 == nil {
		rsaKey = k.(*rsa.PrivateKey)
	} else if k1, err1 := x509.ParsePKCS1PrivateKey(keyBlock.Bytes); err1 == nil {
		rsaKey = k1
	} else {
		return fmt.Errorf("cannot parse CA key: %v", err8)
	}

	caCert = cert
	caKey = rsaKey
	logger.Println("CA cert loaded")
	return nil
}

func leafCert(hostname string) (*tls.Certificate, error) {
	if v, ok := leafMu.Load(hostname); ok {
		return v.(*tls.Certificate), nil
	}
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, err
	}
	sn, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 64))
	tmpl := &x509.Certificate{
		SerialNumber: sn,
		Subject:      pkix.Name{CommonName: hostname},
		DNSNames:     []string{hostname},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().AddDate(1, 0, 0),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, caCert, &key.PublicKey, caKey)
	if err != nil {
		return nil, err
	}
	cert := &tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}
	leafMu.Store(hostname, cert)
	return cert, nil
}

// ─── IPC Server ───────────────────────────────────────────────────────────────
// Point-to-point: one authenticated client at a time.
// Handshake: client sends token line first; Go validates against ipc_token file.
// Wire format: [uint32 big-endian length][raw PushFrame bytes]

func readIPCToken() string {
	data, err := os.ReadFile(filepath.Join(livehelperDir(), "ipc_token"))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

type ipcServer struct {
	mu   sync.Mutex // guards conn
	wmu  sync.Mutex // serialises writes
	conn net.Conn
}

var liveActive uint32 // 1=live websocket active, 0=inactive

func isLiveActive() bool {
	return atomic.LoadUint32(&liveActive) == 1
}

func setLiveActive(active bool, ipc *ipcServer, host string) {
	var next uint32
	state := ctrlWSDisconnected
	if active {
		next = 1
		state = ctrlWSConnected
	}
	prev := atomic.SwapUint32(&liveActive, next)
	if prev == next {
		return
	}
	logger.Printf("live state changed: %v (host=%s)", active, host)
	ipc.push([]byte(ipcCtrlPrefix + state))
}

func (s *ipcServer) serve() {
	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", ipcPort))
	if err != nil {
		logger.Printf("IPC listen failed on port %d: %v", ipcPort, err)
		return
	}
	logger.Printf("IPC listening on 127.0.0.1:%d", ipcPort)
	for {
		conn, err := ln.Accept()
		if err != nil {
			continue
		}
		go s.handshake(conn)
	}
}

func (s *ipcServer) handshake(conn net.Conn) {
	// Token must arrive within 3 seconds
	conn.SetDeadline(time.Now().Add(3 * time.Second))
	buf := bufio.NewReader(conn)
	line, err := buf.ReadString('\n')
	conn.SetDeadline(time.Time{})
	if err != nil {
		conn.Close()
		return
	}
	token := strings.TrimSpace(line)
	expected := readIPCToken()
	if expected == "" || token != expected {
		logger.Printf("IPC auth rejected from %s", conn.RemoteAddr())
		conn.Close()
		return
	}
	logger.Printf("IPC client connected: %s", conn.RemoteAddr())

	// Replace any existing connection
	s.mu.Lock()
	old := s.conn
	s.conn = conn
	s.mu.Unlock()
	if old != nil {
		old.Close()
	}

	// Keep alive until client disconnects
	io.Copy(io.Discard, conn)
	conn.Close()
	s.mu.Lock()
	if s.conn == conn {
		s.conn = nil
		logger.Println("IPC client disconnected")
	}
	s.mu.Unlock()
}

func (s *ipcServer) push(data []byte) {
	s.mu.Lock()
	conn := s.conn
	s.mu.Unlock()
	if conn == nil {
		return
	}
	var hdr [4]byte
	binary.BigEndian.PutUint32(hdr[:], uint32(len(data)))
	s.wmu.Lock()
	defer s.wmu.Unlock()
	if _, err := conn.Write(hdr[:]); err != nil {
		s.mu.Lock()
		if s.conn == conn {
			s.conn = nil
		}
		s.mu.Unlock()
		conn.Close()
		return
	}
	if _, err := conn.Write(data); err != nil {
		s.mu.Lock()
		if s.conn == conn {
			s.conn = nil
		}
		s.mu.Unlock()
		conn.Close()
	}
}

// ─── WebSocket ────────────────────────────────────────────────────────────────

func readWSFrame(r io.Reader) (opcode byte, payload []byte, fin bool, err error) {
	hdr := make([]byte, 2)
	if _, err = io.ReadFull(r, hdr); err != nil {
		return
	}
	fin = hdr[0]&0x80 != 0
	opcode = hdr[0] & 0x0F
	masked := hdr[1]&0x80 != 0
	plen := uint64(hdr[1] & 0x7F)
	if plen == 126 {
		ext := make([]byte, 2)
		io.ReadFull(r, ext)
		plen = uint64(binary.BigEndian.Uint16(ext))
	} else if plen == 127 {
		ext := make([]byte, 8)
		io.ReadFull(r, ext)
		plen = binary.BigEndian.Uint64(ext)
	}
	var maskKey [4]byte
	if masked {
		if _, err = io.ReadFull(r, maskKey[:]); err != nil {
			return
		}
	}
	payload = make([]byte, plen)
	if _, err = io.ReadFull(r, payload); err != nil {
		return
	}
	if masked {
		for i := range payload {
			payload[i] ^= maskKey[i%4]
		}
	}
	return
}

func writeWSFrame(w io.Writer, opcode byte, payload []byte, fin bool) error {
	b0 := opcode
	if fin {
		b0 |= 0x80
	}
	plen := len(payload)
	var hdr []byte
	switch {
	case plen < 126:
		hdr = []byte{b0, byte(plen)}
	case plen <= 0xFFFF:
		hdr = []byte{b0, 126, byte(plen >> 8), byte(plen)}
	default:
		hdr = make([]byte, 10)
		hdr[0] = b0
		hdr[1] = 127
		binary.BigEndian.PutUint64(hdr[2:], uint64(plen))
	}
	if _, err := w.Write(hdr); err != nil {
		return err
	}
	_, err := w.Write(payload)
	return err
}

func relayWS(clientR io.Reader, clientW io.Writer,
	serverR io.Reader, serverW io.Writer,
	host string, ipc *ipcServer) {
	setLiveActive(true, ipc, host)

	// server → client: reassemble fragmented frames, push complete payloads to IPC
	go func() {
		defer setLiveActive(false, ipc, host)
		var acc []byte
		var curOpcode byte
		for {
			opcode, payload, fin, err := readWSFrame(serverR)
			if err != nil {
				return
			}
			if writeWSFrame(clientW, opcode, payload, fin) != nil {
				return
			}
			if opcode != 0 { // new message (not continuation)
				curOpcode = opcode
				acc = append([]byte(nil), payload...)
			} else { // continuation frame
				acc = append(acc, payload...)
			}
			if fin && curOpcode == 2 { // complete binary message
				ipc.push(acc)
				acc = nil
			}
		}
	}()

	// client → server: passthrough
	for {
		opcode, payload, fin, err := readWSFrame(clientR)
		if err != nil {
			setLiveActive(false, ipc, host)
			return
		}
		if writeWSFrame(serverW, opcode, payload, fin) != nil {
			setLiveActive(false, ipc, host)
			return
		}
	}
}

// ─── HTTP Relay ───────────────────────────────────────────────────────────────

func readHeaders(r *bufio.Reader) ([]byte, error) {
	var buf []byte
	for {
		line, err := r.ReadBytes('\n')
		buf = append(buf, line...)
		if err != nil {
			return buf, err
		}
		if bytes.Equal(line, []byte("\r\n")) || bytes.Equal(line, []byte("\n")) {
			return buf, nil
		}
		if len(buf) > 131072 {
			return buf, fmt.Errorf("headers too large")
		}
	}
}

func relayHTTP(clientConn, serverConn net.Conn, host string, ipc *ipcServer) {
	clientBuf := bufio.NewReader(clientConn)
	serverBuf := bufio.NewReader(serverConn)

	req, err := readHeaders(clientBuf)
	if err != nil {
		return
	}
	isWS := bytes.Contains(bytes.ToLower(req), []byte("upgrade: websocket"))
	serverConn.Write(req)

	resp, err := readHeaders(serverBuf)
	if err != nil {
		return
	}
	clientConn.Write(resp)

	status := 0
	if parts := bytes.SplitN(resp, []byte(" "), 3); len(parts) >= 2 {
		fmt.Sscanf(string(parts[1]), "%d", &status)
	}

	if isWS && status == 101 {
		logger.Printf("WS: %s", host)
		relayWS(clientBuf, clientConn, serverBuf, serverConn, host, ipc)
		return
	}

	done := make(chan struct{}, 2)
	go func() { io.Copy(serverConn, clientBuf); done <- struct{}{} }()
	go func() { io.Copy(clientConn, serverBuf); done <- struct{}{} }()
	<-done
}

// ─── Proxy Handler ────────────────────────────────────────────────────────────

func isWebcast(host string) bool {
	return strings.Contains(strings.ToLower(host), "webcast")
}

func rawTunnel(a, b net.Conn) {
	done := make(chan struct{}, 2)
	go func() { io.Copy(a, b); done <- struct{}{} }()
	go func() { io.Copy(b, a); done <- struct{}{} }()
	<-done
}

func handleConn(conn net.Conn, ipc *ipcServer) {
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(30 * time.Second))

	// Read CONNECT request byte-by-byte to avoid consuming data after headers
	var firstLine []byte
	buf := make([]byte, 1)
	for {
		if _, err := conn.Read(buf); err != nil {
			return
		}
		firstLine = append(firstLine, buf[0])
		if buf[0] == '\n' {
			break
		}
	}
	// Drain remaining headers until blank line
	var header []byte
	for {
		if _, err := conn.Read(buf); err != nil {
			return
		}
		header = append(header, buf[0])
		n := len(header)
		if n >= 4 && header[n-4] == '\r' && header[n-3] == '\n' &&
			header[n-2] == '\r' && header[n-1] == '\n' {
			break
		}
		if n >= 2 && header[n-2] == '\n' && header[n-1] == '\n' {
			break
		}
	}

	parts := strings.Fields(strings.TrimSpace(string(firstLine)))
	if len(parts) < 2 || parts[0] != "CONNECT" {
		return
	}
	target := parts[1]
	host, _, err := net.SplitHostPort(target)
	if err != nil {
		host = target
	}

	server, err := net.DialTimeout("tcp", target, 10*time.Second)
	if err != nil {
		conn.Write([]byte("HTTP/1.1 502 Bad Gateway\r\n\r\n"))
		return
	}
	defer server.Close()

	conn.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n"))
	conn.SetDeadline(time.Time{})
	server.SetDeadline(time.Time{})

	if isWebcast(host) {
		lc, err := leafCert(host)
		if err != nil {
			logger.Printf("leafCert(%s): %v — falling back to tunnel", host, err)
			rawTunnel(conn, server)
			return
		}
		clientTLS := tls.Server(conn, &tls.Config{Certificates: []tls.Certificate{*lc}})
		if err := clientTLS.Handshake(); err != nil {
			return
		}
		serverTLS := tls.Client(server, &tls.Config{ServerName: host})
		if err := serverTLS.Handshake(); err != nil {
			clientTLS.Close()
			return
		}
		defer clientTLS.Close()
		defer serverTLS.Close()
		relayHTTP(clientTLS, serverTLS, host, ipc)
	} else {
		rawTunnel(conn, server)
	}
}

// ─── Parent watchdog ──────────────────────────────────────────────────────────
// proxy_shell.exe lifecycle must follow 直播伴侣, not main software.
// We watch the PID of the process that spawned us (Electron main process).
// When 直播伴侣 exits, we exit too.

func processAlive(pid int) bool {
	const PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
	const STILL_ACTIVE = 259
	h, err := syscall.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(pid))
	if err != nil {
		return false
	}
	defer syscall.CloseHandle(h)
	var code uint32
	if err = syscall.GetExitCodeProcess(h, &code); err != nil {
		return false
	}
	return code == STILL_ACTIVE
}

func watchParent() {
	ppid := os.Getppid()
	logger.Printf("Parent watchdog started — tracking PID %d (直播伴侣)", ppid)
	tick := time.NewTicker(5 * time.Second)
	defer tick.Stop()
	for range tick.C {
		if !processAlive(ppid) {
			logger.Printf("Parent PID %d exited — proxy_shell shutting down", ppid)
			os.Exit(0)
		}
	}
}

// ─── Self-check ───────────────────────────────────────────────────────────────

func selfCheck() {
	logger.Println("=== proxy_shell self-check ===")
	// IPC token (written by main software on startup)
	if tok := readIPCToken(); tok != "" {
		logger.Println("  IPC token : OK")
	} else {
		logger.Println("  IPC token : MISSING — main software not started yet")
	}
	// Port availability
	if ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", ipcPort)); err == nil {
		ln.Close()
		logger.Printf("  IPC port  : %d available", ipcPort)
	} else {
		logger.Printf("  IPC port  : %d in use (another instance?)", ipcPort)
	}
	if ln, err := net.Listen("tcp", fmt.Sprintf("0.0.0.0:%d", proxyPort)); err == nil {
		ln.Close()
		logger.Printf("  Proxy port: %d available", proxyPort)
	} else {
		logger.Printf("  Proxy port: %d in use (another instance?)", proxyPort)
	}
	logger.Println("=== end self-check ===")
}

// ─── Main ─────────────────────────────────────────────────────────────────────

func main() {
	setupLogging()
	logger.Printf("proxy_shell (Go) starting — proxy=%d ipc=%d pid=%d ppid=%d",
		proxyPort, ipcPort, os.Getpid(), os.Getppid())
	selfCheck()
	go watchParent()

	if err := loadCA(); err != nil {
		logger.Printf("WARN: %v", err)
		logger.Println("Proxy will run without TLS MITM until CA cert is available")
		// Don't exit — proxy can still tunnel non-webcast traffic.
		// Webcast connections will fall back to raw tunnel until CA is set up.
	}

	ipc := &ipcServer{}
	go ipc.serve()

	ln, err := net.Listen("tcp", fmt.Sprintf("0.0.0.0:%d", proxyPort))
	if err != nil {
		logger.Printf("Port %d already in use — existing instance running, exiting", proxyPort)
		return
	}
	logger.Printf("Proxy listening on 0.0.0.0:%d", proxyPort)

	for {
		conn, err := ln.Accept()
		if err != nil {
			continue
		}
		go handleConn(conn, ipc)
	}
}
