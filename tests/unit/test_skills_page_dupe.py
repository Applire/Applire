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

"""#386 (E049 clause-6 disposition) — the PAGE-scope duplicate predicate.

Every positive pair below shipped as a visible duplicate cluster on the charter
run 10 delivered CV (operations_marcus_de, 2026-07-31). ``skills_page_dupe`` is
deliberately WIDER than ``skills_near_dupe``: the vault-merge predicate must not
collapse 'React'/'React Native' (two real skills, #172), but on one rendered
skills list they read as one competence twice."""

import pytest

from applire.services.ats_audit import skills_near_dupe, skills_page_dupe


# The six delivered duplicate clusters from run 10 (#386), plus the German
# compound pairs token-set rules structurally cannot see.
RUN_10_CLUSTERS = [
    ("MES", "MES (Maschinendaten- und Betriebsdatenerfassung)"),
    ("KVP", "KVP (Kontinuierlicher Verbesserungsprozess)"),
    ("Lean", "Lean Management"),
    ("Shopfloor", "Shopfloor-Management"),
    ("Führung", "Führung gewerblicher Teams"),
    ("Dreischichtbetrieb", "Schichtbetrieb"),   # compound suffix
    ("Führung", "Mitarbeiterführung"),          # compound suffix
]


@pytest.mark.parametrize("a,b", RUN_10_CLUSTERS)
def test_run10_cluster_pairs_are_page_dupes(a, b):
    assert skills_page_dupe(a, b), (a, b)
    assert skills_page_dupe(b, a), "must be symmetric"


@pytest.mark.parametrize(
    "a,b",
    [
        ("Java", "JavaScript"),          # not a suffix of a token, not containment
        ("Python", "FastAPI"),
        ("SAP PP", "SAP MM"),            # sibling modules are DISTINCT skills
        ("ISO 9001", "ISO 45001"),       # distinct norms
        ("Qualität", "Supply Chain"),
    ],
)
def test_distinct_competences_are_not_page_dupes(a, b):
    assert not skills_page_dupe(a, b), (a, b)


def test_short_suffix_never_collapses():
    # 'Lean' (4 chars) may relate by containment/token rules but a CHARACTER
    # suffix under 6 chars must never fire — generic word endings collapse
    # unrelated names otherwise.
    assert not skills_page_dupe("Bau", "Anlagenbau".replace("Anlagen", "Xyzqw"))


def test_page_predicate_is_a_strict_superset_of_the_merge_predicate():
    for a, b in RUN_10_CLUSTERS:
        if skills_near_dupe(a, b):
            assert skills_page_dupe(a, b)
    # And the two predicates genuinely differ — the whole point of #386:
    assert not skills_near_dupe("MES", "MES (Maschinendaten- und Betriebsdatenerfassung)")
    assert skills_page_dupe("MES", "MES (Maschinendaten- und Betriebsdatenerfassung)")
