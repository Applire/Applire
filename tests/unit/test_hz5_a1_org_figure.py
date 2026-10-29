# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FIG-2 (founder ruling, Hazelnut build 5): an organisation-scale figure on a
bullet (company revenue, headcount, market size, funding) is the candidate's
decision, asked once in the profile hub. Selection is code (a figure-carrying
bullet, a FACT); the judgement is the model's (one call at import end,
persisted); the answer is the candidate's (``OrgFigureDecision`` on every entry
carrying the figure, relation verbatim).

Judge bench (gpt-6-luna, thinking off, n = 5): the founder vaults' revenue
bullets flagged 20/20 (two tellings per vault, one canonical figure); their
other figure bullets (own team, volumes, targets) 0 flagged; synthetic
own-result controls 0/30; held-out organisation figures 20/20.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.prompts.profile_decisions import ORG_FIGURE_SYSTEM
from applire.schemas.profile import MasterProfileData
from applire.services.profile.aspiration import aspiration_key
from applire.services.profile.health import assess_health
from applire.services.profile.org_figure import (
    figure_key,
    open_org_figures,
    org_figure_verdict_ops,
    unjudged_bullets,
)
from tests.support.owners import HARNESS_USER_ID
from tests.support.profile_factory import make_master_profile

ROLE_B = "Built the order platform for a €2.1 billion retailer, live in 4 countries."
PROJ_B = "Delivered the shop rollout behind the retailer's 2,1 Mrd. € Jahresumsatz."
OWN_B = "Reduced cost by €2M through vendor consolidation."
TEAM_B = "Led a team of 12 engineers."
PLAIN = "Owned the release process."


def _profile(metadata=None, **kw) -> MasterProfileData:
    blob = {
        "personal_info": {"name": "Jonas Weber"},
        "work_experience": [{
            "id": "w1", "company": "Nordlicht Handel GmbH", "role": "Lead Architect",
            "start_date": "2019-01", "end_date": "2023-06",
            "responsibilities": [TEAM_B, PLAIN], "achievements": [ROLE_B, OWN_B],
        }],
        "projects": [{"id": "p1", "name": "Shop Rollout", "associated_experience": "w1",
                      "achievements": [PROJ_B]}],
        **kw,
    }
    if metadata:
        blob["metadata"] = metadata
    return MasterProfileData.model_validate(blob)


def _verdict(entry, text, figure=None, section="work_experience"):
    return {"section": section, "entry_id": entry, "key": aspiration_key(text), "text": text,
            "figure": figure, "figure_key": figure_key(figure) if figure else None}


VERDICTS = [
    _verdict("w1", TEAM_B), _verdict("w1", OWN_B),
    _verdict("w1", ROLE_B, "€2.1 billion retailer"),
    _verdict("p1", PROJ_B, "2,1 Mrd. € Jahresumsatz", section="projects"),
]


class _Judge:
    def __init__(self, figures=None, fail=False):
        self.figures, self.fail, self.calls = figures or {}, fail, []

    async def aparse_json(self, prompt, system=None, **kw):
        self.calls.append(prompt)
        if self.fail:
            raise RuntimeError("provider down")
        items = []
        for ln in prompt.splitlines():
            if ln[:1].isdigit() and ": " in ln:
                n, text = ln.split(": ", 1)
                if text in self.figures:
                    items.append({"n": int(n), "figure": self.figures[text]})
        return {"items": items}


# ── selection (code) and judgement (model) ───────────────────────────────────


def test_only_figure_carrying_bullets_are_candidates():
    texts = [t for *_x, t in unjudged_bullets(_profile())]
    assert set(texts) == {TEAM_B, ROLE_B, OWN_B, PROJ_B}  # PLAIN carries no figure


@pytest.mark.asyncio
async def test_verdicts_keep_negatives_and_only_a_figure_copied_from_its_bullet():
    judge = _Judge({ROLE_B: "€2.1 billion retailer", OWN_B: "€5 billion invented"})
    [op] = await org_figure_verdict_ops(_profile(), judge)
    by_text = {v.text: v for v in op.verdicts}
    assert by_text[ROLE_B].figure == "€2.1 billion retailer"
    assert by_text[ROLE_B].figure_key == "currency-2100000000"
    assert by_text[OWN_B].figure is None  # not in its own bullet: dropped
    assert by_text[TEAM_B].figure is None and len(op.verdicts) == 4


@pytest.mark.asyncio
async def test_a_judge_failure_asks_nothing_and_a_judged_bullet_is_not_judged_again():
    assert await org_figure_verdict_ops(_profile(), _Judge(fail=True)) == []
    judge = _Judge()
    assert await org_figure_verdict_ops(_profile(metadata={"org_figure_verdicts": VERDICTS}), judge) == []
    assert judge.calls == []


def test_one_question_per_figure_across_its_tellings_and_entries():
    profile = _profile(metadata={"org_figure_verdicts": VERDICTS})
    [(section, entry, key, text, figure)] = open_org_figures(profile)
    assert (section, entry.id, text, figure) == ("work_experience", "w1", ROLE_B, "€2.1 billion retailer")
    [issue] = [i for i in assess_health(profile).issues if i.thread == "org_figure"]
    assert issue.id == f"org_figure:w1:{key}"
    assert issue.org_figure_entry == "Lead Architect @ Nordlicht Handel GmbH"
    assert issue.org_figure_text == ROLE_B
    assert issue.delivery_class == "A" and issue.resolver == "ask_org_figure"


