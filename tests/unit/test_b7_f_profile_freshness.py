# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hazelnut build 7, WP-F — ADR-016 amended 2026-11-07.

789:248: ``flow_sessions.generated_cv_id`` advanced only in the BROWSER after the
CV page saw a generation finish, so regenerating and leaving the page left the
application (and the letter, which reads its CV through the flow) on the old
CV. The web door now schedules ``record_generated_cv`` after the render task.

789:254: after a reopened interview the CV page showed the OLD CV as current.
The flow state now carries ``cv_summary.predates_interview`` — the letter's L-1
predicate applied to the CV.

In-process against a real ``sqlite+aiosqlite`` session; no LLM.
"""

"""F-2 (2) (founder ruling 2026-11-07, Hazelnut build 7 WP-F): the CV page's
strip says "Dein Profil wurde seit der Erstellung dieses Lebenslaufs
aktualisiert" when the profile's CONTENT changed after the CV — from any door —
and never for writes a generation causes itself. The predicate is
`services/profile_freshness.profile_updated_since` (the name L's letter cue
uses too — one predicate), read from the enrichment trail the single committer
appends. Both directions pinned, pure and through the
real committer.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.services.profile_freshness import (
    CV_FEEDING_SECTIONS,
    INTERVIEW_SOURCES,
    profile_updated_since,
)


def profile_changed_after(created_at, profile_json):
    """The CV strip's call: the shared predicate on the CV's sections."""
    return profile_updated_since(created_at, profile_json, sections=CV_FEEDING_SECTIONS)

T0 = datetime(2026, 11, 7, 10, 0, tzinfo=timezone.utc)
WORK = {"section": "work_experience", "action": "added", "field": "responsibilities"}


def _profile(*records):
    return {"personal_info": {}, "metadata": {"enrichment_history": list(records)}}


def _rec(ts, source="manual_edit", changes=(WORK,)):
    return {"timestamp": ts.isoformat(), "source": source, "changes": list(changes)}


# ── fires: a content change after the CV, whatever the door ──────────────────


@pytest.mark.parametrize(
    "source",
    ["manual_edit", "manual_role_add", "cv_upload", "linkedin_import", "interview", "agent_interview", "testimony"],
)
def test_a_content_change_after_the_cv_from_any_door_fires(source):
    assert profile_changed_after(T0, _profile(_rec(T0 + timedelta(minutes=5), source=source))) is True


def test_a_newly_stated_limit_after_the_cv_fires():
    limit = {"section": "metadata", "field": "denied_concepts", "action": "added"}
    assert profile_changed_after(T0, _profile(_rec(T0 + timedelta(hours=1), changes=[limit]))) is True


def test_naive_and_iso_z_timestamps_are_read_as_utc():
    rec = {"timestamp": "2026-11-07T11:00:00Z", "source": "cv_upload", "changes": [WORK]}
    assert profile_changed_after(T0.replace(tzinfo=None), _profile(rec)) is True


# ── does not fire ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "record",
    [
        _rec(T0 - timedelta(minutes=1)),  # the change was in the CV already
        _rec(T0),  # same instant: not after
        _rec(T0 + timedelta(hours=1), changes=[]),  # the unconditional trail: a write with no change
        _rec(  # bookkeeping: a parked question, a receipt — metadata only
            T0 + timedelta(hours=1),
            changes=[
                {"section": "metadata", "field": "pending_confirmations", "action": "added"},
                {"section": "metadata", "field": "denied_concepts", "action": "updated"},
            ],
        ),
        _rec(  # a section the CV is not written from (the letter's notice period)
            T0 + timedelta(hours=1), changes=[{"section": "availability", "action": "updated"}]
        ),
        {"timestamp": "not a time", "source": "manual_edit", "changes": [WORK]},
    ],
    ids=["before-cv", "same-instant", "empty-write", "metadata-bookkeeping", "availability", "unreadable-ts"],
)
def test_no_content_change_after_the_cv_does_not_fire(record):
    assert profile_changed_after(T0, _profile(record)) is False


