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
# G-1 (2026-11-04): the REQUIRED entry is `gap_positioning` (code names the gap);
# `stated_limits` is constraint-only. The key-tier cut lives in `key_requirement_gaps`.

GAP_LEDGER = [
    {"concept": "IFS", "status": "denied", "claimable": False, "fit_weight": 1.0,
     "sources": ["required"]},
    {"concept": "US-GAAP", "status": "denied", "claimable": False, "fit_weight": 0.5,
     "sources": ["nice_to_have"]},
    {"concept": "ISO 9001", "status": "direct", "claimable": True, "fit_weight": 1.0,
     "sources": ["required"]},
]


def test_the_positioning_entry_is_required_and_affirmative():
    from applire.services.cover_letter import build_gap_positioning_entry
    from applire.services.cross_document import gaps_to_name, key_requirement_gaps

    gaps = key_requirement_gaps(GAP_LEDGER, DENIED, "IFS und ISO 9001")
    entry = build_gap_positioning_entry(gaps, gaps_to_name(gaps), "de")
    assert entry is not None
    assert entry["required"] is True
    # RULING L-1 = A/B: only the KEY-requirement gap is named, with its own statement
    assert [n["gap"] for n in entry["named"]] == ["IFS"]
    assert entry["named"][0]["statement"] == DENIED[0]["statement"]
    low = _flat(entry["instruction"])
    # the affirmative half
    assert "same sentence" in low and "einarbeiten" in low
    # the trap, in the same breath
    assert "name only the gap(s) listed" in low


def test_stated_limits_is_constraint_only_never_required():
    from applire.services.cover_letter import _constraining_stated_limits_entry

    entry = _constraining_stated_limits_entry(["Mit IFS habe ich keine Erfahrung."])
    assert entry["limits"] == ["Mit IFS habe ich keine Erfahrung."]
    assert "required" not in entry


def test_no_key_requirement_gap_means_no_required_entry():
    """A candidate whose denials this posting does not ask about at the key tier
    owes the letter nothing — the entry is absent rather than present-and-empty, so
    check 4 cannot demand content that does not exist."""
    from applire.services.cover_letter import build_gap_positioning_entry
    from applire.services.cross_document import gaps_to_name, key_requirement_gaps

    nice_only = [GAP_LEDGER[1], GAP_LEDGER[2]]  # nice-to-have denial + a claimable row
    for ledger in (nice_only, []):
        gaps = key_requirement_gaps(ledger, DENIED, "IFS US-GAAP ISO 9001")
        assert gaps == []
        assert build_gap_positioning_entry(gaps, gaps_to_name(gaps), "de") is None
    assert build_gap_positioning_entry([], [], "de") is None


# ── 3. hook two: the reviewer enumerates it BY NAME (ADR-083 clause 1) ──────


def test_reviewer_check_four_enumerates_the_required_gap_entry_by_name():
    """ADR-075 clause 2 named the mechanism and ADR-021's amendment measured
    why: `required: true` alone is not self-enforcing. What makes the entries
    work is that each is also enumerated by name here and carries its own
    corrector bullet. Both doors, because `_CHECKS` is shared. Since G-1 the
    enumerated key is `gap_positioning` (and `motivation`); `stated_limits` is a
    constraint, and the INVERSE direction (an invented limit) stays check 1's."""
    for prompt in (reviewer.REVIEW_SYSTEM_PROMPT, reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT):
        p = prompt
        check4 = p[p.index("4. REQUIRED CONTENT NOT DELIVERED"):p.index("5. A DETERMINISTIC BLOCK")]
        low = _flat(check4)
        assert "``gap_positioning``" in low and "``motivation``" in low
        assert "without its decision: block" in low   # HALF-DELIVERY (narrowed b6 follow-up)
        assert "``stated_limits`` is a constraint only" in low
        # The reviewer must also be told the INVERSE direction, or it accepts an
        # invented limit as compliance: check 1's INVENTED LIMIT bullet must exist.
        assert "an invented limit is an ungrounded claim" in _flat(p)


def test_the_corrector_carries_its_own_named_bullet():
    low = _flat(reviewer.COVER_LETTER_REFINEMENT_PROMPT)
    assert "``gap_positioning``" in low and "``motivation``" in low
    assert "never write a lack the gap ladder does not name" in low


# ── 4. hook three: the writer is told, in the system prompt ─────────────────


def test_the_writer_owes_the_gap_ladder_sentence_for_the_named_gap():
    """The obligation is carried by the KEY-REQUIREMENT GAPS block (code picks the
    gap, at the key tier only); the writer's rule says what a named gap gets."""
    low = _flat(writer.SYSTEM_PROMPT)
    assert "obligation — the gap ladder (g-1)" in low
    assert "a key-requirement gaps block names the gap(s) this letter states" in low
    assert "chosen by code" in low
    assert "same sentence" in low and "einarbeiten clause" in low


def test_the_writer_rule_does_not_reopen_adr_074():
    """ADR-074's Restfall (a `gap` nobody was ever asked about) has no honest
    move and stays excluded from the STATED LIMITS rule: the constraint half speaks
    of the candidate's own words, and everything claimable stays claimable."""
    low = _flat(writer.SYSTEM_PROMPT)
    i = low.index("stated limits")
    window = low[i:i + 2500]
    assert "own words" in window


# ── 5. the prompt-size ratchets ─────────────────────────────────────────────


