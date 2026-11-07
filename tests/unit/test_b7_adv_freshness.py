# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 7, adversarial fixes A-5 / A-8 — the shared freshness predicate
(``services/profile_freshness.profile_updated_since``, MD7-9) behind the "Dein
Profil wurde seit der Erstellung … aktualisiert" strip and the F-4 auto-start.

A-5 (main MD7-10, founder F-2: "only content changes the document does not yet
hold"): a write the document ITSELF caused — the review card's "Im Profil
eintragen", the CV section assist's accept, a CV section edit saved to the
profile — never fires the cue or the auto-start for THAT document. The committer
tags those records with the originating document; the OTHER document still gets
the cue; untagged (old) records behave as before; and none of them is an
interview answer.

Proof cases from the adversarial pass (``wt-adv-hz7`` 18589648), adapted to the
tag the fix introduces.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.services.flow.orchestrator import _cv_predates_interview
from applire.services.profile_freshness import CV_FEEDING_SECTIONS, profile_updated_since

CV_ID = uuid.uuid4()
LETTER_ID = uuid.uuid4()
CREATED = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)


def _profile(
    source: str,
    section: str,
    *,
    document: tuple[str, uuid.UUID] | None = None,
    session_id: str | None = None,
) -> dict:
    record = {
        "timestamp": (CREATED + timedelta(minutes=3)).isoformat(),
        "source": source,
        "source_session_id": session_id or str(uuid.uuid4()),
        "changes": [{"section": section, "field": "[0]", "action": "added"}],
    }
    if document is not None:
        record["document_kind"], record["document_id"] = document[0], str(document[1])
    return {"metadata": {"enrichment_history": [record]}}


#: The three writes the CV's own surface makes, tagged as the fix tags them.
CV_SIDE = {
    "review add-evidence": ("testimony", "skills"),
    "cv_assist accept": ("testimony", "work_experience"),
    "cv section edit saved to profile": ("manual_edit", "skills"),
}


# ── A-5: the document's own writes ──────────────────────────────────────────


@pytest.mark.parametrize("case", list(CV_SIDE))
def test_a_write_made_from_this_cv_does_not_say_the_cv_is_behind(case):
    profile = _profile(*CV_SIDE[case], document=("cv", CV_ID))
    assert profile_updated_since(CREATED, profile, sections=CV_FEEDING_SECTIONS, document_id=CV_ID) is False


@pytest.mark.parametrize("case", ["review add-evidence", "cv_assist accept"])
def test_a_write_made_from_this_cv_does_not_trigger_the_auto_start(case):
    cv = SimpleNamespace(id=CV_ID, created_at=CREATED)
    assert _cv_predates_interview(cv, _profile(*CV_SIDE[case], document=("cv", CV_ID))) is False


@pytest.mark.parametrize("case", list(CV_SIDE))
def test_negative_the_letter_still_reads_a_cv_side_write_as_behind(case):
    """The letter was written before the CV-side write and does not say it."""
    profile = _profile(*CV_SIDE[case], document=("cv", CV_ID))
    assert profile_updated_since(CREATED, profile, document_id=LETTER_ID) is True


def test_negative_the_cv_still_reads_a_letter_side_write_as_behind_but_not_as_an_interview():
    """The letter's review card wrote a fact: the CV gets the strip (it does
    not say it), never the auto-start (the card is not an interview answer)."""
    profile = _profile("testimony", "skills", document=("cover_letter", LETTER_ID))
    assert profile_updated_since(CREATED, profile, sections=CV_FEEDING_SECTIONS, document_id=CV_ID) is True
    assert _cv_predates_interview(SimpleNamespace(id=CV_ID, created_at=CREATED), profile) is False
    # …and the letter itself stays quiet about its own card.
    assert profile_updated_since(CREATED, profile, document_id=LETTER_ID) is False


