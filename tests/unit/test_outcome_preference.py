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

"""#261 — prefer MEASURED OUTCOMES over TARGETS for the same initiative.

Ground truth (run-4 blind hiring-panel finding, 2026-07-24): the generated CV
kept "targeting a 70% reduction" (a projection) sitting next to a properly
quantified measured win — read by the blind hiring manager as "intentionally
blurring aspiration and outcome", one of two named invite-"no" reasons.

Pure function tests — no DB, no LLM. Marker/pairing logic lives in
``applire.services.outcome_preference``; the real dev-DB shape this issue was
filed against ("Alpha Systems GmbH" — a work_experience bullet targeting a 60%
reduction, plus a signature story + merged achievement holding the measured
result for the SAME work entry) is reproduced in
``TestLiveAlphaSystemsShape`` below.
"""

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


# ── target-phrase detection ──────────────────────────────────────────────────


class TestIsTargetPhrase:
    def test_en_targeting(self):
        from applire.services.outcome_preference import is_target_phrase

        assert is_target_phrase(
            "Built a classification service, targeting a 60% reduction in "
            "manual processing time."
        )

    def test_en_aiming_for(self):
        from applire.services.outcome_preference import is_target_phrase

        assert is_target_phrase("Aiming for a 30% cut in onboarding time.")

    def test_en_goal_of(self):
        from applire.services.outcome_preference import is_target_phrase

        assert is_target_phrase("Set a goal of doubling throughput by Q3.")

    def test_de_ziel_von(self):
        from applire.services.outcome_preference import is_target_phrase

        assert is_target_phrase("Ziel von 60% Reduktion der Bearbeitungszeit.")

    def test_de_angestrebt(self):
        from applire.services.outcome_preference import is_target_phrase

        assert is_target_phrase("Die angestrebte Reduktion liegt bei 60%.")

    def test_de_soll_reduzieren_compound(self):
        from applire.services.outcome_preference import is_target_phrase

        assert is_target_phrase(
            "Der neue Service soll die Bearbeitungszeit um 60% reduzieren."
        )

    def test_de_soll_alone_is_not_enough(self):
        """'soll' is a generic modal verb — require the reduction-verb pairing
        (issue's own notation: DE 'soll ... reduzieren') so an unrelated
        'soll'-sentence isn't misread as a target (over-drop discipline)."""
        from applire.services.outcome_preference import is_target_phrase

        assert not is_target_phrase("Er soll das Meeting um 9 Uhr beginnen.")

    def test_measured_result_is_not_a_target(self):
        from applire.services.outcome_preference import is_target_phrase

        assert not is_target_phrase(
            "Reduced manual processing time by 68% within the first quarter."
        )

    def test_unicode_apostrophe_does_not_break_matching(self):
        """The U+2019 lesson: a real-model curly apostrophe near a marker must
        not defeat detection."""
        from applire.services.outcome_preference import is_target_phrase

        assert is_target_phrase(
            "We’re aiming for a 30% reduction in support tickets."
        )

    def test_bare_mention_of_the_word_target_is_not_a_target_phrase(self):
        """Live-data false-positive trap: the MEASURED bullet references the
        word 'target' only to explain the target was conservative — this must
        not itself classify as a target-phrase (else the guard would treat the
        outcome candidate as if it were another target and refuse to pair)."""
        from applire.services.outcome_preference import is_target_phrase

        assert not is_target_phrase(
            "Documents pre-classified by the service passed the very first "
            "review round in most cases, confirming the 60% reduction target "
            "is conservative."
        )


# ── owner-scoped pairing ─────────────────────────────────────────────────────


def _unit(path, text, owner_ids=frozenset()):
    from applire.services.oracle.matchers import EvidenceUnit

    return EvidenceUnit(path=path, text=text, text_norm=text.lower(), owner_ids=frozenset(owner_ids))


