# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-090 — the review surface's group-1 actions, for both document kinds.

``/api/cv/{id}/review/*`` and ``/api/cover-letter/{id}/review/*`` (WORK-PACKAGES
Contract 2). Every write awaits the document re-audit and answers with the
refreshed ATS report response (``review_state`` on it), the truthfulness report
(or null) and ``review_state``. The logic lives in ``services/review_actions.py``.

Status mapping: unknown document → 404; malformed ``finding_key`` → 422; a key
the current report does not list, ``undo`` without a decision, or ``undo`` of an
``added`` decision, *take-out* of a stem-only finding (RULING B-1), or *take-out*
on a passage whose job title / employer name holds the wording (WP-R) → 409
(``detail.error`` names which); the removal rewrite not
installed → 503.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth.deps import require_user
from applire.models.user import User
from applire.db.session import get_db
from applire.providers import get_provider
from applire.providers.llm.base import LLMProvider
from applire.schemas.ats import ATSReportResponse
from applire.schemas.oracle import TruthfulnessReport
from applire.schemas.testimony import TestimonyResult
from applire.services import review_actions as ra

router = APIRouter(tags=["review"])

Kind = Literal["cv", "cover_letter"]


def _get_provider() -> LLMProvider:
    return get_provider()


# ── bodies & responses ───────────────────────────────────────────────────────


class FindingKeyBody(BaseModel):
    finding_key: str = Field(..., min_length=3, max_length=2000)


class KeepBody(FindingKeyBody):
    """#702 — *So lassen*: ``keep=false`` withdraws the decision."""

    keep: bool = True


class AddEvidenceBody(FindingKeyBody):
    text: str = Field(..., min_length=1, max_length=20_000)


class ReviewStateResponse(BaseModel):
    review_state: dict


class ReviewReportResponse(ReviewStateResponse):
    report: ATSReportResponse
    truthfulness: Optional[TruthfulnessReport] = None


class AddEvidenceResponse(ReviewReportResponse):
    testimony: TestimonyResult


class SectionChange(BaseModel):
    section_id: str
    before: str
    after: str


class TakeOutResponse(ReviewReportResponse):
    changes: list[SectionChange]
    still_listed: bool


# ── helpers ──────────────────────────────────────────────────────────────────


async def _reports(kind: Kind, doc_id: uuid.UUID, db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    if kind == "cv":
        from applire.services.cv import get_cv_ats_report, get_cv_truthfulness_report

        ats = await get_cv_ats_report(doc_id, db, user_id=user_id)
        truth = await get_cv_truthfulness_report(doc_id, db, user_id=user_id)
    else:
        from applire.services.cover_letter import (
            get_cover_letter_ats_report,
            get_cover_letter_truthfulness_report,
        )

        ats = await get_cover_letter_ats_report(doc_id, db, user_id=user_id)
        truth = await get_cover_letter_truthfulness_report(doc_id, db, user_id=user_id)
    return {"report": ats, "truthfulness": truth.report, "review_state": ats.review_state or {}}


async def _run(coro):
    try:
        return await coro
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except ra.FindingNotListed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "finding_not_listed", "message": str(exc)})
    except ra.NoDecision as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "no_decision", "message": str(exc)})
    except ra.UndoUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "undo_unavailable_for_added", "message": str(exc)})
    except ra.TakeOutStemOnly as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "take_out_unavailable_stem_only", "message": str(exc)})
    except ra.TakeOutProtectedName as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"error": "take_out_unavailable_protected_name", "message": str(exc)})
    except ra.RewriteUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))


# ── one set of handlers, mounted for both kinds ──────────────────────────────


def _mount(prefix: str, kind: Kind) -> None:
    async def add_evidence(
        doc_id: uuid.UUID,
        body: AddEvidenceBody,
        db: AsyncSession = Depends(get_db),
        provider: LLMProvider = Depends(_get_provider),
        _auth: User = Depends(require_user),
    ) -> AddEvidenceResponse:
        """ADR-090 cl. 4 — testimony (the `/api/profile/testimony` service), then
        the awaited document re-audit."""
        out = await _run(ra.add_evidence(kind, doc_id, body.finding_key, body.text, db, provider, user_id=_auth.id))
        return AddEvidenceResponse(testimony=out.testimony, **await _reports(kind, doc_id, db, _auth.id))

    async def take_out(
        doc_id: uuid.UUID,
        body: FindingKeyBody,
        db: AsyncSession = Depends(get_db),
        provider: LLMProvider = Depends(_get_provider),
        _auth: User = Depends(require_user),
    ) -> TakeOutResponse:
        """ADR-090 cl. 3 — model rewrite of each section holding the wording,
        saved, re-audited in this request, undoable."""
        out = await _run(ra.take_out(kind, doc_id, body.finding_key, db, provider, user_id=_auth.id))
        return TakeOutResponse(
            changes=[SectionChange(**c) for c in out.changes],
            still_listed=out.still_listed,
            **await _reports(kind, doc_id, db, _auth.id),
        )

    async def undo(
        doc_id: uuid.UUID,
        body: FindingKeyBody,
        db: AsyncSession = Depends(get_db),
        _auth: User = Depends(require_user),
    ) -> ReviewReportResponse:
        """Restore the text a *take it out* / *edited* decision replaced; re-audit."""
        await _run(ra.undo(kind, doc_id, body.finding_key, db, user_id=_auth.id))
        return ReviewReportResponse(**await _reports(kind, doc_id, db, _auth.id))

    async def edited(
        doc_id: uuid.UUID,
        body: FindingKeyBody,
        db: AsyncSession = Depends(get_db),
        _auth: User = Depends(require_user),
    ) -> ReviewReportResponse:
        """ADR-090 cl. 5 — after a section save opened from a finding: await the
        re-audit; record `edited` when the finding cleared."""
        await _run(ra.edited(kind, doc_id, body.finding_key, db, user_id=_auth.id))
        return ReviewReportResponse(**await _reports(kind, doc_id, db, _auth.id))

    async def kept(
        doc_id: uuid.UUID,
        body: KeepBody,
        db: AsyncSession = Depends(get_db),
        _auth: User = Depends(require_user),
    ) -> ReviewStateResponse:
        """#702 (ADR-060 amended 2026-10-07) — record or withdraw the `kept`
        decision on a cross-document item (`critic:` key). Nothing in the
        document changes, so there is no re-audit."""
        from applire.services import review_signals
        from applire.services.review_state import load_state

        out = await _run(
            review_signals.keep(kind, doc_id, body.finding_key, db, keep=body.keep, user_id=_auth.id)
        )
        return ReviewStateResponse(review_state=load_state(out.record.review_state))

    async def walked(
        doc_id: uuid.UUID,
        db: AsyncSession = Depends(get_db),
        _auth: User = Depends(require_user),
    ) -> ReviewStateResponse:
        """Stamp `walked_at` (replaces ADR-081 cl. 5a's browser-local bit)."""
        out = await _run(ra.walked(kind, doc_id, db, user_id=_auth.id))
        from applire.services.review_state import load_state

        return ReviewStateResponse(review_state=load_state(out.record.review_state))

    tag = "cv" if kind == "cv" else "cover-letter"
    for name, fn, model in (
        ("add-evidence", add_evidence, AddEvidenceResponse),
        ("take-out", take_out, TakeOutResponse),
        ("undo", undo, ReviewReportResponse),
        ("edited", edited, ReviewReportResponse),
        ("walked", walked, ReviewStateResponse),
        ("kept", kept, ReviewStateResponse),
    ):
        fn.__name__ = f"review_{name.replace('-', '_')}_{tag.replace('-', '_')}"
        router.add_api_route(
            f"{prefix}/{{doc_id}}/review/{name}", fn, methods=["POST"], response_model=model,
        )


_mount("/api/cv", "cv")
_mount("/api/cover-letter", "cover_letter")
