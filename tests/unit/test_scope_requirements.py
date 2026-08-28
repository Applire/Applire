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
        # #562 (2026-08-28): the semantics may not claim more than the vault
        # records — "direct reports" upgraded a functionally led team to a
        # line-management span in a delivered letter.
        assert "direct reports" not in values[0]["semantics"].split("upgraded")[0]
        assert "functional or disciplinary" in values[0]["semantics"]
        assert "does not record which" in values[0]["semantics"]

    def test_budget_values_stay_raw_strings(self):
        # #328 option 4 / #382: the value stays the candidate's own string, now
        # accompanied by its projection provenance. This fixture's entries carry
        # no `role_fact_projections` (a profile written before the projection
        # existed), so the facets read as unbacked — never as `derived`.
        values = collect_candidate_values(_MARCUS_PROFILE, "budget")
        assert values == [
            {
                "value": "6 Mio. €",
                "entry": "Produktionsleiter @ Weberit Kunststofftechnik GmbH",
                "semantics": "budget managed in that one role, as the candidate stated it (free text)",
                "provenance": "uncorroborated",
                "unit": None,
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


# ── ADR-070 — the attested partial is deliverable ────────────────────────────

_DEPUTY_QUOTE = (
    "Urlaubs- und Krankheitsvertretung des Betriebsleiters: Führung des "
    "Gesamtstandorts mit rund 90 Mitarbeitenden, jeweils 2 bis 4 Wochen am Stück"
)

_MARCUS_PROFILE_WITH_TESTIMONY = {
    "work_experience": [
        {
            "role": "Produktionsleiter",
            "company": "Weberit Kunststofftechnik GmbH",
            "team_size": 38,
            "budget_managed": "6 Mio. €",
            "responsibilities": [
                "Führung von 38 Mitarbeitenden im Dreischichtbetrieb",
                _DEPUTY_QUOTE,
            ],
        },
        {
            "role": "Schichtleiter",
            "company": "Rasselstein Umformtechnik GmbH",
            "team_size": 14,
        },
    ]
}

_ATTESTED = {
    "entry": "Produktionsleiter @ Weberit Kunststofftechnik GmbH",
    "quote": _DEPUTY_QUOTE,
    "unit": "Gesamtstandort-Mitarbeiterzahl in Vertretung",
}


def _attested_entries(cls_overrides=None, profile=None, req=_MARCUS_REQ):
    profile = profile if profile is not None else _MARCUS_PROFILE_WITH_TESTIMONY
    cls = {
        "concept": scope_concept_label(req, "de"),
        "status": "partial",
        "reason": "38/14 direct reports vs 120 span; deputy site lead ~90",
        "attested_evidence": dict(_ATTESTED),
    }
    if cls_overrides:
        cls.update(cls_overrides)
    return build_scope_ledger_entries(
        build_scope_prompt_block([req], profile, "de"),
        [cls],
        profile_json=profile,
    )


class TestAttestedEvidence:
    """ADR-070 clause 1 — the model cites vault prose with a stated unit;
    code verifies the quote resolves and carries a figure, fail-closed."""

    def test_verified_attestation_stored_on_bar(self):
        e = _attested_entries()[0]
        assert e["status"] == "partial"
        att = e["bar"]["attested"]
        assert att["quote"] == _DEPUTY_QUOTE
        assert att["unit"] == "Gesamtstandort-Mitarbeiterzahl in Vertretung"
        assert att["entry"] == "Produktionsleiter @ Weberit Kunststofftechnik GmbH"
        # The attested fact joins the composed evidence string (SF-GAP.4).
        assert "rund 90 Mitarbeitenden" in e["evidence"]

    def test_quote_with_typographic_punctuation_still_resolves(self):
        # The model may fold whitespace / typographic dashes; NFKC + fold must
        # still resolve the quote (the U+2019 lesson, 2026-07-11).
        mangled = _DEPUTY_QUOTE.replace(" 2 bis 4 ", "  2 bis 4 ").replace("-", "‐")
        e = _attested_entries({"attested_evidence": dict(_ATTESTED, quote=mangled)})[0]
        assert e["bar"]["attested"] is not None

    def test_unresolvable_quote_is_dropped(self):
        e = _attested_entries(
            {"attested_evidence": dict(_ATTESTED, quote="Führung von 200 Personen weltweit")}
        )[0]
        assert e["bar"].get("attested") is None
        assert e["status"] == "partial"  # typed values still carry the partial

    def test_quote_without_a_figure_is_dropped(self):
        profile = {
            "work_experience": [
                {
                    "role": "Produktionsleiter",
                    "company": "Weberit Kunststofftechnik GmbH",
                    "team_size": 38,
                    "responsibilities": ["Vertretung des Betriebsleiters bei Abwesenheit"],
                }
            ]
        }
        e = _attested_entries(
            {
                "attested_evidence": {
                    "entry": "Produktionsleiter @ Weberit Kunststofftechnik GmbH",
                    "quote": "Vertretung des Betriebsleiters bei Abwesenheit",
                    "unit": "deputy duty",
                }
            },
            profile=profile,
        )[0]
        assert e["bar"].get("attested") is None

    def test_missing_unit_is_dropped(self):
        e = _attested_entries({"attested_evidence": dict(_ATTESTED, unit="")})[0]
        assert e["bar"].get("attested") is None

    def test_entry_label_is_resolved_by_code_not_the_model(self):
        # Model cites the wrong entry label but the quote resolves in a real
        # entry's prose — code stores the RESOLVED label (ADR-061 discipline).
        e = _attested_entries(
            {"attested_evidence": dict(_ATTESTED, entry="Someone @ Somewhere")}
        )[0]
        att = e["bar"]["attested"]
        assert att is not None
        assert att["entry"] == "Produktionsleiter @ Weberit Kunststofftechnik GmbH"

    def test_partial_floor_lifted_by_verified_attestation(self):
        # No typed values at all — prose testimony alone now supports partial.
        profile = {
            "work_experience": [
                {
                    "role": "Produktionsleiter",
                    "company": "Weberit Kunststofftechnik GmbH",
                    "responsibilities": [_DEPUTY_QUOTE],
                }
            ]
        }
        e = _attested_entries(profile=profile)[0]
        assert e["status"] == "partial"
        assert e["claimable"] is True
        assert e["bar"]["attested"] is not None

    def test_partial_without_values_or_attestation_still_floors_to_gap(self):
        profile = {"work_experience": []}
        e = _attested_entries({"attested_evidence": None}, profile=profile)[0]
        assert e["status"] == "gap"

    def test_direct_without_typed_citation_stays_impossible_with_attestation(self):
        profile = {
            "work_experience": [
                {
                    "role": "Produktionsleiter",
                    "company": "Weberit Kunststofftechnik GmbH",
                    "responsibilities": [_DEPUTY_QUOTE],
                }
            ]
        }
        e = _attested_entries({"status": "direct"}, profile=profile)[0]
        # direct requires a same-quantity TYPED value; attested lifts to partial only.
        assert e["status"] == "partial"

    # ── ADR-070 amended 2026-08-02 (#421) — word-number figures + citation hygiene ──

    def test_word_number_quote_passes_the_figure_gate(self):
        # Run-14 Emma, verbatim: small German team sizes are written as words,
        # so the digit-only gate starved exactly the small-team case (#421).
        word_quote = (
            "Fachliche Führung von zwei Werkstudierenden und einer "
            "Junior-Controllerin im Tagesgeschäft"
        )
        profile = {
            "work_experience": [
                {
                    "role": "Controllerin",
                    "company": "Schwarzwald Präzision GmbH",
                    "responsibilities": [word_quote],
                }
            ]
        }
        e = _attested_entries(
            {
                "attested_evidence": {
                    "entry": "Controllerin @ Schwarzwald Präzision GmbH",
                    "quote": word_quote,
                    "unit": "fachlich geführte Personen",
                }
            },
            profile=profile,
        )[0]
        att = e["bar"]["attested"]
        assert att is not None
        assert att["quote"] == word_quote
        # No typed value in the profile — the verified attestation lifts the floor.
        assert e["status"] == "partial"

    def test_bare_german_article_is_not_a_figure(self):
        # "einer" is the article, not the number — admitting it would let a
        # figure-free quote through the gate (fail-closed direction preserved).
        article_quote = "Führung einer Junior-Controllerin im Tagesgeschäft"
        profile = {
            "work_experience": [
                {
                    "role": "Controllerin",
                    "company": "Schwarzwald Präzision GmbH",
                    "responsibilities": [article_quote],
                }
            ]
        }
        e = _attested_entries(
            {
                "attested_evidence": {
                    "entry": "Controllerin @ Schwarzwald Präzision GmbH",
                    "quote": article_quote,
                    "unit": "fachlich geführte Personen",
                }
            },
            profile=profile,
        )[0]
        assert e["bar"].get("attested") is None
        assert e["status"] == "gap"

    def test_english_word_number_quote_passes_the_figure_gate(self):
        word_quote = "Functional lead for two working students in daily operations"
        profile = {
            "work_experience": [
                {
                    "role": "Controller",
                    "company": "Schwarzwald Präzision GmbH",
                    "responsibilities": [word_quote],
                }
            ]
        }
        e = _attested_entries(
            {
                "attested_evidence": {
                    "entry": "Controller @ Schwarzwald Präzision GmbH",
                    "quote": word_quote,
                    "unit": "functionally led people",
                }
            },
            profile=profile,
        )[0]
        assert e["bar"]["attested"] is not None

    def test_floor_to_gap_clears_stale_cited_entry(self):
        # Run-14 Emma shipped status "gap" with the model's unresolvable
        # citation string still on the bar (#421's hygiene half).
        profile = {"work_experience": []}
        e = _attested_entries(
            {
                "status": "partial",
                "attested_evidence": None,
                "cited_entry": "Controllerin @ Schwarzwald Präzision GmbH",
            },
            profile=profile,
        )[0]
        assert e["status"] == "gap"
        assert e["bar"]["cited_entry"] is None

    def test_cited_entry_persists_only_on_validated_direct(self):
        # The prompt contract says cited_entry is REQUIRED for direct and
        # omitted otherwise; only the direct path validates it, so a non-direct
        # status never carries one (bookkeeping-is-not-testimony).
        e = _attested_entries(
            {"status": "partial", "cited_entry": "Nonexistent @ Nowhere"}
        )[0]
        assert e["status"] == "partial"
        assert e["bar"]["cited_entry"] is None

    def test_legacy_call_without_profile_still_works(self):
        # build_scope_ledger_entries stays callable without profile_json —
        # attestation silently unavailable, everything else unchanged.
        entries = build_scope_ledger_entries(
            build_scope_prompt_block([_MARCUS_REQ], _MARCUS_PROFILE, "de"),
            [
                {
                    "concept": "Führungsspanne ~120 MA",
                    "status": "partial",
                    "reason": "r",
                    "attested_evidence": dict(_ATTESTED),
                }
            ],
        )
        assert entries[0]["status"] == "partial"
        assert entries[0]["bar"].get("attested") is None


class TestScopePositioningBlock:
    """ADR-070 clause 2 — candidate side only; the block can never emit the
    JD's own figure (no concept, no bar.value, no bar.quote)."""

    def _partial_with_attested(self):
        return _attested_entries()[0]

    def test_partial_with_attested_renders_candidate_side_only(self):
        from applire.services.scope_requirements import render_scope_positioning_block

        block = render_scope_positioning_block([self._partial_with_attested()], "de")
        assert "POSITIONING: SCOPE" in block
        assert _DEPUTY_QUOTE in block
        assert "Gesamtstandort-Mitarbeiterzahl in Vertretung" in block  # the unit
        assert "38" in block  # typed value with semantics
        assert "direct reports" in block
        assert "Führungsspanne" in block  # kind label, number-free
        # The JD's own figure appears NOWHERE in the block, in any form:
        assert "120" not in block
        assert "ca. 120" not in block
        assert "Führungsspanne ~120 MA" not in block
        assert _MARCUS_REQ["quote"] not in block

    def test_direct_and_bare_gap_render_nothing(self):
        from applire.services.scope_requirements import render_scope_positioning_block

        direct = _attested_entries({"status": "direct", "cited_entry": "Produktionsleiter @ Weberit Kunststofftechnik GmbH"})[0]
        assert render_scope_positioning_block([direct], "de") == ""
        gap = _scope_entry("gap")
        gap["status"] = "gap"
        gap["claimable"] = False
        gap["bar"]["candidate_values"] = []
        assert render_scope_positioning_block([gap], "de") == ""

    def test_partial_with_typed_values_but_no_attestation_still_renders(self):
        e = _scope_entry("partial")
        from applire.services.scope_requirements import render_scope_positioning_block

        block = render_scope_positioning_block([e], "de")
        assert "38" in block
        assert "120" not in block

    def test_typed_value_without_attestation_is_never_called_attested(self):
        """#562 — the testimony layer itself is honest: a merely-typed candidate
        value (no ADR-070 verified attestation) renders as "typed vault value",
        never as an "attested" claim about THAT figure. This is what let the actual
        defect be traced to the CONSUMER prompt (review_cover_letter.py), not to
        this builder: a real run (2026-08-19) had the letter reviewer demand "the
        candidate's attested Führungsspanne of 480" for exactly this shape — no
        attested_evidence was ever classified for that entry, per this fixture's
        own bar.attested. The block's shared legend explains the two labels in
        general terms (so the word "attested" appears once, defining the term) —
        the per-item concrete marker is what must never fire on an unattested value.
        """
        e = _scope_entry("partial")
        assert e["bar"].get("attested") is None  # sanity: genuinely unattested
        from applire.services.scope_requirements import render_scope_positioning_block

        block = render_scope_positioning_block([e], "de")
        assert "38" in block
        assert "typed vault value" in block
        # The concrete per-item marker (with its opening quote) is the FALSE claim
        # this defect produced — it must never render for an unattested value.
        assert 'attested in the vault: "' not in block
        assert "the attested statement" not in block  # the old, unconditional phrasing

    def test_attested_entry_is_labelled_distinctly_from_a_typed_value(self):
        """The companion case: when a verified attestation DOES exist, the block
        marks that line "attested in the vault" — distinct wording from "typed
        vault value" on the same block, never conflated into one label."""
        from applire.services.scope_requirements import render_scope_positioning_block

        e = self._partial_with_attested()
        assert e["bar"]["attested"] is not None
        block = render_scope_positioning_block([e], "de")
        assert "typed vault value" in block
        assert "attested in the vault" in block

    def test_empty_ledger_renders_empty(self):
        from applire.services.scope_requirements import render_scope_positioning_block

        assert render_scope_positioning_block(None, "de") == ""
        assert render_scope_positioning_block([], "de") == ""
        # Non-scope entries contribute nothing.
        assert (
            render_scope_positioning_block(
                [{"concept": "MES", "claimable": True, "status": "partial"}], "de"
            )
            == ""
        )


class TestClause5SeamPredicates:
    """ADR-070 clause 5 — the four consumers the recon found unpredicated."""

    def test_unaddressed_hard_requirements_skips_scope_entries(self):
        from applire.services.cross_document import find_unaddressed_hard_requirements

        gap = _scope_entry("gap")
        gap["status"] = "gap"
        gap["claimable"] = False
        result = find_unaddressed_hard_requirements([gap], None)
        assert result == []

    def test_vault_evidence_never_anchors_a_scope_entry(self):
        from applire.services.vault_evidence import _claimable_entries

        assert _claimable_entries([_scope_entry("partial")]) == []

    def test_cv_budget_relevance_ignores_scope_entries(self):
        from datetime import date

        from applire.services.cv_budget import compute_bullet_budgets

        # A work entry whose text literally contains the scope label must not
        # gain relevance from the scope entry's concept fallback.
        entries = [
            {
                "id": "a",
                "start_date": "2020-01",
                "end_date": None,
                "is_current": True,
                "responsibilities": ["Führungsspanne ~120 MA im Bericht erwähnt"],
            }
        ]
        with_scope = compute_bullet_budgets(
            entries, [_scope_entry("partial")], 2, today=date(2026, 8, 1)
        )
        without = compute_bullet_budgets(entries, None, 2, today=date(2026, 8, 1))
        assert {k: r.max_bullets for k, r in with_scope.roles.items()} == {
            k: r.max_bullets for k, r in without.roles.items()
        }
        # And the label never rides along as relevance "hit data" either.
        assert all("Führungsspanne" not in f for f in with_scope.claimable_forms)

    def test_load_bearing_universe_skips_scope_entries(self):
        from applire.services.load_bearing import (
            is_load_bearing,
            load_bearing_universe_from_ledger,
        )

        budget_req = {
            "kind": "budget",
            "value": 2500000.0,
            "value_max": None,
            "comparator": "exact",
            "quote": "Budgetverantwortung von 2,5 Mio. €",
            "level": "required",
        }
        e = build_scope_ledger_entries(
            build_scope_prompt_block([budget_req], _MARCUS_PROFILE, "de"),
            [
                {
                    "concept": scope_concept_label(budget_req, "de"),
                    "status": "direct",
                    "reason": "6 Mio. € managed",
                    "cited_entry": "Produktionsleiter @ Weberit Kunststofftechnik GmbH",
                }
            ],
        )[0]
        assert e["status"] == "direct"  # typed budget value + resolving citation
        assert is_load_bearing(e) is False
        assert load_bearing_universe_from_ledger([e]) == frozenset()


class TestWriterPromptWiring:
    """ADR-070 clauses 2–3 — the block reaches both writers; the letter
    reviewer/corrector know the new positioning key."""

    def test_cv_prompt_carries_scope_positioning_block(self):
        from applire.prompts.cv_tailoring import build_user_prompt

        prompt = build_user_prompt(
            {"role_title": "Betriebsleiter"},
            {"work_experience": []},
            [],
            scope_positioning_block="=== POSITIONING: SCOPE (ADR-070) ===\nTESTMARKER",
        )
        assert "TESTMARKER" in prompt
        # Omitted → adds nothing (legacy callers unchanged).
        assert "POSITIONING: SCOPE" not in build_user_prompt(
            {"role_title": "x"}, {"work_experience": []}, []
        )

    def test_cover_letter_prompt_carries_scope_positioning_block(self):
        from applire.prompts.cover_letter import build_cover_letter_prompt

        prompt = build_cover_letter_prompt(
            cv_data={},
            jd_text="JD text",
            pre_gen_inputs={},
            detected_language="de",
            scope_positioning_block="=== POSITIONING: SCOPE (ADR-070) ===\nTESTMARKER",
        )
        assert "TESTMARKER" in prompt

    def test_gap_analysis_prompt_declares_attested_evidence(self):
        from applire.prompts.gap_analysis import SYSTEM_PROMPT

        assert "attested_evidence" in SYSTEM_PROMPT
        assert '"unit"' in SYSTEM_PROMPT
        # The anti-presence rule survives (status still never moves on presence).
        assert "NEVER classify a scope requirement from the presence" in SYSTEM_PROMPT

    def test_letter_reviewer_and_corrector_know_scope_positioning(self):
        from applire.prompts import review_cover_letter as rcl

        assert "scope_positioning" in rcl.REVIEW_SYSTEM_PROMPT
        assert "scope_positioning" in rcl.COVER_LETTER_REFINEMENT_PROMPT
