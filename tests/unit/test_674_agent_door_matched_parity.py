# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#674 line 72 — the `matched` receipt on the agent door (ADR-063 door parity).

#707/#708 mint a `MatchReceipt` when the reconciler recognises an incoming
entry as one the vault already holds under another surface form. It rode
`ApplyResult.matched` → `EnrichmentRecord.matched` to the profile page's
history and reached no agent-door envelope, so an agent importing a translated
second CV saw entries absent from `changes`, some of them named in
`not_applied`, and had no statement of WHY (founder's edge run, 2026-09-18) —
which the guide then makes it report to the human as a loss.

Three envelopes, three tests, plus the black-box split between them:
`import_cv` states the fact about the caller's OWN submission, and only
`get_profile_health` / `submit_testimony` add the vault's own wording.
"""
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.schemas.profile import EnrichmentRecord, MatchReceipt
from tests.support.profile_factory import make_master_profile

# A synthetic translated pair with the SAME SHAPE as the edge finding (never the
# founder's own data): an incoming flat-section entry whose vault twin carries
# the other language's surface form.
_INCOMING = "English"
_EXISTING = "Englisch"


def _receipt(section="languages", incoming=_INCOMING, existing=_EXISTING):
    return MatchReceipt(
        section=section,
        entity_id=str(uuid.uuid4()),
        incoming=incoming,
        existing=existing,
    )


@pytest_asyncio.fixture
async def db():
    import importlib
    import pkgutil

    import applire.models  # noqa: F401

    for _m in pkgutil.iter_modules(applire.models.__path__):
        importlib.import_module(f"applire.models.{_m.name}")
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _db_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# import_cv — the black-box door
# ---------------------------------------------------------------------------


def _import_response(matched):
    from applire.schemas.profile import MasterProfileData, ProfileImportResponse

    now = datetime.now(timezone.utc)
    return ProfileImportResponse(
        id=uuid.uuid4(),
        profile=MasterProfileData(),
        completeness=0.5,
        created_at=now,
        updated_at=now,
        merge_status="partial",
        not_applied=[],
        matched=matched,
    )


@pytest.mark.asyncio
async def test_import_cv_states_what_the_merge_recognised_as_already_present():
    """The defect: the summary carried `merge_status` + `not_applied` and no
    `matched`, so a `partial` import read as pure loss."""
    from applire.mcp.server import import_cv

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.get_storage"),
        patch("applire.mcp.server._import_user_id", AsyncMock(return_value=None)),
        patch(
            "applire.mcp.server.profile_svc.import_from_text",
            AsyncMock(return_value=_import_response([_receipt()])),
        ),
    ):
        summary = await import_cv(text="Sprachen: English (fliessend)")

    assert summary["matched"] == [{"section": "languages", "incoming": _INCOMING}]


@pytest.mark.asyncio
async def test_import_cvs_matched_never_carries_the_vaults_own_wording():
    """The black-box invariant (`tests/test_mcp_agent_journey.py`): this door
    returns a summary of the caller's OWN submission, never vault content it did
    not submit. `incoming` is the caller's word; `existing` and `entity_id` are
    the vault's, and a `match_existing` may legally aim at an engagement id — so
    a leak here would be work-history prose on the one payload that forbids it.
    Mutation kill: flip `reveal_existing` to True in `_profile_summary` and this
    test fails."""
    from applire.mcp.server import import_cv

    receipt = _receipt()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.get_storage"),
        patch("applire.mcp.server._import_user_id", AsyncMock(return_value=None)),
        patch(
            "applire.mcp.server.profile_svc.import_from_text",
            AsyncMock(return_value=_import_response([receipt])),
        ),
    ):
        summary = await import_cv(text="Sprachen: English (fliessend)")

    blob = json.dumps(summary)
    assert _EXISTING not in blob, "the vault's own label leaked onto the black-box door"
    assert receipt.entity_id not in blob, "an internal vault id leaked"
    assert _INCOMING in blob, "the caller's own word is the point of the receipt"


@pytest.mark.asyncio
async def test_import_cv_reports_an_empty_list_when_nothing_was_recognised():
    """Negative control: the key is always present (a caller branches on a
    field, never on the absence of one — #367's rule for this payload)."""
    from applire.mcp.server import import_cv

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch("applire.mcp.server.get_storage"),
        patch("applire.mcp.server._import_user_id", AsyncMock(return_value=None)),
        patch(
            "applire.mcp.server.profile_svc.import_from_text",
            AsyncMock(return_value=_import_response([])),
        ),
    ):
        summary = await import_cv(text="Sprachen: English")

    assert summary["matched"] == []


# ---------------------------------------------------------------------------
# get_profile_health — the door that MAY name the vault's wording
# ---------------------------------------------------------------------------


async def _profile_with_history(db, records):
    profile = make_master_profile(
        profile_json={
            "personal_info": {"name": "Mira Vogel"},
            "work_experience": [],
            "metadata": {
                "enrichment_history": [r.model_dump(mode="json") for r in records]
            },
        }
    )
    db.add(profile)
    await db.commit()
    return profile


@pytest.mark.asyncio
async def test_profile_health_reports_the_last_writes_matched_receipts(db):
    """The hub's third fact beside the issues and the held merges. Here the
    vault's own label IS included: this door already returns `account_name` /
    `cv_name`, so it is the door an agent is sent to for exactly that."""
    from applire.mcp.server import get_profile_health

    await _profile_with_history(
        db,
        [
            EnrichmentRecord(
                timestamp=datetime.now(timezone.utc),
                source="cv_upload",
                matched=[_receipt()],
            )
        ],
    )
    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)):
        payload = await get_profile_health()

    assert payload["recent_matched"] == [
        {"section": "languages", "incoming": _INCOMING, "existing": _EXISTING}
    ]


