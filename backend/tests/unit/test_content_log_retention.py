# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The retention worker's content-log rules (ADR-017 amended 2026-11-18).

``content_log_age`` deletes daily files older than CONTENT_LOG_RETENTION_DAYS
(by the date in the name). ``content_log_withdrawn`` reduces every line of a
withdrawn or deleted person to technical figures without the user id, keeps
everyone else's lines, and is idempotent.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from applire.config import settings
from applire.models.user import User
from applire.ownership import unscoped
from applire.retention import content_logs

TEXT = "Leitung Schichtbetrieb Weberit GmbH 38 Personen"


def _write(directory, day, rows):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day.isoformat()}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    return path


def _read(path):
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def _app_line(uid, ts):
    return {"ts": ts, "level": "DEBUG", "logger": "applire.content.services.cv", "user_id": uid,
            "template": "skill spelling guard: %r has no unambiguous vault original",
            "message": f"skill spelling guard: '{TEXT}' has no unambiguous vault original"}


def _llm_line(uid, ts):
    return {"user_id": uid, "ts": ts, "stage": "cv_generation", "review_role": None,
            "review_attempt": None, "provider": "RequestyProvider", "model": "luna", "method": "acomplete",
            "system": TEXT, "prompt": TEXT, "params": {"max_tokens": 10}, "latency_ms": 812,
            "ok": False, "error": f"LLMError: upstream said {TEXT}", "response": TEXT,
            "reasoning": TEXT, "discarded_tail": None, "discarded_prose": None,
            "tokens": {"prompt": 100, "completion": 20, "reasoning": 0}}


# ── age ────────────────────────────────────────────────────────────────────────


def test_age_rule_deletes_by_the_date_in_the_name_in_both_directories(tmp_path):
    content, llm = tmp_path / "content", tmp_path / "llm"
    today = date(2026, 12, 31)
    old_c = _write(content, today - timedelta(days=31), [{"x": 1}])
    edge_c = _write(content, today - timedelta(days=30), [{"x": 1}])
    old_l = _write(llm, today - timedelta(days=45), [{"x": 1}])
    new_l = _write(llm, today, [{"x": 1}])
    other = llm / "notes.txt"
    other.write_text("keep", "utf-8")
    # A fresh mtime on an old file must not save it (a copy or restore resets mtime).
    os.utime(old_l, (time.time(), time.time()))
    deleted = content_logs.purge_by_age(30, today=today, directories=[content, llm])
    assert deleted == 2
    assert not old_c.exists() and not old_l.exists()
    assert edge_c.exists() and new_l.exists() and other.exists()


def test_age_zero_is_unlimited(tmp_path):
    f = _write(tmp_path, date(2020, 1, 1), [{"x": 1}])
    assert content_logs.purge_by_age(0, directories=[tmp_path]) == 0
    assert f.exists()


# ── withdrawal ─────────────────────────────────────────────────────────────────


async def _user(db, **kw):
    with unscoped("retention"):
        u = User(email=f"{uuid.uuid4().hex[:8]}@example.org", **kw)
        db.add(u)
        await db.commit()
        return u


@pytest.mark.asyncio
async def test_a_withdrawn_persons_lines_become_technical_figures_and_others_stay(async_db, tmp_path):
    now = datetime.now(timezone.utc)
    gone = await _user(async_db, log_consent_at=now - timedelta(days=3), log_consent_version="1-x",
                       log_consent_withdrawn_at=now - timedelta(days=1))
    stays = await _user(async_db, log_consent_at=now - timedelta(days=3), log_consent_version="1-x")
    ts = (now - timedelta(hours=30)).isoformat()
    content, llm = tmp_path / "content", tmp_path / "llm"
    c = _write(content, now.date(), [_app_line(str(gone.id), ts), _app_line(str(stays.id), ts),
                                     _app_line(None, ts)])
    l = _write(llm, now.date(), [_llm_line(str(gone.id), ts), _llm_line(str(stays.id), ts)])

    with unscoped("retention"):
        changed = await content_logs.scrub_withdrawn(async_db, directories=[content, llm])
    assert changed == 2

    app_rows = _read(c)
    assert app_rows[0] == {"ts": ts, "level": "DEBUG", "logger": "applire.content.services.cv",
                           "template": "skill spelling guard: %r has no unambiguous vault original",
                           "removed": "person"}
    assert app_rows[1]["user_id"] == str(stays.id) and TEXT in app_rows[1]["message"]
    assert app_rows[2]["user_id"] is None  # ownerless line untouched
    llm_rows = _read(l)
    assert "user_id" not in llm_rows[0]
    assert TEXT not in json.dumps(llm_rows[0])
    assert llm_rows[0]["stage"] == "cv_generation" and llm_rows[0]["model"] == "luna"
    assert llm_rows[0]["latency_ms"] == 812 and llm_rows[0]["tokens"]["prompt"] == 100
    assert llm_rows[0]["error_type"] == "LLMError"
    assert llm_rows[1]["user_id"] == str(stays.id) and llm_rows[1]["prompt"] == TEXT

    # Idempotent: a second run changes nothing.
    with unscoped("retention"):
        assert await content_logs.scrub_withdrawn(async_db, directories=[content, llm]) == 0


