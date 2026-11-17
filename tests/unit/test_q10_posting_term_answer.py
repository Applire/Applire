# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q10 — the answer to the posting-terms tick list, both doors
(rulings Q10-4, Q10-6; ADR-046 / ADR-054 amended 2026-11-17). No Docker, no LLM.

* the card (REST, ``via="person"``): ticked → a profile skill with its
  confirmation; unticked → declined on THIS application, not asked again;
  nothing ticked is a valid answer;
* the agent (``generate_cv`` → ``needs_confirmation``; ``confirm_posting_terms``):
  confirmed → a profile skill recorded via the agent; ``leave_open`` → the CV may
  be generated, NO answer is recorded, the web card still asks;
* no skill reaches the profile through the agent tool without the agent origin.
"""
from __future__ import annotations

import copy
import json
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.owners import HARNESS_USER_ID
from tests.support.posting_links import link_posting

WID = "11111111-1111-1111-1111-111111111111"
QUOTE = "Built Prometheus and Grafana dashboards with alert routing for the tracking services."
VAULT = {
    "personal_info": {"name": "Daniel Kovac", "email": "d@example.com"},
    "professional_summary": {"en": "Backend engineer with eight years of Python services.", "de": None},
    "work_experience": [{
        "id": WID, "company": "Parcelo GmbH", "role": "Backend Engineer",
        "start_date": "2019-01", "end_date": None,
        "responsibilities": [QUOTE, "Designed and ran the shipment order-event pipeline on Apache Kafka."],
        "achievements": [],
    }],
    "skills": [{"name": "Prometheus", "status": "confirmed"}, {"name": "Kafka", "status": "confirmed"},
               {"name": "Python", "status": "confirmed"}],
}
LEDGER = [
    {"concept": "Observability", "surface_forms": ["Prometheus"], "status": "direct", "claimable": True,
     "sources": ["required"], "evidence": "dashboards",
     "backing": {"quote": QUOTE, "unit_path": "work_experience[0].responsibilities[0]"}},
    {"concept": "Event-driven architecture", "surface_forms": ["event-driven"], "status": "direct",
     "claimable": True, "sources": ["required"], "evidence": "pipeline"},
    {"concept": "Apache Kafka", "surface_forms": ["Kafka"], "status": "direct", "claimable": True,
     "sources": ["required"], "evidence": "pipeline"},
]


@pytest_asyncio.fixture
async def db(tmp_path):
    import applire.models.application  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.gap_job  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    from applire.db.session import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'q10.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, *, vault=VAULT, ledger=LEDGER):
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile, authorized_profile_write
    from applire.models.user import User

    if await db.get(User, HARNESS_USER_ID) is None:
        db.add(User(id=HARNESS_USER_ID, email="harness@test.de"))
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=f"h-{uuid.uuid4()}", raw_text="Backend Engineer (m/w/d)",
        role_title="Senior Backend Engineer", company_name="Lieferkette GmbH",
        required_skills=["Observability"], nice_to_have_skills=[], keywords=[],
        seniority_level="Senior", company_culture_signals=[], language_requirement="English",
        jd_language="en",
    )
    db.add(job)
    await link_posting(db, job, HARNESS_USER_ID, hidden=False)
    with authorized_profile_write():
        profile = MasterProfile(id=uuid.uuid4(), profile_json=copy.deepcopy(vault), user_id=HARNESS_USER_ID)
    db.add(profile)
    db.add(GapAnalysis(id=uuid.uuid4(), job_analysis_id=job.id, profile_id=profile.id,
                       user_id=HARNESS_USER_ID, match_score=70, keyword_ledger=copy.deepcopy(ledger)))
    await db.commit()
    return job


async def _profile(db) -> dict:
    from sqlalchemy import select

    from applire.models.profile import MasterProfile

    row = (await db.execute(
        select(MasterProfile).where(MasterProfile.user_id == HARNESS_USER_ID,
                                    MasterProfile.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    )).scalar_one()
    return row.profile_json


def _skill(profile, name):
    return next((s for s in profile["skills"] if s["name"] == name), None)


# ── the list ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_list_names_the_terms_the_person_never_used_with_their_own_words(db):
    from applire.services.posting_term_answer import posting_terms_state

    job = await _seed(db)
    state = await posting_terms_state(db, job.id, HARNESS_USER_ID)
    terms = {t["term"]: t["their_words"] for t in state.terms}
    assert set(terms) == {"Observability", "Event-driven architecture"}
    assert terms["Observability"] == ["Prometheus", QUOTE]
    assert state.agent_must_ask == state.terms


@pytest.mark.asyncio
async def test_across_languages_there_is_no_list(db):
    from applire.services.posting_term_answer import posting_terms_state

    vault = copy.deepcopy(VAULT)
    vault["professional_summary"] = {"de": "Backend-Entwickler mit acht Jahren Python-Diensten.", "en": None}
    vault["work_experience"][0]["responsibilities"] = [
        "Prometheus- und Grafana-Dashboards mit Alarmweiterleitung gebaut.",
        "Die Auftragsereignis-Pipeline auf Apache Kafka entworfen und betrieben."]
    job = await _seed(db, vault=vault)
    assert (await posting_terms_state(db, job.id, HARNESS_USER_ID)).terms == []


# ── the card (via person) ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_ticked_term_becomes_a_skill_with_its_confirmation_and_the_rest_is_declined(db):
    from applire.services.posting_term_answer import answer_posting_terms

    job = await _seed(db)
    state = await answer_posting_terms(db, job.id, HARNESS_USER_ID, confirmed=["observability"], via="person")
    skill = _skill(await _profile(db), "Observability")
    assert skill["status"] == "confirmed"
    (conf,) = skill["confirmations"]
    assert conf["via"] == "person" and conf["on"] == date.today().isoformat()
    assert conf["posting"] == "Lieferkette GmbH" and conf["job_id"] == str(job.id)
    assert state.terms == [] and state.declined == ["Event-driven architecture"]
    assert state.confirmed == ["Observability"]


@pytest.mark.asyncio
async def test_nothing_ticked_is_an_answer_and_is_not_asked_again(db):
    from applire.services.posting_term_answer import answer_posting_terms, posting_terms_state

    job = await _seed(db)
    before = copy.deepcopy(await _profile(db))
    await answer_posting_terms(db, job.id, HARNESS_USER_ID, confirmed=[], via="person")
    assert (await _profile(db))["skills"] == before["skills"]
    state = await posting_terms_state(db, job.id, HARNESS_USER_ID)
    assert state.terms == [] and set(state.declined) == {"Observability", "Event-driven architecture"}


@pytest.mark.asyncio
async def test_a_term_off_the_list_is_refused(db):
    from applire.services.posting_term_answer import UnknownTerms, answer_posting_terms

    job = await _seed(db)
    with pytest.raises(UnknownTerms):
        await answer_posting_terms(db, job.id, HARNESS_USER_ID, confirmed=["Apache Kafka"], via="person")
    assert _skill(await _profile(db), "Apache Kafka") is None


# ── the agent door (mcp.call_tool) ───────────────────────────────────────────

def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _call(name, args, db):
    from applire.mcp.server import mcp

    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)), \
         patch("applire.mcp.server._current_user_id", AsyncMock(return_value=HARNESS_USER_ID)):
        out = await mcp.call_tool(name, args)
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_generate_cv_puts_the_terms_to_the_person_before_any_generation(db):
    job = await _seed(db)
    with patch("applire.services.cv.generate_cv", AsyncMock(side_effect=AssertionError("generated"))):
        out = await _call("generate_cv", {"job_id": str(job.id)}, db)
    assert out["status"] == "needs_confirmation" and out["kind"] == "posting_terms"
    assert out["next"] == "confirm_posting_terms" and "ask_the_person" in out
    assert {t["term"] for t in out["terms"]} == {"Observability", "Event-driven architecture"}
    assert out["untrusted_content"]["kind"] == "job_posting"
    assert "terms[].term" in out["untrusted_content"]["fields"]


@pytest.mark.asyncio
async def test_the_agent_confirms_a_term_recorded_via_the_agent(db):
    job = await _seed(db)
    out = await _call("confirm_posting_terms", {"job_id": str(job.id), "confirmed": ["Observability"]}, db)
    (conf,) = _skill(await _profile(db), "Observability")["confirmations"]
    assert conf["via"] == "agent"
    assert out["may_generate"] is False                       # one term still unanswered
    assert [t["term"] for t in out["terms"]] == ["Event-driven architecture"]


@pytest.mark.asyncio
async def test_left_open_lets_generation_proceed_and_the_card_still_asks(db):
    from applire.services.posting_term_answer import posting_terms_state

    job = await _seed(db)
    out = await _call("confirm_posting_terms", {"job_id": str(job.id), "leave_open": True}, db)
    assert out["may_generate"] is True and out["answered_at"] is None
    profile = await _profile(db)
    assert _skill(profile, "Observability") is None and _skill(profile, "Event-driven architecture") is None
    state = await posting_terms_state(db, job.id, HARNESS_USER_ID)
    assert {t["term"] for t in state.terms} == {"Observability", "Event-driven architecture"}
    assert state.declined == []                                # nothing recorded as answered
    generated = MagicMock(model_dump=MagicMock(return_value={"cv_id": "x", "status": "ready"}))
    with patch("applire.services.cv.generate_cv", AsyncMock(return_value=generated)):
        res = await _call("generate_cv", {"job_id": str(job.id)}, db)
    assert res["status"] == "ready"


@pytest.mark.asyncio
async def test_no_skill_reaches_the_profile_through_the_agent_tool_without_the_agent_origin(db):
    job = await _seed(db)
    await _call("confirm_posting_terms",
                {"job_id": str(job.id), "confirmed": ["Observability", "Event-driven architecture"]}, db)
    profile = await _profile(db)
    added = [s for s in profile["skills"] if s["name"] not in {x["name"] for x in VAULT["skills"]}]
    assert added and all(c["via"] == "agent" for s in added for c in s["confirmations"])
    trail = (profile.get("metadata") or {}).get("enrichment_history") or []
    assert trail and trail[-1]["source"] == "agent_interview"


@pytest.mark.asyncio
async def test_the_agent_cannot_confirm_a_term_off_the_list(db):
    job = await _seed(db)
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="not on the list: Kubernetes"):
        await _call("confirm_posting_terms", {"job_id": str(job.id), "confirmed": ["Kubernetes"]}, db)
    assert _skill(await _profile(db), "Kubernetes") is None


# ── the confirmation record on the agent door (ADR-084) ──────────────────────
# A confirmation's `posting` label is the posting's company or role name —
# untrusted job-posting text. Every agent read that echoes a skill marks it; the
# record is not agent-writable through update_profile.

_LABEL = "profile.skills[].confirmations[].posting"
#: the write's own receipt echoes the confirmed skill, label included
_TRAIL = "profile.metadata.enrichment_history[].changes[].new_value.confirmations[].posting"


async def _confirmed_by_person(db):
    from applire.services.posting_term_answer import answer_posting_terms

    job = await _seed(db)
    await answer_posting_terms(db, job.id, HARNESS_USER_ID, confirmed=["Observability"], via="person")
    return job


@pytest.mark.asyncio
async def test_get_profile_marks_the_posting_label_as_job_posting_text(db):
    await _confirmed_by_person(db)
    out = await _call("get_profile", {}, db)
    (conf,) = _skill(out["profile"], "Observability")["confirmations"]
    assert conf["posting"] == "Lieferkette GmbH"
    assert out["untrusted_content"]["kind"] == "job_posting"
    assert out["untrusted_content"]["fields"] == [_LABEL, _TRAIL]


@pytest.mark.asyncio
async def test_a_profile_without_a_confirmation_is_not_marked(db):
    await _seed(db)
    assert "untrusted_content" not in await _call("get_profile", {}, db)


@pytest.mark.asyncio
async def test_the_profile_resource_marks_the_posting_label_too(db):
    from applire.mcp.server import mcp

    await _confirmed_by_person(db)
    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)), \
         patch("applire.mcp.server._current_user_id", AsyncMock(return_value=HARNESS_USER_ID)):
        contents = await mcp.read_resource("profile://current")
    out = json.loads(list(contents)[0].content)
    assert out["untrusted_content"]["fields"] == [_LABEL, _TRAIL]


@pytest.mark.asyncio
async def test_update_profile_neither_forges_nor_erases_a_confirmation(db):
    await _confirmed_by_person(db)
    read = await _call("get_profile", {}, db)
    skills = [{k: v for k, v in s.items() if k != "confirmations"} for s in read["profile"]["skills"]]
    forged = {"name": "Event-driven architecture", "category": "technical", "status": "confirmed",
              "confirmations": [{"name": "Event-driven architecture", "via": "person",
                                 "on": "2026-11-17", "posting": "Lieferkette GmbH"}]}
    with patch("applire.mcp.server.get_provider", return_value=None):
        out = await _call("update_profile", {"section": "skills", "data": skills + [forged]}, db)
    profile = await _profile(db)
    assert "confirmations" not in _skill(profile, "Event-driven architecture")   # not forged
    (kept,) = _skill(profile, "Observability")["confirmations"]                 # not erased
    assert kept["via"] == "person"
    assert _LABEL in out["untrusted_content"]["fields"]


def test_the_interview_follow_up_prompt_never_carries_the_posting_label():
    from applire.prompts.interview import build_follow_up_question_prompt

    profile = {"skills": [{"name": "Observability", "confirmations": [
        {"name": "Observability", "via": "person", "on": "2026-11-17", "posting": "LABEL-CANARY-77"}]}],
        "work_experience": []}
    prompt = build_follow_up_question_prompt("Observability", "hint", profile, [])
    assert "Observability" in prompt and "LABEL-CANARY-77" not in prompt


def test_a_bare_skill_name_keeps_its_stored_confirmation():
    from applire.services.posting_term_answer import keep_stored_confirmations

    conf = {"name": "Observability", "via": "person", "on": "2026-11-17", "posting": "Lieferkette GmbH"}
    stored = [{"name": "Observability", "confirmations": [conf]}]
    assert keep_stored_confirmations(["observability", "Kafka"], stored) == [
        {"name": "observability", "confirmations": [conf]}, "Kafka"]


@pytest.mark.asyncio
async def test_the_posting_label_is_never_the_persons_word(db):
    """The label rides the skill AND the write's own receipt; neither may count
    as vault text for the reading (or a posting's name could ground its terms)."""
    from applire.services.keyword_ledger import profile_literal_corpus

    await _confirmed_by_person(db)
    profile = await _profile(db)
    assert "Lieferkette GmbH" in json.dumps(profile, ensure_ascii=False)
    assert "lieferkette" not in profile_literal_corpus(profile)
