"""zoomctl command-line interface."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .notify import Level, WebhookNotifier
from .reconcile import Action, apply, find_webinar_id, plan
from .schedule import Gh, decide, execute
from .spec import ConfigError, json_schemas, load_config
from .zoom import UserTokenProvider, ZoomClient, ZoomError, default_store
from .zoom.tokens import TokenStore

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
node_app = typer.Typer(no_args_is_help=True, help="Commands that run on a room node.")
app.add_typer(node_app, name="node")
auth_app = typer.Typer(no_args_is_help=True, help="User-level Zoom authorization for host accounts (run on the node).")
app.add_typer(auth_app, name="auth")

ConfigOpt = Annotated[Path, typer.Option("--config", "-c", envvar="ZOOMCTL_CONFIG",
                                         help="Config repo root containing rooms/ and events/")]
NowOpt = Annotated[str | None, typer.Option("--now", help="Override current time (ISO 8601), for testing")]


def _now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise typer.BadParameter("--now must include a UTC offset")
    return dt.astimezone(timezone.utc)


def _load(config: Path):
    try:
        return load_config(config)
    except ConfigError as exc:
        for p in exc.problems:
            typer.echo(f"✗ {p}", err=True)
        raise typer.Exit(1) from None


def _store() -> TokenStore:
    try:
        return default_store()
    except ZoomError as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(1) from None


def _zoom_for(store: TokenStore | None = None):
    """Factory of per-host clients. Each client can act only as its host."""
    store = store or _store()
    clients: dict[str, ZoomClient] = {}

    def zoom_for(host: str) -> ZoomClient:
        host = host.strip().lower()
        if host not in clients:
            clients[host] = ZoomClient(UserTokenProvider(host, store))
        return clients[host]

    return zoom_for


@app.command()
def version() -> None:
    """Print the zoomctl version."""
    typer.echo(__version__)


@app.command()
def validate(config: ConfigOpt = Path(".")) -> None:
    """Validate rooms and events (schema, references, overlaps)."""
    cfg = _load(config)
    typer.echo(f"✓ {len(cfg.rooms)} room(s), {len(cfg.events)} event(s) valid")


@app.command()
def check(config: ConfigOpt = Path("."),
          test_alert: Annotated[bool, typer.Option(help="Also send a test message to ALERT_WEBHOOK_URL")] = False
          ) -> None:
    """Verify Zoom credentials, scopes, and each room host's webinar license."""
    from .doctor import FAIL, check as run_check

    cfg = _load(config)
    store = _store()
    findings = run_check(cfg, _zoom_for(store), store)
    for f in findings:
        typer.echo(f.line())
    failed = any(f.status == FAIL for f in findings)
    if test_alert:
        notifier = WebhookNotifier.from_env("zoomctl check")
        if not notifier.url:
            typer.echo("✗ ALERT_WEBHOOK_URL is not set", err=True)
            failed = True
        else:
            notifier.send(Level.INFO, "test alert — zoom-control can reach this channel")
            if notifier.failures:
                typer.echo(f"✗ alert webhook: {notifier.failures[-1]}", err=True)
                failed = True
            else:
                typer.echo("✓ alert webhook accepted the test message")
    if failed:
        raise typer.Exit(1)


@app.command()
def schema(out: Annotated[Path, typer.Option(help="Directory to write JSON Schemas")] = Path("schemas"),
           check: Annotated[bool, typer.Option(help="Fail if files differ instead of writing")] = False) -> None:
    """Export (or --check) the JSON Schemas for room and event specs."""
    drift = []
    for name, sch in json_schemas().items():
        text = json.dumps(sch, indent=2, sort_keys=True) + "\n"
        path = out / name
        if check:
            if not path.exists() or path.read_text() != text:
                drift.append(str(path))
        else:
            out.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            typer.echo(f"wrote {path}")
    if drift:
        typer.echo(f"✗ schema drift: {', '.join(drift)} — run `zoomctl schema`", err=True)
        raise typer.Exit(1)
    if check:
        typer.echo("✓ schemas up to date")


@app.command("plan")
def plan_cmd(config: ConfigOpt = Path("."), now: NowOpt = None,
             prune: Annotated[bool, typer.Option(help="Delete managed webinars whose spec was removed")] = True,
             markdown: Annotated[Path | None, typer.Option(help="Also write a Markdown plan here")] = None) -> None:
    """Show what `apply` would change in Zoom."""
    cfg = _load(config)
    changes = plan(cfg, _zoom_for(), now=_now(now), prune=prune)
    lines = [c.describe() for c in changes]
    typer.echo("\n".join(lines) or "no events")
    pending = sum(c.action in (Action.CREATE, Action.UPDATE, Action.DELETE) for c in changes)
    typer.echo(f"\n{pending} change(s) pending")
    if markdown:
        markdown.write_text("### zoomctl plan\n\n```\n" + "\n".join(lines) + f"\n```\n\n**{pending} change(s) pending**\n")


