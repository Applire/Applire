# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet MD-G9 (adv/holds fix round): incomplete, refused, failed or pending never
releases the download.

One test per adversarial finding, each red on the tree the pass ran against
(8dcf0841): H-1, H-2 (loop + cv.py bound), N-1, D-1, D-3 (CV + letter), D-4, D-5,
D-8, D-9. The probes behind them live in the adv/holds run folder.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.exceptions import LLMTimeoutError, LLMTruncatedError  # noqa: E402
from applire.services import audit_state, review_signals  # noqa: E402
from applire.services.reviewer import review_and_refine  # noqa: E402
from applire.services.terminal_review_outcome import (  # noqa: E402
    build_terminal_review_check,
    settle_to_outcome,
)

sys.path.insert(0, str(Path(__file__).parent))
from test_cv_docx_endpoint import client, db, ready_cv  # noqa: E402,F401
from test_sorbet_l_letter_open_points import RED, _claim, _truth, red_letter  # noqa: E402,F401

TERMINAL = review_signals.TERMINAL_REVIEW_KEY
_BLOCK = "Bullet 'Led a team of 40' is not backed by the vault"
REJECT = {"approved": False, "feedback": "fix it",
          "issues": [{"severity": "blocking", "check": "1", "issue": _BLOCK}]}


# ---------------------------------------------------------------------------
# The loop end (H-1, H-2, N-1) — the composition cv._terminal_review uses
# ---------------------------------------------------------------------------


class _Provider:
    """Reviewer seat answers from ``verdicts`` (an Exception raises, "malformed" is
    a JSON error); the corrector seat writes d1, d2, … or raises ``corrector``."""

    def __init__(self, verdicts, corrector=None):
        self.v, self.read, self.n, self.corrector = list(verdicts), [], 0, corrector

    async def aparse_json(self, prompt, system=None, **kw):
        if system == "rev":
            self.read.append(prompt)
            x = self.v.pop(0)
            if isinstance(x, Exception):
                raise x
            if x == "malformed":
                raise json.JSONDecodeError("Expecting ',' delimiter", "{", 1)
            return x
        self.n += 1
        if isinstance(self.corrector, Exception):
            raise self.corrector
        if self.corrector == "malformed":
            raise json.JSONDecodeError("Expecting ',' delimiter", "{", 1)
        return {"summary": f"d{self.n}"}


async def _loop_end(verdicts, max_retries=1, corrector=None):
    seen: list = []
    p = _Provider(verdicts, corrector)
    out = await review_and_refine(
        source="src", draft={"summary": "d0"},
        generator_prompt_fn=lambda d, f, s: "correct", generator_system="gen",
        reviewer_prompt_fn=lambda s, d: d["summary"], reviewer_system="rev",
        provider=p, max_retries=max_retries, on_settle=seen.append, final_review=True,
    )
    st = seen[0]
    # cv.py's wiring: "reviewed" = the draft a verdict ARRIVED on (H-2).
    outcome = settle_to_outcome(st, chain_id="cv_terminal_review", reviewed_draft=st.verdict_draft)
    check = build_terminal_review_check(outcome).model_dump()
    rec = SimpleNamespace(ats_report={"checks": [check]}, review_state=None, origin="pipeline")
    return out, st, check, review_signals.open_points_pending(rec)


@pytest.mark.asyncio
@pytest.mark.parametrize("corrector", [LLMTruncatedError("cap"), "malformed"])
async def test_h1_a_failed_corrector_after_a_blocking_verdict_holds(corrector):
    out, st, check, held = await _loop_end([REJECT], corrector=corrector)
    assert out == {"summary": "d0"} and st.verdict_draft == {"summary": "d0"}
    assert check["details_key"] == "terminal-review-open" and check["open_points_hash"]
    assert held is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("final", [LLMTimeoutError("t"), "malformed"])
