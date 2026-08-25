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

"""#261 — the ``services.cv`` selection-layer hook: run
``outcome_preference.prefer_measured_outcomes_for_owner`` over every tailored
work entry's bullets, owner-scoped by the vault WorkEntry id
(``TailoredWorkEntry.id`` — the SAME identity ``_backfill_work_ids`` /
``_restore_ledger_bullets`` rely on).

Live-data-shaped fixture: the real dev-DB "Alpha Systems GmbH" work entry
(2026-07-25) — a target-phrase responsibility bullet + a measured-outcome
achievement bullet, both on the same WorkEntry.

Pure function tests — no DB, no LLM.
"""

import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_OWNER_ID = "99637b1e-b561-4106-a2a1-9f47e10beeb3"
_TARGET_BULLET = (
    "Built an internal LLM-assisted document classification service in "
    "Python (FastAPI, PostgreSQL, Docker), targeting a 60% reduction in "
    "manual processing time."
)
_ACHIEVEMENT_BULLET = (
    "Documents pre-classified by the service passed the very first review "
    "round in most cases, confirming the 60% reduction target is "
    "conservative."
)
_STORY_OUTCOME = (
    "Documents pre-classified by the service passed the very first review "
    "round in most cases, which is why the 60% reduction target we set is "
    "conservative."
)


def _alpha_systems_profile_json(*, with_story: bool = True) -> dict:
    return {
        "work_experience": [
            {
                "id": _OWNER_ID,
                "company": "Alpha Systems GmbH",
                "role": "Principal Platform Engineer",
                "start_date": None,
                "end_date": None,
                "is_current": True,
                "responsibilities": [
                    "Lead a team of five platform engineers running the core data platform.",
                    _TARGET_BULLET,
                    "Own production operations end-to-end for the platform (on-call, SLOs).",
                ],
                "achievements": [_ACHIEVEMENT_BULLET],
            }
        ],
        "projects": [],
        "signature_stories": (
            [
                {
                    "id": "story-1",
                    "title": "LLM-assisted document classification reduces review rounds",
                    "challenge": "Manual processing was the bottleneck.",
                    "mechanism": "Built an LLM-assisted classification step.",
                    "outcome": _STORY_OUTCOME,
                    "benchmark": None,
                    "experience_refs": [_OWNER_ID],
                }
            ]
            if with_story
            else []
        ),
    }


def _tailored(bullets, *, entry_id=_OWNER_ID):
    from applire.schemas.cv import TailoredCVData

    return TailoredCVData.model_validate(
        {
            "contact": {"name": "Max Prober"},
            "summary": "Principal Platform Engineer.",
            "work_history": [
                {
                    "id": entry_id,
                    "company": "Alpha Systems GmbH",
                    "role": "Principal Platform Engineer",
                    "start_date": "",
                    "end_date": None,
                    "bullets": list(bullets),
                }
            ],
            "skills": [],
        }
    )


