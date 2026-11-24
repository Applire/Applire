# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WP-L (RULING L-1, founder 2026-11-23 — the same ruling as C-1): a letter whose
delivered check still flags statements is held like a CV with open points.

ONE pattern for both documents: the letter's open statements (the counted group-1 rows
of its LIVE reports, i.e. ``stop_and_fix``) join the ``terminal-review-open`` check the
CV card and the CV hold already read (``review_signals.open_points_check``). PDF, Word
and the agent's download answer 409 ``open_points_undecided`` until each point is fixed
(the live report stops listing it) or the person answers "Passt so" (a ``kept``
decision bound to THESE points; a different set voids it). Personas are synthetic."""
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services import review_signals  # noqa: E402
from applire.services.review_signals import TERMINAL_REVIEW_KEY  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from test_cv_docx_endpoint import db  # noqa: E402,F401

MISPLACED = "At Lindenau Fördertechnik I cut changeover time by 30 percent."
UNBACKED = "I led a team of 120 people."
TRUE_LINE = "I mentor two junior engineers."


def _claim(text, verdict, location="body[1]"):
    return {
        "claim": {"text": text, "location": location, "kind": "sentence"},
        "verdict": {"verdict": verdict, "checker": "grounding"},
    }


def _truth(*claims):
    counts = {k: 0 for k in ("grounded", "inflated", "misattributed", "unbacked", "unverifiable")}
    for c in claims:
        counts[c["verdict"]["verdict"]] += 1
    return {"document_kind": "cover_letter", "claims": list(claims), "counts": counts}


def _ats(terminal_key="terminal-review-clean", terminal_status="pass", present_unsupported=(),
         terminal_hash=None, terminal_count=0, notes=None):
    return {
        "version": 1, "document": "cover_letter", "passed": 2, "failed": 0, "not_applicable": 0,
        "checks": [
            {"id": "contact-name", "status": "pass"},
            {"id": "terminal-review", "status": terminal_status, "details": "x",
             "details_key": terminal_key, "details_params": {"count": terminal_count},
             "notes": notes or [], "open_points_hash": terminal_hash},
        ],
        "keywords": {"present": [], "missing": [], "present_unsupported": list(present_unsupported)},
    }


def _kept(basis):
    return {"walked_at": None, "decisions": [{
        "finding_key": TERMINAL_REVIEW_KEY, "label": "terminal-review", "action": "kept",
        "basis": basis, "at": "2026-11-23T10:00:00+00:00",
    }]}


def _letter(truth, ats=None, state=None, origin="pipeline"):
    return SimpleNamespace(letter_data={}, origin=origin, truthfulness_report=truth,
                           ats_report=ats if ats is not None else _ats(), review_state=state)


RED = _truth(_claim(MISPLACED, "misattributed"), _claim(UNBACKED, "unbacked"), _claim(TRUE_LINE, "grounded"))


# ── the predicate (pure) ─────────────────────────────────────────────────────


def test_a_red_letter_has_one_open_point_per_flagged_statement():
    check = review_signals.open_points_check(_letter(RED))
    assert check["details_key"] == "terminal-review-open" and check["status"] == "fail"
    assert check["details_params"] == {"count": 2}
    assert check["resolver"] == "decide_open" and check["delivery_class"] == "A"
    assert [(n["key"], n["subjects"]) for n in check["notes"]] == [
        ("oracle.misattributed", [MISPLACED]), ("oracle.unbacked", [UNBACKED]),
    ]
    assert review_signals.open_points_pending(_letter(RED)) is not None


def test_a_letter_whose_check_passes_is_not_held():
    clean = _truth(_claim(TRUE_LINE, "grounded"))
    assert review_signals.open_points_check(_letter(clean)) is None
    assert review_signals.open_points_pending(_letter(clean)) is None


def test_an_unavailable_truth_report_is_not_a_hold():
    """Unknown is the U row's business (ADR-081 cl. 9), never a silent hold or pass here."""
    assert review_signals.open_points_check(_letter(None)) is None


