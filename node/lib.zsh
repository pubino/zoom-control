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

# Install roomagent from a release: a signed+notarized .pkg, or a notarized .zip of the
# Developer ID-signed binary. Unsigned builds are refused (TCC grants would not stick).
# EXPECTED_TEAM_ID (optional) pins the signing team.
install_roomagent() {
  local version="$1" tmp v="${1#v}"
  tmp="$(mktemp -d)"
  info "downloading roomagent $version from $PUBLIC_REPO"
  run gh release download "$version" -R "$PUBLIC_REPO" -p 'roomagent-*' -p SHA256SUMS -D "$tmp"
  if [[ "$DRY_RUN" == 1 ]]; then rm -rf "$tmp"; return 0; fi

  (cd "$tmp" && for f in roomagent-*(N); do grep " $f\$" SHA256SUMS; done | shasum -a 256 -c -) \
    || die "checksum mismatch"
  local pkg="$tmp/roomagent-$v.pkg" zip="$tmp/roomagent-$v-macos.zip" staged
  if [[ -f "$pkg" ]]; then
    spctl --assess --type install "$pkg" || die "pkg failed Gatekeeper assessment"
    run sudo installer -pkg "$pkg" -target /
  elif [[ -f "$zip" ]]; then
    ditto -x -k "$zip" "$tmp/x"
    staged="$tmp/x/roomagent"
    codesign --verify --strict "$staged" || die "roomagent signature invalid"
    codesign -dvv "$staged" 2>&1 | grep -q "Authority=Developer ID Application" \
      || die "roomagent is not Developer ID signed; refusing to deploy"
    spctl --assess --type open --context context:primary-signature "$staged" 2>/dev/null \
      || warn "Gatekeeper could not confirm notarization (offline?)"
    run sudo install -d -m 755 /usr/local/bin
    run sudo install -m 755 "$staged" /usr/local/bin/roomagent
  else
    die "release $version has no signed roomagent (found: $(ls "$tmp" | tr '\n' ' '))"
  fi
  if [[ -n "${EXPECTED_TEAM_ID:-}" ]]; then
    codesign -dvv /usr/local/bin/roomagent 2>&1 | grep -q "TeamIdentifier=$EXPECTED_TEAM_ID" \
      || die "installed roomagent is not signed by team $EXPECTED_TEAM_ID"
  fi
  [[ "$(/usr/local/bin/roomagent --version)" == "$v" ]] || die "installed roomagent version != $v"
  ok "roomagent $v installed"
  rm -rf "$tmp"
}
