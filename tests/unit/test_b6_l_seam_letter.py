# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 6, WP-L — SEAMS: one G-1 gap set, one G-2 employer record, every call site.

The gap ladder is ONE fact read at several places of `_render_cover_letter_body`; the
2026-11-04 founder UAT failed because two of them (the writer's REQUIRED block and the
loop's REQUIRED stated-limits entry) disagreed with a third (the per-round UNADDRESSED
block). Each call site gets a named test that drives the real background task once and
asserts on what that site RECEIVED:

* writer       — `build_cover_letter_prompt(gap_ladder_block=…, employer_record=…)`
* loop source  — `positioning_requested.gap_positioning` / `.motivation` /
                 `.company_domain_engagement.candidate_record` / constraint-only `stated_limits`
* v2 checks    — `_v2_checks_fn(named_gaps=…)` (the per-round invented-limit signal)
* terminal     — `_terminal_review_letter(named_gaps=…)` (settle cut, advisory, signal)
* testimony    — `find_gap_testimony` is asked about the NAMED gap labels only

Harness mirrors `test_747_letter_hedge_view_seam.py`. Synthetic data.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

STATEMENT = "Mit Boomi habe ich nicht gearbeitet; Schnittstellen habe ich mit Apache Camel gebaut."

PROFILE = {
    "personal_info": {"name": "Jana Beispiel", "email": "jb@example.com"},
    "work_experience": [
        {"id": "w1", "company": "Kestrel Logistik GmbH", "role": "Head of Integration",
         "start_date": "2019-01", "is_current": True,
         "responsibilities": ["Built 40 partner interfaces on Apache Camel"],
         "technologies": ["Apache Camel"]},
        {"id": "w2", "company": "Nordlicht Therapeutics SE", "role": "Integration Architect",
         "start_date": "2014-03", "end_date": "2018-12",
         "responsibilities": ["Designed the LIMS-MES-ERP integration for clinical manufacturing"]},
    ],
    "skills": [{"name": "Apache Camel", "category": "technical", "status": "confirmed",
                "source": "transcribed"}],
    "signature_stories": [{"title": "Kafka beside Camel", "challenge": "Kafka was new to our team",
                           "mechanism": "we ran Kafka and Camel side by side",
                           "outcome": "migrated in 3 months"},
                          {"title": "Terraform per site", "challenge": "Terraform rollout stalled",
                           "mechanism": "Terraform modules per site", "outcome": "done"}],
    "metadata": {"denied_concepts": [
        {"concept": "Boomi", "statement": STATEMENT, "source": "interview",
         "denial_level": "direct", "date": "2026-10-30"},
        {"concept": "Boomi Atmosphere", "statement": STATEMENT, "source": "interview",
         "denial_level": "direct", "date": "2026-10-30"}]},
}


def _row(c, status, w=1.0, claimable=False, **kw):
    r = {"concept": c, "surface_forms": [c], "status": status, "claimable": claimable,
         "fit_weight": w, "sources": ["required" if w >= 1.0 else "nice_to_have"]}
    r.update(kw)
    return r


LEDGER_3 = [
    _row("Apache Camel", "direct", claimable=True, evidence="Built 40 partner interfaces on Apache Camel"),
    _row("Kafka", "gap"), _row("Boomi", "denied"), _row("Boomi Atmosphere", "denied"),
    _row("Terraform", "gap"), _row("OpenShift", "gap", 0.5),
]
JD_3 = ("Director Integration Platform at Nordlicht Therapeutics SE. Kafka event streaming, "
        "Kafka operations, our Boomi landscape incl. Boomi Atmosphere, Terraform. Kafka is a must.")
LEDGER_2 = [r for r in LEDGER_3 if r["concept"] != "Terraform"]
JD_2 = JD_3.replace(", Terraform", "")


@pytest_asyncio.fixture
async def letter_db():
    from applire.db.session import Base
    import applire.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db, ledger, jd):
    from tests.support.profile_factory import make_master_profile
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    profile = make_master_profile(profile_json=PROFILE)
    db.add(User(id=profile.user_id, email=f"b6l-{uuid.uuid4().hex[:6]}@test.com"))
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=uuid.uuid4().hex, raw_text=jd,
        role_title="Director Integration Platform", company_name="Nordlicht Therapeutics SE",
        required_skills=[], nice_to_have_skills=[], keywords=[], seniority_level="director",
        company_culture_signals=[], language_requirement="en",
    )
    db.add(job)
    db.add(profile)
    await db.flush()
    db.add(GapAnalysis(job_analysis_id=job.id, profile_id=profile.id, user_id=profile.user_id,
                       match_score=60, keyword_ledger=ledger))
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id, profile_id=profile.id, template="classic_german",
        letter_data={}, pre_gen_inputs={}, status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    return job, cl


