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

"""#315 — the shared "load-bearing claim" vocabulary.

Charter run #7 case 2 (``operations_marcus_de``, DE): the ledger concept
``Budget- und Investitionsverantwortung`` (``direct``, ``claimable``,
``fit_weight: 1.0``) reached the delivered CV as a bare keyword
("Budgetverantwortung" in the summary sentence and the skills list) but its
quantified evidence -- "Budgetverantwortung ca. 6 Mio. € (Personal,
Instandhaltung, Material-Gemeinkosten)." -- never landed in a narrative
bullet. Two blind hiring reviewers scored the requirement unmet for want of
exactly that number.

Root cause, pinned against ground truth (backend/logs/llm/2026-07-27.jsonl,
12:03-12:05 UTC): the CV reviewer's OWN round-1 issue named the missing
bullet by text ("work_history[0] missing bullet: Budgetverantwortung (6
Mio. €)"), but the round-1 generator never added it, and round-2 approved
anyway. Neither safety net caught the drop, because both key on
``verified_missing_claimable`` (services/keyword_ledger.py), which scans
the WHOLE serialised draft -- including ``skills`` and ``summary`` -- for a
bare surface-form match. "Budgetverantwortung" was present there from the
very first draft onward, so the concept never registered as "missing" to
either the deterministic coverage gate or the #234 restoration guard
(``services.cv._restore_ledger_bullets``), even though no work-history
bullet ever carried the number. ``cv_budget.condense_to_budget`` never ran
on this bullet at all -- it never existed in ``tailored_data`` to condense.

This module is the shared, explicitly-named notion of "load-bearing claim"
(#315's design constraint): a claimable, ``direct`` ledger concept whose
profile evidence carries a quantified figure. Any cutting/trimming/
restoring step -- on EITHER document chain (CV here, cover letter in #306)
-- must protect it from being reduced to a bare keyword.
"""

from applire.services.keyword_ledger import (
    is_load_bearing,
    tailored_narrative_corpus,
    verified_missing_load_bearing,
)

_BUDGET_ENTRY = {
    "concept": "Budget- und Investitionsverantwortung",
    "surface_forms": [
        "Budget- und Investitionsverantwortung",
        "Budgetverantwortung",
        "Investitionsverantwortung",
    ],
    "sources": ["required"],
    "fit_weight": 1.0,
    "status": "direct",
    "claimable": True,
    "evidence": (
        "Explicitly listed as a skill ('Budgetverantwortung', intermediate) and "
        "work experience (Budget- und Investitionsverantwortung für 6 Mio. €)."
    ),
}

# A claimable, direct concept with NO number in its evidence -- the common
# case (a tool/skill name), must never be flagged load-bearing.
_PLAIN_SKILL_ENTRY = {
    "concept": "SAP (PP/MM)",
    "surface_forms": ["SAP (PP/MM)", "SAP PP", "SAP MM", "SAP"],
    "sources": ["required"],
    "fit_weight": 1.0,
    "status": "direct",
    "claimable": True,
    "evidence": "Explicitly listed as a skill (SAP, expert, 15 years).",
}

# A quantified but ADJACENT/positioning-only entry (ADR-048 amended) --
# status "partial", never "direct" -- must not be treated as load-bearing
# either: the substitute capability, not the JD's own term, is what belongs
# on the page (keyword_ledger.is_positioning_only).
_ADJACENT_QUANTIFIED_ENTRY = {
    "concept": "TOGAF",
    "surface_forms": ["TOGAF"],
    "sources": ["nice_to_have"],
    "fit_weight": 0.5,
    "status": "partial",
    "claimable": True,
    "adjacent_evidence": "arc42",
    "evidence": "Candidate has documented 12 systems using arc42.",
}

# A quantified concept the candidate does NOT hold at all -- not claimable.
_FORBIDDEN_QUANTIFIED_ENTRY = {
    "concept": "PCI DSS Level 1",
    "surface_forms": ["PCI DSS Level 1"],
    "sources": ["keyword"],
    "fit_weight": 0.0,
    "status": "gap",
    "claimable": False,
    "evidence": "",
}


