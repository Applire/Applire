# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RULING D-2 (founder, 2026-11-23): an agent-written document with a blocking
finding is held like any other; the person or the agent decides it through
``decide_open_points`` (ADR-054 amended).

adv/holds D-2 was the bypass: an agent read a held document's content from the
status tool and re-rendered it through ``render_document`` — a new ``origin="agent"``
row, never held, its ``pdf_url`` answering 200 and its Word file returned inline.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from applire.services import review_signals  # noqa: E402
from tests.support.mcp_door import mcp_signing_secret  # noqa: E402,F401 — autouse: the door signs its links
from test_mcp_render_document import (  # noqa: E402,F401
    AGENT_CV_CONTENT,
    AGENT_LETTER_CONTENT,
    _cv_patches,
    _db_cm,
    db,
    seeded,
)
from test_sorbet_l_letter_open_points import _claim, _truth  # noqa: E402

CV_CLAIM = "Led a team of 12 engineers."
#: The misplaced sentence of the adversary's held letter, reworded by the agent.
REWORDED = (
    "During my years at Brückenbau Fintech I also set up the Prometheus and Grafana "
    "monitoring and ran the GitLab CI/CD pipelines."
)


def _letter_patches(session, truth):
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage, ATSReport

    report = ATSReport(
        document="cover_letter",
        checks=[ATSCheck(id="contact-name", status="pass")],
        keywords=ATSKeywordCoverage(present=["Python"], missing=[]),
        passed=1, failed=0,
    )
    return (
        patch("applire.mcp.server.get_db", return_value=_db_cm(session)),
        patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"%PDF")),
        patch("applire.services.ats_audit.audit_cover_letter", return_value=report),
        patch("applire.services.cover_letter._letter_truth_audit", new=AsyncMock(return_value=truth)),
    )


async def _render_cv(seeded, truth, fmt="docx"):  # noqa: F811
    from applire.mcp.server import render_document

    p1, p2, p3, p4 = _cv_patches(seeded["db"])
    with p1, p2, p3, p4, patch("applire.services.cv._truth_audit", new=AsyncMock(return_value=truth)):
        return await render_document(
            document_kind="cv", content=dict(AGENT_CV_CONTENT),
            job_id=str(seeded["job_id"]), format=fmt,
        )


async def _row(db, model, doc_id):  # noqa: F811
    import uuid

    return await db.get(model, uuid.UUID(doc_id))


@pytest.mark.asyncio
async def test_an_agent_written_cv_with_a_blocking_finding_is_held(seeded):  # noqa: F811
    from applire.models.cv import GeneratedCV
    from applire.services import cv as cv_svc
    from applire.services.review_signals import DeliveryHeld

    result = await _render_cv(seeded, _truth(_claim(CV_CLAIM, "unbacked")))
    # the envelope carries the points and the findings, never the file
    assert "docx_base64" not in result and "docx_filename" not in result
    assert "html_url" not in result
    op = result["open_points"]
    assert op["count"] == 1 and op["decided"] is False and len(op["basis"]) == 16
    assert [f["text"] for f in op["findings"]] == [CV_CLAIM]
    assert result["truthfulness_report"]["claims"]
    row = await _row(seeded["db"], GeneratedCV, result["document_id"])
    assert row.origin == "agent"
    assert review_signals.open_points_pending(row) is not None
    with pytest.raises(DeliveryHeld):
        await cv_svc.get_cv_docx(row.id, seeded["db"], user_id=row.user_id)


@pytest.mark.asyncio
async def test_decide_open_points_releases_an_agent_written_cv(seeded):  # noqa: F811
    from applire.mcp.server import decide_open_points
    from applire.models.cv import GeneratedCV
    from applire.services import cv as cv_svc

    result = await _render_cv(seeded, _truth(_claim(CV_CLAIM, "unbacked")), fmt="pdf")
    basis = result["open_points"]["basis"]
    with patch("applire.mcp.server.get_db", return_value=_db_cm(seeded["db"])):
        out = await decide_open_points(cv_id=result["document_id"], keep=True, basis=basis)
    assert out["open_points"]["decided"] is True
    row = await _row(seeded["db"], GeneratedCV, result["document_id"])
    assert review_signals.open_points_pending(row) is None
    assert (await cv_svc.get_cv_docx(row.id, seeded["db"], user_id=row.user_id))[:2] == b"PK"


@pytest.mark.asyncio
async def test_a_reworded_copy_of_a_held_letter_is_held(seeded):  # noqa: F811
    """Variation 1: the agent rewords the misplaced sentence of a held letter; the
    audit of the NEW row still flags it, so the new row is held on its own report."""
    from applire.mcp.server import render_document
    from applire.models.cover_letter import GeneratedCoverLetter

    content = dict(AGENT_LETTER_CONTENT)
    content["body"] = {"paragraphs": ["Sehr geehrte Frau Schmidt,", REWORDED, "Schluss."]}
    p1, p2, p3, p4 = _letter_patches(seeded["db"], _truth(_claim(REWORDED, "misattributed")))
    with p1, p2, p3, p4:
        result = await render_document(
            document_kind="cover_letter", content=content,
            job_id=str(seeded["job_id"]), format="docx",
        )
    assert "docx_base64" not in result and "html_url" not in result
    assert result["open_points"]["decided"] is False
    assert [f["key"] for f in result["open_points"]["findings"]] == ["oracle.misattributed"]
    row = await _row(seeded["db"], GeneratedCoverLetter, result["document_id"])
    assert review_signals.open_points_pending(row) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cv", "cover_letter"])
async def test_a_clean_agent_written_document_passes_untouched(seeded, kind):  # noqa: F811
    """Variation 2: no blocking finding → no hold; the envelope is exactly the old one."""
    from applire.mcp.server import render_document

    clean = _truth(_claim("Python", "grounded"))
    if kind == "cv":
        result = await _render_cv(seeded, clean)
    else:
        p1, p2, p3, p4 = _letter_patches(seeded["db"], clean)
        with p1, p2, p3, p4:
            result = await render_document(
                document_kind="cover_letter", content=dict(AGENT_LETTER_CONTENT),
                job_id=str(seeded["job_id"]), format="docx",
            )
    assert "open_points" not in result
    assert result["html_url"] and result["pdf_url"] and result["docx_url"]
    assert base64.b64decode(result["docx_base64"])[:2] == b"PK"


def test_no_exemption_for_agent_written_documents_is_left():
    """The ADR-054 exemption ("agent-written is never held") is gone from the predicate."""
    from types import SimpleNamespace

    red = _truth(_claim(CV_CLAIM, "unbacked"))
    for origin in ("agent", "pipeline", None):
        rec = SimpleNamespace(ats_report=None, review_state=None, truthfulness_report=red,
                              origin=origin, audit_state=None)
        assert review_signals.open_points_check(rec) is not None, origin
