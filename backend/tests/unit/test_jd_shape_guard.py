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

"""Unit tests for the deterministic JD-skill shape guard (Wave-6 Task 3).

Belt-and-braces for the prompt-wording fix in job_analysis.py /
review_job_analysis.py: a model under review pressure will still occasionally
emit sentence-shaped entries in required_skills/nice_to_have_skills/keywords.
This guard is deliberately conservative:

  - REPAIR (drop) a sentence-shaped entry ONLY when an equivalent concept-shaped
    entry is already present elsewhere in the same list (all of the concept's
    meaningful tokens are contained in the sentence) — the sentence is then
    provably redundant, not a unique requirement.
  - FLAG-ONLY (leave untouched, log a warning) any sentence-shaped entry with no
    concept-shaped equivalent already present — dropping it would silently
    under-extract a requirement the posting states, and splitting/guessing a
    concept out of it would FABRICATE a requirement never assigned by the JD.
    Both are worse than leaving a slightly-too-long entry in place.
  - Never invents an entry that wasn't already in the input list.

Pinned shapes (role-generic, drawn from the wave-5/6 ground truth in
.run5fixture/ledger.json and jd_chain.jsonl — not personal data; all test data
below is synthetic).
"""

import logging

from applire.services.jd_shape_guard import apply_jd_shape_guard, normalize_skill_shape


# ---------------------------------------------------------------------------
# normalize_skill_shape() — pure list-level behaviour
# ---------------------------------------------------------------------------


class TestGenuineConceptTermsUntouched:
    def test_short_concept_terms_pass_through_unchanged(self):
        entries = ["RAG pipelines", "Embeddings", "AI evaluation", "Technical leadership"]
        normalized, notes = normalize_skill_shape(entries)
        assert normalized == entries
        assert notes == []

    def test_single_word_and_four_word_terms_are_concept_shaped(self):
        entries = ["Python", "Distributed systems design experience"]
        normalized, notes = normalize_skill_shape(entries)
        assert normalized == entries
        assert notes == []


class TestSentenceShapedDuplicateIsDropped:
    def test_drops_sentence_when_equivalent_concept_already_present(self):
        entries = [
            "RAG pipelines",
            "Embeddings",
            "Ranking",
            "Retrieval systems",
            "Production experience with RAG, embeddings, ranking and retrieval pipelines.",
        ]
        normalized, notes = normalize_skill_shape(entries)
        assert "Production experience with RAG, embeddings, ranking and retrieval pipelines." not in normalized
        assert normalized == ["RAG pipelines", "Embeddings", "Ranking", "Retrieval systems"]
        assert len(notes) == 1
        assert "dropped" in notes[0].lower()

    def test_never_invents_a_term_not_already_in_the_list(self):
        entries = [
            "AI evaluation",
            "Strong knowledge of AI evaluation, monitoring and observability",
        ]
        normalized, _notes = normalize_skill_shape(entries)
        # The sentence is dropped because "AI evaluation" already covers it — but
        # "monitoring" and "observability" must NOT be invented as new entries.
        assert normalized == ["AI evaluation"]
        assert "monitoring" not in [e.lower() for e in normalized]
        assert "observability" not in [e.lower() for e in normalized]
        assert set(normalized).issubset(set(entries))


class TestAmbiguousSentenceLeftAloneAndLogged:
    def test_sentence_with_no_concept_equivalent_is_kept_and_logged(self, caplog):
        entries = [
            "Technical leadership",
            "Defining engineering standards and technical best practices",
        ]
        with caplog.at_level(logging.WARNING):
            normalized, notes = normalize_skill_shape(entries)
        # No concept-shaped entry in the list fully covers this sentence's
        # tokens (it names its own distinct requirement) — must not be dropped.
        assert "Defining engineering standards and technical best practices" in normalized
        assert len(notes) == 1
        assert "left in place" in notes[0].lower() or "ambiguous" in notes[0].lower()

    def test_never_splits_a_sentence_into_guessed_concepts(self):
        entries = ["Strategic leadership and hands-on delivery"]
        normalized, _notes = normalize_skill_shape(entries)
        # No equivalent concept present anywhere in the list -> left exactly as-is,
        # not split into ["Strategic leadership", "Hands-on delivery"].
        assert normalized == entries


class TestMalformedInputTolerated:
    def test_none_is_tolerated(self):
        normalized, notes = normalize_skill_shape(None)
        assert normalized in ([], None)
        assert notes == []

    def test_empty_list_is_tolerated(self):
        normalized, notes = normalize_skill_shape([])
        assert normalized == []
        assert notes == []

    def test_non_string_entries_pass_through_untouched(self):
        entries = ["Python", None, 42, "", "  "]
        normalized, notes = normalize_skill_shape(entries)
        assert normalized == entries
        assert notes == []


