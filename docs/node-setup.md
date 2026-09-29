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
git clone https://github.com/pubino/zoom-control && cd zoom-control/node
DRY_RUN=1 ./bootstrap.zsh --room room-101 --config-repo pubino/zoom-control-config --version v0.1.0
./bootstrap.zsh          --room room-101 --config-repo pubino/zoom-control-config --version v0.1.0
```

This creates the standard user `av-runner`, disables sleep, enables auto-restart after power loss plus
a daily 04:00 restart, sets automatic login, installs the notarized `roomagent` pkg (it refuses
unsigned builds), and registers a GitHub Actions runner labelled `macOS,room-101` **on the private
config repo only**, running as a LaunchAgent in `av-runner`'s GUI session. The runner's `.env` pins
`ZOOMCTL_ROOM=room-101`, so the node refuses events for other rooms.

## 3. One-time privacy approvals

Log in as `av-runner` (or let auto-login do it) and follow [security.md](security.md): deploy the PPPC
profile, then approve Camera, Microphone and Screen Recording once for `roomagent` and `zoom.us`.
The fastest way to trigger every prompt:

```zsh
roomagent preflight --video-device "<name>" --audio-device "<name>" --display "<name>" --pretty
```

## 4. Discover device names → room YAML

```zsh
roomagent devices --pretty
```

Copy the exact names into `rooms/room-101.yaml` in the config repo (matching is case-insensitive
and allows a unique substring, but exact is best).

## 5. Zoom client

* Deploy `mdm/us.zoom.config.plist` (verify its keys against Zoom's current docs first).
* Launch zoom.us once as `av-runner` and dismiss any first-run dialogs.
* Calibrate UI strings if Zoom changed them: start a test meeting, then `roomagent ax-dump > ax.json`
  and compare with `ZoomUIStrings` in `agent/Sources/RoomAgentMac/Zoom.swift`.

## 6. Verify end to end

Run the **Manual start** workflow in the config repo against a test event, then check that:
share is active on the podium display, `killall zoom.us` triggers a relaunch within ~30 s, and
the webinar ends via the API at end + grace.

## Updating / decommissioning

```zsh
./update.zsh --version v0.2.0                                   # between events only
./teardown.zsh --config-repo pubino/zoom-control-config [--delete-user]
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
