#!/usr/bin/env zsh
# Prompt for the config repo's Actions secrets, verify them against Zoom, then store them.
# Values are read without echo, passed only via environment/stdin, and never written to disk.
#
#   scripts/set-config-secrets.zsh --repo pubino/zoom-control-config --config ../zoom-control-config
#
# Options:
#   --repo OWNER/NAME   private config repo (required)
#   --config PATH       local checkout of the config repo, used for `zoomctl check` (recommended)
#   --only NAME         set just one secret (e.g. --only ALERT_WEBHOOK_URL); repeatable
#   --no-verify         store without running `zoomctl check`
set -euo pipefail

repo="" config="" verify=1
typeset -a only
while (( $# )); do
  case "$1" in
    --repo) repo="$2"; shift 2 ;;
    --config) config="$2"; shift 2 ;;
    --only) only+=("$2"); shift 2 ;;
    --no-verify) verify=0; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) print -u2 "✗ unknown argument: $1"; exit 1 ;;
  esac
done
[[ -n "$repo" ]] || { print -u2 "✗ --repo is required"; exit 1; }
command -v gh >/dev/null || { print -u2 "✗ gh is required"; exit 1; }
gh repo view "$repo" --json visibility -q .visibility | grep -qx PRIVATE \
  || { print -u2 "✗ $repo is not a private repo you can access — refusing to store secrets there"; exit 1; }

typeset -A prompts=(
  ZOOM_ACCOUNT_ID    "Zoom Account ID (Marketplace app ▸ App Credentials)"
  ZOOM_CLIENT_ID     "Zoom Client ID"
  ZOOM_CLIENT_SECRET "Zoom Client Secret"
  ALERT_WEBHOOK_URL  "Alert webhook URL (Slack incoming webhook / Teams workflow)"
)
names=(ZOOM_ACCOUNT_ID ZOOM_CLIENT_ID ZOOM_CLIENT_SECRET ALERT_WEBHOOK_URL)
(( ${#only} )) && names=($only)

typeset -A values
for n in $names; do
  [[ -n "${prompts[$n]:-}" ]] || { print -u2 "✗ unknown secret $n"; exit 1; }
  read -rs "val?${prompts[$n]}: "; print
  [[ -n "$val" ]] || { print -u2 "✗ $n is empty"; exit 1; }
  if [[ "$n" == ALERT_WEBHOOK_URL && "$val" != https://* ]]; then print -u2 "✗ webhook must be https://"; exit 1; fi
  values[$n]="$val"
done

if (( verify )) && [[ -z "${values[ZOOM_ACCOUNT_ID]:-}" || -z "${values[ZOOM_CLIENT_ID]:-}" || \
                     -z "${values[ZOOM_CLIENT_SECRET]:-}" ]]; then
  print -u2 "✗ verification needs all three Zoom values; add them with --only or pass --no-verify"
  exit 1
fi
if (( verify )); then
  zoomctl_bin="$(command -v zoomctl || true)"
  [[ -z "$zoomctl_bin" && -x "${0:A:h}/../ctl/.venv/bin/zoomctl" ]] && zoomctl_bin="${0:A:h}/../ctl/.venv/bin/zoomctl"
  if [[ -z "$zoomctl_bin" || -z "$config" ]]; then
    print -u2 "✗ verification needs zoomctl on PATH (or ctl/.venv) and --config PATH; use --no-verify to skip"
    exit 1
  fi
  args=(check -c "$config")
  [[ -n "${values[ALERT_WEBHOOK_URL]:-}" ]] && args+=(--test-alert)
  print "▸ verifying with zoomctl ${args[*]}"
  if ! env ${values[ZOOM_ACCOUNT_ID]:+ZOOM_ACCOUNT_ID=${values[ZOOM_ACCOUNT_ID]}} \
           ${values[ZOOM_CLIENT_ID]:+ZOOM_CLIENT_ID=${values[ZOOM_CLIENT_ID]}} \
           ${values[ZOOM_CLIENT_SECRET]:+ZOOM_CLIENT_SECRET=${values[ZOOM_CLIENT_SECRET]}} \
           ${values[ALERT_WEBHOOK_URL]:+ALERT_WEBHOOK_URL=${values[ALERT_WEBHOOK_URL]}} \
           "$zoomctl_bin" $args; then
    print -u2 "✗ verification failed — nothing was stored"; exit 1
  fi
fi

for n in $names; do
  print -rn -- "${values[$n]}" | gh secret set "$n" -R "$repo"
done
print "✓ stored ${names[*]} in $repo"
gh secret list -R "$repo"
