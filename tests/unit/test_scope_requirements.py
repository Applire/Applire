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

"""ADR-069 clauses 1–3 — scope requirements: fact layer, floor, exemptions.

Pinned case: charter runs 10/12, operations_marcus_de — the JD's
"Gesamtverantwortung … (ca. 120 Mitarbeitende)" (#387/#350) with the vault's
``team_size: 38``. The designed ledger concept is the case README's own row
label, "Führungsspanne ~120 MA".
"""

from applire.services.job import _coerce_scope_requirements
from applire.services.cv_gap_hints import build_gap_hints
from applire.services.keyword_ledger import (
    claimable_surface_forms,
    is_scope_entry,
    reevaluate_gap_ledger_against_vault,
    render_ledger_prompt_block,
    render_ledger_reviewer_block,
    split_ledger_for_prompt,
    upgrade_ledger_for_concepts,
    verified_missing_claimable,
)
from applire.services.scope_requirements import (
    build_scope_ledger_entries,
    build_scope_prompt_block,
    collect_candidate_values,
    scope_concept_label,
)

_JD_TEXT = (
    "Ihre Aufgaben\n"
    "- Gesamtverantwortung für Produktion, Instandhaltung und Arbeitsvorbereitung\n"
    "  (ca. 120 Mitarbeitende, Mehrschichtbetrieb) am Standort Koblenz.\n"
    "- Budget- und Investitionsplanung für den Produktionsbereich.\n"
)

_MARCUS_REQ = {
    "kind": "team_size",
    "value": 120.0,
    "value_max": None,
    "comparator": "approx",
    "quote": "Gesamtverantwortung für Produktion, Instandhaltung und Arbeitsvorbereitung (ca. 120 Mitarbeitende, Mehrschichtbetrieb) am Standort Koblenz.",
    "level": "required",
}

_MARCUS_PROFILE = {
    "work_experience": [
        {
            "role": "Produktionsleiter",
            "company": "Weberit Kunststofftechnik GmbH",
            "team_size": 38,
            "budget_managed": "6 Mio. €",
        },
        {
            "role": "Schichtleiter",
            "company": "Rasselstein Umformtechnik GmbH",
            "team_size": 14,
        },
    ]
}


class TestCoerceScopeRequirements:
    def test_valid_entry_passes_with_quote_present(self):
        kept = _coerce_scope_requirements([_MARCUS_REQ], _JD_TEXT)
        assert len(kept) == 1
        assert kept[0]["kind"] == "team_size"
        assert kept[0]["value"] == 120.0

    def test_quote_absent_from_posting_is_dropped(self):
        fabricated = dict(_MARCUS_REQ, quote="Führung von 500 Mitarbeitenden weltweit.")
        assert _coerce_scope_requirements([fabricated], _JD_TEXT) == []

    def test_unknown_kind_and_bad_value_are_dropped(self):
        assert _coerce_scope_requirements(
            [dict(_MARCUS_REQ, kind="plant_count")], _JD_TEXT
        ) == []
        assert _coerce_scope_requirements(
            [dict(_MARCUS_REQ, value="hundertzwanzig")], _JD_TEXT
        ) == []
        assert _coerce_scope_requirements([dict(_MARCUS_REQ, value=True)], _JD_TEXT) == []

    def test_non_list_and_garbage_tolerated(self):
        assert _coerce_scope_requirements(None, _JD_TEXT) == []
        assert _coerce_scope_requirements("nope", _JD_TEXT) == []
        assert _coerce_scope_requirements([42, "x"], _JD_TEXT) == []


