# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 7, WP-L — two facts on the letter's status object (REST + MCP, one
object, ADR-066; additive, ADR-095).

* ``predates_interview`` (RULING L-1, #820): an interview door added facts to the
  profile AFTER the letter was generated. Felix (round 3): the skip-path letter stayed
  current under the new 79 % ring after the interview, and nothing said so.
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

from applire.services.cover_letter_positioning import letter_predates_interview  # noqa: E402

T0 = datetime(2026, 11, 7, 6, 53, tzinfo=timezone.utc)


def _trail(*records):
    return {"metadata": {"enrichment_history": list(records)}}


def _rec(source, minutes, changes=1):
    return {
        "id": uuid.uuid4().hex, "source": source,
        "timestamp": (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
        "changes": [{"section": "work_experience", "action": "added"}] * changes,
    }


@pytest.mark.parametrize("source", ["interview", "agent_interview", "testimony"])
def test_an_interview_fact_after_the_letter_predates_it(source):
    assert letter_predates_interview(T0, _trail(_rec(source, 40))) is True


def test_facts_before_the_letter_do_not():
    assert letter_predates_interview(T0, _trail(_rec("interview", -5))) is False


@pytest.mark.parametrize("source", ["cv_upload", "manual_edit", "linkedin_import"])
def test_a_non_interview_source_does_not(source):
    assert letter_predates_interview(T0, _trail(_rec(source, 40))) is False


def test_an_interview_record_with_no_change_does_not():
    assert letter_predates_interview(T0, _trail(_rec("interview", 40, changes=0))) is False


def test_a_naive_letter_timestamp_reads_as_utc_and_junk_reads_false():
    assert letter_predates_interview(T0.replace(tzinfo=None), _trail(_rec("interview", 1))) is True
    assert letter_predates_interview(None, _trail(_rec("interview", 1))) is False
    assert letter_predates_interview(T0, {"metadata": {"enrichment_history": ["x", {"source": "interview", "timestamp": "nope", "changes": [{}]}]}}) is False
    assert letter_predates_interview(T0, None) is False


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

    profile = make_master_profile(id=uuid.uuid4(), profile_json=_trail(_rec("interview", 40)))
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
async def test_seam_status_carries_predates_interview(db):
    profile = await _profile(db)  # the interview added a fact at T0 + 40 min
    stale = await _letter(db, profile, created_at=T0, pre_gen={"motivation": ""})
    fresh = await _letter(db, profile, created_at=T0 + timedelta(hours=1), pre_gen={})
    s1 = await _status(db, stale)
    s2 = await _status(db, fresh)
    assert s1.predates_interview is True and s2.predates_interview is False
    assert s1.model_dump(mode="json")["predates_interview"] is True  # the MCP serialisation


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
