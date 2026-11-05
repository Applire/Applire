# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 6, WP-L fix round: the adversarial pass's confirmed findings.

Ported from the adversarial scratch tests (test_adv_g2_cv.py, test_adv_g1.py):

* G2-1  a current station stored with an ongoing marker ("present", "bis heute") is
        current, never ENDED. One implementation of "current": `profile.aspiration`.
* G2-2  a legal form that ends in a dot (e.V., S.A., B.V., N.V.) is stripped.
* C-1   the condense and the corrector's render measure protect the motivation and a
        named gap's sentence with its einarbeiten clause.
* MD6-3 the shared ledger block's EXPLICITLY DENIED header is an audience variant:
        every CV renderer gets the pre-build-6 text, only the letter names G-1.
* L2-1  the clause carries nothing about the candidate's past or abilities, in every
        checker; the triage sentence no longer exempts a gap + transfer + clause one.
Synthetic data throughout.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest  # noqa: E402

from applire.services.cover_letter_positioning import (  # noqa: E402
    employer_stations,
    render_employer_record,
)


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


def _station(company="Nordlicht Therapeutics SE", **kw):
    entry = {"company": company, "role": "Integration Lead", "start_date": "2019-01"}
    entry.update(kw)
    return {"work_experience": [entry]}


# ── G2-1: one implementation of "current" ───────────────────────────────────


@pytest.mark.parametrize("end", ["present", "Present", "bis heute", "heute", "aktuell", "ongoing", None, ""])
def test_an_ongoing_marker_or_no_end_is_current_not_ended(end):
    station = employer_stations(_station(end_date=end, is_current=False), "Nordlicht Therapeutics")[0]
    assert station["ended"] is False and station["end_date"] == "present"
    assert "ENDED" not in render_employer_record([station])
    assert "An ENDED position is the past" not in render_employer_record([station])


def test_a_real_end_date_is_ended():
    station = employer_stations(_station(end_date="2023-06"), "Nordlicht Therapeutics")[0]
    assert station["ended"] is True and station["end_date"] == "2023-06"


def test_no_start_and_no_end_is_unknown_never_called_ended():
    st = employer_stations({"work_experience": [{"company": "Nordlicht GmbH", "role": "R"}]}, "Nordlicht")
    assert st[0]["ended"] is False


def test_an_ongoing_marker_without_a_start_is_never_called_ended():
    st = employer_stations({"work_experience": [{"company": "Nordlicht GmbH", "role": "R",
                                                 "end_date": "bis heute"}]}, "Nordlicht")
    assert st[0]["ended"] is False


def test_current_is_the_aspiration_rule():
    """ADR-066: the same entry is current here exactly when `aspiration.is_current` says so."""
    from applire.schemas.profile import WorkEntry
    from applire.services.profile.aspiration import is_current

    for end in ("present", "bis heute", "2020-01", None):
        raw = {"company": "Nordlicht SE", "role": "R", "start_date": "2019-01", "end_date": end}
        ours = not employer_stations({"work_experience": [raw]}, "Nordlicht")[0]["ended"]
        theirs = is_current(WorkEntry(**raw))
        assert ours == theirs, end


# ── G2-2: legal forms that end in a dot ─────────────────────────────────────


@pytest.mark.parametrize("held, target", [
    ("Fraunhofer-Gesellschaft e.V.", "Fraunhofer-Gesellschaft"),
    ("ACME S.A.", "ACME"),
    ("Polder B.V.", "Polder"),
    ("Polder N.V.", "Polder B.V."),
    ("Kestrel GmbH.", "Kestrel GmbH"),
    ("Fraunhofer-Gesellschaft e. V.", "Fraunhofer-Gesellschaft e.V."),
])
def test_a_dotted_legal_form_is_stripped(held, target):
    assert employer_stations(_station(company=held, end_date="2020-01"), target)


@pytest.mark.parametrize("held, target", [
    ("ACME S.A.", "ACME Solutions"),
    ("Fraunhofer-Gesellschaft e.V.", "Fraunhofer"),
])
def test_still_never_a_substring(held, target):
    assert employer_stations(_station(company=held, end_date="2020-01"), target) == []


# ── C-1: the condense and the render measure protect motivation and clause ───


@pytest.mark.parametrize("language", ["de", "en", None])
def test_condense_protects_the_motivation_and_the_named_gap_sentence(language):
    from applire.prompts.cover_letter import build_condense_prompt

    p = _flat(build_condense_prompt({"body": {"paragraphs": ["x"]}}, 200, 2, 1, language=language))
    assert "the motivation: the sentence(s) saying why this role at this employer" in p
    assert "each named gap's one sentence" in p
    assert "its closing get-up-to-speed clause" in p
    assert "never cut the clause or the sentence" in p
    assert "arbeite ich mich schnell ein" in p and "get up to speed" in p


def test_the_corrector_render_measure_names_the_same_list():
    from applire.prompts.cover_letter import render_measure_block

    p = _flat(render_measure_block(300, 250, 2, 1))
    assert "the motivation: the sentence(s) saying why this role" in p
    assert "never cut the clause or the sentence" in p


