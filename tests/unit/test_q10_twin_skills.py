# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q10 — ruling Q10-8 (founder 2026-11-17): twin skills on the CV.

When the person ticked a term and one of "their words" the card showed beside it
is EXACTLY a profile skill name, the CV for THAT posting prints the ticked term
and leaves that one skill out. The profile keeps both; other postings' CVs print
the held skill as before. Bound structurally: the confirmation records the skill
names the card showed (``shown_skills``) at answer time; composition reads that
record through the ledger stamp.

Captured case (delivery-run Q10, katall): "Produktionscontrolling" ticked with
"Werkscontrolling" shown → both printed as near-twin chips.
"""
from __future__ import annotations

import copy
import importlib.util
import logging
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from applire.schemas.cv import TailoredCVData
from applire.schemas.profile import Skill, SkillConfirmation
from applire.services import cv as cv_mod
from applire.services import keyword_ledger as kl
from applire.services import posting_terms as pt
from applire.services.cv_v2 import V2CheckContext, V2Terminal, _skill_findings
from applire.services.finding_registry import REGISTRY, note_kinds, resolve
from applire.services.posting_term_answer import add_confirmed_skills

JOB = "22222222-2222-2222-2222-222222222222"
OTHER_JOB = "33333333-3333-3333-3333-333333333333"
WID = "11111111-1111-1111-1111-111111111111"
QUOTE = "Werkscontrolling für zwei Produktionsstandorte: Herstellkosten, Deckungsbeiträge."
TODAY = date(2026, 11, 17)

VAULT = {
    "personal_info": {"name": "Katrin Weber"},
    "professional_summary": {"de": "Controllerin mit Werkscontrolling.", "en": ""},
    "work_experience": [{
        "id": WID, "company": "Schwarzwald Präzision GmbH", "role": "Controllerin",
        "start_date": "2019-01", "end_date": None,
        "responsibilities": [QUOTE, "Investitionsrechnungen für Maschinenbeschaffungen."],
        "achievements": [],
    }],
    "skills": [
        {"name": "Werkscontrolling", "status": "confirmed"},
        {"name": "Investitionsrechnung", "status": "confirmed"},
        {"name": "Excel", "status": "confirmed"},
    ],
}
PROD = {"concept": "Produktionscontrolling", "surface_forms": ["Werkscontrolling", "Produktionscontrolling"],
        "status": "direct", "claimable": True, "sources": ["required"], "evidence": "Werkscontrolling belegt.",
        "backing": {"quote": QUOTE, "unit_path": "work_experience[0].responsibilities[0]"}}
INV = {"concept": "Investitionsentscheidungen", "surface_forms": ["Investitionsrechnung"],
       "status": "direct", "claimable": True, "sources": ["required"], "evidence": "belegt."}
EXCEL = {"concept": "Excel", "surface_forms": ["Excel"], "status": "direct", "claimable": True,
         "sources": ["keyword"], "evidence": "Excel."}


def _ticked(terms=("Produktionscontrolling",), *, vault=VAULT, job=JOB, ledger=(PROD, INV)):
    """The profile after the person ticked *terms* on a card built from *ledger*."""
    prof = copy.deepcopy(vault)
    card = {r["concept"]: pt.their_words(r, vault) for r in ledger}
    prof["skills"], _ = add_confirmed_skills(
        prof["skills"], list(terms), via="person", posting="Lieferkette GmbH", job_id=job,
        today=TODAY, shown=card,
    )
    return prof


def _conf(prof, term):
    return next(c for s in prof["skills"] for c in s.get("confirmations") or [] if c["name"] == term)


# ── the record at answer time ────────────────────────────────────────────────

def test_the_confirmation_records_the_skill_names_the_card_showed_exactly():
    prof = _ticked(("Produktionscontrolling", "Investitionsentscheidungen"))
    assert _conf(prof, "Produktionscontrolling")["shown_skills"] == ["Werkscontrolling"]
    assert _conf(prof, "Investitionsentscheidungen")["shown_skills"] == ["Investitionsrechnung"]


def test_a_card_word_that_is_only_part_of_a_skill_name_or_a_quote_records_nothing():
    vault = copy.deepcopy(VAULT)
    vault["skills"][0]["name"] = "Werkscontrolling & Reporting"  # "Werkscontrolling" is a part only
    prof = _ticked(vault=vault)
    assert "shown_skills" not in _conf(prof, "Produktionscontrolling")


def test_the_stored_spelling_is_recorded_and_the_holder_skill_never():
    skills = copy.deepcopy(VAULT["skills"])
    skills[0]["name"] = "werkscontrolling"
    skills.append({"name": "Produktionscontrolling", "status": "unconfirmed"})
    out, _ = add_confirmed_skills(
        skills, ["Produktionscontrolling"], via="person", posting="L", job_id=JOB, today=TODAY,
        shown={"Produktionscontrolling": ["Werkscontrolling", "Produktionscontrolling", QUOTE]},
    )
    # the term is already a skill name: the confirmation lands there, and that
    # skill is never a stand-in for itself; the other is recorded as stored
    assert _conf({"skills": out}, "Produktionscontrolling")["shown_skills"] == ["werkscontrolling"]


def test_the_schema_keeps_the_record_and_omits_it_while_empty():
    c = SkillConfirmation.model_validate({"name": "T", "via": "person", "on": "2026-11-17",
                                          "job_id": JOB, "shown_skills": ["Werkscontrolling", 3, " "]})
    assert c.shown_skills == ["Werkscontrolling"]
    assert c.model_dump(mode="json")["shown_skills"] == ["Werkscontrolling"]
    bare = SkillConfirmation.model_validate({"name": "T", "via": "person", "on": "2026-11-17"})
    assert "shown_skills" not in bare.model_dump(mode="json")
    skill = Skill.model_validate({"name": "T", "confirmations": [c.model_dump(mode="json")]})
    assert skill.model_dump(mode="json")["confirmations"][0]["shown_skills"] == ["Werkscontrolling"]


# ── the pairs for THIS posting ───────────────────────────────────────────────

def test_the_pair_belongs_to_this_posting_only():
    prof = _ticked()
    pairs, _ = pt.twin_pairs_for_posting(prof, JOB, [PROD, INV])
    assert [(p["skill"], p["term"]) for p in pairs] == [("Werkscontrolling", "Produktionscontrolling")]
    assert pt.twin_pairs_for_posting(prof, OTHER_JOB, [PROD, INV])[0] == []
    assert pt.twin_pairs_for_posting(prof, None, [PROD])[0] == []


def test_a_skill_the_posting_names_itself_is_kept():
    prof = _ticked()
    pairs, kept = pt.twin_pairs_for_posting(prof, JOB, [PROD, {**EXCEL, "concept": "Werkscontrolling"}])
    assert pairs == [] and kept["posting_names"] == 1
    pairs, kept = pt.twin_pairs_for_posting(prof, JOB, [PROD], job_dict={"keywords": ["werkscontrolling"]})
    assert pairs == [] and kept["posting_names"] == 1


def test_a_pinned_skill_is_kept():
    from applire.schemas.application import FactPin

    prof = _ticked()
    pin = FactPin(entry_type="skill", entry_id="s1", quote="Werkscontrolling", targets=["cv"])
    pairs, kept = pt.twin_pairs_for_posting(prof, JOB, [PROD], pins=[pin])
    assert pairs == [] and kept["pinned"] == 1
    # a pin for the letter only does not reach the CV
    letter = FactPin(entry_type="skill", entry_id="s1", quote="Werkscontrolling", targets=["letter"])
    assert len(pt.twin_pairs_for_posting(prof, JOB, [PROD], pins=[letter])[0]) == 1


def test_a_skill_that_is_itself_ticked_for_this_posting_is_never_left_out():
    # T2's card showed T1, which the person ticked for the same posting earlier
    prof = _ticked(("Produktionscontrolling",))
    row = {"concept": "Werkscontrolling-Reporting", "surface_forms": ["Produktionscontrolling"],
           "status": "direct", "claimable": True, "sources": ["required"], "evidence": "x"}
    later = copy.deepcopy(prof)
    later["skills"], _ = add_confirmed_skills(
        later["skills"], ["Werkscontrolling-Reporting"], via="person", posting="L", job_id=JOB,
        today=TODAY, shown={"Werkscontrolling-Reporting": pt.their_words(row, prof)},
    )
    assert _conf(later, "Werkscontrolling-Reporting")["shown_skills"] == ["Produktionscontrolling"]
    pairs, kept = pt.twin_pairs_for_posting(later, JOB, [PROD, row])
    assert ("Produktionscontrolling", "Werkscontrolling-Reporting") not in {(p["skill"], p["term"]) for p in pairs}
    assert kept["ticked"] == 1
    # no chain: every ticked term stays printable
    assert {p["skill"] for p in pairs} == {"Werkscontrolling"}


def test_a_skill_shown_for_two_ticked_terms_is_left_out_once():
    prof = _ticked(("Produktionscontrolling",))
    for s in prof["skills"]:
        if s["name"] == "Investitionsrechnung":
            s["name"] = "Investitionsrechnung (Werk)"
    second = {"concept": "Werkscontrolling (Kosten)", "surface_forms": ["Werkscontrolling"], "status": "direct",
              "claimable": True, "sources": ["required"], "evidence": "x"}
    prof["skills"], _ = add_confirmed_skills(
        prof["skills"], ["Werkscontrolling (Kosten)"], via="person", posting="L", job_id=JOB, today=TODAY,
        shown={"Werkscontrolling (Kosten)": ["Werkscontrolling"]},
    )
    pairs, _ = pt.twin_pairs_for_posting(prof, JOB, [PROD, second])
    assert [p["skill"] for p in pairs] == ["Werkscontrolling"]


def test_a_skill_no_longer_held_is_kept_out_of_the_rule():
    prof = _ticked()
    prof["skills"] = [s for s in prof["skills"] if s["name"] != "Werkscontrolling"]
    pairs, kept = pt.twin_pairs_for_posting(prof, JOB, [PROD])
    assert pairs == [] and kept["gone"] == 1
    denied = _ticked()
    next(s for s in denied["skills"] if s["name"] == "Werkscontrolling")["status"] = "denied"
    assert pt.twin_pairs_for_posting(denied, JOB, [PROD])[0] == []


# ── the stamp: one record every reader reads ─────────────────────────────────

def test_the_stamp_rides_on_the_terms_row_and_never_mutates_the_input(caplog):
    prof = _ticked()
    ledger = [copy.deepcopy(PROD), copy.deepcopy(INV)]
    before = copy.deepcopy(ledger)
    with caplog.at_level(logging.INFO, logger="applire.services.posting_terms"):
        out = pt.stamp_twin_skills(ledger, prof, JOB)
    assert ledger == before
    assert out[0][pt.IN_PLACE_OF_KEY] == [{"skill": "Werkscontrolling", "term": "Produktionscontrolling",
                                           "holder": "Produktionscontrolling"}]
    assert pt.IN_PLACE_OF_KEY not in out[1]
    line = next(r.getMessage() for r in caplog.records if "twin skills" in r.getMessage())
    assert "left_out=1" in line and "unplaced=0" in line and JOB in line
    assert "Werkscontrolling" not in line and "Produktionscontrolling" not in line  # counts only


def test_a_stale_stamp_is_dropped_and_a_term_without_a_row_is_counted_unplaced(caplog):
    stale = [{**copy.deepcopy(PROD), pt.IN_PLACE_OF_KEY: [{"skill": "Excel", "term": "X"}]}]
    assert pt.twin_pairs(pt.stamp_twin_skills(stale, VAULT, JOB)) == []
    prof = _ticked()
    with caplog.at_level(logging.INFO, logger="applire.services.posting_terms"):
        out = pt.stamp_twin_skills([copy.deepcopy(INV)], prof, JOB)
    assert pt.twin_pairs(out) == []
    assert any("unplaced=1" in r.getMessage() for r in caplog.records)


def test_the_stamp_finds_the_terms_row_by_a_surface_form_when_the_concept_moved():
    prof = _ticked()
    moved = {**copy.deepcopy(PROD), "concept": "Controlling der Produktion"}
    out = pt.stamp_twin_skills([moved], prof, JOB)
    assert [p["skill"] for p in pt.twin_pairs(out)] == ["Werkscontrolling"]


# ── the settle: the term printed, the skill left out, never neither ──────────

PAIR = [{"skill": "Werkscontrolling", "term": "Produktionscontrolling", "holder": "Produktionscontrolling"}]


def test_the_skill_is_dropped_when_its_term_is_on_the_list():
    out, applied = pt.settle_twin_skills(["Produktionscontrolling", "werkscontrolling ", "Excel"], PAIR)
    assert out == ["Produktionscontrolling", "Excel"]
    assert applied == [("Werkscontrolling", "Produktionscontrolling", "dropped")]


def test_the_term_takes_the_skills_slot_when_it_is_not_on_the_list():
    out, applied = pt.settle_twin_skills(["Excel", "Werkscontrolling", "SAP CO/FI", "Werkscontrolling"], PAIR)
    assert out == ["Excel", "Produktionscontrolling", "SAP CO/FI"]
    assert applied == [("Werkscontrolling", "Produktionscontrolling", "replaced")]


def test_the_skill_carrying_the_term_counts_as_the_term_on_the_list():
    pair = [{"skill": "Mentoring", "term": "Technical mentoring", "holder": "Engineering mentoring"}]
    out, _ = pt.settle_twin_skills(["Engineering mentoring", "Mentoring"], pair)
    assert out == ["Engineering mentoring"]


def test_a_list_without_the_skill_is_untouched():
    out, applied = pt.settle_twin_skills(["Excel", "Werks- und Produktionscontrolling"], PAIR)
    assert out == ["Excel", "Werks- und Produktionscontrolling"] and applied == []


# ── the chain agrees: compose, checks, restore, writer, reviewer ─────────────

def _stamped():
    return pt.stamp_twin_skills([copy.deepcopy(PROD), copy.deepcopy(INV), copy.deepcopy(EXCEL)], _ticked(), JOB)


def _cv(skills, bullets=(QUOTE,)):
    return TailoredCVData.model_validate({
        "contact": {"name": "Katrin Weber", "email": "k@example.com"},
        "summary": "Controllerin.",
        "work_history": [{"id": WID, "company": "Schwarzwald Präzision GmbH", "role": "Controllerin",
                          "start_date": "2019-01", "end_date": None, "bullets": list(bullets)}],
        "skills": list(skills),
    })


def _ctx(ledger, vault=None):
    job = {"required_skills": ["Produktionscontrolling"], "nice_to_have_skills": [], "keywords": []}
    return V2CheckContext(profile_json=vault or _ticked(), keyword_ledger=ledger, job_dict=job, language="de")


def test_the_v2_compose_settles_every_draft_and_notes_it():
    term = V2Terminal(ctx=_ctx(_stamped()))
    prose = {"summary": "Controllerin.", "work": [{"id": WID, "bullets": [QUOTE]}],
             "skills": ["Produktionscontrolling", "Werkscontrolling", "Excel"]}
    composed = term.compose(prose, _ticked(), _ticked())
    assert composed.skills == ["Produktionscontrolling", "Excel"]
    notes = next(iter(term.notes_by_draft.values()))
    assert notes.twins_settled == [("Werkscontrolling", "Produktionscontrolling", "dropped")]
    # without the stamp the compose is unchanged
    plain = V2Terminal(ctx=_ctx([copy.deepcopy(PROD)]))
    assert "Werkscontrolling" in plain.compose(prose, _ticked(), _ticked()).skills


def test_the_narrative_restore_never_puts_the_skill_back_from_a_bullet():
    cv = _cv(["Produktionscontrolling"])
    unstamped = cv_mod._restore_narrative_named_skills(cv, _ticked(), [copy.deepcopy(PROD)], document_language="de")
    stamped = cv_mod._restore_narrative_named_skills(cv, _ticked(), _stamped(), document_language="de")
    assert "Werkscontrolling" not in stamped.skills
    # the same bullet without the ruling's record: the restore adds it, as always
    assert "Werkscontrolling" in unstamped.skills


def test_the_cap_pass_never_guarantees_the_skill_back():
    job = {"required_skills": ["Produktionscontrolling"], "nice_to_have_skills": [], "keywords": []}
    unstamped = [copy.deepcopy(PROD), copy.deepcopy(INV), copy.deepcopy(EXCEL)]
    before = cv_mod._tailor_skills_to_jd(_cv(["Produktionscontrolling"]), _ticked(), job, unstamped)
    assert "Werkscontrolling" in before.skills  # the #192 guarantee re-adds a required-tier vault skill
    after = cv_mod._tailor_skills_to_jd(_cv(["Produktionscontrolling"]), _ticked(), job, _stamped())
    assert "Werkscontrolling" not in after.skills


def test_the_corrector_is_never_asked_to_add_the_skill_from_a_bullet():
    row = {**copy.deepcopy(PROD), "sources": ["keyword"]}  # below the guarantee tier
    job = {"required_skills": [], "nice_to_have_skills": [], "keywords": []}

    def named(ledger):
        ctx = V2CheckContext(profile_json=_ticked(), keyword_ledger=ledger, job_dict=job, language="de")
        found = {f.check: f for f in _skill_findings(_cv(["Excel"]), ctx, cv_mod)}
        return found["cv.skill_named_in_bullet"].subjects if "cv.skill_named_in_bullet" in found else ()

    assert named([row]) == ("Werkscontrolling",)  # the advisory as it always fired
    assert named(pt.stamp_twin_skills([row], _ticked(), JOB)) == ()


def test_the_v1_tail_settles_last():
    out = cv_mod._settle_twin_skills(_cv(["Werkscontrolling", "Excel"]), _stamped())
    assert out.skills == ["Produktionscontrolling", "Excel"]
    assert cv_mod._settle_twin_skills(_cv(["Werkscontrolling"]), [copy.deepcopy(PROD)]).skills == ["Werkscontrolling"]


def test_the_writer_lists_the_term_and_the_reviewer_never_asks_the_skill_back():
    writer = kl.render_ledger_prompt_block(_stamped())
    line = next(ln for ln in writer.splitlines() if ln.startswith("  - Produktionscontrolling"))
    assert '"Produktionscontrolling" in place of their skill "Werkscontrolling"' in line
    assert "never that skill" in line
    reviewer = kl.render_ledger_reviewer_block(_stamped())
    line = next(ln for ln in reviewer.splitlines() if ln.startswith("  - Produktionscontrolling"))
    assert "never ask to add it" in line and '"Werkscontrolling"' in line
    # an unstamped row reads as before
    plain = kl.render_ledger_reviewer_block([copy.deepcopy(PROD)])
    assert "in place of" not in plain


# ── GARH: the drop is recorded, never silent ─────────────────────────────────

def test_the_delivered_report_records_the_left_out_skill_as_a_silent_note():
    term = V2Terminal(ctx=_ctx(_stamped()))
    note = term.twin_note({"skills": ["Produktionscontrolling", "Excel"]})
    assert note.kind == "note" and note.key == "cv.skill_twin_left_out"
    assert note.subjects == ("Werkscontrolling",) and "Produktionscontrolling" in note.text
    kinds = note_kinds({"kind": note.kind, "key": note.key})
    assert resolve(kinds)[1].cls == "S"
    assert REGISTRY["terminal:note:cv.skill_twin_left_out"].cls == "S"
    # nothing to record: no stamp, or the term is not on the delivered list
    assert V2Terminal(ctx=_ctx([copy.deepcopy(PROD)])).twin_note({"skills": ["Excel"]}) is None
    assert term.twin_note({"skills": ["Excel"]}) is None


def test_the_note_rides_the_terminal_review_row_without_changing_its_state():
    from applire.services.terminal_review_outcome import TerminalReviewOutcome, _state_key

    term = V2Terminal(ctx=_ctx(_stamped()))
    clean = TerminalReviewOutcome(chain_id="cv_terminal_review", path="approved", approved=True,
                                  blocking_issues=(), minor_issues=(), rounds=1)
    prose = {"summary": "Controllerin.", "work": [{"id": WID, "bullets": [QUOTE]}],
             "skills": ["Produktionscontrolling", "Excel"]}
    with patch.object(V2Terminal, "delivered_findings", return_value=[]):
        out = term.report_delivered(clean, prose, {"skills": ["Produktionscontrolling", "Excel"]})
    assert [n.key for n in out.notes] == ["cv.skill_twin_left_out"]
    assert _state_key(out) == _state_key(clean)


# ── the CV seam: the stamp reaches the reviewer and the delivered list ───────

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location("_q10_seam593", _HERE / "test_593_prompt_facing_profile_view.py")
_seam593 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seam593)


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v2", "v1"])
async def test_SEAM_cv_generation_leaves_the_skill_out_and_tells_the_reviewer(monkeypatch, pipeline):
    monkeypatch.setenv("CV_PIPELINE", pipeline)
    m = _seam593
    cv_id, job_id, profile_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    profile_json = m._profile_json()
    profile_json["skills"] = [
        {"name": "Monitoring", "category": "technical", "status": "confirmed"},
        {"name": "Observability", "category": "technical", "status": "confirmed",
         "confirmations": [{"name": "Observability", "via": "person", "on": "2026-11-17",
                            "posting": "Vector Analytics", "job_id": str(job_id),
                            "shown_skills": ["Monitoring"]}]},
    ]
    ledger = [{"concept": "Observability", "surface_forms": ["Monitoring", "Observability"],
               "claimable": True, "status": "direct", "sources": ["required"], "fit_weight": 1.0,
               "evidence": "Monitoring."}]
    raw = m._tailored_raw()
    raw["skills"] = ["Monitoring", "Observability"]
    mock_cv = MagicMock(); mock_cv.status = "pending"; mock_cv.target_pages = 2
    mock_cv.document_language = "en"
    mock_profile = MagicMock(); mock_profile.profile_json = profile_json
    mock_gap = MagicMock()
    mock_gap.keyword_gaps = []; mock_gap.critical_gaps = []; mock_gap.keyword_ledger = ledger
    mock_db = AsyncMock()
    mock_db.get.side_effect = lambda model, id_: {cv_id: mock_cv, job_id: m._job_mock(), profile_id: mock_profile}[id_]
    mock_result = MagicMock(); mock_result.scalar_one_or_none.return_value = mock_gap
    mock_db.execute.side_effect = m._execute_by_entity(mock_cv, mock_result, job_id)
    reviewer_prompts: list[str] = []

    async def fake_loop(**kw):
        if kw.get("chain_id") == "cv_terminal_review":
            reviewer_prompts.append(kw["reviewer_prompt_fn"](kw["source"], kw["draft"]))
        return kw["draft"]

    with patch("applire.services.cv.AsyncSessionLocal") as sl, \
         patch("applire.services.cv.get_provider", return_value=AsyncMock()), \
         patch("applire.services.cv._tailor_cv_with_fallback", new=AsyncMock(return_value=raw)), \
         patch("applire.services.keyword_ledger.refresh_persist_and_rescore", new=AsyncMock(return_value=ledger)), \
         patch("applire.services.cv.review_and_refine", new=AsyncMock(side_effect=fake_loop)), \
         patch("applire.services.cv._review_cv_language", new=AsyncMock(side_effect=lambda d, *a, **k: d)), \
         patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")), \
         patch("applire.services.cv_section_editor.build_content_snapshot", return_value={}):
        sl.return_value.__aenter__.return_value = mock_db
        from applire.services.cv import _render_cv_background

        await _render_cv_background(cv_id, job_id, profile_id, "classic_german")

    assert reviewer_prompts, "the terminal reviewer prompt was never built"
    assert '"Observability" in place of their skill "Monitoring"' in reviewer_prompts[0]
    skills = (mock_cv.tailored_data or {}).get("skills") or []
    assert "Observability" in skills and "Monitoring" not in skills
    # the profile keeps both
    assert {s["name"] for s in profile_json["skills"]} == {"Monitoring", "Observability"}


# ── both doors record what the list showed (web card and agent, Q10-6) ───────

_spec_a = importlib.util.spec_from_file_location("_q10_answer", _HERE / "test_q10_posting_term_answer.py")
_answer = importlib.util.module_from_spec(_spec_a)
_spec_a.loader.exec_module(_answer)
db = _answer.db  # the fixture, re-exported for this module


@pytest.mark.asyncio
async def test_SEAM_the_card_answer_records_the_skill_its_list_showed(db):
    from applire.services.posting_term_answer import answer_posting_terms
    from tests.support.owners import HARNESS_USER_ID

    job = await _answer._seed(db)
    await answer_posting_terms(db, job.id, HARNESS_USER_ID, confirmed=["Observability"], via="person")
    (conf,) = _answer._skill(await _answer._profile(db), "Observability")["confirmations"]
    # the card showed "Prometheus" (a held surface form that IS a skill name) and the quote
    assert conf["shown_skills"] == ["Prometheus"] and conf["job_id"] == str(job.id)


@pytest.mark.asyncio
async def test_SEAM_the_agent_answer_records_the_same(db):
    job = await _answer._seed(db)
    await _answer._call("confirm_posting_terms", {"job_id": str(job.id), "confirmed": ["Observability"]}, db)
    (conf,) = _answer._skill(await _answer._profile(db), "Observability")["confirmations"]
    assert conf["via"] == "agent" and conf["shown_skills"] == ["Prometheus"]


@pytest.mark.asyncio
async def test_SEAM_a_term_left_open_or_unticked_records_nothing(db):
    from applire.services.posting_term_answer import answer_posting_terms
    from tests.support.owners import HARNESS_USER_ID

    job = await _answer._seed(db)
    await answer_posting_terms(db, job.id, HARNESS_USER_ID, confirmed=[], via="person")
    prof = await _answer._profile(db)
    assert not any(s.get("confirmations") for s in prof["skills"])
    assert pt.twin_pairs_for_posting(prof, str(job.id), [])[0] == []


@pytest.mark.asyncio
async def test_SEAM_the_agent_is_told_before_confirming_that_a_term_stands_in_for_a_shown_skill(db):
    # ruling Q10-3 = C: the envelope says what the card's footnote says
    job = await _answer._seed(db)
    with patch("applire.services.cv.generate_cv", AsyncMock(side_effect=AssertionError("generated"))):
        out = await _answer._call("generate_cv", {"job_id": str(job.id)}, db)
    assert out["status"] == "needs_confirmation"
    assert out["ask_the_person"].endswith(
        "If one of their skills is shown beside a term (in their_words), the CV for this job "
        "names the term in its place."
    )
