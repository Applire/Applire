# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 7, WP-O — the operator console's facts and the agent door's answers.

* 785:251 — a backup in its first-run grace reads ``ok`` but does not exist: the
  dashboard health component says ``not_set_up`` (+ ``due_by``) so the console
  can show a neutral to-do instead of green "in Ordnung".
* 789:267 — the admin users list carries ``active_token_count`` (the number
  "Alle Tokens widerrufen" revokes); ``/api/auth/state`` says ``upgraded_install``
  so /setup shows "used Applire without an account?" only where it can be true.
* 788:133 — ``submit_testimony`` answers with a ``summary`` that separates what
  landed from what waits.
* 788:134 / MD7-5 — a failed letter stores a classified sentence, never the
  provider's raw text; both status doors answer ``error_code`` and the agent door
  ``retryable``; a raised provider error carries a typed retry head.
* 788:135 — ``create_application`` reminds the agent that nothing is pinned.
* 788:130 — the letter's stated precondition names both ways to its flow.
"""

from __future__ import annotations

import importlib.util as _ilu
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

from applire.exceptions import (
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMTruncatedError,
)
from applire.models.auth import PersonalToken
from applire.services.generation_errors import (
    is_retryable,
    letter_error_code,
    letter_error_sentence,
    letter_failure_message,
)
from tests.support.owners_1b import add_user, build_app, client_for
from tests.unit.test_c1_instance_settings import _clean_overlay, env  # noqa: F401 — fixtures reused

RAW = "Requesty call timed out after 180s"


# --------------------------------------------------------------------- 785:251


def test_dashboard_component_marks_a_backup_in_grace_as_not_set_up():
    from applire.services.admin.dashboard import _dashboard_component

    due = "2026-12-07T08:00:00+00:00"
    grace = {"status": "ok", "detail": {"last_backup_at": None, "first_due_by": due}}
    out = _dashboard_component("backup", grace)
    assert out["status"] == "ok" and out["not_set_up"] is True and out["due_by"] == due


@pytest.mark.parametrize(
    "name,component",
    [
        ("backup", {"status": "ok", "detail": {"last_backup_at": "2026-11-06T03:00:00+00:00"}}),
        ("backup", {"status": "degraded", "detail": {"last_backup_at": None}}),
        ("disk", {"status": "ok", "detail": {"last_backup_at": None}}),
    ],
)
def test_dashboard_component_leaves_everything_else_alone(name, component):
    from applire.services.admin.dashboard import _dashboard_component

    out = _dashboard_component(name, component)
    assert "not_set_up" not in out and "due_by" not in out


@pytest.mark.asyncio
async def test_dashboard_door_carries_not_set_up_on_a_fresh_instance(env):
    """The delivery point: GET /api/admin/dashboard on a fresh install, no backup."""
    from applire.models.instance_state import KEY_INSTANCE_FIRST_STARTED_AT
    from applire.services.instance_state import write_state

    db, client, _, _ = env
    await write_state(db, KEY_INSTANCE_FIRST_STARTED_AT, datetime.now(timezone.utc).isoformat())
    await db.commit()
    r = await client.get("/api/admin/dashboard")
    assert r.status_code == 200, r.text
    backup = next(c for c in r.json()["health"]["components"] if c["name"] == "backup")
    assert backup["status"] == "ok" and backup["not_set_up"] is True and backup["due_by"]


# --------------------------------------------------------------------- 789:267


@pytest_asyncio.fixture
async def admin_env(async_db):
    app, acting = build_app(async_db)
    admin = await add_user(async_db, email="admin@example.org", role="admin")
    acting.user_id = admin.id
    async with client_for(app) as client:
        yield async_db, client, admin


@pytest.mark.asyncio
async def test_users_list_counts_exactly_the_tokens_revoke_all_would_revoke(admin_env):
    db, client, _ = admin_env
    user = await add_user(db, email="person@example.org")
    rows = [("agent", None), ("api", None), ("probe", None), ("agent", datetime.now(timezone.utc))]
    for i, (scope, revoked) in enumerate(rows):
        db.add(PersonalToken(user_id=user.id, scope=scope, name="t", prefix=f"pfx0000{i}",
                             token_hash=str(i) * 64, revoked_at=revoked))
    await db.commit()
    body = (await client.get("/api/admin/users")).json()
    row = next(u for u in body["users"] if u["email"] == "person@example.org")
    assert row["metadata"]["active_token_count"] == 2  # live agent + api; not probe, not revoked
    admin_row = next(u for u in body["users"] if u["email"] == "admin@example.org")
    assert admin_row["metadata"]["active_token_count"] == 0
    r = await client.post(f"/api/admin/users/{user.id}/revoke-tokens", headers={"Origin": "http://applire.test"})
    assert r.json() == {"revoked": 2}
    body = (await client.get("/api/admin/users")).json()
    row = next(u for u in body["users"] if u["email"] == "person@example.org")
    assert row["metadata"]["active_token_count"] == 0


@pytest.mark.asyncio
async def test_auth_state_says_upgraded_install_only_while_setup_is_required(async_client, async_db):
    from applire.auth.harness import forget_credential_cache
    from applire.auth.setup import ensure_stub_user
    from applire.models.instance_state import KEY_INSTANCE_FIRST_STARTED_AT
    from applire.services.instance_state import write_state

    await ensure_stub_user(async_db)
    await async_db.commit()
    forget_credential_cache()
    state = (await async_client.get("/api/auth/state")).json()
    assert state["setup_required"] is True
    assert state["upgraded_install"] is True  # no fresh-install stamp

    await write_state(async_db, KEY_INSTANCE_FIRST_STARTED_AT, datetime.now(timezone.utc).isoformat())
    await async_db.commit()
    state = (await async_client.get("/api/auth/state")).json()
    assert state["upgraded_install"] is False


# --------------------------------------------------------------------- 788:133


def test_testimony_summary_separates_saved_from_waiting():
    from applire.mcp.server import _testimony_summary

    payload = {"status": "conflict", "changes": [{}] * 12, "conflicts": [{}], "confirmations": [],
               "not_applied": []}
    summary = _testimony_summary(payload)
    assert summary.startswith("12 change(s) saved to the profile.")
    assert "1 conflict(s) for the person" in summary and "stay either way" in summary
    assert _testimony_summary({"status": "applied", "changes": [{}] * 3}) == "3 change(s) saved to the profile."
    assert _testimony_summary({"status": "error", "changes": []}).startswith("Nothing was saved")


@pytest.mark.asyncio
async def test_submit_testimony_door_returns_the_summary(monkeypatch):
    """Seam: the MCP tool body adds ``summary`` to the service's payload."""
    from applire.mcp import server
    from applire.schemas.testimony import TestimonyResult
    from applire.services.profile.reconcile import testimony_bridge

    async def fake_svc(text, db, provider, *, user_id):
        return TestimonyResult(submission_id="s1", status="conflict", changes=[], conflicts=[])

    monkeypatch.setattr(testimony_bridge, "submit_testimony", fake_svc)
    monkeypatch.setattr(server, "get_provider", lambda: object())

    async def _uid():
        import uuid

        return uuid.uuid4()

    monkeypatch.setattr(server, "_current_user_id", _uid)

    class _DB:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(server, "get_db", lambda: _DB())
    fn = getattr(server.submit_testimony, "__wrapped__", server.submit_testimony)
    out = await fn("I led the migration.")
    assert out["status"] == "conflict" and out["summary"].startswith("0 change(s) saved")


