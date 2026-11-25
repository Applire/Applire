# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-G32 fix round 2 (Sorbet invite-gate, adversarial X-1/X-2): every fix crosses the
REAL seam — producer (``build_terminal_review_check`` + the registry stamp) → the
``ATSReport`` model → stored JSON → the serving function (``get_cv_ats_report`` /
``get_cover_letter_ats_report``) → ``ATSReportResponse`` → the frontend reader.

The served responses are written to ``frontend/lib/__tests__/fixtures/served-held-*.json``
and the frontend's one-count test reads THOSE files (a captured served response, never a
hand-written one). This test fails when the fixture no longer equals what the server
serves; ``APPLIRE_UPDATE_SERVED_FIXTURES=1`` rewrites it.

Shapes: a held CV and a held letter with keyed Oracle points (an unbacked claim, a
misattributed one, a posting term), reviewer findings (one truth, two coverage/form), a
truth code fact next to advice code facts, and a translate card. Personas synthetic."""
from __future__ import annotations

import copy
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

from applire.schemas.ats import ATSReport, ATSReportResponse, CheckNote  # noqa: E402
from applire.services import finding_registry  # noqa: E402
from applire.services.terminal_review_outcome import (  # noqa: E402
    TerminalNote,
    TerminalReviewOutcome,
    build_terminal_review_check,
)

_FIXTURES = Path(__file__).parent.parent.parent / "frontend" / "lib" / "__tests__" / "fixtures"
_DOC_ID = {"cv": uuid.UUID("00000000-0000-4000-8000-0000000000c1"),
           "cover_letter": uuid.UUID("00000000-0000-4000-8000-0000000000c2")}

UNBACKED = "I led a team of 120 people at Nordhafen Logistik."
MISPLACED = "At Lindenau Fördertechnik I cut changeover time by 30 percent."
GROUNDED = "I mentor two junior engineers."
TERM = "Maintenance"
F_TRUTH = "w1: the bullet claims a budget of 2 Mio EUR the profile never states."
F_COVER = "The CV does not show the posting's SAP PM requirement."
F_FORM = "The summary repeats the first bullet word for word."


def _claim(text, verdict, location):
    return {"claim": {"text": text, "location": location, "kind": "sentence"},
            "verdict": {"verdict": verdict, "checker": "grounding"}}


def _truth(kind):
    claims = [_claim(UNBACKED, "unbacked", "w1.b1"), _claim(MISPLACED, "misattributed", "w2.b1"),
              _claim(GROUNDED, "grounded", "summary")]
    counts = {k: 0 for k in ("grounded", "inflated", "misattributed", "unbacked", "unverifiable")}
    for c in claims:
        counts[c["verdict"]["verdict"]] += 1
    report = {"version": "2", "document_kind": kind, "claims": claims, "counts": counts}
    finding_registry.annotate_truthfulness(report)
    return report


def _produced(kind):
    """The producer's row: the terminal-review check of a held outcome, the classes the
    class call set, one truth code fact and two advice code facts."""
    if kind == "cv":
        code = (TerminalNote(kind="code", key="cv.skill_untied", text="Skills entries with no tie …",
                             subjects=("Kubernetes",)),
                TerminalNote(kind="code", key="cv.skills_cap", text="The skills list is over …", subjects=("S3",)),
                TerminalNote(kind="code", key="cv.vault_field_missing",
                             text="Return these profile fields, by id: 3f2a9c1e-0b7d-4e55-9a61-2c8d1f0e7b42"))
    else:
        code = (TerminalNote(kind="code", key="letter.new_figure", text="A figure no source states: 40 %.",
                             subjects=("40 %",)),
                TerminalNote(kind="code", key="letter.closing", text="The letter does not end …"),
                TerminalNote(kind="code", key="letter.page_length", text="The letter renders to 2 pages …"))
    outcome = TerminalReviewOutcome(
        chain_id=f"{kind}_terminal_review", path="cycle_detected", approved=False,
        blocking_issues=(F_TRUTH, F_COVER, F_FORM), minor_issues=(), rounds=3, notes=code,
        plain_notes=((F_TRUTH, "A budget figure the profile does not state."),
                     (F_COVER, "The posting asks for SAP PM; the CV does not show it."),
                     (F_FORM, "The summary repeats the first bullet.")),
        point_tags=((F_TRUTH, "truth"), (F_COVER, "coverage"), (F_FORM, "form")),
    )
    check = build_terminal_review_check(outcome, document=kind).model_dump(mode="json", exclude_none=True)
    report = {
        "version": 1, "document": kind, "passed": 1, "failed": 2, "not_applicable": 0,
        "checks": [
            {"id": "contact-name", "status": "pass"},
            {"id": "document-language", "status": "fail", "details": "Two sentences are in English.",
             "details_key": "document-language-mixed"},
            check,
        ],
        "keywords": {"present": [], "missing": [], "present_unsupported": [TERM]},
    }
    finding_registry.annotate_report(report)
    # stored as the model writes it, read back as JSON (the JSONB column)
    return json.loads(json.dumps(ATSReport.model_validate(report).model_dump(mode="json")))


def _record(kind):
    base = dict(id=_DOC_ID[kind], status="ready", ats_report=_produced(kind), truthfulness_report=_truth(kind),
                review_state=None, origin="pipeline", audit_state=None, deleted_at=None)
    if kind == "cover_letter":
        base["letter_data"] = {"body": {"paragraphs": ["x"]}}
    return SimpleNamespace(**base)


async def _served(kind) -> dict:
    rec = _record(kind)
    if kind == "cv":
        from applire.services import cv as svc

        with patch.object(svc, "_load_cv", AsyncMock(return_value=rec)):
            resp = await svc.get_cv_ats_report(rec.id, AsyncMock(), user_id=uuid.uuid4())
    else:
        from applire.services import cover_letter as svc

        with patch.object(svc, "owned_row", AsyncMock(return_value=rec)), \
                patch.object(svc, "resolve_user_id", lambda u, site=None: u):
            resp = await svc.get_cover_letter_ats_report(rec.id, AsyncMock(), user_id=uuid.uuid4())
    assert isinstance(resp, ATSReportResponse)
    wire = json.loads(resp.model_dump_json())
    return {"ats": wire, "truth": rec.truthfulness_report}


def _card(wire):
    (card,) = [c for c in wire["ats"]["report"]["checks"] if c["id"] == "terminal-review"]
    return card


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cv", "cover_letter"])
async def test_one_count_crosses_the_wire_with_its_finding_keys(kind):
    wire = await _served(kind)
    card = _card(wire)
    points = [n for n in card["notes"] if n.get("point")]
    # X-2: the truth lines keep the group-1 key they ARE, so the headline counts them once
    keyed = {n["finding_key"] for n in points if n.get("finding_key")}
    assert len(keyed) == 3 and any(k.startswith("ats:") for k in keyed)
    # the one count: 3 Oracle/ATS truth points + 1 truth finding + 1 truth code fact
    assert card["details_params"]["count"] == len(points) == 5
    truth_code = "cv.skill_untied" if kind == "cv" else "letter.new_figure"
    assert {n.get("key") for n in points if n["kind"] == "code" and not str(n.get("key")).startswith("oracle.")} \
        >= {truth_code}
    # advice rides the same card, marked advice, never a point
    advice = [n for n in card["notes"] if n.get("advice")]
    assert {n.get("tag") for n in advice if n["kind"] == "finding"} == {"coverage", "form"}
    assert all(not n.get("point") for n in advice)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cv", "cover_letter"])
async def test_the_frontend_fixture_is_the_served_response(kind):
    wire = await _served(kind)
    path = _FIXTURES / f"served-held-{'cv' if kind == 'cv' else 'letter'}.json"
    text = json.dumps(wire, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    if os.environ.get("APPLIRE_UPDATE_SERVED_FIXTURES") == "1":
        path.write_text(text, encoding="utf-8")
    if not _FIXTURES.parent.parent.parent.is_dir():
        pytest.skip("frontend tree not checked out")
    assert path.read_text(encoding="utf-8") == text, (
        "the served response changed — rerun with APPLIRE_UPDATE_SERVED_FIXTURES=1 and "
        "check the frontend one-count test still holds")


# ── the guard: no key a producer puts on a check or a note is dropped by the models ──


def _keys_lost(served: dict) -> set[str]:
    wire = ATSReport.model_validate(served).model_dump(mode="json")
    lost: set[str] = set(f"<report>.{k}" for k in set(served) - set(wire))
    for c_in, c_out in zip(served.get("checks") or [], wire.get("checks") or []):
        lost |= {f"check.{k}" for k in set(c_in) - set(c_out)}
        for n_in, n_out in zip(c_in.get("notes") or [], c_out.get("notes") or []):
            lost |= {f"note.{k}" for k in set(n_in) - set(n_out)}
    return lost


@pytest.mark.parametrize("kind", ["cv", "cover_letter"])
def test_no_producer_key_is_dropped_by_the_wire_models(kind):
    """Fails when ANY key the producers put on a check or a note is not a model field
    (X-1 dropped ``tag``/``point``/``advice``, X-2 ``finding_key``)."""
    from applire.services import review_signals

    rec = _record(kind)
    served = review_signals.with_open_points_check(copy.deepcopy(rec.ats_report), rec)
    assert _keys_lost(served) == set()
    # the advice-only shape (the served advice check) and a held row without a truth report
    rec.truthfulness_report = None
    served = review_signals.with_open_points_check(copy.deepcopy(rec.ats_report), rec)
    assert _keys_lost(served) == set()


def test_every_key_a_producer_writes_on_a_note_is_a_model_field():
    """The static half of the guard: every ``n["<key>"] = …`` / note-dict literal key in
    the producers is a ``CheckNote`` field, so a new key cannot ship unmodelled."""
    import re

    fields = set(CheckNote.model_fields)
    src = ""
    for mod in ("review_signals", "terminal_review_outcome", "point_class"):
        src += (_backend / "applire" / "services" / f"{mod}.py").read_text(encoding="utf-8")
    written = set(re.findall(r'\bn\["([a-z_]+)"\]\s*=', src)) | set(re.findall(r'\bnote\["([a-z_]+)"\]\s*=', src))
    literal = set()
    for m in re.finditer(r'\{\s*"kind":[^{}]*\}', src):
        body = m.group(0)
        if '"text":' not in body and '"key":' not in body:
            continue  # a repeated-demand signal (``kind`` = the demand's kind), not a note
        literal |= set(re.findall(r'"([a-z_]+)":', body))
    assert (written | literal) - fields == set()