class TestFactLayer:
    def test_concept_label_matches_the_designed_case_row(self):
        assert scope_concept_label(_MARCUS_REQ, "de") == "Führungsspanne ~120 MA"

    def test_budget_labels_render_human_scale(self):
        req = {"kind": "budget", "value": 2500000.0, "comparator": "exact"}
        assert scope_concept_label(req, "en") == "Budget responsibility 2.5M"
        assert scope_concept_label(req, "de") == "Budgetverantwortung 2.5 Mio."
        rng = {"kind": "budget", "value": 500000.0, "value_max": 1000000.0, "comparator": "range"}
        assert scope_concept_label(rng, "en") == "Budget responsibility 500k–1M"

    def test_candidate_values_carry_entry_and_semantics(self):
        values = collect_candidate_values(_MARCUS_PROFILE, "team_size")
        assert [v["value"] for v in values] == [38, 14]
        assert values[0]["entry"] == "Produktionsleiter @ Weberit Kunststofftechnik GmbH"
        assert "direct reports" in values[0]["semantics"]

    def test_budget_values_stay_raw_strings(self):
        values = collect_candidate_values(_MARCUS_PROFILE, "budget")
        assert values == [
            {
                "value": "6 Mio. €",
                "entry": "Produktionsleiter @ Weberit Kunststofftechnik GmbH",
                "semantics": "budget managed in that one role, as the candidate stated it (free text)",
            }
        ]

    def test_prompt_block_carries_bar_and_values(self):
        block = build_scope_prompt_block([_MARCUS_REQ], _MARCUS_PROFILE, "de")
        assert len(block) == 1
        assert block[0]["concept"] == "Führungsspanne ~120 MA"
        assert block[0]["jd_value"] == 120.0
        assert len(block[0]["candidate_values"]) == 2


class TestFloorAndCitation:
    def _block(self, profile=_MARCUS_PROFILE):
        return build_scope_prompt_block([_MARCUS_REQ], profile, "de")

    def test_partial_judgement_flows_to_a_claimable_entry_with_bar(self):
        entries = build_scope_ledger_entries(
            self._block(),
            [
                {
                    "concept": "Führungsspanne ~120 MA",
                    "status": "partial",
                    "reason": "38 direct reports vs a 120-person total span",
                }
            ],
        )
        assert len(entries) == 1
        e = entries[0]
        assert e["status"] == "partial"
        assert e["claimable"] is True
        assert e["fit_weight"] == 1.0
        assert e["surface_forms"] == []
        assert is_scope_entry(e)
        assert e["bar"]["value"] == 120.0
        # SF-GAP.4: evidence is composed from recorded facts, judgement quoted as such.
        assert "ca. 120 Mitarbeitende" in e["evidence"]
        assert "38" in e["evidence"]
        assert "Judgement:" in e["evidence"]

    def test_missing_judgement_lands_as_gap_row_still_exists(self):
        entries = build_scope_ledger_entries(self._block(), None)
        assert len(entries) == 1
        assert entries[0]["status"] == "gap"
        assert entries[0]["claimable"] is False

    def test_no_typed_vault_value_can_never_be_direct(self):
        block = build_scope_prompt_block([_MARCUS_REQ], {"work_experience": []}, "de")
        entries = build_scope_ledger_entries(
            block,
            [
                {
                    "concept": "Führungsspanne ~120 MA",
                    "status": "direct",
                    "reason": "sounds senior",
                    "cited_entry": "Produktionsleiter @ Weberit Kunststofftechnik GmbH",
                }
            ],
        )
        assert entries[0]["status"] == "gap"

    def test_direct_with_unresolvable_citation_downgrades_to_partial(self):
        entries = build_scope_ledger_entries(
            self._block(),
            [
                {
                    "concept": "Führungsspanne ~120 MA",
                    "status": "direct",
                    "reason": "meets the bar",
                    "cited_entry": "Some Role @ Nowhere GmbH",
                }
            ],
        )
        assert entries[0]["status"] == "partial"
        assert entries[0]["bar"]["cited_entry"] is None

    def test_direct_with_resolving_citation_stands(self):
        entries = build_scope_ledger_entries(
            self._block(),
            [
                {
                    "concept": "Führungsspanne ~120 MA",
                    "status": "direct",
                    "reason": "same quantity, meets the bar",
                    "cited_entry": "Produktionsleiter @ Weberit Kunststofftechnik GmbH",
                }
            ],
        )
        assert entries[0]["status"] == "direct"
        assert (
            entries[0]["bar"]["cited_entry"]
            == "Produktionsleiter @ Weberit Kunststofftechnik GmbH"
        )


