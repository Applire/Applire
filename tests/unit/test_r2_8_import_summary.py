# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""R2-8 (9) + FS-6 (Hazelnut build 4, rulings V-2 = A, V-4 = A).

"See what was combined" lands on a user-language summary of the LAST import
(`GET /api/profile/import-summary`), not the engineer log; each recognised pair
is a question ("Ist A dasselbe wie B?") whose "Ja" is `POST
/api/profile/matches/confirm` (stamps the receipts; never asked again) and whose
"Nein" is the existing undo; a binding the #801 guard refused is listed as
"kept separately"."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from applire.schemas.profile import (
    EnrichmentRecord,
    FieldChange,
    KeptSeparateReceipt,
    MasterProfileData,
    MatchReceipt,
    ProfileMetadata,
    Skill,
)
from applire.services.profile.receipts import summarize_last_import

_T0 = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


def _profile() -> MasterProfileData:
    profile = MasterProfileData(skills=[
        Skill(id="s-budget", name="Budgetplanung", category="soft", aliases=["Budgetverantwortung"]),
        Skill(id="s-ment", name="Mentoring", category="soft"),
        Skill(id="s-rest", name="REST APIs", category="technical"),
        Skill(id="s-sap", name="SAP PP", category="technical"),
    ])
    profile.metadata = ProfileMetadata(enrichment_history=[
        EnrichmentRecord(  # an older import — never the summary's subject
            timestamp=_T0 - timedelta(days=3), source="cv_upload",
            changes=[FieldChange(section="skills", field="name", action="added", new_value="Old skill")],
        ),
        EnrichmentRecord(
            timestamp=_T0, source="cv_upload",
            changes=[FieldChange(section="skills", field="name", action="added", new_value="SAP PP")],
            matched=[
                MatchReceipt(section="skills", entity_id="s-budget", incoming="Budgetverantwortung",
                             existing="Budgetplanung", aliases_added={"aliases": "Budgetverantwortung"}),
                MatchReceipt(section="languages", entity_id="l-en", incoming="English",
                             existing="Englisch", basis="name_table"),
                MatchReceipt(section="skills", entity_id="s-x", incoming="Same", existing="same"),
            ],
            kept_separate=[KeptSeparateReceipt(entity_id="s-ment", incoming="REST APIs", existing="Mentoring")],
        ),
        EnrichmentRecord(  # a later interview turn — not an import
            timestamp=_T0 + timedelta(hours=1), source="interview",
            changes=[FieldChange(section="skills", field="name", action="added", new_value="Kaizen")],
        ),
    ])
    return profile


def test_the_summary_is_the_last_import_in_facts_and_pairs():
    summary = summarize_last_import(_profile())
    assert summary.timestamp == _T0 and summary.source == "cv_upload"
    assert [f.text for f in summary.facts] == ["SAP PP"]  # not the old import, not the interview
    assert summary.added_total == 1
    pairs = [(m.incoming, m.existing, m.basis) for m in summary.matched]
    assert pairs == [("Budgetverantwortung", "Budgetplanung", "model"), ("English", "Englisch", "name_table")]
    assert [(k.incoming, k.existing) for k in summary.kept_separate] == [("REST APIs", "Mentoring")]


def test_no_import_yields_an_empty_summary():
    assert summarize_last_import(MasterProfileData()).record_id is None


def test_an_undone_pair_is_not_listed():
    profile = _profile()
    profile.metadata.enrichment_history[1].matched[0].undone_at = _T0
    assert [m.incoming for m in summarize_last_import(profile).matched] == ["English"]


def test_confirm_match_stamps_the_pair_and_writes_no_section():
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.ops import ConfirmMatch

    before = _profile()
    result = apply_ops(before, [ConfirmMatch(entity_id="s-budget", incoming="budgetverantwortung")], "manual_edit")
    receipt = result.profile.metadata.enrichment_history[1].matched[0]
    assert receipt.confirmed_at is not None
    assert result.profile.skills == before.skills  # nothing in the vault sections moved
    assert [c.rationale_key for c in result.changes] == ["match_confirmed"]
    # idempotent: a second "Ja" writes nothing
    again = apply_ops(result.profile, [ConfirmMatch(entity_id="s-budget", incoming="Budgetverantwortung")], "manual_edit")
    assert again.changes == []


@pytest.mark.parametrize(
    "entity_id,incoming,code",
    [("l-en", "English", "name_table"), ("s-nope", "X", "unknown")],
)
def test_confirm_match_refuses_a_fact_or_an_unknown_pair(entity_id, incoming, code):
    from applire.services.profile.reconcile.apply import MatchNotSeparableError, apply_ops
    from applire.services.profile.reconcile.ops import ConfirmMatch

    with pytest.raises(MatchNotSeparableError) as exc:
        apply_ops(_profile(), [ConfirmMatch(entity_id=entity_id, incoming=incoming)], "manual_edit")
    assert exc.value.code == code


# ── the doors ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def client():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.auth import get_auth_provider
    from applire.db.session import Base, get_db
    from applire.models.profile import MasterProfile
    from applire.models.uploads import UploadRecord
    from applire.models.user import User
    from applire.routers.profile import router
    from tests.support.owners import HARNESS_USER_ID
    from tests.support.profile_factory import make_master_profile

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(
            c, tables=[MasterProfile.__table__, User.__table__, UploadRecord.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(make_master_profile(profile_json=_profile().model_dump(mode="json")))
        session.add(UploadRecord(
            user_id=HARNESS_USER_ID, original_filename="Lebenslauf_Stefan_Brandt.pdf",
            content_hash="h", mime_type="application/pdf", file_path="/x", byte_size=1,
            created_at=_T0 - timedelta(minutes=1),
        ))
        session.add(UploadRecord(  # an upload long before the record is not its file
            user_id=HARNESS_USER_ID, original_filename="old.pdf",
            content_hash="h2", mime_type="application/pdf", file_path="/y", byte_size=1,
            created_at=_T0 - timedelta(days=3),
        ))
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
async def test_import_summary_endpoint_names_the_file_and_the_pairs(client):
    res = await client.get("/api/profile/import-summary")
    assert res.status_code == 200
    body = res.json()
    assert body["file_name"] == "Lebenslauf_Stefan_Brandt.pdf"
    assert [m["incoming"] for m in body["matched"]] == ["Budgetverantwortung", "English"]
    assert body["kept_separate"][0]["existing"] == "Mentoring"


@pytest.mark.asyncio
async def test_confirm_endpoint_stamps_and_the_summary_keeps_the_answer(client):
    res = await client.post("/api/profile/matches/confirm",
                            json={"entity_id": "s-budget", "incoming": "Budgetverantwortung"})
    assert res.status_code == 200
    summary = (await client.get("/api/profile/import-summary")).json()
    budget = next(m for m in summary["matched"] if m["incoming"] == "Budgetverantwortung")
    assert budget["confirmed_at"] is not None
    refused = await client.post("/api/profile/matches/confirm",
                                json={"entity_id": "l-en", "incoming": "English"})
    assert refused.status_code == 409 and refused.json()["detail"] == "name_table"