async def test_h2_a_final_verdict_that_never_arrived_does_not_count_as_reviewed(final):
    out, st, check, held = await _loop_end([REJECT, final])
    assert out == {"summary": "d1"}
    # the prompt for d1 was built, but no verdict read it
    assert st.verdict_draft == {"summary": "d0"}
    assert check["details_key"] == "terminal-review-unverified"
    assert held is not None


@pytest.mark.asyncio
async def test_h2_a_review_only_call_that_times_out_holds():
    out, st, check, held = await _loop_end([LLMTimeoutError("t")], max_retries=0)
    assert st.verdict_draft is None
    assert check["details_key"] == "terminal-review-incomplete" and check["open_points_hash"]
    assert held is not None


@pytest.mark.asyncio
async def test_n1_an_approval_that_lists_a_blocking_issue_is_a_rejection():
    approve_blocking = {**REJECT, "approved": True}
    out, st, check, held = await _loop_end([REJECT, approve_blocking])
    assert st.approved is False and st.path == "exhausted"
    assert check["details_key"] == "terminal-review-open" and held is not None


# ---------------------------------------------------------------------------
# H-2 at the cv.py bound: the review-only call is not skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h2_the_cv_bound_runs_the_review_only_call_after_a_lost_final_verdict(tmp_path):
    import test_cv_terminal_review as tct
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.db.session import Base
    from applire.models.cv import GeneratedCV
    from applire.services.review_issues import ReviewSettle

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        ids = await tct._seed(session)
        calls: list = []

        async def fake(**kw):
            if kw.get("chain_id") != "cv_terminal_review":
                return kw["draft"]
            calls.append((kw.get("max_retries"), kw.get("final_review")))
            d = kw["draft"]
            kw["reviewer_prompt_fn"](kw["source"], d)
            if kw.get("max_retries", 1) <= 0:  # the review-only call: the outage persists
                kw["on_settle"](ReviewSettle(path="reviewer_call_failed", approved=False,
                                             blocking_issues=(), minor_issues=(), rounds=0,
                                             settled=d, verdict_draft=None))
                return d
            rewrite = {**d, "summary": f"Fassung {len(calls)}."}
            if kw.get("final_review"):
                # the final verdict's PROMPT is built for the rewrite; the call times out
                kw["reviewer_prompt_fn"](kw["source"], rewrite)
                kw["on_settle"](ReviewSettle(path="reviewer_call_failed", approved=False,
                                             blocking_issues=(_BLOCK,), minor_issues=(), rounds=1,
                                             settled=rewrite, verdict_draft=d))
            else:
                kw["on_settle"](ReviewSettle(path="exhausted", approved=False,
                                             blocking_issues=(_BLOCK,), minor_issues=(), rounds=1,
                                             settled=rewrite, verdict_draft=d))
            return rewrite

        with patch.dict("os.environ", {"CV_PIPELINE": "v1", "LETTER_PIPELINE": "v1"}):
            await tct._run_pipeline(session, ids, review_fake=fake)
        assert calls[-1] == (0, True), calls  # the bound did not skip it
        record = await session.get(GeneratedCV, ids[2])
        row = next(c for c in record.ats_report["checks"] if c["id"] == "terminal-review")
        assert row["details_key"] in ("terminal-review-unverified", "terminal-review-incomplete"), row
        assert review_signals.open_points_pending(record) is not None
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_h2_a_first_verdict_that_never_arrived_gets_the_review_only_call():
    """The loop broke on 'nothing changed' (the verdict call failed in round 1, so the
    draft came back as it went in) — no verdict read it: one review-only call."""
    import test_cv_terminal_review as tct
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.db.session import Base
    from applire.services.review_issues import ReviewSettle

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        ids = await tct._seed(session)
        calls: list = []

        async def fake(**kw):
            if kw.get("chain_id") != "cv_terminal_review":
                return kw["draft"]
            calls.append((kw.get("max_retries"), kw.get("final_review")))
            d = kw["draft"]
            kw["reviewer_prompt_fn"](kw["source"], d)
            if kw.get("max_retries", 1) <= 0:  # the retry answers
                kw["on_settle"](ReviewSettle(path="approved", approved=True, blocking_issues=(),
                                             minor_issues=(), rounds=1, settled=d, verdict_draft=d))
            else:
                kw["on_settle"](ReviewSettle(path="reviewer_call_failed", approved=False,
                                             blocking_issues=(), minor_issues=(), rounds=0,
                                             settled=d, verdict_draft=None))
            return d

        with patch.dict("os.environ", {"CV_PIPELINE": "v1", "LETTER_PIPELINE": "v1"}):
            await tct._run_pipeline(session, ids, review_fake=fake)
        assert calls[-1] == (0, True), calls
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# The audit seam (D-3, D-5) and the pending window (D-4)
# ---------------------------------------------------------------------------