def test_an_unreadable_document_time_or_profile_never_fires():
    rec = _rec(T0 + timedelta(hours=1))
    assert profile_changed_after(None, _profile(rec)) is False
    assert profile_changed_after("garbage", _profile(rec)) is False
    assert profile_changed_after(T0, None) is False
    assert profile_changed_after(T0, {"metadata": {}}) is False


def test_the_default_scope_is_every_profile_section_but_never_bookkeeping():
    """L's letter calls `profile_updated_since(created_at, profile_json)`: any
    profile section counts (its notice period, `availability`, included); the
    system's metadata receipts never do — a resolved question, a re-confirmed
    limit."""
    after = T0 + timedelta(hours=1)
    availability = _rec(after, changes=[{"section": "availability", "action": "updated"}])
    assert profile_updated_since(T0, _profile(availability)) is True
    assert profile_updated_since(T0, _profile(_rec(after))) is True
    for receipt in (
        {"section": "metadata", "field": "pending_confirmations", "action": "updated"},
        {"section": "metadata", "field": "denied_concepts", "action": "updated"},
        {"section": "_meta", "field": "na", "action": "added"},
        {"action": "added"},
    ):
        assert profile_updated_since(T0, _profile(_rec(after, changes=[receipt]))) is False, receipt


def test_sources_narrow_to_the_interview_doors_for_the_auto_start():
    after = T0 + timedelta(hours=1)
    for source in ("cv_upload", "manual_edit", "linkedin_import"):
        assert profile_updated_since(T0, _profile(_rec(after, source=source)), sources=INTERVIEW_SOURCES) is False
    for source in INTERVIEW_SOURCES:
        assert profile_updated_since(T0, _profile(_rec(after, source=source)), sources=INTERVIEW_SOURCES) is True


def test_the_premise_only_three_modules_may_write_the_profile():
    """The 'a generation's own writes never count' half rests on this: profile
    CONTENT reaches the row only through the committer (which appends the trail
    record read here), a snapshot restore, or the photo service. A fourth writer
    must revisit `profile_freshness` — this test is where that is noticed."""
    from applire.models.profile import AUTHORIZED_PROFILE_WRITE_MODULES

    assert AUTHORIZED_PROFILE_WRITE_MODULES == frozenset(
        {
            "applire/services/profile/commit.py",
            "applire/services/profile/snapshots.py",
            "applire/services/photo.py",
        }
    )


def test_the_dossier_nudge_reads_the_same_rule():
    """ADR-066: E039's stale_cv and this strip share the section list and the
    limit test — the same objects, not copies."""
    from applire.services import application, profile_freshness

    assert application.CV_FEEDING_SECTIONS is profile_freshness.CV_FEEDING_SECTIONS
    assert application._is_new_limit is profile_freshness.is_new_limit


# ── through the real committer ───────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base
    from applire.models.profile import MasterProfile, ProfileSnapshot

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _vault(db_session):
    from applire.models.profile import MasterProfile, authorized_profile_write

    with authorized_profile_write():
        record = MasterProfile(
            profile_json={"personal_info": {"name": "Felix Brandt"}, "skills": [], "metadata": {}}
        )
    db_session.add(record)
    await db_session.commit()
    return record


@pytest.mark.asyncio
async def test_a_manual_edit_through_the_committer_after_the_cv_fires(db_session):
    from applire.services.profile.commit import CommitProvenance, commit_ops
    from applire.services.profile.reconcile.ops import UpsertSkill

    record = await _vault(db_session)
    cv_created_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    assert profile_changed_after(cv_created_at, record.profile_json) is False

    await commit_ops(
        db_session,
        [UpsertSkill(name="Terraform", category="technical")],
        CommitProvenance(source="manual_edit", intake="skill_edit", session_id=str(uuid.uuid4())),
        record=record,
    )
    await db_session.commit()
    await db_session.refresh(record)

    assert profile_changed_after(cv_created_at, record.profile_json) is True
    # …and a CV generated after that edit is current again.
    assert profile_changed_after(datetime.now(timezone.utc) + timedelta(seconds=1), record.profile_json) is False
