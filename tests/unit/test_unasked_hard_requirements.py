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

"""The unasked hard requirement (ADR-074, #526).

A ledger row that is `claimable: false` AND `required` AND carries no `evidence`,
no `adjacent_evidence` and no stated limit has **no truthful expression in a
cover letter**: asserting the term is ungrounded, staying silent breaks the
UNADDRESSED HARD REQUIREMENTS block's own instruction, and denying it is an
invented limit — because `gap` means *nobody asked*, not *the candidate said no*.

ADR-074's decision: ignore it at generation, and tell the candidate instead, as a
fact about US ("you were never asked") rather than about them.

Every conjunct of the predicate is load-bearing, and each has its own test below.
The one that looks redundant — `status != "denied"` — is a deliberate fail-safe:
it is implied today only because `_denied_row` always writes `DENIED_EVIDENCE`,
and a control's correctness must not depend on the spelling of a sentinel.
"""

import pytest

from applire.services.keyword_ledger import (
    DENIAL_FLOOR_EVIDENCE,
    DENIED_EVIDENCE,
    is_unasked_requirement,
    unasked_hard_requirements,
)


def _row(**over):
    base = {
        "concept": "Digitalisierung",
        "surface_forms": ["Digitalisierung"],
        "sources": ["keyword", "required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    }
    base.update(over)
    return base


def test_the_run_1_shape_is_an_unasked_requirement():
    """Ground truth from the gate charter run 1 ledger (dev DB, 2026-08-11
    19:36:46): both blocking concepts, verbatim."""
    assert is_unasked_requirement(_row()) is True
    assert is_unasked_requirement(
        _row(concept="Investitionsverantwortung", sources=["required"])
    ) is True


@pytest.mark.parametrize(
    "over, why",
    [
        ({"claimable": True, "status": "partial", "evidence": "x"},
         "a claimable row is positionable — nothing is missing"),
        ({"sources": ["nice_to_have"]},
         "only a HARD requirement is worth telling the candidate about"),
        ({"adjacent_evidence": "MES-Einführung", "claimable": True, "status": "partial",
          "evidence": "x"},
         "an adjacent partial has material: promote the substitute"),
        ({"status": "denied", "evidence": DENIED_EVIDENCE},
         "the candidate WAS asked and answered — their own words are the material"),
        ({"evidence": DENIAL_FLOOR_EVIDENCE},
         "a containment-floored gap has a related stated limit to build on (ADR-059/#486)"),
        ({"evidence": "some vault context"},
         "any evidence at all is something the writer can position against"),
    ],
)
def test_each_conjunct_excludes_a_row_that_has_material(over, why):
    assert is_unasked_requirement(_row(**over)) is False, why


def test_the_denied_clause_is_a_fail_safe_not_a_redundancy():
    """`_denied_row` always writes DENIED_EVIDENCE today, so the evidence clause
    already excludes a denial. The status clause exists so that a future change to
    that sentinel — or a hand-built/legacy row — cannot silently reclassify the
    candidate's own testimony as "we never asked you"."""
    assert is_unasked_requirement(_row(status="denied", evidence="")) is False


def test_a_scope_entry_is_never_an_unasked_requirement():
    """ADR-069/ADR-070: a scope row's concept is a synthesised label carrying the
    JD's own figure. It is excluded from every coverage instrument by predicate,
    and ADR-070 records that a persistent scope gap is positioned nowhere,
    deliberately — surfacing it here would re-open that decision by the back door."""
    assert is_unasked_requirement(
        _row(concept="Führungsspanne ~120 MA", bar={"kind": "team_size", "value": 120})
    ) is False


def test_the_selector_returns_rows_in_ledger_order_and_tolerates_junk():
    ledger = [
        _row(concept="Digitalisierung"),
        {"concept": "broken"},
        None,
        _row(concept="Arbeitssicherheit", claimable=True, status="direct", evidence="x"),
        _row(concept="Investitionsverantwortung", sources=["required"]),
    ]
    out = unasked_hard_requirements(ledger)
    assert [e["concept"] for e in out] == ["Digitalisierung", "Investitionsverantwortung"]
    assert unasked_hard_requirements(None) == []
    assert unasked_hard_requirements([]) == []


def test_the_letter_block_never_receives_an_unasked_requirement():
    """The generation half of ADR-074 clause 2: such a row falls out of
    `find_unaddressed_hard_requirements`, so the letter is written as though the
    requirement had not been named — rather than under an instruction demanding a
    transfer argument the vault cannot supply."""
    from applire.services.cross_document import find_unaddressed_hard_requirements

    ledger = [
        _row(concept="Digitalisierung"),
        _row(concept="IFS", status="denied", evidence=DENIED_EVIDENCE, sources=["required"]),
    ]
    out = find_unaddressed_hard_requirements(ledger, None)
    assert [e["concept"] for e in out] == ["IFS"]


def test_the_no_vault_context_fallback_is_gone():
    """ADR-062 clause 3. With the Restfall excluded at the selector, no entry
    reaching `render_unaddressed_hard_requirements_block` can lack material — the
    string was the block's way of admitting it had nothing to say while still
    demanding a transfer argument. A branch no input can select is not a
    safeguard; it is the shape of the defect."""
    import applire.services.cross_document as xd

    src = xd.render_unaddressed_hard_requirements_block.__doc__ or ""
    assert "pure keyword gap" not in src
    import inspect

    assert "pure keyword gap" not in inspect.getsource(
        xd.render_unaddressed_hard_requirements_block
    )


def test_the_notice_rides_the_gap_analysis_response_and_is_derived_not_stored():
    """ADR-074 clause 4 — per APPLICATION, derived from the persisted ledger.

    Not a report column on the generated letter: the state belongs to the (job,
    gap analysis) pair and exists before any document does. Deriving it means it
    cannot drift past a post-interview recompute and disappears by itself once the
    candidate is asked. Same #260 `keyword_liabilities` pattern — one source, on
    the response the gaps page AND the `analyze_gaps` MCP tool already return, so
    there is no new endpoint and no new tool to keep in parity."""
    import uuid
    from datetime import datetime

    from applire.schemas.gap import GapAnalysisResponse

    resp = GapAnalysisResponse.model_validate({
        "id": uuid.uuid4(),
        "job_analysis_id": uuid.uuid4(),
        "profile_id": uuid.uuid4(),
        "match_score": 0.77,
        "critical_gaps": [], "minor_gaps": [], "strengths": [], "keyword_gaps": [],
        "keyword_ledger": [
            _row(concept="Digitalisierung"),
            _row(concept="IFS", status="denied", evidence=DENIED_EVIDENCE),
            _row(concept="Lean", claimable=True, status="direct", evidence="5S, SMED, KVP"),
        ],
        "created_at": datetime(2026, 8, 13, 9, 0, 0),
    })

    assert [e.concept for e in resp.unasked_requirements] == ["Digitalisierung"]
    # Derived, never independently settable: a caller-supplied value is replaced
    # by the ledger's own answer rather than trusted.
    forged = GapAnalysisResponse.model_validate({
        "id": uuid.uuid4(), "job_analysis_id": uuid.uuid4(), "profile_id": uuid.uuid4(),
        "critical_gaps": [], "minor_gaps": [], "strengths": [], "keyword_gaps": [],
        "keyword_ledger": [],
        "unasked_requirements": [_row(concept="Invented")],
        "created_at": datetime(2026, 8, 13, 9, 0, 0),
    })
    assert forged.unasked_requirements == []


def test_generation_logs_the_silence_once():
    """ADR-074 clause 5. A requirement that leaves no trace in the document is
    the shape of a control that never fires, so the omission is recorded on the
    always-on logger with a stable prefix — countable after the fact, exactly as
    #264 made review exhaustion countable."""
    import inspect

    from applire.services import cover_letter as cl

    src = inspect.getsource(cl._render_cover_letter_background)
    assert "LETTER_UNASKED_REQUIREMENTS" in src
    assert "logger.warning" in src.split("LETTER_UNASKED_REQUIREMENTS")[0][-400:]
