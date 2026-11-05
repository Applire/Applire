# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 6, WP-L fix round 2: the delivery run's findings.

* D-2 (RULING D-2 = A)  in the G-1 pick a gap from the posting's candidate-requirements
        section ranks ahead of one that occurs only in the responsibilities; then L-1.
* D-3 (MD6-9)  a key-tier row the ADR-100 backing check demoted (``unbacked``) is no
        G-1 gap — the letter leaves it out, never "X is new to me".
* D-5 (MD6-11)  the CV language pass keeps an established loanword of the required
        language's business usage and every term the posting itself uses.
Synthetic data throughout.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest  # noqa: E402

from applire.services import cross_document as xd  # noqa: E402
from applire.services.cross_document import (  # noqa: E402
    gaps_to_name,
    key_requirement_gaps,
    posting_requirements_text,
    render_gap_ladder_block,
)


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


def _row(concept, status="gap", weight=1.0, claimable=False, **over):
    row = {
        "concept": concept, "surface_forms": [], "status": status,
        "claimable": claimable, "fit_weight": weight, "sources": ["required"],
    }
    row.update(over)
    return row


# ── D-2: the posting's requirements section (RULING D-2 = A) ─────────────────

# The delivery shape (synthetic): a responsibilities line names "Capacity planning"
# BEFORE the must-have section names the platform the candidate lacks.
JD_LINES = """Head of Integration (m/f/d)
Halvard Pharma AG · Basel

About us
We make sterile injectables.

Your responsibilities
- Run the integration platform as a product: availability, SLAs and capacity planning
- Lead a team of eight engineers

Your profile (must have)
- Hands-on experience with Boomi and its Flow runtime
- Deep experience with Google Cloud integration services
- Several years of leading engineering teams

Nice to have
- Event streaming with Pulsar

What we offer
Hybrid working.
"""

# The founder-log layout (synthetic): the posting pasted as ONE line, headings run in.
JD_INLINE = (
    "Lugano, hybrid About the Role: You plan the integration roadmap and integration "
    "governance. Your Mission: Drive Boomi adoption, integration governance and "
    "reference designs across sites. A Perfect Match: Engineering or science degree; "
    "several years with Boomi and integration governance; fluent in English "
    "Your Benefits: Pension, childcare."
)


def test_requirements_text_is_the_profile_section_up_to_the_next_heading():
    req = _flat(posting_requirements_text(JD_LINES))
    assert "boomi" in req and "google cloud" in req
    assert "capacity planning" not in req  # responsibilities
    assert "pulsar" not in req             # nice to have ends it
    assert "hybrid" not in req


def test_requirements_text_reads_run_in_colon_headings():
    req = _flat(posting_requirements_text(JD_INLINE))
    assert req.startswith("engineering or science degree")
    assert "boomi" in req and "pension" not in req
    assert "roadmap" not in req and "reference designs" not in req


@pytest.mark.parametrize("jd", [
    "Wir suchen: Kafka, Terraform und Boomi.",                       # no heading at all
    "You meet the following requirements: Kafka and Terraform.",       # lower-case prose
    "Nice to have: OpenShift. Your tasks: run Kafka.",                  # only other headings
])
def test_no_recognisable_requirements_heading_reads_as_no_section(jd):
    assert posting_requirements_text(jd) == ""


def test_german_headings_are_recognised():
    jd = (
        "Ihre Aufgaben\n- Kapazitätsplanung der Plattform\n\nIhr Profil\n- Erfahrung mit "
        "Boomi\n\nWir bieten\n- Homeoffice\n"
    )
    req = _flat(posting_requirements_text(jd))
    assert "boomi" in req and "kapazitätsplanung" not in req and "homeoffice" not in req


def _delivery_ledger():
    return [
        _row("Capacity planning"),
        _row("SLA management"),
        _row("Boomi", status="denied"),
        _row("Google Cloud", status="denied"),
    ]


