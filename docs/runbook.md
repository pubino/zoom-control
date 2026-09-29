# Runbook

All actions are in the **private config repo → Actions** tab.

| Situation | Alert you'll see | Do this |
|---|---|---|
| New/changed event | PR plan comment | Review the plan, merge. `Reconcile` applies it. |
| Event not dispatched in time | `late-dispatch` | Nothing: it is dispatched immediately. Check GitHub status if it repeats. |
| Room runner offline | `stuck-queued` at T‑15 | Check the node's power/network. Run it on another node with `Manual start` if needed. |
| Room run died early | `run-ended` | Check the failed run's log, then `Manual start` (event uid + room). |
| Preflight failed | `preflight failed — retrying` | Check the named device (cable, power, Dante routing). The job keeps retrying until T‑3 and then goes live degraded. |
| Relaunch loop | `recovery budget exhausted` | Remote in (Screen Sharing) to the node, `roomagent health …`, then `Manual start`. |
| Share lost | `re-sharing screen` | Automatic. If it repeats, calibrate UI strings (`roomagent ax-dump`). |
| Signal issues | `health: audio_signal failing` etc. | Hardware or routing: the webinar stays live. Fix locally. A `recovered:` notice follows. |
| End now | — | `Manual end` (event uid), or cancel the `Run event` run (the room resets via `quit --force`). |
| Cancel an event | — | Delete its YAML in a PR. Reconcile deletes the future webinar. |
| Zoom authorization dead | `Zoom authorization for … is not usable` / `Zoom rejected the refresh token` | On the node, in the av-runner GUI session: `~/.zoomctl/venv/bin/zoomctl auth login --host <host>`. Then re-run the failed job. |
| API jobs stuck queued | Reconcile/plan never start | The `zoom-api` runner is offline: check `~av-runner/actions-runner-api` and the node's power and network. |

## Useful node commands (as `av-runner`)

```zsh
roomagent devices --pretty
roomagent health --video-device "…" --audio-device "…" --display "…" --pretty
roomagent quit --force
roomagent ax-dump --depth 8 > ~/ax.json
```

## Local dry-runs

```zsh
zoomctl validate -c path/to/zoom-control-config
zoomctl dispatch -c path/to/zoom-control-config --dry-run --now 2026-09-10T13:00:00-04:00
```
