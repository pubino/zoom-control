#!/usr/bin/env zsh
# Decommission a room node: unregister the runner, remove roomagent, optionally delete the user.
#
#   ./teardown.zsh --config-repo pubino/zoom-control-config [--delete-user] [--keep-power-settings]
source "${0:A:h}/lib.zsh"

config_repo="" delete_user=0 keep_power=0
while (( $# )); do
  case "$1" in
    --config-repo) config_repo="$2"; shift 2 ;;
    --user) RUNNER_USER="$2"; shift 2 ;;
    --delete-user) delete_user=1; shift ;;
    --keep-power-settings) keep_power=1; shift ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$config_repo" ]] || die "--config-repo is required"
require_macos
require_cmd gh

home="$(runner_home)"
for rdir in "$home/actions-runner" "$home/actions-runner-api"; do
  if [[ -n "$home" && -x "$rdir/config.sh" ]]; then
    (cd "$rdir" && as_runner ./svc.sh stop) || true
    (cd "$rdir" && as_runner ./svc.sh uninstall) || true
    token="$(gh api -X POST "repos/$config_repo/actions/runners/remove-token" -q .token)"
    (cd "$rdir" && as_runner ./config.sh remove --token "$token") || warn "runner removal failed; delete it in repo settings"
    run sudo rm -rf "$rdir"
    ok "runner in $rdir removed"
  fi
done

# Revoke and delete Zoom host authorizations stored on this node.
if [[ -x "$home/.zoomctl/venv/bin/zoomctl" ]]; then
  for h in $(sudo -u "$RUNNER_USER" "$home/.zoomctl/venv/bin/zoomctl" auth status 2>/dev/null | awk '/^  [^ ]+@[^ ]+:/{sub(":","",$1); print $1}'); do
    as_runner "$home/.zoomctl/venv/bin/zoomctl" auth logout --host "$h" || warn "could not revoke $h (revoke it in Zoom Marketplace ▸ Manage ▸ Installed apps)"
  done
  run sudo rm -rf "$home/.zoomctl"
fi

run sudo pkill -x zoom.us || true
if [[ -e /usr/local/bin/roomagent ]]; then
  run sudo rm -f /usr/local/bin/roomagent
  run sudo pkgutil --forget us.zoomcontrol.roomagent || true
  ok "roomagent removed"
fi

if (( ! keep_power )); then
  run sudo pmset repeat cancel
  run sudo pmset -a sleep 10 displaysleep 10 disksleep 10
  run sudo sysadminctl -autologin off
  ok "power/auto-login defaults restored"
fi

if (( delete_user )) && id "$RUNNER_USER" >/dev/null 2>&1; then
  run sudo sysadminctl -deleteUser "$RUNNER_USER"
  ok "user $RUNNER_USER deleted"
fi
ok "teardown complete"
