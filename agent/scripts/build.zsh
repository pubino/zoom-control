#!/usr/bin/env zsh
# Build roomagent. Usage: build.zsh [debug|release]   (default: release)
# Release builds are universal unless ARCHS is set (e.g. ARCHS="arm64").
# Prints the built binary's path as the last line of stdout.
set -euo pipefail
cd "${0:A:h}/.."
config="${1:-release}"
if [[ "$config" == release ]]; then
  archs=(${=ARCHS:-arm64 x86_64})
  flags=(-c release)
  for a in $archs; flags+=(--arch $a)
else
  flags=(-c debug)
fi
swift build $flags >&2
bin="$(swift build $flags --show-bin-path)/roomagent"
[[ -x "$bin" ]] || { print -u2 "✗ build produced no binary at $bin"; exit 1; }
"$bin" --version >/dev/null || { print -u2 "✗ built binary does not run"; exit 1; }
print -u2 "✓ roomagent $("$bin" --version) [$(lipo -archs "$bin")]"
print -- "$bin"
