# Architecture

This supersedes the original `plan.txt` blueprint. Same goals (GitOps specs, Zoom API for session
control, local macOS automation for the room), with the failure modes below designed out.

## What changed from the original plan, and why

| Original | Problem | Now |
|---|---|---|
| Cron at T‑10 triggers the run | GitHub `schedule` is best-effort; 5–30 min delays are common | Dispatcher every 15 min with a **3 h lookahead**; the room job sleeps to exact times; watchdog alerts on late/stuck/ended runs |
| Fetch `start_url` when scheduling, keep "in node memory" | `start_url` embeds a ZAK that expires (~2 h) | Fetched **fresh at T‑3** and again on every relaunch |
| `open "$START_URL"` | Opens a browser → "Open zoom.us?" prompt blocks an unattended Mac | Converted to `zoommtg://…/start?confno=…&zak=…` and opened with zoom.us.app |
| `Cmd+Shift+S` + Return | Can't choose "Display 2"; no verification | AX menu press → pick "Screen N" tile for the configured display → **verify** share is active; keystroke only as fallback |
| `caffeine -t 3600` | Not a macOS command | `caffeinate -dimsu -w <pid>` for the lifecycle's lifetime |
| `ffmpeg` probes | Not installed; can't tell black/silent from present | AVFoundation frame rate + luma, CoreAudio RMS dBFS, ScreenCaptureKit display luma |
| `PUT /meetings/{id}/status` | Wrong endpoint for webinars | `PUT /webinars/{id}/status {"action":"end"}` |
| Create on every run | Duplicates; no update/delete | Idempotent reconcile via `[zc:<uid>:<hash>]` agenda marker |
| Self-hosted runner on the repo | Public repo + self-hosted runner = fork PRs run on room hardware | Runners register **only** to the private config repo; public repo CI is GitHub-hosted |
| Grant Accessibility to Terminal/bash | Over-broad; grants break on updates | Signed, notarized `roomagent` with a stable code requirement |
| PPPC "so macOS never prompts for mic/camera" | PPPC cannot pre-approve Camera/Microphone, and can only delegate Screen Recording | One-time approval as `av-runner`, documented; PPPC handles Accessibility |
| Health loop only `echo`s | Silent failures | Alerts on every non-OK transition + a final status for every run |

## Components

### `zoomctl` (Python, `ctl/`)
* `spec.py` — pydantic models for `Room` and `ZoomWebinar` (apiVersion `zoomcontrol/v1`), cross-file
  validation (unknown rooms, duplicate uids, overlapping events incl. preflight/grace windows).
* `zoom/tokens.py` — user-level OAuth for one host account: PKCE login, single-use refresh-token
  rotation under a cross-process lock, macOS keychain store. No account-wide credential exists.
* `zoom/client.py` — acts only as that host (`/users/me/...`), verifies identity before writes, retry
  with backoff on 429/5xx
  honouring `Retry-After`, pagination.
* `reconcile.py` — plan/apply. Managed webinars are found by marker under each room's host user;
  unmarked webinars are never touched; past events are never modified; orphans are pruned only
  if in the future.
* `schedule.py` — dispatch decisions: `dispatch`, `late-dispatch` (alert), `exists`,
  `stuck-queued` (alert: runner offline?), `run-ended` (alert: room run died before event end).
* `node/lifecycle.py` — the state machine that runs on the node (see below).
* `node/agent.py` — `roomagent` wrapper. Missing/invalid JSON, timeouts and crashes are FAIL.

### `roomagent` (Swift, `agent/`)
* `RoomAgentCore` — all decision logic behind protocols; unit-tested with fakes on macOS and Linux.
* `RoomAgentMac` — adapters: AVFoundation, CoreAudio (default input routing), NSScreen/CGDisplay,
  ScreenCaptureKit, Accessibility (AXUIElement), NSWorkspace, Network.framework.
* CLI prints exactly one JSON report per command; exit 0 = ok/warn, 2 = fail, other = crash.
  `roomagent devices` lists names for room YAML; `roomagent ax-dump` calibrates Zoom UI strings.

## Event lifecycle (on the node)

```
T-3h..  dispatched; job starts on room runner, sleeps
T-15    preflight ── fail → CRITICAL alert, retry every 60 s until T-3, then proceed degraded
T-3     fresh start_url → launch (zoommtg) → wait for meeting window → share display → route AV
live    every 15 s health:
          zoom_running / in_meeting fail → quit -f, fresh start_url, relaunch, re-share  (budget)
          sharing fail                    → re-share                                      (budget)
          video/audio/display/lock fail   → WARN alert once, mark degraded, INFO when recovered
          budget exhausted                → CRITICAL, stop, manual intervention
end+grace  API end webinar → graceful quit → force quit → final report (success/degraded/failed)
```

Health check names are the contract between `roomagent` and `zoomctl`
(`zoom_running`, `in_meeting`, `sharing`, `screen_unlocked`, `display_present`,
`display_not_black`, `video_signal`, `audio_signal`).

## Scaling beyond ~20 rooms

All lifecycle logic lives in `zoomctl node run-event`; GitHub Actions is only transport. To scale,
replace the runner with a pull agent (launchd + long-poll or MQTT) that invokes the same command —
the specs, reconcile, lifecycle and `roomagent` are unchanged.
