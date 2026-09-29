# Shared helpers for node scripts. Source, don't execute.
set -euo pipefail

: "${PUBLIC_REPO:=pubino/zoom-control}"
: "${RUNNER_USER:=av-runner}"
: "${DRY_RUN:=0}"

info() { print -P "%F{blue}▸%f $*"; }
ok()   { print -P "%F{green}✓%f $*"; }
warn() { print -P "%F{yellow}⚠%f $*" >&2; }
die()  { print -P "%F{red}✗%f $*" >&2; exit 1; }

# Run a command, or just print it under DRY_RUN=1.
run() {
  if [[ "$DRY_RUN" == 1 ]]; then print -- "[dry-run] ${(q-)@}"; else "$@"; fi
}

as_runner() { run sudo -u "$RUNNER_USER" -H "$@"; }

require_macos() {
  [[ "$(uname -s)" == Darwin ]] || die "room nodes must be macOS"
  local major="${$(sw_vers -productVersion)%%.*}"
  (( major >= 14 )) || die "macOS 14 or later required (found $(sw_vers -productVersion))"
}

require_cmd() { command -v "$1" >/dev/null || die "missing required command: $1 ${2:-}"; }

runner_home() { print -- "$(dscl . -read "/Users/$RUNNER_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"; }

runner_arch() { [[ "$(uname -m)" == arm64 ]] && print arm64 || print x64; }

install_roomagent() {
  local version="$1" tmp
  tmp="$(mktemp -d)"
  info "downloading roomagent $version from $PUBLIC_REPO"
  run gh release download "$version" -R "$PUBLIC_REPO" -p 'roomagent-*.pkg' -p SHA256SUMS -D "$tmp"
  if [[ "$DRY_RUN" != 1 ]]; then
    local pkg=("$tmp"/roomagent-*.pkg(N))
    (( ${#pkg} == 1 )) || die "expected one roomagent pkg in release $version"
    [[ "$pkg[1]" != *UNSIGNED* ]] || die "release $version has only an UNSIGNED pkg; refusing to deploy"
    (cd "$tmp" && grep "$(basename "$pkg[1]")" SHA256SUMS | shasum -a 256 -c -) || die "checksum mismatch"
    spctl --assess --type install "$pkg[1]" || die "pkg failed Gatekeeper assessment"
    run sudo installer -pkg "$pkg[1]" -target /
    /usr/local/bin/roomagent --version >/dev/null || die "roomagent did not install"
    ok "roomagent $(/usr/local/bin/roomagent --version) installed"
  fi
  rm -rf "$tmp"
}