class TestPreferMeasuredOutcomes:
    def test_live_shape_target_only_gets_reframed(self):
        """The writer kept only the naked target bullet -> the outcome from
        the merged achievement surfaces, target demoted to context."""
        from applire.services.outcome_preference import is_already_framed
        from applire.services.cv import _prefer_measured_outcomes

        profile_json = _alpha_systems_profile_json()
        tailored = _tailored([_TARGET_BULLET])

        result = _prefer_measured_outcomes(tailored, profile_json, "en")

        bullets = result.work_history[0].bullets
        assert len(bullets) == 1
        assert is_already_framed(bullets[0])
        assert _ACHIEVEMENT_BULLET in bullets[0]

    def test_live_shape_default_lang_is_german(self):
        """No explicit ``lang`` -> the DACH default (German), never a stray
        English chrome word in a German-output document (ADR-038)."""
        from applire.services.cv import _prefer_measured_outcomes

        profile_json = _alpha_systems_profile_json()
        tailored = _tailored([_TARGET_BULLET])

        result = _prefer_measured_outcomes(tailored, profile_json)

        bullets = result.work_history[0].bullets
        assert "gemessen" in bullets[0]
        assert "measured" not in bullets[0]

    def test_live_shape_via_signature_story_only(self):
        """Same pairing, but the measured result lives ONLY in the signature
        story (no merged achievement) -- still surfaces."""
        from applire.services.outcome_preference import is_already_framed
        from applire.services.cv import _prefer_measured_outcomes

        profile_json = _alpha_systems_profile_json()
        profile_json["work_experience"][0]["achievements"] = []
        tailored = _tailored([_TARGET_BULLET])

        result = _prefer_measured_outcomes(tailored, profile_json, "en")

        bullets = result.work_history[0].bullets
        assert is_already_framed(bullets[0])
        assert _STORY_OUTCOME in bullets[0]

    def test_live_shape_both_bullets_present_drops_naked_target(self):
        """The writer kept BOTH the target and the achievement verbatim
        (the actual run-4 shape: target sitting unqualified next to the
        outcome) -> the naked target is dropped, only the outcome remains."""
        from applire.services.cv import _prefer_measured_outcomes

        profile_json = _alpha_systems_profile_json()
        tailored = _tailored(
            [
                "Lead a team of five platform engineers running the core data platform.",
                _TARGET_BULLET,
                _ACHIEVEMENT_BULLET,
            ]
        )

        result = _prefer_measured_outcomes(tailored, profile_json)

        bullets = result.work_history[0].bullets
        assert _TARGET_BULLET not in bullets
        assert _ACHIEVEMENT_BULLET in bullets
        assert len(bullets) == 2

    def test_target_only_no_vault_outcome_stays_unchanged(self):
        """Only a target exists anywhere in the vault -> unchanged, still
        honestly marked as a target (no regression on today's behaviour)."""
        from applire.services.cv import _prefer_measured_outcomes

        profile_json = {
            "work_experience": [
                {
                    "id": "w2",
                    "company": "Beta Corp",
                    "role": "Engineer",
                    "responsibilities": ["Aiming for a 30% cut in onboarding time."],
                    "achievements": [],
                }
            ],
            "projects": [],
            "signature_stories": [],
        }
        tailored = _tailored(["Aiming for a 30% cut in onboarding time."], entry_id="w2")

        result = _prefer_measured_outcomes(tailored, profile_json)

        assert result.work_history[0].bullets == ["Aiming for a 30% cut in onboarding time."]

    def test_different_owner_no_cross_pairing(self):
        """A target under one work entry and an outcome under a DIFFERENT
        work entry for an unrelated initiative must never cross-pair."""
        from applire.services.cv import _prefer_measured_outcomes

        profile_json = {
            "work_experience": [
                {
                    "id": "w1",
                    "company": "Alpha",
                    "role": "Engineer",
                    "responsibilities": [_TARGET_BULLET],
                    "achievements": [],
                },
                {
                    "id": "w_other",
                    "company": "Gamma Inc",
                    "role": "Engineer",
                    "responsibilities": [],
                    "achievements": [_ACHIEVEMENT_BULLET],
                },
            ],
            "projects": [],
            "signature_stories": [],
        }
        from applire.schemas.cv import TailoredCVData

        tailored = TailoredCVData.model_validate(
            {
                "contact": {"name": "Max"},
                "summary": "",
                "work_history": [
                    {"id": "w1", "company": "Alpha", "role": "Engineer",
                     "start_date": "", "end_date": None, "bullets": [_TARGET_BULLET]},
                    {"id": "w_other", "company": "Gamma Inc", "role": "Engineer",
                     "start_date": "", "end_date": None, "bullets": [_ACHIEVEMENT_BULLET]},
                ],
                "skills": [],
            }
        )

        result = _prefer_measured_outcomes(tailored, profile_json)

        assert result.work_history[0].bullets == [_TARGET_BULLET]
        assert result.work_history[1].bullets == [_ACHIEVEMENT_BULLET]

    def test_missing_work_entry_id_is_left_untouched(self):
        """Legacy/mock fixtures with no id carried -> no owner to scope by,
        fail safe (no change), mirrors the #196 anchor-required rule."""
        from applire.services.cv import _prefer_measured_outcomes

        profile_json = _alpha_systems_profile_json()
        tailored = _tailored([_TARGET_BULLET], entry_id="")

        result = _prefer_measured_outcomes(tailored, profile_json)

        assert result.work_history[0].bullets == [_TARGET_BULLET]

    def test_idempotent_second_pass_is_a_noop(self):
        from applire.services.cv import _prefer_measured_outcomes

        profile_json = _alpha_systems_profile_json()
        tailored = _tailored([_TARGET_BULLET])

        first = _prefer_measured_outcomes(tailored, profile_json)
        second = _prefer_measured_outcomes(first, profile_json)

        assert second.work_history[0].bullets == first.work_history[0].bullets

    def test_no_op_returns_same_object_when_nothing_changes(self):
        """Mirrors the other post-draft guards' no-op contract (e.g.
        ``_restore_ledger_bullets``): untouched input -> untouched output."""
        from applire.services.cv import _prefer_measured_outcomes

        profile_json = {
            "work_experience": [
                {"id": "w1", "company": "Acme", "role": "Engineer",
                 "responsibilities": ["Owned the deployment pipeline."], "achievements": []}
            ],
            "projects": [],
            "signature_stories": [],
        }
        tailored = _tailored(["Owned the deployment pipeline."], entry_id="w1")

        result = _prefer_measured_outcomes(tailored, profile_json)

        assert result is tailored


class TestFactPinGuard:
    """ADR-077 clause 4 (pass-inventory disposition): a bullet carrying an
    active CV fact pin is never dropped or reframed by this pass — the pin is
    the user's verbatim priority and outranks the quality preference."""

    def test_pin_presence_survives_the_pass(self):
        # The contract is CONTAINMENT presence (the real predicate), not
        # byte-identity: a reframe that keeps the quote inside the bullet
        # satisfies the pin; one that drops it must revert the entry.
        from applire.schemas.application import FactPin
        from applire.services.cv import _prefer_measured_outcomes
        from applire.services.pin_reach import pin_present_in_cv

        pin = FactPin(
            entry_type="work", entry_id=_OWNER_ID, quote=_TARGET_BULLET
        )
        tailored = _tailored([_TARGET_BULLET])
        assert pin_present_in_cv(pin, tailored) is True  # fixture sanity
        out = _prefer_measured_outcomes(
            tailored, _alpha_systems_profile_json(), "en", pins=[pin]
        )
        assert pin_present_in_cv(pin, out) is True

    def test_pinned_bullet_that_would_be_dropped_reverts_the_entry(self):
        # "both bullets present" shape: the naked target is DROPPED outright
        # (not reframed) — with a pin on it, the entry reverts.
        from applire.schemas.application import FactPin
        from applire.services.cv import _prefer_measured_outcomes
        from applire.services.pin_reach import pin_present_in_cv

        pin = FactPin(
            entry_type="work", entry_id=_OWNER_ID, quote=_TARGET_BULLET
        )
        tailored = _tailored([_TARGET_BULLET, _ACHIEVEMENT_BULLET])
        unpinned = _prefer_measured_outcomes(
            tailored, _alpha_systems_profile_json(), "en"
        )
        assert _TARGET_BULLET not in unpinned.work_history[0].bullets  # control
        out = _prefer_measured_outcomes(
            tailored, _alpha_systems_profile_json(), "en", pins=[pin]
        )
        assert pin_present_in_cv(pin, out) is True