@app.command("apply")
def apply_cmd(config: ConfigOpt = Path("."), now: NowOpt = None,
              prune: Annotated[bool, typer.Option(help="Delete managed webinars whose spec was removed")] = True
              ) -> None:
    """Reconcile Zoom webinars with the specs (idempotent)."""
    cfg = _load(config)
    zoom_for = _zoom_for()
    changes = plan(cfg, zoom_for, now=_now(now), prune=prune)
    errors = apply(changes, zoom_for)
    for c in changes:
        typer.echo(c.describe())
    if errors:
        notifier = WebhookNotifier.from_env("reconcile")
        notifier.send(Level.CRITICAL, f"reconcile had {len(errors)} error(s)", "\n".join(errors))
        raise typer.Exit(1)
    typer.echo("✓ reconciled")


@app.command()
def dispatch(config: ConfigOpt = Path("."), now: NowOpt = None,
             lookahead: Annotated[int, typer.Option(help="Minutes ahead to dispatch")] = 180,
             workflow: Annotated[str, typer.Option(help="Room workflow file")] = "run-event.yml",
             dry_run: Annotated[bool, typer.Option("--dry-run")] = False) -> None:
    """Dispatch room jobs for upcoming events; alert on missed/stuck starts."""
    cfg = _load(config)
    gh = Gh(workflow=workflow)
    decisions = decide(cfg, gh, _now(now), timedelta(minutes=lookahead))
    notifier = WebhookNotifier.from_env("dispatcher")
    for d in decisions:
        typer.echo(f"{d.action:>14}  {d.run_key}  {d.detail}")
        if d.alert:
            notifier.send(Level.CRITICAL, f"{d.uid}: {d.action}", d.detail)
    errors = execute(cfg, gh, decisions, dry_run=dry_run)
    if errors:
        notifier.send(Level.CRITICAL, "dispatch errors", "\n".join(errors))
        raise typer.Exit(1)
    if not decisions:
        typer.echo(f"no events in the next {lookahead} minutes")


@app.command()
def end(event: Annotated[str, typer.Option("--event", "-e")], config: ConfigOpt = Path(".")) -> None:
    """End an event's webinar via the API."""
    cfg = _load(config)
    zoom_for = _zoom_for()
    ev = cfg.event(event)
    wid = find_webinar_id(cfg, zoom_for, ev)
    zoom_for(cfg.room_for(ev).spec.zoom_host).end_webinar(wid)
    typer.echo(f"✓ ended webinar {wid} ({ev.uid})")


@app.command("start-url")
def start_url(event: Annotated[str, typer.Option("--event", "-e")], config: ConfigOpt = Path(".")) -> None:
    """Write a fresh host start_url to a pipe (e.g. `| roomagent launch --start-url-stdin`).

    Refuses to print to a terminal: the URL embeds a host token (ZAK)."""
    if sys.stdout.isatty():
        typer.echo("✗ refusing to print a host start_url to a terminal; pipe it into "
                   "`roomagent launch --start-url-stdin`", err=True)
        raise typer.Exit(1)
    cfg = _load(config)
    ev = cfg.event(event)
    zoom_for = _zoom_for()
    wid = find_webinar_id(cfg, zoom_for, ev)
    sys.stdout.write(zoom_for(cfg.room_for(ev).spec.zoom_host).fresh_start_url(wid) + "\n")


