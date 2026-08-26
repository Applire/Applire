# Copyright (C) 2024-2026 Tobias Rosenbaum
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

"""#480 PR 3 (design §7.6) — one door-level write test per migrated writer.

`commit_ops` **flushes and never commits**: the transaction stays with the
caller. §7.6 names the cost honestly — *a forgotten `db.commit()` is a silent
no-write* — and the binding mitigation is a test per migrated writer, landed
with the migration. PR 3 migrates the `FieldEdit` intake
(`patch_profile_section`), which is reached through three doors:

* the REST `PATCH /api/profile/{section}` route (the profile page);
* the MCP `update_profile` tool (the agent channel);
* the CV section editor's save-to-profile (#336), which calls the intake.

Every test drives the real door against a **file-backed** database and re-reads
over a SEPARATE connection, so an uncommitted write is invisible — which no
in-session assertion could tell you.

The file also pins what the migration is FOR: a removal now earns its own
receipt instead of vanishing into one opaque section blob (ADR-063 amended
2026-08-09 clause 8), and what it must NOT change: #178 merge-patch semantics,
the status a skill arrives with, and the parked lists (#333 / E037).
"""
import sys
import types
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


_WORK_A = "11111111-1111-1111-1111-111111111111"
_WORK_B = "22222222-2222-2222-2222-222222222222"

_SEED_PROFILE = {
    "personal_info": {
        "name": "Anna Bauer",
        "email": "anna@example.invalid",
        "phone": "+49 30 000000",
    },
    "professional_summary": {"de": "Deutsche Zusammenfassung."},
    "skills": [
        {"name": "Python", "category": "technical", "status": "confirmed"},
        {"name": "Kafka", "category": "technical", "status": "confirmed"},
    ],
    "work_experience": [
        {
            "id": _WORK_A,
            "company": "Acme GmbH",
            "role": "Engineer",
            "start_date": "2020-01",
            "responsibilities": ["Ran the build"],
        },
        {
            "id": _WORK_B,
            "company": "Beta AG",
            "role": "Analyst",
            "start_date": "2016-01",
            "end_date": "2019-12",
        },
    ],
    "languages": [{"language": "German", "level": "C2"}],
    "metadata": {},
}


@pytest_asyncio.fixture
async def durable_db(tmp_path):
    """A file-backed database — so "did it survive the request?" is a real
    question and not an identity-map artefact."""
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    from applire.db.session import Base

    url = f"sqlite+aiosqlite:///{tmp_path / 'vault.sqlite'}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


async def _seed_profile(factory, profile_json: dict | None = None) -> uuid.UUID:
    from applire.models.profile import MasterProfile, authorized_profile_write

    async with factory() as session:
        with authorized_profile_write():
            record = MasterProfile(
                profile_json=profile_json
                if profile_json is not None
                else dict(_SEED_PROFILE)
            )
        session.add(record)
        await session.commit()
        return record.id


async def _read_back(engine, profile_id: uuid.UUID) -> dict:
    """A brand-new session on a brand-new connection: only COMMITTED state."""
    from applire.models.profile import MasterProfile

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        return dict(row.profile_json)


def _fake_request(body):
    """The only thing `patch_section` uses of the Request is `await .json()`."""
    return types.SimpleNamespace(json=AsyncMock(return_value=body))


def _latest_changes(stored: dict) -> list[dict]:
    return stored["metadata"]["enrichment_history"][-1]["changes"]


# ── Door 7: the REST profile-page PATCH route ─────────────────────────────────


@pytest.mark.asyncio
async def test_rest_patch_door_write_survives_the_request(durable_db):
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    async with factory() as request_session:
        response = await patch_section(
            "languages",
            _fake_request([{"language": "French", "level": "B1"}]),
            request_session,
            None,
            None,
        )

    assert [lang.language for lang in response.profile.languages] == ["French"]
    stored = await _read_back(engine, profile_id)
    assert [lang["language"] for lang in stored["languages"]] == ["French"]
    # The committer's invariants are durable too, not just the section.
    history = stored["metadata"]["enrichment_history"]
    assert len(history) == 1
    assert history[0]["source"] == "manual_edit"
    assert stored["metadata"]["completeness_score"] > 0
    assert stored["metadata"]["last_updated"]


