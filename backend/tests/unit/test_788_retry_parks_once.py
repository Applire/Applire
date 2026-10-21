# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""788:L29 — a RETRY of the same turn parks its confirmation / conflict ONCE.

`commit_ops` used to `extend()` `metadata.pending_confirmations` /
`pending_conflicts` unconditionally, so an agent re-sending `submit_claims` /
`submit_testimony` / `resolve_gap`, or an interview answer re-posted after a
timeout, parked the same open question twice (the import doors already deduped
through `_surviving_parked_items`). The fix keys an incoming item with
`_confirmation_key` / `_conflict_key` and skips it when an UNRESOLVED parked
item carries the same key.

One retry test per door, each driving the REAL door function twice with the
identical input and a provider double that returns the same reconcile payload
both times, then reading the PERSISTED profile row:

  1. `submit_agent_claims`        (MCP `submit_claims`)
  2. `submit_testimony`           (MCP `submit_testimony` + the UI paste box)
  3. `resolve_gap`'s service chain (`create_session` -> `send_message`; the MCP
     tool's own identical-retry refusal, ADR-089 cl. 7, sits ABOVE this chain
     and is not what is under test)
  4. `reconcile_interview_turn`   (the interview)

Plus the two boundaries: a RESOLVED parked item with the same key does not
block a fresh ask, and a conflict re-raised on retry parks once.

Run:
    LLM_PROVIDER=mistral DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend \\
      python3 -m pytest backend/tests/unit/test_788_retry_parks_once.py -q -p no:cacheprovider
"""
from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from applire.models.gap import GapAnalysis
from applire.models.job import JobAnalysis
from applire.models.profile import MasterProfile
from applire.schemas.claims import ClaimItem, ClaimsSubmission
from applire.schemas.profile import Conflict, MasterProfileData, PendingConfirmation
from applire.schemas.session import SessionCreateRequest
from applire.services.profile.reconcile.agent_bridge import submit_agent_claims
from applire.services.profile.reconcile.interview_bridge import (
    reconcile_interview_turn,
)
from applire.services.profile.reconcile.testimony_bridge import submit_testimony
from tests.support.owners import HARNESS_USER_ID
from tests.support.posting_links import link_posting
from tests.support.profile_factory import make_master_profile



@pytest.fixture(autouse=True)
def _act_as_the_harness_user():
    """Every call here acts for the harness user, as a request does for its user
    (ADR-092 cl. 8) — explicit, so a context another module left set cannot
    leak in when the files run in one process."""
    from applire.ownership import owner_context

    with owner_context(HARNESS_USER_ID):
        yield


_QUESTION = "Is 'K8s admin' the same as your existing 'Kubernetes' skill?"
_OPTIONS = ["merge", "add_separate"]

_ASK_PAYLOAD = {
    "ops": [],
    "ambiguities": [
        {
            "op": "request_confirmation",
            "question": _QUESTION,
            "options": _OPTIONS,
            "context": {"section": "skills"},
        }
    ],
    "denials": [],
}


class _SameEveryTime:
    """A provider double that answers EVERY call with the same reconcile payload
    (a retry reaches the model again and the model says the same thing)."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        return self.payload


def _flag_conflict_payload(work_id: str) -> dict:
    return {
        "ops": [
            {
                "op": "flag_conflict",
                "target": work_id,
                "field": "company",
                "existing": "Acme GmbH",
                "incoming": "Acme Holding AG",
            }
        ],
        "ambiguities": [],
        "denials": [],
    }


