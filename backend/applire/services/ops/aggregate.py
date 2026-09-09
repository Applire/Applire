# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The aggregator: one verdict, one cache, one log line (ADR-086 clauses 2, 3, 9).

``collect`` runs every probe in ``probes.PROBE_NAMES``, folds them into a single
verdict, and remembers the result. Two consumers read the cache rather than
recomputing:

* ``GET /health`` — its ``ops`` summary field. ``/health`` is the container
  healthcheck's endpoint, called every 30 s (``docker-compose.yml:91``); making
  it depend on Postgres would turn a database blip into a restart loop, so it
  never computes anything (ADR-086 clause 3). Cold cache = ``null``.
* the background refresher — which is what makes the WARNING a *push* rather
  than something only a page load can trigger. ``Personas/Operator.md`` Step 5:
  *"the operator is not watching"*.

**``unknown`` never counts as ``ok``.** A probe that quietly stops working must
not be able to make the instance look healthy — that is ``SF-OPS.4``, and it is
the one way this layer could be worse than no layer at all.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from applire.services.ops import probes as probe_module
from applire.services.ops.config import OPS_REFRESH_SECONDS
from applire.services.ops.probes import (
    DEGRADED,
    DOWN,
    OK,
    UNKNOWN,
    ProbeResult,
    run_probe,
)

logger = logging.getLogger(__name__)

# `unknown` sits at the same rank as `degraded`: not healthy, not proven dead.
_RANK = {OK: 0, DEGRADED: 1, UNKNOWN: 1, DOWN: 2}
_VERDICT_BY_RANK = {0: OK, 1: DEGRADED, 2: DOWN}

# (verdict, since_iso, full_report_dict)
_state: tuple[str, str, dict[str, Any]] | None = None
_refresh_task: asyncio.Task[None] | None = None


def verdict_of(results: list[ProbeResult]) -> str:
    if not results:
        return UNKNOWN
    return _VERDICT_BY_RANK[max(_RANK.get(r.status, 1) for r in results)]


async def collect(db: AsyncSession, *, with_usage: bool = True) -> dict[str, Any]:
    """Run every probe and return the full ops report.

    The database probe runs first and gates the other three DB probes: once the
    session is broken, running them would only produce three copies of the same
    news, and a broken session cannot be reused anyway.
    """
    from applire._version import __version__
    from applire.config import HAS_CLOUD, settings

    results: list[ProbeResult] = []

    database = await run_probe("database", probe_module.probe_database, db)
    results.append(database)
    for name, fn in probe_module.DB_PROBES.items():
        if name == "database":
            continue
        if database.status == DOWN:
            results.append(
                ProbeResult(name, UNKNOWN, "not checked — database unreachable")
            )
            continue
        results.append(await run_probe(name, fn, db))
    for name, fn in probe_module.PLAIN_PROBES.items():
        results.append(await run_probe(name, fn))

    verdict = verdict_of(results)
    usage: dict[str, Any] = {}
    if with_usage and database.status != DOWN:
        usage = await _usage_or_empty(db)

    report: dict[str, Any] = {
        "status": verdict,
        "edition": "cloud" if HAS_CLOUD else "community",
        "version": __version__,
        # The provider FAMILY, never the model id (ADR-086 clause 4).
        "llm_provider": settings.llm_provider,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "components": {
            r.name: {
                "status": r.status,
                "message": r.message,
                "detail": r.detail,
            }
            for r in results
        },
        "usage": usage,
    }
    _remember(verdict, report)
    return report


async def _usage_or_empty(db: AsyncSession) -> dict[str, Any]:
    from applire.services.ops.usage_report import usage_summary

    try:
        return await usage_summary(db)
    except Exception as exc:
        # An un-migrated instance has no llm_usage table yet. Reporting an
        # empty cost picture is right; 500ing the health endpoint is not.
        logger.debug("usage summary unavailable: %s: %s", type(exc).__name__, exc)
        try:
            await db.rollback()
        except Exception:  # pragma: no cover
            pass
        return {}


def _remember(verdict: str, report: dict[str, Any]) -> None:
    """Update the cache and log the transition — ADR-086 clause 9."""
    global _state
    previous = _state[0] if _state else None
    since = _state[1] if _state and previous == verdict else _now_iso()
    if previous is not None and previous != verdict:
        moved = ", ".join(
            f"{name}: {body['status']}"
            for name, body in report["components"].items()
            if body["status"] != OK
        )
        message = "instance health %s -> %s (%s)"
        args = (previous, verdict, moved or "no component reporting a problem")
        if verdict == OK:
            logger.info(message, *args)
        else:
            logger.warning(message, *args)
    _state = (verdict, since, report)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def cached_summary() -> dict[str, Any] | None:
    """What ``GET /health`` puts in its ``ops`` field. Never computes."""
    if _state is None:
        return None
    return {"status": _state[0], "since": _state[1]}


def cached_report() -> dict[str, Any] | None:
    """The last full report, for a caller that must not touch the database."""
    return _state[2] if _state else None


def reset_state() -> None:
    """Test hook."""
    global _state
    _state = None


# ── background refresher ──────────────────────────────────────────────────────


async def _refresh_loop() -> None:
    from applire.db.session import AsyncSessionLocal

    while True:
        try:
            async with AsyncSessionLocal() as db:
                await collect(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("ops refresh failed: %s: %s", type(exc).__name__, exc)
        await asyncio.sleep(max(OPS_REFRESH_SECONDS, 5))


def start_ops_refresh() -> None:
    """Start the periodic refresh. Idempotent; safe to call from a lifespan."""
    global _refresh_task
    if _refresh_task is not None and not _refresh_task.done():
        return
    _refresh_task = asyncio.create_task(_refresh_loop())


async def stop_ops_refresh() -> None:
    """Cancel the refresher and wait for it. Never raises."""
    global _refresh_task
    task, _refresh_task = _refresh_task, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # pragma: no cover
        pass
