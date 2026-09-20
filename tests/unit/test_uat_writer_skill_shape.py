# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""F-9 (#672 line 126) — the delivered skills list's SHAPE gets a reader.

The producer was named on the real path first (``services/skill_shape.py``'s docstring
carries the table): a writer-minted noun phrase that is neither a vault skill nor a JD
echo is a no-op for every pass in the skills pipeline, so the writer's own ``skills``
output is the producer, ``prompts/cv_tailoring.py`` rule 7 already forbids it, and the
reviewer was explicitly told not to look. Category C: the rule exists, the reader did not.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.skill_shape import (  # noqa: E402
    prose_derived_skills,
    render_skill_shape_check_block,
    skill_shape_reviewer_prompt_fn,
)

_BULLET = "Acted as System Owner and ran vendor selection for an enterprise-scale rollout."
_SUMMARY = "Owned the AI automation use case portfolio and used Databricks daily."


def _doc(skills):
    return {
        "summary": _SUMMARY,
        "skills": list(skills),
        "work_history": [{"bullets": [_BULLET, "Validated the LIMS with Databricks."]}],
    }


def _profile(*names, technologies=("Azure",)):
    return {
        "skills": [{"name": n, "status": "confirmed"} for n in names],
        "work_experience": [{"id": "w1", "technologies": list(technologies)}],
    }


# --- the fact ---------------------------------------------------------------


def test_a_phrase_lifted_from_a_bullet_with_no_vault_tie_is_reported():
    found = prose_derived_skills(
        _doc(["System Owner", "vendor selection", "Databricks"]),
        _profile("Databricks", "Computerised System Validation"),
    )
    assert [s for s, _ in found] == ["System Owner", "vendor selection"]
    # The prose fragment travels with it, so the reviewer can judge in context.
    assert all(source == _BULLET for _, source in found)


def test_an_attested_vault_skill_is_never_reported_however_often_it_is_narrated():
    """The asserted baseline: `Databricks` is in every bullet AND in the summary, and is
    exactly the entry that must not be flagged."""
    found = prose_derived_skills(_doc(["Databricks"]), _profile("Databricks"))
    assert found == []


def test_a_work_entry_technology_counts_as_an_attested_vault_form():
    """#386: technologies are vault data too, and the tie pool here is THE same one
    `_drop_ungrounded_jd_echo_skills` uses (ADR-066)."""
    doc = _doc(["Azure"])
    doc["work_history"][0]["bullets"].append("Ran the platform on Azure.")
    assert prose_derived_skills(doc, _profile("Databricks")) == []


def test_an_entry_that_is_not_in_the_documents_prose_is_not_reported():
    """The scan is about LIFTING, not about grounding — an entry the document does not
    narrate is a different question (checks 1/3 own it)."""
    found = prose_derived_skills(_doc(["Kubernetes"]), _profile("Databricks"))
    assert found == []


def test_the_skills_list_itself_is_not_part_of_the_prose_corpus():
    """Every entry occurs in the skills list by construction; if the corpus included it,
    the scan would report the whole list."""
    doc = {"summary": "", "skills": ["Rust", "Go"], "work_history": []}
    assert prose_derived_skills(doc, _profile("Databricks")) == []


def test_presence_is_the_shared_normalisation_not_a_raw_substring():
    doc = _doc(["system-owner"])
    assert [s for s, _ in prose_derived_skills(doc, _profile("Databricks"))] == ["system-owner"]


# --- the block --------------------------------------------------------------


#: The founder-UAT ledger shape: a row whose surface forms are JD noun phrases, of which
#: `_restore_narrative_named_skills` appends ONE while the presence test matched ANOTHER.
_LEDGER = [
    {"concept": "Roadmap & Budget Ownership", "surface_forms": ["roadmap", "budget estimation"],
     "claimable": True, "status": "direct", "sources": ["required"]},
    {"concept": "AI Automation Delivery", "surface_forms": ["AI automation use case", "LLMOps"],
     "claimable": True, "status": "partial", "sources": ["required"]},
]


def test_a_chip_placed_by_a_sibling_form_of_its_own_ledger_row_is_reported():
    """Measured on the real-provider replay of 2026-09-20, n=3: the delivered list carried
    `roadmap` and `AI automation use case` in 3 of 3 runs, neither was in the writer's own
    draft, and the verbatim-only scan reported NOTHING — because
    `_restore_narrative_named_skills` appends one surface form of a ledger row when ANY
    form of that row is present in the narrative. The fact has to ask the producer's own
    question or it cannot see the producer's own output."""
    doc = {
        "summary": "Quality systems lead.",
        "skills": ["roadmap", "AI automation use case", "Databricks"],
        "work_history": [{"bullets": [
            "Owned budget estimation for the validation programme.",
            "Delivered LLMOps tooling for the group.",
            "Used Databricks daily.",
        ]}],
    }
    found = prose_derived_skills(doc, _profile("Databricks"), _LEDGER)
    assert [s for s, _ in found] == ["roadmap", "AI automation use case"]
    # The quoted fragment is the bullet the producer's presence test actually matched.
    assert "budget estimation" in found[0][1]
    assert "LLMOps" in found[1][1]


