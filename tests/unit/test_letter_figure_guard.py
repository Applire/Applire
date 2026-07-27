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

"""#254 — the deterministic letter-figure attribution guard.

Ground truth (2026-07-24, live LLM debug log): the writer draft never
contained "5+"; the ADR-021 CORRECTOR call — reacting to a reviewer demand to
cover the absent keyword "Team management" — minted "mentoring teams of 5+"
by borrowing the headcount from an unrelated vault fact ("Lead a team of
five tech leads and system owners", a different current role).
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.letter_figure_guard import (  # noqa: E402
    _extract_letter_figures,
    guard_letter_figures,
)

# ── the live #254 shape ──────────────────────────────────────────────────────
PROFILE = {
    "personal_info": {"name": "Anna Bauer"},
    "professional_summary": {
        "en": "Engineering leader shipping production systems across DACH."
    },
    "work_experience": [
        {
            "id": "w-datacore",
            "company": "DataCore Systems",
            "role": "Platform Engineering Lead",
            "achievements": [
                "Lead a team of five tech leads and system owners across the "
                "platform organisation.",
            ],
        },
        {
            "id": "w-vector",
            "company": "Vector Analytics",
            "role": "Senior Backend Engineer",
            "achievements": [
                "Delivered a 70% reduction in checkout latency through async "
                "pipeline redesign.",
            ],
        },
    ],
}


def _letter(paragraphs):
    return {
        "header": {},
        "recipient": {},
        "body": {"paragraphs": paragraphs},
        "signature": {},
    }


# ── pure figure extraction ───────────────────────────────────────────────────

def test_extracts_plus_form():
    figs = _extract_letter_figures("mentoring teams of 5+ engineers")
    assert [(f.kind, f.value, f.raw) for f in figs] == [("number", "5", "5+")]


def test_extracts_percent():
    figs = _extract_letter_figures("a 70% reduction in latency")
    assert [(f.kind, f.value) for f in figs] == [("percent", "70")]


def test_extracts_spelled_english():
    figs = _extract_letter_figures("I led a team of five engineers")
    assert ("number", "5") in [(f.kind, f.value) for f in figs]


def test_extracts_spelled_german():
    figs = _extract_letter_figures("Ich leitete ein Team von fünf Ingenieuren")
    assert ("number", "5") in [(f.kind, f.value) for f in figs]


def test_exempts_years():
    figs = _extract_letter_figures("Since 2019 I have driven platform strategy.")
    assert figs == []


def test_year_does_not_leak_as_plain_number():
    figs = _extract_letter_figures("In 2024, we shipped 3 major releases.")
    values = [(f.kind, f.value) for f in figs]
    assert ("number", "2024") not in values
    assert ("number", "3") in values


def test_unicode_apostrophe_does_not_break_matching():
    # U+2019 between words must not defeat spelled-number matching.
    figs = _extract_letter_figures("the team’s five engineers")
    assert ("number", "5") in [(f.kind, f.value) for f in figs]


# ── the live #254 vector: misattributed headcount ────────────────────────────

def test_borrowed_headcount_takes_its_whole_sentence_with_it():
    """The pinned bug: 'five' is a DataCore fact; a Vector Analytics clause
    must never keep a '5+' borrowed from it.

    #296 changed the REMOVAL UNIT from the figure's character span to the
    sentence. The old pin here asserted 'mentoring teams of' survives — i.e.
    it actively required the mutilated remainder that charter run #7 delivered
    to a hiring manager. A figure is a noun-phrase argument; its neighbours are
    grammatically load-bearing, so the sentence is the smallest unit that still
    reads once the figure has to go.
    """
    letter = _letter(
        [
            "Dear Hiring Team,",
            "At Vector Analytics, I have experience mentoring teams of 5+ "
            "engineers and driving delivery excellence.",
            "Sincerely,",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    # Exact-equality, not substring absence: the point of #296 is WHAT REMAINS,
    # and only a full comparison can catch a surviving fragment.
    assert result["body"]["paragraphs"] == ["Dear Hiring Team,", "Sincerely,"]


def test_headcount_survives_when_clause_actually_names_datacore():
    """The SAME figure, honestly attributed to the role it belongs to, must
    survive — the guard targets misattribution, not the figure itself."""
    letter = _letter(
        [
            "At DataCore Systems, I lead a team of five tech leads and system "
            "owners across the platform organisation.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    assert result is letter or result["body"]["paragraphs"] == letter["body"]["paragraphs"]
    body_text = " ".join(result["body"]["paragraphs"])
    assert "five" in body_text


# ── required regression: legitimate figures must survive ────────────────────

def test_bionTech_style_percent_survives_in_its_own_sentence():
    letter = _letter(
        [
            "At Vector Analytics, I delivered a 70% reduction in checkout "
            "latency through async pipeline redesign.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "70%" in body_text


def test_profile_level_years_of_experience_survives():
    """'over 20 years of experience' is a profile-level claim never literally
    stored as the digit string '20' anywhere in the vault (it is derived from
    date spans) — the guard must not treat 'no literal match' as fabricated;
    that is the Oracle's unbacked-figure job, not this guard's."""
    letter = _letter(
        [
            "With over 20 years of experience shipping production systems, "
            "I am excited to apply.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "20 years" in body_text


def test_role_agnostic_evidence_clears_any_context():
    """A figure backed (also) by a role-agnostic vault unit (e.g. the
    professional summary) is never foreign, anchored or not."""
    profile = {
        **PROFILE,
        "professional_summary": {"en": "Delivered 42 major platform migrations."},
    }
    letter = _letter(["I have personally delivered 42 major platform migrations."])
    result = guard_letter_figures(letter, profile)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "42" in body_text


def test_unmatched_figure_is_left_alone():
    """A figure with NO vault match anywhere is not this guard's concern
    (belt-and-suspenders: Oracle's unbacked-figure detection handles it)."""
    letter = _letter(["I shipped 17 major releases last year."])
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "17" in body_text


# ── unanchored-but-named-elsewhere escape (#248-style) ───────────────────────

def test_unanchored_clause_cleared_when_letter_names_the_true_owner_elsewhere():
    letter = _letter(
        [
            "At DataCore Systems, I led the platform organisation.",
            "I also grew and mentored a team of five across the group.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "five" in body_text


def test_unanchored_clause_dropped_when_letter_names_two_employers_and_neither_clause_says_which():
    """The #248 laundering shape: two employers named in the letter, but the
    figure-bearing clause names neither — genuinely undecidable, so the SAFE
    action is to strip it."""
    letter = _letter(
        [
            "At DataCore Systems, I led platform strategy.",
            "At Vector Analytics, I owned backend delivery.",
            "Separately, I have mentored teams of 5+ across my career.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "5+" not in body_text


# ── #283 shape: multi-employer letter, achievement paragraph unanchored ─────
# Invented fixture mirroring the run-6 ground truth (BioNTech "record-breaking
# QC LIMS implementation, 7 months, 3 sites" — belongs to an OLDER position at
# the SAME employer the letter names in a DIFFERENT paragraph; the letter
# separately names a second, unrelated employer too, so the "letter names
# exactly one employer" escape can never fire).
MULTI_ROLE_PROFILE = {
    "personal_info": {"name": "Jordan Lee"},
    "work_experience": [
        {
            "id": "w-northwind-current",
            "company": "Northwind Labs",
            "role": "Director of Platform Engineering",
            "is_current": True,
            "achievements": ["Set strategic direction for the platform org."],
        },
        {
            "id": "w-northwind-past",
            "company": "Northwind Labs",
            "role": "Systems Architect",
            "is_current": False,
            "achievements": [
                "Led a record-breaking LIMS rollout (7 months, 3 sites) as "
                "solution architect.",
            ],
        },
        {
            "id": "w-founder",
            "company": "Riverstone",
            "role": "Founder",
            "achievements": ["Built an open-source developer platform."],
        },
    ],
}


def test_unanchored_achievement_paragraph_drops_the_figures_pinned_shape():
    """The pinned #283 defect, reproduced with invented data: paragraph 2
    names Northwind Labs; paragraph 3 carries the LIMS achievement but names
    NO employer of its own, and the letter separately names a second employer
    (Riverstone) — so neither the sentence anchor nor the whole-letter
    single-employer escape can resolve ownership, and the figures are
    dropped. This is the guard working AS INTENDED — the fix belongs in the
    letter's anchoring (prompt level), not in relaxing this guard."""
    letter = _letter(
        [
            "At Northwind Labs, I currently serve as Director of Platform "
            "Engineering.",
            "My technical leadership spans strategic direction, and I have "
            "delivered record-breaking projects like the LIMS rollout in 7 "
            "months across 3 sites while working fully remote.",
            "As founder of Riverstone, I also built an open-source developer "
            "platform.",
        ]
    )
    result = guard_letter_figures(letter, MULTI_ROLE_PROFILE)
    # The whole achievement sentence goes (#296). Blanking its two figures in
    # place produced exactly the run-#7 wreckage — "the LIMS rollout in months
    # across sites" — and the old pin below it demanded that remainder survive.
    assert result["body"]["paragraphs"] == [
        "At Northwind Labs, I currently serve as Director of Platform "
        "Engineering.",
        "As founder of Riverstone, I also built an open-source developer "
        "platform.",
    ]


def test_same_sentence_anchor_lets_the_figures_survive():
    """The fix target: when the achievement-bearing SENTENCE ITSELF names the
    employer (not just an earlier paragraph), the figures survive — this is
    what the prompt-level anchoring requirement asks the writer/corrector to
    produce. Restoring '7 months across 3 sites' WITH the correct anchor,
    never a bare unattributed figure."""
    letter = _letter(
        [
            "At Northwind Labs, I currently serve as Director of Platform "
            "Engineering.",
            "At Northwind Labs, I also delivered record-breaking projects "
            "like the LIMS rollout in 7 months across 3 sites while working "
            "fully remote.",
            "As founder of Riverstone, I also built an open-source developer "
            "platform.",
        ]
    )
    result = guard_letter_figures(letter, MULTI_ROLE_PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "7 months" in body_text
    assert "3 sites" in body_text


def test_cross_role_borrowed_figure_still_dropped_even_when_anchored_elsewhere():
    """Guard against #254 regression: an anchor naming the WRONG employer must
    never launder a figure that genuinely belongs to a different one. The
    LIMS figures belong to Northwind Labs, not Riverstone — anchoring the
    sentence to Riverstone must still strip them."""
    letter = _letter(
        [
            "As founder of Riverstone, I delivered record-breaking projects "
            "like the LIMS rollout in 7 months across 3 sites while working "
            "fully remote.",
        ]
    )
    result = guard_letter_figures(letter, MULTI_ROLE_PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "7 months" not in body_text
    assert "3 sites" not in body_text


# ── #296: the paragraph's running anchor ────────────────────────────────────
# This is the part of #296 that stops most removals from being necessary at
# all. Charter run #7's letter named two employers, so the whole-letter
# single-employer escape could never fire, and every figure in every sentence
# that did not itself restate the employer was dropped. Prose does not restate
# it: "At Acme I owned the platform. I cut deploy time from 45 to 8 minutes."

def test_running_anchor_carries_the_employer_to_the_next_sentence():
    """A follow-on sentence in the SAME paragraph inherits the anchor, exactly
    as a human reader resolves it — so a legitimately-owned figure survives
    without the writer having to restate the employer in every sentence."""
    letter = _letter(
        [
            "At Northwind Labs, I currently serve as Director of Platform "
            "Engineering. I delivered record-breaking projects like the LIMS "
            "rollout in 7 months across 3 sites.",
            "As founder of Riverstone, I also built an open-source developer "
            "platform.",
        ]
    )
    result = guard_letter_figures(letter, MULTI_ROLE_PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "7 months" in body_text
    assert "3 sites" in body_text


def test_running_anchor_never_survives_a_topic_change():
    """The carry-forward may only fill genuine silence. The moment a later
    sentence anchors somewhere ELSE, the borrowed figure must still be caught —
    otherwise the anchor becomes a laundering channel wider than the
    whole-letter escape it sits in front of."""
    letter = _letter(
        [
            "At Northwind Labs, I currently serve as Director of Platform "
            "Engineering. As founder of Riverstone, I delivered the LIMS "
            "rollout in 7 months across 3 sites.",
        ]
    )
    result = guard_letter_figures(letter, MULTI_ROLE_PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "7 months" not in body_text
    assert "3 sites" not in body_text


# ── #296: what remains must be readable ─────────────────────────────────────

def test_no_run7_wreckage_shape_can_survive_a_drop():
    """The three shapes charter run #7 actually delivered to a hiring manager,
    each produced by blanking the figure's own span: "deploy time from 45 to 8
    minutes" -> "deploy time from to 8 minutes"; "EKS for 12 services" -> "EKS
    for services"; "a 99.9% availability target" -> "a availability target".

    Asserting the FIGURE is gone (which the old pins did) cannot catch any of
    these — the mutilated remainder passes that check perfectly. Only looking
    at what is left does.
    """
    profile = {
        "personal_info": {"name": "Jordan Lee"},
        "work_experience": [
            {
                "id": "w-northwind-current",
                "company": "Northwind Labs",
                "role": "Director of Platform Engineering",
                "achievements": ["Set strategic direction for the platform org."],
            },
            {
                "id": "w-founder",
                "company": "Riverstone",
                "role": "Founder",
                "achievements": [
                    "Cut deploy time from 45 to 8 minutes.",
                    "Ran EKS for 12 services.",
                    "Held a 99.9% availability target.",
                ],
            },
        ],
    }
    letter = _letter(
        [
            "At Northwind Labs, I currently serve as Director of Platform "
            "Engineering.",
            "I reduced deploy time from 45 to 8 minutes. I ran EKS for 12 "
            "services. I held a 99.9% availability target.",
        ]
    )
    result = guard_letter_figures(letter, profile)
    body_text = " ".join(result["body"]["paragraphs"])
    for wreckage in ("from to", "EKS for services", "a availability"):
        assert wreckage not in body_text, f"shipped run-#7 wreckage: {wreckage!r}"
    # No half-sentence left behind either — the whole unattributable paragraph
    # went, and the anchored one stands untouched.
    assert result["body"]["paragraphs"] == [
        "At Northwind Labs, I currently serve as Director of Platform "
        "Engineering.",
    ]


def test_a_truthful_figure_in_the_same_sentence_is_collateral():
    """The acknowledged cost of a sentence-sized removal unit, pinned so it
    stays visible rather than being discovered again in a run report.

    "At Vector Analytics, I mentored teams of 5+ engineers, having delivered a
    70% reduction in checkout latency" mixes a BORROWED DataCore headcount with
    a genuine Vector Analytics figure. Deterministic code cannot keep the
    second without rebuilding the sentence around it, so the floor takes both.
    Recovering the truthful half needs the writer, not the guard: the review
    loop rewrites rather than the guard cutting (follow-up issue), at which
    point this test's expectation changes on purpose.
    """
    letter = _letter(
        [
            "At Vector Analytics, I have experience mentoring teams of 5+ "
            "engineers, having delivered a 70% reduction in checkout latency.",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "5+" not in body_text
    assert "70%" not in body_text  # collateral — the honest half goes too


# ── no-op path ────────────────────────────────────────────────────────────

def test_letter_with_no_figures_is_returned_unchanged_object():
    letter = _letter(["Dear Hiring Team,", "I am excited to apply.", "Sincerely,"])
    result = guard_letter_figures(letter, PROFILE)
    assert result is letter


def test_empty_body_is_tolerated():
    assert guard_letter_figures({}, PROFILE) == {}
    assert guard_letter_figures({"body": {}}, PROFILE) == {"body": {}}
