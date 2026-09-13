# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#367 — ONE ingest function behind every CV-ingestion door (ADR-066 cl. 1–3;
ADR-041 amended 2026-09-13; ruling A-4 / ruling V-2).

`evaluate_merge_gate` used to have exactly one call site — inside `upload_cv`,
the browser CV-upload door. Two other doors ingest a CV and called it from
nowhere: MCP `import_cv` (the agent door) and `POST /api/profile/import`
(the LinkedIn/XING structured export), both through `_import_from_text`. On
those doors an arbitrary document — a JD, a slide deck, another person's CV —
merged into the vault unheld, with no source file, no `UploadRecord`, no GDPR
retention row and no audit trail of what produced a vault claim
(SF-DOOR.2 / SF-DOOR.3).

This file is the **seam suite the fix owes**: one test per DOOR per GATE BRANCH
(3 x 2 = 6), driving the real service path, asserting on what was persisted —
plus the `UploadRecord`/Art.-15 half on every door, and the two additive
response shapes the doors adapt the outcome into.

It is NOT the ADR-066 clause 5 behavioural parity suite (arc42 §8.8), which is
still unbuilt and must not be credited anywhere: this proves ONE operation
converged, not the class.

Mutation contract (how these six are killed):
  * delete the `evaluate_merge_gate` call in `ingest_cv`             -> all 6 gate tests red
  * force `gate.gate = "none"` after evaluating it                   -> all 6 gate tests red
  * drop the `_persist_upload_record` call on the merge branch       -> the 3 audit-trail tests red
  * give any ONE door back its own pipeline                          -> that door's 2 tests red
"""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def sqlite_session():
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot
    from applire.models.uploads import UploadRecord
    from applire.models.user import User
    from applire.models.user_settings import UserSettings  # US184: get_ui_language

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[
                    MasterProfile.__table__,
                    ProfileSnapshot.__table__,
                    UploadRecord.__table__,
                    User.__table__,
                    UserSettings.__table__,
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def storage(tmp_path):
    from applire.storage.local import LocalStorageProvider

    return LocalStorageProvider(str(tmp_path))


def _cv(name: str, *, with_content: bool = True) -> dict:
    """A minimal extraction. `with_content=False` is what a JD / slide deck
    extracts to, and is what `looks_like_cv()` rejects."""
    data: dict = {"personal_info": {"name": name}}
    if with_content:
        data["work_experience"] = [
            {"company": "Acme GmbH", "role": "Engineer", "start_date": "2020-01"}
        ]
        data["skills"] = [{"name": "Python", "category": "technical"}]
    return data


def _provider(extraction: dict):
    provider = AsyncMock()
    provider.__class__.__name__ = "MockProvider"
    provider.aparse_json.return_value = extraction
    return provider


def _stub_llm_layers():
    """Patch only the LLM layers — the gate, the merge, the UploadRecord write
    and every door adapter run for real. `extract_with_fallback` is patched at
    the ingest's own import site so all three doors share the double."""
    return (
        patch(
            "applire.services.profile.extract_with_fallback",
            new=AsyncMock(side_effect=lambda raw, prov, **kw: prov.aparse_json.return_value),
        ),
        patch(
            "applire.services.profile.review_and_refine",
            new=AsyncMock(side_effect=lambda **kw: kw["draft"]),
        ),
        patch(
            "applire.services.profile.enrich_skills",
            new=AsyncMock(side_effect=lambda p, _: p),
        ),
        patch(
            "applire.services.profile.annotate_expected_fields",
            new=AsyncMock(return_value=None),
        ),
    )


# ── the three doors, as the three adapters over the one ingest ────────────────
#
# Each returns the door's OWN response object, so a test that reads `.status`,
# `.gate` and `.staged_id` is reading what that door really answers.


async def _door_browser(session, storage, extraction, *, user_id=None):
    """`POST /api/profile/upload` — the browser CV-upload door."""
    from applire.services.profile import upload_cv

    patches = _stub_llm_layers()
    with patches[0], patches[1], patches[2], patches[3], patch(
        "applire.services.cv_parser.extract_text",
        new=AsyncMock(return_value="raw cv text"),
    ):
        return await upload_cv(
            file_bytes=b"fake-pdf-bytes",
            filename="cv.pdf",
            content_type="application/pdf",
            db=session,
            provider=_provider(extraction),
            storage=storage,
            ocr_extractor=AsyncMock(),
            user_id=user_id,
        )


async def _door_agent(session, storage, extraction, *, user_id=None):
    """MCP `import_cv(text=...)` — the agent door."""
    from applire.services.profile import import_from_text

    patches = _stub_llm_layers()
    with patches[0], patches[1], patches[2], patches[3]:
        return await import_from_text(
            "raw cv text",
            session,
            _provider(extraction),
            storage=storage,
            filename="agent-import.txt",
            user_id=user_id,
        )


