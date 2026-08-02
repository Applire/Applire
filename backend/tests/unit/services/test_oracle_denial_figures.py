# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#422 — denial statements as figure provenance for honest restatements.

The letter writer receives ``metadata.denied_concepts[*].statement`` verbatim
via the STATED LIMITS block (ADR-064, by design), so any figure a denial
statement carries reliably re-appears in letters — and reliably graded
``unbacked`` because the numbers checker's corpus never included the denial
rail (charter runs 14/15: the panel-praised "120 wäre der nächste Schritt"
clause and the "seit 2021" denial-transfer clause were the letters' only
false accusations). Sibling of #207 (a control's corpus must include the
denial rail it co-exists with).

The absorption is deliberately GUARDED: the claim must substantially restate
the statement it borrows the figure from (content-token overlap), so the
denial rail can never launder a fabricated achieved claim that merely reuses
the denied figure — the "control defeated by its own receipt" trap.
"""
import pytest

from applire.schemas.oracle import Claim
from applire.services.oracle import verify_claim
from applire.services.oracle.matchers import build_vault_index

# Synthetic, shaped after tests/files/panel_review_case/operations_marcus_de
# (run-15 ground truth, sanitized fixture family — designed-invitable case).
DENIAL_PROFILE = {
    "personal_info": {"name": "Stefan Brandt"},
    "work_experience": [
        {
            "id": "w1",
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "start_date": "2017-04",
            "responsibilities": [
                "Führung von 38 Mitarbeitenden im Dreischichtbetrieb.",
            ],
        }
    ],
    "metadata": {
        "denied_concepts": [
            {
                "concept": "dauerhafte Spanne von 120",
                "statement": (
                    "Direkt geführt habe ich bei Weberit 38 Mitarbeitende über "
                    "drei Schichtleiter, davor bei Rasselstein eine Schicht mit "
                    "14 Mitarbeitenden. In Urlaubs- und Krankheitsvertretung des "
                    "Betriebsleiters habe ich mehrfach den Gesamtstandort mit "
                    "rund 90 Mitarbeitenden geführt, jeweils 2 bis 4 Wochen am "
                    "Stück. Ehrlich gesagt: die dauerhafte Spanne von 120 wäre "
                    "für mich der nächste Schritt – genau deshalb reizt mich "
                    "die Stelle."
                ),
                "source": "interview",
                "date": "2026-08-02",
                "denial_level": "direct",
            },
            {
                "concept": "IFS",
                "statement": (
                    "Nein, mit IFS oder BRC habe ich keine Erfahrung – das ist "
                    "eine ehrliche Lücke. Für Lebensmittelkunden habe ich nie "
                    "direkt produziert. Hygiene- und Dokumentationsdisziplin "
                    "kenne ich allerdings aus der Fertigung von Kosmetik-"
                    "Verpackungen im Sauberraumbereich bei Weberit seit 2021 "
                    "und aus zehn Jahren ISO-9001-Audit-Praxis – der Standard "
                    "wäre neu, die Audit- und Dokumentationsdisziplin nicht."
                ),
                "source": "interview",
                "date": "2026-08-02",
                "denial_level": "direct",
            },
        ]
    },
}


# ── the denial corpus is separate, never grounding evidence ──────────────────

def test_denial_units_isolated_from_grounding_corpus():
    """Denial statements must NOT enter units/figure_map/all_text_norm — a
    figure inside a denial is not free-floating vault evidence."""
    index = build_vault_index(DENIAL_PROFILE)
    assert ("number", "120") not in index.figure_map
    assert ("number", "90") not in index.figure_map
    assert "dauerhafte spanne von 120" not in index.all_text_norm
    paths = {u.path for u in index.denial_units}
    assert paths == {
        "metadata.denied_concepts[0].statement",
        "metadata.denied_concepts[1].statement",
    }
    unit = next(
        u for u in index.denial_units
        if u.path == "metadata.denied_concepts[0].statement"
    )
    unit_figs = {(f.kind, f.value) for f in unit.figures}
    assert ("number", "120") in unit_figs
    assert ("number", "38") in unit_figs


# ── honest restatements: the run-14/15 false accusations ─────────────────────

@pytest.mark.asyncio
async def test_honest_restatement_of_denial_figure_is_not_unbacked():
    """Run 14: 'Eine dauerhafte Führungsspanne von 120 … wäre der nächste
    Schritt' — the panel-praised sentence must not be the report's only
    unbacked entry."""
    verdict = await verify_claim(
        Claim(
            text=(
                "Eine dauerhafte Führungsspanne von 120 Mitarbeitenden wäre "
                "für mich der nächste Schritt."
            ),
            location="body.paragraphs[2][1]",
        ),
        DENIAL_PROFILE,
    )
    assert verdict.verdict == "grounded"
    assert verdict.checker == "numbers"
    refs = {e.ref for e in verdict.evidence}
    assert "metadata.denied_concepts[0].statement" in refs


@pytest.mark.asyncio
async def test_run15_seit_2021_denial_transfer_clause_is_not_unbacked():
    """Run 15: the delivered clause carries 'seit 2021' verbatim from the
    IFS/BRC denial statement; 2021 exists nowhere else in the vault."""
    verdict = await verify_claim(
        Claim(
            text=(
                "Hygiene- und Dokumentationsdisziplin kenne ich jedoch aus "
                "der Kosmetik-Verpackungsfertigung im Sauberraumbereich bei "
                "Weberit seit 2021 und aus zehn Jahren ISO-9001-Audit-Praxis."
            ),
            location="body.paragraphs[3][0].clauses[1]",
            kind="clause",
        ),
        DENIAL_PROFILE,
    )
    assert verdict.verdict == "grounded"
    assert verdict.checker == "numbers"
    refs = {e.ref for e in verdict.evidence}
    assert "metadata.denied_concepts[1].statement" in refs


# ── the guard: a denial figure can never launder an overclaim ────────────────

@pytest.mark.asyncio
async def test_fabricated_overclaim_reusing_denial_figure_stays_unbacked():
    """'Ich führe derzeit 120 Mitarbeitende…' reuses the denied figure with
    achieved framing and low fidelity to the statement — the accusation
    must stand exactly as before #422."""
    verdict = await verify_claim(
        Claim(
            text="Ich führe derzeit 120 Mitarbeitende in drei Schichten.",
            location="body.paragraphs[2][0]",
        ),
        DENIAL_PROFILE,
    )
    assert verdict.verdict == "unbacked"
    assert verdict.checker == "numbers"
    assert "120" in (verdict.detail or "")


