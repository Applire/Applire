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

"""The ADR-061 AFFIRMATIVE invariant (#318): a ``claimable`` ledger row with no
vault evidence must be impossible.

ADR-059's 2026-07-27 amendment asserted the NEGATIVE half at every ledger write
seam — polarity is consulted before any status moves. This is its twin, and the
one half of the doctrine that can be *asserted* rather than argued: whatever the
ledger says the candidate may claim, the vault must be able to back.

Ground truth is charter run #7 case 2 (``operations_marcus_de``, real provider,
German): the post-interview ledger gained ``MES`` and ``OEE`` as
``direct``/``claimable`` while the interview added **zero** skills to the vault —
the stance guard had thrown the same turn's skill ops away. A CV writer acting on
those rows produces a claim the Oracle must then mark ``unbacked``: the pipeline
generating its own truthfulness violation. Run #7 case 1 is the other shape —
eight denied concepts at ``status="direct", claimable=True`` with the candidate's
own denial sentence stored as the backing evidence.

The invariant is deliberately FACT-level (ADR-062 clause 1). It asks the Oracle's
own question — "does at least one vault evidence unit resolve this concept?"
(``ground_skill_claim``, the same predicate the #219 selection guard already
calls) — and never re-litigates the classifier's semantic judgement.
"""

import logging

import pytest

from applire.services.keyword_ledger import (
    DENIED_EVIDENCE,
    assert_claimable_backed,
)

# ── The run-7 case-2 vault: nine real skills, none of them MES or OEE ────────
MARCUS_VAULT = {
    "skills": [
        {"name": "Lean Management"},
        {"name": "SAP", "proficiency": "basic"},
        {"name": "Feinplanung/Fertigungssteuerung"},
        {"name": "ISO 9001"},
        {"name": "Budgetplanung"},
        {"name": "MS Excel"},
        {"name": "Mitarbeiterführung"},
        {"name": "Arbeitsvorbereitung"},
        {"name": "Fertigungssteuerung"},
    ],
    "work_experience": [
        {
            "id": "w1",
            "company": "Weberit GmbH",
            "position": "Produktionsleiter",
            "responsibilities": ["Steuerung der Fertigung über drei Schichten"],
            "achievements": ["Ausschussquote von 4,1 % auf 2,3 % gesenkt"],
        }
    ],
}

RUN7_DENIAL_STATEMENT = (
    "No — I have not worked under a PSD2 licence or with BaFin supervision, so "
    "that regulated exposure is genuinely new to me."
)


def _entry(concept, status="direct", claimable=True, evidence="…", **kw):
    e = {
        "concept": concept,
        "surface_forms": [concept],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": status,
        "evidence": evidence,
        "claimable": claimable,
    }
    e.update(kw)
    return e


class TestTheRun7Divergence:
    """The measured pathology: the ledger more permissive than the evidence."""

    def test_mes_upgraded_from_a_turn_the_vault_never_recorded_is_healed(self):
        # The exact run-7 case-2 row: `direct`, `claimable`, with the candidate's
        # own interview answer as evidence — and nothing in the vault to back it.
        ledger = [
            _entry(
                "MES",
                evidence="Wir haben 2019 ein MES eingeführt und die Linien angebunden.",
            )
        ]
        healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert [v["concept"] for v in violations] == ["MES"]
        assert violations[0]["reason"] == "no_vault_evidence_unit"
        assert healed[0]["claimable"] is False
        assert healed[0]["status"] == "gap"
        assert healed[0]["evidence"] == ""

    def test_oee_is_healed_too_and_the_backed_row_beside_it_survives(self):
        ledger = [
            _entry("OEE", evidence="Die OEE von 61 % auf 73 % gesteigert."),
            _entry("SAP", evidence="SAP-Anwender seit 2011."),
        ]
        healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert [v["concept"] for v in violations] == ["OEE"]
        assert healed[0]["status"] == "gap"
        # SAP IS a vault skill — untouched, byte for byte.
        assert healed[1] == ledger[1]

    def test_a_vault_backed_concept_survives_under_a_different_spelling(self):
        # `ground_skill_claim` resolves near-dupes via the shared skills
        # instrument, so the ledger's label need not match the vault's exactly.
        ledger = [_entry("Fertigungssteuerung", evidence="Steuerung der Fertigung.")]
        healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert violations == []
        assert healed == ledger

    def test_a_concept_only_a_narrative_bullet_carries_still_grounds(self):
        # An evidence unit is any vault text node, not only a `skills[]` row.
        ledger = [_entry("Ausschussquote", evidence="von 4,1 % auf 2,3 % gesenkt")]
        healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert violations == []
        assert healed == ledger