def test_negative_an_untagged_record_behaves_as_before():
    """Old data: a section edit saved before the tag existed carried the CV only
    as ``source_session_id`` — it still counts, exactly as before the fix."""
    old = _profile("manual_edit", "skills", session_id=str(CV_ID))
    assert profile_updated_since(CREATED, old, sections=CV_FEEDING_SECTIONS, document_id=CV_ID) is True


def test_negative_agent_testimony_is_still_an_interview_door():
    """MCP ``submit_testimony`` (untagged) stays an interview source (F-4)."""
    profile = _profile("testimony", "skills")
    assert _cv_predates_interview(SimpleNamespace(id=CV_ID, created_at=CREATED), profile) is True


# ── A-5: the doors tag their records ────────────────────────────────────────


@pytest_asyncio.fixture
async def db():
    import importlib
    import pkgutil

    import applire.models as _models_pkg
    from applire.db.session import Base

    for _m in pkgutil.iter_modules(_models_pkg.__path__):
        importlib.import_module(f"applire.models.{_m.name}")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed_profile(db, profile_json: dict | None = None):
    from tests.support.profile_factory import make_master_profile

    record = make_master_profile(profile_json=profile_json or {"personal_info": {"full_name": "Emma Beispiel"}})
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


def _head(record) -> dict:
    return record.profile_json["metadata"]["enrichment_history"][-1]


class _FixedOpsProvider:
    def __init__(self, payload):
        self._payload = payload

    async def aparse_json(self, prompt, *, system=None, **kwargs):
        return self._payload


_KAFKA = {"ops": [{"op": "upsert_skill", "name": "Kafka", "category": "technical"}], "ambiguities": [], "denials": []}


@pytest.mark.asyncio
async def test_testimony_from_a_document_names_it_and_plain_testimony_does_not(db):
    from applire.services.profile.reconcile.testimony_bridge import submit_testimony

    record = await _seed_profile(db)
    await submit_testimony(
        "Ich habe Kafka betrieben.", db, _FixedOpsProvider(_KAFKA),
        user_id=record.user_id, document_kind="cover_letter", document_id=LETTER_ID,
    )
    await db.refresh(record)
    head = _head(record)
    assert (head["source"], head["document_kind"], head["document_id"]) == ("testimony", "cover_letter", str(LETTER_ID))

    await submit_testimony("Ich habe Kafka betrieben.", db, _FixedOpsProvider(_KAFKA), user_id=record.user_id)
    await db.refresh(record)
    assert _head(record)["document_id"] is None and _head(record)["document_kind"] is None


@pytest.mark.asyncio
async def test_the_assist_bridge_and_the_section_edit_name_the_cv(db):
    from applire.providers.llm.mock import MockLLMProvider
    from applire.services.profile import patch_profile_section
    from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn

    record = await _seed_profile(db)
    await reconcile_interview_turn(
        db, profile_record=record, gap="Python", question="Section Skills: Python?",
        answer="Python daily", provider=MockLLMProvider(), session_id="assist-1",
        record_source="testimony", intake="cv_assist", document_kind="cv", document_id=str(CV_ID),
    )
    await db.commit()
    await db.refresh(record)
    assert (_head(record)["document_kind"], _head(record)["document_id"]) == ("cv", str(CV_ID))

    await patch_profile_section(
        "skills", [{"name": "Go"}], db, source="manual_edit", source_session_id=str(CV_ID),
        user_id=record.user_id, document_kind="cv", document_id=str(CV_ID),
    )
    await db.refresh(record)
    assert (_head(record)["document_kind"], _head(record)["document_id"]) == ("cv", str(CV_ID))

    # The profile page's own section edit names no document.
    await patch_profile_section("skills", [{"name": "Rust"}], db, user_id=record.user_id)
    await db.refresh(record)
    assert _head(record)["document_id"] is None


