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

## Zoom authorization: user-level only

There is **no account-wide Zoom credential** anywhere: no Server-to-Server app, no `:admin`
scopes. The host account (e.g. `orfetalks@princeton.edu`) authorizes a user-managed General
app once, and its tokens can act only as that user. Zoom enforces this:

* A stolen token can affect only `orfetalks`'s own webinars and profile. It can't read or start
  anyone else's sessions, and it can't list account users.
* `zoomctl` additionally verifies `GET /users/me` matches the configured `zoom_host` before any
  write, and refuses to store tokens at login if the wrong user signed in.
* The Client ID/Secret and tokens live **only** in the node's login keychain (`av-runner`,
  service `zoom-control`), created with an ACL for `/usr/bin/security` only. They are never in
  GitHub, never in argv of long-running processes, and never logged. (`security add-generic-password
  -w` does briefly carry the JSON in argv; macOS shows other users' process arguments only to root.)
* Refresh tokens are single-use and expire after 90 days unused. Each rotation is written
  atomically under a lock before use. Revoke at any time with `zoomctl auth logout --host …`
  or Zoom web ▸ *Apps* ▸ *Installed* ▸ remove.

**What still matters.** The config repo's `zoom-api` jobs run on the node with access to that
keychain. Anyone with **write access to the private config repo** can run code there (for
example by editing a workflow in a PR), and so can act as `orfetalks`. Keep collaborators to the
people who operate the room, require review on `main`, and keep fork PR workflows disabled.

## GitHub secrets

| Secret | Used by | Notes |
|---|---|---|
| `ALERT_WEBHOOK_URL` | all | Slack-compatible `{"text": …}` incoming webhook. The only secret. |

Signing keys and notarization credentials never leave the release manager's Mac: releases are
cut locally with `scripts/release.zsh` ([releasing.md](releasing.md)), and CI only *verifies* published
assets.

The `start_url` (which contains a short-lived ZAK host token) goes to `roomagent` **on stdin**,
never argv, and is redacted in all output. `zoomctl start-url` refuses to print it to a terminal.

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
