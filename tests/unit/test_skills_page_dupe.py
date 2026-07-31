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


def test_slash_compound_containment_is_a_page_dupe():
    # Charter run 11 (2026-07-31): the writer's honest 'SAP PP/MM' next to the
    # guarantee-re-added vault 'SAP PP' shipped as two entries — the slash
    # keeps 'pp/mm' one token for the vault-merge scope, so only the
    # page-scope expansion can see the containment.
    assert skills_page_dupe("SAP PP", "SAP PP/MM")
    assert skills_page_dupe("SAP MM", "SAP PP/MM")
    assert not skills_page_dupe("SAP PP", "SAP MM")  # sibling modules stay distinct
    assert not skills_near_dupe("SAP PP", "SAP PP/MM")  # vault merge unchanged


def test_acronym_spelling_guard_does_not_fire_on_compound_labels():
    # Charter run 11: 'SAP PP/MM' is NOT a mangled spelling of vault 'SAP PP' —
    # rewriting deleted the MM half and duplicated 'SAP PP' on the page. The
    # GxP true positive must keep firing.
    from applire.services.cv import _acronym_expansion_vault_match

    assert _acronym_expansion_vault_match("SAP PP/MM", ["SAP PP", "SAP"]) is None
    assert (
        _acronym_expansion_vault_match(
            "Good Practice Compliance & Computer System Validation",
            ["GxP Compliance & Computer System Validation"],
        )
        == "GxP Compliance & Computer System Validation"
    )


def test_spelling_restore_never_introduces_a_duplicate():
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import _restore_skill_spelling

    cv = TailoredCVData.model_validate(
        {"contact": {"name": "x"}, "skills": ["SAP PP", "Sap Pp"]}
    )
    out = _restore_skill_spelling(
        cv, {"skills": [{"name": "SAP PP", "category": "technical"}]}
    )
    assert out.skills == ["SAP PP"]


def test_page_predicate_is_a_strict_superset_of_the_merge_predicate():
    for a, b in RUN_10_CLUSTERS:
        if skills_near_dupe(a, b):
            assert skills_page_dupe(a, b)
    # And the two predicates genuinely differ — the whole point of #386:
    assert not skills_near_dupe("MES", "MES (Maschinendaten- und Betriebsdatenerfassung)")
    assert skills_page_dupe("MES", "MES (Maschinendaten- und Betriebsdatenerfassung)")
