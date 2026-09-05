# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#532 / ADR-075 — a JD-relevant stated limit is REQUIRED content, not only a constraint.

**The positive set, exhausted before building anything** (`applire-prompt-first`
step 1; the 2026-08-13 finding re-verified against the code on 2026-09-05). Every
channel that carries `stated_limits` / `denied_concepts` into a letter prompt:

| channel | direction |
|---|---|
| `cross_document.render_stated_limits_block` (writer user prompt) | constraining |
| the writer SYSTEM_PROMPT's `STATED LIMITS` rule | constraining |
| `positioning_requested["stated_limits"]` (the loop's `source`) | constraining, **no `required` flag** |
| reviewer check 4's enumeration | did not list `stated_limits` at all |
| the writer's `EVERY UNMET JD HARD REQUIREMENT` rule (#270) | affirmative, but gated on the **`required`** tier |
| `render_unaddressed_hard_requirements_block` | affirmative, gated on `"required" in sources` |
| `positioning_requested["gap_transfer_argument"]` | needs a Signature Story (ADR-055) |

So an affirmative obligation existed for a `required` gap and for nothing else.
IFS / BRC / Lebensmittelindustrie are `nice_to_have_skills`, which is why gate
charter run 1's disclosure — the single property BOTH blind panel reviewers named
as their reason for `ja` — was produced **reactively**, by the "never manufacture
a limit" rule repairing an invented one, and why the 2026-08-13 letter shipped
silent (#532's own measurement, unchanged).

What is built here, per ADR-075 clauses 1–3 and ADR-083 clause 1:

* selection is a FACT — `select_jd_relevant_limits`, a persisted `denied_concepts`
  statement whose concept has a `status == "denied"` row in THIS job's ledger,
  ranked by that row's `fit_weight`, capped at 3, truncation logged;
* the obligation is carried by THREE hooks, because ADR-021's own amendment
  measured that `required: true` alone is not self-enforcing, and ADR-083
  measured a role framing at 0/5 against a named check's 5/5;
* the trap is stated in the same breath — a limit the entry does NOT list may not
  be invented, which is check 1's own INVENTED LIMIT bullet.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import logging  # noqa: E402

from applire.prompts import cover_letter as writer  # noqa: E402
from applire.prompts import review_cover_letter as reviewer  # noqa: E402
from applire.services.cross_document import (  # noqa: E402
    render_stated_limits_block,
    select_jd_relevant_limits,
)


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


# ── the gate-charter-run-1 shape, in miniature ──────────────────────────────

DENIED = [
    {"concept": "IFS", "statement": "Mit IFS oder BRC habe ich keine Erfahrung — das "
                                    "ist eine ehrliche Lücke. Zehn Jahre ISO-9001-"
                                    "Auditpraxis bringe ich aber mit."},
    {"concept": "US-GAAP", "statement": "US-GAAP habe ich nie angewendet."},
    {"concept": "Vertrieb", "statement": "Im Vertrieb war ich nie tätig."},
    {"concept": "Kantine", "statement": "Kantinenplanung habe ich nie gemacht."},
]

LEDGER = [
    {"concept": "IFS", "status": "denied", "fit_weight": 1.0},
    {"concept": "US-GAAP", "status": "denied", "fit_weight": 0.5},
    {"concept": "Vertrieb", "status": "denied", "fit_weight": 0.25},
    {"concept": "Kantine", "status": "denied", "fit_weight": 0.1},
    # a claimable row for a concept the candidate never denied
    {"concept": "ISO 9001", "status": "direct", "fit_weight": 1.0},
]


# ── 1. selection is a fact, ranked and capped ───────────────────────────────


def test_only_denials_this_posting_asks_about_are_selected():
    """A denial with no ledger row for THIS job is not selected. Without the
    filter the letter would import unrelated denials — the difference between
    "the candidate said no" and "the candidate said no about something this
    employer asked for"."""
    selected = select_jd_relevant_limits(
        DENIED + [{"concept": "Segelfliegen", "statement": "Segeln kann ich nicht."}],
        LEDGER,
    )
    assert all("Segeln" not in s for s in selected)


def test_selection_is_ranked_by_the_ledgers_own_fit_weight_and_capped_at_three():
    """ADR-075 clause 4: at most 3, ranked by `fit_weight`, mirroring
    `_MAX_UNADDRESSED_REPORTED`. A candidate with many denials must not turn
    their letter into a list of things they cannot do."""
    selected = select_jd_relevant_limits(DENIED, LEDGER)
    assert len(selected) == 3
    assert "IFS" in selected[0]
    assert "US-GAAP" in selected[1]
    assert all("Kantine" not in s for s in selected)


def test_the_truncation_is_logged(caplog):
    caplog.set_level(logging.INFO, logger="applire.services.cross_document")
    select_jd_relevant_limits(DENIED, LEDGER)
    lines = [r.getMessage() for r in caplog.records if "LIMIT" in r.getMessage()]
    assert lines, [r.getMessage() for r in caplog.records]
    assert "dropped=1" in lines[0]


def test_a_claimable_concept_is_never_selected_even_if_a_denial_names_it():
    """The ledger's own status is the arbiter, never text overlap. An honest
    denial NAMES the adjacent strengths that transfer ("no IFS/BRC experience,
    but ten years of ISO-9001 audit practice") — the deleted
    `find_scoped_boundaries` answered exactly this question backwards on real
    data (charter run #8, four boundaries emitted, all four false)."""
    selected = select_jd_relevant_limits(DENIED, LEDGER)
    assert all("ISO 9001" != s for s in selected)
    # the IFS statement is selected AND mentions ISO 9001 — the strength rides
    # along inside the candidate's own words, which is the point.
    assert any("ISO-9001" in s for s in selected)


def test_no_denials_or_no_ledger_selects_nothing():
    assert select_jd_relevant_limits([], LEDGER) == []
    assert select_jd_relevant_limits(DENIED, []) == []
    assert select_jd_relevant_limits(None, None) == []


def test_the_shared_writer_block_is_unchanged_so_the_cv_is_unaffected():
    """`render_stated_limits_block` is shared with the CV writer
    (`services/cv.py:2788`). Writer rule 3a says a CV is not the place to
    disclose a gap, so the AFFIRMATIVE half must NOT live in the shared block —
    it lives in the letter-only channels. Pinned so a later edit cannot leak
    the obligation onto the CV."""
    block = render_stated_limits_block(["Mit IFS habe ich keine Erfahrung."])
    low = _flat(block)
    assert "never manufacture a limit they do not state" in low
    for affirmative in ("must name", "required", "silence is not"):
        assert affirmative not in low, affirmative


# ── 2. hook one: the source entry is REQUIRED and affirmative ───────────────


def test_the_positioning_entry_is_required_and_affirmative():
    from applire.services.cover_letter import build_stated_limits_entry

    entry = build_stated_limits_entry(DENIED, LEDGER)
    assert entry is not None
    assert entry["required"] is True
    assert entry["limits"] == select_jd_relevant_limits(DENIED, LEDGER)
    low = _flat(entry["instruction"])
    # the affirmative half
    assert "positioning decision" in low
    # the trap, in the same breath
    assert "never" in low and "invent" in low


def test_no_jd_relevant_denial_means_no_required_entry():
    """A candidate whose denials this posting does not ask about owes the letter
    nothing — the entry is absent rather than present-and-empty, so check 4
    cannot demand content that does not exist."""
    from applire.services.cover_letter import build_stated_limits_entry

    assert build_stated_limits_entry(DENIED, []) is None
    assert build_stated_limits_entry([], LEDGER) is None


# ── 3. hook two: the reviewer enumerates it BY NAME (ADR-083 clause 1) ──────


def test_reviewer_check_four_enumerates_stated_limits_by_name():
    """ADR-075 clause 2 named the mechanism and ADR-021's amendment measured
    why: `required: true` alone is not self-enforcing — the deleted
    `unaddressed_hard_requirements` entry read `required: true` for ten rounds
    while the corrector dropped both its concepts. What made the OTHER entries
    work is that each is also enumerated by name here and carries its own
    corrector bullet. Both doors, because `_CHECKS` is shared."""
    for prompt in (reviewer.REVIEW_SYSTEM_PROMPT, reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT):
        p = prompt
        check4 = p[p.index("4. REQUIRED CONTENT NOT DELIVERED"):p.index("5. A DETERMINISTIC BLOCK")]
        assert "stated_limits" in check4
        low = _flat(check4)
        assert "positioning decision" in low
        # The reviewer must also be told the INVERSE direction, or it accepts an
        # invented limit as compliance. Check 4 states it and points at check 1 —
        # and the pointer must RESOLVE: ADR-021's 2026-08-13 clause 4 is exactly
        # the incident of a model sent to a named rule that was not there.
        assert "the entry does not list (check 1)" in low
        assert "an invented limit is an ungrounded claim" in _flat(p)


def test_the_corrector_carries_its_own_named_bullet():
    low = _flat(reviewer.COVER_LETTER_REFINEMENT_PROMPT)
    assert "stated_limits" in low
    assert "positioning decision" in low


# ── 4. hook three: the writer is told, at any requirement tier ──────────────


def test_the_writer_owes_a_positioning_decision_to_a_denied_concept_at_any_tier():
    """The tier gap is the whole point: the writer's #270 rule is affirmative
    but scoped to a `required` JD concept, and IFS/BRC were `nice_to_have`."""
    low = _flat(writer.SYSTEM_PROMPT)
    assert "denied" in low
    assert "any requirement tier" in low or "required or nice-to-have" in low
    assert "positioning decision" in low


def test_the_writer_rule_does_not_reopen_adr_074():
    """ADR-074's Restfall (a `gap` nobody was ever asked about) has no honest
    move and stays excluded. The new obligation reaches only concepts the
    candidate DENIED in their own words, which always carry substantive
    material."""
    low = _flat(writer.SYSTEM_PROMPT)
    i = low.index("stated limits")
    window = low[i:i + 2500]
    assert "own words" in window


# ── 5. the prompt-size ratchets ─────────────────────────────────────────────


def test_the_letter_reviewer_ratchets_still_hold():
    assert len(reviewer.REVIEW_SYSTEM_PROMPT) < 12_500, len(reviewer.REVIEW_SYSTEM_PROMPT)
    assert len(reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT) < 16_100, len(
        reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT
    )


# ── 6. the writer's INPUT carries what the writer's RULE names ──────────────
#
# Found by designing the replay, not by reading the code: the writer's user
# prompt renders the POSITIONING sections and `render_stated_limits_block` — it
# never carries `positioning_requested`, which is the review loop's `source`. A
# writer rule keyed on "when the positioning block marks stated_limits REQUIRED"
# would therefore name a marker the writer cannot see, and would be inert on the
# ONE call that decides whether the disclosure is drafted at all
# (`applire-prompt-first` step 3, pattern 6). The obligation reaches the writer
# as its own rendered block instead.


def test_the_affirmative_block_is_rendered_only_when_something_is_owed():
    from applire.services.cross_document import render_required_limits_block

    assert render_required_limits_block([]) == ""
    block = render_required_limits_block(["Mit IFS habe ich keine Erfahrung."])
    low = _flat(block)
    assert "required" in low
    assert "silence on one of these is not one of the options" in low
    assert "never state a limit that is not listed here" in low
    assert "Mit IFS habe ich keine Erfahrung." in block


def test_the_writer_user_prompt_carries_the_block_the_writer_rule_names():
    """The seam test. The rule says "when a REQUIRED: STATED LIMITS ... block
    appears in the user message" — so the user message must be able to carry
    one, under exactly that heading."""
    from applire.prompts.cover_letter import SYSTEM_PROMPT, build_cover_letter_prompt
    from applire.services.cross_document import render_required_limits_block

    heading = "REQUIRED: STATED LIMITS THIS POSTING ASKS ABOUT"
    assert heading in SYSTEM_PROMPT.upper() or heading.lower() in _flat(SYSTEM_PROMPT)

    prompt = build_cover_letter_prompt(
        cv_data={"contact": {"name": "A. Test"}, "summary": "Controller"},
        jd_text="Senior Controller",
        pre_gen_inputs={},
        detected_language="de",
        required_limits_block=render_required_limits_block(
            ["Mit IFS oder BRC habe ich keine Erfahrung."]
        ),
    )
    assert heading in prompt
    assert "Mit IFS oder BRC habe ich keine Erfahrung." in prompt

    # and nothing is added when nothing is owed
    empty = build_cover_letter_prompt(
        cv_data={"contact": {"name": "A. Test"}, "summary": "Controller"},
        jd_text="Senior Controller",
        pre_gen_inputs={},
        detected_language="de",
        required_limits_block="",
    )
    assert heading not in empty


def test_the_generation_path_builds_the_block_from_the_jobs_own_ledger():
    """The call-site seam: `_render_cover_letter_background` must derive the
    block from `select_jd_relevant_limits(denied_concepts, keyword_ledger)` —
    the two persisted facts — and pass it to the writer prompt."""
    import inspect

    from applire.services import cover_letter as svc

    src = inspect.getsource(svc._render_cover_letter_background)
    assert "render_required_limits_block(" in src
    assert "select_jd_relevant_limits(denied_concepts, keyword_ledger)" in src
    assert "required_limits_block=required_limits_block," in src
