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


# ---------------------------------------------------------------------------
# Skill field coercion — adversarial pass 2026-09-01 (#228 follow-up)
# ---------------------------------------------------------------------------
# `_coerce_partial_date`'s own docstring names the cost these guard against:
# the strict date parser "rejects them and aborts the whole import". Skill was
# the one model carrying a bare `date | None` without that coercion, and
# `normalize_category` was the one enum validator without the `None` branch its
# sibling `normalize_proficiency` has. Neither mattered while the flat/MCP
# extraction door could only emit `skills: list[str]`; #228 gave that door the
# object shape, so `import_cv`, all three LinkedIn variants and paste-text can
# now carry both shapes — and one ambiguous skill would abort the whole import
# (`_import_from_text` has no try/except around model_validate).

from applire.schemas.profile import MasterProfileData


def _skill(**kw):
    return MasterProfileData.model_validate(
        {"personal_info": {"name": "T"}, "skills": [{"name": "Python", **kw}]}
    ).skills[0]


def test_skill_category_null_does_not_abort_the_import():
    """JSON null, not a string — normalize_category's isinstance check missed it."""
    assert _skill(category=None).category == "technical"


def test_skill_last_used_accepts_year_only():
    """A CV that states month/year precision is the ordinary case, not an edge."""
    assert _skill(last_used="2023").last_used.year == 2023


def test_skill_last_used_accepts_year_month():
    got = _skill(last_used="2023-06").last_used
    assert (got.year, got.month) == (2023, 6)


def test_skill_last_used_unparseable_falls_to_none_not_an_exception():
    assert _skill(last_used="irgendwann").last_used is None


def test_skill_last_used_full_date_still_parses():
    assert _skill(last_used="2023-06-15").last_used.isoformat() == "2023-06-15"
