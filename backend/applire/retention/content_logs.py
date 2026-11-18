# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The retention worker's two content-log rules (ADR-017 amended 2026-11-18).

Both content logs are daily JSON-lines files, ``<YYYY-MM-DD>.jsonl``, in
``CONTENT_LOG_DIR`` (application content lines) and ``LLM_DEBUG_LOG_DIR`` (the
LLM call log). Every line carries ``user_id`` (``null`` for ownerless work).

``content_log_age``
    With ``CONTENT_LOG_RETENTION_DAYS = N > 0``, the daily run removes every
    line older than ``N`` days and no line younger than that: a daily file all
    of whose lines are that old is deleted (its date comes from the file NAME:
    a copy or restore resets the mtime), the file that straddles the limit is
    rewritten without its old lines. A line is gone at the first daily run
    after it turns ``N`` days old. In both directories, every regular file that is not
    a daily file (a rotated or renamed copy, a leftover temporary file) cannot
    be read line by line, so neither rule could reach a withdrawn person's lines
    in it: it is deleted once it is a day old (by mtime), whatever ``N`` is.

``content_log_withdrawn``
    For a person whose account is gone, or who withdrew their logging consent,
    rewrite every line of theirs older than their *purge horizon*
    (:func:`applire.services.log_consent.purge_horizon`) to technical figures
    only, without the user id. Stateless and idempotent: a line written by work
    that was admitted before the withdrawal and finished after it is caught on
    the next run. A file is rewritten or deleted atomically under the directory
    lock the writers take (``content_log.directory_lock``), so no line is lost.

Neither rule is suspended by ``RETENTION_ENABLED``: the consent text promises
both to the person.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_DAILY = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")

#: What stays of an LLM call record when its person's text is removed.
LLM_KEEP = (
    "ts", "stage", "review_role", "review_attempt", "provider", "model", "method",
    "params", "latency_ms", "ok", "tokens",
)
#: What stays of an application content line (``template`` carries the check name).
APP_KEEP = ("ts", "level", "logger", "template")


def log_directories() -> list[Path]:
    from applire.config import settings

    dirs = []
    for raw in (settings.content_log_dir or "logs/content", settings.llm_debug_log_dir or "logs/llm"):
        path = Path(raw)
        if path not in dirs:
            dirs.append(path)
    return dirs


def _daily_files(directory: Path) -> Iterable[tuple[Path, date]]:
    if not directory.is_dir():
        return []
    out = []
    for child in sorted(directory.iterdir()):
        m = _DAILY.match(child.name)
        if not m or not child.is_file():
            continue
        try:
            out.append((child, date.fromisoformat(m.group(1))))
        except ValueError:
            continue
    return out


def _is_ours(child: Path) -> bool:
    from applire.content_log import DIRECTORY_LOCK

    return child.name == DIRECTORY_LOCK


def _older_than(ts: Any, horizon: datetime) -> bool:
    parsed = _parse_ts(ts)
    return parsed is None or parsed < horizon


def _drop_old_lines(path: Path, horizon: datetime) -> int:
    """Rewrite ``path`` without the lines older than ``horizon`` (or with no
    readable time); the caller holds the directory lock. Returns how many."""
    kept: list[str] = []
    dropped = 0
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
                ts = record.get("ts") if isinstance(record, dict) else None
            except ValueError:
                ts = None
            if _older_than(ts, horizon):
                dropped += 1
            else:
                kept.append(stripped)
    if dropped:
        if kept:
            _replace(path, kept)
        else:
            path.unlink()
    return dropped


def purge_by_age(days: int, *, now: datetime | None = None, directories: list[Path] | None = None) -> int:
    """Rule ``content_log_age`` (see the module docstring). Returns the number of
    files deleted plus lines removed from a file that straddles the limit."""
    from applire.content_log import directory_lock

    now = now or datetime.now(timezone.utc)
    removed = 0
    for directory in directories if directories is not None else log_directories():
        if not directory.is_dir():
            continue
        with directory_lock(str(directory)):
            removed += _purge_unknown_files(directory, now)
            if days <= 0:
                continue
            horizon = now - timedelta(days=days)
            for path, day in _daily_files(directory):
                day_start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
                try:
                    if day_start + timedelta(days=1) <= horizon:
                        path.unlink()
                        removed += 1
                    elif day_start < horizon:
                        removed += _drop_old_lines(path, horizon)
                except FileNotFoundError:
                    continue
    return removed


