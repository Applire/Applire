# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet quality wave 2b, WP-Q8 — the leadership-kind question carries the
team size, and an entry merge carries the stored kind.

* RULING Q3-2: a stored team size is confirmed with the kind question ("Im
  Profil stehen N Personen – stimmt das?"); the CV prints the confirmed or
  corrected count.
* RULING Q8-2: a role whose own text says the person leads people but which
  holds no team size gets the kind question once, including the number. The
  text only TRIGGERS the question; kind and size are stored only from the answer.
* RULING Q8-4: one question per role; the chips carry the number; kind and size
  are stored only from an exact chip label plus at most one whole number; any
  other answer stores no kind and no size.
* RULING Q8-1: the stored kind carries over when two entries of the same role
  are merged; two different answers → the person is asked once more.
"""
from __future__ import annotations

import copy
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import (
    ApplyImportMerge,
    MergeEntries,
    ReplaceSection,
    SetField,
)
import sys
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile, retire_live_profiles  # noqa: E402

# ── harness (adapted from tests/unit/test_q2_scope_question.py — test modules
# do not import each other) ──────────────────────────────────────────────────

_SCOPE = "Führungsspanne ~120 MA"


def _scope_row():
    return {
        "concept": _SCOPE, "surface_forms": [], "sources": ["required"], "fit_weight": 1.0,
        "status": "partial", "evidence": "JD bar …", "claimable": True, "narrative_backed": True,
        "bar": {"kind": "team_size", "level": "required", "value": 120.0, "value_max": None,
                "comparator": "approx",
                "quote": "Gesamtverantwortung für Produktion (ca. 120 Mitarbeitende, Mehrschichtbetrieb).",
                "candidate_values": [{"value": 2, "entry": "Controllerin @ Kanzlei Rhein"}],
                "cited_entry": None, "attested": None},
    }


def _scope_cluster():
    from applire.services.interview.scope_question import carve_scope_clusters

    return carve_scope_clusters([], [_scope_row()], [])[0]


def _bridge(addressed=True):
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    async def _run(db, *, profile_record, **_kwargs):
        return InterviewTurnResult(
            profile_dict=copy.deepcopy(profile_record.profile_json), changes=[],
            addressed=addressed, denial_recorded=False, denied_concepts=[],
        )

    return AsyncMock(side_effect=_run)


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models  # noqa: F401 — every table

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()

ANSWERED = {"option": "functional", "answer": "Fachliche Anleitung",
            "recorded_at": "2026-11-14T10:00:00+00:00", "source": "interview"}
BOTH = {**ANSWERED, "option": "both", "answer": "Beides"}


# ── Q8-4: reading the answer ─────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "expected"), [
    ("Fachliche Anleitung", ("functional", None)),
    ("Fachliche Anleitung – 14 Personen", ("functional", 14)),
    ("fachliche anleitung - 9", ("functional", 9)),
    ("Beides – Anzahl Personen: 4", ("both", 4)),
    ("Beides – Anzahl Personen:", ("both", None)),
    ("Disziplinarisch (Personalverantwortung) – 1 Person", ("disciplinary", 1)),
    ("Both – 12 people", ("both", 12)),
    ("Disciplinary (line management) – number of people: 7", ("disciplinary", 7)),
    ("Fachliche Anleitung – 14 Personen.", ("functional", 14)),
    # Everything else is own words: no kind, no size.
    ("Fachliche Anleitung – 14 Personen, aber eigentlich 3", ("own_words", None)),
    ("Fachliche Anleitung von 3", ("own_words", None)),
    ("ja, 14, disziplinarisch", ("own_words", None)),
    ("Beides – 0 Personen", ("own_words", None)),
    ("Beides, 12345", ("own_words", None)),
    ("Beides – 12 Personen und 3 Azubis", ("own_words", None)),
    ("nein", ("own_words", None)),
    ("", ("skipped", None)),
])
def test_the_answer_is_read_only_from_an_exact_label_and_one_number(text, expected):
    from applire.services.profile.leadership_kind import option_for_answer, parse_kind_answer

    assert parse_kind_answer(text) == expected
    assert option_for_answer(text) == expected[0]


# ── Q3-2 / Q8-2: the question ────────────────────────────────────────────────

def _q(entry, lang):
    from applire.services.interview.scope_question import leadership_kind_question

    return leadership_kind_question({"entry": entry}, lang)


def test_a_stored_size_is_confirmed_in_the_kind_question():
    said = "Führung einer Schicht mit 14 Mitarbeitenden in der Blechumformung."
    de = _q({"company": "Rasselstein", "team_size": 14, "said": said}, "de")
    assert de["question"] == (
        f"Du schreibst: »{said}« Im Profil stehen 14 Personen – stimmt das? War das disziplinarische "
        "Führung (Personalverantwortung) oder fachliche Anleitung? Wenn die Zahl nicht stimmt, "
        "ändere sie in deiner Antwort.")
    assert de["choices"] == ["Disziplinarisch (Personalverantwortung) – 14 Personen",
                             "Fachliche Anleitung – 14 Personen", "Beides – 14 Personen"]
    en = _q({"company": "Rasselstein", "team_size": 14, "said": said}, "en")
    assert en["question"] == (
        f"You write: “{said}” Your profile shows 14 people – is that right? Was that line management "
        "(disciplinary) or functional guidance? If the number is wrong, change it in your answer.")
    assert en["choices"] == ["Disciplinary (line management) – 14 people",
                             "Functional guidance – 14 people", "Both – 14 people"]
    one = _q({"company": "Weberit", "team_size": 1, "said": ""}, "de")
    assert one["question"].startswith("Bei Weberit stehen im Profil 1 Person – stimmt das?")
    assert one["choices"][1] == "Fachliche Anleitung – 1 Person"


def test_an_unsized_role_is_asked_the_number_with_open_chips():
    said = "Fachliche Anleitung von zwei Werkstudierenden"
    de = _q({"company": "Kanzlei", "team_size": None, "said": said}, "de")
    assert de["question"] == (
        f"Du schreibst: »{said}«. Wie viele Personen waren das, und war es disziplinarische "
        "Führung (Personalverantwortung) oder fachliche Anleitung?")
    assert de["choices"] == ["Disziplinarisch (Personalverantwortung) – Anzahl Personen:",
                             "Fachliche Anleitung – Anzahl Personen:", "Beides – Anzahl Personen:"]
    en = _q({"company": "Kanzlei", "team_size": None, "said": said}, "en")
    assert en["question"] == (
        f"You write: “{said}”. How many people was that, and was it line management "
        "(disciplinary) or functional guidance?")
    assert en["choices"][0] == "Disciplinary (line management) – number of people:"
    # Every chip, sent as clicked or completed, is read back as its own option.
    from applire.services.profile.leadership_kind import parse_kind_answer

    for chip, option in zip(de["choices"], ("disciplinary", "functional", "both")):
        assert parse_kind_answer(chip) == (option, None)
        assert parse_kind_answer(chip + " 4") == (option, 4)
    for chip, option in zip(_q({"team_size": 14, "said": "x"}, "de")["choices"],
                            ("disciplinary", "functional", "both")):
        assert parse_kind_answer(chip) == (option, 14)


# ── Q8-2: who is asked ───────────────────────────────────────────────────────

_TEXT_PROFILE = {"work_experience": [
    {"id": "w1", "company": "Weberit", "role": "Produktionsleiter", "team_size": 38},
    {"id": "w2", "company": "Kanzlei", "role": "Controllerin",
     "responsibilities": ["Fachliche Anleitung von zwei Werkstudierenden."]},
    {"id": "w3", "company": "Bank", "role": "Analystin",
     "responsibilities": ["Begleitung der jährlichen ISO-9001-Audits."]},
    {"id": "w4", "company": "Asked", "role": "Leiterin",
     "responsibilities": ["Führung von 5 Mitarbeitenden."], "leadership_kind_record": ANSWERED,
     "leadership_kind": "functional"},
]}


def test_a_role_with_leadership_text_and_no_size_is_listed_without_a_size():
    from applire.services.profile.role_facts import (
        entries_needing_leadership_kind,
        entries_needing_leadership_size,
        entries_to_ask_leadership,
    )

    assert [e["entry_id"] for e in entries_needing_leadership_kind(_TEXT_PROFILE)] == ["w1"]
    unsized = entries_needing_leadership_size(_TEXT_PROFILE)
    assert unsized == [{"entry_id": "w2", "company": "Kanzlei", "role": "Controllerin",
                        "team_size": None, "said": "Fachliche Anleitung von zwei Werkstudierenden."}]
    assert [e["entry_id"] for e in entries_to_ask_leadership(_TEXT_PROFILE)] == ["w1", "w2"]


def test_the_planner_asks_the_unsized_role_once():
    from applire.services.interview.scope_question import (
        LEADERSHIP_KIND_ID,
        kind_entry_key,
        plan_leadership_kind,
    )

    ids, _c, by_id = plan_leadership_kind(["a"], {"a": "B"}, {"a": {}}, _TEXT_PROFILE)
    assert ids == ["a", LEADERSHIP_KIND_ID, f"{LEADERSHIP_KIND_ID}-2"]
    assert by_id[f"{LEADERSHIP_KIND_ID}-2"]["entry"]["team_size"] is None
    ids2, _c, _b = plan_leadership_kind(
        ["a"], {"a": "B"}, {"a": {}}, _TEXT_PROFILE,
        already_asked={kind_entry_key({"entry_id": "w1"}), kind_entry_key({"entry_id": "w2"})},
    )
    assert ids2 == ["a"]


# ── Q3-2 / Q8-2: the write ───────────────────────────────────────────────────

def _one(**w):
    return MasterProfileData.model_validate({"work_experience": [
        {"id": "w1", "company": "Weberit", "role": "Leiter", **w}]})


def test_a_confirmed_size_is_recorded_and_changes_nothing_else():
    from applire.services.profile.leadership_kind import leadership_kind_op

    result = apply_ops(_one(team_size=14), [leadership_kind_op("w1", "functional", answer="x", team_size=14)],
                       "interview")
    e = result.profile.work_experience[0]
    assert (e.team_size, e.leadership_kind, e.leadership_kind_record.team_size) == (14, "functional", 14)
    assert [c.field for c in result.changes] == ["leadership_kind"]


def test_a_corrected_size_is_written_with_its_receipt():
    from applire.services.profile.leadership_kind import leadership_kind_op

    result = apply_ops(_one(team_size=2), [leadership_kind_op("w1", "functional", answer="x", team_size=3)],
                       "interview")
    e = result.profile.work_experience[0]
    assert (e.team_size, e.leadership_kind_record.team_size) == (3, 3)
    size = [c for c in result.changes if c.field == "team_size"]
    assert [(c.old_value, c.new_value, c.rationale_key) for c in size] == [(2, 3, "team_size_recorded")]


def test_a_size_given_for_an_unsized_role_is_written():
    from applire.services.profile.leadership_kind import leadership_kind_op

    result = apply_ops(_one(), [leadership_kind_op("w1", "both", answer="x", team_size=4)], "interview")
    e = result.profile.work_experience[0]
    assert (e.team_size, e.leadership_kind) == (4, "disciplinary")
    assert [(c.action, c.new_value) for c in result.changes if c.field == "team_size"] == [("added", 4)]


def test_no_number_leaves_the_size_alone():
    from applire.services.profile.leadership_kind import leadership_kind_op

    result = apply_ops(_one(team_size=14), [leadership_kind_op("w1", "functional", answer="x")], "interview")
    e = result.profile.work_experience[0]
    assert (e.team_size, e.leadership_kind_record.team_size) == (14, None)
    assert "team_size" not in e.leadership_kind_record.model_dump()


# ── Q8-1: an entry merge carries the stored kind ─────────────────────────────

def _pair(t: dict, s: dict) -> MasterProfileData:
    return MasterProfileData.model_validate({"work_experience": [
        {"id": "w1", "company": "Weberit", "role": "Leiter", "team_size": 38, **t},
        {"id": "w2", "company": "Weberit GmbH", "role": "Leiter", "team_size": 38, **s},
    ]})


def _merge():
    return MergeEntries(section="work_experience", target_id="w1", source_id="w2")


def _state(profile):
    return [(e.id, e.leadership_kind, e.leadership_kind_record.option if e.leadership_kind_record else None)
            for e in profile.work_experience]


@pytest.mark.parametrize(("t", "s", "expected"), [
    ({}, {"leadership_kind": "disciplinary", "leadership_kind_record": BOTH}, ("disciplinary", "both")),
    ({"leadership_kind": "functional", "leadership_kind_record": ANSWERED}, {}, ("functional", "functional")),
    ({"leadership_kind": "functional", "leadership_kind_record": ANSWERED},
     {"leadership_kind": "functional", "leadership_kind_record": {**ANSWERED, "answer": "anders"}},
     ("functional", "functional")),
    ({}, {}, (None, None)),
], ids=["source-answered", "target-answered", "same-kind", "none"])
def test_the_merge_carries_the_answer(t, s, expected):
    result = apply_ops(_pair(t, s), [_merge()], "manual_edit")
    assert _state(result.profile) == [("w1", *expected)]
    assert "leadership_kind_unasked" not in [i.reason for i in result.not_applied]
    assert not [c for c in result.changes if c.field in ("leadership_kind", "leadership_kind_record")]


@pytest.mark.parametrize("s_record", [
    {"leadership_kind": "disciplinary", "leadership_kind_record": BOTH},
    # an answer that named no kind differs from one that named one
    {"leadership_kind_record": {**ANSWERED, "option": "own_words", "answer": "eher projektbezogen"}},
], ids=["two-kinds", "kind-vs-own-words"])
def test_two_different_answers_are_asked_once_more(s_record):
    from applire.services.interview.scope_question import kind_entry_key
    from applire.services.profile.role_facts import entries_needing_leadership_kind

    before = _pair({"leadership_kind": "functional", "leadership_kind_record": ANSWERED}, s_record)
    result = apply_ops(before, [_merge()], "manual_edit")
    assert _state(result.profile) == [("w1", None, "merge_conflict")]
    record = result.profile.work_experience[0].leadership_kind_record
    assert record.merged_kinds == ["functional", s_record.get("leadership_kind")]
    listed = entries_needing_leadership_kind(result.profile)
    assert [e["entry_id"] for e in listed] == ["w1"]
    # A new key: the old "asked" mark of w1 does not suppress this ask …
    assert kind_entry_key(listed[0]) != kind_entry_key({"entry_id": "w1"})
    # … and once answered, the role is no longer listed (asked once).
    from applire.services.profile.leadership_kind import leadership_kind_op

    answered = apply_ops(result.profile, [leadership_kind_op("w1", "both", answer="Beides")], "interview")
    assert entries_needing_leadership_kind(answered.profile) == []
    assert _state(answered.profile) == [("w1", "disciplinary", "both")]


def test_the_carry_is_the_stored_value_not_a_forged_one():
    """The forged-write table (Q1) still holds through the merge door: a kind a
    model or an import installs in the SAME batch never rides on the merge."""
    before = _pair({}, {})
    forged_src = SetField(target="w2", field="leadership_kind", value="disciplinary")
    forged_tgt = SetField(target="w1", field="leadership_kind", value="disciplinary")
    for ops in ([forged_src, _merge()], [_merge(), forged_tgt], [forged_src, _merge(), forged_tgt]):
        result = apply_ops(before, ops, "interview")
        assert _state(result.profile) == [("w1", None, None)], ops
        assert not [c for c in result.changes if c.field in ("leadership_kind", "leadership_kind_record")]
    merged = before.model_dump(mode="json")
    merged["work_experience"][1]["leadership_kind"] = "disciplinary"
    merged["work_experience"][1]["leadership_kind_record"] = BOTH
    result = apply_ops(before, [ApplyImportMerge(merged=MasterProfileData.model_validate(merged)), _merge()],
                       "cv_upload")
    assert _state(result.profile) == [("w1", None, None)]


def test_a_merge_in_another_section_carries_nothing_and_the_answer_still_wins():
    from applire.services.profile.leadership_kind import leadership_kind_op

    before = _pair({}, {"leadership_kind": "disciplinary", "leadership_kind_record": BOTH})
    result = apply_ops(before, [_merge(), leadership_kind_op("w1", "functional", answer="Fachliche Anleitung")],
                       "interview")
    assert _state(result.profile) == [("w1", "functional", "functional")]


def test_the_section_door_still_cannot_write_the_kind_after_a_merge_conflict():
    before = _pair({"leadership_kind": "functional", "leadership_kind_record": ANSWERED},
                   {"leadership_kind": "disciplinary", "leadership_kind_record": BOTH})
    merged = apply_ops(before, [_merge()], "manual_edit").profile
    payload = merged.model_dump(mode="json")["work_experience"]
    payload[0]["leadership_kind"] = "disciplinary"
    payload[0]["leadership_kind_record"] = {"option": "both", "answer": "forged"}
    result = apply_ops(merged, [ReplaceSection(section="work_experience", value=payload)], "manual_edit")
    assert _state(result.profile) == [("w1", None, "merge_conflict")]


# ── seam: the interview turn writes kind AND size ────────────────────────────

async def _seed_role(db, *, team_size, responsibilities=()):
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis

    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex, raw_text="Leiter Operations. Ihr Profil: ca. 120 Mitarbeitende, MES.",
        role_title="Leiter Operations", required_skills=["MES"], nice_to_have_skills=[], keywords=[],
        seniority_level="Senior", company_culture_signals=[], language_requirement="Deutsch", jd_language="de",
    )
    await retire_live_profiles(db)
    profile = make_master_profile(profile_json={
        "personal_info": {"name": "Katrin Test", "email": "katrin@example.de"}, "skills": [],
        "work_experience": [{"id": "w1", "company": "Kanzlei Rhein", "role": "Controllerin",
                             "start_date": "2019-01", "team_size": team_size,
                             "responsibilities": list(responsibilities)}],
    })
    db.add_all([job, profile])
    await db.flush()
    db.add(GapAnalysis(
        job_analysis_id=job.id, profile_id=profile.id, match_score=0.5, critical_gaps=[], minor_gaps=[],
        strengths=[], keyword_gaps=[], category_a=[], category_b=["Führungsspanne ~120 MA"],
        category_c=["MES"], keyword_ledger=[_scope_row()], gap_clusters=[_scope_cluster()],
    ))
    await db.commit()
    return job


async def _kind_turn(db, *, team_size, responsibilities, answer):
    from applire.models.profile import MasterProfile
    from applire.models.session import InterviewSession
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session, send_message

    job = await _seed_role(db, team_size=team_size, responsibilities=responsibilities)
    provider = MagicMock()
    provider.acomplete = AsyncMock(side_effect=AssertionError("no LLM question call"))
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no LLM question call"))
    reconciled = _bridge(False)
    created = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, provider)
    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(False)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        first = await send_message(created.session_id, "Direkt 3.", db, provider)
    with (
        patch("applire.services.session.reconcile_interview_turn", new=reconciled),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        await send_message(created.session_id, answer, db, provider)
    row = await db.get(InterviewSession, created.session_id)
    prof = await db.get(MasterProfile, row.profile_id)
    await db.refresh(prof)
    return first, prof.profile_json["work_experience"][0], reconciled.await_count


@pytest.mark.asyncio
async def test_seam_a_corrected_chip_writes_kind_and_size(db):
    first, entry, reconciled = await _kind_turn(
        db, team_size=2, responsibilities=["Fachliche Anleitung von zwei Werkstudierenden."],
        answer="Fachliche Anleitung – 3 Personen")
    assert first.question.startswith("Du schreibst: »Fachliche Anleitung von zwei Werkstudierenden.« "
                                     "Im Profil stehen 2 Personen – stimmt das?")
    assert (entry["team_size"], entry["leadership_kind"]) == (3, "functional")
    assert entry["leadership_kind_record"]["team_size"] == 3
    assert reconciled == 0, "a chip answer needs no reconcile call"


@pytest.mark.asyncio
async def test_seam_an_unsized_role_is_asked_and_its_number_written(db):
    first, entry, _ = await _kind_turn(
        db, team_size=None, responsibilities=["Fachliche Anleitung von zwei Werkstudierenden."],
        answer="Fachliche Anleitung – Anzahl Personen: 3")
    assert "Wie viele Personen waren das" in first.question
    assert first.choices[1] == "Fachliche Anleitung – Anzahl Personen:"
    assert (entry["team_size"], entry["leadership_kind"]) == (3, "functional")


@pytest.mark.asyncio
async def test_seam_free_text_stores_no_kind_and_no_size(db):
    _first, entry, reconciled = await _kind_turn(
        db, team_size=None, responsibilities=["Fachliche Anleitung von zwei Werkstudierenden."],
        answer="ja, 3, fachlich")
    assert entry.get("team_size") is None and entry.get("leadership_kind") is None
    assert entry["leadership_kind_record"]["option"] == "own_words"
    assert reconciled == 1, "own words are reconciled like any answer"


@pytest.mark.asyncio
async def test_seam_a_role_without_leadership_text_or_size_is_not_asked(db):
    from applire.schemas.session import SessionCreateRequest
    from applire.services.session import create_session, send_message

    job = await _seed_role(db, team_size=None, responsibilities=["Monatsabschluss nach HGB."])
    provider = MagicMock()
    provider.acomplete = AsyncMock(side_effect=AssertionError("no LLM question call"))
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no LLM question call"))
    created = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, provider)
    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(False)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        first = await send_message(created.session_id, "Direkt 3.", db, provider)
    assert first.complete is True, "no kind ask: the role neither holds a size nor says it leads people"


@pytest.mark.parametrize("order", ["w3-into-w2-then-w2-into-w1", "w2-into-w1-then-w3-into-w1"])
def test_a_merge_chain_keeps_the_conflict(order):
    """F6 (adv-2b, MD-Q17): w2 answered functional, w3 disciplinary; two merges
    in one batch end on w1 — the conflict survives the chain (never a silent
    "functional"), and the person is asked once more."""
    p = MasterProfileData.model_validate({"work_experience": [
        {"id": "w1", "company": "Weberit", "role": "Leiter"},
        {"id": "w2", "company": "Weberit", "role": "Leiter",
         "leadership_kind": "functional", "leadership_kind_record": ANSWERED},
        {"id": "w3", "company": "Weberit", "role": "Leiter",
         "leadership_kind": "disciplinary", "leadership_kind_record": BOTH},
    ]})
    if order.startswith("w3"):
        ops = [MergeEntries(section="work_experience", target_id="w2", source_id="w3"),
               MergeEntries(section="work_experience", target_id="w1", source_id="w2")]
    else:
        ops = [MergeEntries(section="work_experience", target_id="w1", source_id="w2"),
               MergeEntries(section="work_experience", target_id="w1", source_id="w3")]
    res = apply_ops(p, ops, "manual_edit")
    assert _state(res.profile) == [("w1", None, "merge_conflict")]
    assert sorted(res.profile.work_experience[0].leadership_kind_record.merged_kinds) == [
        "disciplinary", "functional"]



@pytest.mark.parametrize("answer", ["Beides14", "Fachliche Anleitung – ٣ Personen", "Fachliche Anleitung – １４ Personen"])
def test_a_glued_or_non_ascii_number_is_own_words(answer):
    """Suspected S2 (adv-2b): one ASCII whole number after a separator, nothing else."""
    from applire.services.profile.leadership_kind import parse_kind_answer

    assert parse_kind_answer(answer) == ("own_words", None)