class TestTheDenialAsEvidenceShape:
    """Run #7 case 1 — the denial stored as the backing evidence."""

    def test_the_denied_evidence_sentinel_can_never_sit_on_a_claimable_row(self):
        ledger = [_entry("BaFin supervision", evidence=DENIED_EVIDENCE)]
        healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert violations[0]["reason"] == "denied_evidence"
        assert healed[0]["status"] == "denied"
        assert healed[0]["claimable"] is False
        assert healed[0]["evidence"] == DENIED_EVIDENCE

    def test_a_claimable_row_for_a_persisted_denial_is_healed_to_denied(self):
        profile = {
            **MARCUS_VAULT,
            "metadata": {
                "denied_concepts": [
                    {
                        "concept": "BaFin supervision",
                        "statement": RUN7_DENIAL_STATEMENT,
                        "source": "interview",
                        "date": "2026-07-26",
                    }
                ]
            },
        }
        ledger = [_entry("BaFin supervision", evidence=RUN7_DENIAL_STATEMENT)]
        healed, violations = assert_claimable_backed(ledger, profile)
        assert violations[0]["reason"] == "denied_concept"
        # Exactly what a rebuild of the row writes (`_enforce_denial_stance`),
        # so the two instruments can never disagree about the same concept.
        assert healed[0]["status"] == "denied"
        assert healed[0]["claimable"] is False
        assert healed[0]["evidence"] == DENIED_EVIDENCE

    def test_a_denial_statement_in_the_vault_never_grounds_its_own_concept(self):
        # The denial's verbatim statement is indexed separately from the vault's
        # evidence units, so "PSD2" cannot ground on the sentence denying it.
        profile = {
            **MARCUS_VAULT,
            "metadata": {
                "denied_concepts": [
                    {
                        "concept": "Krypto-Settlement",
                        "statement": RUN7_DENIAL_STATEMENT,
                        "source": "interview",
                        "date": "2026-07-26",
                    }
                ]
            },
        }
        ledger = [_entry("PSD2 licence", evidence="…")]
        healed, violations = assert_claimable_backed(ledger, profile)
        assert violations[0]["reason"] == "no_vault_evidence_unit"
        assert healed[0]["claimable"] is False


class TestEvidenceIntegrity:
    def test_an_evidence_less_claimable_row_is_healed(self):
        # `upgrade_ledger_for_concepts` writes `evidence` only when the caller
        # passes a non-empty string; a blank turn leaves the builder's "" behind.
        ledger = [_entry("SAP", evidence="")]
        healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert violations[0]["reason"] == "no_evidence"
        assert healed[0]["claimable"] is False
        assert healed[0]["status"] == "gap"

    def test_whitespace_is_not_evidence(self):
        ledger = [_entry("SAP", evidence="   \n ")]
        _healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert violations[0]["reason"] == "no_evidence"

    def test_claimable_true_with_an_unclaimable_status_is_incoherent(self):
        ledger = [_entry("SAP", status="gap", claimable=True)]
        healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert violations[0]["reason"] == "status_not_claimable"
        assert healed[0]["claimable"] is False


class TestUnconfirmedIsNotBacking:
    """ADR-061 clause 3 — an ``unconfirmed`` vault entry backs nothing."""

    def test_an_unconfirmed_skill_does_not_back_a_claimable_row(self):
        profile = {
            "skills": [{"name": "MES", "status": "unconfirmed"}],
            "work_experience": [],
        }
        ledger = [_entry("MES", evidence="Wir haben 2019 ein MES eingeführt.")]
        healed, violations = assert_claimable_backed(ledger, profile)
        assert violations[0]["reason"] == "no_vault_evidence_unit"
        assert healed[0]["claimable"] is False

    def test_a_confirmed_skill_of_the_same_name_does_back_it(self):
        profile = {"skills": [{"name": "MES", "status": "confirmed"}], "work_experience": []}
        ledger = [_entry("MES", evidence="Wir haben 2019 ein MES eingeführt.")]
        healed, violations = assert_claimable_backed(ledger, profile)
        assert violations == []
        assert healed == ledger


