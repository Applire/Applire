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

"""R2-4 (Hazelnut build 4, ADR-013 note 2026-10-24) — an ADDITIONAL role at the
same employer keeps its start date and its bullet goes first.

The op batch below is the shape the reconciler emitted on the audit's Felix
turn (round-2 LLM log, record 199): a targeted ``upsert_work`` carrying the
additional title and ITS start date, plus an ``add_bullets`` onto the same
entry."""
from __future__ import annotations

from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile.apply import _keep_alias_subset, apply_ops
from applire.services.profile.reconcile.ops import AddBullets, UpsertWork

_ROLE = "workstream lead for the DORA-readiness workstream"
_BULLET = (
    "Since August 2026, workstream lead for the DORA-readiness workstream; "
    "coordinate the ICT risk register."
)


def _profile() -> MasterProfileData:
    return MasterProfileData(
        work_experience=[
            WorkEntry(
                id="w-cargo",
                company="Cargonaut Logistics GmbH",
                role="Senior Backend Engineer",
                start_date="2021-03",
                is_current=True,
                achievements=["Cut p95 latency from 1.8s to 240ms."],
                responsibilities=["Own the AWS deployment.", "Mentor two engineers."],
            )
        ]
    )


def _batch(start: str | None = "2026-08", role: str = _ROLE) -> list:
    return [
        UpsertWork(
            ref="w1", target="w-cargo", company="Cargonaut Logistics GmbH",
            role=role, start_date=start, is_current=True,
        ),
        AddBullets(target="w-cargo", responsibilities=[_BULLET], technologies=["DORA"]),
    ]


def test_additional_role_with_its_own_date_is_recorded_and_its_bullet_goes_first():
    result = apply_ops(_profile(), _batch(), "interview")
    entry = result.profile.work_experience[0]
    assert entry.role == "Senior Backend Engineer"  # never overwritten (ADR-013 rule 1)
    assert entry.start_date == "2021-03"  # the entry's own dates untouched
    assert _ROLE in entry.role_aliases
    assert [(d.role, d.since, d.bullets) for d in entry.dated_role_aliases] == [
        (_ROLE, "2026-08", [_BULLET])
    ]
    assert entry.responsibilities[0] == _BULLET
    assert entry.responsibilities[1:] == ["Own the AWS deployment.", "Mentor two engineers."]
    assert entry.technologies == ["DORA"]  # tools are not "bullets" — order kept


def test_an_alias_without_a_date_stays_an_undated_alias():
    result = apply_ops(_profile(), _batch(start=None, role="Staff Software Engineer"), "interview")
    entry = result.profile.work_experience[0]
    assert "Staff Software Engineer" in entry.role_aliases
    assert entry.dated_role_aliases == []
    assert entry.responsibilities[-1] == _BULLET  # appended as before


def test_the_entrys_own_start_date_echoed_is_not_a_since_date():
    result = apply_ops(_profile(), _batch(start="2021-03"), "interview")
    entry = result.profile.work_experience[0]
    assert _ROLE in entry.role_aliases
    assert entry.dated_role_aliases == []


def test_the_existing_title_echoed_writes_no_alias_and_no_date():
    """Record 208's miss shape: the model echoed the held title."""
    result = apply_ops(
        _profile(), _batch(start="2021-03", role="Senior Backend Engineer"), "interview"
    )
    entry = result.profile.work_experience[0]
    assert entry.role_aliases == [] and entry.dated_role_aliases == []


def test_an_empty_dated_list_is_omitted_from_the_dump():
    dumped = _profile().model_dump(mode="json")["work_experience"][0]
    assert "dated_role_aliases" not in dumped


def test_removing_the_alias_removes_its_dated_record():
    entry = apply_ops(_profile(), _batch(), "interview").profile.work_experience[0]
    data = entry.model_dump(mode="json")
    data["role_aliases"] = []
    reloaded = WorkEntry.model_validate(data)
    assert reloaded.dated_role_aliases == []


def test_a_malformed_dated_record_never_rejects_the_profile():
    data = _profile().model_dump(mode="json")
    data["work_experience"][0]["dated_role_aliases"] = [{"role": 3}, "x", {"since": "2026"}]
    profile = MasterProfileData.model_validate(data)
    assert profile.work_experience[0].dated_role_aliases == []


def test_the_section_door_may_drop_a_dated_record_but_never_add_or_redate_one():
    before_entry = apply_ops(_profile(), _batch(), "interview").profile.work_experience[0]
    before = [before_entry.model_dump(mode="json")]
    # re-dated by the client → dropped
    after = [dict(before[0], dated_role_aliases=[{"role": _ROLE, "since": "2019-01", "bullets": []}])]
    assert _keep_alias_subset("work_experience", before, after)[0]["dated_role_aliases"] == []
    # echoed unchanged → kept
    kept = _keep_alias_subset("work_experience", before, [dict(before[0])])
    assert [d["since"] for d in kept[0]["dated_role_aliases"]] == ["2026-08"]
    # a new one on an entry that had none → dropped
    fresh = _profile().model_dump(mode="json")["work_experience"]
    invented = [dict(fresh[0], role_aliases=[_ROLE], dated_role_aliases=[{"role": _ROLE, "since": "2026-08"}])]
    out = _keep_alias_subset("work_experience", fresh, invented)[0]
    assert out["dated_role_aliases"] == [] and out["role_aliases"] == []
