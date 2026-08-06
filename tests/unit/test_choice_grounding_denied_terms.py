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

"""#451 — declared per-term polarity (ADR-064 amended 2026-08-06).

Charter run 17 delivered denial-ONLY choice sets on 4 of 6 choice-bearing
turns: the #347 coverage rules require a partial chip to NAME the exact
unevidenced concepts it denies, and the polarity-blind term-evidence check
read each such mention as an assertion and dropped the chip. The generator now
declares ``denied_terms`` per choice and ``filter_ungrounded_choices`` reads
it as a fact. The regression fixtures below are the run's own drafted chips
(LLM log 2026-08-06, records 30/42/46/50), lightly condensed.
"""

from applire.services.choice_grounding import filter_ungrounded_choices
from applire.services.interview_graph import _carry_levels_by_index

# Run-17 turn 1: cluster "Führungskompetenz und Verantwortung" — the
# candidate evidences Budgetverantwortung and Führung (38 MA / 90 deputy)
# but neither Investitionsverantwortung nor a ~120-MA span.
CLUSTER = {
    "id": "cluster-leadership",
    "label": "Führungskompetenz und Verantwortung",
    "gaps": ["Investitionsverantwortung", "Führungsspanne ~120 MA"],
    "jd_skills": ["Führungserfahrung", "Budgetverantwortung"],
    "jd_context": "Nachweisbare Führungserfahrung und Budgetverantwortung",
}

PROFILE = {
    "skills": [{"name": "Budgetverantwortung"}, {"name": "Mitarbeiterführung"}],
    "work_experience": [
        {
            "id": "w-weberit",
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "technologies": [],
            "responsibilities": [
                "Budgetverantwortung von ca. 6 Mio. € (Personal, Instandhaltung, Material-Gemeinkosten)",
                "Führung von 38 Mitarbeitenden über drei Schichtleiter",
            ],
            "achievements": [
                "Vertretung des Betriebsleiters für den Gesamtstandort mit rund 90 Mitarbeitenden"
            ],
        }
    ],
}

# The run-17 drafted partial chips, verbatim shapes.
PARTIAL_NO_EMPLOYER = {
    "text": (
        "Ich habe Teams bis zu 38 Mitarbeitenden geführt, aber keine Erfahrung mit "
        "Investitionsverantwortung oder einer Führungsspanne von ~120 Mitarbeitenden."
    ),
    "level": "partial",
    "denied_terms": ["Investitionsverantwortung", "Führungsspanne ~120 MA"],
}

PARTIAL_WITH_EMPLOYER = {
    "text": (
        "Ich hatte Budgetverantwortung von ca. 6 Mio. € bei Weberit Kunststofftechnik GmbH, "
        "aber keine Investitionsverantwortung oder Führungsspanne von ~120 Mitarbeitenden."
    ),
    "level": "partial",
    "denied_terms": ["Investitionsverantwortung", "Führungsspanne ~120 MA"],
}


class TestDeclaredDeniedTermsExemption:
    def test_run17_partial_chip_without_employer_is_kept(self):
        kept = filter_ungrounded_choices([PARTIAL_NO_EMPLOYER], CLUSTER, PROFILE, "C")
        assert kept == [PARTIAL_NO_EMPLOYER["text"]]

    def test_run17_partial_chip_naming_its_employer_is_kept(self):
        kept = filter_ungrounded_choices([PARTIAL_WITH_EMPLOYER], CLUSTER, PROFILE, "C")
        assert kept == [PARTIAL_WITH_EMPLOYER["text"]]

    def test_same_chip_without_the_field_still_runs_the_full_pipeline(self):
        # Legacy shape (no denied_terms): pre-#451 behaviour unchanged — the
        # chip names unevidenced concepts and is dropped. This pins the safe
        # fallback, and documents the exact run-17 loss the field repairs.
        legacy = {k: v for k, v in PARTIAL_NO_EMPLOYER.items() if k != "denied_terms"}
        assert filter_ungrounded_choices([legacy], CLUSTER, PROFILE, "C") is None

    def test_denial_first_partial_is_kept(self):
        # Run-17 turn "MES/Digitalisierung" shape: the denial leads, the
        # bridge follows — clause ORDER must not matter to the declaration.
        chip = {
            "text": (
                "Ich habe zwar keine Erfahrung mit Investitionsverantwortung, aber "
                "Budgetverantwortung von ca. 6 Mio. € getragen."
            ),
            "level": "partial",
            "denied_terms": ["Investitionsverantwortung"],
        }
        kept = filter_ungrounded_choices([chip], CLUSTER, PROFILE, "C")
        assert kept == [chip["text"]]

    def test_bald_assertion_with_declared_denial_gets_no_exemption(self):
        # The adversarial-pass bypass: assert the concept in text while
        # declaring it denied. No pivot marker → no exemption → dropped.
        chip = {
            "text": "Ich habe die Investitionsverantwortung bei uns erfolgreich eingeführt.",
            "level": "partial",
            "denied_terms": ["Investitionsverantwortung"],
        }
        assert filter_ungrounded_choices([chip], CLUSTER, PROFILE, "C") is None

    def test_declared_denial_of_an_evidenced_concept_drops_the_chip(self, caplog):
        # #347 mirror condition extended: declaring a denial of a concept the
        # profile evidences is a contradicted denial — one click would record
        # it as testimony.
        chip = {
            "text": (
                "Ich habe keine Erfahrung mit Budgetverantwortung, aber Teams "
                "von 38 Mitarbeitenden geführt."
            ),
            "level": "partial",
            "denied_terms": ["Budgetverantwortung"],
        }
        with caplog.at_level("WARNING"):
            assert filter_ungrounded_choices([chip], CLUSTER, PROFILE, "C") is None
        assert "mirror check" in caplog.text

    def test_spanning_set_survives_delivery(self):
        # The #451 headline: drafted partial + denial must BOTH survive.
        denial = {
            "text": (
                "Ich habe weder Investitionsverantwortung noch eine Führungsspanne "
                "von ~120 Mitarbeitenden innegehabt."
            ),
            "level": "denial",
            "denied_terms": ["Investitionsverantwortung", "Führungsspanne ~120 MA"],
        }
        kept = filter_ungrounded_choices(
            [PARTIAL_NO_EMPLOYER, denial], CLUSTER, PROFILE, "C"
        )
        assert kept == [PARTIAL_NO_EMPLOYER["text"], denial["text"]]


