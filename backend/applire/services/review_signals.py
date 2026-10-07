# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#702 (ADR-060 amended 2026-10-07; ADR-090 cl. 6) — the *So lassen* / *Keep it as
is* decision on a cross-document item.

A cross-document item (``OutcomeCriticReport.cross_document``) is keyed
``critic:<_norm_quote(letter sentence)>``. Keeping it records the decision ``kept``
in the document's ``review_state`` — server-side, per generated document, readable by
every door (ADR-090 cl. 7: losing it would change what the user is shown to do next).
Nothing in the document changes; no re-audit is needed.

Only an item the document's LIVE critic report lists can be kept (a foreign or stale
key is ``FindingNotListed`` → 409), mirroring ADR-090's "only a finding this document
actually had may become a decision". Un-keeping removes the decision again.
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from applire.services import review_state as rs
from applire.services.review_actions import (
    ActionOutcome,
    FindingNotListed,
    Kind,
    load_document,
)


def cross_document_items(record) -> list:
    """The live cross-document items of a document's persisted critic report —
    derived through the schema (never read from the stored blob). Empty for a CV
    (Pass A has no letter), a NULL or malformed report."""
    from applire.schemas.outcome_critic import OutcomeCriticReport

    raw = getattr(record, "critic_report", None)
    if not isinstance(raw, dict):
        return []
    try:
        return OutcomeCriticReport.model_validate(raw).cross_document
    except Exception:
        return []


async def keep(
    kind: Kind,
    doc_id: uuid.UUID,
    key: str,
    db: AsyncSession,
    *,
    keep: bool = True,
    user_id: uuid.UUID | None = None,
) -> ActionOutcome:
    """Record (``keep=True``) or withdraw (``keep=False``) the ``kept`` decision on a
    cross-document item. ValueError (→ 422) on a key that is not a ``critic:`` key;
    FindingNotListed (→ 409) when the live report does not list the item."""
    from applire.services.owner_resolution import resolve_owner

    owner = resolve_owner(user_id, site="review_signals.keep")
    producer, norm = rs.split_key(key)
    if producer != "critic":
        raise ValueError(f"not a cross-document key: {key!r}")
    async with rs.document_lock(kind, doc_id):
        record = await load_document(kind, doc_id, db, user_id=owner)
        state = rs.load_state(record.review_state)
        if keep:
            item = next(
                (i for i in cross_document_items(record) if rs.split_key(i.key)[1] == norm),
                None,
            )
            if item is None:
                raise FindingNotListed(f"{key!r} is not listed on this document's critic report")
            state = rs.with_decision(state, item.key, item.letter_state[:300], "kept")
        else:
            state = rs.without_decision(state, key)
        record.review_state = state
        await db.commit()
        await db.refresh(record)
        return ActionOutcome(record=record)
