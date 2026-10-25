# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""783:60 (Hazelnut build 4, ruling V-3 = A) — an accepted CV-assist answer
reaches the vault.

On "Übernehmen" the candidate's ANSWER — the one the server stored with the
session, never text the client sends and never the generated suggestion —
goes through the interview-shaped reconcile door as testimony
``{gap, question, answer}``, intake ``cv_assist``, through ``commit_ops``.

Measured before wiring (luna, 10 held-out assist cases × 2): 0 clear false
writes in 34 ops, 5 borderline; records in Runs/Hazelnut/build-4/v/.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.exceptions import ReconcileFailedError
from applire.models.profile import MasterProfile, authorized_profile_write
from applire.providers.llm.mock import MockLLMProvider
from applire.services import cv_assist
from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn

_OWNER = uuid.UUID(int=5)
_CV = uuid.UUID(int=9)


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_the_bridge_records_the_answer_as_testimony_through_cv_assist(db_session):
    with authorized_profile_write():
        record = MasterProfile(profile_json={"personal_info": {"name": "Daniel Kovač"}})
    db_session.add(record)
    await db_session.commit()
    await reconcile_interview_turn(
        db_session, profile_record=record, gap="Python", question="Section Skills: Python?",
        answer="Python daily", provider=MockLLMProvider(), session_id="assist-1",
        record_source="testimony", intake="cv_assist",
    )
    await db_session.commit()
    [trail] = record.profile_json["metadata"]["enrichment_history"]
    assert trail["source"] == "testimony"
    assert any(s.get("name") == "Python" for s in record.profile_json["skills"])


def _session(answer: str | None = "I wrote the Terraform modules for both VPCs.") -> str:
    sid = str(uuid.uuid4())
    cv_assist._sessions[sid] = {
        "user_id": str(_OWNER), "cv_id": str(_CV), "section_id": "experience-0",
        "gap_id": "Infrastructure as Code",
        "section_label": "Experience", "section_content": "Backend Engineer",
        "question": "Experience section: did you use Terraform there?",
        **({"answer": answer} if answer is not None else {}),
    }
    return sid


@pytest.fixture
def seams(monkeypatch):
    calls: dict = {}

    async def _owned_cv(db, cv_id, owner):
        return None

    async def _latest(db, owner):
        return calls.get("record", object())

    async def _lang(db, user_id=None):
        return "de"

    class _Turn:
        changes: list = []
        denied_concepts: list = []
        no_write_receipt = None

    async def _bridge(db, **kwargs):
        calls["bridge"] = kwargs
        if calls.get("fail"):
            raise ReconcileFailedError("provider down")
        return _Turn()

    class _Db:
        async def commit(self):
            calls["committed"] = True

    monkeypatch.setattr(cv_assist, "owned_cv", _owned_cv)
    monkeypatch.setattr(cv_assist, "resolve_owner", lambda user_id, site=None: _OWNER)
    monkeypatch.setattr("applire.services.profile._get_latest", _latest)
    monkeypatch.setattr("applire.services.session.get_ui_language", _lang)
    monkeypatch.setattr(
        "applire.services.profile.reconcile.interview_bridge.reconcile_interview_turn", _bridge
    )
    calls["db"] = _Db()
    return calls


@pytest.mark.asyncio
async def test_accept_sends_the_stored_answer_never_the_suggestion(seams):
    sid = _session()
    await cv_assist.accept_assist_answer(_CV, "experience-0", sid, MockLLMProvider(), seams["db"], user_id=_OWNER)

    sent = seams["bridge"]
    assert sent["answer"] == "I wrote the Terraform modules for both VPCs."
    assert sent["question"] == "Experience section: did you use Terraform there?"
    assert sent["gap"] == "Infrastructure as Code"
    assert (sent["record_source"], sent["intake"]) == ("testimony", "cv_assist")
    assert seams.get("committed") is True
    assert sid not in cv_assist._sessions  # one write per session


@pytest.mark.asyncio
async def test_no_answer_yet_is_refused_and_writes_nothing(seams):
    sid = _session(answer=None)
    with pytest.raises(ValueError):
        await cv_assist.accept_assist_answer(_CV, "experience-0", sid, MockLLMProvider(), seams["db"], user_id=_OWNER)
    assert "bridge" not in seams


@pytest.mark.asyncio
async def test_a_foreign_section_is_refused(seams):
    sid = _session()
    with pytest.raises(ValueError):
        await cv_assist.accept_assist_answer(_CV, "skills", sid, MockLLMProvider(), seams["db"], user_id=_OWNER)
    assert "bridge" not in seams


@pytest.mark.asyncio
async def test_a_failed_reconcile_keeps_the_session_for_a_retry(seams):
    seams["fail"] = True
    sid = _session()
    with pytest.raises(ReconcileFailedError):
        await cv_assist.accept_assist_answer(_CV, "experience-0", sid, MockLLMProvider(), seams["db"], user_id=_OWNER)
    assert sid in cv_assist._sessions
    assert "committed" not in seams


def test_the_route_exists():
    from applire.routers.cv import router

    assert any(getattr(r, "path", "").endswith("/assist/accept") and "POST" in r.methods for r in router.routes)


@pytest.mark.asyncio
async def test_submitting_an_answer_stores_it_for_the_accept(seams, monkeypatch):
    async def _ground(suggestion, db, **kwargs):
        return suggestion, 0

    class _Provider:
        async def acomplete(self, prompt, **kwargs):
            return "Built Terraform modules for staging and production VPCs."

    monkeypatch.setattr(cv_assist, "_ground_suggestion", _ground)
    sid = _session(answer=None)
    await cv_assist.submit_assist_answer(
        _CV, "experience-0", sid, "Yes, both VPCs.", _Provider(), seams["db"], user_id=_OWNER
    )
    assert cv_assist._sessions[sid]["answer"] == "Yes, both VPCs."
