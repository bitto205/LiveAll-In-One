//go:build windows

package core

import (
	"fmt"
	"net"

	"github.com/Microsoft/go-winio"
)

func listenPipeFallback(pipe string, tcpErr error) (net.Listener, string, error) {
	ln, err := winio.ListenPipe(pipe, nil)
	if err != nil {
		return nil, "", fmt.Errorf("tcp: %v; pipe: %w", tcpErr, err)
	}
	return ln, "pipe:" + pipe, nil
}
