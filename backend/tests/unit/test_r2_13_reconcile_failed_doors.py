# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""R2-13 (Hazelnut build 4, P-1) — one seam test per NON-interview-turn caller
of ``reconcile``.

The engine no longer degrades a failed call to an empty ``ReconcileResult``; it
retries once and raises ``ReconcileFailedError``. Each door below drives the
REAL service path with a provider double that returns the founder-model reply
shape — a JSON object followed by trailing text, parsed STRICTLY (the pre-O
``json.loads``) — on every call, and asserts on what the door produced: the
"not saved" receipt in its own shape, nothing written to the vault, no 500.

Call sites (grep ``reconcile(`` under ``services/profile/reconcile/``):
``testimony_bridge`` (REST + MCP ``submit_testimony`` + review add-evidence),
``agent_bridge`` (MCP ``submit_claims``), ``import_bridge`` (import jobs, the
MCP ``import_cv`` door and the staged-merge resolve), ``interview_bridge``
(the session turn — WP-I — and the profile-enrich door, here), ``migrate``
(no production caller; ``test_migrate_flat_duplicates``).
"""
from __future__ import annotations

import json
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from applire.exceptions import ReconcileFailedError
from applire.models.profile import MasterProfile
from applire.schemas.claims import ClaimItem, ClaimsSubmission
from applire.schemas.profile import MasterProfileData
from tests.support.owners import HARNESS_USER_ID
from tests.support.profile_factory import make_master_profile

#: The P-1 reply shape: a complete, valid reconcile object, then prose. A strict
#: ``json.loads`` raises ``JSONDecodeError: Extra data`` on it (founder instance,
#: Requesty ``gpt-6-luna``, 2026-10-23, ``line 1 column 915``).
_TRAILING = (
    '{"ops": [{"op": "upsert_skill", "name": "Kafka", "category": "technical"}],'
    ' "ambiguities": [], "denials": []}\nI recorded Kafka as a technical skill.'
)


class _StrictTrailingTextProvider:
    """Every reconcile call answers ``_TRAILING`` through a strict parse."""

    def __init__(self) -> None:
        self.calls = 0

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        return json.loads(_TRAILING)

    async def acomplete(self, prompt: str, **kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("no free-text call expected on a failed reconcile")


class _QueueProvider:
    """One queued payload (or exception) per aparse_json call."""

    def __init__(self, items: list[Any]) -> None:
        self.items = list(items)
        self.calls = 0

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):
            return json.loads(item)
        return item


async def _seed(db, **extra: Any) -> MasterProfile:
    profile_json = {
        "personal_info": {"full_name": "Felix Brandt"},
        "skills": [{"name": "Python", "category": "technical"}],
        "work_experience": [
            {"company": "Finleap", "role": "Engineer", "start_date": "2021-01"}
        ],
        "metadata": {
            "completeness_score": 0.5,
            "created_via": "cv_upload",
            "created_at": "2026-01-01T00:00:00Z",
            "last_updated": "2026-01-01T00:00:00Z",
        },
    }
    profile_json.update(extra)
    record = make_master_profile(profile_json=profile_json)
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


def _skill_names(record: MasterProfile) -> list[str]:
    return [s["name"] for s in record.profile_json.get("skills", [])]


def _history(record: MasterProfile) -> list[dict]:
    return (record.profile_json.get("metadata") or {}).get("enrichment_history") or []


# ── testimony door (REST /api/profile/testimony, MCP submit_testimony) ────────


@pytest.mark.asyncio
async def test_testimony_door_reports_not_saved_and_writes_nothing(async_db):
    from applire.services.profile.reconcile.testimony_bridge import submit_testimony

    record = await _seed(async_db)
    before = json.dumps(record.profile_json, sort_keys=True)
    provider = _StrictTrailingTextProvider()

    result = await submit_testimony("I run Kafka in production.", async_db, provider)

    assert provider.calls == 2  # one call + one retry
    assert result.status == "error"
    assert result.error_code == "not_saved"
    assert "not saved" in (result.detail or "")
    assert result.changes == [] and result.not_applied == []
    await async_db.refresh(record)
    assert json.dumps(record.profile_json, sort_keys=True) == before
    assert "Kafka" not in _skill_names(record)


@pytest.mark.asyncio
async def test_testimony_router_answers_200_with_the_not_saved_receipt(async_client, async_db):
    """The REST door: no 500 — the in-band ``status: "error"`` receipt."""
    import applire.routers.profile as profile_router

    await _seed(async_db)
    from applire.main import app

    app.dependency_overrides[profile_router._get_provider] = lambda: _StrictTrailingTextProvider()
    try:
        resp = await async_client.post(
            "/api/profile/testimony", json={"text": "I run Kafka in production."}
        )
    finally:
        app.dependency_overrides.pop(profile_router._get_provider, None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_code"] == "not_saved"


# ── agent claims door (MCP submit_claims) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_claims_door_fails_only_the_failed_claim_as_not_saved(async_db):
    from applire.services.profile.reconcile.agent_bridge import submit_agent_claims

    record = await _seed(async_db)
    good = {
        "ops": [{"op": "upsert_skill", "name": "Databricks", "category": "technical"}],
        "ambiguities": [],
        "denials": [],
    }
    # Claim 1: both attempts come back with trailing text (strict parse fails);
    # claim 2: a clean reply.
    provider = _QueueProvider([_TRAILING, _TRAILING, good])
    result = await submit_agent_claims(
        ClaimsSubmission(
            claims=[
                ClaimItem(statement="I run Kafka in production."),
                ClaimItem(statement="I built pipelines on Databricks."),
            ]
        ),
        None,
        async_db,
        provider,
        user_id=HARNESS_USER_ID,
    )
    first, second = result.results
    assert first.status == "error"
    assert first.error_code == "not_saved"
    assert "not saved" in (first.detail or "")
    assert second.status == "applied"
    assert second.error_code is None
    await async_db.refresh(record)
    names = _skill_names(record)
    assert "Kafka" not in names and "Databricks" in names


# ── import door (import jobs, MCP import_cv, staged-merge resolve) ────────────


@pytest.mark.asyncio
async def test_import_reconcile_raises_instead_of_an_empty_merge():
    """``reconcile_import`` used to "merge" nothing on a failed call and report
    the whole document as not applied; now the failure propagates."""
    from applire.schemas.profile import Skill
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    existing = MasterProfileData(skills=[Skill(name="Python", category="technical")])
    incoming = MasterProfileData(skills=[Skill(name="Kafka", category="technical")])
    provider = _StrictTrailingTextProvider()
    with pytest.raises(ReconcileFailedError):
        await reconcile_import(existing, incoming, "cv_upload", provider)
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_apply_merge_leaves_the_vault_untouched_on_a_failed_reconcile(async_db):
    """The import service seam every import door funnels through: nothing is
    committed, no enrichment record, no snapshot — the profile is as it was."""
    from applire.schemas.profile import Skill
    from applire.services.profile import _apply_merge

    record = await _seed(async_db)
    before = json.dumps(record.profile_json, sort_keys=True)
    incoming = MasterProfileData(skills=[Skill(name="Kafka", category="technical")])
    with pytest.raises(ReconcileFailedError):
        await _apply_merge(
            async_db,
            incoming,
            source="cv_upload",
            emb_provider=MagicMock(),
            provider=_StrictTrailingTextProvider(),
            user_id=HARNESS_USER_ID,
        )
    await async_db.rollback()
    rows = (await async_db.execute(select(MasterProfile))).scalars().all()
    assert len(rows) == 1
    assert json.dumps(rows[0].profile_json, sort_keys=True) == before
    assert _history(rows[0]) == []


@pytest.mark.parametrize(
    "cause,code",
    [
        (json.JSONDecodeError("Extra data", "{} x", 3), "not_saved"),
        (RuntimeError("outage"), "not_saved"),
        ("timeout", "llm_timeout"),
        ("rate", "rate_limited"),
    ],
)
def test_import_job_code_for_a_failed_reconcile(cause, code):
    from applire.exceptions import LLMRateLimitError, LLMTimeoutError
    from applire.services.profile.import_jobs import classify_import_error

    if cause == "timeout":
        cause = LLMTimeoutError("slow")
    elif cause == "rate":
        cause = LLMRateLimitError("429")
    try:
        raise ReconcileFailedError("failed twice") from cause
    except ReconcileFailedError as exc:
        assert classify_import_error(exc) == code


@pytest.mark.asyncio
async def test_staged_merge_resolve_answers_503_not_saved_and_stays_held(async_client, async_db):
    """The held-import "merge" button (US167): a failed reconcile is a 503 with
    the stable ``not_saved`` code — never a 500 — and the staged item stays
    pending, so the same button can be pressed again."""
    import applire.routers.profile as profile_router
    from applire.main import app
    from applire.models.uploads import UploadRecord

    await _seed(async_db)
    rec = UploadRecord(
        user_id=HARNESS_USER_ID,
        original_filename="cv.pdf",
        content_hash="x",
        mime_type="application/pdf",
        file_path="/tmp/cv.pdf",
        byte_size=10,
        gate_status="name_divergence",
        staged_extraction={
            "personal_info": {"name": "Felix Brandt"},
            "skills": [{"name": "Kafka", "category": "technical"}],
        },
    )
    async_db.add(rec)
    await async_db.commit()
    await async_db.refresh(rec)

    app.dependency_overrides[profile_router._get_provider] = lambda: _StrictTrailingTextProvider()
    try:
        resp = await async_client.post(
            f"/api/profile/staged/{rec.id}/resolve", json={"action": "merge"}
        )
    finally:
        app.dependency_overrides.pop(profile_router._get_provider, None)
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["error"] == "not_saved"
    await async_db.rollback()
    await async_db.refresh(rec)
    assert rec.gate_status == "name_divergence"


# ── profile-enrich interview door (POST /api/profile/enrich/{id}/respond) ─────


@pytest.mark.asyncio
async def test_enrich_respond_answers_503_not_saved_and_does_not_advance(async_db, monkeypatch):
    import applire.routers.profile_enrich as pe
    from applire.models.session import InterviewSession
    from applire.schemas.enrich import EnrichRespondRequest, EnrichStartRequest

    record = await _seed(async_db)
    monkeypatch.setattr(
        pe,
        "gap_detector_mode_c",
        lambda profile_data, scope=None: ["team_size: Engineer @ Finleap", "budget: Engineer @ Finleap"],
    )

    asked: list[str] = []

    async def _fake_question(state, profile_data, provider, lang="en"):
        gap = state["critical_gaps"][state["current_gap_index"]]
        asked.append(gap)
        return {"question": f"Tell me about {gap}"}

    monkeypatch.setattr(pe, "question_generator_with_profile", _fake_question)
    user = MagicMock(id=HARNESS_USER_ID)
    provider = _StrictTrailingTextProvider()
    started = await pe.start_enrich_session(EnrichStartRequest(), async_db, provider, user)
    questions_before = len(asked)

    with pytest.raises(HTTPException) as info:
        await pe.respond_to_enrich(
            started.session_id,
            EnrichRespondRequest(answer="I lead a team of 6 since 2024."),
            async_db,
            provider,
            user,
        )
    assert info.value.status_code == 503
    assert info.value.detail["error"] == "not_saved"
    assert provider.calls == 2
    assert len(asked) == questions_before  # no follow-up question generated
    await async_db.rollback()
    session = await async_db.get(InterviewSession, started.session_id)
    assert session.state["current_gap_index"] == 0
    assert session.state.get("addressed_gaps", []) == []
    await async_db.refresh(record)
    assert _history(record) == []