@pytest.mark.asyncio
async def test_rest_patch_door_still_refuses_an_unknown_section(durable_db):
    """The refusal the route turns into a 422 happens in the pure adapter,
    before anything reaches the write path."""
    from applire.routers.profile import patch_section
    from fastapi import HTTPException

    engine, factory = durable_db
    await _seed_profile(factory)

    async with factory() as request_session:
        with pytest.raises(HTTPException) as exc:
            await patch_section(
                "metadata", _fake_request({"denied_concepts": []}), request_session,
                None, None,
            )

    assert exc.value.status_code == 422
    assert "Invalid section" in str(exc.value.detail)


# ── Door 8: the MCP `update_profile` tool ─────────────────────────────────────


def _mcp_db(factory):
    @asynccontextmanager
    async def _cm():
        async with factory() as session:
            yield session

    return _cm


@pytest.mark.asyncio
async def test_mcp_update_profile_door_write_survives_the_request(
    durable_db, monkeypatch
):
    import applire.mcp.server as server

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    monkeypatch.setattr(server, "get_db", _mcp_db(factory))
    monkeypatch.setattr(server, "get_provider", lambda: None)

    result = await server.update_profile(
        section="languages", data=[{"language": "French", "level": "B1"}]
    )

    assert [lang["language"] for lang in result["profile"]["languages"]] == ["French"]
    stored = await _read_back(engine, profile_id)
    assert [lang["language"] for lang in stored["languages"]] == ["French"]
    assert len(stored["metadata"]["enrichment_history"]) == 1


@pytest.mark.asyncio
async def test_both_doors_produce_the_same_vault_state(durable_db, monkeypatch):
    """ADR-058 clause 2 — the same edit through the UI door and the agent door
    must leave the same vault. The migration keeps them on ONE implementation,
    so this is a parity pin, not a coincidence."""
    import applire.mcp.server as server
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    rest_profile = await _seed_profile(factory)
    payload = [{"language": "French", "level": "B1"}]

    async with factory() as request_session:
        await patch_section("languages", _fake_request(payload), request_session, None, None)
    rest_state = await _read_back(engine, rest_profile)

    # A second, independent vault written through the agent door.
    from applire.models.profile import MasterProfile

    async with factory() as session:
        row = (
            await session.execute(
                select(MasterProfile).where(MasterProfile.id == rest_profile)
            )
        ).scalar_one()
        row.deleted_at = datetime.now(timezone.utc)
        await session.commit()
    mcp_profile = await _seed_profile(factory)

    monkeypatch.setattr(server, "get_db", _mcp_db(factory))
    monkeypatch.setattr(server, "get_provider", lambda: None)
    await server.update_profile(section="languages", data=payload)
    mcp_state = await _read_back(engine, mcp_profile)

    def _comparable(state: dict) -> dict:
        def _strip_ids(node):
            # ADR-077 gave the five previously id-less vault types minted
            # ids — random per door invocation, like timestamps. Parity is
            # about semantic state, so entry ids are normalized away here
            # (the history "id" was already stripped for the same reason).
            if isinstance(node, dict):
                return {
                    k: _strip_ids(v) for k, v in node.items() if k != "id"
                }
            if isinstance(node, list):
                return [_strip_ids(v) for v in node]
            return node

        state = _strip_ids(dict(state))
        meta = dict(state.pop("metadata"))
        meta.pop("last_updated", None)
        meta.pop("created_at", None)
        history = [
            {k: v for k, v in record.items() if k not in {"timestamp", "id"}}
            for record in meta.pop("enrichment_history")
        ]
        return {"profile": state, "metadata": meta, "history": history}

    assert _comparable(rest_state) == _comparable(mcp_state)


