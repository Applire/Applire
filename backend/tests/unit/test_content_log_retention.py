# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The retention worker's content-log rules (ADR-017 amended 2026-11-18).

``content_log_age`` removes every line before it is older than
CONTENT_LOG_RETENTION_DAYS (a run a day: a daily file whose lines are all
older than N - 1 days is deleted by the date in its name, the file that
straddles the limit loses its old lines), and deletes every regular file that
is not a daily file once it is a day old. ``content_log_withdrawn`` reduces every line of a
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


def _ts(at: datetime) -> str:
    return at.isoformat()


def test_age_rule_deletes_by_the_date_in_the_name_in_both_directories(tmp_path):
    content, llm = tmp_path / "content", tmp_path / "llm"
    now = datetime(2026, 12, 31, 3, 0, tzinfo=timezone.utc)
    today = now.date()
    old_c = _write(content, today - timedelta(days=30), [{"ts": _ts(now - timedelta(days=30))}])
    edge = today - timedelta(days=29)  # straddles the limit (now - 29 days)
    edge_c = _write(content, edge, [
        {"ts": _ts(datetime(edge.year, edge.month, edge.day, 1, tzinfo=timezone.utc))},
        {"ts": _ts(datetime(edge.year, edge.month, edge.day, 5, tzinfo=timezone.utc))},
    ])
    old_l = _write(llm, today - timedelta(days=45), [{"ts": _ts(now - timedelta(days=45))}])
    new_l = _write(llm, today, [{"ts": _ts(now)}])
    # A fresh mtime on an old file must not save it (a copy or restore resets mtime).
    os.utime(old_l, (time.time(), time.time()))
    removed = content_logs.purge_by_age(30, now=now, directories=[content, llm])
    assert removed == 3  # two files, one line
    assert not old_c.exists() and not old_l.exists() and new_l.exists()
    assert [r["ts"] for r in _read(edge_c)] == [_ts(datetime(edge.year, edge.month, edge.day, 5, tzinfo=timezone.utc))]


@pytest.mark.parametrize("days", [1, 2, 30])
def test_with_a_run_a_day_no_line_is_ever_older_than_the_limit(tmp_path, days):
    """Lines every six hours for 70 days, a run every day at 03:00: just before
    each run, the oldest line left is at most ``days`` old."""
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    directory = tmp_path / "llm"
    worst = timedelta(0)
    for d in range(70):
        run_at = start + timedelta(days=d, hours=3)
        # the six-hourly lines written since the last run
        for h in range(4):
            at = run_at - timedelta(hours=6 * (h + 1))
            if at >= start:
                directory.mkdir(parents=True, exist_ok=True)
                with open(directory / f"{at.date().isoformat()}.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"ts": _ts(at)}) + "\n")
        remaining = [datetime.fromisoformat(r["ts"]) for f in directory.glob("*.jsonl") for r in _read(f)]
        if remaining:
            worst = max(worst, run_at - min(remaining))
        content_logs.purge_by_age(days, now=run_at, directories=[directory])
    assert worst <= timedelta(days=days)


def test_files_that_are_not_daily_files_go_after_a_day_whatever_the_limit(tmp_path):
    from applire.content_log import DIRECTORY_LOCK

    now = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)
    old = (now - timedelta(days=2)).timestamp()
    fresh = (now - timedelta(hours=2)).timestamp()
    tmp_path.mkdir(exist_ok=True)
    names = ["2026-11-01.jsonl.1", "2026-11-01.jsonl.bak", "old-notes.jsonl", "2026-13-45.jsonl",
             ".2026-11-01.jsonl.abc.tmp"]
    for name in names:
        (tmp_path / name).write_text(json.dumps({"user_id": "u", "prompt": TEXT}) + "\n", "utf-8")
        os.utime(tmp_path / name, (old, old))
    (tmp_path / "recent.bak").write_text("x", "utf-8")
    os.utime(tmp_path / "recent.bak", (fresh, fresh))
    (tmp_path / DIRECTORY_LOCK).write_text("", "utf-8")
    os.utime(tmp_path / DIRECTORY_LOCK, (old, old))
    (tmp_path / "2026-11-02.jsonl").mkdir()  # a directory named like a daily file
    (tmp_path / "link.jsonl").symlink_to(tmp_path / "recent.bak")
    for days in (0, 30):
        content_logs.purge_by_age(days, now=now, directories=[tmp_path])
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == sorted([DIRECTORY_LOCK, "recent.bak", "2026-11-02.jsonl", "link.jsonl"])


def test_appends_survive_concurrent_rewrites_from_other_processes(tmp_path):
    """Two writer processes append while this process rewrites the same file in
    a loop (the withdrawal rule): no line is lost."""
    import multiprocessing as mp

    from applire import content_log

    directory = tmp_path / "llm"
    gone = str(uuid.uuid4())
    content_log.append_jsonl(str(directory), {"user_id": gone, "ts": _ts(datetime.now(timezone.utc)), "prompt": TEXT})
    path = directory / f"{content_log._today()}.jsonl"
    per_writer = 1500
    ctx = mp.get_context("fork")
    writers = [ctx.Process(target=_append_many, args=(str(directory), w, per_writer)) for w in range(2)]
    for p in writers:
        p.start()
    rewrites = 0
    while any(p.is_alive() for p in writers):
        content_logs.rewrite_file(path, {gone: "all"})
        content_log.append_jsonl(str(directory), {"user_id": gone, "ts": _ts(datetime.now(timezone.utc)), "prompt": TEXT})
        rewrites += 1
    for p in writers:
        p.join()
    rows = _read(path)
    written = [r for r in rows if r.get("writer") is not None]
    assert len(written) == 2 * per_writer
    assert rewrites > 20, "the test did not contend"


def _append_many(directory: str, writer: int, count: int) -> None:
    from applire import content_log

    for i in range(count):
        content_log.append_jsonl(directory, {"user_id": None, "writer": writer, "i": i})


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


# ── the setting ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["-5", "abc", "3.5", "30 days"])
def test_a_limit_that_is_not_a_count_of_days_stops_the_start(raw, monkeypatch):
    from applire.config import InvalidSettingError, Settings

    monkeypatch.setenv("CONTENT_LOG_RETENTION_DAYS", raw)
    with pytest.raises(InvalidSettingError, match="CONTENT_LOG_RETENTION_DAYS must be a whole number"):
        Settings(_env_file=None)


@pytest.mark.parametrize("raw,days", [("", 0), ("0", 0), (" 30 ", 30)])
def test_a_count_of_days_is_read(raw, days, monkeypatch):
    from applire.config import Settings

    monkeypatch.setenv("CONTENT_LOG_RETENTION_DAYS", raw)
    assert Settings(_env_file=None).content_log_retention_days == days
