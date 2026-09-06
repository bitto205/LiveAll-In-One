package core

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"sync"
	"time"
)

const (
	connWriteTimeout = 2 * time.Second
	connReadTimeout  = 60 * time.Second
	serverStopWait   = 3 * time.Second
)

type Handler func(conn *Conn, env Envelope)

type Conn struct {
	raw    net.Conn
	wmu    sync.Mutex
	closed bool
}

func (c *Conn) Send(env Envelope) error {
	c.wmu.Lock()
	defer c.wmu.Unlock()
	if c.closed {
		return io.ErrClosedPipe
	}
	b, err := json.Marshal(env)
	if err != nil {
		return err
	}
	b = append(b, '\n')
	_ = c.raw.SetWriteDeadline(time.Now().Add(connWriteTimeout))
	_, err = c.raw.Write(b)
	if err != nil {
		c.closed = true
		_ = c.raw.Close()
	}
	return err
}

func (c *Conn) Close() error {
	c.wmu.Lock()
	c.closed = true
	c.wmu.Unlock()
	return c.raw.Close()
}

type Server struct {
	PipeName     string
	TCPAddr      string
	Handler      Handler
	Log          *slog.Logger
	OnConnect    func(*Conn)
	OnDisconnect func(*Conn)

	ln     net.Listener
	cancel context.CancelFunc
	wg     sync.WaitGroup

	cmu   sync.Mutex
	conns map[*Conn]struct{}
}

func (s *Server) log() *slog.Logger {
	if s.Log != nil {
		return s.Log
	}
	return slog.Default()
}

func (s *Server) track(c *Conn) {
	s.cmu.Lock()
	if s.conns == nil {
		s.conns = map[*Conn]struct{}{}
	}
	s.conns[c] = struct{}{}
	s.cmu.Unlock()
}

func (s *Server) untrack(c *Conn) {
	s.cmu.Lock()
	delete(s.conns, c)
	s.cmu.Unlock()
}

func (s *Server) closeAllConns() {
	s.cmu.Lock()
	list := make([]*Conn, 0, len(s.conns))
	for c := range s.conns {
		list = append(list, c)
	}
	s.cmu.Unlock()
	for _, c := range list {
		_ = c.Close()
	}
}

func (s *Server) Listen() (net.Listener, string, error) {
	// Phase 0: TCP primary (reliable without elevation). Pipe optional later.
	addr := s.TCPAddr
	if addr == "" {
		addr = DefaultTCPAddr
	}
	tcpLn, err := net.Listen("tcp", addr)
	if err == nil {
		return tcpLn, "tcp:" + addr, nil
	}

	pipe := s.PipeName
	if pipe == "" {
		pipe = DefaultPipeName
	}
	return listenPipeFallback(pipe, err)
}

func (s *Server) Serve(ctx context.Context) error {
	ln, how, err := s.Listen()
	if err != nil {
		return err
	}
	s.ln = ln
	s.log().Info("IPC listening", "addr", how)

	ctx, s.cancel = context.WithCancel(ctx)
	s.wg.Add(1)
	go func() {
		defer s.wg.Done()
		<-ctx.Done()
		_ = ln.Close()
		s.closeAllConns()
	}()

	for {
		raw, err := ln.Accept()
		if err != nil {
			select {
			case <-ctx.Done():
				return nil
			default:
				s.log().Error("accept failed", "err", err)
				return err
			}
		}
		s.wg.Add(1)
		go func(raw net.Conn) {
			defer s.wg.Done()
			s.serveConn(ctx, raw)
		}(raw)
	}
}

func (s *Server) serveConn(ctx context.Context, raw net.Conn) {
	c := &Conn{raw: raw}
	s.track(c)
	defer func() {
		s.untrack(c)
		_ = c.Close()
	}()
	if s.OnConnect != nil {
		s.OnConnect(c)
	}
	defer func() {
		if s.OnDisconnect != nil {
			s.OnDisconnect(c)
		}
	}()
	_ = c.Send(Envelope{"op": OpReady, "version": Version, "protocol_version": ProtocolVersion})

	r := bufio.NewReader(raw)
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		_ = raw.SetReadDeadline(time.Now().Add(connReadTimeout))
		line, err := r.ReadBytes('\n')
		if err != nil {
			if err != io.EOF {
				s.log().Debug("conn read end", "err", err)
			}
			return
		}
		var env Envelope
		if err := json.Unmarshal(line, &env); err != nil {
			_ = c.Send(Envelope{"op": OpError, "code": "bad_json", "msg": err.Error()})
			continue
		}
		if s.Handler != nil {
			s.Handler(c, env)
		}
	}
}

// RequestShowUI asks the instance that owns the launcher to show its UI.
// Used by a second launch, which must not start a UI of its own.
func RequestShowUI(tcp string) error {
	addr := tcp
	if addr == "" {
		addr = DefaultTCPAddr
	}
	var last error
	for attempt := 0; attempt < 8; attempt++ {
		if attempt > 0 {
			time.Sleep(250 * time.Millisecond)
		}
		last = requestShowUIOnce(addr)
		if last == nil {
			return nil
		}
	}
	return fmt.Errorf("core not listening on %s (launcher mutex held by a stale process?): %w", addr, last)
}

func requestShowUIOnce(addr string) error {
	raw, err := net.DialTimeout("tcp", addr, 3*time.Second)
	if err != nil {
		return err
	}
	defer raw.Close()
	_ = raw.SetDeadline(time.Now().Add(3 * time.Second))
	b, err := json.Marshal(Envelope{"op": OpUICommand, "action": "ui.show"})
	if err != nil {
		return err
	}
	if _, err := raw.Write(append(b, '\n')); err != nil {
		return err
	}
	// 等待 ack，确保对端处理完再退出本进程。
	r := bufio.NewReader(raw)
	for i := 0; i < 8; i++ {
		line, err := r.ReadBytes('\n')
		if err != nil {
			return nil
		}
		var env Envelope
		if json.Unmarshal(line, &env) != nil {
			continue
		}
		if env["ack"] == "ui.show" {
			return nil
		}
	}
	return nil
}

func (s *Server) Stop() {
	if s.cancel != nil {
		s.cancel()
	}
	if s.ln != nil {
		_ = s.ln.Close()
	}
	s.closeAllConns()
	done := make(chan struct{})
	go func() {
		s.wg.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(serverStopWait):
		s.log().Warn("Server.Stop timed out waiting for connections")
	}
}
