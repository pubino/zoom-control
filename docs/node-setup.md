# Room node setup

Target: Mac mini (Apple silicon or Intel), macOS 14+, wired Ethernet, capture device, audio
interface (e.g. Dante Virtual Soundcard), podium display.

## 1. Prerequisites (as an admin user)

```zsh
brew install gh python@3.12
gh auth login            # account with admin on the private config repo
```

## 2. Bootstrap

```zsh
git clone https://github.com/pu-shd/zoom-control && cd zoom-control/node
DRY_RUN=1 ./bootstrap.zsh --room room-101 --config-repo pu-shd/zoom-control-config --version v0.1.1 --api-runner
./bootstrap.zsh          --room room-101 --config-repo pu-shd/zoom-control-config --version v0.1.1 --api-runner
```

This creates the standard user `av-runner`, disables sleep, enables auto-restart after power loss plus
a daily 04:00 restart, sets automatic login, installs the notarized `roomagent` pkg (it refuses
unsigned builds), and registers a GitHub Actions runner labelled `macOS,room-101` **on the private
config repo only**, running as a LaunchAgent in `av-runner`'s GUI session. The runner's `.env` pins
`ZOOMCTL_ROOM=room-101`, so the node refuses events for other rooms.

`--api-runner` registers a second runner (`room-101-api`, label `zoom-api`) for jobs that call
the Zoom API: PR plan, reconcile, check, manual end. They can then run while the room runner is
busy with an event. Use it on the node that holds the host's authorization (next step). Bootstrap
also installs `zoomctl` for interactive use at `~av-runner/.zoomctl/venv/bin/zoomctl`.

## 2b. Authorize the host account

In the `av-runner` GUI session (after the reboot that enables auto-login):

```zsh
~/.zoomctl/venv/bin/zoomctl auth login --host orfetalks@princeton.edu
```

Sign in **as the host account** in the browser that opens. Details and troubleshooting are in
[zoom-setup.md](zoom-setup.md).

## 3. One-time privacy approvals and calibration

Deploy the PPPC profile ([security.md](security.md)), then work through
**[calibration.md](calibration.md)** as `av-runner`. It covers the Camera, Microphone and Screen
Recording approvals, checking Zoom's UI strings with `roomagent calibrate`, choosing the launch
mode, and verifying Zoom's managed preferences.

## 4. Discover device names → room YAML

```zsh
roomagent devices --pretty
```

Copy the exact names into `rooms/room-101.yaml` in the config repo (matching is case-insensitive
and allows a unique substring, but exact is best).

## 5. Zoom client

* Deploy `mdm/us.zoom.config.plist` (verify its keys against Zoom's current docs first).
* Launch zoom.us once as `av-runner` and dismiss any first-run dialogs.
* Calibrate UI strings and launch mode: [calibration.md](calibration.md) §2–3.

## 6. Verify end to end

Run the **Manual start** workflow in the config repo against a test event, then check that:
share is active on the podium display, `killall zoom.us` triggers a relaunch within ~30 s, and
the webinar ends via the API at end + grace.

## Updating / decommissioning

```zsh
./update.zsh --version v0.2.0                                   # between events only
./teardown.zsh --config-repo pu-shd/zoom-control-config [--delete-user]
```

## FileVault

macOS disables automatic login when FileVault is on, so after a power cut the node would sit at
the pre-boot unlock screen. Options: (a) leave FileVault off on the physically secured closet Mac
(the node holds no long-lived secrets — see security.md); (b) keep FileVault on and use
`fdesetup authrestart` for planned reboots, accepting manual unlock after outages.

## Screen lock

Screen sharing and Accessibility need an unlocked GUI session. Disable the screen saver and
"Require password after screen saver" for `av-runner`. `roomagent` reports `screen_unlocked=fail`
if the session is locked.
