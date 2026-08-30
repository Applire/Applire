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

"""#617 — ADR-069 clause 4b (amended 2026-08-29): the JD-analysis reviewer's
prompt-facing view + code-computed GROUNDING FACTS block.

Captured evidence: the review loop eroded a correct extraction from 21
required skills (round 1) to 5 (round 5, never approved) because the auditor
called verbatim-grounded terms "fabricated" for reasons its own VERBATIM
GROUNDING RULE already forbade ("not stated", "only part of a phrase",
capitalisation, ...). Replay showed rewording the checks alone was
insufficient (~45 flags survived); supplying a code-computed fact per term
converged the same replay to zero verbatim false positives. This file pins
``services/jd_grounding.py`` (the view + facts) and its wiring into
``build_job_analysis_review_prompt``.

Synthetic posting only (never the captured Connect-AI text or
``Documents/testdata/RealProfiles/``) — see the module fixtures below for the
shapes it reproduces: a sub-phrase ("60% technical leadership"), a
hyphenated form ("information-retrieval"), a case-only difference
("agentic workflows"), a job-board metadata line ("Seniority level
Mid-Senior level"), a negated mention ("No Kubernetes experience needed"), a
short token that is a substring of a longer word ("AI" inside "domain"), and
a paraphrase ("commercially minded").
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from applire.prompts.review_job_analysis import build_job_analysis_review_prompt
from applire.services.jd_grounding import (
    JD_SCHEMA_KEYS,
    grounding_facts,
    is_verbatim,
    normalise,
    reviewer_view,
    strip_locators,
)


# ---------------------------------------------------------------------------
# Shared synthetic fixture — reused by tests 3, 4 and 6 below.
# ---------------------------------------------------------------------------

_JD_TEXT = """\
Nordlicht Analytics GmbH is hiring a Lead Machine Learning Engineer for our Berlin-based platform team.

About the role: you will design and operate production systems that combine large language models with structured retrieval. Our stack leans on information-retrieval pipelines, ranking and agentic workflows to answer customer questions from a large, constantly growing document corpus. You will lead a retrieval pod of around 6 engineers within the wider platform team.

Your responsibilities: own the roadmap for our retrieval stack; mentor two engineers; partner with the data science team on evaluation design. The role is 60% technical leadership, 40% hands-on coding — you will still ship code every week and review every pull request that touches the retrieval path.

What we look for: strong Python skills; hands-on experience with vector search and embeddings; comfort operating services in production; a commercially minded approach to prioritisation that weighs engineering effort against customer value. No Kubernetes experience needed — our platform team owns the deployment layer end to end. German language skills are a plus but not required; the working language is English throughout the team.

We build for a regulated domain and value engineers who communicate clearly with non-technical stakeholders and write things down. We offer a hybrid working model, a learning budget, and a small, senior team that ships weekly.

