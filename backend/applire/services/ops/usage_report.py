# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Token-cost aggregations over ``llm_usage`` (US313, ADR-086 clause 7).

Three questions the operator asks, answered by SQL over the rows the provider
seam writes: *what did today cost*, *what did the last seven days cost*, and
*which documents and applications did the spending*.

Every aggregate says whether it contains **estimated** rows. An estimate that
does not say it is an estimate is worse than no number (``SF-OPS.8``), and an
operator reconciling a provider invoice needs to know which half of the figure
was measured.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

# How many rows the per-document / per-application breakdowns return. The panel
# shows the recent spenders, not a ledger; the table itself is the ledger.
_TOP_N = 10


async def usage_summary(db: AsyncSession, *, days: int = 7) -> dict[str, Any]:
    """Token totals for today and the trailing window, plus the top spenders."""
    from applire.models.llm_usage import LlmUsage

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_start = now - timedelta(days=days)

    today = await _totals(db, LlmUsage.created_at >= today_start)
    window = await _totals(db, LlmUsage.created_at >= window_start)

    by_document = await _grouped(
        db, LlmUsage.document_id, window_start, extra=LlmUsage.document_kind
    )
    by_application = await _grouped(db, LlmUsage.application_id, window_start)

    return {
        "today": today,
        "window_days": days,
        "window": window,
        "by_document": by_document,
        "by_application": by_application,
    }


async def _totals(db: AsyncSession, *where: Any) -> dict[str, Any]:
    from applire.models.llm_usage import LlmUsage

    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(LlmUsage.prompt_tokens), 0),
                func.coalesce(func.sum(LlmUsage.completion_tokens), 0),
                # The STORED total, not prompt+completion recomputed here: the
                # per-document breakdown below sums the same column, and two
                # aggregations of one fact that disagree is a defect waiting to
                # be reported as a bug against the panel.
                func.coalesce(func.sum(LlmUsage.total_tokens), 0),
                func.count(LlmUsage.id),
                # Portable "how many of them were estimates" — a CAST, not a
                # dialect-specific FILTER clause: these queries run on SQLite in
                # the unit tests and on PostgreSQL in production (ADR-002's spirit).
                func.coalesce(func.sum(cast(LlmUsage.estimated, Integer)), 0),
            ).where(*where)
        )
    ).one()
    prompt, completion, total, calls, estimated = row
    return {
        "prompt_tokens": int(prompt),
        "completion_tokens": int(completion),
        "total_tokens": int(total),
        "calls": int(calls),
        "estimated_calls": int(estimated),
        # The one fact an operator needs before trusting the number.
        "fully_measured": int(estimated) == 0,
    }


async def _grouped(
    db: AsyncSession, column: Any, since: datetime, extra: Any = None
) -> list[dict[str, Any]]:
    from applire.models.llm_usage import LlmUsage

    selected = [column]
    if extra is not None:
        selected.append(func.min(extra))
    selected += [
        func.coalesce(func.sum(LlmUsage.total_tokens), 0),
        func.count(LlmUsage.id),
    ]
    stmt = (
        select(*selected)
        .where(LlmUsage.created_at >= since, column.is_not(None))
        .group_by(column)
        .order_by(func.coalesce(func.sum(LlmUsage.total_tokens), 0).desc())
        .limit(_TOP_N)
    )
    out: list[dict[str, Any]] = []
    for row in (await db.execute(stmt)).all():
        values = list(row)
        identifier = values.pop(0)
        kind = values.pop(0) if extra is not None else None
        total, calls = values
        entry: dict[str, Any] = {
            "id": str(identifier),
            "total_tokens": int(total),
            "calls": int(calls),
        }
        if extra is not None:
            entry["kind"] = kind or ""
        out.append(entry)
    return out


async def purge_old_usage(db: AsyncSession, retention_days: int) -> int:
    """Delete usage rows past their retention window (ADR-086 clause 10).

    Called by the retention worker. Not an ADR-005 PII clock — these rows carry
    no personal data — a growth bound on the table the disk probe watches.
    """
    if retention_days <= 0:
        return 0
    from sqlalchemy import delete

    from applire.models.llm_usage import LlmUsage

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = await db.execute(delete(LlmUsage).where(LlmUsage.created_at < cutoff))
    await db.commit()
    return int(result.rowcount or 0)
