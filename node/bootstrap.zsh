#!/usr/bin/env zsh
# Provision a closet Mac mini as a zoom-control room node.
#
#   sudo -v && ./bootstrap.zsh --room room-101 --config-repo pubino/zoom-control-config --version v0.1.0
#
# Run as an admin user with `gh` authenticated (needs admin on the config repo to mint a
# runner registration token). Idempotent: safe to re-run. DRY_RUN=1 prints every action.
source "${0:A:h}/lib.zsh"

room="" config_repo="" version="" labels_extra=""
while (( $# )); do
  case "$1" in
    --room) room="$2"; shift 2 ;;
    --config-repo) config_repo="$2"; shift 2 ;;
    --version) version="$2"; shift 2 ;;
    --user) RUNNER_USER="$2"; shift 2 ;;
    --labels) labels_extra="$2"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
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

# 5. GitHub Actions runner (repo-scoped to the PRIVATE config repo) ------------------------
home="$(runner_home)"; [[ -n "$home" || "$DRY_RUN" == 1 ]] || die "cannot find home for $RUNNER_USER"
rdir="$home/actions-runner"
runner_tag="$(gh api repos/actions/runner/releases/latest -q .tag_name)"
runner_ver="${runner_tag#v}"
if [[ ! -x "$rdir/config.sh" ]]; then
  info "installing actions runner $runner_ver"
  tarball="actions-runner-osx-$(runner_arch)-$runner_ver.tar.gz"
  as_runner mkdir -p "$rdir"
  as_runner curl -fsSL -o "$rdir/$tarball" "https://github.com/actions/runner/releases/download/$runner_tag/$tarball"
  as_runner tar -xzf "$rdir/$tarball" -C "$rdir"
  as_runner rm -f "$rdir/$tarball"
fi

if [[ -f "$rdir/.runner" ]]; then
  ok "runner already registered"
else
  info "registering runner $room on $config_repo"
  token="$(gh api -X POST "repos/$config_repo/actions/runners/registration-token" -q .token)"
  [[ -n "$token" ]] || die "could not obtain registration token (need admin on $config_repo)"
  labels="macOS,$room${labels_extra:+,$labels_extra}"
  (cd "$rdir" && as_runner ./config.sh --unattended --replace --url "https://github.com/$config_repo" \
      --token "$token" --name "$room" --labels "$labels" --work _work)
fi

# Runner environment: pins this node's identity and a PATH that finds python3.12 + roomagent.
brew_prefix="$(dirname "$(dirname "$(command -v python3.12)")")"
env_file="$rdir/.env"
env_body="ZOOMCTL_ROOM=$room
ROOMAGENT=/usr/local/bin/roomagent
PATH=$brew_prefix/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if [[ "$DRY_RUN" == 1 ]]; then print "[dry-run] write $env_file"; else
  print -- "$env_body" | sudo -u "$RUNNER_USER" tee "$env_file" >/dev/null
fi

# LaunchAgent (not LaunchDaemon): the job must run inside the auto-logged-in GUI session
# for Accessibility, Screen Recording and AVFoundation to work.
(cd "$rdir" && as_runner ./svc.sh install) || warn "svc.sh install failed (already installed?)"
uid="$(id -u "$RUNNER_USER" 2>/dev/null || echo 0)"
if launchctl print "gui/$uid" >/dev/null 2>&1; then
  (cd "$rdir" && as_runner ./svc.sh start) || warn "svc.sh start failed"
else
  warn "$RUNNER_USER has no GUI session yet — the runner starts at next (auto-)login. Reboot to finish."
fi

cat <<MSG

$(ok "bootstrap complete for $room")
Remaining one-time steps (see docs/node-setup.md and docs/security.md):
  1. Deploy mdm/roomagent-pppc.mobileconfig (Accessibility, Screen Recording, Camera, Mic) via MDM,
     or grant them manually in System Settings ▸ Privacy & Security while logged in as $RUNNER_USER.
  2. Deploy mdm/us.zoom.config.plist (Zoom managed preferences).
  3. Log in to zoom.us once as $RUNNER_USER and dismiss first-run dialogs.
  4. Reboot, then run:  sudo -u $RUNNER_USER /usr/local/bin/roomagent devices --pretty
     and copy exact device/display names into rooms/$room.yaml in $config_repo.
MSG
