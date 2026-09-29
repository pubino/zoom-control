# Zoom & alert setup (config repo secrets)

The private config repo needs four Actions secrets:

| Secret | Where it comes from |
|---|---|
| `ZOOM_ACCOUNT_ID` | Zoom Marketplace ▸ your Server-to-Server OAuth app ▸ **App Credentials** |
| `ZOOM_CLIENT_ID` | same page |
| `ZOOM_CLIENT_SECRET` | same page |
| `ALERT_WEBHOOK_URL` | a Slack incoming webhook (or a Teams workflow webhook) |

## 1. Prerequisites in Zoom

* A Zoom account on a plan with **Webinars**. Every room's `zoom_host` must be a **Licensed** user
  with a **Webinar license** assigned (Admin ▸ User Management ▸ Users ▸ edit user ▸ Webinar).
  One host per room is simplest. A single host can serve several rooms only if their events never
  overlap, because a host can run one webinar at a time.
* You need a role that may create Server-to-Server OAuth apps. Account owners and admins have it.
  Otherwise an owner grants it under Admin ▸ User Management ▸ Roles ▸ *Role Settings* ▸
  Advanced features ▸ **Server-to-Server OAuth app** (View + Edit).

## 2. Create the Server-to-Server OAuth app

1. Go to <https://marketplace.zoom.us> ▸ **Develop** ▸ **Build App** ▸ **Server to Server OAuth App**.
   Name it e.g. `zoom-control`.
2. **App Credentials**: copy **Account ID**, **Client ID**, **Client Secret**.
3. **Information**: fill in the required company/contact fields.
4. **Scopes** ▸ *Add Scopes*. Search for and add these (granular names):

   | Scope | Used for |
   |---|---|
   | `webinar:read:list_webinars:admin` | find managed webinars (reconcile) |
   | `webinar:read:webinar:admin` | fetch a fresh `start_url` |
   | `webinar:write:webinar:admin` | create webinars |
   | `webinar:update:webinar:admin` | update webinars when a spec changes |
   | `webinar:delete:webinar:admin` | delete webinars whose spec was removed |
   | `webinar:update:status:admin` | end a webinar at teardown |
   | `user:read:user:admin` | `zoomctl check`: host exists and is licensed |
   | `user:read:settings:admin` | `zoomctl check`: host has a webinar license |

   Zoom occasionally renames scopes. If a name isn't found, pick the equivalent under
   *Webinar* / *User*. `zoomctl check` reports any missing scope by name (Zoom error 4711).
5. **Activation** ▸ **Activate your app**. An inactive app returns `invalid_client`.

## 3. Alert webhook

* **Slack**: <https://api.slack.com/apps> ▸ Create App ▸ *Incoming Webhooks* ▸ On ▸
  *Add New Webhook to Workspace* ▸ pick the AV-ops channel ▸ copy `https://hooks.slack.com/services/…`.
* **Microsoft Teams**: in the channel ▸ **Workflows** ▸ *Post to a channel when a webhook request
  is received* ▸ copy the URL. zoom-control posts `{"text": "…"}`.

## 4. Verify, then store

From a checkout of zoom-control, with `zoomctl` installed in `ctl/.venv`:

```zsh
scripts/set-config-secrets.zsh --repo pubino/zoom-control-config --config ../zoom-control-config
```

The script prompts for each value without echoing it. It then runs
`zoomctl check --test-alert` against your room files and stores the secrets with `gh secret set`
**only if every check passes**. The expected output looks like:

```
✓ credentials: S2S OAuth token issued
✓ host av-101@example.edu (room-101): webinar license (500 attendees)
✓ host av-101@example.edu (room-101) webinars: list permitted
✓ alert webhook accepted the test message
✓ stored ZOOM_ACCOUNT_ID ZOOM_CLIENT_ID ZOOM_CLIENT_SECRET ALERT_WEBHOOK_URL in pubino/zoom-control-config
```

Replace the example `zoom_host` in `rooms/*.yaml` with your real host(s) **before** running this,
or the host checks fail. To rotate one secret later: `--only ZOOM_CLIENT_SECRET`, which still
needs the other Zoom values for verification, or add `--no-verify`.

Then re-run the failed **Reconcile** workflow (Actions ▸ Reconcile ▸ Re-run) or push a spec change.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `invalid_client` | wrong Client ID/Secret, or the app is not activated |
| `Invalid access token, does not contain scopes:[…]` (4711) | add the listed scope, then re-activate if prompted |
| `User does not exist` (1001) | `zoom_host` typo, or the user is in a different account |
| `user is Basic (unlicensed)` | assign a Licensed seat plus a Webinar license |
| Webhook HTTP 403/404 | the webhook was revoked or the URL was copied incompletely |
