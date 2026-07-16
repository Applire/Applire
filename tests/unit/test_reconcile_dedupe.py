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

"""#177 — predicate tests for the section-agnostic near-dupe classifier."""
from __future__ import annotations

from applire.schemas.profile import EducationEntry, Language, WorkEntry
from applire.services.profile.reconcile.dedupe import classify_dupe, classify_engagement_dupe


def test_education_near_dupe_matches_long_institution_form():
    entry = EducationEntry(institution="Julius-Maximilians-Universität Würzburg", degree="Diplom Informatik")
    v = classify_dupe(
        {"institution": "Universität Würzburg", "degree": "Diplom Informatik"},
        [entry], {"institution": lambda e: e.institution, "degree": lambda e: e.degree},
    )
    assert v.match is entry          # 2-token containment = strict near-dupe → auto-merge


def test_education_single_token_degree_containment_is_ambiguous():
    entry = EducationEntry(institution="Universität Würzburg", degree="Diplom Informatik")
    v = classify_dupe(
        {"institution": "Universität Würzburg", "degree": "Diplom"},
        [entry], {"institution": lambda e: e.institution, "degree": lambda e: e.degree},
    )
    assert v.match is None and v.ambiguous == [entry]   # → RequestConfirmation


def test_distinct_field_blocks_match():
    entry = EducationEntry(institution="Universität Würzburg", degree="Diplom Informatik")
    v = classify_dupe(
        {"institution": "TU München", "degree": "Diplom Informatik"},
        [entry], {"institution": lambda e: e.institution, "degree": lambda e: e.degree},
    )
    assert v.match is None and not v.ambiguous


def test_language_containment_auto_merges():
    lang = Language(language="German")
    v = classify_dupe({"language": "German (Native)"}, [lang],
                      {"language": lambda l: l.language}, containment_is_same=True)
    assert v.match is lang


def test_empty_fields_are_neutral_not_matching():
    entry = EducationEntry(institution="", degree="")
    v = classify_dupe({"institution": "", "degree": "Diplom"}, [entry],
                      {"institution": lambda e: e.institution, "degree": lambda e: e.degree})
    assert v.match is None           # no evidence ≠ same entry


# ── #177 review (finding 3): classify_engagement_dupe org-containment ruling ──


def test_engagement_single_token_org_containment_is_ambiguous_not_match():
    """ADR-046 strict ruling: bare single-token org containment ('Ford' ⊂ 'Ford
    Foundation') is NEVER identity, even with an equal start month — two
    distinct employers can share a token. Ask, never guess."""
    entry = WorkEntry(company="Ford Foundation", role="Program Officer", start_date="2015-04-01")
    v = classify_engagement_dupe(
        org="Ford", role="Engineer", start_date="2015-04-15",
        existing=[entry], org_getter=lambda w: w.company,
    )
    assert v.match is None
    assert v.ambiguous == [entry]


def test_engagement_two_token_org_containment_with_equal_month_matches():
    """A 2+-token containment (legal-form/short-form pair) still counts as
    identity — the strict rule targets bare SINGLE-token containment only."""
    entry = WorkEntry(company="Continental Automotive GmbH", role="Software Engineer",
                      start_date="2015-04-01")
    v = classify_engagement_dupe(
        org="Continental Automotive", role="Senior Software Engineer", start_date="2015-04-15",
        existing=[entry], org_getter=lambda w: w.company,
    )
    assert v.match is entry


# ── #181 review (item 3): strong-org + unconfirmed dates + role signal ──


def test_engagement_strong_org_no_dates_no_role_is_ambiguous_not_appended():
    """#181: org SAME, no start months to confirm, and NO role evidence — the old
    rule appended silently. Strict ADR-046 asks instead."""
    entry = WorkEntry(company="Continental Automotive GmbH", role="Software Engineer")
    v = classify_engagement_dupe(
        org="Continental Automotive", role=None, start_date=None,
        existing=[entry], org_getter=lambda w: w.company,
    )
    assert v.match is None
    assert v.ambiguous == [entry]


def test_engagement_strong_org_no_dates_distinct_role_still_appends():
    """A clearly different role at the same employer with no confirming dates is a
    genuine second position — append (empty verdict), don't nag."""
    entry = WorkEntry(company="Continental Automotive GmbH", role="Software Engineer")
    v = classify_engagement_dupe(
        org="Continental Automotive", role="Chief Financial Officer", start_date=None,
        existing=[entry], org_getter=lambda w: w.company,
    )
    assert v.match is None
    assert not v.ambiguous
