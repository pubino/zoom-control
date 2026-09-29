#!/usr/bin/env zsh
# Upgrade roomagent on a room node to a release. The runner self-updates; zoomctl is
# installed per-job by the setup-zoomctl action at the ref pinned in the config repo.
#
#   ./update.zsh --version v0.2.0
source "${0:A:h}/lib.zsh"

version=""
while (( $# )); do
  case "$1" in
    --version) version="$2"; shift 2 ;;
    --user) RUNNER_USER="$2"; shift 2 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$version" ]] || die "--version is required"
require_macos
require_cmd gh

if pgrep -x zoom.us >/dev/null; then
  die "Zoom is running (event in progress?). Re-run between events."
fi
before="$(/usr/local/bin/roomagent --version 2>/dev/null || echo none)"
install_roomagent "$version"
ok "roomagent $before → $(/usr/local/bin/roomagent --version 2>/dev/null || echo "$version (dry-run)")"
warn "if the new binary's code requirement changed, re-deploy the PPPC profile (docs/security.md)"
