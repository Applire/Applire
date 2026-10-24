# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
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

"""FS-5 (Hazelnut build 4, founder 2026-10-23; ADR-046 amended 2026-10-24) —
"Nicht dasselbe" on a targeted skill upsert also removes the evidence the false
binding merged into the target (783:131). Before, the alias came off and the
entry was added back, but the target kept the job ref the binding had added
(the audit vault: Mentoring kept the Finleap ref)."""
from __future__ import annotations

from datetime import datetime, timezone

from applire.schemas.profile import (
    EnrichmentRecord,
    MasterProfileData,
    ProfileMetadata,
    Skill,
    WorkEntry,
)
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import SeparateMatch, UpsertSkill


def _vault() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(id="w-old", company="Werk Nord", role="Leiter"),
            WorkEntry(id="w-new", company="Werk Süd", role="Schichtleiter"),
        ],
        skills=[Skill(id="s-budget", name="Budgetplanung", category="soft", experience_refs=["w-old"])],
    )


def _bound() -> MasterProfileData:
    """The false binding as a targeted upsert writes it, recorded on the trail."""
    op = UpsertSkill(
        name="Budgetverantwortung", category="soft", target="s-budget",
        existing="Budgetplanung", evidence=["w-new"],
    )
    applied = apply_ops(_vault(), [op], "cv_upload")
    profile = applied.profile
    profile.metadata = ProfileMetadata(enrichment_history=[EnrichmentRecord(
        timestamp=datetime.now(timezone.utc), source="cv_upload",
        changes=applied.changes, matched=applied.matched,
    )])
    return profile


def test_the_binding_records_the_refs_it_added():
    profile = _bound()
    target = profile.skills[0]
    assert target.experience_refs == ["w-old", "w-new"]
    receipt = profile.metadata.enrichment_history[0].matched[0]
    assert receipt.refs_added == ["w-new"]  # only what THIS binding added


def test_nicht_dasselbe_takes_the_evidence_back_onto_the_separated_entry():
    result = apply_ops(_bound(), [SeparateMatch(entity_id="s-budget", incoming="Budgetverantwortung")], "manual_edit")
    by_name = {s.name: s for s in result.profile.skills}
    assert by_name["Budgetplanung"].experience_refs == ["w-old"]
    assert by_name["Budgetverantwortung"].experience_refs == ["w-new"]
    removed = [c for c in result.changes if c.field == "experience_refs"]
    assert removed and removed[0].action == "removed" and removed[0].old_value == ["w-new"]


def test_a_ref_the_target_already_held_stays_on_it():
    vault = _vault()
    vault.skills[0].experience_refs = ["w-old", "w-new"]  # held before the binding
    op = UpsertSkill(name="Budgetverantwortung", category="soft", target="s-budget",
                     existing="Budgetplanung", evidence=["w-new"])
    applied = apply_ops(vault, [op], "cv_upload")
    assert applied.matched[0].refs_added == []
    profile = applied.profile
    profile.metadata = ProfileMetadata(enrichment_history=[EnrichmentRecord(
        timestamp=datetime.now(timezone.utc), source="cv_upload", matched=applied.matched)])
    result = apply_ops(profile, [SeparateMatch(entity_id="s-budget", incoming="Budgetverantwortung")], "manual_edit")
    assert next(s for s in result.profile.skills if s.id == "s-budget").experience_refs == ["w-old", "w-new"]


# ── the one-time read-only check (FS-5 second half) ──────────────────────────

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

_FIX = Path(__file__).resolve().parents[3] / "tests" / "files" / "801_cross_category_alias"
_MENTORING = "8def9047-4bd7-4421-ad1b-e9b27a07ed9d"


class _Provider:
    def __init__(self, batch):
        self.batch = batch

    async def aparse_json(self, prompt, *, system=None, **_kw):
        import copy

        if system and "profile reconciler" in system:
            return copy.deepcopy(self.batch)
        return {}


async def _audit_vault_after_the_false_binding(monkeypatch) -> MasterProfileData:
    """The audit's shape, rebuilt: the second import's binding written with the
    #801 guard switched off (how it was written before the guard existed)."""
    from applire.services.profile.reconcile import binding_guard, import_bridge
    from applire.services.profile.reconcile.import_bridge import reconcile_import

    monkeypatch.setattr(import_bridge, "refuse_false_skill_bindings", lambda inc, prof, ops: (list(ops), []))
    monkeypatch.setattr(binding_guard, "skill_binding_refusal", lambda *a, **k: None)
    vault = MasterProfileData.model_validate(json.loads((_FIX / "vault_before_second_import.json").read_text()))
    incoming = MasterProfileData.model_validate(json.loads((_FIX / "incoming_linkedin.json").read_text()))
    batch = json.loads((_FIX / "audit_binding_batch.json").read_text())
    result = await reconcile_import(vault, incoming, "linkedin_import", _Provider(batch))
    profile = result.merged_profile
    history = list(profile.metadata.enrichment_history) if profile.metadata else []
    history.append(EnrichmentRecord(
        timestamp=datetime.now(timezone.utc), source="linkedin_import",
        changes=result.changes, matched=result.matched,
    ))
    profile.metadata = (profile.metadata or ProfileMetadata()).model_copy(update={"enrichment_history": history})
    return profile


@pytest.mark.asyncio
async def test_the_audit_binding_is_raised_as_one_question_and_nothing_is_written(monkeypatch):
    from applire.services.profile.health import assess_health

    profile = await _audit_vault_after_the_false_binding(monkeypatch)
    mentoring = next(s for s in profile.skills if s.id == _MENTORING)
    assert "REST APIs" in mentoring.aliases  # the shape the audit found
    before = profile.model_dump(mode="json")
    issues = [i for i in assess_health(profile).issues if i.thread == "suspect_binding"]
    assert [(i.incoming_name, i.skill_name, i.profile_mismatch_severity) for i in issues] == [
        ("REST APIs", "Mentoring", "review")
    ]
    assert profile.model_dump(mode="json") == before  # read-only


@pytest.mark.asyncio
async def test_an_answered_pair_is_not_raised_again(monkeypatch):
    from applire.services.profile.health import assess_health
    from applire.services.profile.reconcile.ops import ConfirmMatch

    profile = await _audit_vault_after_the_false_binding(monkeypatch)
    undone = apply_ops(profile, [SeparateMatch(entity_id=_MENTORING, incoming="REST APIs")], "manual_edit").profile
    assert not [i for i in assess_health(undone).issues if i.thread == "suspect_binding"]
    mentoring = next(s for s in undone.skills if s.id == _MENTORING)
    assert "REST APIs" not in (mentoring.aliases or [])
    assert mentoring.experience_refs == []  # FS-5: the merged evidence went back
    kept = apply_ops(profile, [ConfirmMatch(entity_id=_MENTORING, incoming="REST APIs")], "manual_edit").profile
    assert not [i for i in assess_health(kept).issues if i.thread == "suspect_binding"]


def test_a_same_category_binding_is_not_suspect():
    from applire.services.profile.health import assess_health

    assert not [i for i in assess_health(_bound()).issues if i.thread == "suspect_binding"]