# ── MD6-3: the CV sees the pre-build-6 header, byte for byte ─────────────────

LEDGER = [
    {"concept": "Apache Camel", "status": "direct", "claimable": True, "fit_weight": 1.0,
     "sources": ["required"], "surface_forms": ["Apache Camel"], "evidence": "Built Camel routes"},
    {"concept": "MuleSoft", "status": "denied", "claimable": False, "fit_weight": 1.0,
     "sources": ["required"]},
]
#: the EXPLICITLY DENIED header as it stood at 381d30cd (before build 6) — RULING MD6-3
CV_HEADER = (
    "EXPLICITLY DENIED BY THE CANDIDATE (they were asked and stated they do NOT "
    "have these). Never claim them, and never soften or walk back the denial. "
    "Unlike the list above these are the candidate's OWN stated position, so a "
    "cover letter MAY name one honestly — ONLY where a REQUIRED: STATED LIMITS "
    "block lists it, with what they do bring in the same sentence (RULING L-1); "
    "otherwise the letter, like a CV, simply omits them:"
)
G1_MARKERS = ("KEY-REQUIREMENT GAPS", "RULING G-1")


def _assert_cv(prompt: str) -> None:
    assert CV_HEADER in prompt
    for marker in G1_MARKERS:
        assert marker not in prompt


def test_the_shared_block_defaults_to_the_cv_text():
    from applire.services.keyword_ledger import render_ledger_prompt_block

    _assert_cv(render_ledger_prompt_block(LEDGER))
    letter = render_ledger_prompt_block(LEDGER, audience="letter")
    assert "ONLY where its KEY-REQUIREMENT GAPS block names it" in letter
    assert CV_HEADER not in letter


def test_SEAM_cv_single_call_prompt_gets_the_cv_header():
    from applire.prompts.cv_tailoring import build_user_prompt

    _assert_cv(build_user_prompt({}, {}, [], "de", keyword_ledger=LEDGER))


@pytest.mark.parametrize("builder", ["outline", "work", "summary", "skills"])
def test_SEAM_cv_segmented_prompts_get_the_cv_header(builder):
    from applire.prompts import cv_segmented as seg

    prompt = {
        "outline": lambda: seg.build_outline_prompt({}, {}, "de", keyword_ledger=LEDGER),
        "work": lambda: seg.build_work_section_prompt({}, {}, {}, [], "de", keyword_ledger=LEDGER),
        "summary": lambda: seg.build_summary_prompt({}, {}, {}, "de", keyword_ledger=LEDGER),
        "skills": lambda: seg.build_skills_prompt({}, {}, {}, [], "de", keyword_ledger=LEDGER),
    }[builder]()
    _assert_cv(prompt)


def test_SEAM_letter_prompt_gets_the_g1_header():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    p = build_cover_letter_prompt(cv_data={}, jd_text="x", detected_language="de",
                                  pre_gen_inputs={}, keyword_ledger=LEDGER)
    assert "ONLY where its KEY-REQUIREMENT GAPS block names it" in p
    assert CV_HEADER not in p


# ── L2-1: nothing about the past or abilities, in every checker ──────────────


def test_the_shared_clause_rule_bars_skills_tools_and_experience():
    from applire.services.cross_document import EINARBEITEN_CLAUSE_RULE

    p = _flat(EINARBEITEN_CLAUSE_RULE)
    assert "it says nothing about the candidate's past or abilities" in p
    assert "no skill, tool, experience, duration, figure, employer, project, certificate or trait" in p
    assert "a transfer goes before the clause" in p


@pytest.mark.parametrize("v2", [False, True])
def test_the_reviewer_and_the_corrector_bar_the_same(v2):
    from applire.prompts.review_cover_letter import letter_loop_prompts

    review, terminal, corrector = letter_loop_prompts(v2)
    for text in (review, terminal):
        assert "but any skill, tool, experience, figure, employer or trait in it is a claim" in _flat(text)
    assert ("nothing about their past or abilities — no skill, tool, experience, figure, "
            "employer, project, certificate or trait") in _flat(corrector)


def test_the_triage_gap_transfer_clause_sentence_is_a_claim():
    from applire.prompts.oracle_triage import ORACLE_TRIAGE_SYSTEM_PROMPT as p

    flat = _flat(p)
    assert "closes with such a clause, and states nothing else, is candidate-limit" in flat
    assert "if the sentence also states a transfer, experience, skill or tool the candidate has" in flat
    assert "the rule above applies: candidate-claim" in flat
    assert "\"thanks to my dbt experience\"" in flat
    # rule 3's own sentence stands unchanged beside it
    assert "a sentence that states a limit and also claims something positive is candidate-claim" in flat


def test_prompt_ratchets_hold():
    from applire.prompts import review_cover_letter as rc

    assert len(rc.REVIEW_SYSTEM_PROMPT) < 13_450
    assert len(rc.TERMINAL_REVIEW_SYSTEM_PROMPT) < 17_000
