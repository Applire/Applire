# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 7, WP-L — two facts on the letter's status object (REST + MCP, one
object, ADR-066; additive, ADR-095).

* ``profile_updated_since`` (#820 cue, founder ruling F-2): the profile's CONTENT
  changed AFTER the letter was generated — any source (interview, import, manual edit,
  agent door), never a write the generation itself causes. Felix (round 3): the
  skip-path letter stayed current under the new 79 % ring after the interview.
* ``motivation_derived`` (789:249): no motivation was typed, so the writer derived one
  (RULING G-2); the letter view must not say "no reason for the change" about it.
Synthetic data throughout.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from applire.services.profile_freshness import profile_updated_since  # noqa: E402

T0 = datetime(2026, 11, 7, 6, 53, tzinfo=timezone.utc)


def _trail(*records):
    return {"metadata": {"enrichment_history": list(records)}}


def _rec(source, minutes, changes=1):
    return {
        "id": uuid.uuid4().hex, "source": source,
        "timestamp": (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
        "changes": [{"section": "work_experience", "action": "added"}] * changes,
    }


ALL_SOURCES = ["interview", "agent_interview", "testimony", "cv_upload", "cv_paste",
               "linkedin_import", "xing_import", "manual_edit", "manual_role_add"]


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_a_content_change_after_the_letter_from_any_source_fires(source):
    assert profile_updated_since(T0, _trail(_rec(source, 40))) is True


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_a_change_before_or_at_creation_does_not(source):
    # the letter form's own notice-period save is written BEFORE the letter row
    assert profile_updated_since(T0, _trail(_rec(source, -5))) is False
    assert profile_updated_since(T0, _trail(_rec(source, 0))) is False


def test_a_record_with_no_change_does_not():
    # a receipt of what was matched / held back is not a content change
    assert profile_updated_since(T0, _trail(_rec("cv_upload", 40, changes=0))) is False


def test_generation_bookkeeping_outside_the_trail_does_not():
    profile = {"metadata": {
        "enrichment_history": [_rec("interview", -30)],
        "last_updated": (T0 + timedelta(minutes=40)).isoformat(),   # health/receipt clock
        "denied_concepts": [{"concept": "X", "date": "2026-11-07"}],
    }, "fact_pins": [{"id": "p1", "pinned_at": (T0 + timedelta(minutes=40)).isoformat()}]}
    assert profile_updated_since(T0, profile) is False


def test_a_naive_letter_timestamp_reads_as_utc_and_junk_reads_false():
    assert profile_updated_since(T0.replace(tzinfo=None), _trail(_rec("interview", 1))) is True
    assert profile_updated_since(None, _trail(_rec("interview", 1))) is False
    assert profile_updated_since(T0, {"metadata": {"enrichment_history": ["x", {"source": "interview", "timestamp": "nope", "changes": [{}]}]}}) is False
    assert profile_updated_since(T0, None) is False


# ── the seam: the status object both doors serialise ─────────────────────────


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _profile(db):
    from tests.support.profile_factory import make_master_profile

    profile = make_master_profile(id=uuid.uuid4(), profile_json=_trail(_rec("manual_edit", 40)))
    db.add(profile)
    await db.flush()
    return profile


async def _letter(db, profile, *, created_at, pre_gen):
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter

    cl = GeneratedCoverLetter(
        job_analysis_id=uuid.uuid4(), profile_id=profile.id, user_id=profile.user_id,
        template="classic_german", letter_data={"body": {"paragraphs": ["x"]}},
        pre_gen_inputs=pre_gen, status=CoverLetterStatus.ready.value, created_at=created_at,
    )
    db.add(cl)
    await db.commit()
    return cl


async def _status(db, cl):
    from applire.services.cover_letter import get_cover_letter_status

    return await get_cover_letter_status(cl.id, db, "http://x", user_id=cl.user_id)


@pytest.mark.asyncio
async def test_seam_status_carries_profile_updated_since(db):
    profile = await _profile(db)  # a manual edit changed the profile at T0 + 40 min
    stale = await _letter(db, profile, created_at=T0, pre_gen={"motivation": ""})
    fresh = await _letter(db, profile, created_at=T0 + timedelta(hours=1), pre_gen={})
    s1 = await _status(db, stale)
    s2 = await _status(db, fresh)
    assert s1.profile_updated_since is True and s2.profile_updated_since is False
    assert s1.model_dump(mode="json")["profile_updated_since"] is True  # the MCP serialisation
    assert "predates_interview" not in s1.model_dump(mode="json")


@pytest.mark.asyncio
async def test_seam_status_carries_motivation_derived_and_none_for_legacy_rows(db):
    profile = await _profile(db)
    derived = await _letter(db, profile, created_at=T0, pre_gen={"motivation": "", "motivation_derived": True})
    typed = await _letter(db, profile, created_at=T0, pre_gen={"motivation": "Ich will X.", "motivation_derived": False})
    legacy = await _letter(db, profile, created_at=T0, pre_gen={"motivation": ""})
    assert (await _status(db, derived)).motivation_derived is True
    assert (await _status(db, typed)).motivation_derived is False
    assert (await _status(db, legacy)).motivation_derived is None


def test_seam_generation_records_motivation_derived_from_the_request():
    import inspect

    from applire.services import cover_letter as svc

    src = inspect.getsource(svc.generate_cover_letter)
    assert '"motivation_derived": not (request.motivation or "").strip(),' in src