# ---------------------------------------------------------------------------
# apply_jd_shape_guard() — whole-draft wiring
# ---------------------------------------------------------------------------


class TestApplyJdShapeGuardOnFullDraft:
    def test_applies_to_all_three_fields_independently(self):
        data = {
            "role_title": "Senior AI Engineer",
            "required_skills": [
                "RAG pipelines",
                "Embeddings",
                "Production experience with RAG, embeddings and retrieval pipelines.",
            ],
            "nice_to_have_skills": ["Kubernetes"],
            "keywords": [
                "AI evaluation",
                "Strong knowledge of AI evaluation and observability practices.",
            ],
        }
        result = apply_jd_shape_guard(data)
        assert result["required_skills"] == ["RAG pipelines", "Embeddings"]
        assert result["nice_to_have_skills"] == ["Kubernetes"]
        assert result["keywords"] == ["AI evaluation"]
        # Untouched fields survive unchanged.
        assert result["role_title"] == "Senior AI Engineer"

    def test_missing_keys_are_left_absent_not_added(self):
        data = {"role_title": "Senior AI Engineer"}
        result = apply_jd_shape_guard(data)
        assert "required_skills" not in result

    def test_none_valued_field_is_left_as_none(self):
        data = {"required_skills": None}
        result = apply_jd_shape_guard(data)
        assert result["required_skills"] is None

    def test_non_dict_input_is_returned_unchanged(self):
        assert apply_jd_shape_guard(None) is None
        assert apply_jd_shape_guard([1, 2, 3]) == [1, 2, 3]

    def test_logs_a_warning_for_each_dropped_or_flagged_entry(self, caplog):
        data = {
            "required_skills": [
                "RAG pipelines",
                "Production experience with RAG pipelines and retrieval systems.",
            ],
        }
        with caplog.at_level(logging.WARNING):
            apply_jd_shape_guard(data)
        assert any("jd_shape_guard" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# #675 line 78 / founder-UAT F-6 — the case-folded duplicate
# ---------------------------------------------------------------------------


class TestCaseFoldedDuplicates:
    """`Data science` and `Data Science` from one 2.6k posting, in the same list.

    The extractor reads the posting section by section, so the same concept
    named twice comes back twice. String identity under case folding is a FACT
    (ADR-062 clause 1) — the guard may floor it; "are these two DIFFERENT terms
    the same concept" stays the model's judgement and is out of reach here.
    """

    def test_drops_the_later_case_variant_and_keeps_the_first(self):
        entries = ["Data science", "Machine Learning", "Data Science"]
        normalized, notes = normalize_skill_shape(entries)
        assert normalized == ["Data science", "Machine Learning"]
        assert any("case-folded duplicate" in n for n in notes)

    def test_exact_repeat_is_dropped_too(self):
        normalized, _ = normalize_skill_shape(["LLMOps", "AgentOps", "LLMOps"])
        assert normalized == ["LLMOps", "AgentOps"]

    def test_surrounding_and_inner_whitespace_is_normalised(self):
        normalized, _ = normalize_skill_shape(["Vector databases", " Vector  databases "])
        assert normalized == ["Vector databases"]

    def test_a_longer_term_containing_the_other_is_never_a_duplicate(self):
        """Identity, never similarity: no stemming, no containment, no acronyms."""
        entries = ["Data science", "Data Science Platform", "RAG", "RAG pipelines"]
        normalized, _ = normalize_skill_shape(entries)
        assert normalized == entries

    def test_a_sentence_shaped_repeat_is_also_deduped(self):
        entries = [
            "Production experience with RAG, embeddings and retrieval pipelines",
            "production experience with RAG, embeddings and retrieval pipelines",
        ]
        normalized, notes = normalize_skill_shape(entries)
        assert len(normalized) == 1
        assert any("case-folded duplicate" in n for n in notes)

    def test_dedup_runs_on_all_three_guarded_fields(self):
        data = {
            "required_skills": ["Python", "python"],
            "nice_to_have_skills": ["Kubernetes", "KUBERNETES"],
            "keywords": ["agile", "Agile"],
            # Not a guarded field — the guard must not touch it.
            "company_culture_signals": ["Du-Kultur", "du-kultur"],
        }
        apply_jd_shape_guard(data)
        assert data["required_skills"] == ["Python"]
        assert data["nice_to_have_skills"] == ["Kubernetes"]
        assert data["keywords"] == ["agile"]
        assert data["company_culture_signals"] == ["Du-Kultur", "du-kultur"]

    def test_malformed_entries_are_not_deduped_against_each_other(self):
        """None/blank pass through untouched — two of them are not one."""
        entries = [None, "", "Python", None, "  "]
        normalized, _ = normalize_skill_shape(entries)
        assert normalized == entries
