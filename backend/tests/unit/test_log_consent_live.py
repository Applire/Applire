# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A withdrawal takes effect on running work (ADR-091/096 amended 2026-11-18).

Work that started while the person had consented (the door read the facts
then) writes no further content line or model-call line after the withdrawal
and starts no further model call: at once in the process that stored the
withdrawal, within ``log_consent_live.TTL`` seconds in any other process.

The concurrency test at the end runs on real Postgres when
``APPLIRE_TEST_PG_URL`` names a migrated, disposable database.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire import content_log
from applire.config import settings
from applire.models.user import User
from applire.ownership import owner_context
from applire.providers.llm import debug_log
from applire.providers.llm.base import LLMProvider
from applire.services import allowance, log_consent, log_consent_live
from tests.unit.test_log_consent_doors import CONSENT_VALUES

CANARY = "Lebenslauf-Zeile-Brombeere-5150"


@pytest.fixture(autouse=True)
def _clean():
    allowance.reset_context_for_tests()
    yield
    allowance.reset_context_for_tests()


@pytest.fixture
def debug(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    monkeypatch.setattr(settings, "content_log_retention_days", 30)
    monkeypatch.setattr(settings, "content_log_dir", str(tmp_path / "content"))
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(tmp_path / "llm"))
    for k, v in CONSENT_VALUES.items():
        monkeypatch.setattr(settings, k, v)
    return tmp_path


