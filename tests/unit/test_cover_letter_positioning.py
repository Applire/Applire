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

"""E048/US264 — deterministic (no-LLM) positioning inputs.

Hermetic: pure functions, no LLM, no DB.
"""

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ---------------------------------------------------------------------------
# detect_concurrent_roles
# ---------------------------------------------------------------------------


def test_detect_concurrent_roles_true_when_two_current_roles():
    from applire.services.cover_letter_positioning import detect_concurrent_roles

    work_experience = [
        {"role": "CTO", "company": "Startup A", "is_current": True, "end_date": None},
        {"role": "Advisor", "company": "Startup B", "is_current": True, "end_date": None},
    ]
    assert detect_concurrent_roles(work_experience) is True


def test_detect_concurrent_roles_true_for_open_end_date_without_is_current_flag():
    """is_current unset (legacy rows) + blank end_date still counts as open-ended."""
    from applire.services.cover_letter_positioning import detect_concurrent_roles

    work_experience = [
        {"role": "Engineer", "company": "A", "end_date": None},
        {"role": "Consultant", "company": "B", "end_date": ""},
    ]
    assert detect_concurrent_roles(work_experience) is True


def test_detect_concurrent_roles_false_for_single_current_role():
    from applire.services.cover_letter_positioning import detect_concurrent_roles

    work_experience = [
        {"role": "Engineer", "company": "A", "is_current": True, "end_date": None},
        {"role": "Past role", "company": "B", "is_current": False, "end_date": "2019"},
    ]
    assert detect_concurrent_roles(work_experience) is False


def test_detect_concurrent_roles_false_when_explicitly_ended_with_blank_end_date():
    """is_current=False must never count as open, even with a blank end_date —
    the tri-state convention (#155): False always means known-ended."""
    from applire.services.cover_letter_positioning import detect_concurrent_roles

    work_experience = [
        {"role": "A", "company": "X", "is_current": False, "end_date": None},
        {"role": "B", "company": "Y", "is_current": False, "end_date": None},
    ]
    assert detect_concurrent_roles(work_experience) is False


def test_detect_concurrent_roles_empty_list():
    from applire.services.cover_letter_positioning import detect_concurrent_roles

    assert detect_concurrent_roles([]) is False


# ---------------------------------------------------------------------------
# find_gap_testimony
# ---------------------------------------------------------------------------


def test_find_gap_testimony_matches_regulated_industries_argument():
    from applire.services.cover_letter_positioning import find_gap_testimony

    category_c = ["regulated industries experience"]
    stories = [
        {
            "title": "Bringing GxP rigor to a startup",
            "challenge": "The team had never worked in regulated industries before.",
            "mechanism": "I brought my prior pharma QA discipline to the process.",
            "outcome": "We passed our first audit with zero findings.",
            "benchmark": None,
        }
    ]
    result = find_gap_testimony(category_c, stories)
    assert result is not None
    assert result["gap"] == "regulated industries experience"
    assert result["story"]["title"] == "Bringing GxP rigor to a startup"


def test_find_gap_testimony_none_when_no_story_overlaps():
    from applire.services.cover_letter_positioning import find_gap_testimony

    category_c = ["Kubernetes orchestration"]
    stories = [
        {
            "title": "Winning a design award",
            "challenge": "Our brand felt generic.",
            "mechanism": "I ran a full visual identity overhaul.",
            "outcome": "We won a regional design award.",
        }
    ]
    assert find_gap_testimony(category_c, stories) is None


def test_find_gap_testimony_none_when_no_stories():
    from applire.services.cover_letter_positioning import find_gap_testimony

    assert find_gap_testimony(["some gap"], []) is None


def test_find_gap_testimony_first_matching_gap_wins():
    """category_c is already severity-ordered; the first gap with a positive
    story match wins (deterministic, no re-ranking across gaps)."""
    from applire.services.cover_letter_positioning import find_gap_testimony

    category_c = ["no story here at all", "regulated industries experience"]
    stories = [
        {
            "title": "Regulated industries pivot",
            "challenge": "New to regulated industries.",
            "mechanism": "Applied adjacent QA rigor.",
            "outcome": "Delivered a compliant release.",
        }
    ]
    result = find_gap_testimony(category_c, stories)
    assert result["gap"] == "regulated industries experience"


# ---------------------------------------------------------------------------
# find_availability_testimony
# ---------------------------------------------------------------------------


def test_find_availability_testimony_from_signature_story():
    """#272 Task 1: the bare word 'parallel' in the challenge sentence must NOT be
    what qualifies this story — only the mechanism sentence's own 'availability'
    phrase does, and only THAT sentence is returned (never the challenge sentence,
    never the whole story)."""
    from applire.services.cover_letter_positioning import find_availability_testimony

    stories = [
        {
            "title": "Balancing two advisory roles",
            "challenge": "I run two advisory roles in parallel.",
            "mechanism": "I block dedicated hours for each and communicate availability clearly.",
            "outcome": "Both engagements stayed on schedule.",
        }
    ]
    result = find_availability_testimony(stories, [])
    assert result is not None
    assert "availability" in result
    assert result == "I block dedicated hours for each and communicate availability clearly."
    assert "parallel" not in result