def test_a_must_have_gap_outranks_an_earlier_responsibilities_gap():
    gaps = key_requirement_gaps(_delivery_ledger(), [], JD_LINES)
    # equal weight, equal mentions: as built, first position picked "Capacity planning"
    assert [g.label for g in gaps_to_name(gaps)] == ["Boomi"]
    assert [g.in_requirements for g in gaps] == [True, True, False, False]
    assert [g.label for g in gaps[:2]] == ["Boomi", "Google Cloud"]


def test_inside_the_section_l1_order_still_decides():
    ledger = [_row("reference designs"), _row("Boomi"), _row("integration governance")]
    gaps = key_requirement_gaps(ledger, [], JD_INLINE)
    # Boomi + integration governance are both in "A Perfect Match"; governance is named
    # three times in the posting, Boomi twice → mentions decide inside the section.
    assert [g.label for g in gaps] == ["integration governance", "Boomi", "reference designs"]
    assert [g.in_requirements for g in gaps] == [True, True, False]


def test_without_a_section_the_pick_is_l1_unchanged():
    jd = JD_LINES.replace("Your profile (must have)", "More about the work")
    gaps = key_requirement_gaps(_delivery_ledger(), [], jd)
    assert not any(g.in_requirements for g in gaps)
    assert gaps_to_name(gaps)[0].label == "Capacity planning"


def test_a_recorded_statement_is_labelled_by_its_must_have_concept():
    statement = "Mit Kapazitätsplanung und Boomi habe ich nicht gearbeitet."
    denied = [{"concept": "Capacity planning", "statement": statement},
              {"concept": "Boomi", "statement": statement}]
    ledger = [_row("Capacity planning", status="denied"), _row("Boomi", status="denied"),
              _row("SLA management")]
    gaps = key_requirement_gaps(ledger, denied, JD_LINES)
    assert gaps[0].label == "Boomi" and gaps[0].concepts == ("Boomi", "Capacity planning")
    assert gaps[0].in_requirements


# ── D-3 (MD6-9): an unbacked row is no gap ───────────────────────────────────

def _heal_ledger():
    # heal1 shape (synthetic): the classifier judged "Enterprise integration"
    # supported, its quote did not resolve → status gap + unbacked; it sits first.
    return [
        _row("Enterprise integration", unbacked="no_verifiable_quote"),
        _row("Google Cloud"),
        _row("Capacity planning"),
        _row("Boomi", unbacked="no_verifiable_quote"),
    ]


def test_an_unbacked_row_is_not_a_key_gap():
    assert not xd._is_key_gap_row(_row("X", unbacked="no_verifiable_quote"))
    assert xd._is_key_gap_row(_row("X"))


def test_the_heal_shape_names_a_real_gap_and_never_the_unbacked_one():
    jd = JD_LINES.replace("Hands-on experience with Boomi",
                          "Enterprise integration experience, hands-on with Boomi")
    gaps = key_requirement_gaps(_heal_ledger(), [], jd)
    labels = [g.label for g in gaps]
    assert labels == ["Google Cloud", "Capacity planning"]
    block = render_gap_ladder_block(gaps, gaps_to_name(gaps), "en")
    assert "Enterprise integration" not in block and "Boomi" not in block
    assert "Google Cloud" in block


# ── D-5 (MD6-11): loanwords and posting terms survive the language pass ──────

from applire.prompts import review_cv_language as lang  # noqa: E402


def test_both_sides_of_the_pass_carry_the_loanword_rule():
    rev = _flat(lang.CV_LANGUAGE_REVIEW_SYSTEM_PROMPT)
    gen = _flat(lang.CV_LANGUAGE_REFINEMENT_PROMPT)
    for text in (rev, gen):
        assert "established loanwords and posting terms" in text
        assert "lean management" in text and "key-user" in text
        assert "schlankes management" in text
    # the must-translate examples no longer name a German business loanword
    must = rev.split("are competency phrases and must be translated")[0].rsplit("sounds:", 1)[1]
    assert "change management" not in must and "stakeholder" not in must
    # …and a genuinely English phrase is still the pass's job
    assert "an english phrase or sentence that is not such a term is still translated" in rev