_OPEN_ROW = {"id": "terminal-review", "status": "fail", "details": "x",
             "details_key": "terminal-review-open", "details_params": {"count": 1},
             "notes": [{"kind": "finding", "text": _BLOCK, "plain": None}],
             "open_points_hash": "a" * 16, "delivery_class": "A", "resolver": "decide_open"}


def _cv_patches(*, truth=None, truth_raises=None, pdf_raises=None):
    from applire.services import cv as cv_mod

    pdf = AsyncMock(side_effect=pdf_raises) if pdf_raises else AsyncMock(return_value=b"pdf")
    truth_mock = AsyncMock(side_effect=truth_raises) if truth_raises else AsyncMock(return_value=truth)
    return [
        patch.object(cv_mod, "get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch.object(cv_mod, "_html_to_pdf", new=pdf),
        patch("applire.services.ats_audit.extract_text_and_pages", new=MagicMock(return_value=("text", 1))),
        patch.object(cv_mod, "_truth_audit", new=truth_mock),
    ]


async def _reaudit_cv(db, record, terminal_review=None, **kw):
    from contextlib import ExitStack

    from applire.services import cv as cv_mod

    with ExitStack() as stack:
        for p in _cv_patches(**kw):
            stack.enter_context(p)
        await cv_mod._update_ats_report(record, db, critic=False, terminal_review=terminal_review)
    await db.refresh(record)
    return record


GREEN = _truth(_claim("Python", "grounded"))


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", ["log_consent_required", "allowance_exhausted", "user_key_missing", "error"])
async def test_d3_a_cv_re_audit_that_did_not_run_keeps_the_hold(db, ready_cv, refusal):  # noqa: F811
    from applire.services.allowance import AdmissionRefused

    ready_cv.ats_report = {"checks": [dict(_OPEN_ROW)]}
    await db.commit()
    await _reaudit_cv(db, ready_cv, truth=GREEN)  # a normal re-audit: still held on its row
    assert review_signals.open_points_pending(ready_cv) is not None
    exc = RuntimeError("boom") if refusal == "error" else AdmissionRefused(refusal)
    await _reaudit_cv(db, ready_cv, truth_raises=exc)
    assert ready_cv.truthfulness_report is None
    assert ready_cv.audit_state["state"] == "not_run"
    held = review_signals.open_points_pending(ready_cv)
    assert held is not None
    keys = [n.get("key") for n in held["notes"]]
    assert "audit.not_rechecked" in keys
    # the decision the person can take: "Passt so" on exactly this set
    out = await review_signals.keep("cv", ready_cv.id, TERMINAL, db, basis=held["open_points_hash"],
                                    check_basis=True)
    assert review_signals.open_points_pending(out.record) is None


@pytest.mark.asyncio
async def test_d3_a_clean_cv_whose_re_audit_was_refused_is_not_released_unchecked(db, ready_cv):  # noqa: F811
    from applire.services.allowance import AdmissionRefused

    await _reaudit_cv(db, ready_cv, truth=GREEN)
    assert review_signals.open_points_pending(ready_cv) is None
    ready_cv.section_overrides = {"summary": "I led a team of 40 engineers."}
    audit_state.mark_pending(ready_cv)
    await db.commit()
    await _reaudit_cv(db, ready_cv, truth_raises=AdmissionRefused("log_consent_required"))
    assert review_signals.open_points_pending(ready_cv) is not None


@pytest.mark.asyncio
async def test_d3_a_refused_letter_re_audit_keeps_the_hold(db, red_letter):  # noqa: F811
    from applire.services import cover_letter as cl_mod
    from applire.services.allowance import AdmissionRefused

    assert review_signals.open_points_pending(red_letter) is not None
    red_letter.section_overrides = {"body": "unchanged"}
    audit_state.mark_pending(red_letter)
    await db.commit()
    with patch.object(cl_mod, "_letter_truth_audit",
                      new=AsyncMock(side_effect=AdmissionRefused("log_consent_required"))), \
         patch("applire.services.cover_letter_pdf.render_pdf", new=AsyncMock(return_value=b"pdf")), \
         patch("applire.services.ats_audit.extract_text_and_pages", new=MagicMock(return_value=("t", 1))):
        await cl_mod._update_ats_report_letter(red_letter, db, critic=False)
    await db.refresh(red_letter)
    assert red_letter.truthfulness_report is None
    held = review_signals.open_points_pending(red_letter)
    assert held is not None
    texts = " ".join(n.get("text") or "" for n in held["notes"])
    assert "Lindenau" in texts  # the statement held before is still named


@pytest.mark.asyncio
async def test_d4_the_pdf_is_held_while_the_saved_edit_awaits_its_re_audit(client, db, ready_cv):  # noqa: F811
    await _reaudit_cv(db, ready_cv, truth=GREEN)
    assert (await client.get(f"/api/cv/{ready_cv.id}/docx")).status_code == 200
    ready_cv.section_overrides = {"summary": "I led a team of 40 engineers."}
    audit_state.mark_pending(ready_cv)
    await db.commit()
    with patch.object(review_signals, "AUDIT_WAIT_SECONDS", 0.0):
        for door in ("pdf", "docx"):
            r = await client.get(f"/api/cv/{ready_cv.id}/{door}")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "audit_pending", door
    await _reaudit_cv(db, ready_cv, truth=GREEN)
    assert (await client.get(f"/api/cv/{ready_cv.id}/docx")).status_code == 200


def test_d4_a_pending_marker_that_went_stale_holds_as_not_rechecked():
    rec = SimpleNamespace(tailored_data={"a": 1}, content_snapshot=None, section_overrides=None,
                          ats_report=None, review_state=None, origin="pipeline", truthfulness_report=None,
                          audit_state=None)
    audit_state.mark_pending(rec)
    assert audit_state.pending(rec)
    with pytest.raises(review_signals.AuditPending):
        review_signals.hold_if_open_points_pending(rec)
    rec.audit_state = {**rec.audit_state,
                       "at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    assert not audit_state.pending(rec)
    with pytest.raises(review_signals.DeliveryHeld) as exc:
        review_signals.hold_if_open_points_pending(rec)
    assert not isinstance(exc.value, review_signals.AuditPending)


def test_d4_content_changed_after_the_last_audit_without_a_marker_holds():
    rec = SimpleNamespace(tailored_data={"a": 1}, content_snapshot=None, section_overrides=None,
                          ats_report=None, review_state=None, origin="pipeline", truthfulness_report=None,
                          audit_state=None)
    audit_state.record_audit(rec, ran=True)
    assert review_signals.open_points_pending(rec) is None
    rec.tailored_data = {"a": 2}
    assert review_signals.open_points_pending(rec) is not None


@pytest.mark.asyncio
async def test_d5_a_render_crash_on_a_re_audit_does_not_erase_the_hold(client, db, ready_cv):  # noqa: F811
    ready_cv.ats_report = {"checks": [dict(_OPEN_ROW)]}
    await db.commit()
    await _reaudit_cv(db, ready_cv, truth=GREEN, pdf_raises=RuntimeError("chromium crashed"))
    assert ready_cv.ats_report is None
    assert review_signals.open_points_pending(ready_cv) is not None
    # the next normal re-audit carries the terminal-review row forward again
    await _reaudit_cv(db, ready_cv, truth=GREEN)
    row = next(c for c in ready_cv.ats_report["checks"] if c["id"] == "terminal-review")
    assert row["details_key"] == "terminal-review-open"
    assert (await client.get(f"/api/cv/{ready_cv.id}/pdf")).status_code == 409


@pytest.mark.asyncio
async def test_d5_a_render_crash_on_the_generation_audit_keeps_the_fresh_open_points(db, ready_cv):  # noqa: F811
    """No hold stood before this audit (a new document): the fresh terminal outcome's
    open points still reach the card, read from the row kept beside the NULL report."""
    from applire.services.review_issues import ReviewSettle

    outcome = settle_to_outcome(
        ReviewSettle(path="exhausted", approved=False, blocking_issues=(_BLOCK,), minor_issues=(),
                     rounds=2, settled={"summary": "d1"}, verdict_draft={"summary": "d1"}),
        chain_id="cv_terminal_review", reviewed_draft={"summary": "d1"},
    )
    await _reaudit_cv(db, ready_cv, terminal_review=outcome, truth=GREEN,
                      pdf_raises=RuntimeError("chromium crashed"))
    assert ready_cv.ats_report is None
    held = review_signals.open_points_pending(ready_cv)
    assert held is not None
    assert _BLOCK in " ".join(n.get("text") or "" for n in held["notes"])


def test_an_incomplete_review_alone_is_served_as_the_decidable_card():
    row = {"id": "terminal-review", "status": "not_applicable", "details_key": "terminal-review-incomplete",
           "details_params": {"count": 0}, "notes": [], "open_points_hash": "c" * 16}
    rec = SimpleNamespace(ats_report={"checks": [row]}, review_state=None, origin="pipeline",
                          truthfulness_report=None, audit_state=None)
    check = review_signals.open_points_check(rec)
    assert check["details_key"] == "terminal-review-open" and check["resolver"] == "decide_open"
    assert check["details_params"] == {"count": 1}
    assert [n["key"] for n in check["notes"]] == ["review.incomplete"]


# ---------------------------------------------------------------------------
# D-8: the CV is held on its delivered truth report (one pattern)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d8_a_cv_whose_delivered_truth_report_says_stop_is_held(client, db, ready_cv):  # noqa: F811
    await _reaudit_cv(db, ready_cv, truth=_truth(_claim("I led a team of 40 engineers.", "unbacked")))
    r = await client.get(f"/api/cv/{ready_cv.id}/docx")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "open_points_undecided"
    report = (await client.get(f"/api/cv/{ready_cv.id}/ats-report")).json()["report"]
    row = next(c for c in report["checks"] if c["id"] == "terminal-review")
    assert row["details_key"] == "terminal-review-open" and row["resolver"] == "decide_open"
    assert any(n.get("key") == "oracle.unbacked" for n in row["notes"])


# ---------------------------------------------------------------------------
# D-1: the signed html link is a download door
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["link", "bearer", "session"])
async def test_d1_html_is_held_on_a_link_or_bearer_and_readable_in_the_session(db, ready_cv, via):  # noqa: F811
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from applire.auth.deps_links import user_or_signed_link
    from applire.db.session import get_db
    from applire.routers.cv import router

    ready_cv.ats_report = {"checks": [dict(_OPEN_ROW)]}
    await db.commit()
    user = SimpleNamespace(id=ready_cv.user_id)

    async def _auth(request: Request):
        if via == "link":
            request.state.signed_link = True
        return user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[user_or_signed_link] = _auth
    headers = {"Authorization": "Bearer apl_x"} if via == "bearer" else {}
    with patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html>held</html>")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
            r = await ac.get(f"/api/cv/{ready_cv.id}/html", headers=headers)
    if via == "session":
        assert r.status_code == 200
    else:
        assert r.status_code == 409 and r.json()["detail"]["code"] == "open_points_undecided"


@pytest.mark.asyncio
async def test_d1_a_verified_signed_link_marks_the_request():
    from applire.auth import deps_links

    request = SimpleNamespace(
        path_params={"cv_id": str(uuid.uuid4())}, query_params={"exp": "1", "sig": "s", "uid": "u"},
        headers={}, state=SimpleNamespace(),
    )
    user = SimpleNamespace(id=uuid.uuid4())
    with patch.object(deps_links, "bearer_from_request", return_value=None), \
         patch.object(deps_links, "verify_document_link", new=AsyncMock(return_value=user)), \
         patch("applire.services.allowance.load_facts", new=AsyncMock()):
        assert await deps_links.user_or_signed_link(request, MagicMock(), MagicMock()) is user
    assert request.state.signed_link is True


# ---------------------------------------------------------------------------
# D-9: "Passt so" binds to the set the client read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d9_passt_so_with_a_stale_basis_is_refused(db, red_letter):  # noqa: F811
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from applire.auth import get_auth_provider
    from applire.auth.no_auth import NoAuthProvider
    from applire.db.session import get_db
    from applire.routers.review import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    current = review_signals.decision_basis(review_signals.open_points_check(red_letter))
    url = f"/api/cover-letter/{red_letter.id}/review/kept"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        for stale in ("deadbeefdeadbeef", None):
            body = {"finding_key": TERMINAL, "keep": True}
            if stale:
                body["basis"] = stale
            r = await ac.post(url, json=body)
            assert r.status_code == 409, r.text
            assert r.json()["detail"]["error"] == "open_points_changed"
            assert r.json()["detail"]["basis"] == current
        assert review_signals.open_points_pending(red_letter) is not None
        r = await ac.post(url, json={"finding_key": TERMINAL, "keep": True, "basis": current})
        assert r.status_code == 200, r.text
    await db.refresh(red_letter)
    assert review_signals.open_points_pending(red_letter) is None


@pytest.mark.asyncio
async def test_d9_the_agent_relays_the_basis_it_was_shown(db, red_letter):  # noqa: F811
    from mcp.shared.exceptions import McpError

    from applire.mcp.server import decide_open_points

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    with patch("applire.mcp.server.get_db", return_value=cm):
        with pytest.raises(McpError) as exc:
            await decide_open_points(cover_letter_id=str(red_letter.id), keep=True, basis="deadbeefdeadbeef")
        assert "changed since they were read" in str(exc.value)
    assert review_signals.open_points_pending(red_letter) is not None


def test_the_fingerprint_is_total_over_any_column_value():
    """A column value that is no JSON type (a mock, an object whose ``isoformat``
    returns another object) is fingerprinted by its text — never recursed into."""
    import datetime as _dt
    from unittest.mock import MagicMock

    from applire.services import audit_state

    rec = SimpleNamespace(tailored_data={"at": _dt.date(2026, 11, 23)}, content_snapshot=MagicMock(),
                          section_overrides=None)
    assert len(audit_state.fingerprint(rec)) == 64
    a = SimpleNamespace(tailored_data={"at": _dt.date(2026, 11, 23)}, content_snapshot=None, section_overrides=None)
    b = SimpleNamespace(tailored_data={"at": "2026-11-23"}, content_snapshot=None, section_overrides=None)
    assert audit_state.fingerprint(a) == audit_state.fingerprint(b)
