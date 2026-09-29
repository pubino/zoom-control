# Security model

## Repo split

* **Public `zoom-control`**: code, templates, docs. No secrets, no data, and its CI runs only on
  GitHub-hosted runners, so a fork PR can never reach room hardware.
* **Private `zoom-control-config`**: room and event YAML, secrets, and the only place room runners
  are registered (repo-scoped). Disable "Run workflows from fork pull requests" and require review
  on `main`.
* The config repo pins `pubino/zoom-control/.github/actions/setup-zoomctl@<tag>`, so a change in the
  public repo only takes effect after a reviewed bump (Dependabot opens it). For maximum assurance,
  pin a commit SHA instead of a tag.

## Secrets

| Secret | Used by | Notes |
|---|---|---|
| `ZOOM_ACCOUNT_ID` / `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` | validate, reconcile, run-event, manual-* | S2S OAuth app. Scope it to webinar read/write/status only. |
| `ALERT_WEBHOOK_URL` | all | Slack-compatible `{"text": …}` incoming webhook |
| `DEVELOPER_ID_*`, `APPLE_*` | public repo `release` environment | Only exposed to tag builds via environment protection |

Secrets reach a room node only for the duration of a job. The `start_url` (which contains a ZAK
host token) goes to `roomagent` **on stdin**, never argv, and is redacted in all output.
Use per-room GitHub Environments (created automatically as `room-XXX`) if you want to add required
reviewers or scope secrets per room.

## macOS privacy (TCC)

| Permission | Needed by | How to grant |
|---|---|---|
| Accessibility | `roomagent` (UI scripting of Zoom) | PPPC profile `mdm/roomagent-pppc.mobileconfig` (**Allow**) |
| Screen Recording | `roomagent` (display luma), `zoom.us` (share) | PPPC can only delegate (`AllowStandardUserToSetSystemService`); approve once as `av-runner` |
| Camera, Microphone | `roomagent` (signal probes), `zoom.us` | **Cannot** be pre-approved by any profile; approve once as `av-runner` |

* `roomagent` is Developer ID signed with identifier `us.zoomcontrol.roomagent`. Grants are bound to
  its code requirement (`agent/scripts/notarize.zsh` prints it), so they survive upgrades as long as
  the team ID stays the same. Unsigned builds change hash every build: never deploy them.
* TCC attributes some requests to the *responsible process*. The job runs under the runner's
  `Runner.Listener`, so the PPPC template also lists it for Accessibility. If a prompt still
  appears, check `log stream --predicate 'subsystem == "com.apple.TCC"'` while running
  `roomagent preflight` from a job.
* macOS 15+ re-confirms Screen Recording periodically. On supervised devices, the profile's
  `forceBypassScreenCaptureAlert` suppresses this. Otherwise expect the occasional prompt and
  watch for `display_not_black=fail` alerts.

## Node hardening

* `av-runner` is a standard (non-admin) user. Bootstrap/update/teardown run from an admin account.
* The daily 04:00 restart clears leaks. Auto-restart after power loss is on.
* Zoom auto-update is disabled by managed prefs; update the client in maintenance windows.