def test_the_prompt_example_is_held_out_and_own_work_is_named():
    assert "19" not in ORG_FIGURE_SYSTEM and "revenue in 2021" not in ORG_FIGURE_SYSTEM
    assert '"team of 12"' in ORG_FIGURE_SYSTEM and '"reduced cost by €2M"' in ORG_FIGURE_SYSTEM


def test_verdicts_never_reach_a_prompt_but_the_decision_does():
    from applire.services.prompt_view import prompt_profile_view

    p = _profile(metadata={"org_figure_verdicts": VERDICTS})
    view = prompt_profile_view(p.model_dump(mode="json"))
    assert "org_figure_verdicts" not in (view.get("metadata") or {})
    assert "org_figure_decisions" not in view["work_experience"][0]  # omitted while empty


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
    p = _profile(metadata={"org_figure_verdicts": VERDICTS})
    db_session.add(make_master_profile(profile_json=p.model_dump(mode="json")))
    await db_session.commit()


async def _open(client):
    issues = (await client.get("/api/profile/health")).json()["issues"]
    return [i for i in issues if i["thread"] == "org_figure"]


@pytest.mark.asyncio
async def test_ja_stores_the_relation_verbatim_on_every_entry_carrying_the_figure(client, db_session):
    await _seed(db_session)
    [issue] = await _open(client)
    relation = "  Mein Shop-System trug 30 % des Online-Umsatzes  "
    resp = await client.post("/api/profile/org-figures/answer",
                             json={"issue_id": issue["id"], "include": True, "relation": relation})
    assert resp.status_code == 200, resp.text
    prof = resp.json()["profile"]
    [d_role] = prof["work_experience"][0]["org_figure_decisions"]
    [d_proj] = prof["projects"][0]["org_figure_decisions"]
    assert d_role == {"figure": "€2.1 billion retailer", "include": True,
                      "relation": relation.strip(), "text": ROLE_B, "key": "currency-2100000000"}
    assert d_proj["figure"] == "2,1 Mrd. € Jahresumsatz" and d_proj["relation"] == relation.strip()
    assert prof["work_experience"][0]["achievements"] == [ROLE_B, OWN_B]  # nothing rewritten
    last = prof["metadata"]["enrichment_history"][-1]
    assert last["source"] == "manual_edit"
    assert {c["rationale_key"] for c in last["changes"]} == {"org_figure_include"}
    assert await _open(client) == []  # gone on recompute; asked once


@pytest.mark.asyncio
async def test_nein_records_the_omission_without_a_relation(client, db_session):
    await _seed(db_session)
    [issue] = await _open(client)
    resp = await client.post("/api/profile/org-figures/answer",
                             json={"issue_id": issue["id"], "include": False, "relation": "ignored"})
    assert resp.status_code == 200
    [d] = resp.json()["profile"]["work_experience"][0]["org_figure_decisions"]
    assert d["include"] is False and d["relation"] is None
    assert await _open(client) == []


@pytest.mark.asyncio
async def test_refusals_404_409_422(client, db_session):
    await _seed(db_session)
    [issue] = await _open(client)
    post = lambda body: client.post("/api/profile/org-figures/answer", json=body)  # noqa: E731
    assert (await post({"issue_id": issue["id"], "include": True})).status_code == 422
    assert (await post({"issue_id": issue["id"], "include": True, "relation": " "})).status_code == 422
    assert (await post({"issue_id": "aspiration:w1:x", "include": False})).status_code == 404
    assert (await post({"issue_id": "org_figure:w1:currency-1", "include": False})).status_code == 409  # stale
    assert (await post({"issue_id": issue["id"], "include": False})).status_code == 200
    assert (await post({"issue_id": issue["id"], "include": False})).status_code == 409  # answered


# ── the decision is entry data: it survives a merge ──────────────────────────


def test_an_entry_merge_carries_the_decision_and_the_verdicts_follow():
    from applire.schemas.profile import OrgFigureDecision, WorkEntry
    from applire.services.profile.reconcile.apply import _apply_merge_entries
    from applire.services.profile.reconcile.ops import MergeEntries

    profile = _profile(metadata={"org_figure_verdicts": VERDICTS})
    profile.work_experience.append(WorkEntry(id="w2", company="Nordlicht Handel", role="Lead Architect",
                                             start_date="2019-01", end_date="2023-06"))
    decision = OrgFigureDecision(figure="€2.1 billion retailer", include=False, text=ROLE_B,
                                 key="currency-2100000000")
    profile.work_experience[0].org_figure_decisions = [decision]
    _apply_merge_entries(MergeEntries(section="work_experience", target_id="w2", source_id="w1"), profile, [])
    [kept] = profile.work_experience
    assert kept.id == "w2" and kept.org_figure_decisions == [decision]
    assert {v.entry_id for v in profile.metadata.org_figure_verdicts if v.section == "work_experience"} == {"w2"}
