# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-G36 fix round 3, item 6 (delivery re-probe on e7e7cfa3: crossa CV 9cbaa96b, Lena CV
d0dc6d25). The Oracle judged 17 of 20 claims; 3 got no usable judgement (the backing seam
drops their citation on every run, ``ORACLE_JUDGEMENT_CITATION_DROP seam=backing``).
Nothing is held. The web headline said "1 Punkt braucht Dich", made up only of the
"Nicht geprüft: Wahrheits-Orakel" row the person cannot clear.

This test serves exactly that state through the real doors (``get_cv_ats_report`` and
``get_cv_truthfulness_report``) and captures it as
``frontend/lib/__tests__/fixtures/served-partial-judgement-cv.json``; the frontend test
``review-served-partial-judgement.test.tsx`` reads that file. It also pins what the agent
door holds for it: nothing. ``APPLIRE_UPDATE_SERVED_FIXTURES=1`` rewrites the file."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.ats import ATSReport  # noqa: E402
from applire.schemas.oracle import TruthfulnessReport  # noqa: E402
from applire.services import finding_registry, review_signals  # noqa: E402
from applire.services.terminal_review_outcome import TerminalReviewOutcome, build_terminal_review_check  # noqa: E402

_FIXTURES = Path(__file__).parent.parent.parent / "frontend" / "lib" / "__tests__" / "fixtures"
_CV_ID = uuid.UUID("00000000-0000-4000-8000-0000000000c3")
JUDGED, UNJUDGED = 17, 3


def _truth() -> dict:
    claims = [{"claim": {"text": f"Synthetic grounded statement {i}.", "location": f"w1.b{i}", "kind": "sentence"},
               "verdict": {"verdict": "grounded", "checker": "grounding"}} for i in range(JUDGED)]
    # the fail-safe placeholder a claim without a usable judgement keeps (ADR-068 cl. 3)
    claims += [{"claim": {"text": f"Synthetic statement {i} the backing seam dropped.", "location": f"w2.b{i}",
                          "kind": "sentence"},
                "verdict": {"verdict": "unverifiable", "checker": "backing_judgement"}} for i in range(UNJUDGED)]
    counts = {k: 0 for k in ("grounded", "inflated", "misattributed", "unbacked", "unverifiable", "not_applicable")}
    counts["grounded"], counts["unverifiable"] = JUDGED, UNJUDGED
    report = {"version": "2", "document_kind": "cv", "claims": claims, "counts": counts,
              "judgement_unavailable": UNJUDGED}
    finding_registry.annotate_truthfulness(report)
    return json.loads(TruthfulnessReport.model_validate(report).model_dump_json())


def _ats() -> dict:
    clean = TerminalReviewOutcome(chain_id="cv_terminal_review", path="approved", approved=True,
                                  blocking_issues=(), minor_issues=(), rounds=1)
    report = {"version": 1, "document": "cv", "passed": 2, "failed": 0, "not_applicable": 0,
              "checks": [{"id": "contact-name", "status": "pass"},
                         build_terminal_review_check(clean, document="cv").model_dump(mode="json", exclude_none=True)],
              "keywords": {"present": ["SAP"], "missing": [], "present_unsupported": []}}
    finding_registry.annotate_report(report)
    return json.loads(json.dumps(ATSReport.model_validate(report).model_dump(mode="json")))


def _record():
    return SimpleNamespace(id=_CV_ID, status="ready", ats_report=_ats(), truthfulness_report=_truth(),
                           review_state=None, origin="pipeline", audit_state=None, deleted_at=None)


async def _served() -> dict:
    from applire.services import cv as svc

    rec = _record()
    with patch.object(svc, "_load_cv", AsyncMock(return_value=rec)):
        ats = await svc.get_cv_ats_report(rec.id, AsyncMock(), user_id=uuid.uuid4())
        truth = await svc.get_cv_truthfulness_report(rec.id, AsyncMock(), user_id=uuid.uuid4())
    truth_wire = json.loads(truth.model_dump_json())["report"]
    truth_wire["generated_at"] = "2026-11-25T12:00:00Z"  # the only moving part
    return {"ats": json.loads(ats.model_dump_json()), "truth": truth_wire}


@pytest.mark.asyncio
async def test_the_partial_judgement_fixture_is_the_served_response():
    wire = await _served()
    assert wire["truth"]["judgement_unavailable"] == UNJUDGED and len(wire["truth"]["claims"]) == JUDGED + UNJUDGED
    text = json.dumps(wire, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    path = _FIXTURES / "served-partial-judgement-cv.json"
    if os.environ.get("APPLIRE_UPDATE_SERVED_FIXTURES") == "1":
        path.write_text(text, encoding="utf-8")
    if not _FIXTURES.parent.parent.parent.is_dir():
        pytest.skip("frontend tree not checked out")
    assert path.read_text(encoding="utf-8") == text


def test_the_agent_door_holds_nothing_for_a_partial_judgement():
    rec = _record()
    assert review_signals.open_points_pending(rec) is None