def test_an_agent_authored_letter_is_never_held():
    """ADR-054: the BYOI letter ran no loop; its author reads the reports it got back."""
    assert review_signals.open_points_check(_letter(RED, origin="agent")) is None


def test_an_ats_posting_term_the_profile_does_not_back_is_a_point_too():
    """Delivery-run case 1 (held on the ATS 'payments platform' row, no Oracle claim)."""
    ats = _ats(present_unsupported=["payments platform"])
    check = review_signals.open_points_check(_letter(_truth(_claim(TRUE_LINE, "grounded")), ats))
    assert [(n["key"], n["subjects"]) for n in check["notes"]] == [("oracle.posting_term", ["payments platform"])]


def test_an_employer_sentence_row_is_listed_but_never_held():
    """#803 (T-2 = B): the panel lists it uncounted, so the hold does not count it either."""
    ats = _ats(present_unsupported=["Kubernetes"])
    ats["keywords"]["present_unsupported_employer_sentence"] = ["Kubernetes"]
    check = review_signals.open_points_check(_letter(RED, ats))
    assert check["details_params"] == {"count": 2}
    assert "Kubernetes" not in [n["subjects"][0] for n in check["notes"]]


def test_a_cv_is_held_on_its_truth_report_like_the_letter():
    """MD-G9 (adv/holds D-8, "one pattern"): the CV joins its delivered truth report's
    open statements exactly as the letter does; with a clean report it reads its own
    persisted row (hash unchanged, so an earlier "Passt so" still holds)."""
    report = {"checks": [{"id": "terminal-review", "status": "fail", "details_key": "terminal-review-open",
                          "details_params": {"count": 2}, "open_points_hash": "a" * 16}]}
    cv = SimpleNamespace(ats_report=report, review_state=None, truthfulness_report=RED, origin="pipeline")
    joined = review_signals.open_points_check(cv)
    assert joined["details_key"] == "terminal-review-open" and joined["details_params"]["count"] == 4
    assert joined["open_points_hash"] != "a" * 16
    clean = SimpleNamespace(ats_report=report, review_state=None,
                            truthfulness_report=_truth(_claim(TRUE_LINE, "grounded")), origin="pipeline")
    assert review_signals.open_points_check(clean) == report["checks"][0]
    agent = SimpleNamespace(ats_report=report, review_state=None, truthfulness_report=RED, origin="agent")
    assert review_signals.open_points_check(agent) is None


def test_the_terminal_reviews_own_open_points_join_the_statements():
    notes = [{"kind": "finding", "text": "The closing repeats the opening.", "plain": None}]
    ats = _ats("terminal-review-open", "fail", terminal_hash="b" * 16, terminal_count=1, notes=notes)
    check = review_signals.open_points_check(_letter(RED, ats))
    assert check["details_params"] == {"count": 3}
    assert check["notes"][0]["kind"] == "finding" and len(check["notes"]) == 3
    # and the terminal points alone still hold a letter whose statements pass
    alone = review_signals.open_points_check(_letter(_truth(_claim(TRUE_LINE, "grounded")), ats))
    assert alone["open_points_hash"] == "b" * 16


def test_passt_so_releases_and_a_different_set_of_points_voids_it():
    h = review_signals.open_points_check(_letter(RED))["open_points_hash"]
    assert review_signals.open_points_pending(_letter(RED, state=_kept(h))) is None
    # a new statement is flagged after an edit: the earlier answer does not cover it
    redder = _truth(*RED["claims"], _claim("I wrote the company's ISO 9001 manual.", "unbacked"))
    assert review_signals.open_points_pending(_letter(redder, state=_kept(h))) is not None
    # one point fixed through a review action: the set changed, the answer is asked again
    fewer = _truth(_claim(UNBACKED, "unbacked"), _claim(TRUE_LINE, "grounded"))
    assert review_signals.open_points_pending(_letter(fewer, state=_kept(h))) is not None


