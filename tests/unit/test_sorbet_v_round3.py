# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sorbet invite-gate WP-V, round 3 — the adversarial re-probe of the vault
package (adv/vault "Re-probe 2026-11-24"; RULING MD-G15).

* NF-1 — a turn whose closed answer WROTE the vault (the replace chip, the
  leadership-kind chip, the size follow-up chip) says so on both doors:
  ``changes_applied`` and ``profile_updated_at`` (the UI's response and the
  agent's ``send_message`` result, driven through ``mcp.call_tool``).

Synthetic data only.
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.interview.scope_question import BULLET_REPLACE  # noqa: E402
from tests.support.mcp_door import mcp_signing_secret  # noqa: E402,F401 — autouse
from tests.support.profile_factory import make_master_profile, retire_live_profiles  # noqa: E402

HELD = "Fachliche Anleitung von zwei Werkstudierenden."
NEW = "Seit zwei Jahren zwei Werkstudierende fachlich angeleitet, inklusive Review ihrer Reports."
GOOD = "Fachliche Anleitung von zwei Werkstudierenden seit zwei Jahren, inklusive Review ihrer Reports."
SHIFT = "Führung einer Schicht mit 14 Mitarbeitenden in der Blechumformung."


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models  # noqa: F401 — every table

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _profile(responsibilities=None, team_size=None, extra=None):
    entry = {"id": "w-1", "company": "Schwarzwald Präzision GmbH", "role": "Controllerin",
             "start_date": "2019-02", "is_current": True, "team_size": team_size,
             "responsibilities": list(responsibilities or [HELD, "Monatsabschluss nach HGB.", NEW])}
    entry.update(extra or {})
    return {"personal_info": {"name": "Katrin Vogt", "email": "katrin@example.org"},
            "work_experience": [entry], "metadata": {}}


def _replace_cluster(n=1, held=HELD, new=NEW, proposal=GOOD):
    item = {"entry_id": "w-1", "field": "responsibilities", "held": held, "new": new,
            "proposal": proposal, "absorbs": True, "path": "reviewer"}
    return {"id": f"bullet-replace-{n}", "kind": BULLET_REPLACE, "entry": item,
            "label": f"bullet-replace-{n}", "gaps": [], "jd_skills": [], "jd_context": ""}


def _size_cluster(old=14, new=12):
    item = {"entry_id": "w-1", "company": "Schwarzwald Präzision GmbH", "role": "Controllerin",
            "text": SHIFT, "field": "responsibilities", "old": old, "new": new}
    return {"id": "size-bullet-1", "kind": "size_bullet", "entry": item, "entries": [item],
            "label": "size-bullet-1", "gaps": [], "jd_skills": [], "jd_context": ""}


_NEXT = {"id": "HGB", "label": "HGB", "gaps": ["HGB"], "jd_skills": [], "jd_context": ""}


def _state(job_id, profile_id, clusters):
    return {
        "mode": "targeted", "job_id": str(job_id), "gap_analysis_id": None,
        "profile_id": str(profile_id),
        "critical_gaps": [c["id"] for c in clusters],
        "gap_categories": {c["id"]: "C" for c in clusters},
        "gap_clusters_by_id": {c["id"]: c for c in clusters},
        "addressed_gaps": [], "current_gap_index": 0,
        "current_question": "Frage?", "messages": [{"role": "assistant", "content": "Frage?"}],
        "questions_asked": 1, "hard_ceiling": 12, "questions_per_gap": {},
        "skipped_gaps": [], "full_gaps": [],
    }


async def _seed(db, clusters, profile_json):
    from applire.models.job import JobAnalysis
    from applire.models.session import InterviewSession

    await retire_live_profiles(db)
    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex, raw_text="Leitung Controlling.", role_title="Leitung Controlling",
        required_skills=["HGB"], nice_to_have_skills=[], keywords=[], seniority_level="Senior",
        company_culture_signals=[], language_requirement="German", jd_language="de",
    )
    profile = make_master_profile(profile_json=copy.deepcopy(profile_json))
    db.add_all([job, profile])
    await db.flush()
    record = InterviewSession(
        job_analysis_id=job.id, gap_analysis_id=None, profile_id=profile.id, mode="targeted",
        status="active", state=_state(job.id, profile.id, clusters), hard_ceiling=12,
        questions_asked=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(record)
    await db.commit()
    return record, profile


def _unused_turn():
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    return InterviewTurnResult(profile_dict={}, changes=[], addressed=False)


def _patches(bridge):
    return (
        patch("applire.services.session.reconcile_interview_turn", new=bridge),
        patch("applire.services.session.question_generator_with_profile",
              new=AsyncMock(return_value={"question": "Weiter?", "choices": None})),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    )


def _provider():
    provider = MagicMock()
    provider.aparse_json = AsyncMock(return_value={"question": "Weiter?", "choices": None})
    return provider


async def _turn(db, record, answer, bridge=None):
    """One UI-door turn (``services.session.send_message``) → the response."""
    from applire.services.session import send_message

    bridge = bridge or AsyncMock(return_value=_unused_turn())
    p1, p2, p3 = _patches(bridge)
    with p1, p2, p3:
        return await send_message(record.id, answer, db, _provider())


async def _agent_turn(db, record, answer):
    """The same turn through the agent door: ``mcp.call_tool("send_message")``."""
    from applire.mcp.server import mcp

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    p1, p2, p3 = _patches(AsyncMock(return_value=_unused_turn()))
    with (
        p1, p2, p3,
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider", return_value=_provider()),
        patch("applire.mcp.server._current_user_id", AsyncMock(return_value=record.user_id)),
    ):
        out = await mcp.call_tool("send_message", {"session_id": str(record.id), "message": answer})
    blocks = out[0] if isinstance(out, tuple) else out
    return json.loads(blocks[0].text)


# ── NF-1: a turn that wrote the vault says so ───────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("chip", ["Ersetzen", "Replace", "„Ersetzen“"])
async def test_nf1_a_written_replace_reports_the_change(db, chip):
    record, profile = await _seed(db, [_replace_cluster(), _NEXT], _profile())
    out = await _turn(db, record, chip)
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["responsibilities"][0] == GOOD, "the replace was written"
    assert out.changes_applied is True
    assert out.profile_updated_at is not None
    assert out.no_write_receipt is None


@pytest.mark.asyncio
@pytest.mark.parametrize("chip", ["Beide behalten", "Keep both"])
async def test_nf1_keep_both_reports_no_change(db, chip):
    record, profile = await _seed(db, [_replace_cluster(), _NEXT], _profile())
    out = await _turn(db, record, chip)
    assert out.changes_applied is False
    assert out.profile_updated_at is None


@pytest.mark.asyncio
async def test_nf1_a_held_bullet_gone_meanwhile_reports_no_change(db):
    """The chip arrives after the person edited the held bullet away: the
    replace writes nothing, so the turn must not claim it did."""
    record, profile = await _seed(
        db, [_replace_cluster(), _NEXT], _profile(responsibilities=["Monatsabschluss nach HGB.", NEW]))
    out = await _turn(db, record, "Ersetzen")
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["responsibilities"] == ["Monatsabschluss nach HGB.", NEW]
    assert out.changes_applied is False
    assert out.profile_updated_at is None


@pytest.mark.asyncio
async def test_nf1_two_replaces_in_a_row_each_report_their_write(db):
    other_held, other_new = "Monatsabschluss nach HGB.", "Monatsabschluss nach HGB in fünf Arbeitstagen."
    other_good = "Monatsabschluss nach HGB in fünf Arbeitstagen."
    second = _replace_cluster(2, held=other_held, new=other_new, proposal=other_good)
    record, profile = await _seed(
        db, [_replace_cluster(), second, _NEXT],
        _profile(responsibilities=[HELD, other_held, NEW, other_new]))
    first = await _turn(db, record, "Ersetzen")
    again = await _turn(db, record, "Ersetzen")
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["responsibilities"] == [GOOD, other_good]
    assert first.changes_applied is True and again.changes_applied is True
    assert again.profile_updated_at is not None and again.profile_updated_at >= first.profile_updated_at


@pytest.mark.asyncio
async def test_nf1_the_agent_door_reports_the_replace(db):
    record, profile = await _seed(db, [_replace_cluster(), _NEXT], _profile())
    out = await _agent_turn(db, record, "Ersetzen")
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["responsibilities"][0] == GOOD
    assert out["changes_applied"] is True
    assert out["profile_updated_at"]


@pytest.mark.asyncio
async def test_nf1_the_agent_door_reports_keep_both_as_no_change(db):
    record, _ = await _seed(db, [_replace_cluster(), _NEXT], _profile())
    out = await _agent_turn(db, record, "Keep both")
    assert out["changes_applied"] is False
    assert out["profile_updated_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("chip", "written"), [("Auf 12 ändern", True), ("Change to 12", True)])
async def test_nf1_a_written_size_follow_up_reports_the_change(db, chip, written):
    from applire.services.profile.leadership_kind import leadership_kind_op
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.schemas.profile import MasterProfileData

    base = MasterProfileData.model_validate(_profile(responsibilities=[SHIFT], team_size=14))
    corrected = apply_ops(base, [leadership_kind_op("w-1", "disciplinary", answer="Disziplinarisch – 12 Personen",
                                                    team_size=12)], "interview").profile
    record, profile = await _seed(db, [_size_cluster(), _NEXT], corrected.model_dump(mode="json"))
    out = await _turn(db, record, chip)
    await db.refresh(profile)
    assert "12 Mitarbeitenden" in profile.profile_json["work_experience"][0]["responsibilities"][0]
    assert out.changes_applied is written
    assert (out.profile_updated_at is not None) is written


@pytest.mark.asyncio
async def test_nf1_the_agent_door_reports_a_written_size_follow_up(db):
    from applire.services.profile.leadership_kind import leadership_kind_op
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.schemas.profile import MasterProfileData

    base = MasterProfileData.model_validate(_profile(responsibilities=[SHIFT], team_size=14))
    corrected = apply_ops(base, [leadership_kind_op("w-1", "disciplinary", answer="Disziplinarisch – 12 Personen",
                                                    team_size=12)], "interview").profile
    record, _ = await _seed(db, [_size_cluster(), _NEXT], corrected.model_dump(mode="json"))
    out = await _agent_turn(db, record, "Auf 12 ändern")
    assert out["changes_applied"] is True


# the leadership-kind chip (harness adapted from test_sorbet_q8_kind_and_size.py)

_SCOPE = "Führungsspanne ~120 MA"


def _scope_row():
    return {
        "concept": _SCOPE, "surface_forms": [], "sources": ["required"], "fit_weight": 1.0,
        "status": "partial", "evidence": "JD bar …", "claimable": True, "narrative_backed": True,
        "bar": {"kind": "team_size", "level": "required", "value": 120.0, "value_max": None,
                "comparator": "approx",
                "quote": "Gesamtverantwortung für Produktion (ca. 120 Mitarbeitende, Mehrschichtbetrieb).",
                "candidate_values": [{"value": 2, "entry": "Controllerin @ Kanzlei Rhein"}],
                "cited_entry": None, "attested": None},
    }


def _bridge(addressed=False):
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult

    async def _run(db, *, profile_record, **_kwargs):
        return InterviewTurnResult(
            profile_dict=copy.deepcopy(profile_record.profile_json), changes=[],
            addressed=addressed, denial_recorded=False, denied_concepts=[],
        )

    return AsyncMock(side_effect=_run)


async def _kind_session(db, *, team_size):
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.schemas.session import SessionCreateRequest
    from applire.services.interview.scope_question import carve_scope_clusters
    from applire.services.session import create_session, send_message

    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex, raw_text="Leiter Operations. Ihr Profil: ca. 120 Mitarbeitende, MES.",
        role_title="Leiter Operations", required_skills=["MES"], nice_to_have_skills=[], keywords=[],
        seniority_level="Senior", company_culture_signals=[], language_requirement="Deutsch", jd_language="de",
    )
    await retire_live_profiles(db)
    profile = make_master_profile(profile_json={
        "personal_info": {"name": "Katrin Test", "email": "katrin@example.de"}, "skills": [],
        "work_experience": [{"id": "w1", "company": "Kanzlei Rhein", "role": "Controllerin",
                             "start_date": "2019-01", "team_size": team_size,
                             "responsibilities": ["Fachliche Anleitung von zwei Werkstudierenden."]}],
    })
    db.add_all([job, profile])
    await db.flush()
    db.add(GapAnalysis(
        job_analysis_id=job.id, profile_id=profile.id, match_score=0.5, critical_gaps=[], minor_gaps=[],
        strengths=[], keyword_gaps=[], category_a=[], category_b=[_SCOPE],
        category_c=["MES"], keyword_ledger=[_scope_row()],
        gap_clusters=[carve_scope_clusters([], [_scope_row()], [])[0]],
    ))
    await db.commit()
    provider = MagicMock()
    provider.acomplete = AsyncMock(side_effect=AssertionError("no LLM question call"))
    provider.aparse_json = AsyncMock(side_effect=AssertionError("no LLM question call"))
    created = await create_session(SessionCreateRequest(job_id=job.id, mode="targeted"), db, provider)
    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(False)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        first = await send_message(created.session_id, "Direkt 3.", db, provider)
    return created.session_id, first, provider, profile


@pytest.mark.asyncio
@pytest.mark.parametrize(("answer", "written"), [
    ("Fachliche Anleitung – 3 Personen", True),          # kind + corrected size
    ("Fachliche Anleitung – 2 Personen", True),          # kind, size confirmed
])
async def test_nf1_a_written_kind_chip_reports_the_change(db, answer, written):
    from applire.services.session import send_message

    sid, first, provider, profile = await _kind_session(db, team_size=2)
    assert first.choices and first.choices[0].startswith("Disziplinarisch"), first
    with (
        patch("applire.services.session.reconcile_interview_turn", new=_bridge(False)),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        out = await send_message(sid, answer, db, provider)
    await db.refresh(profile)
    assert profile.profile_json["work_experience"][0]["leadership_kind"] == "functional"
    assert out.changes_applied is written
    assert (out.profile_updated_at is not None) is written


# ── NF-5: a question-shaped echo is not the closed answer (V-1) ──────────────


@pytest.mark.parametrize(("message", "decision"), [
    ("Ersetzen?", None), ("Replace?", None), ("Keep both?", None), ("Beide behalten?", None),
    ("ersetzen ?", None), ("Ersetzen?!", None), ("„Ersetzen?“", None), ("Ersetzen…?", None),
    ("Ersetzen?.", None),
    # what V-1 / F-1 keep as the chip
    ("Ersetzen", "replace"), ("Ersetzen!", "replace"), ("Ersetzen.", "replace"), ("Replace …", "replace"),
    ("„Beide behalten“", "keep"), ("Keep both!", "keep"),
])
def test_nf5_a_question_mark_makes_it_a_question_not_the_chip(message, decision):
    from applire.services.interview.scope_question import bullet_replace_decision

    assert bullet_replace_decision(_replace_cluster(), message) == decision


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["Ersetzen?", "Replace?"])
async def test_nf5_a_question_shaped_echo_replaces_nothing_and_gets_the_one_reask(db, answer):
    from applire.services.interview.scope_question import BULLET_REPLACE_UNMATCHED_HINT

    async def _q(state, profile, provider, *_a, **kw):
        from applire.services import interview_graph as g

        current = state["gap_clusters_by_id"][state["critical_gaps"][state["current_gap_index"]]]
        if current.get("kind") == BULLET_REPLACE:
            return g.bullet_replace_question(current, kw.get("lang") or "de")
        return {"question": "Weiter?", "choices": None}

    record, profile = await _seed(db, [_replace_cluster(), _NEXT], _profile())
    nothing = AsyncMock(return_value=__import__(
        "applire.services.profile.reconcile.interview_bridge", fromlist=["x"]).InterviewTurnResult(
        profile_dict=_profile(), changes=[], addressed=False))
    with patch("applire.services.session._cluster_question", new=_q):
        out = await _turn(db, record, answer, bridge=nothing)
    await db.refresh(profile)
    await db.refresh(record)
    assert profile.profile_json["work_experience"][0]["responsibilities"] == [HELD, "Monatsabschluss nach HGB.", NEW]
    assert out.changes_applied is False
    assert out.question.startswith(BULLET_REPLACE_UNMATCHED_HINT["de"])
    nxt = record.state["gap_clusters_by_id"][record.state["critical_gaps"][record.state["current_gap_index"]]]
    assert nxt.get("kind") == BULLET_REPLACE and nxt.get("reask") is True


# ── F-6 residual: a link the vault holds as a company-name string ────────────
# Live vaults carry the legacy ref shape (rb: ``PP`` refs ``[<id>, "Weberit
# Automotive GmbH"]``, skill_enrichment's company-name provenance). Re-probe L1:
# stored ``["Rasselstein Umformtechnik GmbH"]``, the model restates
# ``evidence=["w", "r"]`` on a Weberit answer → a spurious ``skill_evidence``
# ask about the link the profile already holds.


def _guard_profile(**skill):
    from applire.schemas.profile import MasterProfileData, WorkEntry

    def w(i, c, r, **k):
        return WorkEntry(id=i, company=c, role=r, responsibilities=k.pop("b", ["x."]), **k)

    p = MasterProfileData(work_experience=[
        w("w", "Weberit Kunststofftechnik GmbH", "Produktionsleiter", start_date="2017-04", is_current=True),
        w("r", "Rasselstein Umformtechnik GmbH", "Fertigungsmeister / Schichtleiter",
          start_date="2011-08", end_date="2017-03"),
        w("r2", "Rasselstein Umformtechnik GmbH", "Industriemechaniker", start_date="2004-07", end_date="2011-07"),
    ])
    return p


def _guard(profile, ops, answer):
    from applire.services.profile.reconcile.attribution import enforce_attribution
    from applire.services.profile.reconcile.ops import RequestConfirmation

    out = enforce_attribution(ops, profile=profile, new_info={"gap": "g", "question": "Erzähl mehr.",
                                                               "answer": answer}, source="interview")
    asks = [(o.context or {}).get("flagged") for o in out if isinstance(o, RequestConfirmation)]
    kept = [o for o in out if not isinstance(o, RequestConfirmation)]
    return kept, asks


@pytest.mark.parametrize("stored", [
    "Rasselstein Umformtechnik GmbH", "Rasselstein Umformtechnik", "rasselstein umformtechnik gmbh",
])
@pytest.mark.parametrize("answer", [
    "Bei Weberit arbeite ich täglich mit SAP PP.",
    "At Weberit I work with SAP PP every day.",
])
def test_f6_a_link_stored_as_a_company_name_is_never_asked_about(stored, answer):
    from applire.schemas.profile import Skill
    from applire.services.profile.reconcile.ops import UpsertSkill

    p = _guard_profile()
    p.skills = [Skill(name="SAP PP", category="tool", experience_refs=[stored])]
    kept, asks = _guard(p, [UpsertSkill(name="SAP PP", category="tool", evidence=["w", "r"])], answer)
    assert asks == [], "the Rasselstein link is the profile's own"
    [op] = kept
    assert op.evidence == ["w", "r"]


def test_f6_a_story_link_stored_as_a_company_name_is_never_asked_about():
    from applire.schemas.profile import SignatureStory
    from applire.services.profile.reconcile.ops import UpsertStory

    p = _guard_profile()
    p.signature_stories = [SignatureStory(title="Rüstzeiten halbiert", challenge="c", mechanism="m",
                                          outcome="o", experience_refs=["Rasselstein Umformtechnik GmbH"])]
    kept, asks = _guard(p, [UpsertStory(title="Rüstzeiten halbiert", challenge="c", mechanism="m", outcome="o", evidence=["w", "r"])],
                        "Bei Weberit habe ich die Rüstzeiten halbiert.")
    assert asks == []


def test_f6_a_name_string_of_another_employer_still_holds_a_wrong_link():
    """The stored string names Weberit; the model links the Rasselstein role on
    a Weberit answer: that link is new and points at another employer."""
    from applire.schemas.profile import Skill
    from applire.services.profile.reconcile.ops import UpsertSkill

    p = _guard_profile()
    p.skills = [Skill(name="SAP PP", category="tool", experience_refs=["Weberit Kunststofftechnik GmbH"])]
    kept, asks = _guard(p, [UpsertSkill(name="SAP PP", category="tool", evidence=["w", "r"])],
                        "Bei Weberit arbeite ich täglich mit SAP PP.")
    assert len(asks) == 1 and kept[0].evidence == ["w"]


def test_f6_an_id_link_to_another_role_of_the_same_employer_is_still_asked():
    """Re-probe L2 stays as it was: the profile holds ``r``; ``r2`` is a new link."""
    from applire.schemas.profile import Skill
    from applire.services.profile.reconcile.ops import UpsertSkill

    p = _guard_profile()
    p.skills = [Skill(name="SAP PP", category="tool", experience_refs=["r"])]
    kept, asks = _guard(p, [UpsertSkill(name="SAP PP", category="tool", evidence=["w", "r2"])],
                        "Bei Weberit arbeite ich täglich mit SAP PP.")
    assert len(asks) == 1 and kept[0].evidence == ["w"]
