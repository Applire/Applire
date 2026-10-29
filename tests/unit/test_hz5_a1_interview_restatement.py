# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 D-17. An interview answer that retells a fact the profile
already holds came back as a new bullet onto the entry that holds it, in other
words. The reconcile prompt's "not a SECOND TIME" rule did not stop it
(delivery run 3: rep a 1, rep b 5 near-duplicates). The import door has had
the restatement check since D-B3V6; the interview door now runs it too.

Replay of the five captured write-back turns (gpt-6-luna, thinking off, n = 3):
the check held back 15-16 of 19 new bullets that restate a held one, the same
ones each run; 32 control bullets with a fact the profile does not hold, 0
held back.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import MasterProfile, authorized_profile_write
from applire.prompts.restatement import RESTATEMENT_SYSTEM_PROMPT
from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn

HELD = "Led the move of the lab software from Pascal to C# across four product lines."
RETOLD = "Led the transition of the lab software from Pascal to C#."
W1 = "7d1c4e2a-5b6f-4a3e-9c8d-0e1f2a3b4c5d"
NEW = "Mentored two apprentices through their final chamber examination."


def _profile() -> dict:
    return {
        "personal_info": {"full_name": "Test User"},
        "work_experience": [{
            "id": W1, "company": "Nordwerk AG", "role": "Systems Developer",
            "start_date": "2006-01", "end_date": "2010-12",
            "responsibilities": [HELD], "achievements": [],
        }],
    }


class _Provider:
    def __init__(self, restatement=None, fail=False):
        self.restatement = restatement if restatement is not None else {
            "items": [{"new": "e1.n1", "held": "e1.h1", "added": ""}]
        }
        self.fail = fail
        self.restatement_calls = 0

    async def aparse_json(self, prompt, system=None, **kw):
        if system == RESTATEMENT_SYSTEM_PROMPT:
            self.restatement_calls += 1
            if self.fail:
                raise RuntimeError("provider down")
            return self.restatement
        return {"ops": [{"op": "add_bullets", "target": "id_w1", "responsibilities": [RETOLD, NEW]}],
                "ambiguities": [], "denials": []}


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[MasterProfile.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _turn(db, provider):
    with authorized_profile_write():
        record = MasterProfile(profile_json=_profile())
    db.add(record)
    await db.commit()
    out = await reconcile_interview_turn(
        db, profile_record=record, gap="Migration", question="How did the migration go?",
        answer="I led the transition of the lab software from Pascal to C#. I also mentored two apprentices through their final chamber examination.",
        provider=provider, session_id="s1",
    )
    await db.commit()
    return out, record


@pytest.mark.asyncio
async def test_a_retold_held_fact_is_held_back_and_receipted_the_new_fact_lands(db):
    provider = _Provider()
    out, record = await _turn(db, provider)

    bullets = out.profile_dict["work_experience"][0]["responsibilities"]
    assert bullets == [HELD, NEW]
    assert provider.restatement_calls == 1
    [receipt] = record.profile_json["metadata"]["enrichment_history"][-1]["restated"]
    assert receipt["text"] == RETOLD and receipt["restates"] == HELD and receipt["entity_id"] == W1
    assert out.addressed is True


@pytest.mark.asyncio
async def test_the_check_fails_open(db):
    out, _ = await _turn(db, _Provider(fail=True))

    assert out.profile_dict["work_experience"][0]["responsibilities"] == [HELD, RETOLD, NEW]


@pytest.mark.asyncio
async def test_a_turn_that_only_retells_writes_nothing_and_says_so(db):
    provider = _Provider(restatement={"items": [
        {"new": "e1.n1", "held": "e1.h1", "added": ""},
        {"new": "e1.n2", "held": "e1.h1", "added": ""},
    ]})
    out, _ = await _turn(db, provider)

    assert out.profile_dict["work_experience"][0]["responsibilities"] == [HELD]
    assert out.addressed is False