def _scope_entry(status="partial"):
    return build_scope_ledger_entries(
        build_scope_prompt_block([_MARCUS_REQ], _MARCUS_PROFILE, "de"),
        [{"concept": "Führungsspanne ~120 MA", "status": status, "reason": "r"}],
    )[0]


class TestCoverageExemptions:
    """ADR-069 clause 3 — by predicate, not by the empty-surface_forms
    convention (every consumer falls back ``or [concept]``)."""

    def test_scope_entry_never_in_claimable_surface_forms(self):
        ledger = [
            {
                "concept": "MES",
                "surface_forms": ["MES"],
                "status": "direct",
                "claimable": True,
                "fit_weight": 1.0,
            },
            _scope_entry("partial"),
        ]
        forms = claimable_surface_forms(ledger)
        assert "MES" in forms
        assert all("Führungsspanne" not in f for f in forms)

    def test_scope_entry_never_demanded_by_verified_missing_claimable(self):
        ledger = [_scope_entry("partial")]
        draft = {"summary": "Ein Text ohne die Zahl."}
        assert verified_missing_claimable(draft, ledger) == []

    def test_reevaluate_never_upgrades_a_scope_entry_from_corpus_presence(self):
        entry = _scope_entry("gap")
        entry["status"] = "gap"
        entry["claimable"] = False
        # Vault prose that literally contains the synthesised label AND the number —
        # substring presence must not move a bar's status (SF-GAP.6).
        profile = {
            "work_experience": [
                {
                    "role": "Leiter",
                    "company": "X",
                    "achievements": ["Führungsspanne ~120 MA im Projektbericht erwähnt"],
                }
            ]
        }
        new_ledger, changed = reevaluate_gap_ledger_against_vault([entry], profile)
        assert changed is False
        assert new_ledger[0]["status"] == "gap"

    def test_scope_entry_reaches_neither_writer_prompt_list(self):
        """2026-08-01 adversarial pass finding #1 (BLOCKER): split_ledger_for_prompt
        fed every claimable entry to render_ledger_prompt_block, whose
        ``or [concept]`` fallback told the CV/letter writers to SURFACE the
        JD's own figure ("Führungsspanne ~120 MA") as the candidate's fact."""
        ledger = [
            {
                "concept": "MES",
                "surface_forms": ["MES"],
                "status": "direct",
                "claimable": True,
                "fit_weight": 1.0,
                "evidence": "MES rollout",
            },
            _scope_entry("partial"),
        ]
        claimable, forbidden = split_ledger_for_prompt(ledger)
        assert [e["concept"] for e in claimable] == ["MES"]
        assert all("Führungsspanne" not in c for c in forbidden)
        # And through both render blocks (writers AND reviewers):
        assert "Führungsspanne" not in render_ledger_prompt_block(ledger)
        assert "Führungsspanne" not in render_ledger_reviewer_block(ledger)

    def test_scope_entry_never_becomes_an_editor_gap_hint(self):
        """Finding #2: the section editor rendered the synthesised bar label as
        a 'claimable' chip inviting the candidate to type it into the CV."""
        by_section, general = build_gap_hints(
            ledger=[_scope_entry("partial")],
            category_b=[],
            category_c=[],
            section_contents={"summary": "Ein Absatz."},
        )
        labels = [h.label for hs in by_section.values() for h in hs]
        labels += [h.label for h in general]
        assert all("Führungsspanne" not in label for label in labels)

    def test_upgrade_for_concepts_skips_scope_entries(self):
        entry = _scope_entry("gap")
        entry["status"] = "gap"
        entry["claimable"] = False
        new_ledger, changed = upgrade_ledger_for_concepts(
            [entry], ["Führungsspanne ~120 MA"], "Ich habe 90 Leute geführt."
        )
        assert changed is False
        assert new_ledger[0]["status"] == "gap"
        assert new_ledger[0]["evidence"] != "Ich habe 90 Leute geführt."