def test_the_letter_reviewer_ratchets_still_hold():
    # 2026-09-20 (#732, #731): the letter's prose door moves 12,900 -> 13,450 and its terminal door 16,300 -> 16,850 ONCE, for #732's named BLOCKING half-delivery finding in check 4 and the check-5 scoping that keeps it from contradicting the admit-and-affirm protection. The merge attempt the ratchet asks for was made and it PAID: a check-1 mirror bullet for #731 (1,245 chars) was written, measured at 0/5 on the real provider, and DELETED again rather than kept (ADR-062 deletion over repair), and the writer's separate SHAPE-OF-A-NAMED-GAP rule (764 chars) was folded into the STATED LIMITS (b) rule it duplicates. Measured BEFORE the ceiling moved, luna, reviewer isolated on the founder-UAT draft shape, n=5 per arm: the unfused-shape finding raised 0/5 -> 5/5, all blocking; at the delivery point, n=2 before / n=3 after, delivered gap sentences carrying their own transfer clause 0 of 3 -> 5 of 5, disclosures 3 -> 5 (never fewer), and the blind hiring manager moved from NO to YES naming the fused form as the reason. Mapped to SF-WRITE (new row, id assigned at integration).
    # 2026-10-24 (R2-5, founder ruling T-1 = A, Hazelnut build 4): moved ONCE for the terminal doors' per-issue `note` field (+~150 chars, the same point in the candidate's words for the review card; the corrector never reads it). Replayed before the move on 5 captured round-2 terminal records (3 CV, 2 letter), luna thinking off, n=5: verdict parity held (approvals 1/25 vs 1/25, blocking counts inside the arm-A range), finish=stop 25/25, 81/81 notes in the document's language once the user prompt names it, 0 field paths.
    assert len(reviewer.REVIEW_SYSTEM_PROMPT) < 13_450, len(reviewer.REVIEW_SYSTEM_PROMPT)
    assert len(reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT) < 17_000, len(
        reviewer.TERMINAL_REVIEW_SYSTEM_PROMPT
    )


# ── 6. the writer's INPUT carries what the writer's RULE names ──────────────
#
# Found by designing the replay, not by reading the code: the writer's user
# prompt never carries `positioning_requested` (the review loop's `source`). A
# writer rule keyed on a positioning marker would be inert on the ONE call that
# decides whether the gap sentence is drafted at all (`applire-prompt-first` step
# 3, pattern 6). The obligation reaches the writer as its own rendered block.


def test_the_gap_block_is_rendered_only_when_something_is_owed():
    from applire.services.cross_document import (
        gaps_to_name,
        key_requirement_gaps,
        render_gap_ladder_block,
    )

    assert render_gap_ladder_block([], [], "de") == ""
    gaps = key_requirement_gaps(GAP_LEDGER, DENIED, "IFS")
    block = render_gap_ladder_block(gaps, gaps_to_name(gaps), "de")
    low = _flat(block)
    assert "key-requirement gaps" in low
    # RULING C-3 (2026-09-20) / G-1: the sentence is gap + transfer (or the einarbeiten
    # clause); a list of lacks and a bare admission are both banned.
    assert "in the same sentence" in low
    assert "never a list of lacks" in low
    assert "silence there is correct, never a finding" in low
    assert DENIED[0]["statement"].lower() in low


def test_the_writer_user_prompt_carries_the_block_the_writer_rule_names():
    """The seam test. The rule says "a KEY-REQUIREMENT GAPS block names the gap(s)"
    — so the user message must be able to carry one, under exactly that heading."""
    from applire.prompts.cover_letter import SYSTEM_PROMPT, build_cover_letter_prompt
    from applire.services.cross_document import (
        gaps_to_name,
        key_requirement_gaps,
        render_gap_ladder_block,
    )

    heading = "KEY-REQUIREMENT GAPS"
    assert heading in SYSTEM_PROMPT

    gaps = key_requirement_gaps(GAP_LEDGER, DENIED, "IFS")
    prompt = build_cover_letter_prompt(
        cv_data={"contact": {"name": "A. Test"}, "summary": "Controller"},
        jd_text="Senior Controller",
        pre_gen_inputs={},
        detected_language="de",
        gap_ladder_block=render_gap_ladder_block(gaps, gaps_to_name(gaps), "de"),
    )
    assert heading in prompt
    assert DENIED[0]["statement"] in prompt

    # and nothing is added when nothing is owed
    empty = build_cover_letter_prompt(
        cv_data={"contact": {"name": "A. Test"}, "summary": "Controller"},
        jd_text="Senior Controller",
        pre_gen_inputs={},
        detected_language="de",
        gap_ladder_block="",
    )
    assert heading not in empty


def test_the_generation_path_builds_the_block_from_the_jobs_own_ledger():
    """The call-site seam: the render body must derive the gaps from the two
    persisted facts (`key_requirement_gaps(keyword_ledger, denied_concepts, ...)`)
    and pass the block to the writer prompt."""
    import inspect

    from applire.services import cover_letter as svc

    src = inspect.getsource(svc._render_cover_letter_body)  # ADR-092: the body behind the owner-context entry
    assert "key_requirement_gaps(keyword_ledger, denied_concepts, job.raw_text)" in src
    assert "render_gap_ladder_block(" in src
    assert "gap_ladder_block=gap_ladder_block," in src
