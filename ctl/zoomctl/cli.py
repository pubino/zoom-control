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
from .zoom import Credentials, ZoomClient, ZoomError

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
node_app = typer.Typer(no_args_is_help=True, help="Commands that run on a room node.")
app.add_typer(node_app, name="node")

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


def _zoom() -> ZoomClient:
    try:
        return ZoomClient(Credentials.from_env())
    except ZoomError as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(1) from None


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
    changes = plan(cfg, _zoom(), now=_now(now), prune=prune)
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
    zoom = _zoom()
    changes = plan(cfg, zoom, now=_now(now), prune=prune)
    errors = apply(changes, zoom)
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
    zoom = _zoom()
    ev = cfg.event(event)
    wid = find_webinar_id(cfg, zoom, ev)
    zoom.end_webinar(wid)
    typer.echo(f"✓ ended webinar {wid} ({ev.uid})")


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

    zoom = _zoom()
    webinar: dict[str, int] = {}

    def wid() -> int:
        if "id" not in webinar:
            webinar["id"] = find_webinar_id(cfg, zoom, ev)
        return webinar["id"]

    lc = Lifecycle(ev, room, RoomAgentCLI(binary), ZoomOps(lambda: zoom.fresh_start_url(wid()),
                                                            lambda: zoom.end_webinar(wid())),
                   notifier, keep_awake=Caffeinate(), immediate=immediate)
    result = lc.run()
    typer.echo(json.dumps({"outcome": result.outcome.value, "recoveries": result.recoveries,
                           "degraded": result.degraded_reasons, "error": result.error}, indent=2))
    if result.outcome is Outcome.FAILED:
        raise typer.Exit(2)


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
