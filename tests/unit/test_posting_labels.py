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

"""F7 — effective_posting_labels (ADR-092 cl. 5(f), S-17, RD-2).

The application's title/company win over the shared posting's; blank or
missing application values fall back to the posting; no application means the
posting's values unchanged.
"""

from types import SimpleNamespace

import pytest

from applire.services.posting_labels import effective_posting_labels


def _job(role="Data Engineer", company="Posting GmbH"):
    return SimpleNamespace(role_title=role, company_name=company)


def _app(role=None, company=None):
    return SimpleNamespace(role_title=role, company_name=company)


def test_no_application_returns_the_postings_labels():
    assert effective_posting_labels(_job(), None) == ("Data Engineer", "Posting GmbH")


def test_application_values_win_over_the_postings():
    assert effective_posting_labels(_job(), _app("Senior Data Engineer", "Acme AG")) == (
        "Senior Data Engineer",
        "Acme AG",
    )


def test_each_field_wins_independently():
    assert effective_posting_labels(_job(), _app(role="Lead")) == ("Lead", "Posting GmbH")
    assert effective_posting_labels(_job(), _app(company="Acme AG")) == ("Data Engineer", "Acme AG")


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_application_values_fall_back_to_the_posting(blank):
    assert effective_posting_labels(_job(), _app(blank, blank)) == ("Data Engineer", "Posting GmbH")


def test_posting_without_company_and_no_override_stays_none():
    assert effective_posting_labels(_job(company=None), _app()) == ("Data Engineer", None)


def test_application_company_fills_a_posting_without_one():
    assert effective_posting_labels(_job(company=None), _app(company="Acme AG")) == (
        "Data Engineer",
        "Acme AG",
    )


def test_works_on_the_orm_rows():
    from applire.models.application import Application
    from applire.models.job import JobAnalysis

    job = JobAnalysis(raw_text_hash="h", raw_text="x", role_title="Engineer", company_name="Posting GmbH")
    app = Application(role_title="Platform Engineer", company_name=None)
    assert effective_posting_labels(job, app) == ("Platform Engineer", "Posting GmbH")
