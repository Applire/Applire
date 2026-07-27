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

"""US186 — tests for the one-time flat-duplicate reshape pass.

The reshape reuses the ADR-046 engine (LLM proposer, code disposer). Tests inject
a controlled provider (never ``get_provider()``); the LLM is canned, so each test
fixes both the messy input profile AND the ops a competent reconciler would emit,
then asserts the deterministic apply collapses the documented UAT duplicate
classes. ADR-040: no fixture ever asserts an invented link the provider did not
propose.
"""
from __future__ import annotations

from typing import Any

import pytest

from applire.schemas.profile import (
    MasterProfileData,
    ProjectEntry,
    VolunteerActivity,
    WorkEntry,
)
from applire.services.profile.reconcile.migrate import (
    ReshapeOutcome,
    reshape_profile,
)


class _StubProvider:
    """Provider stub returning a canned reconcile payload (hermetic — no real LLM).

    Accepts **kwargs to absorb the full provider-ABC signature.
    """

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.last_prompt: str | None = None
        self.calls = 0

    async def aparse_json(self, prompt: str, **kwargs: Any) -> Any:
        self.calls += 1
        self.last_prompt = prompt
        return self.payload


# ── Acceptance: the documented Chocolate UAT duplicate classes ────────────────


@pytest.mark.asyncio
async def test_synonym_role_fold() -> None:
    """`applire | Owner` folds into `applire | Founder & Lead Developer`."""
    keeper = WorkEntry(company="applire", role="Founder & Lead Developer")
    dup = WorkEntry(company="applire", role="Owner")
    profile = MasterProfileData(work_experience=[keeper, dup])

    # A competent reconciler folds the synonym role onto the keeper id.
    provider = _StubProvider(
        {
            "ops": [
                {
                    "op": "upsert_work",
                    "ref": "w1",
                    "target": keeper.id,
                    "company": "applire",
                    "role": "Owner",
                }
            ],
            "ambiguities": [],
        }
    )

    outcome = await reshape_profile(profile, provider, source="migration")

    assert isinstance(outcome, ReshapeOutcome)
    work = outcome.profile.work_experience
    keeper_out = next(w for w in work if w.id == keeper.id)
    assert "Owner" in keeper_out.role_aliases
    assert keeper_out.role == "Founder & Lead Developer"
    assert outcome.changed is True
    assert outcome.folds >= 1


@pytest.mark.asyncio
async def test_project_under_position() -> None:
    """An orphan `Solution Architect / QC LIMS` becomes a ProjectEntry under
    `Associate Director @ NordPharm`."""
    job = WorkEntry(company="NordPharm", role="Associate Director")
    profile = MasterProfileData(work_experience=[job])

    provider = _StubProvider(
        {
            "ops": [
                {
                    "op": "upsert_project",
                    "ref": "p1",
                    "target": None,
                    "name": "QC LIMS",
                    "parent": job.id,
                    "role": "Solution Architect",
                }
            ],
            "ambiguities": [],
        }
    )

    outcome = await reshape_profile(profile, provider, source="migration")

    assert len(outcome.profile.projects) == 1
    proj = outcome.profile.projects[0]
    assert proj.associated_experience == job.id
    assert proj.role == "Solution Architect"
    assert outcome.projects_nested >= 1
    assert outcome.changed is True


@pytest.mark.asyncio
async def test_de_en_employer_merge() -> None:
    """`Bavarian blood donation service` folds onto `Blutspendedienst`."""
    keeper = WorkEntry(company="Blutspendedienst", role="IT Administrator")
    dup = WorkEntry(company="Bavarian blood donation service", role="IT Admin")
    profile = MasterProfileData(work_experience=[keeper, dup])

    provider = _StubProvider(
        {
            "ops": [
                {
                    "op": "upsert_work",
                    "ref": "w1",
                    "target": keeper.id,
                    "company": "Bavarian blood donation service",
                    "role": "IT Admin",
                }
            ],
            "ambiguities": [],
        }
    )

    outcome = await reshape_profile(profile, provider, source="migration")

    keeper_out = next(
        w for w in outcome.profile.work_experience if w.id == keeper.id
    )
    # company never overwritten; the EN title is recorded as an alias.
    assert keeper_out.company == "Blutspendedienst"
    assert "IT Admin" in keeper_out.role_aliases
    assert outcome.changed is True


