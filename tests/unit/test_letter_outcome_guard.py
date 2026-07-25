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

"""#261 — the letter-side evidence-selection guard.

Same rule as ``services.outcome_preference`` (CV path), applied to the
FINAL settled cover-letter prose (mirrors ``letter_figure_guard``'s own
run-once-per-settled-output contract): a sentence phrased as a target, for a
position the letter names UNAMBIGUOUSLY (the strict single-employer-anchor
signal ``oracle.extract._find_employer_anchor`` already provides), gets the
paired measured outcome folded in as context. Deliberately narrower than the
CV path: prose sentences are only ever REFRAMED, never dropped (removing a
whole sentence from flowing prose risks breaking grammar in a way removing a
bullet-list item does not) — a documented, minimal scope choice.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.letter_outcome_guard import guard_letter_outcome_preference  # noqa: E402
from applire.services.outcome_preference import is_already_framed  # noqa: E402

PROFILE = {
    "personal_info": {"name": "Max Prober"},
    "work_experience": [
        {
            "id": "w-alpha",
            "company": "Alpha Systems GmbH",
            "role": "Principal Platform Engineer",
            "responsibilities": [
                "Built an internal LLM-assisted document classification "
                "service, targeting a 60% reduction in manual processing "
                "time."
            ],
            "achievements": [
                "Documents pre-classified by the service passed the very "
                "first review round in most cases, confirming the 60% "
                "reduction target is conservative."
            ],
        },
        {
            "id": "w-beta",
            "company": "Beta Corp",
            "role": "Software Engineer",
            "responsibilities": [],
            "achievements": [],
        },
    ],
}


def _letter(paragraphs):
    return {"header": {}, "recipient": {}, "body": {"paragraphs": paragraphs}, "signature": {}}


class TestGuardLetterOutcomePreference:
    def test_reframes_target_sentence_named_to_one_employer(self):
        para = (
            "At Alpha Systems GmbH, I built an internal LLM-assisted "
            "document classification service, targeting a 60% reduction in "
            "manual processing time. It has been well received by the team."
        )
        letter_data = _letter([para])

        result = guard_letter_outcome_preference(letter_data, PROFILE, "en")

        new_para = result["body"]["paragraphs"][0]
        assert is_already_framed(new_para)
        assert "measured" in new_para
        assert "very first review round" in new_para
        # Untouched neighbour sentence stays exactly as-is.
        assert "It has been well received by the team." in new_para

    def test_default_lang_is_german_not_english(self):
        """No explicit ``lang`` -> the DACH default (German) -- must never
        leak an English chrome word into an undeclared-language letter."""
        para = (
            "At Alpha Systems GmbH, I built an internal LLM-assisted "
            "document classification service, targeting a 60% reduction in "
            "manual processing time."
        )
        letter_data = _letter([para])

        result = guard_letter_outcome_preference(letter_data, PROFILE)

        new_para = result["body"]["paragraphs"][0]
        assert "gemessen" in new_para
        assert "measured" not in new_para

    def test_no_op_when_letter_data_unchanged_object_identity(self):
        para = "I led the on-call rotation and improved reliability."
        letter_data = _letter([para])

        result = guard_letter_outcome_preference(letter_data, PROFILE)

        assert result is letter_data

    def test_ambiguous_anchor_two_employers_named_stays_unchanged(self):
        """A sentence naming TWO employers is not a safe single-owner
        anchor -- fail open (mirrors _find_employer_anchor's own contract)."""
        para = (
            "Both at Alpha Systems GmbH and Beta Corp I was targeting a 60% "
            "reduction in manual processing time."
        )
        letter_data = _letter([para])

        result = guard_letter_outcome_preference(letter_data, PROFILE)

        assert result is letter_data

    def test_no_anchor_named_stays_unchanged(self):
        para = "I was targeting a 60% reduction in manual processing time."
        letter_data = _letter([para])

        result = guard_letter_outcome_preference(letter_data, PROFILE)

        assert result is letter_data

    def test_target_for_owner_with_no_paired_outcome_stays_unchanged(self):
        """Only a target exists for this owner -> unchanged, still honestly
        a target (no regression)."""
        para = "At Beta Corp, I was aiming for a 25% cut in release cycle time."
        letter_data = _letter([para])

        result = guard_letter_outcome_preference(letter_data, PROFILE)

        assert result is letter_data

    def test_idempotent_second_pass_is_a_noop(self):
        para = (
            "At Alpha Systems GmbH, I built an internal LLM-assisted "
            "document classification service, targeting a 60% reduction in "
            "manual processing time."
        )
        letter_data = _letter([para])

        first = guard_letter_outcome_preference(letter_data, PROFILE)
        second = guard_letter_outcome_preference(first, PROFILE)

        assert second["body"]["paragraphs"] == first["body"]["paragraphs"]

    def test_no_paragraphs_returns_input_unchanged(self):
        letter_data = {"header": {}, "recipient": {}, "body": {}, "signature": {}}
        result = guard_letter_outcome_preference(letter_data, PROFILE)
        assert result is letter_data
