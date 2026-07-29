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

"""#260 — GapAnalysisResponse.keyword_liabilities: the derived pre-generation
liability slice, available on the SAME response the gaps page and the
analyze_gaps MCP tool both already read (data-only widening, no new
endpoint/tool needed for agent-door parity)."""

import uuid
from datetime import datetime, timezone

from applire.schemas.gap import GapAnalysisResponse, KeywordLedgerEntry


def _base_kwargs(**overrides):
    kwargs = dict(
        id=uuid.uuid4(),
        job_analysis_id=uuid.uuid4(),
        profile_id=uuid.uuid4(),
        match_score=0.8,
        critical_gaps=[],
        minor_gaps=[],
        strengths=[],
        keyword_gaps=[],
        created_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    kwargs.update(overrides)
    return kwargs


def test_keyword_ledger_entry_narrative_backed_defaults_true():
    """Legacy persisted rows (pre-#260) have no narrative_backed key — must
    default to True (not a liability), never crash."""
    e = KeywordLedgerEntry(
        concept="Python", fit_weight=1.0, status="direct", claimable=True,
    )
    assert e.narrative_backed is True


def test_response_derives_keyword_liabilities_from_the_ledger():
    ledger = [
        {
            "concept": "RAG", "surface_forms": ["RAG"], "sources": ["required"],
            "fit_weight": 1.0, "status": "direct", "evidence": "listed under Skills",
            "claimable": True, "narrative_backed": False,
        },
        {
            "concept": "Python", "surface_forms": ["Python"], "sources": ["required"],
            "fit_weight": 1.0, "status": "direct", "evidence": "5y",
            "claimable": True, "narrative_backed": True,
        },
    ]
    resp = GapAnalysisResponse.model_validate(_base_kwargs(keyword_ledger=ledger))
    assert [e.concept for e in resp.keyword_liabilities] == ["RAG"]


# ── ADR-064 — denial_level invariant + the Pydantic-drop regression ─────────


def test_keyword_ledger_entry_drops_denial_level_when_not_denied(caplog):
    """M2 finding-fix (2026-07-29): denial_level is meaningful ONLY when
    status == "denied" — any other combination is a data anomaly, but this
    schema validates a PERSISTED-READ path (GapAnalysis.keyword_ledger rows
    coming back OUT of the database), so the anomaly must DEGRADE (drop the
    inconsistent field, log a warning) rather than 500 every GET of that gap
    analysis — matching every other back-compat concern in this schema
    (narrative_backed defaults instead of raising)."""
    with caplog.at_level("WARNING"):
        e = KeywordLedgerEntry(
            concept="Python", fit_weight=1.0, status="direct", claimable=True,
            denial_level="direct",
        )
    assert e.denial_level is None
    assert e.status == "direct"  # the rest of the row survives untouched
    assert any(
        "denial_level" in r.message and "Python" in r.message for r in caplog.records
    )


def test_keyword_ledger_entry_allows_denial_level_when_denied():
    e = KeywordLedgerEntry(
        concept="BaFin supervision", fit_weight=1.0, status="denied",
        claimable=False, denial_level="partial",
    )
    assert e.denial_level == "partial"


def test_ledger_row_roundtrips_denial_level_through_gap_analysis_response():
    """The Pydantic-drop regression: GapAnalysisResponse validates every
    ledger row through KeywordLedgerEntry(**e) (_derive_keyword_liabilities),
    and Pydantic v2's default extra="ignore" would silently drop an
    unmodelled key on every API response — denial_level must survive."""
    ledger = [
        {
            "concept": "BaFin supervision", "surface_forms": ["BaFin supervision"],
            "sources": ["required"], "fit_weight": 1.0, "status": "denied",
            "evidence": "Candidate explicitly stated a limit here (interview).",
            "claimable": False, "denial_level": "partial",
        },
    ]
    resp = GapAnalysisResponse.model_validate(_base_kwargs(keyword_ledger=ledger))
    assert resp.keyword_ledger[0].denial_level == "partial"


def test_response_keyword_liabilities_empty_when_no_ledger():
    resp = GapAnalysisResponse.model_validate(_base_kwargs())
    assert resp.keyword_liabilities == []


def test_response_keyword_liabilities_empty_for_legacy_ledger_without_narrative_backed_key():
    """A ledger row persisted before #260 has no narrative_backed key at all
    -- back-compat default (True/backed) means nothing is ever flagged from
    stale data, never a false liability signal."""
    ledger = [
        {
            "concept": "RAG", "surface_forms": ["RAG"], "sources": ["required"],
            "fit_weight": 1.0, "status": "direct", "evidence": "listed under Skills",
            "claimable": True,
        },
    ]
    resp = GapAnalysisResponse.model_validate(_base_kwargs(keyword_ledger=ledger))
    assert resp.keyword_liabilities == []
