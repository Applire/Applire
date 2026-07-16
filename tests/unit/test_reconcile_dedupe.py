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

from applire.schemas.profile import EducationEntry, Language
from applire.services.profile.reconcile.dedupe import classify_dupe


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
