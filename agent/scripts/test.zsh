#!/usr/bin/env zsh
# Run the Swift test suite. Fails if zero XCTest cases executed (silence is not success).
set -euo pipefail
cd "${0:A:h}/.."
log="$(mktemp -t roomagent-test)"
trap 'rm -f "$log"' EXIT
swift test 2>&1 | tee "$log"
executed="$(grep -Eo "Executed [0-9]+ tests?, with 0 failures" "$log" | tail -1 | grep -Eo '[0-9]+' | head -1 || true)"
if [[ -z "$executed" || "$executed" -eq 0 ]]; then
  print -u2 "✗ no Swift tests executed (or failures present)"; exit 1
fi
print "✓ $executed Swift tests passed"
