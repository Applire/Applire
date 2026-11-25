# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""DH-1 (founder ruling 2026-11-25, "reviewer tags, truth holds"; built as DH-1b): each
open point of the final review is classed truth | coverage | form by a separate call. Only truth points (and the Oracle/ATS truth
keys) hold the download; coverage and form points are advice on the same card and never
hold. A missing or unknown class is truth (fails closed), so every row stored before the
class existed holds as it did. The shapes are the Sorbet invite-gate delivery rows
(CV 6e7434a5: five posting gaps; CV bba950da: a skills cap and a hyphenation), trimmed."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services import review_signals  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_cv_docx_endpoint import client, db, ready_cv  # noqa: E402,F401


def _finding(text, tag=None):
    note = {"kind": "finding", "key": None, "text": text, "plain": f"{text}.", "subjects": []}
    if tag is not None:
        note["tag"] = tag
    return note


GAPS = [_finding(f"posting gap {i}", "coverage") for i in range(2)]
CAP = _finding("skills list over the cap", "form")
UNBACKED = _finding("an unbacked support of work preparation", "truth")
CODE = {"kind": "code", "key": "cv.claim_missing", "text": "claimable …", "subjects": ["Hohe Last"]}


def _ats(notes, *, count):
    return {
        "version": 1, "document": "cv", "passed": 1, "failed": 1, "not_applicable": 0,
        "checks": [
            {"id": "contact-name", "status": "pass"},
            {"id": "terminal-review", "status": "fail", "details": "x", "details_key": "terminal-review-open",
             "details_params": {"count": count}, "notes": notes, "open_points_hash": "c" * 16},
        ],
        "keywords": {"present": [], "missing": [], "present_unsupported": []},
    }


def _rec(notes, *, count):
    return SimpleNamespace(ats_report=_ats(notes, count=count), review_state=None, truthfulness_report=None,
                           origin="pipeline", audit_state=None)


# ── the producer: a separate call classes the open findings (DH-1b) ─────────


def test_the_reviewers_never_ask_for_a_class():
    """DH-1b: a class field inside the reviewer made it stricter on clean documents
    (1/20 → 11/20 blocking verdicts, replayed); the reviewer prompts stay as they were."""
    from applire.prompts import review_cover_letter as rl
    from applire.prompts import review_cv_tailoring as rc

    for prompt in (rc.TERMINAL_REVIEW_SYSTEM_PROMPT, rc.V2_TERMINAL_REVIEW_SYSTEM_PROMPT,
                   rl.TERMINAL_REVIEW_SYSTEM_PROMPT, rl.V2_TERMINAL_REVIEW_SYSTEM_PROMPT,
                   rc.REVIEW_SYSTEM_PROMPT, rl.REVIEW_SYSTEM_PROMPT):
        assert '"class"' not in prompt


def _outcome(blocking=(), notes=()):
    from applire.services.terminal_review_outcome import TerminalReviewOutcome

    return TerminalReviewOutcome(chain_id="cv_terminal_review", path="exhausted", approved=False,
                                 blocking_issues=tuple(blocking), minor_issues=(), rounds=3, notes=tuple(notes))


class _Provider:
    def __init__(self, answer=None, exc=None):
        self.answer, self.exc, self.calls = answer, exc, []

    async def aparse_json(self, prompt, *, system=None, **kw):
        from applire.providers.llm.debug_log import current_call_site

        self.calls.append((prompt, system, current_call_site()[0]))
        if self.exc:
            raise self.exc
        return self.answer


@pytest.mark.asyncio
async def test_a_clean_document_makes_no_class_call():
    from applire.services.point_class import classify_open_points

    provider = _Provider({"classes": []})
    out = _outcome(blocking=())
    assert await classify_open_points(out, document={"summary": "x"}, provider=provider) is out
    code_only = _outcome(blocking=("[cv.skills_cap] The skills list holds 23 entries.",))
    assert await classify_open_points(code_only, document=None, provider=provider) is code_only
    assert provider.calls == []