# --------------------------------------------------------------------- 788:134


@pytest.mark.parametrize(
    "exc,code",
    [
        (LLMTimeoutError(RAW), "llm_timeout"),
        (LLMProviderUnavailableError("Requesty is temporarily unavailable (HTTP 503)."), "provider_unavailable"),
        (LLMTruncatedError("cut"), "llm_truncated"),
        (RuntimeError(f"boom {RAW}"), "generation_failed"),
    ],
)
def test_letter_failure_stores_a_classified_sentence_without_the_raw_text(exc, code):
    stored = letter_failure_message(exc)
    assert RAW not in stored and "Requesty" not in stored
    assert letter_error_code(stored) == code
    assert is_retryable(code) is (code != "generation_failed")


def test_a_legacy_raw_row_reads_as_generation_failed():
    assert letter_error_code(RAW) == "generation_failed"
    assert letter_error_code(None) == "generation_failed"


def test_status_schema_classifies_a_failed_row_and_clears_others():
    from applire.schemas.cover_letter import CoverLetterStatusResponse

    common = {"cover_letter_id": "00000000-0000-0000-0000-000000000001",
              "expires_at": datetime.now(timezone.utc) + timedelta(days=1)}
    timed_out = CoverLetterStatusResponse(status="failed", error_message=letter_error_sentence("llm_timeout"), **common)
    assert timed_out.error_code == "llm_timeout"
    legacy = CoverLetterStatusResponse(status="failed", error_message=RAW, **common)
    assert legacy.error_code == "generation_failed" and RAW not in legacy.error_message
    ready = CoverLetterStatusResponse(status="ready", **common)
    assert ready.error_code is None and ready.error_message is None


