# zoom-control

Declarative, self-healing Zoom Webinar automation for unattended macOS room nodes
(closet Mac minis driving a podium capture, room mic matrix and podium display).

```
zoom-control-config (private)                     zoom-control (this repo, public)
 rooms/*.yaml  events/*.yaml                       zoomctl  — Python controller / lifecycle
 .github/workflows/*  ── uses setup-zoomctl@vX ──► roomagent — Swift macOS agent (notarized)
   PR ─ validate + plan comment      (GitHub-hosted)
   push/nightly ─ reconcile → Zoom   (GitHub-hosted, idempotent)
   cron */15 ─ dispatch, 3h lookahead + missed-start watchdog
   run-event ─ runs-on [self-hosted, macOS, room-XXX]
        zoomctl node run-event ─► roomagent preflight | launch | share | select-av | health | quit
```

* **GitOps**: rooms and events are YAML in a private config repo; merging a PR creates/updates/deletes
  webinars. State lives in Zoom itself (an agenda marker), so re-runs never duplicate.
* **Reliable timing**: GitHub cron is best-effort, so the dispatcher looks 3h ahead and the room job
  sleeps until exact lifecycle times (T‑15 preflight, T‑3 fresh `start_url`, end + grace teardown).
* **No blind clicking**: `roomagent` uses AVFoundation/CoreAudio/ScreenCaptureKit to *measure*
  signal, and the Accessibility API to share a specific display — then verifies it worked.
* **Self-healing**: crash → relaunch with a fresh ZAK, lost share → re-share, bounded by a
  recovery budget. Every non-OK transition and every final result is alerted. Silence ≠ success.

| Path | What |
|---|---|
| [`ctl/`](ctl) | `zoomctl` Python package (specs, user-level Zoom OAuth client, reconcile, dispatch, lifecycle) |
| [`agent/`](agent) | `roomagent` SwiftPM package + build/run/test/notarize scripts |
| [`.github/actions/setup-zoomctl`](.github/actions/setup-zoomctl) | Composite action the config repo pins |
| [`examples/config/`](examples/config) | Template for the private config repo (rooms, events, workflows) |
| [`node/`](node) | zsh bootstrap / update / teardown for room Macs |
| [`mdm/`](mdm) | PPPC profile + Zoom managed-preferences templates |
| [`schemas/`](schemas) | JSON Schemas for room/event YAML (editor validation) |
| [`docs/`](docs) | [architecture](docs/architecture.md) · [Zoom setup](docs/zoom-setup.md) · [node setup](docs/node-setup.md) · [calibration](docs/calibration.md) · [releasing](docs/releasing.md) · [security](docs/security.md) · [runbook](docs/runbook.md) |

## Quick start (development)

```zsh
# Python controller
cd ctl && python3.12 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'
pytest && zoomctl validate -c ../examples/config

# Swift agent (macOS)
agent/scripts/test.zsh
agent/scripts/run.zsh devices --pretty

# Everything in containers
docker-compose run --rm test         # zoomctl tests + schema drift + example validation
docker-compose run --rm test-swift   # RoomAgentCore tests on Linux
```

## Deploying

1. **Release** a signed, notarized build from your Mac: `scripts/release.zsh 0.1.1 --config-repo ../zoom-control-config`
   ([docs/releasing.md](docs/releasing.md)).
2. **Zoom**: OIT creates a user-managed General app with user-level scopes only. The host account
   (e.g. `orfetalks@princeton.edu`) authorizes it on the node with `zoomctl auth login`. There is no
   account-wide credential anywhere ([docs/zoom-setup.md](docs/zoom-setup.md)). Add the one GitHub
   secret, `ALERT_WEBHOOK_URL`.
3. **Nodes**: provision each Mac with `node/bootstrap.zsh` ([docs/node-setup.md](docs/node-setup.md)), then do the
   one-time [calibration](docs/calibration.md) (privacy approvals, Zoom UI strings, launch mode).
4. **Events**: add them by PR in the config repo. Review the plan comment, merge, done.

## License

MIT — see [LICENSE](LICENSE).