# ── Door 9: the CV section editor's save-to-profile ───────────────────────────


def _cv_fixture_rows(profile_id: uuid.UUID):
    """User → Job → GeneratedCV wired to the seeded profile, matching the
    `position::` save path (mirrors backend/tests/unit/test_iter23_section_editor)."""
    from applire.models.cv import GeneratedCV
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    job_id = uuid.UUID("00000000-0000-0000-0000-0000000000a2")
    cv_id = uuid.UUID("00000000-0000-0000-0000-0000000000a5")
    position_uuid = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    user = User(
        id=uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
        email="pr3-door-test@applire.community",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    job = JobAnalysis(
        id=job_id,
        raw_text_hash="pr3-door-test",
        raw_text="Python developer job",
        role_title="Python Developer",
        required_skills=["Python"],
        nice_to_have_skills=[],
        keywords=["Python"],
        seniority_level="mid",
        company_culture_signals=[],
        language_requirement="de",
    )
    cv = GeneratedCV(
        id=cv_id,
        job_analysis_id=job_id,
        profile_id=profile_id,
        tailored_data={
            "contact": {"name": "Anna Bauer", "email": "anna@example.invalid"},
            "summary": "Deutsche Zusammenfassung.",
            "work_history": [
                {
                    "company": "Acme GmbH",
                    "role": "Engineer",
                    "start_date": "2020-01",
                    "end_date": None,
                    "bullets": ["Ran the build"],
                }
            ],
            "skills": ["Python"],
        },
        template="classic_german",
        status="ready",
        content_snapshot={
            "introduction": "Deutsche Zusammenfassung.",
            "positions": [
                {
                    "id": position_uuid,
                    "index": 0,
                    "title": "Engineer",
                    "company": "Acme GmbH",
                    "period": "2020-01",
                    "bullets": ["Ran the build"],
                    "work_id": _WORK_A,
                }
            ],
            "skills": ["Python"],
        },
        section_overrides=None,
        ats_report=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    return [user, job, cv], cv_id, position_uuid


@pytest.mark.asyncio
async def test_section_editor_save_survives_the_request(durable_db):
    """The #336 residual closes here: the editor's save inherits the whole
    invariant set transitively, because it goes through the intake that is now
    a `commit_ops` caller."""
    from applire.services.cv_section_editor import patch_cv_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    rows, cv_id, position_uuid = _cv_fixture_rows(profile_id)

    async with factory() as session:
        session.add_all(rows)
        await session.commit()

    async with factory() as request_session:
        await patch_cv_section(
            cv_id,
            f"position::{position_uuid}",
            "Ran the build\nOwned the release train",
            True,
            request_session,
        )

    stored = await _read_back(engine, profile_id)
    acme = next(e for e in stored["work_experience"] if e["id"] == _WORK_A)
    assert acme["responsibilities"] == ["Ran the build", "Owned the release train"]
    # The invariants the editor's own writer never had (ADR-063 finding 2).
    history = stored["metadata"]["enrichment_history"]
    assert len(history) == 1
    assert history[0]["source"] == "manual_edit"
    assert history[0]["source_session_id"] == str(cv_id)
    assert stored["metadata"]["completeness_score"] > 0
    # …and the OTHER role is untouched: a section replace is not a rewrite.
    beta = next(e for e in stored["work_experience"] if e["id"] == _WORK_B)
    assert beta["role"] == "Analyst"


@pytest.mark.asyncio
async def test_section_editor_saved_skills_are_still_unconfirmed(durable_db):
    """#336, pinned through the new path. A skill typed into a tailored CV has
    no testimony behind it; it must arrive `unconfirmed` (ADR-061 clause 3) —
    visible, candidate-confirmable, never claimable. Routing the save through
    the committer must not quietly promote it."""
    from applire.services.cv_section_editor import patch_cv_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    rows, cv_id, _ = _cv_fixture_rows(profile_id)

    async with factory() as session:
        session.add_all(rows)
        await session.commit()

    async with factory() as request_session:
        await patch_cv_section(
            cv_id, "skills", "Python\nKubernetes", True, request_session
        )

    stored = await _read_back(engine, profile_id)
    by_name = {s["name"]: s for s in stored["skills"]}
    assert by_name["Kubernetes"]["status"] == "unconfirmed"
    assert by_name["Kubernetes"]["source"] == "transcribed"
    assert by_name["Python"]["status"] == "confirmed"
    # The editor's skills save is ADDITIVE — nothing the candidate attested is
    # dropped just because the document did not mention it.
    assert by_name["Kafka"]["status"] == "confirmed"


# ── What the migration is FOR: the per-entry receipt, removals included ───────


@pytest.mark.asyncio
async def test_a_removal_through_the_rest_door_earns_its_own_receipt(durable_db):
    """The defect this PR closes. Deletion was always expressible through PATCH
    (§7.7 — no new capability); what was missing was a usable receipt. The trail
    used to hold ONE `updated` blob carrying both section dumps, so "which entry
    went?" was answerable only by diffing two JSON blobs by eye."""
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    async with factory() as request_session:
        await patch_section(
            "skills",
            _fake_request([{"name": "Python", "category": "technical"}]),
            request_session,
            None,
            None,
        )

    stored = await _read_back(engine, profile_id)
    assert [s["name"] for s in stored["skills"]] == ["Python"]

    changes = _latest_changes(stored)
    removals = [c for c in changes if c["action"] == "removed"]
    assert [c["field"] for c in removals] == ["Kafka"]
    assert removals[0]["section"] == "skills"
    assert removals[0]["old_value"]["name"] == "Kafka"


@pytest.mark.asyncio
async def test_a_removal_through_the_agent_door_earns_the_same_receipt(
    durable_db, monkeypatch
):
    import applire.mcp.server as server

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    monkeypatch.setattr(server, "get_db", _mcp_db(factory))
    monkeypatch.setattr(server, "get_provider", lambda: None)

    await server.update_profile(
        section="skills", data=[{"name": "Python", "category": "technical"}]
    )

    stored = await _read_back(engine, profile_id)
    removals = [c for c in _latest_changes(stored) if c["action"] == "removed"]
    assert [c["field"] for c in removals] == ["Kafka"]


@pytest.mark.asyncio
async def test_deleting_a_role_removes_it_and_says_which_one(durable_db):
    """End to end: the entry is gone from the vault AND the trail names it."""
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    kept = [e for e in _SEED_PROFILE["work_experience"] if e["id"] == _WORK_A]

    async with factory() as request_session:
        await patch_section(
            "work_experience", _fake_request(kept), request_session, None, None
        )

    stored = await _read_back(engine, profile_id)
    assert [e["id"] for e in stored["work_experience"]] == [_WORK_A]
    removals = [c for c in _latest_changes(stored) if c["action"] == "removed"]
    assert [c["field"] for c in removals] == ["Analyst @ Beta AG"]


# ── What the migration must NOT change ────────────────────────────────────────


@pytest.mark.asyncio
async def test_object_section_merge_patch_survives_the_new_path(durable_db):
    """#178 through the committer: supplied keys win, omitted keys survive, an
    explicit null clears — and the clearing is receipted as a removal."""
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    async with factory() as request_session:
        await patch_section(
            "personal_info",
            _fake_request({"address": "Musterweg 1", "phone": None}),
            request_session,
            None,
            None,
        )

    stored = await _read_back(engine, profile_id)
    info = stored["personal_info"]
    assert info["address"] == "Musterweg 1"
    assert info["name"] == "Anna Bauer"  # omitted key survived
    assert info["email"] == "anna@example.invalid"
    assert info["phone"] is None  # explicit null cleared it

    changes = {c["field"]: c["action"] for c in _latest_changes(stored)}
    assert changes["address"] == "added"
    assert changes["phone"] == "removed"


@pytest.mark.asyncio
async def test_a_summary_edit_keeps_the_other_language_slot(durable_db):
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    async with factory() as request_session:
        await patch_section(
            "professional_summary",
            _fake_request({"en": "English summary."}),
            request_session,
            None,
            None,
        )

    stored = await _read_back(engine, profile_id)
    assert stored["professional_summary"]["en"] == "English summary."
    assert stored["professional_summary"]["de"] == "Deutsche Zusammenfassung."


@pytest.mark.asyncio
async def test_patched_skills_keep_the_status_the_payload_gave_them(durable_db):
    """The write path must not re-decide a skill's standing. A payload without
    a status takes the schema default (`confirmed`, as a manual edit always
    has); one that says `unconfirmed` stays `unconfirmed` — which is what the
    section editor's #336 write depends on."""
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    async with factory() as request_session:
        await patch_section(
            "skills",
            _fake_request(
                [
                    {"name": "Python", "category": "technical"},
                    {"name": "Rust", "category": "technical", "status": "unconfirmed"},
                ]
            ),
            request_session,
            None,
            None,
        )

    stored = await _read_back(engine, profile_id)
    by_name = {s["name"]: s for s in stored["skills"]}
    assert by_name["Python"]["status"] == "confirmed"
    assert by_name["Rust"]["status"] == "unconfirmed"


@pytest.mark.asyncio
async def test_a_section_edit_preserves_the_parked_lists(durable_db):
    """#333 / E037 PQ #4 — an open question is the human's; a section edit is
    not an answer to it and must not discard it."""
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    seed = {
        **_SEED_PROFILE,
        "metadata": {
            "pending_confirmations": [
                {
                    "confirmation_id": "c1",
                    "question": "Which employer?",
                    "options": ["Acme GmbH", "Beta AG"],
                    "source": "cv_upload",
                }
            ],
            "pending_conflicts": [
                {
                    "conflict_id": "x1",
                    "section": "work_experience",
                    "field": "end_date",
                    "existing_value": "2019-12",
                    "incoming_value": "2020-01",
                    "source": "cv_upload",
                }
            ],
        },
    }
    profile_id = await _seed_profile(factory, seed)

    async with factory() as request_session:
        await patch_section(
            "languages",
            _fake_request([{"language": "French", "level": "B1"}]),
            request_session,
            None,
            None,
        )

    meta = (await _read_back(engine, profile_id))["metadata"]
    assert [c["confirmation_id"] for c in meta["pending_confirmations"]] == ["c1"]
    assert [c["conflict_id"] for c in meta["pending_conflicts"]] == ["x1"]


@pytest.mark.asyncio
async def test_a_section_edit_cannot_reach_a_persisted_denial(durable_db):
    """`metadata` is in no op's vocabulary, so nothing a manual edit can say
    releases a recorded denial or rewrites the trail (ADR-059 / ADR-063 amended
    clause 1). The refusal is structural, not a per-door check."""
    from applire.routers.profile import patch_section
    from fastapi import HTTPException

    engine, factory = durable_db
    seed = {
        **_SEED_PROFILE,
        "metadata": {
            "denied_concepts": [
                {
                    "concept": "Kubernetes",
                    "statement": "I have never used Kubernetes.",
                    "source": "interview",
                    "recorded_at": "2026-01-01T00:00:00Z",
                }
            ]
        },
    }
    profile_id = await _seed_profile(factory, seed)

    async with factory() as request_session:
        with pytest.raises(HTTPException) as exc:
            await patch_section(
                "metadata", _fake_request({"denied_concepts": []}), request_session,
                None, None,
            )
        # And the same refusal for the sidecar the N/A suppressions live in.
        with pytest.raises(HTTPException):
            await patch_section(
                "_meta", _fake_request({"na_fields": []}), request_session, None, None
            )

    assert exc.value.status_code == 422
    denied = (await _read_back(engine, profile_id))["metadata"]["denied_concepts"]
    assert [d["concept"] for d in denied] == ["Kubernetes"]


# ── ADR-063 amended 2026-08-25 (E055 / JF-F-H1.6) — stale-edit refusal ───────


async def _updated_at(engine, profile_id: uuid.UUID) -> datetime:
    from applire.models.profile import MasterProfile

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        row = (
            await session.execute(
                select(MasterProfile).where(MasterProfile.id == profile_id)
            )
        ).scalar_one()
        value = row.updated_at
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_rest_patch_with_a_matching_basis_saves(durable_db):
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    basis = await _updated_at(engine, profile_id)

    async with factory() as request_session:
        response = await patch_section(
            "languages",
            _fake_request([{"language": "French", "level": "B1"}]),
            request_session,
            None,
            None,
            basis_updated_at=basis,
        )
    assert [lang.language for lang in response.profile.languages] == ["French"]


@pytest.mark.asyncio
async def test_rest_patch_with_a_stale_basis_is_a_409_carrying_the_current_profile(durable_db):
    """The UI door: the profile moved since the GET → 409, body names the
    error and carries the CURRENT profile so the editor can reload; the vault
    is untouched (no receipt either)."""
    from fastapi import HTTPException

    from applire.routers.profile import patch_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    stale = (await _updated_at(engine, profile_id)) - timedelta(seconds=5)

    async with factory() as request_session:
        with pytest.raises(HTTPException) as excinfo:
            await patch_section(
                "languages",
                _fake_request([{"language": "French", "level": "B1"}]),
                request_session,
                None,
                None,
                basis_updated_at=stale,
            )

    assert excinfo.value.status_code == 409
    detail = excinfo.value.detail
    assert detail["error"] == "stale_edit"
    assert [lang["language"] for lang in detail["current"]["profile"]["languages"]] == ["German"]
    stored = await _read_back(engine, profile_id)
    assert [lang["language"] for lang in stored["languages"]] == ["German"]
    assert not stored["metadata"].get("enrichment_history")


@pytest.mark.asyncio
async def test_mcp_update_profile_with_a_stale_basis_is_refused_the_same_way(durable_db, monkeypatch):
    """ADR-058 parity: the agent door takes the same OPTIONAL basis and refuses
    a stale one; omitting it keeps last-write-wins exactly as before."""
    from mcp.shared.exceptions import McpError

    from applire.mcp import server as mcp_server

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    stale = (await _updated_at(engine, profile_id)) - timedelta(seconds=5)

    @asynccontextmanager
    async def _get_db():
        async with factory() as session:
            yield session

    monkeypatch.setattr(mcp_server, "get_db", _get_db)
    monkeypatch.setattr(mcp_server, "get_provider", lambda: None)

    with pytest.raises(McpError) as excinfo:
        await mcp_server.update_profile(
            "languages",
            [{"language": "French", "level": "B1"}],
            basis_updated_at=stale.isoformat(),
        )
    assert "stale_edit" in str(excinfo.value)

    # Omitted basis: unchanged behaviour.
    result = await mcp_server.update_profile(
        "languages", [{"language": "French", "level": "B1"}]
    )
    assert [lang["language"] for lang in result["profile"]["languages"]] == ["French"]


@pytest.mark.asyncio
async def test_mcp_update_profile_advertises_the_optional_basis():
    from applire.mcp.server import mcp

    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "update_profile")
    props = tool.inputSchema["properties"]
    assert "basis_updated_at" in props
    assert "basis_updated_at" not in tool.inputSchema.get("required", [])


# ── E055 / JF-F-H2.1 — the denial re-floor THROUGH a manual skills edit ───────
# Owed since the F-H walk: the re-floor had only ever been tested op-level.
# `test_a_section_edit_cannot_reach_a_persisted_denial` tests the `metadata`
# exclusion, not this path.


@pytest.mark.asyncio
async def test_a_manual_skills_edit_cannot_confirm_a_denied_skill(durable_db):
    """A chip editor (or any raw PATCH) that re-sends a denied skill as
    `confirmed` gets it re-floored to `denied` by the committer's invariant 2;
    an `unconfirmed` skill whose level changes stays `unconfirmed`."""
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    seed = dict(_SEED_PROFILE)
    seed["skills"] = [
        {"id": "s-py", "name": "Python", "category": "technical", "status": "confirmed"},
        {"id": "s-an", "name": "Ansible", "category": "technical", "status": "denied"},
        {"id": "s-go", "name": "Go", "category": "technical", "status": "unconfirmed",
         "proficiency": "basic"},
    ]
    seed["metadata"] = {
        "denied_concepts": [
            {"concept": "Ansible", "statement": "I have never used Ansible.",
             "source": "interview", "date": "2026-08-01", "denial_level": "direct"}
        ]
    }
    profile_id = await _seed_profile(factory, seed)

    laundered = [
        {"id": "s-py", "name": "Python", "category": "technical", "status": "confirmed"},
        {"id": "s-an", "name": "Ansible", "category": "technical", "status": "confirmed",
         "proficiency": "expert"},
        {"id": "s-go", "name": "Go", "category": "technical", "status": "unconfirmed",
         "proficiency": "advanced"},
    ]
    async with factory() as request_session:
        await patch_section("skills", _fake_request(laundered), request_session, None, None)

    stored = {s["name"]: s for s in (await _read_back(engine, profile_id))["skills"]}
    assert stored["Ansible"]["status"] == "denied"
    assert stored["Go"]["status"] == "unconfirmed"
    assert stored["Go"]["proficiency"] == "advanced"  # the level edit itself landed
    assert stored["Python"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_certification_dates_accept_the_pickers_shapes(durable_db):
    """JF-F-H2.3: the certification editor emits `YYYY-MM` / `YYYY`; the schema
    coerces both to a date (year-only lands on 1 January — recorded shape)."""
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)
    payload = [
        {"name": "AWS SAA", "date_obtained": "2021-06", "expiry_date": "2024"},
        {"name": "Undated", "date_obtained": None, "expiry_date": ""},
    ]
    async with factory() as request_session:
        await patch_section("certifications", _fake_request(payload), request_session, None, None)

    stored = {c["name"]: c for c in (await _read_back(engine, profile_id))["certifications"]}
    assert stored["AWS SAA"]["date_obtained"] == "2021-06-01"
    assert stored["AWS SAA"]["expiry_date"] == "2024-01-01"
    assert stored["Undated"]["date_obtained"] is None
    assert stored["Undated"]["expiry_date"] is None


@pytest.mark.asyncio
async def test_a_persisted_denied_status_is_floored_without_a_denied_concept(durable_db):
    """Adversarial finding 2026-08-26: the re-floor only knew
    `metadata.denied_concepts`; a skill persisted as `denied` any other way
    could be re-confirmed by a raw PATCH. Invariant 2b floors on the
    PERSISTED status itself (by id, then by name); removal stays allowed."""
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    seed = dict(_SEED_PROFILE)
    seed["skills"] = [
        {"id": "s-k8s", "name": "Kubernetes", "category": "technical", "status": "denied"},
        {"id": "s-py", "name": "Python", "category": "technical", "status": "confirmed"},
    ]
    seed["metadata"] = {}  # NO denied_concepts — the gap the finding named
    profile_id = await _seed_profile(factory, seed)

    async with factory() as request_session:
        await patch_section(
            "skills",
            _fake_request([
                {"id": "s-k8s", "name": "Kubernetes", "category": "technical", "status": "confirmed"},
                {"name": "kubernetes", "category": "technical", "status": "confirmed"},  # namesake, no id
                {"id": "s-py", "name": "Python", "category": "technical", "status": "confirmed"},
            ]),
            request_session, None, None,
        )
    stored = (await _read_back(engine, profile_id))["skills"]
    by_name = {s["name"].casefold(): s["status"] for s in stored}
    assert by_name["kubernetes"] == "denied"
    assert all(s["status"] == "denied" for s in stored if s["name"].casefold() == "kubernetes")
    assert by_name["python"] == "confirmed"
    # Removal is not floored — the candidate may drop the entry.
    async with factory() as request_session:
        await patch_section(
            "skills",
            _fake_request([{"id": "s-py", "name": "Python", "category": "technical", "status": "confirmed"}]),
            request_session, None, None,
        )
    assert [s["name"] for s in (await _read_back(engine, profile_id))["skills"]] == ["Python"]


# ── US292 / ADR-063 amended 2026-08-25 — `projects` through the same door ────


@pytest.mark.asyncio
async def test_rest_patch_door_writes_projects_with_a_named_receipt(durable_db):
    """JF-F-H3.2 ruling (0): the projects editor saves through the mandated
    door, its receipt NAMES the project (not the bare section), and the id
    the committer minted survives the next edit as an `updated` receipt."""
    from applire.routers.profile import patch_section

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    async with factory() as request_session:
        response = await patch_section(
            "projects",
            _fake_request(
                [
                    {
                        "name": "CI/CD Migration",
                        "role": "Lead",
                        "achievements": ["Cut build time by 78%"],
                    }
                ]
            ),
            request_session,
            None,
            None,
        )

    assert [p.name for p in response.profile.projects] == ["CI/CD Migration"]
    stored = await _read_back(engine, profile_id)
    assert [p["name"] for p in stored["projects"]] == ["CI/CD Migration"]
    added = [c for c in _latest_changes(stored) if c["section"] == "projects"]
    assert [(c["action"], c["field"]) for c in added] == [("added", "CI/CD Migration")]

    minted_id = stored["projects"][0]["id"]
    assert minted_id
    async with factory() as request_session:
        await patch_section(
            "projects",
            _fake_request([{**stored["projects"][0], "role": "Lead Developer"}]),
            request_session,
            None,
            None,
        )
    stored2 = await _read_back(engine, profile_id)
    assert stored2["projects"][0]["id"] == minted_id
    updated = [c for c in _latest_changes(stored2) if c["section"] == "projects"]
    assert [(c["action"], c["field"]) for c in updated] == [("updated", "CI/CD Migration")]


@pytest.mark.asyncio
async def test_mcp_update_profile_writes_projects_the_same_way(durable_db, monkeypatch):
    """ADR-058 parity: the agent door accepts `projects` with identical
    wholesale-replace semantics and the same named receipt."""
    import applire.mcp.server as server

    engine, factory = durable_db
    profile_id = await _seed_profile(factory)

    monkeypatch.setattr(server, "get_db", _mcp_db(factory))
    monkeypatch.setattr(server, "get_provider", lambda: None)

    result = await server.update_profile(
        section="projects", data=[{"name": "Oncology Pipeline", "role": "Data Lead"}]
    )

    assert [p["name"] for p in result["profile"]["projects"]] == ["Oncology Pipeline"]
    stored = await _read_back(engine, profile_id)
    changes = [c for c in _latest_changes(stored) if c["section"] == "projects"]
    assert [(c["action"], c["field"]) for c in changes] == [("added", "Oncology Pipeline")]


@pytest.mark.asyncio
async def test_mcp_update_profile_advertises_projects():
    """The tool description derives its section list from `VAULT_SECTIONS`;
    an agent reading the surface must see the widened vocabulary."""
    from applire.mcp.server import mcp

    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "update_profile")
    assert "projects" in tool.description
