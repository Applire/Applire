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
US161 (E033 / ADR-041 amended) — merge count-reconciliation.

Captures how many data points were *extracted* from an incoming CV vs how many
are *stored* (represented) in the merged profile, so silent merge data-loss
(FMEA JF-M-3.3) becomes a detectable accuracy signal. Deterministic, no LLM.

Architecture boundary (ADR-013): reconciliation is OBSERVATIONAL — it inspects
the merge result, it must never change what gets merged.
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import (  # noqa: E402
    Certification,
    EducationEntry,
    MasterProfileData,
    Skill,
    WorkEntry,
)
import pytest  # noqa: E402

from applire.services.profile import _enrichment_from_merge  # noqa: E402
from applire.services.profile.reconcile.import_bridge import reconcile_import  # noqa: E402
from applire.services.profile.reconciliation import (  # noqa: E402
    compute_merge_reconciliation,
)


class _Stub:
    """LLMProvider stub whose aparse_json returns a canned reconcile payload."""

    def __init__(self, payload):
        self.payload = payload

    async def aparse_json(self, prompt, **kwargs):
        return self.payload


def _profile(*entries: WorkEntry) -> MasterProfileData:
    return MasterProfileData(work_experience=list(entries))


def test_missing_extracted_work_entry_is_flagged_as_data_loss():
    """An incoming work entry absent from the merged profile = a positive delta."""
    incoming = _profile(
        WorkEntry(company="Acme GmbH", role="Dev", start_date="2020-01", end_date="2021-12"),
        WorkEntry(company="Globex Inc", role="Eng", start_date="2018-01", end_date="2019-12"),
    )
    # a buggy merge that dropped the second entry
    merged = _profile(
        WorkEntry(company="Acme GmbH", role="Dev", start_date="2020-01", end_date="2021-12"),
    )

    rec = compute_merge_reconciliation(incoming, merged)

    assert rec["work_experience"]["extracted"] == 2
    assert rec["work_experience"]["stored"] == 1
    assert rec["work_experience"]["delta"] == 1


def test_clean_additive_merge_has_zero_delta_across_all_entities():
    """When every extracted item is represented in merged, every delta is 0."""
    incoming = MasterProfileData(
        work_experience=[WorkEntry(company="Acme GmbH", role="Dev", start_date="2020-01")],
        skills=[Skill(name="Python"), Skill(name="SQL")],
        certifications=[Certification(name="AWS SAA")],
        education=[EducationEntry(institution="TU München", degree="MSc")],
    )
    # additive merge keeps everything (plus an unrelated pre-existing item)
    merged = MasterProfileData(
        work_experience=[
            WorkEntry(company="Acme GmbH", role="Dev", start_date="2020-01"),
            WorkEntry(company="Old Co", role="Intern", start_date="2015-01"),
        ],
        skills=[Skill(name="Python"), Skill(name="SQL"), Skill(name="Go")],
        certifications=[Certification(name="AWS SAA")],
        education=[EducationEntry(institution="TU München", degree="MSc")],
    )

    rec = compute_merge_reconciliation(incoming, merged)

    for entity in ("work_experience", "skills", "certifications", "education"):
        assert rec[entity]["delta"] == 0, f"{entity} should reconcile cleanly"
        assert rec[entity]["stored"] == rec[entity]["extracted"]


def test_dropped_skill_cert_and_education_each_flagged():
    """Data loss in skills / certifications / education each produces a delta."""
    incoming = MasterProfileData(
        skills=[Skill(name="Python"), Skill(name="Rust")],
        certifications=[Certification(name="AWS SAA"), Certification(name="CKA")],
        education=[
            EducationEntry(institution="TU München", degree="MSc"),
            EducationEntry(institution="LMU", degree="BSc"),
        ],
    )
    merged = MasterProfileData(
        skills=[Skill(name="Python")],
        certifications=[Certification(name="AWS SAA")],
        education=[EducationEntry(institution="TU München", degree="MSc")],
    )

    rec = compute_merge_reconciliation(incoming, merged)

    assert rec["skills"]["delta"] == 1
    assert rec["certifications"]["delta"] == 1
    assert rec["education"]["delta"] == 1


def _add_work_stub(company="Acme GmbH", role="Dev", start="2020-01"):
    return _Stub({
        "ops": [{"op": "upsert_work", "ref": "w1", "company": company,
                 "role": role, "start_date": start}],
        "ambiguities": [],
    })


@pytest.mark.asyncio
async def test_merge_profiles_exposes_reconciliation():
    """US184: the import bridge wires the reconciliation onto its MergeResult
    (observational — ADR-013), same as the retired lexical merge did."""
    existing = _profile()
    incoming = _profile(WorkEntry(company="Acme GmbH", role="Dev", start_date="2020-01"))

    result = await reconcile_import(existing, incoming, "cv_upload", _add_work_stub())

    assert result.reconciliation["work_experience"]["extracted"] == 1
    assert result.reconciliation["work_experience"]["delta"] == 0


@pytest.mark.asyncio
async def test_enrichment_record_persists_reconciliation():
    """The merge EnrichmentRecord carries the reconciliation delta (US161)."""
    existing = _profile()
    incoming = _profile(WorkEntry(company="Acme GmbH", role="Dev", start_date="2020-01"))
    result = await reconcile_import(existing, incoming, "cv_upload", _add_work_stub())

    record = _enrichment_from_merge(result, source="cv_upload")

    assert record.reconciliation is not None
    assert record.reconciliation["work_experience"]["extracted"] == 1