@node_app.command("run-event")
def run_event(event: Annotated[str, typer.Option("--event", "-e")], config: ConfigOpt = Path("."),
              immediate: Annotated[bool, typer.Option(help="Skip waits; start now (manual start)")] = False,
              roomagent: Annotated[str | None, typer.Option(help="Path to roomagent binary")] = None,
              expect_room: Annotated[str | None, typer.Option(envvar="ZOOMCTL_ROOM",
                                                              help="Refuse to run unless event is for this room")]
              = None) -> None:
    """Run the full lifecycle for one event on this node."""
    from .node.agent import RoomAgentCLI, find_roomagent
    from .node.lifecycle import Caffeinate, Lifecycle, Outcome, ZoomOps

    cfg = _load(config)
    ev = cfg.event(event)
    room = cfg.room_for(ev)
    notifier = WebhookNotifier.from_env(f"{room.id} · {ev.uid}")
    if expect_room and expect_room != room.id:
        notifier.send(Level.CRITICAL, f"event is for {room.id}, but this node is {expect_room}; refusing")
        raise typer.Exit(1)
    binary = find_roomagent(roomagent)
    if not binary:
        notifier.send(Level.CRITICAL, "roomagent binary not found on node", "install via node/update.zsh")
        raise typer.Exit(1)

    zoom_for = _zoom_for()
    zoom = zoom_for(room.spec.zoom_host)
    try:  # fail at dispatch time, not hours later at T-3, if the host's authorization is dead
        zoom.verify_identity()
    except ZoomError as exc:
        notifier.send(Level.CRITICAL, f"Zoom authorization for {room.spec.zoom_host} is not usable", str(exc))
        raise typer.Exit(1) from None
    webinar: dict[str, int] = {}

    def wid() -> int:
        if "id" not in webinar:
            webinar["id"] = find_webinar_id(cfg, zoom_for, ev)
        return webinar["id"]

    lc = Lifecycle(ev, room, RoomAgentCLI(binary), ZoomOps(lambda: zoom.fresh_start_url(wid()),
                                                            lambda: zoom.end_webinar(wid())),
                   notifier, keep_awake=Caffeinate(), immediate=immediate)
    result = lc.run()
    typer.echo(json.dumps({"outcome": result.outcome.value, "recoveries": result.recoveries,
                           "degraded": result.degraded_reasons, "error": result.error}, indent=2))
    if result.outcome is Outcome.FAILED:
        raise typer.Exit(2)


@auth_app.command("login")
def auth_login(
    host: Annotated[str, typer.Option(help="Host account email, e.g. orfetalks@princeton.edu")],
    client_id: Annotated[str | None, typer.Option(envvar="ZOOM_CLIENT_ID", help="General app Client ID")] = None,
    redirect_uri: Annotated[str, typer.Option(help="Must exactly match the app's Redirect URL")] = "",
    open_browser: Annotated[bool, typer.Option("--browser/--no-browser", help="Open the sign-in page")] = True,
    timeout: Annotated[int, typer.Option(help="Seconds to wait for the loopback redirect")] = 300,
) -> None:
    """Authorize the user-managed Zoom app AS the host account and store its tokens on this node."""
    import getpass
    import webbrowser

    from .auth import DEFAULT_REDIRECT, complete_login, is_loopback, parse_callback, run_in_thread, wait_for_loopback
    from .zoom.tokens import OAuthApp

    redirect_uri = redirect_uri or DEFAULT_REDIRECT
    client_id = client_id or typer.prompt("Client ID")
    secret = getpass.getpass("Client Secret (leave blank for a PKCE public client): ") or None
    store = _store()
    app_ = OAuthApp(client_id, secret, redirect_uri)
    url, state, verifier = app_.begin()
    typer.echo(f"\nSign in as {host} (use a private window if another Zoom user is signed in):\n\n  {url}\n")
    if is_loopback(redirect_uri):
        thread, out = run_in_thread(lambda: wait_for_loopback(redirect_uri, timeout))
        if open_browser:
            webbrowser.open(url)
        typer.echo(f"waiting for the redirect on {redirect_uri} …")
        thread.join()
        if "error" in out:
            raise out["error"]
        callback = out["value"]
    else:
        if open_browser:
            webbrowser.open(url)
        callback = typer.prompt("After approving, paste the FULL URL your browser was redirected to")
    rec, user = complete_login(app_, host, parse_callback(callback, state), verifier, store)
    typer.echo(f"✓ authorized as {user.get('email')} — stored in {store.describe()}")
    typer.echo(f"  scopes: {rec.scope or '?'}")
    typer.echo("  next: zoomctl check -c <config repo>")


@auth_app.command("status")
def auth_status() -> None:
    """List host authorizations stored on this node (no network calls)."""
    import time as _time

    store = _store()
    hosts = store.hosts()
    typer.echo(f"token store: {store.describe()}")
    if not hosts:
        typer.echo("✗ no hosts authorized — run: zoomctl auth login --host <email>", err=True)
        raise typer.Exit(1)
    for h in hosts:
        rec = store.load(h)
        typer.echo(f"  {rec.summary(_time.time())}" if rec else f"  {h}: index entry without token (re-login)")


@auth_app.command("logout")
def auth_logout(host: Annotated[str, typer.Option(help="Host account email")]) -> None:
    """Revoke the host's authorization at Zoom and delete it from this node."""
    from .auth import logout

    if logout(host, _store()):
        typer.echo(f"✓ revoked and removed {host}")
    else:
        typer.echo(f"✗ {host} is not authorized on this node", err=True)
        raise typer.Exit(1)


def main() -> None:  # pragma: no cover
    try:
        app()
    except ZoomError as exc:
        typer.echo(f"✗ Zoom API: {exc}", err=True)
        sys.exit(1)
    except (ConfigError, LookupError) as exc:
        typer.echo(f"✗ {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    main()
