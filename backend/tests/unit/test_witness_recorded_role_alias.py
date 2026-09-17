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

"""ADR-063 amended 2026-09-17 — the import witness reads an existing
position's recorded alternate titles (``role_aliases``).

Real case (edge, 2026-09-17): a German LinkedIn export re-imported into a
vault that already holds the same career listed two positions as not carried
over. The vault had recorded the German titles as alternate titles on an
earlier merge, but the company names differ beyond what the near-dupe
matcher accepts, and the model emitted no op (rule 7: nothing new).
"""
from applire.schemas.profile import MasterProfileData, WorkEntry
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.engine import _parse_ops
from applire.services.profile.reconcile.import_witness import (
    compute_import_not_applied,
)
from applire.services.profile.reconciliation import compute_merge_reconciliation

_BSD_ID = "4b0c1b1e-6f0e-4d7c-9a51-2f7f3b0c2a11"
_ROCHE_ID = "9d3e2c55-0f55-4a39-8f0e-5e1c8b7c4d22"


def _vault(**overrides) -> MasterProfileData:
    bsd = dict(
        id=_BSD_ID,
        company="Bayerischer Blutspendedienst gGmbH",
        role="Professional System Engineer / IT Quality Officer",
        start_date="2012-08",
        role_aliases=["Systementwickler"],
    )
    roche = dict(
        id=_ROCHE_ID,
        company="Roche Diagnostics GmbH",
        role="System Analyst",
        start_date="2011-06",
        role_aliases=["Systemanalytiker"],
    )
    bsd.update(overrides.get("bsd", {}))
    roche.update(overrides.get("roche", {}))
    return MasterProfileData(work_experience=[WorkEntry(**bsd), WorkEntry(**roche)])


def _linkedin(**overrides) -> MasterProfileData:
    bsd = dict(
        company="Blutspendedienst des Bayerischen Roten Kreuzes gGmbH",
        role="Systementwickler",
        start_date="2012-08",
    )
    bsd.update(overrides)
    roche = dict(company="Roche", role="Systemanalytiker", start_date="2011-06")
    return MasterProfileData(work_experience=[WorkEntry(**bsd), WorkEntry(**roche)])


def _labels(items) -> list[str]:
    return [i.label for i in items]


def test_the_edge_case_both_positions_are_carried_through_the_real_merge():
    """The model emitted nothing; the real committer leaves the vault as is,
    and the witness reads the recorded alternate titles."""
    applied = apply_ops(_vault(), _parse_ops([]), "linkedin_import")
    items = compute_import_not_applied(_linkedin(), applied.profile, ops=[])
    assert items == [], _labels(items)


def test_an_alias_recorded_by_rule_7_carries_the_next_import():
    """The seam: an id-targeted upsert_work with a new title records the alias
    (rule 7); the next import naming that title under another company name is
    carried."""
    vault = _vault(bsd={"role_aliases": []})
    ops = _parse_ops([
        {"op": "upsert_work", "ref": "w1", "target": _BSD_ID,
         "company": "Bayerischer Blutspendedienst gGmbH", "role": "Systementwickler"},
    ])
    first = apply_ops(vault, ops, "cv_upload")
    assert "Systementwickler" in first.profile.work_experience[0].role_aliases

    items = compute_import_not_applied(_linkedin(), first.profile, ops=[])
    assert items == [], _labels(items)


def test_without_the_alias_the_position_stays_listed():
    items = compute_import_not_applied(_linkedin(), _vault(bsd={"role_aliases": []}), ops=[])
    assert _labels(items) == [
        "Blutspendedienst des Bayerischen Roten Kreuzes gGmbH / Systementwickler / 2012-08"
    ]


def test_a_different_start_month_stays_listed():
    """A repeat stint with the same title is a distinct CV line (B1)."""
    items = compute_import_not_applied(_linkedin(start_date="2014-03"), _vault(), ops=[])
    assert _labels(items) == [
        "Blutspendedienst des Bayerischen Roten Kreuzes gGmbH / Systementwickler / 2014-03"
    ]


def test_a_year_only_date_on_either_side_stays_listed():
    items = compute_import_not_applied(_linkedin(start_date="2012"), _vault(), ops=[])
    assert len(items) == 1
    items = compute_import_not_applied(_linkedin(), _vault(bsd={"start_date": "2012"}), ops=[])
    assert _labels(items) == [
        "Blutspendedienst des Bayerischen Roten Kreuzes gGmbH / Systementwickler / 2012-08"
    ]
    # both sides year-only: a shared year is too weak to name one stint
    items = compute_import_not_applied(
        _linkedin(start_date="2012"), _vault(bsd={"start_date": "2012"}), ops=[]
    )
    assert _labels(items) == [
        "Blutspendedienst des Bayerischen Roten Kreuzes gGmbH / Systementwickler / 2012"
    ]


def test_the_existing_role_itself_does_not_carry_a_different_employer():
    """Same title and month at an unrelated company is a new job, not an alias."""
    vault = MasterProfileData(work_experience=[
        WorkEntry(company="Acme GmbH", role="Consultant", start_date="2020-01"),
    ])
    incoming = MasterProfileData(work_experience=[
        WorkEntry(company="Zeta Beratung AG", role="Consultant", start_date="2020-01"),
    ])
    items = compute_import_not_applied(incoming, vault, ops=[])
    assert _labels(items) == ["Zeta Beratung AG / Consultant / 2020-01"]


def test_two_positions_holding_the_alias_in_that_month_rescue_nothing():
    vault = _vault(roche={"start_date": "2012-08", "role_aliases": ["Systementwickler"]})
    incoming = MasterProfileData(work_experience=[
        WorkEntry(company="Blutspendedienst des Bayerischen Roten Kreuzes gGmbH",
                  role="Systementwickler", start_date="2012-08"),
    ])
    items = compute_import_not_applied(incoming, vault, ops=[])
    assert len(items) == 1


def test_alias_comparison_uses_the_committer_normalisation():
    items = compute_import_not_applied(
        _linkedin(role="  SYSTEMENTWICKLER "), _vault(), ops=[]
    )
    assert items == [], _labels(items)


def test_the_health_hub_count_moves_with_the_list():
    incoming, merged = _linkedin(), _vault()
    items = compute_import_not_applied(incoming, merged, ops=[])
    rec = compute_merge_reconciliation(incoming, merged, items)
    assert rec["work_experience"]["extracted"] == 2
    assert rec["work_experience"]["stored"] == 2
    assert rec["work_experience"]["delta"] == 0