# ── adversarial round 1 (2026-08-02): inversion laundering ──────────────────
# The refutation pass BROKE the overlap-only guard: word reuse cannot see
# DIRECTION, so a claim that inverts the denial's hedged framing into an
# achieved assertion ("120 wäre der nächste Schritt" → "120 habe ich
# geführt") cleared 0.6 comfortably. The guard now adds stance-consistency:
# a figure whose statement sub-clauses are all hedged/negated absorbs only
# into a claim that itself carries a hedge marker.

@pytest.mark.asyncio
async def test_inverted_achieved_claim_on_hedged_denial_figure_stays_unbacked():
    """The refutation vector verbatim: the denial's own words, inverted."""
    verdict = await verify_claim(
        Claim(
            text="Die dauerhafte Spanne von 120 habe ich bei Weberit geführt.",
            location="body.paragraphs[9][0]",
        ),
        DENIAL_PROFILE,
    )
    assert verdict.verdict == "unbacked"
    assert verdict.checker == "numbers"


@pytest.mark.asyncio
async def test_percent_range_denial_inversion_stays_unbacked():
    """#412 × #422 interaction: a hedged percent-range denial must not
    ground its own inversion."""
    profile = {
        "personal_info": {"name": "Stefan Brandt"},
        "metadata": {
            "denied_concepts": [
                {
                    "concept": "OEE-Verbesserung 61 auf 73",
                    "statement": (
                        "Eine Verbesserung der OEE von 61 auf 73 % wäre für "
                        "mich ambitioniert, das habe ich so nie erreicht "
                        "oder behauptet."
                    ),
                    "source": "interview",
                    "date": "2026-08-02",
                    "denial_level": "direct",
                }
            ]
        },
    }
    verdict = await verify_claim(
        Claim(
            text="Die Verbesserung der OEE von 61 auf 73 % habe ich erreicht.",
            location="body.paragraphs[2][0]",
        ),
        profile,
    )
    assert verdict.verdict == "unbacked"


@pytest.mark.asyncio
async def test_mixed_true_win_plus_laundered_denial_figure_stays_unbacked():
    """The most damaging refutation shape: one real achievement plus a
    quietly extended scope in the same sentence."""
    verdict = await verify_claim(
        Claim(
            text=(
                "Ich führe 38 Mitarbeitende und habe zudem die dauerhafte "
                "Spanne von 120 bei Weberit bereits direkt geführt."
            ),
            location="body.paragraphs[2][0]",
        ),
        DENIAL_PROFILE,
    )
    assert verdict.verdict == "unbacked"
    assert "120" in (verdict.detail or "")


# ── adversarial round 2 (self-probe of the round-1 hardening) ───────────────
# The first hardening checked "is the claim's figure-bearing clause hedged"
# with a statement-side splitter that does not break on commas — so an
# assertion bought absorption by appending a hedged tail. The claim side now
# splits on commas too (see ``_DENIAL_CLAIM_SPLIT_RE``); the statement side
# deliberately does not.

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "Die dauerhafte Spanne von 120 habe ich bei Weberit geführt, mehr wäre der nächste Schritt.",
        "Die dauerhafte Spanne von 120 habe ich geführt; das wäre der nächste Schritt.",
        "Ich habe die dauerhafte Spanne von 120 bei Weberit verantwortet – künftig gerne mehr.",
    ],
    ids=["comma-tail", "semicolon-tail", "dash-tail"],
)
async def test_hedged_tail_cannot_launder_an_assertion(text):
    verdict = await verify_claim(Claim(text=text, location="x"), DENIAL_PROFILE)
    assert verdict.verdict == "unbacked"


def test_statement_side_is_not_comma_split():
    """The asymmetry itself: a hedged statement must not be carvable into a
    factual-looking fragment that grants blanket absorption."""
    from applire.services.oracle.audit import _denial_sub_clauses

    statement = "Die Spanne von 120, die wäre neu für mich."
    assert len(_denial_sub_clauses(statement)) == 1
    assert len(_denial_sub_clauses(statement, claim_side=True)) == 2


# ── mixed case: vault-backed + denial-backed figures in one claim ────────────

@pytest.mark.asyncio
async def test_mixed_vault_and_denial_figures_ground_with_both_refs():
    verdict = await verify_claim(
        Claim(
            text=(
                "Ich führe 38 Mitarbeitende; die dauerhafte Spanne von 120 "
                "wäre für mich der nächste Schritt."
            ),
            location="body.paragraphs[2][0]",
        ),
        DENIAL_PROFILE,
    )
    assert verdict.verdict == "grounded"
    refs = {e.ref for e in verdict.evidence}
    assert "work_experience[0].responsibilities[0]" in refs
    assert "metadata.denied_concepts[0].statement" in refs