@pytest.mark.asyncio
async def test_consent_given_again_keeps_the_new_lines_and_removes_the_old(async_db, tmp_path):
    now = datetime.now(timezone.utc)
    again = now - timedelta(hours=2)
    person = await _user(async_db, log_consent_at=again, log_consent_version="1-x",
                         log_consent_withdrawn_at=now - timedelta(days=1))
    before = (now - timedelta(days=2)).isoformat()
    after = (now - timedelta(hours=1)).isoformat()
    f = _write(tmp_path, now.date(), [_app_line(str(person.id), before), _app_line(str(person.id), after)])
    with unscoped("retention"):
        assert await content_logs.scrub_withdrawn(async_db, directories=[tmp_path]) == 1
    rows = _read(f)
    assert "user_id" not in rows[0] and rows[1]["user_id"] == str(person.id)


@pytest.mark.asyncio
async def test_a_deleted_or_unknown_account_loses_its_lines(async_db, tmp_path):
    now = datetime.now(timezone.utc)
    deleted = await _user(async_db, deleted_at=now, log_consent_at=now, log_consent_version="1-x")
    ts = now.isoformat()
    f = _write(tmp_path, now.date(), [_app_line(str(deleted.id), ts), _app_line(str(uuid.uuid4()), ts),
                                      _app_line("not-a-uuid", ts)])
    with unscoped("retention"):
        assert await content_logs.scrub_withdrawn(async_db, directories=[tmp_path]) == 3
    assert all("user_id" not in r for r in _read(f))


@pytest.mark.asyncio
async def test_a_line_that_is_not_json_is_withheld_when_its_file_is_rewritten(async_db, tmp_path):
    now = datetime.now(timezone.utc)
    gone = await _user(async_db, log_consent_withdrawn_at=now)
    path = _write(tmp_path, now.date(), [_app_line(str(gone.id), now.isoformat())])
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f'{{"user_id": "{gone.id}", "message": "{TEXT}\n')  # truncated write
    with unscoped("retention"):
        await content_logs.scrub_withdrawn(async_db, directories=[tmp_path])
    assert TEXT not in path.read_text("utf-8")


@pytest.mark.asyncio
async def test_the_sweep_runs_both_rules_and_reports_them(async_db, tmp_path, monkeypatch):
    """Through the worker's own sweep: both rules run and are in the report."""
    from applire.retention import worker

    now = datetime.now(timezone.utc)
    gone = await _user(async_db, log_consent_withdrawn_at=now)
    content, llm = tmp_path / "content", tmp_path / "llm"
    monkeypatch.setattr(settings, "content_log_dir", str(content))
    monkeypatch.setattr(settings, "llm_debug_log_dir", str(llm))
    monkeypatch.setattr(settings, "content_log_retention_days", 30)
    old = _write(content, now.date() - timedelta(days=40), [_app_line(None, now.isoformat())])
    cur = _write(llm, now.date(), [_llm_line(str(gone.id), now.isoformat())])

    class _Session:
        async def __aenter__(self):
            return async_db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(worker, "AsyncSessionLocal", lambda: _Session())
    with unscoped("retention"):
        report = await worker._sweep_unscoped()
    assert report["content_log_files_deleted"] == 1
    assert report["content_log_lines_removed"] == 1
    assert "content_log_age" not in report["failed_rules"]
    assert not old.exists() and TEXT not in cur.read_text("utf-8")