def test_without_a_ledger_the_scan_is_the_verbatim_subset():
    """The asserted baseline for the arm above, and the back-compat contract: no ledger,
    no sibling arm, and a strict subset of the population."""
    doc = {
        "summary": "Quality systems lead.",
        "skills": ["roadmap", "AI automation use case", "Databricks"],
        "work_history": [{"bullets": ["Owned budget estimation for the validation programme."]}],
    }
    assert prose_derived_skills(doc, _profile("Databricks")) == []


def test_a_sibling_form_never_overrides_a_vault_tie():
    """A chip the vault attests is out of this check's business whatever the ledger says."""
    doc = {
        "summary": "",
        "skills": ["roadmap"],
        "work_history": [{"bullets": ["Owned budget estimation for the programme."]}],
    }
    assert prose_derived_skills(doc, _profile("Platform Roadmap"), _LEDGER) == []


def test_the_block_states_the_fact_and_hands_the_judgement_over():
    block = render_skill_shape_check_block(
        prose_derived_skills(_doc(["System Owner"]), _profile("Databricks"))
    )
    assert "SKILLS-LIST SHAPE" in block
    assert "a fact, not a verdict" in block
    assert '"System Owner"' in block
    # The wording must not overstate the scan: with the sibling arm the chip itself need
    # not be verbatim in the prose, and the block says which of the two it is.
    assert "the phrase itself, or another form of the same Keyword Ledger row" in block


def test_no_finding_means_no_block():
    assert render_skill_shape_check_block([]) == ""
    fn = skill_shape_reviewer_prompt_fn(lambda s, d: "BASE", _profile("Databricks"))
    assert fn("src", _doc(["Databricks"])) == "BASE"


def test_the_named_entries_are_bounded_and_the_rest_are_counted():
    entries = [(f"entry {i}", "from somewhere") for i in range(9)]
    block = render_skill_shape_check_block(entries)
    assert block.count("  - ") == 6
    assert "and 3 more of the same shape" in block


def test_a_raising_document_view_leaves_the_reviewer_prompt_untouched():
    def boom(_draft):
        raise RuntimeError("compose exploded")

    fn = skill_shape_reviewer_prompt_fn(
        lambda s, d: "BASE", _profile("Databricks"), structured_document_fn=boom
    )
    assert fn("src", _doc(["System Owner"])) == "BASE"


# --- the check that reads it ------------------------------------------------


def test_check_12_exists_on_the_terminal_door_only_and_is_blocking():
    from applire.prompts.review_cv_tailoring import (
        REVIEW_SYSTEM_PROMPT,
        TERMINAL_REVIEW_SYSTEM_PROMPT,
    )

    assert "12. SKILLS-LIST SHAPE" in TERMINAL_REVIEW_SYSTEM_PROMPT
    # The prose door's skills list is pre-pipeline, so the fact would describe a list
    # that never ships.
    assert "SKILLS-LIST SHAPE" not in REVIEW_SYSTEM_PROMPT
    mandate = TERMINAL_REVIEW_SYSTEM_PROMPT.split("WHAT IS BLOCKING IN THIS PASS:")[1]
    assert "EXCEPT checks 2, 9\nand 10" in mandate
    assert "Checks 1,\n3-8, 11 and 12 are whether this CV tells the truth" in mandate


def test_the_block_says_check_12_does_not_contradict_rule_7s_closing_line():
    """Rule 7 requires a competence named in a bullet to appear in the skills list, which
    is exactly the population this scan covers. The instruction must say so, or the two
    rules fight (ADR-062 clause 4). It lives in the BLOCK, not in the system prompt — the
    prompt-size ratchet is why."""
    block = render_skill_shape_check_block(
        prose_derived_skills(_doc(["System Owner"]), _profile("Databricks"))
    )
    assert "raise check 12 as BLOCKING with ONE issue per entry" in block
    assert "DOES\nname a competence stays on the page" in " ".join(block.split("\n")[:1]) or (
        "DOES name a competence stays on the page" in block
    )
    assert "not listed below is never a check-12 finding" in block


# --- the measured bound on what a deterministic fix could have done ---------


def test_the_echo_drop_cannot_remove_a_single_token_tag_and_that_is_on_purpose():
    """Recorded because it bounds the alternative. `ats_audit.skills_page_dupe` counts
    bare single-token containment as a vault tie, so `Engineering` ties to
    `Requirements Engineering` and the #250 echo-drop keeps it even as a JD echo.
    Tightening that is not available: the same rule is what makes `Python` tie to
    `Python 3`, and telling those two cases apart is a judgement (ADR-062 clause 1)."""
    from applire.services.ats_audit import skills_page_dupe

    assert skills_page_dupe("Engineering", "Requirements Engineering")
    assert skills_page_dupe("Python", "Python 3")
    # Which is why the shape question goes to the reviewer instead.
    found = prose_derived_skills(
        {"summary": "Ran requirements engineering for three sites.",
         "skills": ["Engineering"], "work_history": []},
        _profile("Requirements Engineering"),
    )
    assert found == [], "an entry WITH a vault tie is never this check's business"