class TestTheNamedExemptions:
    """ADR-048/ADR-069 already define which claimable rows are NOT a vault claim."""

    def test_a_scope_entry_is_exempt_from_the_vault_backing_demand(self):
        # ADR-069: a scope row's concept is a synthesised label carrying the JD's
        # OWN figure. It is excluded from every claimable consumer already, and
        # its floor + citation check live in `scope_requirements`.
        ledger = [
            _entry(
                "Führungsspanne ~120 MA",
                status="partial",
                evidence='JD bar (headcount): "~120 Mitarbeitende". Vault: 45.',
                surface_forms=[],
                bar={"kind": "headcount", "value": 120, "quote": "~120 Mitarbeitende"},
            )
        ]
        healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert violations == []
        assert healed == ledger

    def test_a_positioning_only_entry_is_exempt_from_the_vault_backing_demand(self):
        # ADR-048 am. 2026-07-27: the candidate does NOT have this term; demanding
        # a vault evidence unit for it contradicts the row's own semantics.
        ledger = [
            _entry(
                "TOGAF",
                status="partial",
                evidence="Adjacent architecture documentation practice.",
                adjacent_evidence="arc42",
            )
        ]
        healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert violations == []
        assert healed == ledger

    def test_an_exempt_row_is_still_subject_to_the_denial_floor(self):
        profile = {
            **MARCUS_VAULT,
            "metadata": {
                "denied_concepts": [
                    {
                        "concept": "TOGAF",
                        "statement": "Nie damit gearbeitet.",
                        "source": "interview",
                        "date": "2026-07-26",
                    }
                ]
            },
        }
        ledger = [
            _entry(
                "TOGAF",
                status="partial",
                evidence="Adjacent architecture documentation practice.",
                adjacent_evidence="arc42",
            )
        ]
        healed, violations = assert_claimable_backed(ledger, profile)
        assert violations[0]["reason"] == "denied_concept"
        assert healed[0]["status"] == "denied"

    def test_an_exempt_row_still_needs_evidence(self):
        ledger = [_entry("TOGAF", status="partial", evidence="", adjacent_evidence="arc42")]
        _healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert violations[0]["reason"] == "no_evidence"


class TestTheInvariantIsNeverSilentAndNeverGratuitous:
    def test_a_violation_is_logged_at_warning_with_the_concept_and_the_reason(self, caplog):
        ledger = [_entry("MES", evidence="Wir haben 2019 ein MES eingeführt.")]
        with caplog.at_level(logging.WARNING, logger="applire.services.keyword_ledger"):
            assert_claimable_backed(ledger, MARCUS_VAULT, seam="test-seam")
        blob = " ".join(r.getMessage() for r in caplog.records)
        assert "MES" in blob
        assert "no_vault_evidence_unit" in blob
        assert "test-seam" in blob

    def test_no_profile_on_hand_makes_the_invariant_vacuous(self):
        # Mirrors `_annotate_narrative_backed`: a caller with no vault to check
        # must never raise a false violation.
        ledger = [_entry("MES", evidence="…")]
        healed, violations = assert_claimable_backed(ledger, None)
        assert violations == []
        assert healed == ledger

    def test_non_claimable_rows_are_never_touched(self):
        ledger = [
            _entry("MES", status="gap", claimable=False, evidence=""),
            _entry("BaFin", status="denied", claimable=False, evidence=DENIED_EVIDENCE),
        ]
        healed, violations = assert_claimable_backed(ledger, MARCUS_VAULT)
        assert violations == []
        assert healed == ledger

    def test_an_empty_or_none_ledger_is_tolerated(self):
        assert assert_claimable_backed(None, MARCUS_VAULT) == ([], [])
        assert assert_claimable_backed([], MARCUS_VAULT) == ([], [])

    def test_a_malformed_profile_fails_open_with_a_warning_never_a_purge(self, caplog):
        # Fail-CLOSED here would nuke every claimable row of a healthy ledger on
        # one unparseable vault; that is a bigger truthfulness loss than the
        # check it replaces. Fail open, loudly.
        with caplog.at_level(logging.WARNING, logger="applire.services.keyword_ledger"):
            healed, violations = assert_claimable_backed(
                [_entry("MES", evidence="…")], {"skills": "not-a-list"}
            )
        assert violations == []
        assert healed[0]["claimable"] is True
        assert caplog.records