class TestFindPairedOutcome:
    def test_pairs_same_owner_achievement(self):
        from applire.services.outcome_preference import find_paired_outcome

        target = (
            "Built an internal LLM-assisted document classification service "
            "in Python (FastAPI, PostgreSQL, Docker), targeting a 60% "
            "reduction in manual processing time."
        )
        outcome_text = (
            "Documents pre-classified by the service passed the very first "
            "review round in most cases, confirming the 60% reduction target "
            "is conservative."
        )
        units = [
            _unit("work_experience[0].achievements[0]", outcome_text, {"w1"}),
        ]
        result = find_paired_outcome(target, frozenset({"w1"}), units)
        assert result is not None
        assert result.text == outcome_text

    def test_no_pairing_across_different_owners(self):
        """Fabrication guard: an outcome scoped to a DIFFERENT work entry must
        never pair, even with identical/near-identical wording."""
        from applire.services.outcome_preference import find_paired_outcome

        target = (
            "Built an internal LLM-assisted document classification service, "
            "targeting a 60% reduction in manual processing time."
        )
        outcome_text = (
            "Documents pre-classified by the service passed the very first "
            "review round, confirming the 60% reduction target is conservative."
        )
        units = [_unit("work_experience[1].achievements[0]", outcome_text, {"OTHER_OWNER"})]
        result = find_paired_outcome(target, frozenset({"w1"}), units)
        assert result is None

    def test_no_pairing_for_unrelated_initiative_same_owner(self):
        """Same owner, but the outcome describes a DIFFERENT initiative — low
        token overlap must fail the pairing (fabrication guard: same owner
        alone is not sufficient)."""
        from applire.services.outcome_preference import find_paired_outcome

        target = (
            "Built an internal LLM-assisted document classification service, "
            "targeting a 60% reduction in manual processing time."
        )
        unrelated_outcome = (
            "Migrated the on-call rotation tooling to PagerDuty, cutting "
            "incident acknowledgement time from 15 to 4 minutes."
        )
        units = [_unit("work_experience[0].achievements[0]", unrelated_outcome, {"w1"})]
        result = find_paired_outcome(target, frozenset({"w1"}), units)
        assert result is None

    def test_no_pairing_when_owner_id_missing(self):
        """No rendered-position anchor -> never pair (fail safe, mirrors the
        #196 attribution matcher's own 'claims without an anchor are never
        flagged' rule)."""
        from applire.services.outcome_preference import find_paired_outcome

        units = [_unit("work_experience[0].achievements[0]", "Reduced X by 60%.", {"w1"})]
        result = find_paired_outcome("targeting a 60% reduction", frozenset(), units)
        assert result is None

    def test_candidate_that_is_itself_a_target_phrase_is_rejected(self):
        """An 'outcome' candidate that is ALSO phrased as a target is not a
        measured result — must not be selected as the pairing."""
        from applire.services.outcome_preference import find_paired_outcome

        target = "Building a classification service, targeting a 60% reduction."
        also_a_target = "Still targeting a 60% reduction in processing time next quarter."
        units = [_unit("work_experience[0].achievements[0]", also_a_target, {"w1"})]
        result = find_paired_outcome(target, frozenset({"w1"}), units)
        assert result is None

    def test_signature_story_outcome_field_is_a_valid_candidate(self):
        from applire.services.outcome_preference import find_paired_outcome

        target = (
            "Built an internal LLM-assisted document classification service, "
            "targeting a 60% reduction in manual processing time."
        )
        story_outcome = (
            "Documents pre-classified by the service passed the very first "
            "review round in most cases, which is why the 60% reduction "
            "target we set is conservative."
        )
        units = [_unit("signature_stories[0].outcome", story_outcome, {"w1"})]
        result = find_paired_outcome(target, frozenset({"w1"}), units)
        assert result is not None
        assert result.text == story_outcome

    def test_signature_story_title_field_is_not_a_candidate(self):
        """Only the ``outcome`` field is the measurable-result field (ADR-055) —
        title/challenge/mechanism must not be treated as outcome evidence."""
        from applire.services.outcome_preference import find_paired_outcome

        target = "Built a classification service, targeting a 60% reduction."
        units = [
            _unit(
                "signature_stories[0].title",
                "LLM-assisted document classification reduces review rounds",
                {"w1"},
            )
        ]
        result = find_paired_outcome(target, frozenset({"w1"}), units)
        assert result is None


# ── the real dev-DB shape (live-reproduced, read-only) ──────────────────────


class TestLiveAlphaSystemsShape:
    """Exact text pulled read-only from the dev Postgres 'Alpha Systems GmbH'
    profile (2026-07-25) — the concrete case issue #261 was filed against."""

    TARGET_BULLET = (
        "Built an internal LLM-assisted document classification service in "
        "Python (FastAPI, PostgreSQL, Docker), targeting a 60% reduction in "
        "manual processing time."
    )
    ACHIEVEMENT = (
        "Documents pre-classified by the service passed the very first "
        "review round in most cases, confirming the 60% reduction target is "
        "conservative."
    )
    STORY_OUTCOME = (
        "Documents pre-classified by the service passed the very first "
        "review round in most cases, which is why the 60% reduction target "
        "we set is conservative."
    )
    OWNER_ID = "99637b1e-b561-4106-a2a1-9f47e10beeb3"

    def test_target_bullet_detected(self):
        from applire.services.outcome_preference import is_target_phrase

        assert is_target_phrase(self.TARGET_BULLET)

    def test_achievement_and_story_are_not_targets(self):
        from applire.services.outcome_preference import is_target_phrase

        assert not is_target_phrase(self.ACHIEVEMENT)
        assert not is_target_phrase(self.STORY_OUTCOME)

    def test_pairs_via_achievement(self):
        from applire.services.outcome_preference import find_paired_outcome

        units = [_unit("work_experience[0].achievements[0]", self.ACHIEVEMENT, {self.OWNER_ID})]
        result = find_paired_outcome(self.TARGET_BULLET, frozenset({self.OWNER_ID}), units)
        assert result is not None and result.text == self.ACHIEVEMENT

    def test_pairs_via_signature_story(self):
        from applire.services.outcome_preference import find_paired_outcome

        units = [_unit("signature_stories[0].outcome", self.STORY_OUTCOME, {self.OWNER_ID})]
        result = find_paired_outcome(self.TARGET_BULLET, frozenset({self.OWNER_ID}), units)
        assert result is not None and result.text == self.STORY_OUTCOME