def test_posting_terms_are_the_analysis_lists_deduped_in_order():
    job = {"required_skills": ["Lean-Produktion", "KVP", " kvp "],
           "nice_to_have_skills": ["SAP PP", ""], "keywords": ["Lean-Produktion", 7, "Qualität"]}
    assert lang.language_pass_posting_terms(job) == ("Lean-Produktion", "KVP", "SAP PP", "Qualität")
    assert lang.language_pass_posting_terms(None) == ()
    many = {"keywords": [f"T{i}" for i in range(100)]}
    assert len(lang.language_pass_posting_terms(many)) == lang._POSTING_TERMS_CAP


_DRAFT = {"summary": "Produktionsleiter.", "work": [{"id": "w1", "bullets": ["Führte KVP ein."]}],
          "skills": ["Lean Management"]}


def test_without_terms_both_user_prompts_are_unchanged():
    rev = lang.build_cv_language_review_prompt("German", _DRAFT)
    assert "POSTING TERMS" not in rev
    assert rev == lang.build_cv_language_review_prompt("German", _DRAFT, posting_terms=())
    gen = lang.build_cv_language_refinement_prompt(_DRAFT, "fb", "German")
    assert "POSTING TERMS" not in gen and gen.startswith("Reviewer feedback: fb\n\nPrevious draft:")


def test_terms_reach_both_user_prompts():
    terms = ("Lean-Produktion", "Shopfloor-Management")
    rev = lang.build_cv_language_review_prompt("German", _DRAFT, posting_terms=terms)
    gen = lang.build_cv_language_refinement_prompt(_DRAFT, "fb", "German", posting_terms=terms)
    for text in (rev, gen):
        assert 'POSTING TERMS' in text and '"Lean-Produktion", "Shopfloor-Management"' in text
    assert rev.index("POSTING TERMS") < rev.index("Are the summary")
    assert gen.index("POSTING TERMS") < gen.index("Previous draft:")


def test_the_pass_hands_the_terms_to_reviewer_and_refiner(monkeypatch):
    from applire.services import cv as cv_mod

    seen = {}

    async def fake_review_and_refine(**kw):
        seen["rev"] = kw["reviewer_prompt_fn"]("German", _DRAFT)
        seen["gen"] = kw["generator_prompt_fn"](_DRAFT, "fb", "German")
        return kw["draft"]

    monkeypatch.setattr(cv_mod, "review_and_refine", fake_review_and_refine)
    asyncio.run(cv_mod._review_cv_language(_DRAFT, "de", object(), posting_terms=("Lean-Produktion",)))
    assert "Lean-Produktion" in seen["rev"] and "Lean-Produktion" in seen["gen"]
    asyncio.run(cv_mod._review_cv_language(_DRAFT, "de", object()))
    assert "POSTING TERMS" not in seen["rev"] and "POSTING TERMS" not in seen["gen"]


@pytest.mark.parametrize("jd_language,document_language,expect", [
    ("de", "de", True),
    ("de", "de-DE", True),
    ("en", "de", False),   # an English posting's terms never license English in a German CV
    ("de", None, False),
])
def test_terms_ride_only_when_the_posting_is_in_the_document_language(
    jd_language, document_language, expect
):
    from applire.services import cv as cv_mod

    job = SimpleNamespace(jd_language=jd_language, raw_text="")
    terms = cv_mod._language_pass_terms(job, {"required_skills": ["Lean-Produktion"]}, document_language)
    assert bool(terms) is expect


def test_the_second_round_and_the_terminal_recheck_forward_the_terms():
    import inspect

    from applire.services import cv as cv_mod

    for fn in (cv_mod._second_language_round, cv_mod._terminal_language_recheck,
               cv_mod._terminal_review):
        assert "posting_terms" in inspect.signature(fn).parameters, fn.__name__
    src = inspect.getsource(cv_mod)
    assert src.count("posting_terms=language_terms") == 4  # main, second, 2× terminal
    # 2× functools.partial in the pass, + second round, recheck, terminal→recheck
    assert src.count("posting_terms=posting_terms") == 5