Seniority level: Mid-Senior level
Employment type: Full-time
Industry: Software Development"""

_SCOPE_QUOTE = "You will lead a retrieval pod of around 6 engineers within the wider platform team."
_LEADERSHIP_QUOTE = "The role is 60% technical leadership, 40% hands-on coding"

_DRAFT: dict = {
    "company_name": "Nordlicht Analytics GmbH",
    "role_title": "Lead Machine Learning Engineer",
    # "Kubernetes" is deliberately included here even though the posting only
    # NEGATES it ("No Kubernetes experience needed") — is_verbatim is
    # polarity-blind by design (check 1b's job, not the fact block's), so
    # this term still reads "verbatim yes" and the case documents that.
    "required_skills": ["Python", "Information retrieval", "Technical leadership", "Kubernetes"],
    "nice_to_have_skills": ["Agentic workflows", "German"],
    # "AI" is the #207 short-token-substring trap ("domain" contains "ai" but
    # is not "ai"); "Commercial mindset" is a paraphrase of "commercially
    # minded" — both must read verbatim NO with a words-found count.
    "keywords": ["AI", "Commercial mindset"],
    "seniority_level": "Mid-Senior level",
    "company_culture_signals": ["Berlin-based", "senior team"],
    "language_requirement": "English",
    "berufsbild_code": None,
    "berufsbild_label": None,
    "scope_requirements": [
        {
            "kind": "team_size",
            "value": 6.0,
            "value_max": None,
            "comparator": "approx",
            "quote": _SCOPE_QUOTE,
            "level": "required",
        }
    ],
    "leadership_emphasis": {"emphasis": "leadership_led", "quote": _LEADERSHIP_QUOTE},
}


def _draft_with_level_changes() -> dict:
    """A round-N draft shape: the extraction fields above PLUS the
    corrector's bookkeeping transport field, exactly as
    ``services/reviewer.py::review_and_refine`` would hand it to
    ``build_job_analysis_review_prompt`` on a later round."""
    return {**_DRAFT, "level_changes": [{"concept": "Kubernetes", "to": "nice_to_have"}]}


# ---------------------------------------------------------------------------
# 1. reviewer_view — schema allowlist + deep-copy discipline
# ---------------------------------------------------------------------------


def test_reviewer_view_strips_level_changes_and_unknown_keys_617():
    draft = _draft_with_level_changes()
    draft["_debug_note"] = "a future bookkeeping key that must never reach the reviewer"

    view = reviewer_view(draft)

    assert "level_changes" not in view
    assert "_debug_note" not in view
    # Every schema key this draft populated survives, and nothing else does.
    assert set(view.keys()) == JD_SCHEMA_KEYS
    assert view["required_skills"] == _DRAFT["required_skills"]
    assert view["leadership_emphasis"] == _DRAFT["leadership_emphasis"]


def test_reviewer_view_never_mutates_the_draft_617():
    draft = _draft_with_level_changes()
    original_level_changes = list(draft["level_changes"])

    view = reviewer_view(draft)

    # services/reviewer.py::review_and_refine hands THIS SAME draft object to
    # draft_history, which apply_jd_level_guard reads afterwards — a view
    # that mutated it here would corrupt that history invisibly.
    assert "level_changes" in draft
    assert draft["level_changes"] == original_level_changes

    assert id(view) != id(draft)
    # Deep copy: no nested dict/list is a shared reference with the draft.
    assert view["required_skills"] is not draft["required_skills"]
    assert view["scope_requirements"] is not draft["scope_requirements"]
    assert view["scope_requirements"][0] is not draft["scope_requirements"][0]
    assert view["leadership_emphasis"] is not draft["leadership_emphasis"]

    # Mutating the view must never reach the draft.
    view["required_skills"].append("Mutated")
    view["leadership_emphasis"]["quote"] = "mutated"
    assert draft["required_skills"] == _DRAFT["required_skills"]
    assert draft["leadership_emphasis"]["quote"] == _LEADERSHIP_QUOTE


# ---------------------------------------------------------------------------
# 2. is_verbatim — whole-word/phrase matching under the fixed normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term,posting_text,expected",
    [
        pytest.param(
            "Information retrieval",
            "Production experience with information-retrieval pipelines.",
            True,
            id="hyphenated-form",
        ),
        pytest.param(
            "Agentic workflows",
            "Hands-on experience with Agentic Workflows in production.",
            True,
            id="case-only-difference",
        ),
        pytest.param(
            "Technical leadership",
            "The role is 60% technical leadership, 40% hands-on.",
            True,
            id="sub-phrase-with-percent-sign",
        ),
        pytest.param(
            "AI",
            "Deep expertise in a regulated domain is required.",
            False,
            id="short-token-not-a-substring-hit-207-precedent",
        ),
        pytest.param(
            "Mid-Senior level",
            "Seniority level Mid-Senior level",
            True,
            id="job-board-metadata-line",
        ),
        pytest.param(
            "Client’s",  # curly right single quotation mark
            "We work directly with the client's stakeholders.",  # straight apostrophe
            True,
            id="nfkc-curly-vs-straight-apostrophe",
        ),
        pytest.param(
            "Kubernetes",
            "No Kubernetes experience needed for this role.",
            True,
            id="polarity-blind-by-design-check-1b-decides-negation-not-this",
        ),
        pytest.param("", "anything at all", False, id="empty-term-is-never-verbatim"),
    ],
)
def test_is_verbatim_whole_word_and_normalisation_617(term, posting_text, expected):
    assert is_verbatim(term, normalise(posting_text)) is expected


# ---------------------------------------------------------------------------
# 3. grounding_facts — every field, right fact, words-found only on "no"
# ---------------------------------------------------------------------------


def test_grounding_facts_block_covers_every_field_617():
    view = reviewer_view(_DRAFT)
    facts = grounding_facts(view, _JD_TEXT)

    assert facts.startswith("GROUNDING FACTS")

    # required_skills — all four verbatim (hyphenated, sub-phrase, and the
    # polarity-blind Kubernetes case all read "yes").
    assert '"Python": verbatim yes' in facts
    assert '"Information retrieval": verbatim yes' in facts
    assert '"Technical leadership": verbatim yes' in facts
    assert '"Kubernetes": verbatim yes' in facts

    # nice_to_have_skills
    assert '"Agentic workflows": verbatim yes' in facts
    assert '"German": verbatim yes' in facts

    # keywords — both non-verbatim, both carry a words-found count.
    assert '"AI": verbatim no (words found: 0/1)' in facts
    assert '"Commercial mindset": verbatim no (words found: 1/2)' in facts

    # role_title / company_name / seniority_level
    assert 'role_title — "Lead Machine Learning Engineer": verbatim yes' in facts
    assert 'company_name — "Nordlicht Analytics GmbH": verbatim yes' in facts
    assert 'seniority_level — "Mid-Senior level": verbatim yes' in facts

    # scope_requirements[].quote / leadership_emphasis.quote
    assert f'scope_requirements[0].quote — "{_SCOPE_QUOTE}": verbatim yes' in facts
    assert f'leadership_emphasis.quote — "{_LEADERSHIP_QUOTE}": verbatim yes' in facts

    # "words found" appears exactly twice — once per verbatim-NO term, never
    # alongside a verbatim-YES term.
    assert facts.count("words found") == 2
    for yes_line in (
        '"Python": verbatim yes',
        '"Kubernetes": verbatim yes',
        '"German": verbatim yes',
    ):
        idx = facts.index(yes_line)
        line_end = facts.index("\n", idx)
        assert "words found" not in facts[idx:line_end]


def test_grounding_facts_is_deterministic_and_order_follows_the_draft_617():
    """No LLM call, and the block does not re-sort the draft's own lists —
    it must read as a companion to the EXTRACTED ANALYSIS block above it."""
    view = reviewer_view(_DRAFT)
    first = grounding_facts(view, _JD_TEXT)
    second = grounding_facts(view, _JD_TEXT)
    assert first == second

    required_block = first.split("required_skills:")[1].split("nice_to_have_skills:")[0]
    order = [t for t in ("Python", "Information retrieval", "Technical leadership", "Kubernetes")]
    positions = [required_block.index(f'"{t}"') for t in order]
    assert positions == sorted(positions)


# ---------------------------------------------------------------------------
# 4. build_job_analysis_review_prompt — carries the view + the facts block
# ---------------------------------------------------------------------------


def test_review_prompt_carries_view_and_facts_617():
    result = build_job_analysis_review_prompt(_JD_TEXT, _draft_with_level_changes())

    assert "level_changes" not in result
    assert "GROUNDING FACTS" in result

    facts_idx = result.index("GROUNDING FACTS")
    return_idx = result.rindex("Return your review JSON.")
    assert facts_idx < return_idx

    # The view itself still reaches the EXTRACTED ANALYSIS block.
    assert "Nordlicht Analytics GmbH" in result
    assert '"Kubernetes": verbatim yes' in result


# ---------------------------------------------------------------------------
# 6. The level guard still reads level_changes from draft_history afterwards
# ---------------------------------------------------------------------------


def test_level_guard_still_reads_level_changes_after_view_617():
    """Reproduces review_and_refine's own sequencing (services/reviewer.py):
    reviewer_prompt_fn(source, current_draft) is called on the SAME object
    that draft_history holds, both for the initial draft (round 0) and for
    each corrector output (round N, appended to draft_history BEFORE the
    next round's reviewer call reads it again). apply_jd_level_guard then
    reads draft_history's raw dicts. If building the reviewer prompt ever
    mutated those objects (e.g. popping level_changes in place instead of
    copying), the guard would find no declaration and revert the corrector's
    legitimate, declared move — reproducing the #617 shape one level down.
    """
    from applire.services.jd_level_guard import apply_jd_level_guard

    jd_text = "Senior Ops Manager at Beispiel AG. Requires SAP and English. AWS is a plus."
    initial = {
        "company_name": "Beispiel AG",
        "role_title": "Senior Ops Manager",
        "required_skills": ["SAP", "English"],
        "nice_to_have_skills": ["AWS"],
        "keywords": [],
        "seniority_level": "Senior",
        "company_culture_signals": [],
        "language_requirement": "",
        "berufsbild_code": None,
        "berufsbild_label": None,
        "scope_requirements": [],
        "leadership_emphasis": None,
    }
    draft_history: list[dict] = [initial]

    # Round 1's reviewer call — built from draft_history[0], as
    # services/reviewer.py:638 does (reviewer_prompt_fn(source, current_draft)).
    build_job_analysis_review_prompt(jd_text, draft_history[0])
    assert draft_history[0] == initial, "round-1 view-building mutated the initial draft"

    # The corrector declares a legitimate demotion this round.
    corrected = {
        **initial,
        "required_skills": ["SAP"],
        "nice_to_have_skills": ["AWS", "English"],
        "level_changes": [{"concept": "English", "to": "nice_to_have"}],
    }
    draft_history.append(corrected)

    # Round 2's reviewer call — built from the NEW draft, exactly as the
    # loop's next iteration would (current_draft is draft_history[-1]).
    build_job_analysis_review_prompt(jd_text, draft_history[-1])
    assert draft_history[-1] == corrected, "round-2 view-building mutated the corrected draft"
    assert "level_changes" in draft_history[-1]

    settled = dict(corrected)
    result = apply_jd_level_guard(settled, draft_history)

    assert "English" in result["nice_to_have_skills"]
    assert "English" not in result["required_skills"]
    assert "SAP" in result["required_skills"]


# ---------------------------------------------------------------------------
# 7. The schema-key constant mirrors the extraction prompt's own schema block
# ---------------------------------------------------------------------------


def test_schema_keys_constant_matches_extraction_prompt_617():
    """As applire-prompt-first step 2 does: parse the extraction prompt's
    OWN "Schema:" JSON block with a regex and compare, rather than trusting
    the two to stay in sync by convention. Top-level keys only (exactly two
    leading spaces) — nested keys like scope_requirements[].quote or
    leadership_emphasis.emphasis are not draft-level keys and must not be
    counted as such."""
    import applire.prompts.job_analysis as job_analysis_prompt

    src = Path(job_analysis_prompt.__file__).read_text()
    schema_block = src.split("Schema:")[1].split("FIELD SHAPE")[0]
    top_level_keys = frozenset(re.findall(r'^  "(\w+)":', schema_block, flags=re.MULTILINE))

    assert top_level_keys, "the regex found nothing — the schema block's shape changed"
    assert JD_SCHEMA_KEYS == top_level_keys


# ---------------------------------------------------------------------------
# 8. The FALSE-POSITIVE direction (adversarial pass, 2026-08-30).
#
# A "verbatim yes" fact forecloses every FABRICATED finding against that term
# (the block says so in as many words), so a fact that is wrong in the
# POSITIVE direction is strictly worse than no fact at all. Two mechanisms
# produced one each, both demonstrated on realistic postings before the fix:
#
#   * stripping "+", "#" and "." collapsed a name to a bare word — "C++" and
#     "C#" both became "c" and matched "a Grade C in Mathematics"; ".NET"
#     became "net" and matched "own net margin targets";
#   * the hyphen-to-space rule isolated a matchable word inside a link —
#     "AI" matched a posting whose only "ai" was in "ai-solutions.de".
#
# Both are now closed at the normalisation, and the safe direction is
# preserved: a term the posting spells differently ("Node.js" vs "Node JS")
# reads "verbatim no" and stays the reviewer's judgement.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "term,posting_text,expected",
    [
        # symbol names must not collapse into an unrelated short word
        ("C++", "We need a Grade C in Mathematics and strong fundamentals.", False),
        ("C#", "A Grade C in Maths is required.", False),
        (".NET", "You own net margin targets for the whole portfolio.", False),
        # ... while still matching when the posting really names them
        ("C++", "Production experience with C++ and Rust.", True),
        ("C#", "Deep C# expertise in a .NET shop.", True),
        (".NET", "Experience with .NET Core services.", True),
        ("Node.js", "Node.js in production since 2019.", True),
    ],
    ids=[
        "cpp-not-grade-c",
        "csharp-not-grade-c",
        "dotnet-not-net-margin",
        "cpp-real",
        "csharp-real",
        "dotnet-real",
        "nodejs-real",
    ],
)
def test_is_verbatim_symbol_names_do_not_collapse_617(term, posting_text, expected):
    assert is_verbatim(term, normalise(strip_locators(posting_text))) is expected


@pytest.mark.parametrize(
    "posting_text,expected",
    [
        ("Find us at ai-solutions.de or write to careers@ai-solutions.de.", False),
        ("More at https://example.com/ai-team — apply today.", False),
        ("We build AI products for European SMEs.", True),
    ],
    ids=["bare-domain-and-email", "url", "real-mention"],
)
def test_is_verbatim_ignores_links_and_addresses_617(posting_text, expected):
    """A concept named only inside a link or an e-mail address is not a stated
    requirement — and must never be reported as a settled verbatim fact."""
    assert is_verbatim("AI", normalise(strip_locators(posting_text))) is expected


@pytest.mark.parametrize(
    "term,posting_text",
    [
        ("Großkunden", "Betreuung von Grosskunden in der Schweiz."),
        ("Grosskunden", "Betreuung von Großkunden im DACH-Raum."),
        ("Maßnahmen", "Ableitung von Massnahmen aus dem Reporting."),
    ],
    ids=["ss-in-posting", "eszett-in-posting", "measures"],
)
def test_normalise_folds_eszett_617(term, posting_text):
    """DACH-native: Swiss German never writes ß, so the same word spelled
    either way must compare equal (NFKC does not fold it)."""
    assert is_verbatim(term, normalise(strip_locators(posting_text))) is True


def test_grounding_facts_normalises_the_posting_through_strip_locators_617():
    """The block's posting side goes through strip_locators — pinning the CALL
    SITE, not just the helper (a helper nobody calls is not a control).

    The probe is a BARE DOMAIN on purpose: an e-mail address would pass this
    test even with the call site removed, because stripping "@" glues its
    neighbours into one token ("careersai") and nothing matches "ai" anyway.
    A bare "ai-solutions.de" is the case that genuinely needs the stripping —
    the hyphen rule isolates "ai" as its own word. Verified by mutation: with
    the call site removed this assertion fails, with the e-mail probe it did
    not (adversarial pass follow-up, 2026-08-30).
    """
    view = {"required_skills": ["AI"], "keywords": []}
    facts = grounding_facts(view, "Mehr zu uns finden Sie auf ai-solutions.de.")
    assert '"AI": verbatim no' in facts
