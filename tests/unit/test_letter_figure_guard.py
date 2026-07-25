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

def test_borrowed_headcount_is_dropped_from_unrelated_clause():
    """The pinned bug: 'five' is a DataCore fact; a Vector Analytics clause
    must never keep a '5+' borrowed from it."""
    letter = _letter(
        [
            "Dear Hiring Team,",
            "At Vector Analytics, I have experience mentoring teams of 5+ "
            "engineers and driving delivery excellence.",
            "Sincerely,",
        ]
    )
    result = guard_letter_figures(letter, PROFILE)
    body_text = " ".join(result["body"]["paragraphs"])
    assert "5+" not in body_text
    assert "5" not in body_text.replace("2019", "")  # no stray bare digit either
    # the surrounding clause survives, figure-free
    assert "mentoring teams of" in body_text
    assert "Vector Analytics" in body_text


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


# ── no-op path ────────────────────────────────────────────────────────────

def test_letter_with_no_figures_is_returned_unchanged_object():
    letter = _letter(["Dear Hiring Team,", "I am excited to apply.", "Sincerely,"])
    result = guard_letter_figures(letter, PROFILE)
    assert result is letter


def test_empty_body_is_tolerated():
    assert guard_letter_figures({}, PROFILE) == {}
    assert guard_letter_figures({"body": {}}, PROFILE) == {"body": {}}
