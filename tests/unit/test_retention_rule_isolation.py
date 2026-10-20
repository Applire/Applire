# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""785:L69 / ADR-005 amended 2026-10-20 — one retention rule's unexpected error
no longer aborts the sweep: every later rule (GDPR TTLs included) still runs,
the failing rule is named in ``failed_rules``, and the run is recorded failed
and exits non-zero, so a partial run is never reported as a clean one."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.retention import worker

#: Every rule the sweep calls, in order, with the shape it returns.
_RULES = {
    "_purge_uploads": 0,
    "_purge_sessions": 0,
    "_count_submitted_exempt": 0,
    "_purge_cvs": 0,
    "_tombstone_inactive_profiles": 0,
    "_tombstone_inactive_users": 0,
    "_tombstone_inactive_applications": 0,
    "_release_fact_pins": 0,
    "_purge_cancelled_documents": (0, 0, 0),
    "_reap_stale_cv_jobs": 0,
    "_purge_cover_letters": 0,
    "_reap_stale_cl_jobs": 0,
    "_purge_import_jobs": 0,
    "_purge_gap_jobs": 0,
    "_scan_orphan_files": 0,
    "_purge_llm_usage": 0,
    "_purge_orphan_postings": 0,
    "_purge_auth_housekeeping": {"auth_sessions_deleted": 0},
    "_purge_audit_events": 0,
}


@pytest.fixture
def calls(monkeypatch):
    eng = create_async_engine("sqlite+aiosqlite://")
    monkeypatch.setattr(worker, "AsyncSessionLocal", async_sessionmaker(eng))
    seen: list[str] = []
    for name, result in _RULES.items():
        async def _stub(db, *a, _n=name, _r=result, **k):
            seen.append(_n)
            return _r
        monkeypatch.setattr(worker, name, _stub)
    return seen


def _raising(monkeypatch, name, calls, exc):
    async def _boom(db, *a, **k):
        calls.append(name)
        raise exc
    monkeypatch.setattr(worker, name, _boom)


@pytest.mark.asyncio
async def test_a_clean_sweep_runs_every_rule_and_fails_none(calls):
    report = await worker._sweep_unscoped()
    assert calls == list(_RULES)
    assert report["failed_rules"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["_purge_sessions", "_tombstone_inactive_users", "_purge_cancelled_documents"])
@pytest.mark.parametrize(
    "exc",
    [IntegrityError("DELETE …", {}, Exception("fk")), RuntimeError("bug"), ValueError("bad row")],
    ids=["IntegrityError", "RuntimeError", "ValueError"],
)
async def test_one_failing_rule_does_not_skip_the_later_rules(monkeypatch, calls, failing, exc):
    _raising(monkeypatch, failing, calls, exc)
    report = await worker._sweep_unscoped()
    assert calls == list(_RULES), "a rule after the failing one did not run"
    assert len(report["failed_rules"]) == 1
    # the later GDPR TTL rules still produced their counters
    assert report["generated_cover_letters_deleted"] == 0
    assert report["audit_events_deleted"] == 0


@pytest.mark.asyncio
async def test_the_failed_rule_is_named_and_logged_without_row_values(monkeypatch, calls, caplog):
    _raising(monkeypatch, "_purge_sessions", calls, RuntimeError("SECRET-ROW-VALUE"))
    with caplog.at_level("ERROR", logger=worker.logger.name):
        report = await worker._sweep_unscoped()
    assert report["failed_rules"] == ["interview_sessions"]
    assert "interview_sessions" in caplog.text and "RuntimeError" in caplog.text
    assert "SECRET-ROW-VALUE" not in caplog.text


@pytest.mark.asyncio
async def test_a_partial_run_is_recorded_failed_and_exits_non_zero(monkeypatch):
    recorded = {}

    async def _sweep():
        return {"run_at": "x", "failed_rules": ["orphan_files"]}

    async def _record(report, *, duration_ms, ok, error):
        recorded.update(ok=ok, error=error, report=report)

    monkeypatch.setattr(worker, "_sweep", _sweep)
    monkeypatch.setattr(worker, "record_run", _record)
    with pytest.raises(worker.RetentionRulesFailed):
        await worker.run()
    assert recorded["ok"] is False and "orphan_files" in recorded["error"]
    assert recorded["report"]["failed_rules"] == ["orphan_files"]


@pytest.mark.asyncio
async def test_a_clean_run_is_recorded_ok(monkeypatch):
    recorded = {}

    async def _sweep():
        return {"run_at": "x", "failed_rules": []}

    async def _record(report, *, duration_ms, ok, error):
        recorded.update(ok=ok, error=error)

    monkeypatch.setattr(worker, "_sweep", _sweep)
    monkeypatch.setattr(worker, "record_run", _record)
    await worker.run()
    assert recorded == {"ok": True, "error": None}
