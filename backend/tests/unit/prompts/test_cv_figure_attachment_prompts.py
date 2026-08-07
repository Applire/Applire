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

"""#375 — a grounded figure may not be reattached to a stronger predicate.

The defect, reproduced verbatim from ``backend/logs/llm`` across three real-provider
charter runs on the same vault sentence::

    vault      "Produktions- und Betriebsleiter mit 14 Jahren Erfahrung in der
                diskreten Fertigung"
    2026-07-29 "Erfahrener Produktionsleiter mit 14 Jahren Führungserfahrung in der
                diskreten Fertigung"          (cv_tailoring generator, attempt 1)
    2026-08-02 "Erfahrener Produktionsleiter mit über 12 Jahren Führungserfahrung in
                der diskreten Fertigung"      (cv_tailoring generator, attempt 1)
    2026-08-06 "Erfahrener Produktionsleiter mit 14 Jahren Expertise in der diskreten
                Fertigung"                    (run 17, on prompt v8 — this branch's base)

TRIAGE (applire-prompt-first step 1) — **category B, twice, and NOT category C**:

* The writer was never asked. Rule 1 governs the NUMBER ("never infer, round or invent
  a number") and the number was faithfully kept every time. Rule 6's enumerated list —
  team size, budget, scope, user count, seniority of role — does not contain "the noun a
  quantity is a quantity of". No rule in the prompt asked the writer to keep a figure
  attached to the predicate the profile attaches it to.
* The reviewer was never asked either, and its miss is a SCOPE gap rather than a missing
  check: check 5 (oversell) read "Flag **bullets** that overstate…", and the WHAT IS
  BLOCKING paragraph listed "summary phrasing" as minor BY DEFINITION. Across the three
  runs above the reviewer produced ~40 findings over 15 rounds and **not one named the
  summary** — every finding addressed ``work[i].bullets[j]`` or ``skills``. The defect
  sat in the summary, in the reviewer's own input, verbatim, and was out of mandate.

Why there is no deterministic half (ADR-062 clause 1): deciding whether
"Führungserfahrung" is the predicate the vault attaches "14 Jahre" to requires reading
prose for meaning — a JUDGEMENT. The Oracle's tenure floor (3db6b571, #469/#403) states
this boundary in its own docstring: the derivable-span ceiling is a valid upper bound for
any domain subset, and domain-scoped inflation BELOW that total "needs 'which roles count
toward domain X', a JUDGEMENT under ADR-062 clause 1". 14 <= the vault's ~22-year span,
so the ceiling passes and must. No fact-grade check is available, so none is written.

No input threading either: the vault sentence is ALREADY in both prompts verbatim (the
writer gets CANDIDATE PROFILE, the reviewer gets it as source of truth). Only the rule
was missing.

ADR-062 clause 7: these are prompt-effect changes. CI pins the wording; whether the model
obeys needs charter-run verification.
"""


def _writer_prompt() -> str:
    from applire.prompts.cv_tailoring import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def _reviewer_prompt() -> str:
    from applire.prompts.review_cv_tailoring import REVIEW_SYSTEM_PROMPT

    return REVIEW_SYSTEM_PROMPT


# --------------------------------------------------------------------------
# Writer (services/cv.py -> prompts/cv_tailoring.SYSTEM_PROMPT)
# --------------------------------------------------------------------------


def test_writer_states_that_a_figure_keeps_its_own_subject():
    """The invariant the three runs broke: number and predicate move together."""
    prompt = _writer_prompt()
    assert "A FIGURE KEEPS ITS OWN SUBJECT" in prompt
    lowered = prompt.lower()
    assert "quantity of" in lowered, (
        "the rule must name WHAT the figure is a quantity of, not just the figure"
    )
    assert "move together" in lowered


