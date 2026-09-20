# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""F-5 (#672 line 124) — the signature story's measured figure gets a READER.

The triage that produced this module rather than a louder prompt rule is in
``services/story_reach.py``'s docstring: rule 3 of ``prompts/cv_tailoring.py`` already
demands every story's measured outcome figure, the story reaches the writer's input in
full (profile root, unfiltered), and the rule was violated anyway — category C, with the
positive set of possible readers exhausted and empty. This file pins the FACT the
reviewer is now given, and the bounds on it.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.story_reach import (  # noqa: E402
    DEMANDABLE_FIGURE_KINDS,
    STORY_DEMAND_LIMIT,
    figures_missing_from,
    render_story_figures_check_block,
    story_figures,
    story_figures_reviewer_prompt_fn,
)

_WORK_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _profile(*stories):
    return {
        "work_experience": [
            {"id": _WORK_ID, "company": "Pharmavit GmbH", "role": "QA Systems Lead"}
        ],
        "signature_stories": list(stories),
    }


def _story(**kw):
    base = {
        "id": "s1",
        "title": "LIMS rollout across three sites",
        "challenge": "Three parallel validation strategies were planned.",
        "mechanism": "One shared validation strategy.",
        "outcome": "Validation effort fell by roughly 80 % and the first site went live.",
        "experience_refs": [_WORK_ID],
    }
    base.update(kw)
    return base


def _doc(*texts):
    return {"summary": texts[0] if texts else "", "work_history": [{"bullets": list(texts[1:])}]}


# --- the fact ---------------------------------------------------------------


def test_a_storys_measured_percent_is_a_demandable_figure_with_its_owning_entry():
    figures = story_figures(_profile(_story()))
    assert len(figures) == 1
    figure = figures[0]
    assert (figure.kind, figure.value) == ("percent", "80")
    assert figure.title == "LIMS rollout across three sites"
    # The entry is looked up through `experience_refs` — a story is anchored to its
    # experience, never nested in it (ADR-044).
    assert figure.entry_label == "Pharmavit GmbH — QA Systems Lead"


def test_only_percent_and_currency_are_demanded():
    """A year is a date and a bare number is noise; demanding either would spend a
    blocking round on something no reader treats as evidence."""
    assert DEMANDABLE_FIGURE_KINDS == frozenset({"percent", "currency"})
    figures = story_figures(_profile(
        _story(id="s2", outcome="Went live in 2024 with 3 teams and 7 months of runway."),
    ))
    assert figures == []


def test_only_the_outcome_field_is_read():
    """`benchmark` is the comparison the candidate drew and `challenge`/`mechanism` are
    setting and method — a figure there is not the story's measured outcome, and
    demanding it would put a number on the page the story does not claim."""
    figures = story_figures(_profile(_story(
        outcome="The strategy was adopted.",
        challenge="Validation effort was running at 140 % of plan.",
        benchmark="Industry practice is 50 % rework.",
    )))
    assert figures == []


def test_a_story_with_an_unresolvable_experience_ref_still_yields_its_figure():
    figures = story_figures(_profile(_story(experience_refs=["not-a-real-id"])))
    assert len(figures) == 1
    assert figures[0].entry_label == "the entry this story belongs to"


def test_presence_is_figure_identity_not_string_matching():
    """`80 %` and `80%` are the same figure to the canonical extractor, so a demand keyed
    on the literal would be raised against a document that already carries the number."""
    figures = story_figures(_profile(_story()))
    assert figures_missing_from(figures, _doc("Nothing quantified here.")) == figures
    assert figures_missing_from(figures, _doc("Cut validation effort by 80%.")) == []
    # The stated limit of the canonical extractor: a spelled-out unit ("80 Prozent") is
    # NOT a percent figure to it, so the demand can be raised against a document that
    # does carry the number in words. Pinned as the known false-positive direction rather
    # than hidden — the remedy is one more bullet, never a deleted fact, and the block
    # tells the corrector never to restate a figure the document already carries.
    assert figures_missing_from(figures, _doc("Cut effort by 80 Prozent.")) == figures


def test_a_figure_only_in_a_vault_joined_section_still_counts_as_present():
    """The scan runs over the COMPOSED document, so a bullet the deterministic tail
    assembled (`_nest_projects`) is not invisible to it (#659's class)."""
    figures = story_figures(_profile(_story()))
    composed = {
        "summary": "QA lead.",
        "work_history": [{"bullets": [], "projects": [{"bullets": ["80 % less validation effort"]}]}],
    }
    assert figures_missing_from(figures, composed) == []


# --- the block --------------------------------------------------------------