@pytest.mark.asyncio
async def test_profile_health_reports_only_the_most_recent_write(db):
    """Scope control: the hub answers "why is my last import's entry missing",
    not "everything ever recognised"."""
    from applire.mcp.server import get_profile_health

    await _profile_with_history(
        db,
        [
            EnrichmentRecord(
                timestamp=datetime.now(timezone.utc),
                source="cv_upload",
                matched=[_receipt(section="skills", incoming="Old", existing="Alt")],
            ),
            EnrichmentRecord(
                timestamp=datetime.now(timezone.utc),
                source="linkedin_import",
                matched=[_receipt()],
            ),
        ],
    )
    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)):
        payload = await get_profile_health()

    assert [m["incoming"] for m in payload["recent_matched"]] == [_INCOMING]


@pytest.mark.asyncio
async def test_profile_health_on_a_fresh_install_reports_an_empty_list(db):
    """No profile at all — the health door never crashes on one (its own rule)."""
    from applire.mcp.server import get_profile_health

    with patch("applire.mcp.server.get_db", return_value=_db_cm(db)):
        payload = await get_profile_health()
    assert payload["recent_matched"] == []


# ---------------------------------------------------------------------------
# submit_testimony
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_testimony_carries_the_batchs_match_receipts():
    """A restatement turn writes nothing and must never address a gap
    (`MatchReceipt`'s docstring) — which is precisely why the agent needs the
    receipt: `changes` is empty and the reason is not."""
    from applire.schemas.testimony import TestimonyResult
    from applire.mcp.server import submit_testimony

    result = TestimonyResult(
        submission_id=str(uuid.uuid4()),
        status="no_change",
        changes=[],
        matched=[_receipt(section="certifications", incoming="ITIL v4", existing="ITIL 4 Foundation")],
    )
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
    cm.__aexit__ = AsyncMock(return_value=False)
    with (
        patch("applire.mcp.server.get_db", return_value=cm),
        patch("applire.mcp.server.get_provider"),
        patch(
            "applire.services.profile.reconcile.testimony_bridge.submit_testimony",
            AsyncMock(return_value=result),
        ),
    ):
        payload = await submit_testimony(text="I hold ITIL v4.")

    assert payload["matched"] == [
        {
            "section": "certifications",
            "incoming": "ITIL v4",
            "existing": "ITIL 4 Foundation",
        }
    ]
    assert "entity_id" not in payload["matched"][0], "an internal vault id leaked"