def test_each_point_fixed_ends_the_hold_without_a_decision():
    fixed = _truth(_claim(TRUE_LINE, "grounded"), _claim(MISPLACED.replace("Lindenau", "Talwerk"), "grounded"))
    assert review_signals.open_points_pending(_letter(fixed)) is None


def test_the_hash_does_not_depend_on_the_order_of_the_points():
    a = review_signals.open_points_check(_letter(RED))["open_points_hash"]
    swapped = _truth(*reversed(RED["claims"]))
    assert review_signals.open_points_check(_letter(swapped))["open_points_hash"] == a


def test_the_served_report_carries_the_joined_row_and_consistent_counts():
    served = review_signals.with_open_points_check(_ats(), _letter(RED))
    rows = [c for c in served["checks"] if c["id"] == "terminal-review"]
    assert len(rows) == 1 and rows[0]["details_key"] == "terminal-review-open"
    assert (served["passed"], served["failed"]) == (1, 1)
    assert [c["id"] for c in served["checks"]] == ["contact-name", "terminal-review"]
    clean = _letter(_truth(_claim(TRUE_LINE, "grounded")))
    assert review_signals.with_open_points_check(clean.ats_report, clean) is clean.ats_report


# ── the doors (real ASGI stack + the agent tools) ───────────────────────────


@pytest_asyncio.fixture
async def client(db):  # noqa: F811
    from applire.auth import get_auth_provider
    from applire.auth.no_auth import NoAuthProvider
    from applire.db.session import get_db
    from applire.routers.cover_letter import router as cl_router

    app = FastAPI()
    app.include_router(cl_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_auth_provider] = lambda: NoAuthProvider()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def red_letter(db):  # noqa: F811
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from tests.support.profile_factory import make_master_profile

    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash=str(uuid.uuid4()), raw_text="x", role_title="Produktionsleiter",
        company_name="Beispielwerk", required_skills=[], nice_to_have_skills=[], keywords=[],
        seniority_level="senior", company_culture_signals=[], language_requirement="de",
    )
    profile = make_master_profile(id=uuid.uuid4(), profile_json={"personal_info": {"name": "Jonas Beispiel"}})
    db.add_all([job, profile])
    await db.commit()
    cl = GeneratedCoverLetter(
        id=uuid.uuid4(), job_analysis_id=job.id, profile_id=profile.id, template="classic_german",
        status="ready", document_language="de", pre_gen_inputs={},
        letter_data={"body": {"paragraphs": ["Sehr geehrte Damen und Herren,", MISPLACED, UNBACKED,
                                             "Mit freundlichen Grüßen"]},
                     "signature": {"name": "Jonas Beispiel"}},
        ats_report=_ats(), truthfulness_report=RED,
    )
    db.add(cl)
    await db.commit()
    return cl


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _held_basis(cl):
    return review_signals.open_points_check(cl)["open_points_hash"]


@pytest.mark.asyncio
async def test_word_export_is_409_until_the_person_keeps_the_points(client, db, red_letter):  # noqa: F811
    r = await client.get(f"/api/cover-letter/{red_letter.id}/docx")
    assert r.status_code == 409
    assert r.json()["detail"] == {"code": "open_points_undecided", "count": 2}

    red_letter.review_state = _kept(_held_basis(red_letter))
    await db.commit()
    r = await client.get(f"/api/cover-letter/{red_letter.id}/docx")
    assert r.status_code == 200 and r.content[:2] == b"PK"