@pytest.mark.asyncio
async def test_the_open_findings_get_their_class_under_their_own_stage():
    from applire.prompts.point_class import POINT_CLASS_SYSTEM_PROMPT
    from applire.services.point_class import STAGE, classify_open_points
    from applire.services.terminal_review_outcome import TerminalNote

    provider = _Provider({"classes": [
        {"index": 0, "class": "coverage"}, {"index": 1, "class": "truth (unbacked)"}, {"index": 2, "class": "form"},
    ]})
    out = _outcome(
        blocking=("The CV omits Budget planning.", "[cv.skills_cap] cap", "w1: an unbacked support role."),
        notes=(TerminalNote(kind="finding", key="letter.post_condense", text="The list is long."),),
    )
    got = await classify_open_points(out, document={"summary": "x"}, provider=provider)
    (prompt, system, stage), = provider.calls
    assert system == POINT_CLASS_SYSTEM_PROMPT and stage == STAGE
    assert "[0] The CV omits Budget planning." in prompt and "[cv.skills_cap]" not in prompt
    assert dict(got.point_tags) == {"The CV omits Budget planning.": "coverage", "w1: an unbacked support role.": "truth"}
    assert got.notes[0].tag == "form"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [
    _Provider(exc=TimeoutError()),
    _Provider(answer={"classes": "coverage"}),
    _Provider(answer={"classes": [{"index": 1, "class": "coverage"}]}),  # index 0 missing
    _Provider(answer={"classes": [{"index": 0, "class": "advice"}]}),
])
async def test_the_class_call_fails_closed(provider):
    from applire.services.point_class import classify_open_points

    out = _outcome(blocking=("w1: an unbacked claim.",))
    got = await classify_open_points(out, document=None, provider=provider)
    # no answer, or an answer that is not exactly a class word: truth (MD-G32 X-6)
    assert all(cls == "truth" for _t, cls in got.point_tags)
    notes = [{"kind": "finding", "key": None, "text": "w1: an unbacked claim.", "plain": "P.", "subjects": []}]
    assert review_signals.open_points_check(_rec(notes, count=1))["details_params"]["count"] == 1


def test_the_persisted_note_carries_the_class():
    from applire.services.terminal_review_outcome import _finding_note

    note = _finding_note("posting gap", {"posting gap": "Plain."}, {"posting gap": "coverage"})
    assert note["tag"] == "coverage" and note["plain"] == "Plain."
    assert "tag" not in _finding_note("old shape", {})


# ── the hold (pure) ───────────────────────────────────────────────────────────


def test_an_advice_only_document_is_not_held_and_needs_no_basis():
    rec = _rec(GAPS + [CAP, CODE], count=3)
    assert review_signals.open_points_check(rec) is None
    assert review_signals.open_points_pending(rec) is None
    advice = review_signals.advice_check(rec)
    assert advice["status"] == "pass" and advice["details_key"] == "terminal-review-advice"
    assert advice.get("open_points_hash") is None and advice["delivery_class"] == "S"
    assert all(n["point"] is False for n in advice["notes"])


def test_one_truth_point_and_two_advice_points_hold_with_count_one():
    rec = _rec([UNBACKED] + GAPS, count=3)
    check = review_signals.open_points_check(rec)
    assert check["details_params"]["count"] == 1
    assert [n["text"] for n in check["notes"] if n["point"]] == ["an unbacked support of work preparation"]
    assert [n["text"] for n in check["notes"] if n.get("advice")] == ["posting gap 0", "posting gap 1"]
    held = review_signals.open_points_pending(rec)
    assert review_signals.open_points_count(held) == 1


@pytest.mark.parametrize("tag", [None, "", "untagged", "TRUTH"])
def test_a_missing_or_unknown_class_holds(tag):
    rec = _rec([_finding("a point", tag)] + GAPS, count=3)
    check = review_signals.open_points_check(rec)
    assert check is not None and check["details_params"]["count"] == 1


def test_a_row_stored_before_the_class_existed_holds_as_before():
    old = [{k: v for k, v in n.items() if k != "tag"} for n in GAPS + [CAP]] + [CODE]
    check = review_signals.open_points_check(_rec(old, count=3))
    assert check["details_params"]["count"] == 3
    assert [n["key"] for n in check["notes"] if not n["point"]] == ["cv.claim_missing"]


def test_the_served_report_shows_an_advice_only_row_as_passed():
    rec = _rec(GAPS, count=2)
    served = review_signals.with_open_points_check(rec.ats_report, rec)
    row = next(c for c in served["checks"] if c["id"] == "terminal-review")
    assert row["status"] == "pass" and row["details_key"] == "terminal-review-advice"
    assert (served["passed"], served["failed"]) == (2, 0)


# ── the doors: REST download + the agent door through mcp.call_tool ──────────


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


async def _call(db, name, args):  # noqa: F811
    from applire.mcp.server import mcp

    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)), \
         patch("applire.auth.links.sign_document_url", side_effect=lambda k, i, u, v: v):
        out = await mcp.call_tool(name, args)
    blocks = out[0] if isinstance(out, tuple) else out
    return blocks


