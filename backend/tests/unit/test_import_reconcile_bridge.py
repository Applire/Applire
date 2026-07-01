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

import pytest
from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.merge import MergeResult
from applire.services.profile.reconcile.import_bridge import reconcile_import


class _Stub:
    def __init__(self, payload): self.payload = payload
    async def aparse_json(self, prompt, **kw): return self.payload


@pytest.mark.asyncio
async def test_import_folds_synonym_role_into_existing():
    existing = MasterProfileData(work_experience=[WorkEntry(company="Applire", role="Founder & Lead Developer")])
    wid = existing.work_experience[0].id
    incoming = MasterProfileData(work_experience=[WorkEntry(company="applire", role="Owner")])
    stub = _Stub({"ops":[{"op":"upsert_work","ref":"w1","target":wid,"company":"applire","role":"Owner"}],"ambiguities":[]})
    result = await reconcile_import(existing, incoming, "linkedin_import", stub)
    assert isinstance(result, MergeResult)
    assert len(result.merged_profile.work_experience) == 1            # no duplicate
    assert "Owner" in result.merged_profile.work_experience[0].role_aliases
    assert result.changes
    assert isinstance(result.reconciliation, dict)


@pytest.mark.asyncio
async def test_import_ambiguity_becomes_confirmation_not_conflict():
    """E037 PQ #4 — a RequestConfirmation ambiguity must surface through the
    confirmation channel (question + each option as its own option), NOT be
    force-coerced into the 2-value Conflict shape (which garbled the dialog:
    section='', the whole question swallowed into `field`, and the option list
    comma-joined into `incoming_value`)."""
    stub = _Stub({"ops": [], "ambiguities": [{
        "op": "request_confirmation",
        "question": "Is 'Lead Developer' at applire the same role as your existing 'Founder' entry?",
        "options": ["Keep as separate roles", "Merge into existing role", "Replace existing role"],
    }]})
    result = await reconcile_import(MasterProfileData(), MasterProfileData(), "cv_upload", stub)

    # The ambiguity rides the confirmation channel, intact.
    assert len(result.pending_confirmations) == 1
    pc = result.pending_confirmations[0]
    assert pc.question == (
        "Is 'Lead Developer' at applire the same role as your existing 'Founder' entry?"
    )
    # Each option is preserved as its own selectable option (3 distinct buttons),
    # never comma-joined into one string.
    assert pc.options == [
        "Keep as separate roles", "Merge into existing role", "Replace existing role"
    ]
    assert pc.source == "cv_upload"

    # The old garble path is gone: no Conflict is manufactured from the ambiguity.
    assert result.conflicts == []
    # Belt-and-braces: there is no Conflict with the empty-section / list-valued
    # incoming_value signature the malformed coercion produced.
    for c in result.conflicts:
        assert c.section != ""
        assert not isinstance(c.incoming_value, list)


@pytest.mark.asyncio
async def test_import_real_flag_conflict_still_surfaces_as_conflict():
    """A genuine two-value FlagConflict (existing vs incoming) is unaffected — it
    still surfaces on the conflict channel; only RequestConfirmation ambiguities
    move to confirmations."""
    existing = MasterProfileData(work_experience=[WorkEntry(company="Acme", role="Engineer", start_date="2020-01")])
    wid = existing.work_experience[0].id
    incoming = MasterProfileData(work_experience=[WorkEntry(company="Acme", role="Engineer", start_date="2019-06")])
    stub = _Stub({"ops": [
        {"op": "upsert_work", "ref": "w1", "target": wid, "company": "Acme", "role": "Engineer"},
        {"op": "flag_conflict", "target": wid, "field": "start_date",
         "existing": "2020-01", "incoming": "2019-06"},
    ], "ambiguities": []})
    result = await reconcile_import(existing, incoming, "cv_upload", stub)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].field == "start_date"
    assert result.conflicts[0].incoming_value == "2019-06"
    assert result.pending_confirmations == []


@pytest.mark.asyncio
async def test_import_de_en_employer_fold():
    existing = MasterProfileData(work_experience=[WorkEntry(company="Roche Diagnostics GmbH", role="System Analyst")])
    rid = existing.work_experience[0].id
    incoming = MasterProfileData(work_experience=[WorkEntry(company="Roche", role="Systemanalytiker")])
    stub = _Stub({"ops":[{"op":"upsert_work","ref":"w1","target":rid,"company":"Roche","role":"Systemanalytiker"}],"ambiguities":[]})
    result = await reconcile_import(existing, incoming, "cv_upload", stub)
    assert len(result.merged_profile.work_experience) == 1            # DE/EN fold, no dup
