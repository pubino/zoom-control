# One-time node calibration

Do this once per room node after [node setup](node-setup.md), and again after a major Zoom
client update. It takes about 20 minutes. Everything runs **as `av-runner`, logged in at the
node's own display** (in person or over Screen Sharing), because macOS privacy prompts and the
Accessibility tree only exist in the GUI session.

You need a Zoom account you can start a test meeting with. Use the room's host account, or any
licensed account for steps 2–3.

## 1. Privacy approvals (Camera, Microphone, Screen Recording)

PPPC profiles cannot pre-approve Camera or Microphone, and can only delegate Screen Recording
(see [security.md](security.md)). Trigger every prompt once and click **Allow**:

```zsh
roomagent preflight --video-device "<capture>" --audio-device "<mic>" --display "<podium>" --pretty
```

* **Camera / Microphone**: prompts appear on the first run. If nothing appears and the report says
  `access not granted`, open System Settings ▸ Privacy & Security ▸ Camera / Microphone and enable
  `roomagent`. It may be listed under the runner, `Runner.Listener`, when started from a job.
* **Screen Recording**: System Settings ▸ Privacy & Security ▸ Screen & System Audio Recording ▸
  enable `roomagent` **and** `zoom.us`.
* **Accessibility**: granted by the PPPC profile. Without MDM, enable `roomagent` (and
  `Runner.Listener`) under Privacy & Security ▸ Accessibility.

Re-run until `display_not_black`, `video_signal` and `audio_signal` show real measurements
instead of `access not granted` errors.

**Also approve for the job context.** Run **Manual start** once against a test event and watch
the node's screen. Any prompt that appears now belongs to the runner process; allow it.

## 2. Zoom UI strings

`roomagent` finds Zoom's meeting window, *Share Screen* menu item, share picker and share toolbar
by their Accessibility titles. Zoom renames these between releases. Check them against your
installed client:

1. Start a test meeting in zoom.us. Stay in the meeting and **don't** share.
2. Run:
   ```zsh
   roomagent calibrate --pretty
   ```
   Every check should be `ok`. A `fail` shows what was actually seen, for example
   `no startShareItems matched …; share-like items: Share content | …`.
3. Start sharing the podium display manually, then run:
   ```zsh
   roomagent calibrate --sharing --pretty
   ```
   `sharing_detected` must be `ok`.
4. If anything failed, write an override with **only** the keys that need changing:
   ```zsh
   roomagent ui-strings > ~/ui-strings.json     # current effective values, for reference
   sudo mkdir -p "/Library/Application Support/zoom-control"
   sudo tee "/Library/Application Support/zoom-control/ui-strings.json" >/dev/null <<'JSON'
   {
     "startShareItems": ["Share content", "Share Screen"],
     "stopShareItems":  ["Stop sharing", "Stop Share"]
   }
   JSON
   roomagent calibrate --pretty                  # ui_strings_source now shows the file
   ```
   Keys: `meetingWindowTitles`, `meetingMenu`, `startShareItems`, `stopShareItems`,
   `shareToolbarWindows`, `sharePickerButtons`, `sharePickerTiles` (`{n}` is the display's number
   in the picker, `{name}` its name). An unknown key or malformed file makes every command fail
   loudly instead of silently using defaults. `roomagent ax-dump --depth 8` prints the full tree if
   you need to hunt for a title.

   Lookup order: `$ROOMAGENT_UI_STRINGS`, then
   `~/Library/Application Support/zoom-control/ui-strings.json`, then the system path above.
   Keep the system path for fleet-wide values; deploy it with MDM or `scp`.
5. **Test the share picker.** End the manual share, then:
   ```zsh
   roomagent share --video-device "<capture>" --audio-device "<mic>" --display "<podium>" --pretty
   ```
   It should report `sharing: ok (via accessibility)` and the podium display should be what
   attendees see. If it picks the wrong screen, adjust `sharePickerTiles`.

If you've fixed strings that apply to every node, please open an issue or PR on zoom-control so
the defaults can be updated.

## 3. Launch mode (start_url → Zoom client)

By default the host `start_url` is converted to `zoommtg://…/start?confno=…&zak=…` and opened
directly in zoom.us, so no browser prompt appears. Verify it on this node:

1. Leave any meeting and quit Zoom.
2. Get a fresh start URL for a test webinar (it expires within about 2 hours). Use Zoom web ▸
   Webinars ▸ your test webinar ▸ *Start*, and copy the link from the browser, or run on a machine
   with the Zoom secrets:
   ```zsh
   python -c 'from zoomctl.zoom import *; print(ZoomClient(Credentials.from_env()).fresh_start_url(WEBINAR_ID))'
   ```
3. Launch it:
   ```zsh
   pbpaste | roomagent launch --start-url-stdin --mode zoommtg --pretty
   ```
   Expect `zoom_running: ok` and `in_meeting: ok` with no dialog on screen.
4. If it hangs on a dialog or `in_meeting` fails, try the fallback:
   ```zsh
   roomagent quit --force; pbpaste | roomagent launch --start-url-stdin --mode https --pretty
   ```
   If that works, set `launch_mode: https` under `spec:` in this room's YAML in the config repo.
5. End the test webinar: `roomagent quit` (or `zoomctl end`).

## 4. Zoom client preferences

`mdm/us.zoom.config.plist` suppresses first-run and audio-choice dialogs. Confirm each key against
Zoom's current article *"Mass deploying with preconfigured settings for macOS"*, deploy it, then
check that it took effect:

```zsh
defaults read /Library/Preferences/us.zoom.config    # or the MDM-managed domain
```

Then join a test meeting and confirm there is **no** "Join with Computer Audio" dialog and no
update prompt.

## 5. Record the result

In the config repo, update `rooms/<room>.yaml` with the verified device names and `launch_mode`.
Add a comment with the Zoom client version you calibrated against
(`defaults read /Applications/zoom.us.app/Contents/Info.plist CFBundleShortVersionString`).
Recalibrate when Zoom's major version changes.

## Checklist

- [ ] `roomagent preflight` shows real measurements (no permission errors)
- [ ] Manual start from GitHub triggered no new prompts
- [ ] `roomagent calibrate` all ok; `calibrate --sharing` shows `sharing_detected: ok`
- [ ] `roomagent share` shares the podium display
- [ ] `launch` works with the chosen `launch_mode`, with no dialogs
- [ ] Zoom managed prefs verified; no audio or update dialogs
- [ ] Room YAML updated with names, `launch_mode` and the calibrated Zoom version