@pytest.mark.asyncio
async def test_accept_assist_answer_names_its_cv(monkeypatch):
    from applire.services import cv_assist

    owner = uuid.uuid4()
    sent: dict = {}

    async def _owned_cv(db, cv_id, owner):
        return None

    async def _latest(db, owner):
        return object()

    async def _lang(db, user_id=None):
        return "de"

    class _Turn:
        changes: list = []
        denied_concepts: list = []
        no_write_receipt = None

    async def _bridge(db, **kwargs):
        sent.update(kwargs)
        return _Turn()

    class _Db:
        async def commit(self):
            pass

    monkeypatch.setattr(cv_assist, "owned_cv", _owned_cv)
    monkeypatch.setattr(cv_assist, "resolve_owner", lambda user_id, site=None: owner)
    monkeypatch.setattr("applire.services.profile._get_latest", _latest)
    monkeypatch.setattr("applire.services.session.get_ui_language", _lang)
    monkeypatch.setattr("applire.services.profile.reconcile.interview_bridge.reconcile_interview_turn", _bridge)
    sid = str(uuid.uuid4())
    cv_assist._sessions[sid] = {
        "user_id": str(owner), "cv_id": str(CV_ID), "section_id": "skills", "gap_id": "Kafka",
        "section_label": "Skills", "section_content": "x", "question": "Kafka?", "answer": "Ja, drei Jahre.",
    }
    await cv_assist.accept_assist_answer(CV_ID, "skills", sid, None, _Db(), user_id=owner)
    assert (sent["document_kind"], sent["document_id"]) == ("cv", str(CV_ID))


@pytest.mark.asyncio
async def test_the_section_editor_save_to_profile_names_its_cv(monkeypatch):
    from applire.services import cv_section_editor

    seen: dict = {}

    async def _patch(section, value, db, **kwargs):
        seen.update(kwargs, section=section)

    class _Db:
        async def get(self, model, pk):
            return SimpleNamespace(profile_json={"personal_info": {}})

    monkeypatch.setattr("applire.services.profile.patch_profile_section", _patch)
    monkeypatch.setattr(cv_section_editor, "build_section_field_edit", lambda *a, **k: ("skills", [{"name": "Go"}]))
    cv = SimpleNamespace(profile_id=uuid.uuid4(), content_snapshot={}, tailored_data={})
    await cv_section_editor._save_section_to_profile(CV_ID, "skills", "Go", cv, _Db(), uuid.uuid4())
    assert (seen["document_kind"], seen["document_id"]) == ("cv", str(CV_ID))


# ── A-5 at the seams that read the predicate ────────────────────────────────


def _rec(minutes: int, *, document: tuple[str, uuid.UUID] | None = None, source: str = "testimony") -> dict:
    out = {
        "id": str(uuid.uuid4()),
        "timestamp": (CREATED + timedelta(minutes=minutes)).isoformat(),
        "source": source,
        "changes": [{"section": "skills", "field": "[0]", "action": "added"}],
    }
    if document is not None:
        out["document_kind"], out["document_id"] = document[0], str(document[1])
    return out


@pytest.mark.asyncio
async def test_letter_status_skips_its_own_card_and_keeps_the_cv_side_write(db):
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.services.cover_letter import get_cover_letter_status

    from tests.support.profile_factory import set_profile_json

    profile = await _seed_profile(db)
    cl = GeneratedCoverLetter(
        id=LETTER_ID, job_analysis_id=uuid.uuid4(), profile_id=profile.id, user_id=profile.user_id,
        template="classic_german", letter_data={"body": {"paragraphs": ["x"]}},
        pre_gen_inputs={}, status=CoverLetterStatus.ready.value, created_at=CREATED,
    )
    db.add(cl)
    await db.commit()

    async def _letter_with(record):
        set_profile_json(profile, {"metadata": {"enrichment_history": [record]}})
        await db.commit()
        return await get_cover_letter_status(cl.id, db, "http://x", user_id=cl.user_id)

    own = await _letter_with(_rec(3, document=("cover_letter", LETTER_ID)))
    assert own.profile_updated_since is False
    cv_side = await _letter_with(_rec(3, document=("cv", CV_ID)))
    assert cv_side.profile_updated_since is True
