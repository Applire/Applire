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
async def test_import_ambiguity_becomes_conflict():
    stub = _Stub({"ops":[],"ambiguities":[{"op":"request_confirmation","question":"Same role?","options":["Yes","No"]}]})
    result = await reconcile_import(MasterProfileData(), MasterProfileData(), "cv_upload", stub)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].source == "cv_upload"


@pytest.mark.asyncio
async def test_import_de_en_employer_fold():
    existing = MasterProfileData(work_experience=[WorkEntry(company="Roche Diagnostics GmbH", role="System Analyst")])
    rid = existing.work_experience[0].id
    incoming = MasterProfileData(work_experience=[WorkEntry(company="Roche", role="Systemanalytiker")])
    stub = _Stub({"ops":[{"op":"upsert_work","ref":"w1","target":rid,"company":"Roche","role":"Systemanalytiker"}],"ambiguities":[]})
    result = await reconcile_import(existing, incoming, "cv_upload", stub)
    assert len(result.merged_profile.work_experience) == 1            # DE/EN fold, no dup
