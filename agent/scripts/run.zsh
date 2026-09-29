#!/usr/bin/env zsh
# Build (debug) and run roomagent with the given arguments, e.g.:
#   run.zsh preflight --video-device "MacBook Pro Camera" --audio-device "MacBook Pro Microphone" \
#           --display "Built-in Retina Display" --pretty
set -euo pipefail
cd "${0:A:h}/.."
swift build -c debug >/dev/null
exec .build/debug/roomagent "$@"
