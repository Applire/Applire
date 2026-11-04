# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-100 M-3 / Agent #788 — the ledger row's verified ``backing`` reaches the
agent channel and the REST gap response.

The 11-04 E2E run (synthetic LIMS twin) found every claimable DB row carrying
``backing {quote, unit_path, instrument}`` while MCP ``analyze_gaps`` returned
no ``backing`` key on any row: ``KeywordLedgerEntry`` did not declare the field,
so ``GapAnalysisResponse.model_validate`` stripped it (the `adjacent_evidence` /
`jd_phrase` class).
"""
from __future__ import annotations

import datetime as _dt
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.support.mcp_door import mcp_signing_secret  # noqa: F401 — autouse

_QUOTE = "Kopplung des Fertigungsleitsystems an die Laborsysteme zur automatischen Übergabe der Chargendaten."


def _row(**over):
    row = {
        "concept": "MES", "surface_forms": ["MES integration", "MES"], "sources": ["required"],
        "fit_weight": 1.0, "status": "direct", "evidence": "The profile shows an MES coupling.",
        "claimable": True,
        "backing": {"quote": _QUOTE, "unit_path": "work_experience[0].responsibilities[1]", "instrument": "quote"},
    }
    row.update(over)
    return row


def _response(rows):
    from applire.schemas.gap import GapAnalysisResponse

    return GapAnalysisResponse.model_validate({
        "id": uuid.uuid4(), "job_analysis_id": uuid.uuid4(), "profile_id": uuid.uuid4(),
        "match_score": 0.8, "critical_gaps": [], "minor_gaps": [], "strengths": [],
        "keyword_gaps": [], "created_at": _dt.datetime(2026, 11, 4, tzinfo=_dt.timezone.utc),
        "keyword_ledger": rows,
    })


def test_ledger_entry_publishes_backing():
    dumped = _response([_row()]).model_dump(mode="json")
    assert dumped["keyword_ledger"][0]["backing"] == {
        "quote": _QUOTE, "unit_path": "work_experience[0].responsibilities[1]", "instrument": "quote",
    }


def test_row_without_backing_publishes_null():
    row = _row()
    row.pop("backing")
    dumped = _response([_row(concept="Kubernetes", status="denied", claimable=False, backing=None), row])
    assert [e["backing"] for e in dumped.model_dump(mode="json")["keyword_ledger"]] == [None, None]


@pytest.mark.parametrize("bad", ["junk", {"quote": ""}, {"quote": "   "}, {"unit_path": "x"}, {"quote": 7}, ["q"]])
def test_malformed_stored_backing_reads_absent_never_500(bad):
    """A persisted-read path: a malformed stored value must not take the GET down."""
    dumped = _response([_row(backing=bad)]).model_dump(mode="json")
    assert dumped["keyword_ledger"][0]["backing"] is None


def test_non_string_instrument_drops_to_none():
    dumped = _response([_row(backing={"quote": _QUOTE, "unit_path": "skills[0]", "instrument": ["x"]})])
    assert dumped.model_dump(mode="json")["keyword_ledger"][0]["backing"] == {
        "quote": _QUOTE, "unit_path": "skills[0]", "instrument": None,
    }


@pytest.mark.parametrize(
    "over",
    [
        {"backing": {"quote": _QUOTE, "unit_path": 3}},  # no floor stamp
        {"backing": {"quote": _QUOTE}},
        {"claimable": False, "status": "gap", "aim_only": True},  # raw classifier quote on an aim row
    ],
    ids=["non-str-path", "pathless", "unclaimable"],
)
def test_only_a_floor_verified_backing_is_published(over):
    """Review (#788): an unverified quote must never reach an agent as evidence."""
    dumped = _response([_row(**over)]).model_dump(mode="json")
    assert dumped["keyword_ledger"][0]["backing"] is None


@pytest.mark.asyncio
async def test_mcp_analyze_gaps_exposes_backing():
    """Seam test, the MCP door: a REAL GapAnalysisResponse through the tool body."""
    from applire.mcp.server import analyze_gaps

    session = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.gap_svc.analyze_or_refresh_gaps", AsyncMock(return_value=_response([_row()]))),
    ):
        result = await analyze_gaps(job_id=str(uuid.uuid4()))

    row = result["keyword_ledger"][0]
    assert row["backing"]["quote"] == _QUOTE
    assert row["backing"]["unit_path"] == "work_experience[0].responsibilities[1]"