class TestTheHealDirection:
    """The heal is fail-SAFE: never toward claimable, always away from it."""

    @pytest.mark.parametrize(
        "row",
        [
            _entry("MES", evidence="Wir haben 2019 ein MES eingeführt."),
            _entry("SAP", evidence=""),
            _entry("SAP", evidence=DENIED_EVIDENCE),
            _entry("SAP", status="gap", claimable=True),
        ],
    )
    def test_every_heal_lands_on_a_non_claimable_row(self, row):
        healed, violations = assert_claimable_backed([row], MARCUS_VAULT)
        assert violations
        assert healed[0]["claimable"] is False
        assert healed[0]["status"] in {"gap", "denied"}

    def test_healing_is_idempotent(self):
        ledger = [_entry("MES", evidence="Wir haben 2019 ein MES eingeführt.")]
        once, first = assert_claimable_backed(ledger, MARCUS_VAULT)
        twice, second = assert_claimable_backed(once, MARCUS_VAULT)
        assert first and second == []
        assert twice == once

    def test_the_input_ledger_is_never_mutated(self):
        row = _entry("MES", evidence="Wir haben 2019 ein MES eingeführt.")
        ledger = [row]
        assert_claimable_backed(ledger, MARCUS_VAULT)
        assert row["claimable"] is True
        assert ledger[0] is row


def test_every_ledger_persist_seam_checks_the_affirmative_invariant():
    """The invariant is ASSERTED, not emergent — structural guard over every
    seam that writes a ledger to a persisted row.

    Modelled on ``test_every_ledger_upgrade_call_site_passes_the_live_denials``
    (#341, the ADR-059 negative twin), for the same reason it exists: the
    affirmative floor lives today in four *local* rules that each happen to
    demand some evidence, and nothing states the joint property. A FIFTH write
    seam added later is an omission the author cannot see — the ledger row is a
    plain JSONB list and a new seam looks perfectly correct in the diff, in the
    tests and in the ADR while writing a claimable row nothing backs. This
    fails on the new seam itself, not on the eventual truthfulness incident.

    Two seam shapes are recognised, because both exist today: an in-place
    attribute write (``gap_row.keyword_ledger = …``) and the construction of
    the persisted row itself (``GapAnalysis(…, keyword_ledger=…)``).

    What it does NOT do (stated so nobody trusts it further than it goes): it
    checks co-occurrence within the enclosing function, not dataflow. A seam
    that calls the invariant on some *other* ledger and persists an unchecked
    one reads as compliant here. This catches the seam nobody thought about,
    which is what actually happened to the denial floor on the agent door.
    """
    import ast
    from pathlib import Path

    import applire

    # `applire` is a NAMESPACE package (ADR-031) — no `__file__`; use `__path__`.
    package_root = Path(applire.__path__[0])
    seams: list[str] = []
    unchecked: list[str] = []

    def _name(call: ast.Call) -> str | None:
        f = call.func
        return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)

    for path in package_root.rglob("*.py"):
        # The invariant's own module defines it and persists nothing.
        if path.name == "keyword_ledger.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert not (
                        alias.name == "assert_claimable_backed" and alias.asname
                    ), (
                        f"{path.relative_to(package_root)}:{node.lineno} imports "
                        "assert_claimable_backed under an alias, which hides it from "
                        "this guard — import it under its own name"
                    )
        funcs = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for node in ast.walk(tree):
            is_seam = (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Attribute) and t.attr == "keyword_ledger"
                    for t in node.targets
                )
            ) or (
                isinstance(node, ast.Call)
                and _name(node) == "GapAnalysis"
                and any(kw.arg == "keyword_ledger" for kw in node.keywords)
            )
            if not is_seam:
                continue
            where = f"{path.relative_to(package_root)}:{node.lineno}"
            seams.append(where)
            enclosing = max(
                (f for f in funcs if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)),
                key=lambda f: f.lineno,
                default=None,
            )
            checked = enclosing is not None and any(
                isinstance(n, ast.Call) and _name(n) == "assert_claimable_backed"
                for n in ast.walk(enclosing)
            )
            if not checked:
                unchecked.append(where)

    assert seams, "guard is inert — no persist seams found (was the column renamed?)"
    assert not unchecked, (
        "these seams persist a Keyword Ledger without checking the ADR-061 "
        "affirmative invariant, so a `claimable` row with no vault evidence can "
        f"reach both document writers through them (#318): {unchecked}"
    )