class TestIsLoadBearing:
    def test_direct_claimable_with_quantified_evidence_is_load_bearing(self):
        assert is_load_bearing(_BUDGET_ENTRY) is True

    def test_plain_skill_with_no_figure_is_not_load_bearing(self):
        assert is_load_bearing(_PLAIN_SKILL_ENTRY) is False

    def test_adjacent_positioning_only_entry_is_not_load_bearing(self):
        """Quantified evidence on a 'partial' adjacent entry does not make the
        JD's own (unheld) term load-bearing -- ADR-048's substitution rule
        already governs it; #315 must not fight that."""
        assert is_load_bearing(_ADJACENT_QUANTIFIED_ENTRY) is False

    def test_non_claimable_gap_is_not_load_bearing(self):
        assert is_load_bearing(_FORBIDDEN_QUANTIFIED_ENTRY) is False

    def test_missing_or_empty_evidence_is_not_load_bearing(self):
        entry = {**_BUDGET_ENTRY, "evidence": ""}
        assert is_load_bearing(entry) is False


class TestTailoredNarrativeCorpus:
    def test_excludes_skills_and_summary(self):
        draft = {
            "summary": "Expertise in Budgetverantwortung.",
            "skills": ["Budget- und Investitionsverantwortung"],
            "work_history": [
                {"id": "w1", "bullets": ["Led the migration to Kubernetes."]},
            ],
        }
        corpus = tailored_narrative_corpus(draft)
        assert "budgetverantwortung" not in corpus
        assert "kubernetes" in corpus

    def test_includes_nested_project_bullets(self):
        draft = {
            "work_history": [
                {
                    "id": "w1",
                    "bullets": [],
                    "projects": [{"name": "MES rollout", "bullets": ["OEE stieg auf 73 %."]}],
                }
            ],
        }
        corpus = tailored_narrative_corpus(draft)
        assert "oee stieg auf 73" in corpus

    def test_empty_draft_is_empty_corpus(self):
        assert tailored_narrative_corpus(None) == ""
        assert tailored_narrative_corpus({}) == ""


class TestVerifiedMissingLoadBearing:
    def test_bare_keyword_in_summary_and_skills_still_counts_as_missing(self):
        """The exact charter run #7 shape: the concept is 'present' by
        verified_missing_claimable's whole-document standard (bare keyword in
        summary + skills) but absent from every narrative bullet."""
        draft = {
            "summary": "... und Budgetverantwortung.",
            "skills": ["Budget- und Investitionsverantwortung"],
            "work_history": [
                {"id": "w1", "bullets": ["Führung von 38 Mitarbeitenden."]},
            ],
        }
        missing = verified_missing_load_bearing(draft, [_BUDGET_ENTRY])
        assert [e["concept"] for e in missing] == ["Budget- und Investitionsverantwortung"]

    def test_present_in_a_narrative_bullet_is_not_missing(self):
        draft = {
            "summary": "",
            "skills": [],
            "work_history": [
                {
                    "id": "w1",
                    "bullets": ["Budgetverantwortung ca. 6 Mio. € (Personal, Instandhaltung)."],
                }
            ],
        }
        assert verified_missing_load_bearing(draft, [_BUDGET_ENTRY]) == []

    def test_present_in_a_nested_project_bullet_is_not_missing(self):
        draft = {
            "work_history": [
                {
                    "id": "w1",
                    "bullets": [],
                    "projects": [{"name": "P", "bullets": ["Budgetverantwortung von 6 Mio. €."]}],
                }
            ],
        }
        assert verified_missing_load_bearing(draft, [_BUDGET_ENTRY]) == []

    def test_plain_skill_concepts_are_never_flagged(self):
        """Only load-bearing entries are ever checked here -- a plain skill
        missing its own bullet is #303's territory (selection), not #315's."""
        draft = {"summary": "", "skills": [], "work_history": [{"id": "w1", "bullets": []}]}
        assert verified_missing_load_bearing(draft, [_PLAIN_SKILL_ENTRY]) == []

    def test_no_ledger_is_a_noop(self):
        assert verified_missing_load_bearing({"work_history": []}, None) == []
        assert verified_missing_load_bearing({"work_history": []}, []) == []