@pytest.mark.asyncio
async def test_idempotent_clean_profile_no_changes() -> None:
    """A clean profile yields zero changes (provider emits no ops)."""
    profile = MasterProfileData(
        work_experience=[WorkEntry(company="applire", role="Founder")]
    )
    provider = _StubProvider({"ops": [], "ambiguities": []})

    outcome = await reshape_profile(profile, provider, source="migration")

    assert outcome.changed is False
    assert outcome.folds == 0
    assert outcome.projects_nested == 0
    # The cleaned profile is structurally unchanged.
    assert outcome.profile.model_dump(mode="json") == profile.model_dump(mode="json")


@pytest.mark.asyncio
async def test_empty_ops_on_provider_noise_is_a_noop() -> None:
    """Engine degrades to empty ops on garbage → reshape is a no-op (never raises)."""
    profile = MasterProfileData(
        work_experience=[WorkEntry(company="X", role="Y")]
    )
    provider = _StubProvider("not-json-at-all")

    outcome = await reshape_profile(profile, provider, source="migration")

    assert outcome.changed is False
    assert outcome.profile.model_dump(mode="json") == profile.model_dump(mode="json")


# ── Truthfulness (ADR-040) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ambiguity_leaves_entry_unchanged_and_recorded() -> None:
    """An ambiguity the engine cannot resolve is recorded, not auto-applied."""
    a = WorkEntry(company="Acme", role="Engineer")
    b = WorkEntry(company="Acme Corp", role="Developer")
    profile = MasterProfileData(work_experience=[a, b])

    provider = _StubProvider(
        {
            "ops": [],
            "ambiguities": [
                {
                    "op": "request_confirmation",
                    "question": "Are 'Acme' and 'Acme Corp' the same employer?",
                    "options": ["yes", "no"],
                    "context": {},
                }
            ],
        }
    )

    outcome = await reshape_profile(profile, provider, source="migration")

    # No ops → nothing merged; both entries survive untouched.
    assert len(outcome.profile.work_experience) == 2
    assert outcome.changed is False
    assert len(outcome.ambiguities) == 1
    assert "Acme" in outcome.ambiguities[0].question


@pytest.mark.asyncio
async def test_no_invented_parent_link() -> None:
    """The reshape never invents a parent the provider did not propose (ADR-040).

    A standalone project with NO parent op stays standalone — the engine must not
    auto-nest it under some nearby job."""
    job = WorkEntry(company="NordPharm", role="Associate Director")
    standalone = ProjectEntry(name="Side Project", role="Maintainer")
    profile = MasterProfileData(work_experience=[job], projects=[standalone])

    # Provider proposes nothing for the standalone project.
    provider = _StubProvider({"ops": [], "ambiguities": []})

    outcome = await reshape_profile(profile, provider, source="migration")

    proj = next(p for p in outcome.profile.projects if p.name == "Side Project")
    assert proj.associated_experience is None
    assert outcome.changed is False


@pytest.mark.asyncio
async def test_flattened_new_info_carries_existing_entries() -> None:
    """The reshape feeds the profile's OWN entries to the engine as new_info, so
    the prompt contains the messy entries to re-reconcile (reuse, not re-extract)."""
    profile = MasterProfileData(
        work_experience=[
            WorkEntry(company="applire", role="Founder & Lead Developer"),
            WorkEntry(company="applire", role="Owner"),
        ]
    )
    provider = _StubProvider({"ops": [], "ambiguities": []})

    await reshape_profile(profile, provider, source="migration")

    assert provider.calls == 1
    assert provider.last_prompt is not None
    # Both messy roles must appear in the material handed to the reconciler.
    assert "Founder & Lead Developer" in provider.last_prompt
    assert "Owner" in provider.last_prompt
