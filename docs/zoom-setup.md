# Zoom setup: user-level OAuth for the host account

zoom-control never holds an account-wide Zoom credential. It uses a **user-managed General
app** that the **host account itself** authorizes, for example `orfetalks@princeton.edu`. The
resulting tokens can act only as that user: its own webinars, its own profile. They cannot see or
change anyone else in the Princeton account, and Zoom enforces that, not this code.

| What | Where it lives |
|---|---|
| App Client ID / Client Secret | Only in the node's keychain, stored at `zoomctl auth login` |
| Host access and refresh tokens | Only in the node's keychain (`av-runner` login keychain, service `zoom-control`) |
| `ALERT_WEBHOOK_URL` | The only GitHub secret, in the config repo |

## 1. What to ask OIT for

Individual Princeton users can't create Marketplace apps, so OIT (the Zoom account admins) must
either create the app or let you create it. The request:

* **App type:** *General app*, **User-managed**, **not published** (internal to the Princeton
  account only).
* **Scopes (user-level only, no `:admin` scopes):**

  | Scope | Used for |
  |---|---|
  | `webinar:read:list_webinars` | find the host's managed webinars |
  | `webinar:read:webinar` | read a webinar and get a fresh host `start_url` |
  | `webinar:write:webinar` | create webinars |
  | `webinar:update:webinar` | update webinars when a spec changes |
  | `webinar:delete:webinar` | cancel webinars whose spec was removed |
  | `webinar:update:status` | end the webinar at the scheduled time |
  | `user:read:user` | confirm the token belongs to the host (identity check) |
  | `user:read:settings` | confirm the host has a Webinar license |

  Exact names can differ slightly in the scope picker. Choose the *non-admin* variant of each.
  `zoomctl check` reports any missing scope by name (Zoom error 4711).
* **Redirect URL and OAuth allow list:** `http://127.0.0.1:8765/zoom/callback`. Use 127.0.0.1,
  not `localhost`, which Zoom has recently rejected. If OIT requires HTTPS, any HTTPS URL they
  control works. `zoomctl auth login --redirect-uri <url>` then asks you to paste the redirected
  URL instead of catching it locally.
* **Installation:** allow `orfetalks@princeton.edu` to authorize (install) this app. Pre-approve
  it if the account requires admin approval for Marketplace apps.
* **Hand-off:** the Client ID and Client Secret, sent securely. They're entered once on the node
  and never stored in GitHub.

The host account needs a **Licensed** seat and a **Webinar license** (`orfetalks` has one). Your
own account needs neither.

## 2. Authorize on the node (once, and again only if revoked or unused for 90 days)

On the room Mac, **in the `av-runner` GUI session** (log in at the console or over Screen
Sharing; the login keychain must be unlocked):

```zsh
~/.zoomctl/venv/bin/zoomctl auth login --host orfetalks@princeton.edu
```

1. Enter the Client ID and Client Secret when prompted. The secret is not echoed.
2. A browser opens the Zoom consent page. **Sign in as `orfetalks@princeton.edu`** (use a private
   window if you're signed in to Zoom as yourself) and approve.
3. `zoomctl` catches the redirect on 127.0.0.1 and exchanges the code (PKCE). It then calls
   `GET /users/me`: **if the signed-in user isn't the `--host`, the token is revoked and nothing is
   stored.**
4. Tokens are saved in the keychain.

Then check everything end to end:

```zsh
~/.zoomctl/venv/bin/zoomctl auth status
~/.zoomctl/venv/bin/zoomctl check -c ~/zoom-control-config --test-alert   # or run the "Check Zoom setup" workflow
```

## 3. Alert webhook (the only GitHub secret)

* **Slack:** <https://api.slack.com/apps> ▸ Create App ▸ *Incoming Webhooks* ▸ On ▸
  *Add New Webhook to Workspace* ▸ copy `https://hooks.slack.com/services/…`.
* **Teams:** channel ▸ **Workflows** ▸ *Post to a channel when a webhook request is received*.

```zsh
gh secret set ALERT_WEBHOOK_URL -R pu-shd/zoom-control-config   # paste when prompted
```

Then run **Check Zoom setup** from the config repo's Actions tab. It posts a test alert.

## How the tokens stay alive

* Access tokens last about 1 hour. Refresh tokens are **single-use**: each refresh returns a new
  one and invalidates the old one. An unused refresh token expires after **90 days**.
* Every refresh happens under a file lock and is written to the keychain **before** the token is
  used. So concurrent jobs on the node (a room event plus a reconcile) never waste the single-use
  token.
* The nightly **Reconcile** refreshes the token daily, so it never nears 90 days.
  `zoomctl check` warns once a token hasn't rotated in 60 days.
* If Zoom rejects the refresh token (revoked, expired, app changed), every job fails with a
  critical alert that says exactly what to run: `zoomctl auth login --host …`. The room job checks
  this **at dispatch time**, hours before the event, not at T-3.

**One node per host.** Because refresh tokens are single-use, a host's authorization lives on
exactly one node, the one running the `zoom-api` runner. Rooms on other Macs can't share it. If
you add a second closet Mac, give it its own host account or ask to extend the design.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `you signed in as bino@princeton.edu, but --host is orfetalks@…` | Sign out of Zoom in the browser or use a private window, then sign in as the host |
| `invalid_client` at login | wrong Client ID/Secret, or the app is disabled |
| redirect page shows an error | Redirect URL / allow list doesn't exactly match `http://127.0.0.1:8765/zoom/callback` |
| `does not contain scopes:[…]` (4711) | OIT must add that (user-level) scope; then re-run `auth login` |
| `keychain read failed … unlocked` | Run in the av-runner GUI session, or check that auto-login is on |
| `Zoom rejected the refresh token` | Re-run `zoomctl auth login --host …` on the node |
| `user is Basic (unlicensed)` / `no Webinar license` | Ask OIT to assign a Licensed seat plus a Webinar license to the host |
