# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-090 — the review surface's group-1 actions, for both document kinds.

``/api/cv/{id}/review/*`` and ``/api/cover-letter/{id}/review/*`` (WORK-PACKAGES
Contract 2). Every write awaits the document re-audit and answers with the
refreshed ATS report response (``review_state`` on it), the truthfulness report
(or null) and ``review_state``. The logic lives in ``services/review_actions.py``.

Status mapping: unknown document → 404; malformed ``finding_key`` → 422; a key
the current report does not list, ``undo`` without a decision, or ``undo`` of an
``added`` decision → 409 (``detail.error`` names which); the removal rewrite not
installed → 503.
"""
from __future__ import annotations

import uuid
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from applire.auth import get_auth_provider
from applire.auth.base import AuthProvider
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


async def _reports(kind: Kind, doc_id: uuid.UUID, db: AsyncSession) -> dict[str, Any]:
    if kind == "cv":
        from applire.services.cv import get_cv_ats_report, get_cv_truthfulness_report

        ats = await get_cv_ats_report(doc_id, db)
        truth = await get_cv_truthfulness_report(doc_id, db)
    else:
        from applire.services.cover_letter import (
            get_cover_letter_ats_report,
            get_cover_letter_truthfulness_report,
        )

        ats = await get_cover_letter_ats_report(doc_id, db)
        truth = await get_cover_letter_truthfulness_report(doc_id, db)
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
        _auth: AuthProvider = Depends(get_auth_provider),
    ) -> AddEvidenceResponse:
        """ADR-090 cl. 4 — testimony (the `/api/profile/testimony` service), then
        the awaited document re-audit."""
        out = await _run(ra.add_evidence(kind, doc_id, body.finding_key, body.text, db, provider))
        return AddEvidenceResponse(testimony=out.testimony, **await _reports(kind, doc_id, db))

    async def take_out(
        doc_id: uuid.UUID,
        body: FindingKeyBody,
        db: AsyncSession = Depends(get_db),
        provider: LLMProvider = Depends(_get_provider),
        _auth: AuthProvider = Depends(get_auth_provider),
    ) -> TakeOutResponse:
        """ADR-090 cl. 3 — model rewrite of each section holding the wording,
        saved, re-audited in this request, undoable."""
        out = await _run(ra.take_out(kind, doc_id, body.finding_key, db, provider))
        return TakeOutResponse(
            changes=[SectionChange(**c) for c in out.changes],
            still_listed=out.still_listed,
            **await _reports(kind, doc_id, db),
        )

    async def undo(
        doc_id: uuid.UUID,
        body: FindingKeyBody,
        db: AsyncSession = Depends(get_db),
        _auth: AuthProvider = Depends(get_auth_provider),
    ) -> ReviewReportResponse:
        """Restore the text a *take it out* / *edited* decision replaced; re-audit."""
        await _run(ra.undo(kind, doc_id, body.finding_key, db))
        return ReviewReportResponse(**await _reports(kind, doc_id, db))

    async def edited(
        doc_id: uuid.UUID,
        body: FindingKeyBody,
        db: AsyncSession = Depends(get_db),
        _auth: AuthProvider = Depends(get_auth_provider),
    ) -> ReviewReportResponse:
        """ADR-090 cl. 5 — after a section save opened from a finding: await the
        re-audit; record `edited` when the finding cleared."""
        await _run(ra.edited(kind, doc_id, body.finding_key, db))
        return ReviewReportResponse(**await _reports(kind, doc_id, db))

    async def walked(
        doc_id: uuid.UUID,
        db: AsyncSession = Depends(get_db),
        _auth: AuthProvider = Depends(get_auth_provider),
    ) -> ReviewStateResponse:
        """Stamp `walked_at` (replaces ADR-081 cl. 5a's browser-local bit)."""
        out = await _run(ra.walked(kind, doc_id, db))
        from applire.services.review_state import load_state

        return ReviewStateResponse(review_state=load_state(out.record.review_state))

    tag = "cv" if kind == "cv" else "cover-letter"
    for name, fn, model in (
        ("add-evidence", add_evidence, AddEvidenceResponse),
        ("take-out", take_out, TakeOutResponse),
        ("undo", undo, ReviewReportResponse),
        ("edited", edited, ReviewReportResponse),
        ("walked", walked, ReviewStateResponse),
    ):
        fn.__name__ = f"review_{name.replace('-', '_')}_{tag.replace('-', '_')}"
        router.add_api_route(
            f"{prefix}/{{doc_id}}/review/{name}", fn, methods=["POST"], response_model=model,
        )


_mount("/api/cv", "cv")
_mount("/api/cover-letter", "cover_letter")
