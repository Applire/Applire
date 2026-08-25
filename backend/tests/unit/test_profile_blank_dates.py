# Copyright (C) 2026 Tobias Rosenbaum
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""E055 / JF-F-H1.9 — a blank date is "unknown" and is stored as None, on
every writer (the adversarial pass 2026-08-25 persisted "" through PATCH)."""

import pytest

from applire.schemas.profile import EducationEntry, WorkEntry


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_work_dates_become_none(blank):
    entry = WorkEntry(company="Acme", role="Eng", start_date=blank, end_date=blank)
    assert entry.start_date is None
    assert entry.end_date is None


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_education_dates_become_none(blank):
    entry = EducationEntry(institution="TU", degree="MSc", start_date=blank, end_date=blank)
    assert entry.start_date is None
    assert entry.end_date is None


def test_real_and_legacy_dates_are_untouched():
    entry = WorkEntry(company="Acme", role="Eng", start_date=" 2019-03 ", end_date="Q3 2019")
    assert entry.start_date == "2019-03"  # stripped, not reinterpreted
    assert entry.end_date == "Q3 2019"  # legacy shape preserved (JF-F-H1.12)
    assert WorkEntry(company="Acme", role="Eng").start_date is None
