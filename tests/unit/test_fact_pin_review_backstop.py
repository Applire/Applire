# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""#580 / ADR-077 amended 2026-08-26 — the CV chain's reviewer-side pin surface.

The fixture under ``tests/files/fact_pin_review_loop/`` is cut from the captured
2026-08-25 dev-stack run (synthetic Anna-Bauer profile): ``writer_draft`` is the
writer output that carried the long pin WORD-FOR-WORD, ``corrected_draft`` is the
corrector's round-1 output after the reviewer's check 6(b) had flagged the
pinned bullet ("microservices" is on the Keyword Ledger's DO-NOT-CLAIM list).
Realism of inputs beats assertion strength: the block is tested against the
drafts the loop really produced, not against hand-made ones.
"""
import json
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.application import FactPin  # noqa: E402
from applire.schemas.cv import TailoredCVData  # noqa: E402
from applire.services.pin_reach import (  # noqa: E402
    CV_UNRENDERABLE_PIN_TYPES,
    DEMANDABLE_PIN_TYPES,
    PINNED_FACT_SIGNAL_ID,
    measure_pins_in_draft,
    pin_ledger_conflicts,
    pinned_facts_reviewer_prompt_fn,
    render_pinned_facts_check_block,
)

FIXTURE = Path(__file__).parent.parent / "files" / "fact_pin_review_loop" / "fixture.json"
LONG_QUOTE_HEAD = "Architected and led the migration of a monolithic Django application"
WORK_ID = "3fe1ebf1-7042-418b-b996-8bc6dcff2779"


@pytest.fixture(scope="module")
def fx() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _pins(fx) -> list[FactPin]:
    return [FactPin.model_validate(p) for p in fx["pins"]]


def _base(source: str, draft: dict) -> str:
    return "BASE"


# ── the fixture's own claim ───────────────────────────────────────────────────


def test_fixture_writer_draft_carries_the_pin_and_the_corrected_draft_lost_it(fx):
    """Test the fixture too: the file claims a property, prove it with the
    real predicate (feedback_mutation_test_the_guard)."""
    pins = _pins(fx)
    writer = measure_pins_in_draft(pins, fx["writer_draft"], fx["profile"])
    corrected = measure_pins_in_draft(pins, fx["corrected_draft"], fx["profile"])
    long_pin, skill_pin = pins
    assert writer[long_pin.pin_id] is True and writer[skill_pin.pin_id] is True
    assert corrected[long_pin.pin_id] is False and corrected[skill_pin.pin_id] is True


# ── the deterministic check block ─────────────────────────────────────────────


def test_block_lists_the_absent_pin_as_a_demand_with_its_entry_id(fx):
    fn = pinned_facts_reviewer_prompt_fn(_base, _pins(fx), fx["profile"], keyword_ledger=[])
    prompt = fn("src", fx["corrected_draft"])
    assert prompt.startswith("BASE")
    assert "PINNED FACTS CHECK" in prompt
    block = prompt.split("PINNED FACTS CHECK", 1)[1]
    present_part, demand_part = block.split("DEMAND —", 1)
    # the skill pin IS present in the corrected draft — stated as PRESENT, never demanded
    assert "PRESENT" in present_part and "Public Speaking" in present_part
    assert WORK_ID in demand_part and LONG_QUOTE_HEAD in demand_part
    assert "Public Speaking" not in demand_part


def test_present_pins_are_stated_as_present_so_nothing_is_re_derived(fx):
    """The 2026-08-26 replay: the reviewer raised "pinned skill missing" for a pin
    the scan had found present, because the block only listed absent ones. The
    block is a complete statement — a prohibition is not an answer."""
    fn = pinned_facts_reviewer_prompt_fn(_base, _pins(fx), fx["profile"], keyword_ledger=[])
    prompt = fn("src", fx["writer_draft"])
    block = prompt.split("PINNED FACTS CHECK", 1)[1]
    assert "PRESENT" in block and "Public Speaking" in block and LONG_QUOTE_HEAD in block
    assert "DEMAND —" not in block


def test_a_pin_is_demanded_at_most_once_per_loop(fx):
    """The bound that replaces verdict memory (ADR-077 amendment 1): a demand a
    truth check overrides must not re-enter, and pins can never supply more than
    one corrector round each."""
    fn = pinned_facts_reviewer_prompt_fn(_base, _pins(fx), fx["profile"], keyword_ledger=[])
    first = fn("src", fx["corrected_draft"])
    second = fn("src", fx["corrected_draft"])
    assert "DEMAND —" in first
    assert "DEMAND —" not in second
    # the second round SAYS why the pin is not demanded again
    assert "ALREADY DEMANDED" in second and LONG_QUOTE_HEAD in second


def test_two_wrappers_do_not_share_the_bound(fx):
    """The prose loop and the terminal loop are separate invocations with
    separate bounds (at most one demand per pin per LOOP, two per generation)."""
    a = pinned_facts_reviewer_prompt_fn(_base, _pins(fx), fx["profile"], keyword_ledger=[])
    b = pinned_facts_reviewer_prompt_fn(_base, _pins(fx), fx["profile"], keyword_ledger=[])
    assert "DEMAND —" in a("src", fx["corrected_draft"])
    assert "DEMAND —" in b("src", fx["corrected_draft"])


def test_a_ledger_conflicted_pin_is_listed_but_never_demanded(fx):
    """The captured case: the quote carries 'microservices', a DO-NOT-CLAIM
    concept. Truth outranks the pin — the block says so and no demand can
    contradict check 6(b) inside one round."""
    fn = pinned_facts_reviewer_prompt_fn(
        _base, _pins(fx), fx["profile"], keyword_ledger=fx["keyword_ledger"]
    )
    prompt = fn("src", fx["corrected_draft"])
    block = prompt.split("PINNED FACTS CHECK", 1)[1]
    assert "LEDGER CONFLICT" in block
    assert "microservices" in block
    assert "DEMAND —" not in block
    # listed every round (informational, never a demand) — no bound applies
    assert "LEDGER CONFLICT" in fn("src", fx["corrected_draft"])


def test_ledger_conflict_is_a_token_bounded_string_fact(fx):
    ledger = fx["keyword_ledger"]
    assert pin_ledger_conflicts(fx["pins"][0]["quote"], ledger) == ["microservices"]
    # `surface_present` would have matched these; `_term_present` must not
    assert pin_ledger_conflicts("Designed RESTful services for the shop", ledger) == []
    assert pin_ledger_conflicts("Fragile legacy code retired", ledger) == []
    assert pin_ledger_conflicts("Built and maintained REST APIs in FastAPI", ledger) == ["REST APIs"]
    # claimable concepts never conflict
    assert pin_ledger_conflicts("Python and FastAPI expert", ledger) == []
    assert pin_ledger_conflicts("anything", None) == []


def test_non_authored_pin_types_are_never_demanded(fx):
    """Certifications/education/languages are joined by code after the writer;
    publications and volunteering have no CV section (ADR-077 amendment 1).
    A demand the corrector cannot satisfy is a control that fires falsely."""
    assert DEMANDABLE_PIN_TYPES == frozenset({"work", "skill", "signature_story"})
    assert CV_UNRENDERABLE_PIN_TYPES == frozenset({"volunteer", "publication"})
    cert = FactPin(
        pin_id="cert-1", entry_type="certification", entry_id="c-1",
        quote="AWS Solutions Architect", targets=["cv"],
    )
    fn = pinned_facts_reviewer_prompt_fn(_base, [cert], fx["profile"], keyword_ledger=[])
    block = fn("src", fx["corrected_draft"]).split("PINNED FACTS CHECK", 1)[1]
    assert "REPORT ONLY" in block and "AWS Solutions Architect" in block
    assert "DEMAND —" not in block


def test_stale_and_letter_only_pins_are_ignored(fx):
    long_pin = _pins(fx)[0]
    stale = long_pin.model_copy(update={"stale": True})
    letter_only = long_pin.model_copy(update={"targets": ["letter"], "pin_id": "l-1"})
    fn = pinned_facts_reviewer_prompt_fn(
        _base, [stale, letter_only], fx["profile"], keyword_ledger=[]
    )
    assert fn("src", fx["corrected_draft"]) == "BASE"


def test_an_unassemblable_draft_is_fail_safe(fx):
    """Measurement failure must never become a demand (or a crash): a draft the
    assembler refuses (unknown work id) yields no block."""
    broken = {"summary": "x", "work": [{"id": "not-a-vault-id", "bullets": ["y"]}], "skills": []}
    assert measure_pins_in_draft(_pins(fx), broken, fx["profile"]) is None
    fn = pinned_facts_reviewer_prompt_fn(_base, _pins(fx), fx["profile"], keyword_ledger=[])
    assert fn("src", broken) == "BASE"


def test_terminal_round_measures_the_composed_subject(fx):
    """`_terminal_review` hands the wrapper the COMPOSED document (already
    TailoredCVData-shaped), not the prose draft."""
    from applire.services.cv import assemble_tailored_cv

    composed_absent = assemble_tailored_cv(fx["corrected_draft"], fx["profile"])
    composed_present = assemble_tailored_cv(fx["writer_draft"], fx["profile"])
    fn = pinned_facts_reviewer_prompt_fn(
        _base, _pins(fx), fx["profile"], keyword_ledger=[], composed=True
    )
    assert "DEMAND —" in fn("src", composed_absent)
    fn2 = pinned_facts_reviewer_prompt_fn(
        _base, _pins(fx), fx["profile"], keyword_ledger=[], composed=True
    )
    assert "DEMAND —" not in fn2("src", composed_present)


def test_block_wording_names_the_check_and_forbids_re_derivation(fx):
    pin = _pins(fx)[0]
    block = render_pinned_facts_check_block(
        demand=[(pin, "work: Senior Software Engineer, TechVision GmbH")], conflicted=[]
    )
    assert "ground truth" in block and "do not re-derive" in block
    assert "check 7" in block
    assert f"entry id {WORK_ID}" in block
    only_conflict = render_pinned_facts_check_block(
        demand=[], conflicted=[(pin, "work: x", ["microservices"])]
    )
    assert "do NOT demand" in only_conflict and "microservices" in only_conflict
    assert render_pinned_facts_check_block(demand=[], conflicted=[]) == ""


def test_the_corrector_gets_its_own_reference_block_not_the_writers(fx):
    """The 2026-08-26 replay: with the WRITER header ("reproduce WORD-FOR-WORD")
    folded into `source`, the corrector re-inserted the ledger-conflicted pin the
    feedback had told it not to insert. The loop folds the corrector variant."""
    from applire.schemas.profile import MasterProfileData
    from applire.services.pin_reach import render_pinned_facts_block

    profile = MasterProfileData.model_validate(fx["profile"])
    writer = render_pinned_facts_block(_pins(fx), profile, target="cv", language="en")
    corrector = render_pinned_facts_block(
        _pins(fx), profile, target="cv", language="en", audience="corrector"
    )
    assert "WORD-FOR-WORD" in writer and "REQUIRED" in writer
    assert "ONLY when the REVIEW FEEDBACK names it" in corrector
    assert "Never insert a quote the feedback marks as conflicted" in corrector
    assert LONG_QUOTE_HEAD in corrector  # the quotes themselves ride along
    # the loop folds the corrector variant, never the writer's
    import inspect

    from applire.services import cv

    src = inspect.getsource(cv)
    assert 'audience="corrector"' in src
    assert "source_material = f\"{source_material}\\n\\n{pinned_facts_loop_block}\"" in src


# ── ADR-076 clause 2 bookkeeping ──────────────────────────────────────────────


def test_the_pinned_fact_signal_is_registered_ship_and_report_when_the_cv_chain_imports():
    import importlib

    import applire.services.cv  # noqa: F401 — the migration site
    from applire.services.signal_disposition import (
        ExhaustionDisposition,
        get_signal_disposition,
    )

    importlib.import_module("applire.services.pin_reach")
    record = get_signal_disposition(PINNED_FACT_SIGNAL_ID)
    assert record.disposition is ExhaustionDisposition.SHIP_AND_REPORT
    assert record.fallback_fn is None
    assert "ADR-077" in record.rationale


def test_both_cv_loops_declare_the_signal_and_wrap_the_reviewer_prompt():
    """Rule-against-one-of-N pin: the prose loop AND the terminal loop pass the
    signal id and the pin wrapper. A structural read of the two call sites."""
    import inspect
    import re

    from applire.services import cv

    src = inspect.getsource(cv)
    calls = [m.start() for m in re.finditer(r"await review_and_refine\(", src)]
    # `_review_cv_language` is the third caller — pins do not reach it (no
    # content demand there); the two content loops must both carry the signal.
    windows = [src[i : i + 1200] for i in calls]
    carrying = [w for w in windows if "signal_ids=(PINNED_FACT_SIGNAL_ID,)" in w]
    assert len(carrying) == 2, [w[:80] for w in windows]
    # both loops wrap their reviewer prompt fn — prose and terminal
    assert src.count("pinned_facts_reviewer_prompt_fn(") >= 2
    assert src.count("ensure_pinned_fact_signal_registered()") >= 2


# ── the compliance instrument (#537) ──────────────────────────────────────────


def test_pinned_fact_issues_classify_to_their_own_signal_class():
    from applire.services.review_compliance import SignalClass, classify_signal

    assert SignalClass.PINNED_FACT.value == "pinned_fact"
    issue = 'Pinned fact missing in work entry 3fe1ebf1: "Public Speaking"'
    assert classify_signal(issue) == SignalClass.PINNED_FACT
    # the positioning-block class keeps its own cues
    assert classify_signal("required content not delivered: company_domain_engagement") \
        == SignalClass.UNADDRESSED_REQUIREMENT


def test_pinned_fact_shape_is_two_sided_over_the_quoted_span():
    from applire.services.review_compliance import ComplianceOutcome, evaluate_compliance

    issue = ('Pinned fact not delivered — work entry 3fe1ebf1 must carry '
             '"Coordinated a team of 6 engineers across 3 time zones" word-for-word')
    present = "… Coordinated a team of 6 engineers across 3 time zones over an 18-month rollout …"
    absent = "… Coordinated six engineers in three time zones …"
    ok = evaluate_compliance(issue, "before", present)
    assert ok.outcome is ComplianceOutcome.IMPLEMENTED and ok.shape == "pinned_fact_quote_present"
    ko = evaluate_compliance(issue, "before", absent)
    assert ko.outcome is ComplianceOutcome.NOT_IMPLEMENTED
    # typographic quotes and dashes are inside the sanctioned normalisation set
    curly = 'Pinned fact missing: “Coordinated a team of 6 engineers across 3 time zones”'
    assert evaluate_compliance(curly, "b", present).outcome is ComplianceOutcome.IMPLEMENTED


def test_a_truncated_pinned_quote_grades_indeterminate_not_implemented():
    from applire.services.review_compliance import ComplianceOutcome, evaluate_compliance

    issue = 'Pinned fact missing in entry 3fe1ebf1: "Coordinated a team of 6 engineers…"'
    verdict = evaluate_compliance(issue, "b", "Coordinated a team of 6 engineers across 3 time zones")
    assert verdict.outcome is ComplianceOutcome.INDETERMINATE
    assert verdict.shape == "pinned_fact_quote_truncated"


def test_a_pinned_fact_issue_without_a_quote_stays_unmeasurable():
    from applire.services.review_compliance import ComplianceOutcome, evaluate_compliance

    verdict = evaluate_compliance("pinned fact missing in entry 3fe1ebf1", "b", "anything")
    assert verdict.outcome is ComplianceOutcome.UNMEASURABLE


# ── the report attribution (ADR-077 amendment 3) ──────────────────────────────


def test_report_entry_carries_the_ledger_conflict_fact(fx):
    from applire.services.ats_audit import _audit_cv_text

    long_pin = _pins(fx)[0]
    tailored = TailoredCVData.model_validate({
        "contact": {"name": "X"},
        "work_history": [{"id": WORK_ID, "company": "TechVision GmbH", "role": "Senior",
                          "start_date": "2020-01", "bullets": ["nothing pinned here"]}],
        "skills": [],
    })
    report = _audit_cv_text(
        "text", tailored, [], fx["keyword_ledger"], page_count=1, target=2,
        region="DACH", condensation_exhausted=False, pins=[long_pin],
    )
    entry = report.pinned_facts[0]
    assert entry.present is False
    assert entry.ledger_conflict == ["microservices"]
    # without a ledger the fact is simply empty — never None
    report2 = _audit_cv_text(
        "text", tailored, [], None, page_count=1, target=2,
        region="DACH", condensation_exhausted=False, pins=[long_pin],
    )
    assert report2.pinned_facts[0].ledger_conflict == []


def test_letter_report_entry_carries_the_same_fact(fx):
    from applire.services.ats_audit import _audit_letter_text

    long_pin = _pins(fx)[0]
    report = _audit_letter_text(
        "Nothing pinned here.", {"body": {"paragraphs": ["Nothing pinned here."]}}, [],
        fx["keyword_ledger"], pins=[long_pin],
    )
    assert report.pinned_facts[0].ledger_conflict == ["microservices"]


# ── clause 4 correction: the two pin-blind skills passes ──────────────────────


def _tailored_skills(skills) -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "X"},
        "work_history": [],
        "skills": list(skills),
    })


def _skill_pin(quote: str) -> FactPin:
    return FactPin(pin_id="s-1", entry_type="skill", entry_id="sk-1", quote=quote, targets=["cv"])


def test_dedup_skills_never_collapses_a_pinned_quote(monkeypatch):
    """Two page-duplicate tags where the FIRST is the survivor and the pinned
    one is the later, less specific form — the pass would drop it. With the pin
    it survives verbatim; the unpinned twin is the one that goes."""
    from applire.services import cv

    monkeypatch.setattr(
        cv, "_is_more_specific", lambda candidate, kept: False
    )
    from applire.services import ats_audit

    monkeypatch.setattr(ats_audit, "skills_page_dupe", lambda a, b: {a, b} == {"Public Speaking", "Speaking in public"})
    tailored = _tailored_skills(["Speaking in public", "Public Speaking"])
    without = cv._dedup_skills(tailored)
    assert without.skills == ["Speaking in public"]
    with_pin = cv._dedup_skills(tailored, pins=[_skill_pin("Public Speaking")])
    assert "Public Speaking" in with_pin.skills


def test_jd_echo_drop_never_drops_a_pinned_quote(monkeypatch):
    from applire.services import cv

    # No vault tie at all → the pass would drop a JD-echo tag; the pin keeps it.
    monkeypatch.setattr(
        "applire.services.profile.reconcile.stance.claimable_skill_names", lambda profile: []
    )
    job = {"required_skills": ["Public Speaking"], "nice_to_have_skills": [], "keywords": []}
    tailored = _tailored_skills(["Public Speaking"])
    dropped = cv._drop_ungrounded_jd_echo_skills(tailored, {"work_experience": []}, job, None)
    assert dropped.skills == []
    kept = cv._drop_ungrounded_jd_echo_skills(
        tailored, {"work_experience": []}, job, None, pins=[_skill_pin("Public Speaking")]
    )
    assert kept.skills == ["Public Speaking"]


# ── clause 1 correction: target renderability at pin time ─────────────────────


def test_a_cv_target_on_a_volunteer_or_publication_pin_is_refused():
    from applire.schemas.application import AddFactPinRequest
    from applire.services.fact_pins import check_target_renderable

    for entry_type in ("volunteer", "publication"):
        req = AddFactPinRequest(entry_type=entry_type, entry_id="x", quote="q", targets=["cv", "letter"])
        with pytest.raises(ValueError, match="CV"):
            check_target_renderable(req)
        letter_only = AddFactPinRequest(entry_type=entry_type, entry_id="x", quote="q", targets=["letter"])
        check_target_renderable(letter_only)  # no raise
    check_target_renderable(AddFactPinRequest(entry_type="work", entry_id="x", quote="q", targets=["cv"]))


# ── the size ratchet (letter precedent, test_review_prompts.py) ──────────────


def test_cv_reviewer_prompt_size_ratchet():
    """Check 7 landed at 10,476 chars (was 9,408). The ceiling sits just above the
    current size so the NEXT append meets the same question the letter's ratchet
    asks: map the new content to an SF-WRITE/SF-PIN row and replace, do not append."""
    from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT

    assert len(REVIEW_SYSTEM_PROMPT) < 10_700, (
        f"CV reviewer prompt is {len(REVIEW_SYSTEM_PROMPT)} chars — it is regrowing."
    )