# ---------------------------------------------------------------------------
# update_profile — the refuted premise
# ---------------------------------------------------------------------------


def test_update_profile_cannot_produce_a_match_receipt():
    """#674 line 72 names `update_profile` as a fourth envelope. REFUTED: that
    door is a field edit. `patch_profile_section` builds exactly one op
    (`build_replace_section_op` → `ReplaceSection`, adapter-only), while
    `ApplyResult.matched` is filled only by `MatchExisting` — an op solely the
    RECONCILER may emit (`ReconcileOp`) — and by `ApplyImportMerge`, the
    import's whole-merge act. A `matched` field on that envelope would be
    always-empty, i.e. a claim the door cannot make. Pinned rather than written:
    if a replace ever becomes reconciled, this test says so."""
    from applire.services.profile.field_edit import build_replace_section_op
    from applire.services.profile.reconcile.ops import (
        ApplyImportMerge,
        MatchExisting,
        ReplaceSection,
    )

    op = build_replace_section_op("skills", [])
    assert isinstance(op, ReplaceSection)
    assert not isinstance(op, (MatchExisting, ApplyImportMerge))


# ---------------------------------------------------------------------------
# The two service seams the door payloads ride on (ADR-058 clause 2: one act,
# and the REST import door / profile history read the same field)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_import_door_carries_the_merges_matched_receipts(db):
    """Seam 1 — `_import_from_text`: the merge outcome's receipts reach
    `ProfileImportResponse`. Mutation kill: drop `matched=outcome.merge.matched`
    and this test fails (the door then reports an empty list for a merge that
    recognised an entry)."""
    from types import SimpleNamespace

    import applire.services.profile as profile_svc

    record = make_master_profile(profile_json={"personal_info": {}, "work_experience": []})
    db.add(record)
    await db.commit()

    outcome = SimpleNamespace(
        held=False,
        merge=SimpleNamespace(
            merge_status="partial",
            not_applied=[],
            not_applied_loss_count=0,  # F-7 (#674) — new ApplyMergeOutcome field
            matched=[_receipt()],
        ),
    )
    with patch.object(profile_svc, "ingest_cv", AsyncMock(return_value=outcome)):
        response = await profile_svc._import_from_text(
            "Sprachen: English",
            db,
            MagicMock(),
            storage=MagicMock(),
            source_bytes=b"",
            filename="import.txt",
            content_type="text/plain",
        )

    assert [m.incoming for m in response.matched] == [_INCOMING]
    assert [m.existing for m in response.matched] == [_EXISTING]


@pytest.mark.asyncio
async def test_the_testimony_bridge_carries_the_committed_records_receipts(db):
    """Seam 2 — `testimony_bridge`: `matched` comes off the SAME committed
    record as `changes`, so the two can never disagree about one batch.
    Mutation kill: drop the `matched=` line and this test fails."""
    from types import SimpleNamespace

    import applire.services.profile.reconcile.testimony_bridge as bridge

    record = make_master_profile(profile_json={"personal_info": {}, "work_experience": []})
    db.add(record)
    await db.commit()

    rc = SimpleNamespace(
        ops=[], rejected_ops=[], denials=[], empty_reason=None, ambiguities=[]
    )
    committed = SimpleNamespace(
        enrichment_record=EnrichmentRecord(
            timestamp=datetime.now(timezone.utc),
            source="testimony",
            changes=[],
            matched=[_receipt(section="certifications", incoming="ITIL v4", existing="ITIL 4 Foundation")],
        ),
        pending_confirmations=[],
        conflicts=[],
        denials=[],
        changes=[],
    )
    with (
        patch.object(bridge, "reconcile", AsyncMock(return_value=rc)),
        patch.object(bridge, "commit_ops", AsyncMock(return_value=committed)),
    ):
        result = await bridge.submit_testimony("I hold ITIL v4.", db, MagicMock())

    assert [m.incoming for m in result.matched] == ["ITIL v4"]
    assert result.changes == [], (
        "a restatement writes nothing — the receipt must not become a change"
    )