_spec = _ilu.spec_from_file_location(
    "_degradation_harness_hz7o", Path(__file__).resolve().parents[3] / "tests" / "unit" / "test_786_review_degradation.py"
)
_h = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_h)
db = _h.db
db_with_cover_letter = _h.db_with_cover_letter


@pytest.mark.asyncio
async def test_seam_letter_catch_site_to_both_status_doors(db_with_cover_letter, monkeypatch):
    """The delivery point: a writer timeout inside the real background render →
    the stored row → REST status (``error_code``) → MCP status (``retryable``)."""
    from applire.mcp import server
    from applire.services.cover_letter import get_cover_letter_status

    async def timing_out(**kwargs):
        raise LLMTimeoutError(RAW)

    cl = await _h._render_letter(db_with_cover_letter, timing_out, "v2", monkeypatch)
    assert cl.status == "failed"
    assert RAW not in (cl.error_message or "")
    status = await get_cover_letter_status(db_with_cover_letter["cl_id"], db_with_cover_letter["db"], "http://x")
    assert status.error_code == "llm_timeout"
    payload = server._with_retry_advice(status.model_dump(mode="json"))
    assert payload["retryable"] is True and payload["error_code"] == "llm_timeout"


def test_retry_advice_leaves_a_ready_payload_alone():
    from applire.mcp.server import _with_retry_advice

    assert _with_retry_advice({"status": "ready"}) == {"status": "ready"}
    assert _with_retry_advice({"status": "failed", "error_code": "generation_failed"})["retryable"] is False


@pytest.mark.parametrize(
    "exc,head",
    [
        (LLMTimeoutError(RAW), "provider_timeout (safe to retry"),
        (LLMRateLimitError("Mistral rate limit after 3 attempts"), "provider_rate_limited (safe to retry"),
        (LLMProviderUnavailableError("down"), "provider_unavailable (safe to retry"),
        (LLMTruncatedError("cut"), "llm_truncated (safe to retry"),
    ],
)
def test_a_raised_provider_error_carries_a_typed_retry_head(exc, head):
    from applire.internal_errors import agent_facing_message

    assert agent_facing_message(exc, where="test").startswith(head)


# --------------------------------------------------------------------- 788:135


def test_pin_reminder_only_when_a_ready_cv_is_unpinned():
    from applire.mcp.server import _with_pin_reminder

    assert "pin_reminder" in _with_pin_reminder({"workflow_status": "completed", "submitted_cv_id": None})
    assert "pin_reminder" not in _with_pin_reminder({"workflow_status": "completed", "submitted_cv_id": "x"})
    assert "pin_reminder" not in _with_pin_reminder({"workflow_status": "pending", "submitted_cv_id": None})


# --------------------------------------------------------------------- 788:130 + guide


@pytest.mark.asyncio
async def test_letter_precondition_and_guide_name_both_ways_to_the_flow():
    from applire.mcp.server import _load_guide, mcp

    tools = {t.name: t for t in await mcp.list_tools()}
    desc = tools["generate_cover_letter"].description
    assert "start_flow(job_id)" in desc and "create_application(start_workflow=true)" in desc
    guide = _load_guide()
    assert "`create_application(job_id, start_workflow=true)` creates; either one is enough" in guide
    assert "`retryable: true`" in guide and "pin_reminder" in guide and "`summary` says both" in guide
