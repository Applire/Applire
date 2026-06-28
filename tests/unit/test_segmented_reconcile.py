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

"""US190 (E036, ADR-047 §1 / ADR-046 amended) — segmented profile-reconciliation
fallback.

A rich multi-CV merge asks the reconciler to emit one large op batch. On an
output-capped model that batch truncates and (after the provider's own
``retry_on_truncation``) re-raises ``LLMTruncatedError`` — which, on the import
path, would otherwise fail the whole upload and drop a CV's content. US190 adds a
batched-entry fallback: the incoming profile is sliced into dependency-ordered
groups (experiences first, then skills/qualifications, then identity), each
reconciled in ONE ``aparse_json`` call against the evolving profile and applied
before the next slice runs — so skills can reference the just-created experiences
by their real ids (the Open Q#3 resolution). N independent single-shot calls, not
a multi-turn tool loop (ADR-046 §3's rejection stands).
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import re  # noqa: E402

from applire.exceptions import LLMTruncatedError  # noqa: E402
from applire.schemas.profile import MasterProfileData, Skill, WorkEntry  # noqa: E402
from applire.services.profile.reconcile.import_bridge import (  # noqa: E402
    _slice_incoming,
    reconcile_import,
)


def _new_information(prompt: str) -> str:
    """Return only the NEW-INFORMATION section of a reconcile prompt.

    The current profile is dumped earlier in the prompt, so a marker that already
    landed in the profile (an applied experience) must NOT be mistaken for new
    input on a later slice — match against the new-info tail only.
    """
    return prompt.split("NEW INFORMATION")[-1]


class _CapThenSegmentProvider:
    """Simulates a hard output-cap model: the single whole-profile reconcile (which
    carries BOTH the new work entry and the new skill) truncates; each smaller
    per-slice call fits and returns its own op set."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def aparse_json(self, prompt, **kwargs):
        self.calls.append(prompt)
        new_info = _new_information(prompt)
        has_work = "NewCo" in new_info
        has_skill = "Rustlang" in new_info
        if has_work and has_skill:
            # The big single-shot op batch does not fit on this model.
            raise LLMTruncatedError("output cap reached mid-batch")
        if has_work:
            return {
                "ops": [{"op": "upsert_work", "ref": "w1", "company": "NewCo",
                         "role": "Engineer"}],
                "ambiguities": [],
            }
        if has_skill:
            return {
                "ops": [{"op": "upsert_skill", "name": "Rustlang",
                         "category": "technical", "evidence": []}],
                "ambiguities": [],
            }
        return {"ops": [], "ambiguities": []}


def _incoming_two_section() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[WorkEntry(company="NewCo", role="Engineer")],
        skills=[Skill(name="Rustlang")],
    )


class TestSegmentedReconcileFallback:
    @pytest.mark.asyncio
    async def test_truncated_single_call_falls_back_and_keeps_both_sections(self):
        """The release-critical property: a truncated single call must NOT drop a
        CV's content — the batched fallback absorbs the work entry AND the skill."""
        existing = MasterProfileData()
        provider = _CapThenSegmentProvider()

        result = await reconcile_import(existing, _incoming_two_section(), "cv_upload", provider)

        merged = result.merged_profile
        assert any(w.company == "NewCo" for w in merged.work_experience), (
            "the experiences slice must survive the segmented fallback"
        )
        assert any(s.name == "Rustlang" for s in merged.skills), (
            "the skills slice must survive the segmented fallback (no dropped CV content)"
        )

    @pytest.mark.asyncio
    async def test_fast_path_stays_single_call_when_it_fits(self):
        """No truncation → the single-call fast path is unchanged: exactly ONE
        aparse_json, no segmentation overhead on capable models."""
        class _Fits:
            def __init__(self):
                self.calls = 0

            async def aparse_json(self, prompt, **kwargs):
                self.calls += 1
                return {"ops": [{"op": "upsert_work", "ref": "w1",
                                 "company": "NewCo", "role": "Engineer"}],
                        "ambiguities": []}

        provider = _Fits()
        await reconcile_import(MasterProfileData(), _incoming_two_section(), "cv_upload", provider)
        assert provider.calls == 1, "the fast path must not segment when the single call fits"

    @pytest.mark.asyncio
    async def test_segmented_path_is_single_shot_per_slice_experiences_first(self):
        """ADR-046 §3 boundary: each slice is ONE single-shot call, never a tool
        loop. And the experiences slice must run BEFORE the skills slice (Open Q#3)
        so the skill can reference the just-created experience by its real id."""
        provider = _CapThenSegmentProvider()
        await reconcile_import(MasterProfileData(), _incoming_two_section(), "cv_upload", provider)

        # 1 failed single call + 2 slice calls (experiences, skills) = 3 total.
        assert len(provider.calls) == 3
        work_call = next(i for i, p in enumerate(provider.calls)
                         if "NewCo" in _new_information(p))
        skill_call = next(i for i, p in enumerate(provider.calls[1:], start=1)
                          if "Rustlang" in _new_information(p))
        assert work_call < skill_call, "experiences must be reconciled before skills"

    @pytest.mark.asyncio
    async def test_skill_can_reference_just_created_experience_by_id(self):
        """The keystone of the experiences-first design: once the experiences slice
        is applied, its entry carries a real id that the skills slice sees in the
        CURRENT PROFILE dump and can attach as evidence — full cross-section
        fidelity, no dangling cross-batch local ref."""
        class _RefByIdProvider:
            async def aparse_json(self, prompt, **kwargs):
                new_info = _new_information(prompt)
                if "NewCo" in new_info and "Rustlang" in new_info:
                    raise LLMTruncatedError("cap")
                if "NewCo" in new_info:
                    return {"ops": [{"op": "upsert_work", "ref": "w1",
                                     "company": "NewCo", "role": "Engineer"}],
                            "ambiguities": []}
                if "Rustlang" in new_info:
                    # The applied experience's id is now in the CURRENT PROFILE dump.
                    current = prompt.split("NEW INFORMATION")[0]
                    wid = re.search(r'"id":\s*"([0-9a-f-]{36})"', current).group(1)
                    return {"ops": [{"op": "upsert_skill", "name": "Rustlang",
                                     "category": "technical", "evidence": [wid]}],
                            "ambiguities": []}
                return {"ops": [], "ambiguities": []}

        result = await reconcile_import(
            MasterProfileData(), _incoming_two_section(), "cv_upload", _RefByIdProvider()
        )
        merged = result.merged_profile
        wid = merged.work_experience[0].id
        skill = next(s for s in merged.skills if s.name == "Rustlang")
        assert wid in skill.experience_refs, (
            "the skill must link to the experience created in the prior slice (by id)"
        )


class TestSliceIncoming:
    def test_partitions_every_section_without_loss(self):
        """No incoming content may be dropped: every populated section lands in
        exactly one slice, experiences first."""
        incoming = MasterProfileData(
            work_experience=[WorkEntry(company="NewCo", role="Engineer")],
            skills=[Skill(name="Rustlang")],
        )
        slices = _slice_incoming(incoming)
        # Only the two non-empty groups produce slices.
        assert len(slices) == 2
        assert slices[0].work_experience and not slices[0].skills, "experiences slice first"
        assert slices[1].skills and not slices[1].work_experience, "skills slice second"

    def test_empty_groups_are_skipped(self):
        incoming = MasterProfileData(skills=[Skill(name="Rustlang")])
        slices = _slice_incoming(incoming)
        assert len(slices) == 1 and slices[0].skills