SAP_CLUSTER = {
    "id": "cluster-sap",
    "label": "SAP-Module",
    "gaps": ["SAP PP"],
    "jd_skills": ["SAP"],
    "jd_context": "SAP PP in der Fertigung",
}

SAP_FREE_PROFILE = {
    "skills": [{"name": "Qualitätsmanagement"}],
    "work_experience": [
        {
            "company": "MedTech GmbH",
            "role": "QA Engineer",
            "technologies": [],
            "responsibilities": ["Qualitätsmanagement im Labor"],
            "achievements": [],
        }
    ],
}


class TestSubtermScoping:
    def test_compound_only_mention_exempts_the_riding_subterm(self):
        # "SAP" appears ONLY inside the denied "SAP PP" — demanding evidence
        # for it would re-drop the honest compound-denial shape (#351).
        chip = {
            "text": "Ich habe Qualitätsmanagement betrieben, aber keine Erfahrung mit SAP PP.",
            "level": "partial",
            "denied_terms": ["SAP PP"],
        }
        kept = filter_ungrounded_choices([chip], SAP_CLUSTER, SAP_FREE_PROFILE, "C")
        assert kept == [chip["text"]]

    def test_independently_asserted_subterm_keeps_its_evidence_requirement(self):
        # "SAP" also appears OUTSIDE the denied compound, as an assertion the
        # profile does not back — the declared "SAP PP" must not launder it.
        chip = {
            "text": "Ich nutze SAP täglich, aber keine Erfahrung mit SAP PP.",
            "level": "partial",
            "denied_terms": ["SAP PP"],
        }
        assert filter_ungrounded_choices([chip], SAP_CLUSTER, SAP_FREE_PROFILE, "C") is None

    def test_declared_shorter_term_never_exempts_the_longer_one(self):
        # The reverse direction: declaring bare "SAP" denied must not exempt
        # an asserted, unevidenced "SAP PP".
        chip = {
            "text": "Ich habe SAP PP eingeführt, aber sonst kein SAP genutzt.",
            "level": "partial",
            "denied_terms": ["SAP"],
        }
        assert filter_ungrounded_choices([chip], SAP_CLUSTER, SAP_FREE_PROFILE, "C") is None


class TestCarryDeniedTermsByIndex:
    def test_missing_denied_terms_is_filled_in_from_the_pre_review_choice(self):
        pre = [{"text": "orig", "level": "partial", "denied_terms": ["X"]}]
        reviewed = [{"text": "übersetzt", "level": "partial"}]
        carried = _carry_levels_by_index(pre, reviewed)
        assert carried == [
            {"text": "übersetzt", "level": "partial", "denied_terms": ["X"]}
        ]

    def test_reviewed_choice_with_its_own_list_is_trusted(self):
        pre = [{"text": "orig", "level": "partial", "denied_terms": ["X"]}]
        reviewed = [{"text": "übersetzt", "level": "partial", "denied_terms": ["Y"]}]
        carried = _carry_levels_by_index(pre, reviewed)
        assert carried[0]["denied_terms"] == ["Y"]

    def test_bare_string_gets_both_fields_reattached(self):
        pre = [{"text": "orig", "level": "denial", "denied_terms": ["X"]}]
        carried = _carry_levels_by_index(pre, ["übersetzt"])
        assert carried == [
            {"text": "übersetzt", "level": "denial", "denied_terms": ["X"]}
        ]

    def test_count_mismatch_falls_back_to_reviewed_output(self):
        pre = [{"text": "a", "level": "denial", "denied_terms": ["X"]}]
        reviewed = [{"text": "a"}, {"text": "b"}]
        assert _carry_levels_by_index(pre, reviewed) == reviewed


class TestPromptSchemas:
    def test_both_generator_schemas_declare_denied_terms(self):
        # The adversarial pass found only Mode A test-pinned — the transfer
        # probe carries its own schema literal and must move in lockstep.
        from applire.prompts.interview import (
            DENIAL_PROBE_QUESTION_SYSTEM_PROMPT,
            QUESTION_SYSTEM_PROMPT,
        )

        assert '"denied_terms"' in QUESTION_SYSTEM_PROMPT
        assert '"denied_terms"' in DENIAL_PROBE_QUESTION_SYSTEM_PROMPT

    def test_language_pass_preserves_denied_terms(self):
        from applire.prompts.review_question_language import (
            QUESTION_LANGUAGE_REFINEMENT_PROMPT,
            QUESTION_LANGUAGE_REVIEW_SYSTEM_PROMPT,
        )

        assert "denied_terms" in QUESTION_LANGUAGE_REVIEW_SYSTEM_PROMPT
        assert "denied_terms" in QUESTION_LANGUAGE_REFINEMENT_PROMPT