async def _door_linkedin(session, storage, extraction, *, user_id=None):
    """`POST /api/profile/import` — the LinkedIn/XING structured-export door.

    The third door. It shares `_import_from_text` with the agent door and
    therefore shared both bypasses; no analysis of #367 named it until 2026-09-13
    (ruling V-2), because §5.3.24's matrix joins MCP tools to REST endpoints and
    a REST door whose MCP counterpart is a different tool has no row.
    """
    from applire.services.profile import import_from_linkedin

    patches = _stub_llm_layers()
    with patches[0], patches[1], patches[2], patches[3]:
        return await import_from_linkedin(
            {"firstName": "Katrin", "lastName": "Hoffmann"},
            session,
            _provider(extraction),
            storage=storage,
            user_id=user_id,
        )


DOORS = [
    pytest.param(_door_browser, id="browser_upload"),
    pytest.param(_door_agent, id="agent_import_cv"),
    pytest.param(_door_linkedin, id="linkedin_export"),
]


async def _seed_profile(session, storage, name="Marcus Schmidt"):
    """One clean import so the account has a name for the divergence check."""
    result = await _door_browser(session, storage, _cv(name))
    assert result.status != "GATED", "seeding import must not itself be held"
    return result


async def _profile_count(session):
    from applire.models.profile import MasterProfile

    rows = (await session.execute(select(MasterProfile))).scalars().all()
    return len(rows)


# ── 1. The gate branch `not_a_cv` — one test per door ─────────────────────────


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.asyncio
async def test_not_a_cv_is_held_on_every_door(door, sqlite_session, storage):
    """A document that extracts to nothing is HELD, whichever door submitted it.

    Before #367 this merged silently on two of the three doors.
    """
    from applire.models.uploads import UploadRecord

    result = await door(sqlite_session, storage, _cv("Anna Bauer", with_content=False))

    assert result.status == "GATED", f"{door.__name__} merged a not-a-CV document"
    assert result.gate == "not_a_cv"
    assert result.staged_id is not None, "nothing to resolve — the hold is unreachable"
    assert result.looks_like_cv is False
    assert result.profile_id is None

    # Nothing reached the vault.
    assert await _profile_count(sqlite_session) == 0

    # The extraction is parked on the upload row and re-merges without a second
    # LLM call (ADR-041 amendment point 3).
    rec = (
        await sqlite_session.execute(
            select(UploadRecord).where(UploadRecord.id == result.staged_id)
        )
    ).scalar_one()
    assert rec.gate_status == "not_a_cv"
    assert rec.staged_extraction["personal_info"]["name"] == "Anna Bauer"


# ── 2. The gate branch `name_divergence` — one test per door ──────────────────


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.asyncio
async def test_name_divergence_is_held_on_every_door(door, sqlite_session, storage):
    """Another person's CV is HELD, whichever door submitted it.

    The identity question is the user's (ADR-041: the system detects difference,
    the user adjudicates). Before #367 two of three doors merged a foreign CV
    into the vault, where the Oracle would thereafter treat it as grounded.
    """
    from applire.models.profile import MasterProfile

    await _seed_profile(sqlite_session, storage, "Marcus Schmidt")
    before = (
        await sqlite_session.execute(select(MasterProfile))
    ).scalars().first().profile_json

    result = await door(sqlite_session, storage, _cv("Anna Bauer"))

    assert result.status == "GATED", f"{door.__name__} merged a foreign CV"
    assert result.gate == "name_divergence"
    assert result.name_mismatch is True
    assert result.staged_id is not None
    assert result.account_name == "Marcus Schmidt"
    assert result.cv_name == "Anna Bauer"

    after = (
        await sqlite_session.execute(select(MasterProfile))
    ).scalars().first().profile_json
    assert after == before, "a held merge must leave the vault byte-identical"


# ── 3. A clean CV still merges on every door (no friction added) ──────────────


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.asyncio
async def test_a_clean_cv_still_merges_on_every_door(door, sqlite_session, storage):
    result = await door(sqlite_session, storage, _cv("Marcus Schmidt"))

    assert getattr(result, "gate", "none") == "none"
    assert getattr(result, "status", None) != "GATED"
    assert await _profile_count(sqlite_session) == 1


