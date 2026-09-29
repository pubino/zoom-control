#!/usr/bin/env zsh
# Provision a closet Mac mini as a zoom-control room node.
#
#   sudo -v && ./bootstrap.zsh --room room-101 --config-repo pubino/zoom-control-config --version v0.1.1 --api-runner
#
# --api-runner also registers a second runner (label zoom-api) on this node for jobs that call
# the Zoom API (plan, reconcile, check, manual end). Use it on the node where the host account
# (e.g. orfetalks@princeton.edu) is authorized with `zoomctl auth login`.
#
# Run as an admin user with `gh` authenticated (needs admin on the config repo to mint a
# runner registration token). Idempotent: safe to re-run. DRY_RUN=1 prints every action.
source "${0:A:h}/lib.zsh"

room="" config_repo="" version="" labels_extra="" api_runner=0
while (( $# )); do
  case "$1" in
    --room) room="$2"; shift 2 ;;
    --config-repo) config_repo="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    --user) RUNNER_USER="$2"; shift 2 ;;
    --labels) labels_extra="$2"; shift 2 ;;
    --api-runner) api_runner=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ -n "$room" && -n "$config_repo" && -n "$version" ]] || die "--room, --config-repo and --version are required"
[[ "$room" =~ '^[a-z0-9][a-z0-9-]{0,62}$' ]] || die "invalid room id: $room"

require_macos
require_cmd gh "(brew install gh)"
require_cmd python3.12 "(brew install python@3.12)"
gh auth status >/dev/null 2>&1 || die "gh is not authenticated (gh auth login)"

# 1. Dedicated non-admin user --------------------------------------------------------------
if id "$RUNNER_USER" >/dev/null 2>&1; then
  ok "user $RUNNER_USER exists"
  dseditgroup -o checkmember -m "$RUNNER_USER" admin >/dev/null 2>&1 && warn "$RUNNER_USER is an admin — should be standard"
else
  info "creating standard user $RUNNER_USER (you will be prompted for its password)"
  run sudo sysadminctl -addUser "$RUNNER_USER" -fullName "AV Runner" -password -
fi

# 2. Power, sleep, restart-after-outage -----------------------------------------------------
info "configuring power management"
run sudo pmset -a autorestart 1 sleep 0 displaysleep 0 disksleep 0 womp 1 powernap 0
run sudo systemsetup -setrestartfreeze on >/dev/null
run sudo pmset repeat restart MTWRFSU 04:00:00   # daily clean restart outside event hours
as_runner defaults -currentHost write com.apple.screensaver idleTime -int 0

# 3. Auto-login (required: screen sharing needs an unlocked GUI session) -------------------
if [[ "$(fdesetup status 2>/dev/null)" == *"FileVault is On"* ]]; then
  warn "FileVault is ON — macOS disables automatic login. The node will not recover unattended"
  warn "after a power cut. See docs/node-setup.md#filevault for options."
fi
current_auto="$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null || true)"
if [[ "$current_auto" != "$RUNNER_USER" ]]; then
  info "enabling automatic login for $RUNNER_USER (prompts for its password)"
  run sudo sysadminctl -autologin set -userName "$RUNNER_USER" -password -
else
  ok "automatic login already set to $RUNNER_USER"
fi
warn "also disable 'Require password after screen saver' for $RUNNER_USER (System Settings ▸ Lock Screen)"

# 4. roomagent (signed + notarized) ---------------------------------------------------------
install_roomagent "$version"

# 5. zoomctl for interactive use on the node (jobs install their own pinned copy) ----------
install_zoomctl "$version"

# 6. GitHub Actions runner(s), repo-scoped to the PRIVATE config repo -----------------------
home="$(runner_home)"; [[ -n "$home" || "$DRY_RUN" == 1 ]] || die "cannot find home for $RUNNER_USER"
runner_tag="$(gh api repos/actions/runner/releases/latest -q .tag_name)"
runner_ver="${runner_tag#v}"
brew_prefix="$(dirname "$(dirname "$(command -v python3.12)")")"
uid="$(id -u "$RUNNER_USER" 2>/dev/null || echo 0)"

# setup_runner <dir> <name> <labels>
setup_runner() {
  local rdir="$1" name="$2" labels="$3"
  if [[ ! -x "$rdir/config.sh" ]]; then
    info "installing actions runner $runner_ver in $rdir"
    local tarball="actions-runner-osx-$(runner_arch)-$runner_ver.tar.gz"
    as_runner mkdir -p "$rdir"
    as_runner curl -fsSL -o "$rdir/$tarball" "https://github.com/actions/runner/releases/download/$runner_tag/$tarball"
    as_runner tar -xzf "$rdir/$tarball" -C "$rdir"
    as_runner rm -f "$rdir/$tarball"
  fi
  if [[ -f "$rdir/.runner" ]]; then
    ok "runner $name already registered"
  else
    info "registering runner $name ($labels) on $config_repo"
    local token
    token="$(gh api -X POST "repos/$config_repo/actions/runners/registration-token" -q .token)"
    [[ -n "$token" ]] || die "could not obtain registration token (need admin on $config_repo)"
    (cd "$rdir" && as_runner ./config.sh --unattended --replace --url "https://github.com/$config_repo" \
        --token "$token" --name "$name" --labels "$labels" --work _work)
  fi
  # Pins this node's identity and a PATH that finds python3.12 + roomagent.
  local env_body="ZOOMCTL_ROOM=$room
ROOMAGENT=/usr/local/bin/roomagent
ZOOMCTL_TOKEN_STORE=keychain
PATH=$brew_prefix/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  if [[ "$DRY_RUN" == 1 ]]; then print "[dry-run] write $rdir/.env"; else
    print -- "$env_body" | sudo -u "$RUNNER_USER" tee "$rdir/.env" >/dev/null
  fi
  # LaunchAgent (not LaunchDaemon): jobs must run inside the auto-logged-in GUI session for
  # Accessibility, Screen Recording, AVFoundation and the login keychain (Zoom tokens).
  (cd "$rdir" && as_runner ./svc.sh install) || warn "svc.sh install failed for $name (already installed?)"
  if launchctl print "gui/$uid" >/dev/null 2>&1; then
    (cd "$rdir" && as_runner ./svc.sh start) || warn "svc.sh start failed for $name"
  else
    warn "$RUNNER_USER has no GUI session yet — $name starts at next (auto-)login. Reboot to finish."
  fi
}

setup_runner "$home/actions-runner" "$room" "macOS,$room${labels_extra:+,$labels_extra}"
(( api_runner )) && setup_runner "$home/actions-runner-api" "$room-api" "macOS,zoom-api"

cat <<MSG

$(ok "bootstrap complete for $room")
Remaining one-time steps (docs/node-setup.md, docs/calibration.md):
  1. Deploy mdm/roomagent-pppc.mobileconfig via MDM (or grant Accessibility manually).
  2. Deploy mdm/us.zoom.config.plist (Zoom managed preferences).
  3. Reboot so $RUNNER_USER auto-logs in, then IN THAT GUI SESSION (Screen Sharing is fine):
       roomagent devices --pretty                  # names for rooms/$room.yaml
       ~/.zoomctl/venv/bin/zoomctl auth login --host <host email>   # e.g. orfetalks@princeton.edu
  4. Work through docs/calibration.md (privacy approvals, UI strings, launch mode).
MSG