@pytest.mark.asyncio
async def test_pdf_is_held_before_any_render(client, db, red_letter):  # noqa: F811
    render = AsyncMock(return_value=b"%PDF-1.7")
    with patch("applire.services.cover_letter_pdf.render_pdf", new=render):
        r = await client.get(f"/api/cover-letter/{red_letter.id}/pdf")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "open_points_undecided"
        assert render.await_count == 0
        red_letter.review_state = _kept(_held_basis(red_letter))
        await db.commit()
        r = await client.get(f"/api/cover-letter/{red_letter.id}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


@pytest.mark.asyncio
async def test_fixing_every_point_releases_the_download(client, db, red_letter):  # noqa: F811
    """What a review action's re-audit leaves behind: the live report no longer lists them."""
    red_letter.truthfulness_report = _truth(_claim(TRUE_LINE, "grounded"))
    await db.commit()
    assert (await client.get(f"/api/cover-letter/{red_letter.id}/docx")).status_code == 200


@pytest.mark.asyncio
async def test_the_ats_report_serves_the_card_row_to_both_doors(client, red_letter):  # noqa: F811
    r = await client.get(f"/api/cover-letter/{red_letter.id}/ats-report")
    body = r.json()
    row = next(c for c in body["report"]["checks"] if c["id"] == "terminal-review")
    assert row["details_key"] == "terminal-review-open" and row["resolver"] == "decide_open"
    assert row["open_points_hash"] == _held_basis(red_letter)
    assert body["truthfulness"]["stop_and_fix"] is True


@pytest.mark.asyncio
async def test_passt_so_through_the_review_door_binds_to_these_points(db, red_letter):  # noqa: F811
    out = await review_signals.keep("cover_letter", red_letter.id, TERMINAL_REVIEW_KEY, db, keep=True)
    decision = next(d for d in out.record.review_state["decisions"] if d["finding_key"] == TERMINAL_REVIEW_KEY)
    assert decision["basis"] == _held_basis(red_letter)
    assert review_signals.open_points_pending(out.record) is None
    out = await review_signals.keep("cover_letter", red_letter.id, TERMINAL_REVIEW_KEY, db, keep=False)
    assert review_signals.open_points_pending(out.record) is not None  # undo kept


@pytest.mark.asyncio
async def test_the_agent_status_names_the_points_and_decide_releases_then_withdraws(
    client, db, red_letter,  # noqa: F811
):
    from applire.mcp.server import decide_open_points, get_cover_letter_status

    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)), \
         patch("applire.auth.links.sign_document_url", side_effect=lambda k, i, u, v: v):
        status = await get_cover_letter_status(str(red_letter.id))
        basis = _held_basis(red_letter)
        assert status["open_points"] == {"count": 2, "decided": False, "basis": basis}
        # MD-G9 (D-1): the signed html is a download door — not handed out while held
        assert "html_url" not in status
        out = await decide_open_points(cover_letter_id=str(red_letter.id), keep=True, basis=basis)
        assert out == {"cover_letter_id": str(red_letter.id),
                       "open_points": {"count": 2, "decided": True, "basis": basis}}
        assert (await client.get(f"/api/cover-letter/{red_letter.id}/docx")).status_code == 200
        assert "html_url" in await get_cover_letter_status(str(red_letter.id))
        out = await decide_open_points(cover_letter_id=str(red_letter.id), keep=False)
        assert out["open_points"] == {"count": 2, "decided": False, "basis": basis}
    assert (await client.get(f"/api/cover-letter/{red_letter.id}/docx")).status_code == 409


@pytest.mark.asyncio
async def test_decide_refuses_a_clean_letter_and_needs_exactly_one_id(db, red_letter):  # noqa: F811
    from mcp.shared.exceptions import McpError

    from applire.mcp.server import decide_open_points, get_cover_letter_status

    red_letter.truthfulness_report = _truth(_claim(TRUE_LINE, "grounded"))
    await db.commit()
    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)), \
         patch("applire.auth.links.sign_document_url", side_effect=lambda k, i, u, v: v):
        assert (await get_cover_letter_status(str(red_letter.id)))["open_points"] is None
        with pytest.raises(McpError) as exc:
            await decide_open_points(cover_letter_id=str(red_letter.id), keep=True)
        assert "get_cover_letter_status.open_points" in str(exc.value)
        for kwargs in ({}, {"cv_id": str(uuid.uuid4()), "cover_letter_id": str(red_letter.id)}):
            with pytest.raises(McpError) as exc:
                await decide_open_points(**kwargs)
            assert "exactly one" in str(exc.value)