class _Model(LLMProvider):
    def __init__(self) -> None:
        super().__init__(timeout=5)
        self.calls = 0

    async def acomplete(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        return "ok"

    async def aparse_json(self, prompt: str, **kwargs):
        self.calls += 1
        return {}

    def _model(self):  # pragma: no cover - not used
        return None


def _consenting(user_id: uuid.UUID) -> User:
    user = User(email=f"{user_id.hex[:8]}@example.org")
    user.id = user_id
    user.log_consent_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    user.log_consent_version = log_consent.current_version()
    return user


async def _door(user: User) -> None:
    """What the auth door does: load the facts (mode instance: no query)."""
    await allowance.load_facts(None, user)


def _lines(directory: Path, user_id: uuid.UUID) -> list[dict]:
    out = []
    for f in sorted(directory.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            row = json.loads(line)
            if row.get("user_id") == str(user_id):
                out.append(row)
    return out


@pytest.fixture
def content_file(debug):
    """The content tree on, writing to the temp directory (as at DEBUG)."""
    root = logging.getLogger(content_log.CONTENT_ROOT)
    handler = content_log.ContentFileHandler(str(debug / "content"))
    old = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    yield debug / "content"
    root.removeHandler(handler)
    root.setLevel(old)


clog = content_log.content_logger("applire.tests.live")


@pytest.mark.asyncio
async def test_a_withdrawal_in_this_process_stops_lines_and_calls_at_once(debug, content_file):
    uid = uuid.uuid4()
    model = debug_log._LoggingProvider(_Model())
    with owner_context(uid):
        await _door(_consenting(uid))
        clog.debug("before %s", CANARY)
        await model.acomplete(f"prompt {CANARY}")
        assert len(_lines(content_file, uid)) == 1 and len(_lines(debug / "llm", uid)) == 1

        log_consent_live.note_withdrawn(uid)  # what DELETE /api/me/log-consent does

        clog.debug("after %s", CANARY)
        with pytest.raises(allowance.AdmissionRefused) as exc:
            await model.acomplete(f"prompt {CANARY}")
    assert exc.value.code == "log_consent_required"
    assert model._inner.calls == 1
    assert len(_lines(content_file, uid)) == 1 and len(_lines(debug / "llm", uid)) == 1


@pytest_asyncio.fixture
async def users_db(tmp_path):
    from applire.db.session import Base
    import applire.models  # noqa: F401

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'live.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_withdrawal_stored_by_another_process_is_read_within_the_ttl(debug, content_file, users_db, monkeypatch):
    uid = uuid.uuid4()
    async with users_db() as db:
        db.add(_consenting(uid))
        await db.commit()
    monkeypatch.setattr(log_consent_live, "TTL", 0.0)
    model = debug_log._LoggingProvider(_Model())
    with owner_context(uid):
        async with users_db() as db:
            await _door(await db.get(User, uid))
        await model.acomplete("first")
        # Another process stores the withdrawal: nothing in this process is told.
        async with users_db() as db:
            await db.execute(update(User).where(User.id == uid).values(log_consent_withdrawn_at=datetime.now(timezone.utc)))
            await db.commit()
        # The refresher reads it; content lines are held back from then on.
        await log_consent_live.refresh([uid], session_factory=users_db)
        clog.debug("between calls %s", CANARY)
        monkeypatch.setattr(log_consent_live, "_state", {uid: (True, 0.0)})  # stale, still "yes"
        original = log_consent_live.allowed_to_call
        monkeypatch.setattr(
            log_consent_live, "allowed_to_call",
            lambda user_id, **kw: original(user_id, session_factory=users_db),
        )
        with pytest.raises(allowance.AdmissionRefused):
            await model.acomplete("second")  # the stale state is re-read before the call
    assert model._inner.calls == 1
    assert [r["message"] for r in _lines(content_file, uid)] == []


def test_the_refresher_lists_only_people_this_process_worked_for():
    a, b = uuid.uuid4(), uuid.uuid4()
    log_consent_live.note_door(a, True)
    log_consent_live.note_door(b, False)
    assert set(log_consent_live._active) == {a}
    assert log_consent_live.revoked(b) and not log_consent_live.revoked(a)
    log_consent_live.note_consented(b)
    assert not log_consent_live.revoked(b)


@pytest.mark.asyncio
async def test_without_a_door_state_the_facts_decide_as_before(debug):
    assert await log_consent_live.allowed_to_call(uuid.uuid4()) is True
    assert await log_consent_live.allowed_to_call(None) is True


# ── real Postgres: a running job, a withdrawal from another connection ─────────

PG_URL = os.environ.get("APPLIRE_TEST_PG_URL", "")


@pytest.mark.asyncio
@pytest.mark.skipif(not PG_URL, reason="APPLIRE_TEST_PG_URL not set (a migrated, disposable Postgres)")
@pytest.mark.parametrize("where", ["this process", "another process"])
async def test_on_postgres_a_running_job_stops_after_a_concurrent_withdrawal(debug, content_file, monkeypatch, where):
    engine = create_async_engine(PG_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    uid = uuid.uuid4()
    user = _consenting(uid)
    user.email = f"live-{uid.hex[:8]}@race.example"
    async with factory() as db:
        db.add(user)
        await db.commit()
    monkeypatch.setattr(log_consent_live, "TTL", 0.3)
    original = log_consent_live.allowed_to_call
    monkeypatch.setattr(
        log_consent_live, "allowed_to_call", lambda user_id, **kw: original(user_id, session_factory=factory)
    )
    model = debug_log._LoggingProvider(_Model())
    withdrawn_at: list[float] = []
    ended: list[BaseException] = []

    async def job():
        with owner_context(uid):
            async with factory() as db:
                await _door(await db.get(User, uid))
            try:
                for i in range(200):
                    clog.debug("job step %d %s", i, CANARY)
                    await model.acomplete(f"step {i} {CANARY}")
                    await asyncio.sleep(0.02)
            except allowance.AdmissionRefused as exc:
                ended.append(exc)

    async def withdraw():
        await asyncio.sleep(0.5)
        async with factory() as db:
            await db.execute(update(User).where(User.id == uid).values(log_consent_withdrawn_at=datetime.now(timezone.utc)))
            await db.commit()
        withdrawn_at.append(time.time())
        if where == "this process":
            log_consent_live.note_withdrawn(uid)

    refresher_stop = asyncio.Event()
    refresher = asyncio.create_task(log_consent_live.run_refresher(refresher_stop, session_factory=factory))
    try:
        await asyncio.gather(job(), withdraw())
    finally:
        refresher_stop.set()
        await refresher
        async with factory() as db:
            await db.execute(update(User).where(User.id == uid).values(email=f"gone-{uid.hex}@race.example"))
            await db.commit()
        await engine.dispose()

    assert ended and ended[0].code == "log_consent_required"
    window = 0.0 if where == "this process" else log_consent_live.TTL + 0.1
    for directory in (content_file, debug / "llm"):
        late = [
            r for r in _lines(directory, uid)
            if datetime.fromisoformat(r["ts"]).timestamp() > withdrawn_at[0] + window
        ]
        assert late == [], directory.name
    assert model._inner.calls < 200
