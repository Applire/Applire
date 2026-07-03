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


# --- Issue #118: reverse-chronological by START date -------------------------


def test_two_concurrent_open_ended_positions_sort_by_start_date():
    """#118 — two ongoing ('present') positions must order newest-start-first,
    not keep the incidental input order."""
    entries = [
        WorkEntry(company="Beta", role="r", start_date="2024-12", end_date=None),
        WorkEntry(company="Alpha", role="r", start_date="2026-03", end_date=None),
    ]
    ordered = [e.company for e in _sort_work_by_date(entries)]
    assert ordered == ["Alpha", "Beta"]


def test_start_date_outranks_end_date():
    # Newest start wins even when the other entry ended later.
    entries = [
        WorkEntry(company="EndedLater", role="r", start_date="2018-01", end_date="2023-06"),
        WorkEntry(company="StartedLater", role="r", start_date="2020-05", end_date="2022-01"),
    ]
    ordered = [e.company for e in _sort_work_by_date(entries)]
    assert ordered == ["StartedLater", "EndedLater"]


def test_same_start_tie_breaks_on_end_date_then_original_order():
    entries = [
        WorkEntry(company="EndedFirst", role="r", start_date="2021-01", end_date="2021-12"),
        WorkEntry(company="Ongoing", role="r", start_date="2021-01", end_date=None),
        WorkEntry(company="AlsoEndedFirst", role="r", start_date="2021-01", end_date="2021-12"),
    ]
    ordered = [e.company for e in _sort_work_by_date(entries)]
    # Ongoing (open end) outranks the ended ones; full ties keep original order.
    assert ordered == ["Ongoing", "EndedFirst", "AlsoEndedFirst"]


def test_missing_start_date_sorts_last():
    entries = [
        WorkEntry(company="NoStart", role="r", start_date=None, end_date="2025-01"),
        WorkEntry(company="Dated", role="r", start_date="2015-02", end_date="2016-03"),
    ]
    ordered = [e.company for e in _sort_work_by_date(entries)]
    assert ordered == ["Dated", "NoStart"]


def test_year_only_and_full_date_starts_are_comparable():
    entries = [
        WorkEntry(company="YearOnly", role="r", start_date="2020", end_date=None),
        WorkEntry(company="YearMonth", role="r", start_date="2020-06", end_date=None),
        WorkEntry(company="FullDate", role="r", start_date="2021-02-15", end_date=None),
    ]
    ordered = [e.company for e in _sort_work_by_date(entries)]
    assert ordered == ["FullDate", "YearMonth", "YearOnly"]


def test_garbage_start_date_is_treated_as_missing():
    entries = [
        WorkEntry(company="Garbage", role="r", start_date="seit jeher", end_date=None),
        WorkEntry(company="Dated", role="r", start_date="2019-04", end_date="2020-01"),
    ]
    ordered = [e.company for e in _sort_work_by_date(entries)]
    assert ordered == ["Dated", "Garbage"]
