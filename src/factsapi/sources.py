"""Reading materialized instrument artifacts.

The service never touches an instrument's operational database. It reads the
same generated JSON files the instruments already publish, so a failure here
cannot impair Labelwatch, Weatherwatch or Driftwatch, and the API can only ever
expose facts the instruments already made public.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone

# Instrument artifacts. Overridable for tests and for non-default deployments.
LABELWATCH_OVERVIEW = os.environ.get(
    "FACTSAPI_LABELWATCH_OVERVIEW", "/var/www/labelwatch/overview.json"
)
WEATHERWATCH_SUMMARY = os.environ.get(
    "FACTSAPI_WEATHERWATCH_SUMMARY", "/var/www/weatherwatch/summary.json"
)


class SourceUnavailable(Exception):
    """The artifact is missing or unreadable.

    Raised rather than substituting a default: an absent artifact is absence of
    evidence, and the API must say so instead of publishing a confident empty
    reading.
    """


@dataclass(frozen=True)
class Artifact:
    path: str
    data: dict
    mtime: float
    etag: str

    @property
    def last_modified(self) -> datetime:
        return datetime.fromtimestamp(self.mtime, tz=timezone.utc)


def load(path: str) -> Artifact:
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        stat = os.stat(path)
    except OSError as exc:
        raise SourceUnavailable(f"artifact unavailable: {os.path.basename(path)}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceUnavailable(f"artifact unreadable: {os.path.basename(path)}") from exc
    if not isinstance(data, dict):
        raise SourceUnavailable(f"artifact malformed: {os.path.basename(path)}")
    etag = '"%s"' % hashlib.sha256(raw).hexdigest()[:32]
    return Artifact(path=path, data=data, mtime=stat.st_mtime, etag=etag)


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
