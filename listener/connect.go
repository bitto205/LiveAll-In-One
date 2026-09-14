package listener

import "liveaio/util/connectdiag"

type ConnectCode = connectdiag.ConnectCode
type ConnectError = connectdiag.ConnectError

const (
	CodeConnected  = connectdiag.CodeConnected
	CodeNotLiving  = connectdiag.CodeNotLiving
	CodeBadRoom    = connectdiag.CodeBadRoom
	CodeTimeoutNet = connectdiag.CodeTimeoutNet
	CodeTimeout    = connectdiag.CodeTimeout

	MsgConnected  = connectdiag.MsgConnected
	MsgNotLiving  = connectdiag.MsgNotLiving
	MsgNotLivingConnectFail = connectdiag.MsgNotLivingConnectFail
	MsgProxyShellNoResponse = connectdiag.MsgProxyShellNoResponse
	MsgBadRoom    = connectdiag.MsgBadRoom
	MsgTimeoutNet = connectdiag.MsgTimeoutNet
	MsgTimeout    = connectdiag.MsgTimeout
)

var AsConnectError = connectdiag.AsConnectError

func errNotLiving() error   { return connectdiag.ErrNotLiving() }
func errBadRoom() error     { return connectdiag.ErrBadRoom() }
func errTimeoutNet() error  { return connectdiag.ErrTimeoutNet() }
func errTimeout() error     { return connectdiag.ErrTimeout() }
func errWithDetail(code ConnectCode, detail string) error {
	return connectdiag.ErrWithDetail(code, detail)
}
