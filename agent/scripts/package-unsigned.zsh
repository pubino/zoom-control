#!/usr/bin/env zsh
# Build an UNSIGNED pkg (CI without signing secrets, or local testing). TCC grants will
# not survive rebuilds of an unsigned binary — do not deploy this to room nodes.
set -euo pipefail
cd "${0:A:h}/.."
bin="$(./scripts/build.zsh release | tail -1)"
version="${VERSION:-$("$bin" --version)}"
out="${OUT:-dist}"
root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT
mkdir -p "$out" "$root/usr/local/bin"
cp "$bin" "$root/usr/local/bin/roomagent"
pkgbuild --root "$root" --identifier us.zoomcontrol.roomagent --version "$version" \
  --install-location / "$out/roomagent-$version-UNSIGNED.pkg"
print "⚠ built UNSIGNED $out/roomagent-$version-UNSIGNED.pkg"