def _purge_unknown_files(directory: Path, now: datetime) -> int:
    """Delete regular files that are not daily files once they are a day old."""
    deleted = 0
    cutoff = (now - timedelta(days=1)).timestamp()
    for child in sorted(directory.iterdir()):
        if child.is_symlink() or not child.is_file() or _is_ours(child):
            continue
        m = _DAILY.match(child.name)
        if m:
            try:
                date.fromisoformat(m.group(1))
                continue  # a daily file: the line rules apply
            except ValueError:
                pass
        try:
            if child.stat().st_mtime < cutoff:
                child.unlink()
                deleted += 1
        except FileNotFoundError:
            continue
    return deleted


def technical_figures(record: dict[str, Any]) -> dict[str, Any]:
    """The line without its person: no user id, no text."""
    if "template" in record or "message" in record:
        kept = {k: record[k] for k in APP_KEEP if k in record}
    else:
        kept = {k: record[k] for k in LLM_KEEP if k in record}
        error = record.get("error")
        if isinstance(error, str) and error:
            kept["error_type"] = error.split(":", 1)[0][:80]
    kept["removed"] = "person"
    return kept


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def _must_scrub(record: dict[str, Any], horizons: dict[str, Any]) -> bool:
    uid = record.get("user_id")
    if not uid or uid not in horizons:
        return False
    horizon = horizons[uid]
    if horizon is None:
        return False
    if horizon == "all":
        return True
    ts = _parse_ts(record.get("ts"))
    return ts is None or ts < horizon


def user_ids_in(directories: list[Path]) -> set[str]:
    ids: set[str] = set()
    for directory in directories:
        for path, _day in _daily_files(directory):
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        try:
                            uid = json.loads(line).get("user_id")
                        except (ValueError, AttributeError):
                            continue
                        if isinstance(uid, str) and uid:
                            ids.add(uid)
            except FileNotFoundError:
                continue
    return ids


def _replace(path: Path, lines: list[str]) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def rewrite_file(path: Path, horizons: dict[str, Any]) -> int:
    """Rewrite ``path`` with every line of a purged person reduced; returns how many.

    Holds the directory lock (``content_log.directory_lock``) for the whole
    read-and-replace, the lock every writer takes before it opens the file, so
    no line is appended to the file being replaced. A line that is not JSON
    cannot be attributed and may hold text: it becomes ``{"unparsed": true}``.
    """
    from applire.content_log import directory_lock

    with directory_lock(str(path.parent)):
        changed = 0
        out_lines: list[str] = []
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                stripped = raw.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                    if not isinstance(record, dict):
                        raise ValueError("not an object")
                except ValueError:
                    out_lines.append(json.dumps({"unparsed": True}))
                    changed += 1
                    continue
                if _must_scrub(record, horizons):
                    out_lines.append(json.dumps(technical_figures(record), ensure_ascii=False))
                    changed += 1
                else:
                    out_lines.append(stripped)
        if changed:
            _replace(path, out_lines)
    return changed


async def load_horizons(db: AsyncSession, ids: set[str]) -> dict[str, Any]:
    from applire.models.user import User
    from applire.services.log_consent import purge_horizon

    parsed: dict[str, uuid.UUID] = {}
    for raw in ids:
        try:
            parsed[raw] = uuid.UUID(raw)
        except ValueError:
            continue
    rows: dict[uuid.UUID, dict] = {}
    if parsed:
        result = await db.execute(
            select(User.id, User.deleted_at, User.log_consent_at, User.log_consent_withdrawn_at).where(
                User.id.in_(list(parsed.values()))
            )
        )
        for uid, deleted_at, agreed, withdrawn in result.all():
            rows[uid] = {
                "deleted_at": deleted_at,
                "log_consent_at": agreed,
                "log_consent_withdrawn_at": withdrawn,
            }
    horizons: dict[str, Any] = {raw: purge_horizon(rows.get(uid)) for raw, uid in parsed.items()}
    # A user id that is not even a UUID cannot belong to a live account.
    for raw in ids - set(parsed):
        horizons[raw] = "all"
    return horizons


async def scrub_withdrawn(db: AsyncSession, *, directories: list[Path] | None = None) -> int:
    """Rule ``content_log_withdrawn``; returns the number of lines reduced."""
    dirs = directories if directories is not None else log_directories()
    ids = user_ids_in(dirs)
    if not ids:
        return 0
    horizons = await load_horizons(db, ids)
    if not any(h is not None for h in horizons.values()):
        return 0
    changed = 0
    for directory in dirs:
        for path, _day in _daily_files(directory):
            try:
                changed += rewrite_file(path, horizons)
            except FileNotFoundError:
                continue
    return changed


async def rule_age(_db: AsyncSession) -> int:
    from applire.services.log_consent import retention_days

    return purge_by_age(retention_days())


async def rule_withdrawn(db: AsyncSession) -> int:
    return await scrub_withdrawn(db)
