# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""R-2b (UAT-3 review cards): "Passt so" records ``kept`` on ``send:terminal-review``.

The decision's ``basis`` is the hash of the check's persisted ``unreviewed_changes``,
read server-side. Nothing in the document or its report changes."""
import copy
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.owners import HARNESS_USER_ID
from tests.support.profile_factory import make_master_profile

KEY = "send:terminal-review"
HASH_A = "aaaaaaaaaaaaaaaa"
HASH_B = "bbbbbbbbbbbbbbbb"
DELTA = [{"section_id": "body", "before": "Old.", "after": "New."}]


def _report(details_key="terminal-review-unverified", hash_=HASH_A, delta=DELTA):
    return {
        "checks": [
            {"id": "terminal-review", "details_key": details_key,
             "unreviewed_changes": delta, "unreviewed_changes_hash": hash_},
            {"id": "contact-name"},
        ]
    }


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    user_id = HARNESS_USER_ID
    job_id = uuid.uuid4()
    db.add_all([
        User(id=user_id, email="r2b@test.com"),
        JobAnalysis(
            id=job_id, raw_text_hash="r2bhash", raw_text="Qualitätsmanager",
            role_title="Qualitätsmanager", required_skills=["ISO 9001"], nice_to_have_skills=[],
            keywords=["ISO 9001"], seniority_level="senior", company_culture_signals=[],
            language_requirement="de",
        ),
        (profile := make_master_profile(profile_json={}, user_id=user_id)),
    ])
    await db.flush()
    return {"db": db, "user_id": user_id, "job_id": job_id, "profile_id": profile.id}


async def _letter(seeded, report, *, user_id=None):
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter

    cl_id = uuid.uuid4()
    seeded["db"].add(GeneratedCoverLetter(
        id=cl_id, job_analysis_id=seeded["job_id"], profile_id=seeded["profile_id"],
        user_id=user_id or seeded["user_id"], template="classic_german", letter_data={},
        pre_gen_inputs={}, status=CoverLetterStatus.ready.value, ats_report=report,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    ))
    await seeded["db"].commit()
    return cl_id


async def _keep(seeded, cl_id, key=KEY, keep=True, user_id=None):
    from applire.services import review_signals

    return await review_signals.keep(
        "cover_letter", cl_id, key, seeded["db"], keep=keep, user_id=user_id or seeded["user_id"]
    )


@pytest.mark.asyncio
async def test_kept_records_terminal_review_decision(seeded):
    report = _report()
    cl_id = await _letter(seeded, report)
    before = copy.deepcopy(report)
    out = await _keep(seeded, cl_id)
    decisions = out.record.review_state["decisions"]
    assert len(decisions) == 1
    d = decisions[0]
    assert (d["finding_key"], d["action"], d["basis"]) == (KEY, "kept", HASH_A)
    assert out.record.ats_report == before  # no re-audit, byte-identical


@pytest.mark.asyncio
async def test_kept_on_a_legacy_check_records_basis_none(seeded):
    cl_id = await _letter(seeded, _report("terminal-review-unverified-clean", None, None))
    d = (await _keep(seeded, cl_id)).record.review_state["decisions"][0]
    assert d["action"] == "kept" and d["basis"] is None


@pytest.mark.asyncio
async def test_kept_withdraw(seeded):
    cl_id = await _letter(seeded, _report())
    await _keep(seeded, cl_id)
    out = await _keep(seeded, cl_id, keep=False)
    assert out.record.review_state["decisions"] == []


@pytest.mark.asyncio
async def test_kept_rejects_other_ats_keys(seeded):
    cl_id = await _letter(seeded, _report())
    with pytest.raises(ValueError):
        await _keep(seeded, cl_id, key="ats:keyword-x")


@pytest.mark.asyncio
async def test_kept_rejects_other_send_keys(seeded):
    cl_id = await _letter(seeded, _report())
    for key in ("send:document-language", "send:terminal-review-x"):
        with pytest.raises(ValueError):
            await _keep(seeded, cl_id, key=key)


@pytest.mark.asyncio
@pytest.mark.parametrize("details_key", [
    "terminal-review-clean", "terminal-review-open", "terminal-review-not-run", None,
])
async def test_kept_rejects_a_check_that_is_not_unverified(seeded, details_key):
    cl_id = await _letter(seeded, _report(details_key))
    with pytest.raises(ValueError):
        await _keep(seeded, cl_id)


@pytest.mark.asyncio
async def test_kept_rejects_a_document_without_the_check(seeded):
    cl_id = await _letter(seeded, {"checks": [{"id": "contact-name"}]})
    with pytest.raises(ValueError):
        await _keep(seeded, cl_id)
    cl_id = await _letter(seeded, None)
    with pytest.raises(ValueError):
        await _keep(seeded, cl_id)


@pytest.mark.asyncio
async def test_new_version_starts_unacknowledged(seeded):
    first = await _letter(seeded, _report())
    await _keep(seeded, first)
    second = await _letter(seeded, _report())
    from applire.models.cover_letter import GeneratedCoverLetter

    rec = await seeded["db"].get(GeneratedCoverLetter, second)
    assert not (rec.review_state or {}).get("decisions")


@pytest.mark.asyncio
async def test_kept_cross_user_404(seeded):
    cl_id = await _letter(seeded, _report())
    with pytest.raises(LookupError):
        await _keep(seeded, cl_id, user_id=uuid.uuid4())


@pytest.mark.asyncio
async def test_kept_does_not_touch_group_one_counts(seeded):
    from applire.services import review_state as rs

    report = _report()
    cl_id = await _letter(seeded, report)
    base = rs.derive_review(None, report, None)
    out = await _keep(seeded, cl_id)
    after = rs.derive_review(out.record.review_state, report, None)
    assert after == base  # the send decision never moves group 1's "k of n decided"
    assert not [d for d in rs.load_state(out.record.review_state)["decisions"]
                if rs.split_key(d["finding_key"])[0] in rs.GROUP_ONE_PRODUCERS]


@pytest.mark.asyncio
async def test_kept_void_when_delta_changes(seeded):
    from applire.models.cover_letter import GeneratedCoverLetter

    cl_id = await _letter(seeded, _report())
    out = await _keep(seeded, cl_id)
    stored = out.record.review_state["decisions"][0]["basis"]
    rec = await seeded["db"].get(GeneratedCoverLetter, cl_id)
    rec.ats_report = _report(hash_=HASH_B, delta=[{"section_id": "body", "before": "x", "after": "y"}])
    await seeded["db"].commit()
    await seeded["db"].refresh(rec)
    check = rec.ats_report["checks"][0]
    assert rec.review_state["decisions"][0]["basis"] == stored
    assert stored != check["unreviewed_changes_hash"]  # the row reads as unacknowledged again


def test_old_decision_without_basis_stays_readable():
    from applire.services import review_state as rs

    raw = {"walked_at": None, "decisions": [
        {"finding_key": "critic:python", "label": "x", "action": "kept", "at": "t", "undo": None}]}
    assert rs.load_state(raw)["decisions"][0]["action"] == "kept"