# ── 4. SF-DOOR.3 — the audit trail exists on every door ───────────────────────


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.asyncio
async def test_every_door_persists_an_upload_record(door, sqlite_session, storage):
    """GDPR Art. 15 / ADR-005: every ingest leaves exactly one retention row
    naming the source document — hash, mime type, byte size, provider, expiry.

    Before #367 only the browser door wrote one, so an access request
    under-reported what had been ingested through the other two.
    """
    from applire.models.uploads import UploadRecord

    user_id = uuid.uuid4()
    await door(sqlite_session, storage, _cv("Marcus Schmidt"), user_id=user_id)

    rows = (await sqlite_session.execute(select(UploadRecord))).scalars().all()
    assert len(rows) == 1, f"{door.__name__} wrote {len(rows)} upload records"
    rec = rows[0]
    assert rec.user_id == user_id, "an ownerless row is invisible to the scoped lists"
    assert len(rec.content_hash) == 64, "SHA-256 of the source document"
    assert rec.byte_size > 0
    assert rec.mime_type
    assert rec.file_path, "the source document itself must be stored"
    assert rec.llm_provider == "MockProvider"
    assert rec.expires_at is not None, "no expiry = no retention sweep (ADR-005)"
    assert rec.gate_status is None, "a clean merge parks nothing"


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.asyncio
async def test_a_held_import_is_also_recorded_and_resolvable(door, sqlite_session, storage):
    """A HOLD is an ingest too: it keeps its source document AND becomes visible
    to `list_open_gates`, which is what `get_profile_health.held_merges` and the
    browser's uploads list both read. One parking place, whichever door held it.
    """
    from applire.services.profile import list_open_gates, resolve_staged_extraction

    user_id = uuid.uuid4()
    await _seed_profile(sqlite_session, storage, "Marcus Schmidt")
    result = await door(sqlite_session, storage, _cv("Anna Bauer"), user_id=user_id)

    open_gates = await list_open_gates(sqlite_session, user_id=user_id)
    assert [r.id for r in open_gates] == [result.staged_id]
    assert open_gates[0].file_path, "the held source document must be stored too"

    # And the ONE resolver resolves it, regardless of which door raised it.
    resolved = await resolve_staged_extraction(
        sqlite_session, result.staged_id, action="discard", user_id=user_id
    )
    assert resolved.action == "discard"
    assert await list_open_gates(sqlite_session, user_id=user_id) == []


# ── 5. The response shapes the doors adapt the one outcome into ───────────────


@pytest.mark.asyncio
async def test_a_hold_is_the_same_object_on_every_door(sqlite_session, storage):
    """ADR-066 cl. 2 read at the response layer: a held merge is ONE
    representation, not three — same class, same field names, same values but
    for the door-local filename."""
    from applire.schemas.profile import CVUploadResponse

    results = []
    for door in (_door_browser, _door_agent, _door_linkedin):
        # A fresh not-a-CV hold per door; no profile exists, so nothing carries over.
        results.append(await door(sqlite_session, storage, _cv("X", with_content=False)))

    assert all(isinstance(r, CVUploadResponse) for r in results)
    shapes = {
        (r.status, r.gate, r.looks_like_cv, r.name_mismatch, r.profile_id)
        for r in results
    }
    assert len(shapes) == 1, f"doors disagree about what a hold looks like: {shapes}"


@pytest.mark.asyncio
async def test_merged_import_response_carries_completeness_score(sqlite_session, storage):
    """#367 / ruling V-2 — `ProfileImportResponse` gained `completeness_score`,
    the name every import caller already reads (`ProfileImportView` posts a
    LinkedIn export here and then reads `data.completeness_score`)."""
    result = await _door_linkedin(sqlite_session, storage, _cv("Marcus Schmidt"))

    assert result.completeness_score is not None
    assert result.completeness_score == result.completeness, "one number, two names"


# ── 6. The agent door's tool payload (ADR-054 amended) ────────────────────────


@pytest.mark.asyncio
async def test_import_cv_tool_reports_a_hold_additively_and_without_names(
    sqlite_session, storage
):
    """MCP `import_cv` on a HOLD: `merged=false, gated=true, staged_id,
    hold_reason` — and NOT the two names.

    The black-box invariant (`tests/test_mcp_agent_journey.py`) is why this tool
    returns a summary at all; a hold is not a licence to break it. The agent
    reads `get_profile_health().held_merges[]` for `account_name`/`cv_name` when
    it puts the identity question to the human.
    """
    from applire.mcp.server import _held_import_summary, _profile_summary

    await _seed_profile(sqlite_session, storage, "Marcus Schmidt")
    held = await _door_agent(sqlite_session, storage, _cv("Anna Bauer"))
    payload = _held_import_summary(held)

    assert payload == {
        "merged": False,
        "gated": True,
        "staged_id": str(held.staged_id),
        "hold_reason": "name_divergence",
    }
    leaked = str(payload).lower()
    for pii in ("anna", "bauer", "marcus", "schmidt"):
        assert pii not in leaked, f"black-box violation: {pii!r} in the hold payload"

    # The merged branch states the same two facts, so the caller branches on a
    # field rather than on the absence of one.
    merged = await _door_agent(sqlite_session, storage, _cv("Marcus Schmidt"))
    summary = _profile_summary(merged)
    assert summary["merged"] is True and summary["gated"] is False
