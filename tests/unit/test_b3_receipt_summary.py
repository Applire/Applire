# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut b3 WP-V, ruling V-1 = A (ADR-040 amended 2026-10-22, D-B3V1).

The interview receipt is a SUMMARY of one session, produced from the complete
decision trail: one fact = one line (a list value is exploded, a fact written to
a role's technologies AND as a skill is one line naming both sections), no
bookkeeping rows, grouped per section with per-role counts, a deterministic
"top" list, and the trail itself never rewritten.

Real data: the UX audit's Marcus session (tests/files/receipts/), which rendered
38 rows over ~7 screens on 0.44.0-beta.
"""
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import (  # noqa: E402
    EnrichmentRecord,
    FieldChange,
    MasterProfileData,
    ProfileMetadata,
    WorkEntry,
)
from applire.services.profile.receipts import summarize_session_receipts  # noqa: E402

_FIXTURE = Path(__file__).parent.parent / "files" / "receipts" / "marcus_interview_trail.json"
_SESSION = "45d2276b-674d-4700-9f8d-39311704d98e"


def _marcus() -> MasterProfileData:
    return MasterProfileData.model_validate(json.loads(_FIXTURE.read_text())["profile"])


def _raw_rows(profile: MasterProfileData) -> int:
    return sum(len(r.changes) for r in profile.metadata.enrichment_history)


def test_marcus_session_fixture_is_the_audit_shape():
    profile = _marcus()
    assert _raw_rows(profile) == 38  # the 38 rows the audit saw


def test_one_fact_one_line_tools_and_skills_collapse():
    s = summarize_session_receipts(_marcus(), _SESSION)
    keys = [f.key for f in s.facts]
    assert len(keys) == len(set(keys)), "a fact is listed once"
    sap_pp = [f for f in s.facts if f.text == "SAP PP"]
    assert len(sap_pp) == 1
    # listed under Skills (where the vault keeps capabilities), also at the role
    assert sap_pp[0].section == "skills" and sap_pp[0].kind == "skill"
    assert sap_pp[0].also_in == ["work_experience"]
    assert sap_pp[0].entity_label and "Weberit" in sap_pp[0].entity_label


def test_list_values_are_exploded_never_joined():
    s = summarize_session_receipts(_marcus(), _SESSION)
    for f in s.facts:
        assert "., " not in f.text, f.text
    assert any(f.text == "SAP MM" for f in s.facts)
    assert not any(f.text.startswith("SAP PP, SAP MM") for f in s.facts)


def test_bookkeeping_is_not_a_fact_and_denials_are_limits():
    s = summarize_session_receipts(_marcus(), _SESSION)
    limits = [f for f in s.facts if f.kind == "limit"]
    assert {f.text for f in limits} >= {"dauerhafte Führungsspanne von 120 Mitarbeitenden", "IFS", "BRC"}
    assert all(f.section != "metadata" or f.kind == "limit" for f in s.facts)


def test_fact_count_is_smaller_than_the_raw_rows_and_groups_add_up():
    profile = _marcus()
    s = summarize_session_receipts(profile, _SESSION)
    assert s.facts_total == len(s.facts)
    assert sum(g.count for g in s.groups) == s.facts_total
    assert s.answers == 11
    for g in s.groups:
        assert len(g.preview) + g.more == g.count


def test_work_group_counts_per_role_from_the_bullets_location():
    s = summarize_session_receipts(_marcus(), _SESSION)
    work = next(g for g in s.groups if g.section == "work_experience")
    assert work.entities, "legacy rows without entity_id are located by where the bullet lives"
    assert sum(e.count for e in work.entities) <= work.count
    assert all("@" in e.label for e in work.entities)


def test_top_is_story_then_results_and_never_a_bare_skill():
    s = summarize_session_receipts(_marcus(), _SESSION)
    assert 1 <= len(s.top) <= 5
    assert s.top[0].kind == "story"
    assert s.top[0].detail and "73" in s.top[0].detail
    assert all(f.kind not in {"skill", "tool", "limit"} for f in s.top)
    assert s.top[1].kind == "result"


def test_trail_is_not_rewritten():
    profile = _marcus()
    before = copy.deepcopy(profile.model_dump(mode="json"))
    summarize_session_receipts(profile, _SESSION)
    assert profile.model_dump(mode="json") == before


def _profile_with_two_sessions() -> MasterProfileData:
    entry = WorkEntry(company="Acme", role="Lead", responsibilities=["A", "B"])
    now = datetime.now(timezone.utc)

    def rec(sid, text):
        return EnrichmentRecord(
            timestamp=now, source="interview", source_session_id=sid,
            changes=[FieldChange(section="work_experience", field="responsibilities",
                                 action="merged", new_value=[text], entity_id=entry.id)],
        )

    return MasterProfileData(
        work_experience=[entry],
        metadata=ProfileMetadata(enrichment_history=[
            rec("s-old", "A"),
            EnrichmentRecord(timestamp=now, source="cv_upload", changes=[
                FieldChange(section="skills", field="name", action="added", new_value="Python")]),
            rec("s-new", "B"),
        ]),
    )


def test_scoped_to_the_named_session_only():
    s = summarize_session_receipts(_profile_with_two_sessions(), "s-old")
    assert [f.text for f in s.facts] == ["A"]
    assert s.session_id == "s-old"


def test_default_session_is_the_latest_interview_session():
    s = summarize_session_receipts(_profile_with_two_sessions(), None)
    assert s.session_id == "s-new"
    assert [f.text for f in s.facts] == ["B"]
    assert s.facts[0].entity_label == "Lead @ Acme"


def test_no_interview_trail_is_an_empty_summary():
    s = summarize_session_receipts(MasterProfileData(), None)
    assert s.facts_total == 0 and s.groups == [] and s.top == [] and s.session_id is None


# ── the REST door: GET /api/profile/changes/summary ────────────────────────────

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402


@pytest_asyncio.fixture
async def client():
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.auth import get_auth_provider
    from applire.db.session import Base, get_db
    from applire.models.profile import MasterProfile
    from applire.models.user import User
    from applire.routers.profile import router
    from tests.support.owners import HARNESS_USER_ID
    from tests.support.profile_factory import make_master_profile

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__, User.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(make_master_profile(profile_json=_marcus().model_dump(mode="json")))
        await session.commit()
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: session
        auth = MagicMock()
        auth.get_current_user = AsyncMock(return_value=MagicMock(id=HARNESS_USER_ID))
        app.dependency_overrides[get_auth_provider] = lambda: auth
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    await engine.dispose()


@pytest.mark.asyncio
async def test_endpoint_serves_the_session_summary(client):
    res = await client.get("/api/profile/changes/summary", params={"session_id": _SESSION})
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == _SESSION
    assert body["facts_total"] == len(body["facts"]) > 0
    assert body["top"][0]["kind"] == "story"
    other = await client.get("/api/profile/changes/summary", params={"session_id": "nope"})
    assert other.json()["facts_total"] == 0


# ── the producer (ADR-063 amended 2026-10-22, D-B3V2) ──────────────────────────


def test_add_bullets_receipt_names_its_entity_and_only_what_it_appended():
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import AddBullets

    entry = WorkEntry(company="Acme", role="Lead", responsibilities=["Ran the team."])
    profile = MasterProfileData(work_experience=[entry])
    result = apply_ops(
        profile,
        [AddBullets(target=entry.id, responsibilities=["ran the team.", "Hired four engineers."],
                    technologies=["Python"])],
        "interview",
    )
    by_field = {c.field: c for c in result.changes}
    # the held bullet (case-folded duplicate) is not claimed as written
    assert by_field["responsibilities"].new_value == ["Hired four engineers."]
    assert by_field["responsibilities"].entity_id == entry.id
    assert by_field["technologies"].entity_id == entry.id


def test_agent_interview_records_are_interview_receipts():
    """US256: a claim an agent submitted over MCP is testimony — it belongs to
    the interview receipt (moved here from DecisionTrailReview's filter)."""
    now = datetime.now(timezone.utc)
    profile = MasterProfileData(metadata=ProfileMetadata(enrichment_history=[
        EnrichmentRecord(timestamp=now, source="agent_interview", source_session_id="a1", changes=[
            FieldChange(section="skills", field="name", action="added", new_value="Terraform")]),
        EnrichmentRecord(timestamp=now, source="cv_upload", changes=[
            FieldChange(section="skills", field="name", action="added", new_value="Python")]),
    ]))
    s = summarize_session_receipts(profile, None)
    assert s.session_id == "a1" and [f.text for f in s.facts] == ["Terraform"]