def test_writer_carries_the_captured_run_pair_as_its_worked_example():
    """The vault sentence and both observed corruptions are pinned verbatim.

    A future edit that generalises the rule into abstraction loses the one thing that
    made it legible — that the number stayed right. Replay evidence, not decoration.
    """
    prompt = _writer_prompt()
    assert "14 Jahren Erfahrung in der diskreten Fertigung" in prompt
    assert "14 Jahren Führungserfahrung" in prompt
    assert "14 Jahren Expertise" in prompt


def test_writer_names_the_defensible_number_as_the_danger():
    """The issue's core point: every truthfulness check stays green, which is what
    makes the shape dangerous rather than harmless."""
    lowered = _writer_prompt().lower()
    assert "defensible" in lowered
    assert "interview" in lowered


def test_writer_claim_strength_rule_is_not_scoped_to_bullets_alone():
    """Rule 6 read 'a bullet drawn from a truthful source…' — and the defect landed in
    the summary, the one piece of prose rule 6 could be read to exclude."""
    prompt = _writer_prompt()
    rule6 = prompt.split("6. CLAIM STRENGTH")[1].split("\n7. ")[0]
    assert "summary" in rule6.lower(), (
        "CLAIM STRENGTH must visibly cover the summary, not only bullets"
    )


def test_writer_summary_rule_forbids_relabelling_a_fact_into_a_ledger_concept():
    """Rule 5 ('lead with the KEYWORD LEDGER's top claimable concepts') is the pressure
    that produced the defect: 'Führung' was the JD's top concept. The counterweight
    belongs where the pressure is created, not one rule away."""
    prompt = _writer_prompt()
    rule5 = prompt.split("5. SUMMARY")[1].split("\n6. ")[0]
    lowered = rule5.lower()
    assert "re-labelling" in lowered or "relabelling" in lowered
    assert "rule 6" in lowered, "the counterweight must point at the rule that owns it"


# --------------------------------------------------------------------------
# Reviewer (services/cv.py -> prompts/review_cv_tailoring.REVIEW_SYSTEM_PROMPT)
# --------------------------------------------------------------------------


def test_reviewer_oversell_check_covers_the_summary():
    """15 rounds, ~40 findings, zero on the summary — because check 5 said 'bullets'.

    Asserted on the check's OWN scope sentence, not on the check as a whole: the
    reattached-figure paragraph below also says "summary", and a mutation reverting the
    scope line alone must not be able to hide behind it.
    """
    prompt = _reviewer_prompt()
    check5 = prompt.split("5. OVERSTATED CLAIM STRENGTH")[1].split("\n6. ")[0]
    scope_sentence = check5.split("INCLUDING A REATTACHED FIGURE")[0]
    assert "SUMMARY" in scope_sentence, (
        "the oversell check must reach the summary in its own scope line, where #375's "
        "defect landed — not only in the figure paragraph"
    )
    assert "Flag bullets" not in scope_sentence, (
        "the bullets-only scope is the reason 15 review rounds never looked at the summary"
    )


def test_reviewer_oversell_check_names_the_reattached_figure_case():
    prompt = _reviewer_prompt()
    check5 = prompt.split("5. OVERSTATED CLAIM STRENGTH")[1].split("\n6. ")[0]
    assert "REATTACHED FIGURE" in check5
    assert "quantity of" in check5.lower()
    # The captured pair, so the reviewer is told what "the number verifies" looks like.
    assert "14 Jahren Erfahrung in der diskreten Fertigung" in check5
    assert "14 Jahren Führungserfahrung" in check5


def test_reviewer_no_longer_classes_every_summary_defect_as_minor_by_definition():
    """ADR-062 clause 4 in miniature: check 5 asking for the summary while the BLOCKING
    paragraph lists 'summary phrasing' as minor BY DEFINITION is one prompt contradicting
    itself about one location. The minor entry is narrowed, not deleted — genuine
    phrasing nits stay minor."""
    prompt = _reviewer_prompt()
    blocking = prompt.split("WHAT IS BLOCKING IN THIS PASS")[1]
    assert "summary phrasing," not in blocking, (
        "the unqualified 'summary phrasing' entry contradicts the widened check 5"
    )
    assert "summary phrasing that does not change what is claimed" in blocking


