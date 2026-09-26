# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-058 amended 2026-09-26 (ruling A-1; Agent #676 line "get_cv_ats_report
carries no truthfulness verdict", founder UAT F-12): the Oracle's verdict rides
the ATS-report envelope on BOTH doors.

Unit tier for ``services.truthfulness_summary.summarize`` + one seam test per
door (REST CV, REST letter, MCP CV, MCP letter), each asserting on the envelope
the door actually serialises from a persisted row.
"""
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_ats_report_persistence import (  # noqa: E402,F401 — fixtures
    _make_ats_report,
    _seed_cl,
    _seed_cv,
    cl_ats_client,
    cl_router_db,
    cv_ats_client,
    cv_router_db,
    db,
    db_with_cover_letter,
    db_with_cv,
)


def _claim(text, verdict, kind="sentence", location="experience[0].bullets[0]"):
    return {
        "claim": {"text": text, "location": location, "kind": kind},
        "verdict": {"verdict": verdict, "checker": "grounding"},
    }


def _truth_report(claims, kind="cv"):
    from applire.schemas.oracle import ClaimResult, TruthfulnessReport

    results = [ClaimResult.model_validate(c) for c in claims]
    return TruthfulnessReport.from_results(kind, results).model_dump(mode="json")


RED = _truth_report([
    _claim("Led a team of 12 engineers", "inflated"),
    _claim("Built the SAP rollout at Siemens", "misattributed"),
    _claim("Python", "grounded", kind="skill"),
])
CLEAN = _truth_report([
    _claim("Python", "grounded", kind="skill"),
    _claim("Ran the Berlin office", "grounded"),
])


# ---------------------------------------------------------------------------
# summarize — unit tier
# ---------------------------------------------------------------------------

def test_red_report_is_stop_and_fix_with_the_panels_count():
    from applire.services.truthfulness_summary import summarize

    s = summarize(RED, None)
    assert s.available is True
    assert s.flagged == 2
    assert s.stop_and_fix is True
    assert s.counts["inflated"] == 1 and s.counts["misattributed"] == 1
    assert s.counts["grounded"] == 1


def test_clean_report_is_not_stop_and_fix():
    from applire.services.truthfulness_summary import summarize

    s = summarize(CLEAN, None)
    assert (s.available, s.flagged, s.stop_and_fix) == (True, 0, False)


@pytest.mark.parametrize("verdict", ["inflated", "misattributed", "unbacked"])
def test_each_flag_verdict_alone_stops(verdict):
    from applire.services.truthfulness_summary import summarize

    s = summarize(_truth_report([_claim("Claim x", verdict)]), None)
    assert s.flagged == 1 and s.stop_and_fix is True


@pytest.mark.parametrize("verdict", ["grounded", "unverifiable", "not_applicable"])
def test_non_flag_verdicts_never_stop(verdict):
    from applire.services.truthfulness_summary import summarize

    s = summarize(_truth_report([_claim("Claim x", verdict)]), None)
    assert s.flagged == 0 and s.stop_and_fix is False


@pytest.mark.parametrize("stored", [None, {}, {"claims": "not-a-list", "document_kind": 7}])
def test_unknown_is_never_clean(stored):
    """No report / empty / malformed → available False and NULL flags — never 0/False."""
    from applire.services.truthfulness_summary import summarize

    s = summarize(stored, None)
    assert s.available is False
    assert s.flagged is None
    assert s.stop_and_fix is None
    assert s.counts == {}


def test_flagged_is_the_review_panels_group_one_selection():
    """An ``unbacked`` skill chip that is a claimable (related) concept is NOT a
    group-1 row in the review panel — the summary must agree with the panel,
    not re-derive its own reading of FLAG_VERDICTS."""
    from applire.services.review_state import group_one_findings
    from applire.services.truthfulness_summary import summarize

    truth = _truth_report([
        _claim("Kubernetes", "unbacked", kind="skill"),
        _claim("Terraform", "unbacked", kind="skill"),
    ])
    ats = _make_ats_report("cv").model_dump(mode="json")
    ats["keywords"]["claimable_concepts"] = ["Kubernetes"]

    s = summarize(truth, ats)
    assert s.flagged == 1 == len(group_one_findings(ats, truth))
    assert s.stop_and_fix is True
    # without the ATS report there is no claimable set — both count 2
    assert summarize(truth, None).flagged == 2 == len(group_one_findings(None, truth))


def _ats_with_unsupported(terms, claimable=()):
    ats = _make_ats_report("cv").model_dump(mode="json")
    ats["keywords"]["present_unsupported"] = list(terms)
    ats["keywords"]["present_unsupported_matches"] = {t: [{"form": t}] for t in terms}
    ats["keywords"]["claimable_concepts"] = list(claimable)
    return ats


def test_ats_only_row_stops_the_agent_too():
    """Ruling A-1b (founder 2026-09-26; adversarial probe + delivery letters
    e84514c3 / e9c36a18): an ATS ``present_unsupported`` term with no Oracle
    claim is a group-1 row the human sees — the summary must count it."""
    from applire.services.review_state import group_one_findings
    from applire.services.truthfulness_summary import summarize

    ats = _ats_with_unsupported(["Kubernetes"])
    truth = _truth_report([])
    rows = group_one_findings(ats, truth)
    assert [r.producer for r in rows] == ["ats"]
    s = summarize(truth, ats)
    assert (s.flagged, s.stop_and_fix) == (1, True)
    # counts stays the Oracle's tally — the ATS row is not a verdict
    assert sum(s.counts.values()) == 0


def test_an_ats_term_folding_into_its_oracle_claim_is_one_row_not_two():
    from applire.services.review_state import group_one_findings
    from applire.services.truthfulness_summary import summarize

    ats = _ats_with_unsupported(["Kubernetes", "Terraform"])
    truth = _truth_report([
        _claim("Kubernetes", "unbacked", kind="skill"),       # folds into the ATS row
        _claim("Led a team of 12 engineers", "inflated"),     # Oracle-only row
    ])
    s = summarize(truth, ats)
    assert s.flagged == 3 == len(group_one_findings(ats, truth))
    assert s.counts["unbacked"] == 1 and s.counts["inflated"] == 1


def test_unknown_truth_report_stays_unknown_even_with_ats_rows():
    """No persisted Oracle report ⇒ unknown, never a partial count that reads
    as the whole story."""
    from applire.services.truthfulness_summary import summarize

    s = summarize(None, _ats_with_unsupported(["Kubernetes"]))
    assert (s.available, s.flagged, s.stop_and_fix) == (False, None, None)


# ---------------------------------------------------------------------------
# Seam tests — one per door
# ---------------------------------------------------------------------------

async def _set_truth(session, model, row_id, report):
    row = await session.get(model, row_id)
    row.truthfulness_report = report
    await session.commit()


def _db_cm(sess):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=sess)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_seam_rest_cv_ats_report_carries_the_verdict(cv_ats_client):
    from applire.models.cv import GeneratedCV

    client, session = cv_ats_client
    cv_id = await _seed_cv(session, ats_report=_make_ats_report("cv").model_dump(mode="json"))
    await _set_truth(session, GeneratedCV, cv_id, RED)

    body = (await client.get(f"/api/cv/{cv_id}/ats-report")).json()
    assert body["truthfulness"]["available"] is True
    assert body["truthfulness"]["flagged"] == 2
    assert body["truthfulness"]["stop_and_fix"] is True


@pytest.mark.asyncio
async def test_seam_rest_cover_letter_ats_report_carries_the_verdict(cl_ats_client):
    from applire.models.cover_letter import GeneratedCoverLetter

    client, session = cl_ats_client
    cl_id = await _seed_cl(
        session, ats_report=_make_ats_report("cover_letter").model_dump(mode="json")
    )
    await _set_truth(session, GeneratedCoverLetter, cl_id,
                     _truth_report([_claim("I led 40 people", "unbacked")], "cover_letter"))

    body = (await client.get(f"/api/cover-letter/{cl_id}/ats-report")).json()
    assert body["truthfulness"]["flagged"] == 1
    assert body["truthfulness"]["stop_and_fix"] is True


@pytest.mark.asyncio
async def test_seam_rest_cv_without_a_persisted_audit_reads_unknown_not_clean(cv_ats_client):
    client, session = cv_ats_client
    cv_id = await _seed_cv(session, ats_report=None)

    body = (await client.get(f"/api/cv/{cv_id}/ats-report")).json()
    assert body["truthfulness"] == {
        "available": False, "counts": {}, "flagged": None,
        "stop_and_fix": None, "unverifiable_dominated": None,
    }


@pytest.mark.asyncio
async def test_seam_mcp_get_cv_ats_report_carries_the_verdict(db_with_cv):
    from applire.mcp.server import get_cv_ats_report as mcp_tool
    from applire.models.cv import GeneratedCV

    session, cv_id = db_with_cv["db"], db_with_cv["cv_id"]
    await _set_truth(session, GeneratedCV, cv_id, RED)
    with patch("applire.mcp.server.get_db", return_value=_db_cm(session)):
        result = await mcp_tool(str(cv_id))
    assert result["truthfulness"]["flagged"] == 2
    assert result["truthfulness"]["stop_and_fix"] is True
    assert result["truthfulness"]["counts"]["misattributed"] == 1


@pytest.mark.asyncio
async def test_seam_mcp_get_cover_letter_ats_report_carries_the_verdict(db_with_cover_letter):
    from applire.mcp.server import get_cover_letter_ats_report as mcp_tool
    from applire.models.cover_letter import GeneratedCoverLetter

    session, cl_id = db_with_cover_letter["db"], db_with_cover_letter["cl_id"]
    await _set_truth(session, GeneratedCoverLetter, cl_id, CLEAN)
    with patch("applire.mcp.server.get_db", return_value=_db_cm(session)):
        result = await mcp_tool(str(cl_id))
    assert result["truthfulness"]["available"] is True
    assert result["truthfulness"]["stop_and_fix"] is False


@pytest.mark.asyncio
async def test_seam_mcp_unknown_cv_id_still_not_found(db_with_cv):
    from mcp.shared.exceptions import McpError

    from applire.mcp.server import get_cv_ats_report as mcp_tool

    with patch("applire.mcp.server.get_db", return_value=_db_cm(db_with_cv["db"])):
        with pytest.raises(McpError):
            await mcp_tool(str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_seam_rest_cover_letter_ats_only_row_stops(cl_ats_client):
    """The delivery-run shape (letter e84514c3, ruling A-1b): one ATS
    present_unsupported row, no Oracle flag — the door must say stop."""
    from applire.models.cover_letter import GeneratedCoverLetter

    client, session = cl_ats_client
    ats = _make_ats_report("cover_letter").model_dump(mode="json")
    ats["keywords"]["present_unsupported"] = ["Lebensmittelkunden"]
    cl_id = await _seed_cl(session, ats_report=ats)
    await _set_truth(session, GeneratedCoverLetter, cl_id,
                     _truth_report([_claim("Sehr geehrte Damen und Herren", "not_applicable")],
                                   "cover_letter"))

    body = (await client.get(f"/api/cover-letter/{cl_id}/ats-report")).json()
    assert body["truthfulness"]["flagged"] == 1
    assert body["truthfulness"]["stop_and_fix"] is True