def _profile_json(*, confirmations=(), conflicts=()) -> dict:
    data = MasterProfileData.model_validate(
        {
            "personal_info": {"full_name": "Anna Bauer"},
            "work_experience": [
                {"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}
            ],
            "metadata": {
                "completeness_score": 10.0,
                "created_via": "cv_upload",
                "created_at": "2026-01-01T00:00:00Z",
                "last_updated": "2026-01-01T00:00:00Z",
                "pending_confirmations": [
                    c.model_dump(mode="json") for c in confirmations
                ],
                "pending_conflicts": [c.model_dump(mode="json") for c in conflicts],
            },
        }
    )
    return data.model_dump(mode="json")


async def _seed_profile(db, **kwargs) -> MasterProfile:
    record = make_master_profile(profile_json=_profile_json(**kwargs))
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


def _work_id(record: MasterProfile) -> str:
    return record.profile_json["work_experience"][0]["id"]


async def _parked(db, record: MasterProfile) -> tuple[list[dict], list[dict]]:
    await db.refresh(record)
    meta = record.profile_json["metadata"]
    return meta["pending_confirmations"], meta["pending_conflicts"]


async def _submit_claims(db, provider) -> None:
    await submit_agent_claims(
        ClaimsSubmission(claims=[ClaimItem(statement="I do K8s administration.")]),
        None,
        db,
        provider,
    )


async def _submit_testimony(db, provider) -> None:
    await submit_testimony("I do K8s administration.", db, provider)


async def _interview_turn(db, record: MasterProfile, provider) -> None:
    await reconcile_interview_turn(
        db,
        profile_record=record,
        gap="skills",
        question="What do you use?",
        answer="I do K8s administration.",
        provider=provider,
        session_id="s-788",
    )
    # The bridge flushes; the DOOR commits (ADR-063 amended clause 6).
    await db.commit()


# ── resolve_gap's service chain ────────────────────────────────────────────────

_CLUSTER = "cluster-infra"


async def _seed_gap(db, record: MasterProfile) -> uuid.UUID:
    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex,
        raw_text="Platform Engineer: Kubernetes.",
        role_title="Platform Engineer",
        required_skills=["Kubernetes"],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="Senior",
        company_culture_signals=[],
        language_requirement="English",
        jd_language="en",
    )
    db.add(job)
    await db.flush()
    await link_posting(db, job, HARNESS_USER_ID)
    row = lambda c: {  # noqa: E731
        "concept": c, "surface_forms": [c], "sources": ["required"],
        "fit_weight": 1.0, "status": "gap", "evidence": "", "claimable": False,
    }
    db.add(
        GapAnalysis(
            job_analysis_id=job.id,
            profile_id=record.id,
            match_score=0.4,
            critical_gaps=[], minor_gaps=[], strengths=[], keyword_gaps=[],
            category_a=[], category_b=[], category_c=["Kubernetes", "Terraform"],
            keyword_ledger=[row("Kubernetes"), row("Terraform")],
            gap_clusters=[
                {
                    "id": _CLUSTER, "label": "Cloud infrastructure", "category": "C",
                    "gaps": ["Kubernetes", "Terraform"],
                    "jd_skills": ["Kubernetes", "Terraform"], "jd_context": "",
                    "outcome": {"asked": 0, "covered": [], "declined": [], "session_ids": []},
                    "coverage": "open",
                }
            ],
        )
    )
    await db.commit()
    return job.id


async def _end_open_sessions(db) -> None:
    from applire.models.session import InterviewSession

    for row in (await db.execute(select(InterviewSession))).scalars().all():
        row.status = "complete"
    await db.commit()


async def _resolve_gap_service(db, job_id: uuid.UUID, provider) -> None:
    """The composition the MCP `resolve_gap` tool runs after its own guards:
    `create_session(mode="targeted", target_gap=…)` then `send_message(answer)`.
    Only the question writer and the gap re-analysis (LLM-bound, unrelated to
    parking) are doubled; the reconcile -> `commit_ops` path is the real one."""
    from applire.services import session as session_svc

    async def _writer(state, profile, provider, **kwargs):
        return {"question": "Which Kubernetes work did you do?", "choices": None}

    with patch(
        "applire.services.session.question_generator_with_profile",
        new=AsyncMock(side_effect=_writer),
    ), patch("applire.services.session.analyze_gaps", new=AsyncMock()):
        created = await session_svc.create_session(
            SessionCreateRequest(job_id=job_id, mode="targeted", target_gap=_CLUSTER),
            db,
            provider,
            user_id=HARNESS_USER_ID,
        )
        await session_svc.send_message(
            created.session_id,
            "I do K8s administration.",
            db,
            provider,
            user_id=HARNESS_USER_ID,
        )