def test_the_block_is_a_complete_statement_not_a_prohibition():
    figures = story_figures(_profile(_story()))
    block = render_story_figures_check_block(present=figures, demand=[], already=[])
    assert "PRESENT" in block and "MISSING" not in block
    assert "LIMS rollout across three sites" in block


def test_no_stories_means_no_block_at_all():
    assert render_story_figures_check_block(present=[], demand=[], already=[]) == ""
    fn = story_figures_reviewer_prompt_fn(lambda s, d: "BASE", _profile())
    assert fn("src", _doc("x")) == "BASE"


def test_the_wrapper_demands_each_figure_at_most_once_per_invocation():
    seen = []
    fn = story_figures_reviewer_prompt_fn(
        lambda s, d: "BASE", _profile(_story()), on_demand=seen.append
    )
    first = fn("src", _doc("Nothing quantified."))
    second = fn("src", _doc("Still nothing quantified."))
    assert "MISSING — raise check 11 as BLOCKING" in first
    assert "ALREADY DEMANDED" in second
    assert "MISSING — raise check 11 as BLOCKING" not in second
    assert len(seen) == 1, "on_demand fires once per figure per invocation"


def test_the_demand_count_per_round_is_bounded():
    stories = [
        _story(id=f"s{i}", title=f"Story {i}", outcome=f"Effort fell by {10 + i} %.")
        for i in range(STORY_DEMAND_LIMIT + 2)
    ]
    fn = story_figures_reviewer_prompt_fn(lambda s, d: "BASE", _profile(*stories))
    block = fn("src", _doc("Nothing quantified."))
    demand_section = block.split("MISSING — raise check 11")[1].split("ALREADY DEMANDED")[0]
    assert demand_section.count("  - ") == STORY_DEMAND_LIMIT


# --- fail-safe --------------------------------------------------------------


def test_a_raising_document_view_leaves_the_reviewer_prompt_untouched():
    def boom(_draft):
        raise RuntimeError("compose exploded")

    fn = story_figures_reviewer_prompt_fn(
        lambda s, d: "BASE", _profile(_story()), structured_document_fn=boom
    )
    assert fn("src", _doc("x")) == "BASE"


def test_a_malformed_profile_never_raises():
    fn = story_figures_reviewer_prompt_fn(lambda s, d: "BASE", {"signature_stories": "nope"})
    assert fn("src", _doc("x")) == "BASE"


# --- the check that reads it ------------------------------------------------


def test_check_11_exists_on_the_terminal_door_only_and_is_blocking():
    from applire.prompts.review_cv_tailoring import (
        REVIEW_SYSTEM_PROMPT,
        TERMINAL_REVIEW_SYSTEM_PROMPT,
    )

    assert "11. SIGNATURE STORY FIGURES" in TERMINAL_REVIEW_SYSTEM_PROMPT
    # The prose door has no composed document to scan, so the check must not reach it.
    assert "SIGNATURE STORY FIGURES" not in REVIEW_SYSTEM_PROMPT
    # The mandate excludes 2, 9 and 10 from blocking — never 11.
    mandate = TERMINAL_REVIEW_SYSTEM_PROMPT.split("WHAT IS BLOCKING IN THIS PASS:")[1]
    assert "EXCEPT checks 2, 9\nand 10" in mandate
    assert "Checks 1,\n3-8, 11 and 12 are whether this CV tells the truth" in mandate
    # The check itself is a NAME plus a pointer; the instruction lives in the per-round
    # block, which is input and costs nothing against the prompt-size ratchet.
    assert "answered from the" in TERMINAL_REVIEW_SYSTEM_PROMPT.split(
        "11. SIGNATURE STORY FIGURES")[1][:200]


def test_the_skills_list_scope_paragraph_no_longer_exempts_rule_7s_shape_rule():
    """F-9's contradiction sweep: the paragraph read as a blanket 'never flag a grounded
    skill', which covered a noun phrase lifted from a bullet and so contradicted writer
    rule 7 across the seam (ADR-062 clause 4). Narrowed IN PLACE, not prefixed — the
    prompt-size ratchet asks for replacement, not appending."""
    from applire.prompts.review_cv_tailoring import TERMINAL_REVIEW_SYSTEM_PROMPT

    scope = TERMINAL_REVIEW_SYSTEM_PROMPT.split("SKILLS-LIST SCOPE")[1]
    flat = " ".join(scope.split())
    assert "Never flag such a skill as fabricated, ungrounded, or a certification" in flat
    assert "whether it is a SKILL at all is check 12's question" in flat


def test_the_prose_reviewer_prompt_stays_under_its_size_gate():
    from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT

    assert len(REVIEW_SYSTEM_PROMPT) < 12_900
