# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""RULING B-1 (Hazelnut build 5, U-7) — "Ist das erreicht?" for a current role's
goal/task-worded bullet: candidate selection (code), the judgement (the model,
persisted once at import end), the hub thread, and the answer endpoint."""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.profile import MasterProfileData
from applire.services.profile.aspiration import (
    aspiration_key,
    aspiration_verdict_ops,
    open_aspirations,
    unjudged_bullets,
)
from applire.services.profile.health import assess_health
from tests.support.owners import HARNESS_USER_ID
from tests.support.profile_factory import make_master_profile

GOAL = "Aufbau einer konzernweiten Risiko-Governance"
TARGET = "The rollout targets a 30% shorter month-end close."
DUTY = "Verantwortung für das Konzerncontrolling"
PAST = "Built the reporting cube for the CFO office."


def _work(i, end_date=None, **kw):
    return {"id": i, "company": "Talwerk GmbH", "role": "Head of Controlling",
            "start_date": "2022-01", "end_date": end_date, **kw}


def _profile(metadata=None, **kw) -> MasterProfileData:
    blob = {
        "personal_info": {"name": "Lena Brandt"},
        "work_experience": [
            _work("cur", responsibilities=[GOAL, DUTY], achievements=[TARGET]),
            _work("old", end_date="2021-12", responsibilities=["Establish a shared-service centre"]),
        ],
        **kw,
    }
    if metadata:
        blob["metadata"] = metadata
    return MasterProfileData.model_validate(blob)


def _verdicts(*flagged, entry="cur", texts=(GOAL, DUTY, TARGET)):
    return [{"entry_id": entry, "key": aspiration_key(t), "text": t, "aspiration": t in flagged}
            for t in texts]


class _Judge:
    """A provider double: answers the aspiration prompt with the bullet numbers
    of the given texts, as the model would."""

    def __init__(self, flagged=(), fail=False):
        self.flagged, self.fail, self.calls = set(flagged), fail, []

    async def aparse_json(self, prompt, system=None, **kw):
        self.calls.append(prompt)
        if self.fail:
            raise RuntimeError("provider down")
        lines = [ln for ln in prompt.splitlines() if ln[:1].isdigit()]
        return {"items": [int(ln.split(":", 1)[0]) for ln in lines
                          if ln.split(": ", 1)[1] in self.flagged]}


# ── selection: code; judgement: the model ────────────────────────────────────


def test_only_current_roles_are_candidates():
    texts = [t for _, _, _, t in unjudged_bullets(_profile())]
    assert texts == [GOAL, DUTY, TARGET]  # the ended role's "Establish …" is never asked


def test_a_judged_bullet_is_not_judged_again():
    p = _profile(metadata={"aspiration_verdicts": _verdicts(GOAL)})
    assert unjudged_bullets(p) == []


@pytest.mark.asyncio
async def test_verdict_op_keeps_negatives_and_the_models_choice():
    judge = _Judge(flagged={GOAL, TARGET})
    (op,) = await aspiration_verdict_ops(_profile(), judge)
    assert len(judge.calls) == 1
    assert {(v.text, v.aspiration) for v in op.verdicts} == {(GOAL, True), (DUTY, False), (TARGET, True)}


@pytest.mark.asyncio
async def test_a_judge_failure_asks_nothing_and_never_raises():
    assert await aspiration_verdict_ops(_profile(), _Judge(fail=True)) == []


@pytest.mark.asyncio
async def test_no_candidates_no_call():
    judge = _Judge()
    p = _profile(metadata={"aspiration_verdicts": _verdicts()})
    assert await aspiration_verdict_ops(p, judge) == [] and judge.calls == []


def test_the_code_keeps_no_marker_list():
    """ADR-062: the wording judgement is the model's. A goal-worded bullet the
    model did not flag is never asked — no code list second-guesses it."""
    p = _profile(metadata={"aspiration_verdicts": _verdicts()})  # model flagged nothing
    assert open_aspirations(p) == []


# ── the hub thread ────────────────────────────────────────────────────────────


def test_hub_asks_each_flagged_bullet_once():
    p = _profile(metadata={"aspiration_verdicts": _verdicts(GOAL, TARGET)})
    issues = [i for i in assess_health(p).issues if i.thread == "aspiration"]
    assert [i.aspiration_text for i in issues] == [GOAL, TARGET]
    for i in issues:
        assert (i.delivery_class, i.resolver) == ("A", "ask_achieved")
        assert i.entry_id == "cur" and i.id == f"aspiration:cur:{aspiration_key(i.aspiration_text)}"


def test_a_bullet_edited_away_or_a_role_ended_is_not_asked():
    p = _profile(metadata={"aspiration_verdicts": _verdicts(GOAL, TARGET)})
    p.work_experience[0].responsibilities = [DUTY]
    assert [i.aspiration_text for i in assess_health(p).issues if i.thread == "aspiration"] == [TARGET]
    p.work_experience[0].end_date = "2026-09"
    assert [i for i in assess_health(p).issues if i.thread == "aspiration"] == []


def test_bookkeeping_never_reaches_a_prompt_but_the_stance_does():
    from applire.services.prompt_view import prompt_profile_view

    p = _profile(metadata={"aspiration_verdicts": _verdicts(GOAL)})
    p.work_experience[0].not_yet_reached = [GOAL]
    view = prompt_profile_view(p.model_dump(mode="json"))
    assert "aspiration_verdicts" not in (view.get("metadata") or {})
    assert view["work_experience"][0]["not_yet_reached"] == [GOAL]


# ── the answer endpoint ──────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session():
    import applire.models  # noqa: F401
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session):
    from unittest.mock import AsyncMock, MagicMock

    from applire.auth import get_auth_provider
    from applire.db.session import get_db
    from applire.routers.profile import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db_session
    auth = MagicMock()
    auth.get_current_user = AsyncMock(return_value=MagicMock(id=HARNESS_USER_ID))
    app.dependency_overrides[get_auth_provider] = lambda: auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _seed(db_session):
    p = _profile(metadata={"aspiration_verdicts": _verdicts(GOAL, TARGET)})
    db_session.add(make_master_profile(profile_json=p.model_dump(mode="json")))
    await db_session.commit()


async def _open(client):
    issues = (await client.get("/api/profile/health")).json()["issues"]
    return {i["aspiration_text"]: i["id"] for i in issues if i["thread"] == "aspiration"}


@pytest.mark.asyncio
async def test_ja_writes_the_outcome_in_the_candidates_words(client, db_session):
    await _seed(db_session)
    ids = await _open(client)
    outcome = "Risiko-Governance in 12 Gesellschaften eingeführt"
    resp = await client.post("/api/profile/aspirations/answer",
                             json={"issue_id": ids[GOAL], "achieved": True, "outcome": outcome})
    assert resp.status_code == 200
    entry = resp.json()["profile"]["work_experience"][0]
    assert entry["achievements"] == [TARGET, outcome]
    assert GOAL in entry["responsibilities"] and entry["not_yet_reached"] == []
    last = resp.json()["profile"]["metadata"]["enrichment_history"][-1]
    assert last["source"] == "manual_edit"
    assert [c["rationale_key"] for c in last["changes"]] == ["aspiration_achieved"]
    assert set(await _open(client)) == {TARGET}  # asked once


@pytest.mark.asyncio
async def test_nein_keeps_it_as_a_goal(client, db_session):
    await _seed(db_session)
    ids = await _open(client)
    resp = await client.post("/api/profile/aspirations/answer",
                             json={"issue_id": ids[TARGET], "achieved": False})
    assert resp.status_code == 200
    entry = resp.json()["profile"]["work_experience"][0]
    assert entry["not_yet_reached"] == [TARGET] and entry["achievements"] == [TARGET]
    last = resp.json()["profile"]["metadata"]["enrichment_history"][-1]
    assert [c["rationale_key"] for c in last["changes"]] == ["aspiration_goal"]
    assert set(await _open(client)) == {GOAL}


@pytest.mark.asyncio
async def test_refusals_404_409_422(client, db_session):
    await _seed(db_session)
    ids = await _open(client)
    post = lambda body: client.post("/api/profile/aspirations/answer", json=body)  # noqa: E731
    assert (await post({"issue_id": ids[GOAL], "achieved": True})).status_code == 422
    assert (await post({"issue_id": ids[GOAL], "achieved": True, "outcome": "  "})).status_code == 422
    assert (await post({"issue_id": "aspiration:cur:000000000000", "achieved": False})).status_code == 404
    assert (await post({"issue_id": "not_applied:x", "achieved": False})).status_code == 404
    dup_key = aspiration_key(DUTY)  # judged, but not flagged → never an open issue
    assert (await post({"issue_id": f"aspiration:cur:{dup_key}", "achieved": False})).status_code == 404
    assert (await post({"issue_id": ids[GOAL], "achieved": False})).status_code == 200
    assert (await post({"issue_id": ids[GOAL], "achieved": False})).status_code == 409


@pytest.mark.asyncio
async def test_an_answer_does_not_hide_the_not_applied_question(client, db_session):
    """The trail head rule (V-6) reads the newest record; a hub answer is
    transparent, like an adoption — answering one question may not make
    another one vanish."""
    from datetime import datetime, timezone

    from applire.schemas.profile import EnrichmentRecord, ImportNotApplied

    p = _profile(metadata={"aspiration_verdicts": _verdicts(GOAL)})
    p.metadata.enrichment_history.append(EnrichmentRecord(
        timestamp=datetime.now(timezone.utc), source="cv_upload",
        not_applied=[ImportNotApplied(section="education", label="Hochschule Nord / M.Sc.",
                                      reason="no_op_carried_entry")]))
    db_session.add(make_master_profile(profile_json=p.model_dump(mode="json")))
    await db_session.commit()
    ids = await _open(client)
    await client.post("/api/profile/aspirations/answer", json={"issue_id": ids[GOAL], "achieved": False})
    issues = (await client.get("/api/profile/health")).json()["issues"]
    assert any(i["thread"] == "not_applied" for i in issues)


# ── A1-3 gap ruling: current PROJECTS are asked too ───────────────────────────

P_GOAL = "Establish a model-risk review board for all AI use cases"


def _with_projects(*projects, metadata=None):
    p = _profile(metadata=metadata)
    blob = p.model_dump(mode="json")
    blob["projects"] = list(projects)
    return MasterProfileData.model_validate(blob)


def _proj(i, **kw):
    return {"id": i, "name": f"Projekt {i}", "responsibilities": [f"{P_GOAL} ({i})"], **kw}


def test_current_projects_are_candidates_and_ended_ones_are_not():
    p = _with_projects(
        _proj("ongoing", start_date="2024"),
        _proj("flagged", is_current=True, end_date="2023"),
        _proj("by_id", associated_experience="cur"),
        _proj("by_company", associated_experience=" talwerk  GmbH "),
        _proj("ended", start_date="2020", end_date="2021", associated_experience="cur"),
        _proj("old_link", associated_experience="old"),
        _proj("standalone"),
    )
    got = {(sec, eid) for sec, eid, _, _ in unjudged_bullets(p) if sec == "projects"}
    assert got == {("projects", i) for i in ("ongoing", "flagged", "by_id", "by_company")}


@pytest.mark.asyncio
async def test_a_project_bullet_is_judged_asked_and_answered_on_the_project(client, db_session):
    text = f"{P_GOAL} (ongoing)"
    judge = _Judge(flagged={text})
    p = _with_projects(_proj("ongoing", start_date="2024"),
                       metadata={"aspiration_verdicts": _verdicts()})
    (op,) = await aspiration_verdict_ops(p, judge)
    assert [(v.section, v.entry_id, v.aspiration) for v in op.verdicts] == [("projects", "ongoing", True)]

    blob = p.model_dump(mode="json")
    blob["metadata"]["aspiration_verdicts"] += [v.model_dump() for v in op.verdicts]
    db_session.add(make_master_profile(profile_json=blob))
    await db_session.commit()
    issues = [i for i in (await client.get("/api/profile/health")).json()["issues"]
              if i["thread"] == "aspiration"]
    assert [(i["section"], i["entry_id"], i["aspiration_text"]) for i in issues] == [
        ("projects", "ongoing", text)]
    resp = await client.post("/api/profile/aspirations/answer",
                             json={"issue_id": issues[0]["id"], "achieved": False})
    assert resp.status_code == 200
    body = resp.json()["profile"]
    assert body["projects"][0]["not_yet_reached"] == [text]
    assert all(not w.get("not_yet_reached") for w in body["work_experience"])
    assert body["metadata"]["enrichment_history"][-1]["changes"][0]["section"] == "projects"


def test_project_stance_reaches_the_prompt_view():
    from applire.services.prompt_view import prompt_profile_view

    p = _with_projects(_proj("ongoing", start_date="2024", not_yet_reached=[P_GOAL]))
    assert prompt_profile_view(p.model_dump(mode="json"))["projects"][0]["not_yet_reached"] == [P_GOAL]