@pytest.mark.asyncio
async def test_an_advice_only_cv_downloads_on_every_door(client, db, ready_cv):  # noqa: F811
    ready_cv.ats_report = _ats(GAPS + [CAP, CODE], count=3)
    await db.commit()
    r = await client.get(f"/api/cv/{ready_cv.id}/docx")
    assert r.status_code == 200 and r.content[:2] == b"PK"

    status = json.loads((await _call(db, "get_cv_status", {"cv_id": str(ready_cv.id)}))[0].text)
    op = status["open_points"]
    assert (op["count"], op["decided"], op["basis"], op["findings"]) == (0, True, None, [])
    assert [a["plain"] for a in op["advice"]][:3] == ["posting gap 0.", "posting gap 1.", "skills list over the cap."]
    assert all(a["blocks"] is False for a in op["advice"])
    assert status.get("html_url")  # the signed link is handed out — nothing holds it

    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        await _call(db, "decide_open_points", {"cv_id": str(ready_cv.id), "keep": True})


@pytest.mark.asyncio
async def test_a_truth_point_beside_advice_holds_every_door_with_count_one(client, db, ready_cv):  # noqa: F811
    ready_cv.ats_report = _ats([UNBACKED] + GAPS, count=3)
    await db.commit()
    r = await client.get(f"/api/cv/{ready_cv.id}/docx")
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "open_points_undecided", "count": 1}

    status = json.loads((await _call(db, "get_cv_status", {"cv_id": str(ready_cv.id)}))[0].text)
    op = status["open_points"]
    assert op["count"] == 1 and op["decided"] is False and op["basis"]
    assert [a["plain"] for a in op["advice"]] == ["posting gap 0.", "posting gap 1."]
    assert "html_url" not in status


def test_the_class_survives_the_report_schema():
    """The report is stored through ``ATSReport``; a class the schema dropped would
    leave every finding untagged, and an untagged finding holds."""
    from applire.schemas.ats import CheckNote

    for tag in ("truth", "coverage", "form"):
        dumped = CheckNote(kind="finding", text="x", plain="x.", tag=tag).model_dump()
        assert dumped["tag"] == tag
        assert review_signals._is_advice(dumped) is (tag != "truth")
    assert CheckNote(kind="finding", text="x").model_dump()["tag"] is None


def test_no_key_of_a_finding_note_is_dropped_by_the_schema():
    """Every key the producer puts on a finding note must be a ``CheckNote`` field."""
    from applire.schemas.ats import CheckNote
    from applire.services.terminal_review_outcome import _finding_note

    note = _finding_note("posting gap", {"posting gap": "Der Begriff fehlt."}, {"posting gap": "coverage"})
    assert set(note) <= set(CheckNote.model_fields), set(note) - set(CheckNote.model_fields)
    stored = CheckNote(**note).model_dump()
    assert stored["tag"] == "coverage" and review_signals._is_advice(stored)


def test_the_served_marks_survive_the_report_schema():
    """The web reads ``point`` and ``advice`` off the served report, and the served report
    is validated through ``ATSReport``: no key the open-points check puts on a note or
    on the row may be dropped there."""
    from applire.schemas.ats import ATSCheck, ATSReport, CheckNote

    for notes, count in ((GAPS, 2), ([UNBACKED, *GAPS, CAP, CODE], 5)):
        one = _rec(notes, count=count)
        for check in review_signals.with_open_points_check(one.ats_report, one)["checks"]:
            assert set(check) <= set(ATSCheck.model_fields), set(check) - set(ATSCheck.model_fields)
    rec = _rec([UNBACKED, *GAPS, CAP, CODE], count=5)
    served = review_signals.with_open_points_check(rec.ats_report, rec)
    row = next(c for c in served["checks"] if c["id"] == "terminal-review")
    for n in row["notes"]:
        assert set(n) <= set(CheckNote.model_fields), set(n) - set(CheckNote.model_fields)
    wire = ATSReport.model_validate(served).model_dump()
    notes = next(c for c in wire["checks"] if c["id"] == "terminal-review")["notes"]
    assert [n["point"] for n in notes if n["kind"] == "finding"] == [True, False, False, False]
    assert [bool(n["advice"]) for n in notes if n["kind"] == "finding"] == [False, True, True, True]
    assert next(c for c in wire["checks"] if c["id"] == "terminal-review")["details_params"]["count"] == 1
