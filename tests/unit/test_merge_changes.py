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

"""
US145 — the merge must emit a STRUCTURED FieldChange per auto-decision (not one
opaque summary blob), so the ADR-040 "what changed & why" surfaces render from
data. Each change carries a `rationale` (the assumption note shown to the user).

US184 — migrated off the retired lexical merge_profiles onto the ADR-046 engine
import path (reconcile_import). The structured-change contract is unchanged: the
engine's apply layer emits the same FieldChange shape (action / section /
new_value / rationale / rationale_key) and the bridge still populates the legacy
`added` list and feeds `_enrichment_from_merge`. The tests drive a stub provider
that returns canned reconcile ops.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import MasterProfileData, WorkEntry  # noqa: E402
from applire.services.profile.reconcile.import_bridge import reconcile_import  # noqa: E402


class _Stub:
    """LLMProvider stub whose aparse_json returns a canned reconcile payload."""

    def __init__(self, payload):
        self.payload = payload

    async def aparse_json(self, prompt, **kwargs):
        return self.payload


def _profile(*entries: WorkEntry) -> MasterProfileData:
    return MasterProfileData(work_experience=list(entries))


class TestMergeEmitsStructuredChanges:
    @pytest.mark.asyncio
    async def test_new_entry_emits_structured_change(self):
        existing = _profile()
        incoming = _profile(
            WorkEntry(company="Acme GmbH", role="Developer", start_date="2020-01", end_date="2022-12")
        )
        stub = _Stub({
            "ops": [{"op": "upsert_work", "ref": "w1", "company": "Acme GmbH",
                     "role": "Developer", "start_date": "2020-01", "end_date": "2022-12"}],
            "ambiguities": [],
        })
        result = await reconcile_import(existing, incoming, "cv_upload", stub)
        wx_changes = [c for c in result.changes if c.section == "work_experience" and c.action == "added"]
        assert wx_changes, "a new work entry must produce a structured 'added' FieldChange"
        assert "Acme GmbH" in str(wx_changes[0].new_value)

    @pytest.mark.asyncio
    async def test_role_alias_accumulation_emits_merged_change(self):
        existing = _profile(
            WorkEntry(company="Acme GmbH", role="Developer", start_date="2020-01", end_date="2022-12",
                      responsibilities=["Built APIs"])
        )
        wid = existing.work_experience[0].id
        incoming = _profile(
            WorkEntry(company="Acme GmbH", role="Senior Developer", start_date="2020-01", end_date="2022-12",
                      responsibilities=["Led migrations"])
        )
        # target = the existing entry's id → folds in, producing a 'merged' change.
        stub = _Stub({
            "ops": [{"op": "upsert_work", "ref": "w1", "target": wid, "company": "Acme GmbH",
                     "role": "Senior Developer", "start_date": "2020-01", "end_date": "2022-12"}],
            "ambiguities": [],
        })
        result = await reconcile_import(existing, incoming, "cv_upload", stub)
        assert any(c.action == "merged" for c in result.changes), (
            "accumulating into an existing position must produce a 'merged' FieldChange"
        )

    @pytest.mark.asyncio
    async def test_every_change_has_a_rationale(self):
        existing = _profile()
        incoming = _profile(
            WorkEntry(company="Acme GmbH", role="Developer", start_date="2020-01", end_date="2022-12")
        )
        stub = _Stub({
            "ops": [{"op": "upsert_work", "ref": "w1", "company": "Acme GmbH",
                     "role": "Developer", "start_date": "2020-01", "end_date": "2022-12"}],
            "ambiguities": [],
        })
        result = await reconcile_import(existing, incoming, "cv_upload", stub)
        assert result.changes
        assert all(c.rationale for c in result.changes), "ADR-040: each change needs a why-note"
        # #2 (ADR-038): each change also carries a localizable key for the UI.
        assert all(c.rationale_key for c in result.changes), "each change needs a rationale_key for i18n"

    @pytest.mark.asyncio
    async def test_added_list_still_populated_for_backward_compat(self):
        existing = _profile()
        incoming = _profile(
            WorkEntry(company="Acme GmbH", role="Developer", start_date="2020-01", end_date="2022-12")
        )
        stub = _Stub({
            "ops": [{"op": "upsert_work", "ref": "w1", "company": "Acme GmbH",
                     "role": "Developer", "start_date": "2020-01", "end_date": "2022-12"}],
            "ambiguities": [],
        })
        result = await reconcile_import(existing, incoming, "cv_upload", stub)
        assert result.added  # legacy human-readable list preserved


class TestEnrichmentRecordCarriesStructuredChanges:
    @pytest.mark.asyncio
    async def test_enrichment_from_merge_stores_per_decision_changes(self):
        """The persisted EnrichmentRecord must carry the structured changes list,
        not a single summary blob (US145 / ADR-040)."""
        from applire.services.profile import _enrichment_from_merge

        existing = _profile()
        incoming = _profile(
            WorkEntry(company="Acme GmbH", role="Developer", start_date="2020-01", end_date="2022-12")
        )
        stub = _Stub({
            "ops": [{"op": "upsert_work", "ref": "w1", "company": "Acme GmbH",
                     "role": "Developer", "start_date": "2020-01", "end_date": "2022-12"}],
            "ambiguities": [],
        })
        result = await reconcile_import(existing, incoming, "cv_upload", stub)
        record = _enrichment_from_merge(result, source="cv_upload")
        assert record.source == "cv_upload"
        assert len(record.changes) == len(result.changes) >= 1
        assert all(c.rationale for c in record.changes)
