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

"""Reverse-chronological work-ordering helper (survives lexical-merge retirement,
US184; still used by services/cv.py)."""
from __future__ import annotations

from applire.schemas.profile import WorkEntry
from applire.services.profile.merge import _sort_work_by_date


def test_sorts_reverse_chronological_by_end_date():
    entries = [
        WorkEntry(company="A", role="r", end_date="2018-06"),
        WorkEntry(company="B", role="r", end_date="2022-01"),
        WorkEntry(company="C", role="r", end_date="2020-09"),
    ]
    ordered = [e.company for e in _sort_work_by_date(entries)]
    assert ordered == ["B", "C", "A"]


def test_ongoing_role_without_end_date_sorts_first():
    entries = [
        WorkEntry(company="Past", role="r", end_date="2023-12"),
        WorkEntry(company="Current", role="r", end_date=None),
    ]
    ordered = [e.company for e in _sort_work_by_date(entries)]
    assert ordered == ["Current", "Past"]


def test_year_only_end_date_is_handled():
    # A bare "YYYY" end_date is normalised to that year's December.
    entries = [
        WorkEntry(company="Older", role="r", end_date="2019"),
        WorkEntry(company="Newer", role="r", end_date="2021-03"),
    ]
    ordered = [e.company for e in _sort_work_by_date(entries)]
    assert ordered == ["Newer", "Older"]