def test_find_availability_testimony_from_enrichment_history():
    from applire.services.cover_letter_positioning import find_availability_testimony

    enrichment_history = [
        {
            "source": "interview",
            "changes": [
                {
                    "section": "personal_info",
                    "field": "availability",
                    "action": "added",
                    "new_value": "Available immediately; current contract ends this month.",
                    "rationale": "candidate stated their availability",
                }
            ],
        }
    ]
    result = find_availability_testimony([], enrichment_history)
    assert result is not None
    assert "Available immediately" in result


def test_find_availability_testimony_none_when_nothing_matches():
    from applire.services.cover_letter_positioning import find_availability_testimony

    stories = [
        {"title": "Winning an award", "challenge": "x", "mechanism": "y", "outcome": "z"}
    ]
    enrichment_history = [
        {"changes": [{"rationale": "added a new skill", "new_value": "Python"}]}
    ]
    assert find_availability_testimony(stories, enrichment_history) is None


def test_find_availability_testimony_empty_inputs():
    from applire.services.cover_letter_positioning import find_availability_testimony

    assert find_availability_testimony([], []) is None


# ---------------------------------------------------------------------------
# #272 Task 1 — RC-C regression: bare-token "parallel" must never qualify
# ---------------------------------------------------------------------------


def test_find_availability_testimony_rejects_paper_title_bare_parallel_match():
    """RC-C ground truth (run-5): an enrichment record — rationale 'Added title to
    publications via reconciliation', value 'Parallel Processing via a Dual
    Olfactory Pathway in the Honeybee' — matched on the bare token 'parallel' and
    was threaded into the writer prompt as availability testimony. It must NOT
    match under phrase-scoped detection."""
    from applire.services.cover_letter_positioning import find_availability_testimony

    enrichment_history = [
        {
            "changes": [
                {
                    "rationale": "Added title to publications via reconciliation.",
                    "new_value": "Parallel Processing via a Dual Olfactory Pathway in the Honeybee",
                }
            ]
        }
    ]
    assert find_availability_testimony([], enrichment_history) is None


def test_find_availability_testimony_rejects_paper_title_as_story_text():
    """Same paper-title record, but shaped as a signature story — still no match."""
    from applire.services.cover_letter_positioning import find_availability_testimony

    stories = [
        {
            "title": "Parallel Processing via a Dual Olfactory Pathway in the Honeybee",
            "challenge": "Added title to publications via reconciliation.",
            "mechanism": None,
            "outcome": None,
        }
    ]
    assert find_availability_testimony(stories, []) is None


def test_find_availability_testimony_from_denied_concept_statement():
    """RC-C ground truth: the candidate's real availability testimony was the tail
    of a denied_concepts[].statement — a source find_availability_testimony never
    searched. Only the availability-bearing sentence must be returned, never the
    RAG-scope sentences that precede it in the same statement."""
    from applire.services.cover_letter_positioning import find_availability_testimony

    denied_concepts = [
        {
            "concept": "embedding models",
            "statement": (
                "…So I have not configured embedding models, vector stores or "
                "rerankers myself… On availability: that can be discussed."
            ),
            "source": "interview",
            "date": "2026-07-01",
        }
    ]
    result = find_availability_testimony([], [], denied_concepts)
    assert result == "On availability: that can be discussed."
    assert "embedding models" not in result
    assert "RAG" not in result


def test_find_availability_testimony_denied_concepts_default_none_is_back_compat():
    """Existing 2-positional-arg callers (pre-#272) must keep working unchanged."""
    from applire.services.cover_letter_positioning import find_availability_testimony

    assert find_availability_testimony([], []) is None


def test_find_availability_testimony_notice_period_phrase():
    from applire.services.cover_letter_positioning import find_availability_testimony

    denied_concepts = [
        {
            "concept": "x",
            "statement": "My current notice period is three months.",
            "source": "interview",
            "date": "2026-07-01",
        }
    ]
    result = find_availability_testimony([], [], denied_concepts)
    assert result == "My current notice period is three months."


def test_find_availability_testimony_alongside_my_phrase():
    from applire.services.cover_letter_positioning import find_availability_testimony

    stories = [
        {
            "title": "Two commitments",
            "challenge": "Context around the role.",
            "mechanism": "I manage this alongside my current role without conflict.",
            "outcome": None,
        }
    ]
    result = find_availability_testimony(stories, [])
    assert result == "I manage this alongside my current role without conflict."


