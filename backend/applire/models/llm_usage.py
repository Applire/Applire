# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-086 clause 7 / US313 — what a document, an application and a day cost.

One row per provider call. Written by ``providers/llm/usage.py``'s recorder,
which sits at the provider seam as the debug log's neighbour.

**The PII boundary is structural, not a policy** (E060 task 1.3, ``SF-OPS.9``):
this table has **no text column at all**. It carries token counts, durations,
a provider/model/stage label and opaque identifiers — never a prompt, never a
completion, never an error message that could contain either. The full-content
artefact is ``providers/llm/debug_log.py``, which is off by default and carries
CV PII; the two never merge. ``tests/unit/test_llm_usage_seam.py`` pins this
table's column set against an explicit allowlist so adding a text column fails a
named test rather than shipping.

**No foreign keys, deliberately.** ``document_id`` and ``application_id`` are
plain UUID columns. An FK would make the retention worker's hard-DELETE of
``generated_cvs`` / ``generated_cover_letters`` abort on PostgreSQL — the exact
failure that was found in the 2026-07-13 real-LLM PQ and is why
``_purge_cancelled_documents`` releases the flow session's document references
before deleting. A cost record must never be the reason a GDPR sweep fails, and
a dangling id in a counters table harms nobody: the aggregation groups by it and
does not need to resolve it.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    # Provider FAMILY, not the class name — 'openrouter', 'mistral', 'ollama'.
    # The same value ADR-085 clause 3 permits the PDF mark to carry.
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # The configured model id. Present here (an operator-side counters table
    # behind their own instance) but deliberately NOT on the ops endpoint's
    # payload — ADR-086 clause 4 keeps the exact model id off the
    # unauthenticated surface.
    model: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    # Free-text pipeline label from `llm_usage_context(...)`, falling back to
    # debug_log's `_stage` contextvar. Empty when neither is set — see
    # SF-OPS.7: a visibly unattributed row, never a wrongly attributed one.
    stage: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    method: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # True when the provider reported no usage and the counts are a
    # character-length estimate. An estimate that does not say it is an
    # estimate is worse than no number (SF-OPS.8).
    estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 'cv' | 'cover_letter' | '' — which kind of document the call belonged to.
    document_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    document_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    application_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # False when the call raised. A failed call still cost input tokens on most
    # providers, and a burst of failures is exactly what a runaway loop looks
    # like — so the row is written either way.
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
