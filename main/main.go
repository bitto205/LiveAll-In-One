package main

import (
	"os"

	"liveaio/core"
)

// Debug shell entry (go run / go build ./main).
// Same role as LiveAIO.exe: process lifecycle owner that drives Core Supervisor.
// Not a business owner — hub/tools/UI live under Core / Pages / Tools.
func main() {
	os.Exit(core.SupervisorRun(os.Args))
}