# ---------------------------------------------------------------------------
# #272 Task 3 — has_closing_paragraph (structural retention predicate)
# ---------------------------------------------------------------------------


def test_has_closing_paragraph_true_for_genuine_closing():
    from applire.services.cover_letter_positioning import has_closing_paragraph

    letter_data = {
        "body": {
            "paragraphs": [
                "Dear Hiring Team,",
                "Why me paragraph.",
                "I would welcome the opportunity to discuss how my experience "
                "aligns with your needs. My notice period can be discussed.",
            ]
        }
    }
    assert has_closing_paragraph(letter_data) is True


def test_has_closing_paragraph_false_for_bare_stub():
    """RC-D ground truth: the run-5 shipped final paragraph was the bare stub
    'Notice period can be discussed.' — must read as NOT a genuine closing."""
    from applire.services.cover_letter_positioning import has_closing_paragraph

    letter_data = {"body": {"paragraphs": ["Dear Hiring Team,", "Notice period can be discussed."]}}
    assert has_closing_paragraph(letter_data) is False


def test_has_closing_paragraph_false_when_no_paragraphs():
    from applire.services.cover_letter_positioning import has_closing_paragraph

    assert has_closing_paragraph({"body": {"paragraphs": []}}) is False
    assert has_closing_paragraph({}) is False
    assert has_closing_paragraph(None) is False


# ---------------------------------------------------------------------------
# #272 Task 6 — word_floor_reviewer_prompt_fn (deterministic reviewer wrapper)
# ---------------------------------------------------------------------------


def test_word_floor_reviewer_prompt_fn_appends_block_when_under_floor():
    from applire.services.cover_letter_positioning import word_floor_reviewer_prompt_fn

    def base(source, draft):
        return "BASE PROMPT"

    wrapped = word_floor_reviewer_prompt_fn(base, word_floor=150)
    draft = {"body": {"paragraphs": ["Only ten words appear right here in this short body."]}}
    result = wrapped("source", draft)
    assert "BASE PROMPT" in result
    assert "WORD FLOOR" in result
    assert "insufficient selected evidence" in result.lower()


def test_word_floor_reviewer_prompt_fn_no_block_when_at_or_above_floor():
    from applire.services.cover_letter_positioning import word_floor_reviewer_prompt_fn

    def base(source, draft):
        return "BASE PROMPT"

    wrapped = word_floor_reviewer_prompt_fn(base, word_floor=5)
    draft = {"body": {"paragraphs": ["one two three four five six seven eight"]}}
    result = wrapped("source", draft)
    assert result == "BASE PROMPT"


def test_word_floor_reviewer_prompt_fn_never_instructs_padding():
    from applire.services.cover_letter_positioning import word_floor_reviewer_prompt_fn

    wrapped = word_floor_reviewer_prompt_fn(lambda s, d: "BASE", word_floor=100)
    result = wrapped("source", {"body": {"paragraphs": ["short body"]}})
    low = result.lower()
    assert "pad" not in low or "never" in low  # padding must be explicitly forbidden, not suggested
    assert "invent" in low or "never invent" in low


# ---------------------------------------------------------------------------
# Wave-6 follow-up (charter run #6, Task 2) — body_word_count / within_word_budget
# ---------------------------------------------------------------------------


def test_body_word_count_sums_all_paragraphs():
    from applire.services.cover_letter_positioning import body_word_count

    letter_data = {"body": {"paragraphs": ["one two three", "four five"]}}
    assert body_word_count(letter_data) == 5


def test_body_word_count_empty_inputs():
    from applire.services.cover_letter_positioning import body_word_count

    assert body_word_count(None) == 0
    assert body_word_count({}) == 0
    assert body_word_count({"body": {"paragraphs": []}}) == 0


def test_within_word_budget_true_at_or_under_budget():
    from applire.services.cover_letter_positioning import within_word_budget

    letter_data = {"body": {"paragraphs": ["one two three four five"]}}
    assert within_word_budget(letter_data, word_budget=5) is True
    assert within_word_budget(letter_data, word_budget=10) is True


def test_within_word_budget_false_over_budget():
    from applire.services.cover_letter_positioning import within_word_budget

    letter_data = {"body": {"paragraphs": ["one two three four five six"]}}
    assert within_word_budget(letter_data, word_budget=5) is False


def test_within_word_budget_reuses_the_same_counter_as_the_word_floor_check():
    """The floor and the ceiling must never disagree about what "the body's
    word count" means — both go through body_word_count."""
    from applire.services.cover_letter_positioning import body_word_count, within_word_budget

    letter_data = {"body": {"paragraphs": ["a b c d e f g h i j"]}}
    count = body_word_count(letter_data)
    assert within_word_budget(letter_data, word_budget=count) is True
    assert within_word_budget(letter_data, word_budget=count - 1) is False
