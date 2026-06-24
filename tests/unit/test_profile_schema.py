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

"""Unit tests for ExperienceBase.expected_fields (US179 / ADR-041 amended 2026-06-24).

Verifies that:
- expected_fields defaults to None (legacy-safe)
- A list value round-trips correctly
- Legacy JSONB blobs without the field load without error
- Non-list values are coerced to None (garbage-in tolerance)
"""
from applire.schemas.profile import WorkEntry


def test_workentry_expected_fields_defaults_none():
    assert WorkEntry(role="Dev", company="Acme").expected_fields is None


def test_workentry_accepts_expected_fields_list():
    e = WorkEntry(role="Lead", company="Acme", expected_fields=["team_size", "budget_managed"])
    assert e.expected_fields == ["team_size", "budget_managed"]


def test_legacy_blob_without_field_loads():
    e = WorkEntry.model_validate({"role": "Dev", "company": "Acme"})
    assert e.expected_fields is None


def test_expected_fields_non_list_coerced_to_none():
    e = WorkEntry.model_validate({"role": "Dev", "company": "Acme", "expected_fields": "garbage"})
    assert e.expected_fields is None