# ── bullet-list transform ────────────────────────────────────────────────────


class TestPreferMeasuredOutcomesForOwner:
    def test_target_only_gets_reframed_with_outcome(self):
        """Vault has target + measured outcome for one initiative; the tailored
        CV only kept the naked target -> reframe in place, outcome surfaces."""
        from applire.services.outcome_preference import (
            is_already_framed,
            prefer_measured_outcomes_for_owner,
        )

        target = (
            "Built an internal LLM-assisted document classification service, "
            "targeting a 60% reduction in manual processing time."
        )
        outcome = (
            "Documents pre-classified by the service passed the very first "
            "review round in most cases, confirming the 60% reduction target "
            "is conservative."
        )
        units = [_unit("work_experience[0].achievements[0]", outcome, {"w1"})]

        result = prefer_measured_outcomes_for_owner([target], "w1", units, "en")

        assert len(result) == 1
        assert result[0].startswith(target)
        assert is_already_framed(result[0])
        assert "measured" in result[0]
        assert outcome in result[0]

    def test_frame_word_follows_document_output_language_not_ui_language(self):
        """ADR-038: the framing word is written INTO generated document
        content, so it must follow the document's own output language — a
        German-output CV must never get an English 'measured' chrome word
        injected (de+en parity)."""
        from applire.services.outcome_preference import prefer_measured_outcomes_for_owner

        target = "Ziel von 60% Reduktion der Bearbeitungszeit durch den neuen Service."
        outcome = "Der Service erreichte tatsächlich eine 65% Reduktion der Bearbeitungszeit."
        units = [_unit("work_experience[0].achievements[0]", outcome, {"w1"})]

        de_result = prefer_measured_outcomes_for_owner([target], "w1", units, "de")
        en_result = prefer_measured_outcomes_for_owner([target], "w1", units, "en")

        assert "gemessen" in de_result[0] and "measured" not in de_result[0]
        assert "measured" in en_result[0] and "gemessen" not in en_result[0]

    def test_target_and_outcome_both_present_drops_naked_target(self):
        """The exact run-4 shape: both the target AND the measured outcome
        already sit in the bullet list, unqualified side by side -> the bare
        target is dropped, the outcome stands alone."""
        from applire.services.outcome_preference import prefer_measured_outcomes_for_owner

        target = (
            "Built an internal LLM-assisted document classification service, "
            "targeting a 60% reduction in manual processing time."
        )
        outcome = (
            "Documents pre-classified by the service passed the very first "
            "review round in most cases, confirming the 60% reduction target "
            "is conservative."
        )
        units = [_unit("work_experience[0].achievements[0]", outcome, {"w1"})]

        result = prefer_measured_outcomes_for_owner([target, outcome], "w1", units)

        assert result == [outcome]

    def test_target_only_no_vault_pairing_leaves_it_a_clearly_marked_target(self):
        """Only a target exists in the vault -> unchanged, still honestly a
        target (must not regress today's already-honest behaviour)."""
        from applire.services.outcome_preference import prefer_measured_outcomes_for_owner

        target = "Aiming for a 30% cut in onboarding time next year."
        result = prefer_measured_outcomes_for_owner([target], "w1", units=[])
        assert result == [target]

    def test_no_owner_id_leaves_bullets_untouched(self):
        from applire.services.outcome_preference import prefer_measured_outcomes_for_owner

        target = "Aiming for a 30% cut in onboarding time next year."
        result = prefer_measured_outcomes_for_owner([target], "", units=[])
        assert result == [target]

    def test_idempotent_second_pass_is_a_noop(self):
        from applire.services.outcome_preference import prefer_measured_outcomes_for_owner

        target = (
            "Built an internal LLM-assisted document classification service, "
            "targeting a 60% reduction in manual processing time."
        )
        outcome = (
            "Documents pre-classified by the service passed the very first "
            "review round in most cases, confirming the 60% reduction target "
            "is conservative."
        )
        units = [_unit("work_experience[0].achievements[0]", outcome, {"w1"})]

        first = prefer_measured_outcomes_for_owner([target], "w1", units)
        second = prefer_measured_outcomes_for_owner(first, "w1", units)
        assert second == first

    def test_non_matching_owner_leaves_bullets_untouched(self):
        """Fabrication guard at the list-transform level: an outcome scoped to
        a different owner must not reframe this owner's target bullet."""
        from applire.services.outcome_preference import prefer_measured_outcomes_for_owner

        target = (
            "Built an internal LLM-assisted document classification service, "
            "targeting a 60% reduction in manual processing time."
        )
        outcome = (
            "Documents pre-classified by the service passed the very first "
            "review round in most cases, confirming the 60% reduction target "
            "is conservative."
        )
        units = [_unit("work_experience[1].achievements[0]", outcome, {"OTHER_OWNER"})]

        result = prefer_measured_outcomes_for_owner([target], "w1", units)
        assert result == [target]