# ── 1-4. One retry test per door ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_claims_retry_parks_the_confirmation_once(async_db):
    record = await _seed_profile(async_db)
    provider = _SameEveryTime(_ASK_PAYLOAD)

    await _submit_claims(async_db, provider)
    await _submit_claims(async_db, provider)  # the retry

    confirmations, _ = await _parked(async_db, record)
    assert provider.calls == 2, "the retry must actually reach the reconciler"
    assert len(confirmations) == 1
    assert confirmations[0]["question"] == _QUESTION
    assert confirmations[0]["resolved"] is False


@pytest.mark.asyncio
async def test_submit_testimony_retry_parks_the_confirmation_once(async_db):
    record = await _seed_profile(async_db)
    provider = _SameEveryTime(_ASK_PAYLOAD)

    await _submit_testimony(async_db, provider)
    await _submit_testimony(async_db, provider)  # the retry

    confirmations, _ = await _parked(async_db, record)
    assert provider.calls == 2
    assert len(confirmations) == 1
    assert confirmations[0]["question"] == _QUESTION


@pytest.mark.asyncio
async def test_resolve_gap_retry_parks_the_confirmation_once(async_db):
    record = await _seed_profile(async_db)
    job_id = await _seed_gap(async_db, record)
    provider = _SameEveryTime(_ASK_PAYLOAD)

    await _resolve_gap_service(async_db, job_id, provider)
    # While the first call's micro-session is still open, a re-sent answer
    # lands on ITS waiting confirmation (#187) and never reaches the
    # reconciler. The retry that does is the one that finds the session gone —
    # reaped by a later "start an interview" (#627) or a lost connection — so
    # it opens a fresh micro-session and reconciles the same testimony again.
    await _end_open_sessions(async_db)
    await _resolve_gap_service(async_db, job_id, provider)  # the retry

    confirmations, _ = await _parked(async_db, record)
    assert provider.calls == 2, "the retry must actually reach the reconciler"
    assert len(confirmations) == 1
    assert confirmations[0]["question"] == _QUESTION


@pytest.mark.asyncio
async def test_interview_turn_retry_parks_the_confirmation_once(async_db):
    record = await _seed_profile(async_db)
    provider = _SameEveryTime(_ASK_PAYLOAD)

    await _interview_turn(async_db, record, provider)
    await _interview_turn(async_db, record, provider)  # the answer re-posted

    confirmations, _ = await _parked(async_db, record)
    assert provider.calls == 2
    assert len(confirmations) == 1
    assert confirmations[0]["question"] == _QUESTION


# ── 5. A resolved parked ask does not block a fresh one ────────────────────────


@pytest.mark.asyncio
async def test_a_resolved_parked_confirmation_does_not_block_a_fresh_park(async_db):
    answered = PendingConfirmation(
        question=_QUESTION,
        options=list(_OPTIONS),
        source="testimony",
        resolved=True,
        chosen_option="merge",
    )
    record = await _seed_profile(async_db, confirmations=[answered])
    provider = _SameEveryTime(_ASK_PAYLOAD)

    await _submit_testimony(async_db, provider)

    confirmations, _ = await _parked(async_db, record)
    assert len(confirmations) == 2
    assert sorted(c["resolved"] for c in confirmations) == [False, True]
    assert {c["question"] for c in confirmations} == {_QUESTION}
    # ...and the fresh one is now an open ask: a retry parks nothing more.
    await _submit_testimony(async_db, provider)
    confirmations, _ = await _parked(async_db, record)
    assert len(confirmations) == 2


# ── 6. A conflict re-raised on retry parks once ────────────────────────────────


@pytest.mark.asyncio
async def test_a_conflict_re_raised_on_retry_parks_once(async_db):
    record = await _seed_profile(async_db)
    provider = _SameEveryTime(_flag_conflict_payload(_work_id(record)))

    await _submit_testimony(async_db, provider)
    _, conflicts = await _parked(async_db, record)
    assert len(conflicts) == 1, "the first turn parks the dispute"
    assert conflicts[0]["field"] == "company"

    await _submit_testimony(async_db, provider)  # the retry

    _, conflicts = await _parked(async_db, record)
    assert provider.calls == 2
    assert len(conflicts) == 1
