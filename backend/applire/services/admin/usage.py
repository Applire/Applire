# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-user LLM usage for the admin (B2-2, S-8; ADR-086 amended 2026-10-07).

Aggregates of ``llm_usage`` only: counts and token sums grouped by ``user_id`` and
by (``provider``, ``model``). ``llm_usage`` has no text column (ADR-086 cl. 7), so
nothing here can carry content. ``llm_usage`` and ``users`` are identity/instance
tables (``ownership.IDENTITY_TABLES``) — no owner context is needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.llm_usage import LlmUsage
from applire.models.user import User
from applire.services.admin import users as accounts

_FIELDS = ("calls", "failed_calls", "estimated_calls", "prompt_tokens",
           "completion_tokens", "reasoning_tokens", "total_tokens")


def _aggregates():
    return (
        func.count(LlmUsage.id).label("calls"),
        func.coalesce(func.sum(case((LlmUsage.ok.is_(False), 1), else_=0)), 0).label("failed_calls"),
        func.coalesce(func.sum(case((LlmUsage.estimated.is_(True), 1), else_=0)), 0).label("estimated_calls"),
        func.coalesce(func.sum(LlmUsage.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(LlmUsage.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(LlmUsage.reasoning_tokens), 0).label("reasoning_tokens"),
        func.coalesce(func.sum(LlmUsage.total_tokens), 0).label("total_tokens"),
    )


def _totals(row: Any | None) -> dict[str, int]:
    if row is None:
        return {f: 0 for f in _FIELDS}
    return {f: int(getattr(row, f) or 0) for f in _FIELDS}


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def totals_since(db: AsyncSession, since: datetime) -> dict[str, int]:
    row = (await db.execute(select(*_aggregates()).where(LlmUsage.created_at >= since))).one_or_none()
    return _totals(row)


async def usage_report(db: AsyncSession, *, days: int, now: datetime | None = None) -> dict[str, Any]:
    """The ``AdminUsageResponse`` payload for the last ``days`` days."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    window = LlmUsage.created_at >= since

    totals = await totals_since(db, since)

    per_user = {
        r.user_id: r
        for r in (
            await db.execute(
                select(
                    LlmUsage.user_id,
                    *_aggregates(),
                    func.max(LlmUsage.created_at).label("last_call_at"),
                )
                .where(window, LlmUsage.user_id.is_not(None))
                .group_by(LlmUsage.user_id)
            )
        ).all()
    }
    unattributed = (
        await db.execute(select(*_aggregates()).where(window, LlmUsage.user_id.is_(None)))
    ).one_or_none()

    users_out = []
    for user in await accounts.list_users(db):
        row = per_user.get(user.id)
        users_out.append(
            {
                "user_id": user.id,
                "email": user.email,
                "role": user.role,
                "status": accounts.user_status(user),
                "totals": _totals(row),
                "last_call_at": _aware(row.last_call_at) if row is not None else None,
            }
        )
    users_out.sort(key=lambda u: (-u["totals"]["total_tokens"], u["email"]))

    by_provider = [
        {"provider": r.provider, "model": r.model, "totals": _totals(r)}
        for r in (
            await db.execute(
                select(LlmUsage.provider, LlmUsage.model, *_aggregates())
                .where(window)
                .group_by(LlmUsage.provider, LlmUsage.model)
            )
        ).all()
    ]
    by_provider.sort(key=lambda p: (-p["totals"]["total_tokens"], p["provider"], p["model"]))

    by_kind = {"cv": _totals(None), "cover_letter": _totals(None), "other": _totals(None)}
    kind_rows = (
        await db.execute(
            select(LlmUsage.document_kind, *_aggregates()).where(window).group_by(LlmUsage.document_kind)
        )
    ).all()
    for r in kind_rows:
        bucket = r.document_kind if r.document_kind in ("cv", "cover_letter") else "other"
        for f, v in _totals(r).items():
            by_kind[bucket][f] += v

    return {
        "window_days": days,
        "since": since,
        "totals": totals,
        "users": users_out,
        "unattributed": _totals(unattributed),
        "by_provider": by_provider,
        "by_document_kind": by_kind,
    }