async def _run(db, ledger, jd, monkeypatch) -> dict:
    """Drive the real background task once; capture what every G-1/G-2 call site received."""
    import applire.services.cover_letter as svc
    import applire.services.cover_letter_positioning as pos

    monkeypatch.delenv("LETTER_PIPELINE", raising=False)  # v2, the default
    job, cl = await _seed(db, ledger, jd)
    captured: dict = {"terminal": [], "checks": [], "testimony": []}

    async def fake_loop(**kw):
        captured.setdefault("sources", []).append(kw.get("source"))
        return kw["draft"]

    def fake_writer_prompt(**kw):
        captured["writer_kwargs"] = kw
        return "PROMPT"

    real_terminal, real_checks, real_testimony = (
        svc._terminal_review_letter, svc._v2_checks_fn, pos.find_gap_testimony)

    async def spy_terminal(*a, **kw):
        captured["terminal"].append(kw.get("named_gaps"))
        return await real_terminal(*a, **kw)

    def spy_checks(*a, **kw):
        captured["checks"].append(kw.get("named_gaps"))
        return real_checks(*a, **kw)

    def spy_testimony(gaps, stories):
        captured["testimony"].append(list(gaps))
        return real_testimony(gaps, stories)

    async def fake_refresh(gap, profile_json, db_, seam=None):
        return ledger

    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value={"body": {"paragraphs": [
        "Dear Hiring Manager,", "x", "Kind regards"]}})
    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl, \
         patch("applire.services.cover_letter.get_provider", return_value=provider), \
         patch("applire.services.cover_letter.build_cover_letter_prompt", side_effect=fake_writer_prompt), \
         patch("applire.services.cover_letter.review_and_refine", new=AsyncMock(side_effect=fake_loop)), \
         patch("applire.services.cover_letter._terminal_review_letter", new=spy_terminal), \
         patch("applire.services.cover_letter._v2_checks_fn", new=spy_checks), \
         patch("applire.services.cover_letter_positioning.find_gap_testimony", new=spy_testimony), \
         patch("applire.services.keyword_ledger.refresh_persist_and_rescore", new=fake_refresh), \
         patch("applire.services.cover_letter_pdf.render_pdf",
               new=AsyncMock(side_effect=RuntimeError("no browser in unit test"))):
        sl.return_value.__aenter__.return_value = db
        await svc._render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)
    return captured


def _positioning(captured) -> dict:
    sources = [s for s in captured.get("sources", []) if s]
    assert sources, "the letter review loop was never called"
    obj, _ = json.JSONDecoder().raw_decode(sources[0])
    return obj["positioning_requested"]


@pytest.mark.asyncio
async def test_SEAM_writer_gets_the_ladder_naming_only_the_most_urgent_of_three(letter_db, monkeypatch):
    captured = await _run(letter_db, LEDGER_3, JD_3, monkeypatch)
    block = captured["writer_kwargs"]["gap_ladder_block"]
    assert block.startswith("=== KEY-REQUIREMENT GAPS")
    named = block.split("NAME IN THE LETTER:")[1]
    assert "Kafka" in named and "Boomi" not in named and "Terraform" not in named
    assert "This posting has 3 key requirement(s)" in block
    assert "required_limits_block" not in captured["writer_kwargs"]
    # the unnamed recorded statement stays a constraint, never an obligation
    assert STATEMENT in (captured["writer_kwargs"].get("stated_limits_block") or "")


@pytest.mark.asyncio
async def test_SEAM_writer_gets_both_gaps_of_two_and_the_named_statement_leaves_the_constraint(
        letter_db, monkeypatch):
    captured = await _run(letter_db, LEDGER_2, JD_2, monkeypatch)
    named = captured["writer_kwargs"]["gap_ladder_block"].split("NAME IN THE LETTER:")[1]
    assert "Kafka" in named and "Boomi" in named and STATEMENT in named
    assert STATEMENT not in (captured["writer_kwargs"].get("stated_limits_block") or "")


@pytest.mark.asyncio
async def test_SEAM_writer_gets_the_candidates_own_record_of_the_employer(letter_db, monkeypatch):
    captured = await _run(letter_db, LEDGER_3, JD_3, monkeypatch)
    record = captured["writer_kwargs"]["employer_record"]
    assert "Integration Architect at Nordlicht Therapeutics SE (2014-03–2018-12, ENDED)" in record
    assert "An ENDED position is the past" in record


@pytest.mark.asyncio
async def test_SEAM_loop_source_carries_the_same_gaps_the_motivation_and_the_record(letter_db, monkeypatch):
    captured = await _run(letter_db, LEDGER_3, JD_3, monkeypatch)
    pr = _positioning(captured)
    assert pr["gap_positioning"]["required"] is True
    assert pr["gap_positioning"]["gap_count"] == 3
    assert [n["gap"] for n in pr["gap_positioning"]["named"]] == ["Kafka"]
    assert pr["motivation"]["required"] is True and "typed" not in pr["motivation"]
    record = pr["company_domain_engagement"]["candidate_record"][0]
    assert record["role"] == "Integration Architect" and record["ended"] is True
    assert "within its dates" in pr["company_domain_engagement"]["instruction"]
    assert "within its dates" in pr["motivation"]["instruction"]
    assert "required" not in pr["stated_limits"]           # constraint only (G-1)
    assert pr["stated_limits"]["limits"] == [STATEMENT]


@pytest.mark.asyncio
async def test_SEAM_v2_checks_ground_the_named_gaps(letter_db, monkeypatch):
    captured = await _run(letter_db, LEDGER_3, JD_3, monkeypatch)
    assert captured["checks"] and all(n == ["Kafka"] for n in captured["checks"]), captured["checks"]


@pytest.mark.asyncio
async def test_SEAM_terminal_review_grounds_the_named_gaps(letter_db, monkeypatch):
    captured = await _run(letter_db, LEDGER_2, JD_2, monkeypatch)
    assert captured["terminal"], "terminal review never ran"
    assert all(n == ["Kafka", "Boomi", "Boomi Atmosphere"] for n in captured["terminal"]), captured["terminal"]


@pytest.mark.asyncio
async def test_SEAM_gap_testimony_is_asked_about_the_named_gap_labels_only(letter_db, monkeypatch):
    captured = await _run(letter_db, LEDGER_3, JD_3, monkeypatch)
    assert captured["testimony"] == [["Kafka"]], captured["testimony"]
