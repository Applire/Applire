# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""R2-6 (4) (Hazelnut build 4) — the turn receipt decides by ``changes`` first.

UX re-audit 2, felix-051: after Felix's verbatim crypto "no", the interview said
"Nothing was recorded from what you said — say it again". The vault entry for
that turn had ``changes`` = 3× ``denied_concepts`` AND ``not_applied`` = one
sentence (``no_write``) — the receipt contradicted itself, and the UI rendered
the ``not_applied`` half.

These tests drive that REAL persisted shape: the captured reconcile reply of the
round-2 run (``denials`` = blockchain / crypto / on-chain settlement rails,
``empty_reason`` = nothing_actionable) and Felix's verbatim answer, through the
real bridge and the real committer (only the provider is doubled), then through
the real turn handler. The control: a turn that records nothing keeps its
receipt.

Run:
    LLM_PROVIDER=mistral DATABASE_URL=sqlite+aiosqlite:///:memory: PYTHONPATH=backend \\
      python3 -m pytest tests/unit/test_r2_6_turn_receipt_by_changes.py -q
"""

import copy
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile, retire_live_profiles  # noqa: E402

#: Felix's verbatim answer (round-2 LLM log, 2026-10-23 22:25:34, stage reconcile).
_CRYPTO_NO = (
    "No — I have no blockchain or crypto experience at all. That's an honest gap; "
    "I haven't worked with settlement rails on chain and I don't want to claim otherwise."
)
#: The model's reply to that turn, as captured.
_CAPTURED_REPLY = {
    "ops": [], "ambiguities": [],
    "denials": ["blockchain", "crypto", "on-chain settlement rails"],
    "empty_reason": "nothing_actionable",
}
_POSITIVE = "I led the migration of our billing platform to Kubernetes at Acme in 2022."


class _Reply:
    def __init__(self, payload):
        self._payload = payload

    async def aparse_json(self, prompt, **kw):
        return copy.deepcopy(self._payload)

    async def acomplete(self, prompt, **kw):
        return "unused"


@pytest_asyncio.fixture
async def db():
    import applire.main  # noqa: F401 — every model on Base.metadata
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db):
    from applire.models.job import JobAnalysis
    from applire.models.session import InterviewSession

    await retire_live_profiles(db)
    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex, raw_text="Backend Engineer (Payments).",
        role_title="Backend Engineer", required_skills=["Python"],
        nice_to_have_skills=["Crypto settlement"], keywords=[], seniority_level="Senior",
        company_culture_signals=[], language_requirement="English", jd_language="en",
    )
    profile = make_master_profile(profile_json={
        "personal_info": {"name": "Felix Test", "email": "felix@example.com"},
        "skills": [{"name": "Python", "category": "technical"}],
    })
    db.add_all([job, profile])
    await db.flush()
    gaps = ["Crypto Settlement", "Observability"]
    state = {
        "mode": "targeted", "job_id": str(job.id), "gap_analysis_id": None,
        "profile_id": str(profile.id), "critical_gaps": gaps,
        "gap_categories": {g: "C" for g in gaps},
        "gap_clusters_by_id": {
            g: {"id": g, "label": g, "gaps": [g], "jd_skills": [], "jd_context": ""} for g in gaps
        },
        "addressed_gaps": [], "current_gap_index": 0,
        "current_question": "Have you worked with crypto or blockchain settlement?",
        "messages": [{"role": "assistant", "content": "Have you worked with crypto or blockchain settlement?"}],
        "questions_asked": 1, "hard_ceiling": 12, "questions_per_gap": {},
        "skipped_gaps": [], "full_gaps": [],
    }
    record = InterviewSession(
        job_analysis_id=job.id, gap_analysis_id=None, profile_id=profile.id,
        mode="targeted", status="active", state=state, hard_ceiling=12, questions_asked=1,
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(record)
    await db.commit()
    return record


async def _turn(db, record, answer, provider):
    from applire.services.session import send_message

    writer = AsyncMock(return_value={"question": "Next question?", "choices": None})
    with (
        patch("applire.services.session.question_generator_with_profile", new=writer),
        patch("applire.services.session._cluster_question", new=writer),
        patch("applire.services.session._select_denial_probe_concept", new=AsyncMock(return_value=None)),
    ):
        return await send_message(record.id, answer, db, provider)


@pytest.mark.asyncio
async def test_the_captured_shape_really_is_self_contradicting(db):
    """Fixture check: the real committer persists BOTH halves for this turn."""
    record = await _seed(db)
    await _turn(db, record, _CRYPTO_NO, _Reply(_CAPTURED_REPLY))
    from applire.models.profile import MasterProfile

    profile = await db.get(MasterProfile, uuid.UUID(record.state["profile_id"]))
    entry = profile.profile_json["metadata"]["enrichment_history"][-1]
    assert [c["field"] for c in entry["changes"]] == ["denied_concepts"] * 3
    assert [i["reason"] for i in entry["not_applied"]] == ["no_write"]


@pytest.mark.asyncio
async def test_recorded_denial_turn_carries_no_nothing_recorded_receipt(db):
    record = await _seed(db)
    resp = await _turn(db, record, _CRYPTO_NO, _Reply(_CAPTURED_REPLY))

    assert resp.denial_recorded is True
    assert resp.no_write_receipt is None, (
        "the turn recorded 3 denials — 'Nothing was recorded' would contradict the vault"
    )


@pytest.mark.asyncio
async def test_control_a_turn_that_records_nothing_keeps_its_receipt(db):
    record = await _seed(db)
    empty = {"ops": [], "ambiguities": [], "denials": []}
    resp = await _turn(db, record, _POSITIVE, _Reply(empty))

    assert resp.denial_recorded is False
    assert resp.no_write_receipt == "no_write"