def test_reviewer_does_not_gain_a_seventh_numbered_check():
    """ADR-062 Consequences: fewer, more general rules beat many specific ones, and this
    reviewer's blocking surface is being DE-escalated (ADR-071 amended 2026-08-06 demoted
    check 2 to visibility-only after run 17 exhausted the loop on 13 false findings).
    #375 is a scope correction of an existing check, never a new mandate."""
    prompt = _reviewer_prompt()
    assert "\n7. " not in prompt.split("WHAT IS BLOCKING IN THIS PASS")[0], (
        "#375 must not add a numbered check to the CV reviewer"
    )


# --------------------------------------------------------------------------
# Segmented writer path (ADR-066 / ADR-067 parity)
# --------------------------------------------------------------------------


def test_segmented_core_rules_carry_the_same_figure_subject_rule():
    """One logical operation, one contract: the two writer paths may not diverge on a
    truthfulness rule. #289 established this for its own clause in the same flavour;
    #375's rule sits in the same claim-strength bullet, for the same reason."""
    from applire.prompts.cv_segmented import _CORE_RULES

    low = _CORE_RULES.lower()
    assert "keeps its own subject" in low
    assert "quantity" in low
    assert "führungserfahrung" in low


# --------------------------------------------------------------------------
# The #289 + #375 fold (ADR-062 clause 4 — one prompt, one voice)
# --------------------------------------------------------------------------


def test_writer_states_the_never_drop_boundary_exactly_once_for_both_clauses():
    """#289's clause and #375's clause are both "a true figure, wrongly written". Left as
    two paragraphs each with its own closer, the prompt would say one thing twice
    differently — and #375's "the number and its subject move together, or neither moves"
    reads as licence to DROP a figure, the very inverse defect (#283) that #289's closer
    exists to prevent. One shared closer, stated once, governing both."""
    prompt = _writer_prompt()
    rule6 = prompt.split("6. CLAIM STRENGTH")[1].split("\n7. ")[0]
    assert rule6.count("never a reason to drop the figure") == 1
    assert rule6.count("opposite defect") == 1
    # …and it must sit AFTER both clauses, or it governs only the first.
    closer = rule6.index("never a reason to drop the figure")
    assert rule6.index("A FIGURE KEEPS ITS OWN SUBJECT") < closer
    assert rule6.index("AGENCY, NOT PROXIMITY") < closer


def test_reviewer_reads_every_figure_against_its_source_from_one_stem():
    """Both additions opened with the same instruction — "find the profile statement it
    comes from and read X". Two stems for one act of reading is ADR-062 clause 4 inside a
    single check; they are folded into one stem with two questions."""
    prompt = _reviewer_prompt()
    check5 = prompt.split("5. OVERSTATED CLAIM STRENGTH")[1].split("\n6. KEYWORD LEDGER")[0]
    assert "EVERY FIGURE IS READ AGAINST THE PROFILE STATEMENT IT COMES FROM" in check5
    assert check5.lower().count("find the profile statement") == 1, (
        "the reviewer must be told to find the source statement ONCE, then ask it both "
        "questions — not once per issue"
    )
    assert check5.count("NEVER ask for the figure to be removed") == 1
    # Both questions must survive the fold as named, separable items. Folding two
    # instructions into one stem is only safe while each is still individually visible;
    # otherwise the next edit drops one and every pin that reads the check as a whole
    # still passes on the other's wording.
    assert "- A REATTACHED FIGURE (#375)" in check5
    assert "- AGENCY vs PROXIMITY (#289)" in check5


# --------------------------------------------------------------------------
# Prompt health (applire-prompt-first step 3)
# --------------------------------------------------------------------------


def test_writer_prompt_stays_smaller_than_its_reviewer():
    """The 2026-07-30 audit's calibration: a writer prompt larger than its reviewer is
    the accretion smell that produced the E049 rebuild. Base: writer 6655 / reviewer
    7381 chars."""
    assert len(_writer_prompt()) < len(_reviewer_prompt())
