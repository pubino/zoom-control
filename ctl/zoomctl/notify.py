"""Alerting. Every lifecycle transition that isn't OK, and every final result, is reported."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import httpx


class Level(StrEnum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


ICON = {Level.INFO: "ℹ️", Level.WARN: "⚠️", Level.CRITICAL: "🚨"}


class Notifier(Protocol):
    def send(self, level: Level, title: str, detail: str = "") -> None: ...


@dataclass
class WebhookNotifier:
    """Posts Slack-compatible JSON ({"text": ...}); also mirrors to stderr and the GH step summary."""

    url: str | None
    context: str = ""
    http: httpx.Client | None = None
    failures: list[str] = field(default_factory=list)

    @classmethod
    def from_env(cls, context: str = "") -> WebhookNotifier:
        return cls(os.environ.get("ALERT_WEBHOOK_URL") or None, context)

    def send(self, level: Level, title: str, detail: str = "") -> None:
        prefix = f"[{self.context}] " if self.context else ""
        text = f"{ICON[level]} {prefix}{title}" + (f"\n{detail}" if detail else "")
        print(f"::{'error' if level is Level.CRITICAL else 'warning' if level is Level.WARN else 'notice'}::"
              f"{prefix}{title}", file=sys.stderr)
        if detail:
            print(detail, file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with Path(summary).open("a", encoding="utf-8") as fh:
                fh.write(f"- {ICON[level]} **{prefix}{title}**" + (f" — {detail}" if detail else "") + "\n")
        if not self.url:
            return
        try:
            client = self.http or httpx.Client(timeout=10.0)
            resp = client.post(self.url, content=json.dumps({"text": text}),
                               headers={"Content-Type": "application/json"})
            if resp.status_code >= 300:
                self.failures.append(f"webhook HTTP {resp.status_code}")
                print(f"::warning::alert webhook returned HTTP {resp.status_code}", file=sys.stderr)
        except httpx.HTTPError as exc:
            self.failures.append(str(exc))
            print(f"::warning::alert webhook failed: {exc}", file=sys.stderr)


@dataclass
class RecordingNotifier:
    """Test double that records every alert."""

    sent: list[tuple[Level, str, str]] = field(default_factory=list)

    def send(self, level: Level, title: str, detail: str = "") -> None:
        self.sent.append((level, title, detail))

    def titles(self, level: Level | None = None) -> list[str]:
        return [t for lv, t, _ in self.sent if level is None or lv is level]
