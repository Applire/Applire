# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The admin dashboard's data (#694; ADR-086 amended 2026-10-07, ADR-093).

Composes facts other modules own — ops health (``services/ops``), user counts
(``services/admin/users``), usage totals (``services/admin/usage``), failed jobs
(``services/admin/failed_jobs``), the retention toggle (``services/instance_settings``)
— into one payload plus ``notices[]``. Metadata only (S-4, RD-6).

``notices_cheap`` is the DB-only subset behind the admin-only one-line signal on
the user dashboard: it never runs a probe, it reads the ops layer's LAST report.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.config import HAS_CLOUD, settings
from applire.models.audit import AuditEvent
from applire.models.retention_run import RetentionRun
from applire.models.user import ROLE_ADMIN
from applire.ownership import unscoped
from applire.services import instance_settings
from applire.services.admin import failed_jobs, usage
from applire.services.admin import users as accounts

_CRITICAL = "critical"
_WARNING = "warning"


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _upgrade_notice() -> dict | None:
    from applire.routers.health import get_upgrade_notice

    return get_upgrade_notice()


async def _user_counts(db: AsyncSession) -> dict[str, int]:
    users = await accounts.list_users(db)
    counts = {"total": len(users), "active": 0, "pending": 0, "disabled": 0, "admins": 0}
    for user in users:
        counts[accounts.user_status(user)] += 1
        if user.role == ROLE_ADMIN:
            counts["admins"] += 1
    return counts


async def _last_retention_run(db: AsyncSession) -> RetentionRun | None:
    try:
        return (
            await db.execute(select(RetentionRun).order_by(RetentionRun.run_at.desc()).limit(1))
        ).scalar_one_or_none()
    except Exception:
        await db.rollback()
        return None


_RETENTION_ACTIONS = ("settings.changed", "settings.reset", "settings.env_observed")


async def _last_off_evidence(db: AsyncSession) -> datetime | None:
    """The newest moment retention was demonstrably OFF in the WORKER: a
    ``retention.skipped`` audit row or a run record with ``retention_enabled:
    false`` (adv-admin ADM-1). The settings rows only show what the web process
    observed; the worker reads its own environment at every run."""
    skipped_row = (
        await db.execute(
            select(AuditEvent.at)
            .where(AuditEvent.action == "retention.skipped")
            .order_by(AuditEvent.at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    try:
        skipped_run = (
            await db.execute(
                select(RetentionRun.run_at)
                .where(RetentionRun.report["retention_enabled"].as_boolean().is_(False))
                .order_by(RetentionRun.run_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    except Exception:
        await db.rollback()
        skipped_run = None
    times = [_aware(t) for t in (skipped_row, skipped_run) if t is not None]
    return max(times) if times else None


async def _first_enabled_run_after(db: AsyncSession, after: datetime) -> datetime | None:
    try:
        runs = (
            await db.execute(
                select(RetentionRun.run_at, RetentionRun.report)
                .where(RetentionRun.run_at > after)
                .order_by(RetentionRun.run_at.asc())
                .limit(50)
            )
        ).all()
    except Exception:
        await db.rollback()
        return None
    for run_at, report in runs:
        if (report or {}).get("retention_enabled") is not False:
            return _aware(run_at)
    return None


async def _retention_history(db: AsyncSession) -> tuple[datetime | None, uuid.UUID | None]:
    """``(enabled_since, off_by_actor)`` (CONTRACT-CHANGE MD2-6 (3), adv-admin ADM-1).

    ``enabled_since`` is the latest of: the newest OFF→ON transition row of
    ``RETENTION_ENABLED``, the instance claim time, and the newest OFF EVIDENCE
    (a skipped run). After a skipped run it is the first run after it that did
    not skip, or else the skip itself — it never claims an ON period across a run
    that skipped. ``off_by_actor``: the actor of the newest panel change that set
    it to false.
    """
    rows = (
        await db.execute(
            select(AuditEvent)
            .where(AuditEvent.action.in_(_RETENTION_ACTIONS))
            # ADM-1: filter on the KEY, so 500 provider switches cannot push the
            # retention rows out of the window.
            .where(AuditEvent.detail["key"].as_string() == "RETENTION_ENABLED")
            .order_by(AuditEvent.at.desc())
            .limit(500)
        )
    ).scalars().all()
    since: datetime | None = None
    off_by: uuid.UUID | None = None
    off_by_seen = False
    for row in rows:
        detail = row.detail or {}
        if detail.get("key") != "RETENTION_ENABLED":
            continue
        turned_on = detail.get("from_value") is False and (
            row.action == "settings.reset" or detail.get("to_value") is True
        )
        if since is None and turned_on:
            since = row.at
        if not off_by_seen and row.action == "settings.changed" and detail.get("to_value") is False:
            off_by, off_by_seen = row.actor_user_id, True
    if since is None:
        claimed = (
            await db.execute(
                select(AuditEvent.at)
                .where(AuditEvent.action == "setup.claimed")
                .order_by(AuditEvent.at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        since = claimed
    since = _aware(since)
    off = await _last_off_evidence(db)
    if off is not None and (since is None or off >= since):
        since = await _first_enabled_run_after(db, off) or off
    return since, off_by


async def retention_block(db: AsyncSession) -> dict[str, Any]:
    enabled, source = instance_settings.retention_state()
    run = await _last_retention_run(db)
    report = (run.report or {}) if run is not None else {}
    skipped = report.get("retention_enabled") is False if run is not None else None
    since, off_by = await _retention_history(db)
    changed_by_email = None
    if not enabled and source == "panel" and off_by is not None:
        changed_by_email = (await instance_settings._emails(db, {off_by})).get(off_by)
    return {
        "enabled": enabled,
        "source": source,
        "last_run_at": _aware(run.run_at) if run is not None else None,
        "last_run_ok": run.ok if run is not None else None,
        "last_run_skipped": skipped,
        "enabled_since": since if enabled else None,
        "changed_by_email": changed_by_email,
        "ttl_days": ttl_days(),
    }


def ttl_days() -> dict[str, int]:
    """The effective TTLs (CONTRACT-CHANGE MD2-8). Read from ``constants`` at call
    time so a test's monkeypatch is honoured."""
    from applire import constants

    return {
        "uploads": int(constants.UPLOAD_TTL_DAYS),
        "interview_sessions": int(constants.INTERVIEW_SESSION_TTL_DAYS),
        "generated_documents": int(constants.GENERATED_DOCUMENTS_TTL_DAYS),
        "cancelled_applications": int(constants.CANCELLED_APPLICATION_TTL_DAYS),
        "profile_inactivity": int(constants.PROFILE_INACTIVITY_TTL_DAYS),
        "audit_log": int(getattr(settings, "audit_log_retention_days", 730) or 0),
    }


def _setting_notices(*, last_run_skipped: bool | None = None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    enabled, _source = instance_settings.retention_state()
    # ADM-1: also when the web process reads ON but the WORKER's newest run
    # skipped (its own environment, an unobserved flip): the run record is the truth.
    if not HAS_CLOUD and (not enabled or last_run_skipped):
        out.append({"code": "retention_disabled", "severity": _WARNING})
    if settings.llm_debug_log:
        out.append({"code": "debug_log_on", "severity": _WARNING})
    if (settings.applire_topology or "").strip().lower() == "dev":
        out.append({"code": "dev_topology", "severity": _WARNING})
    active = (settings.llm_provider or "").strip().lower()
    if active in instance_settings.KEY_REQUIRED and not instance_settings.provider_ready(active):
        out.append({"code": "provider_not_ready", "severity": _CRITICAL})
    for dep in instance_settings.dependencies():
        if dep["code"] == "ocr_needs_mistral_key" and not dep["satisfied"]:
            out.append({"code": "ocr_needs_mistral_key", "severity": _WARNING})
    if instance_settings.unreadable_secrets():
        out.append({"code": "settings_secret_unreadable", "severity": _CRITICAL})
    if _upgrade_notice():
        out.append({"code": "upgrade_notice", "severity": _WARNING})
    return out


def _health_notice(status: str | None) -> list[dict[str, str]]:
    if status == "down":
        return [{"code": "health_down", "severity": _CRITICAL}]
    if status == "degraded":
        return [{"code": "health_degraded", "severity": _WARNING}]
    return []


def _order(notices: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(notices, key=lambda n: (n["severity"] != _CRITICAL, n["code"]))


async def notices_cheap(db: AsyncSession) -> list[dict[str, str]]:
    """No probe runs: the health verdict is the ops layer's last report."""
    from applire.services.ops.aggregate import cached_summary

    summary = cached_summary()
    notices = _health_notice(summary["status"] if summary else None)
    run = await _last_retention_run(db)
    skipped = (run.report or {}).get("retention_enabled") is False if run is not None else None
    notices += _setting_notices(last_run_skipped=skipped)
    count, _items = await failed_jobs.collect(db)
    if count:
        notices.append({"code": "failed_jobs", "severity": _WARNING})
    return _order(notices)


async def dashboard(db: AsyncSession) -> dict[str, Any]:
    from applire.services.ops.aggregate import collect

    with unscoped("ops-aggregate"):
        # ADM-5: the page an admin opens to switch away from a slow provider
        # never waits on that provider — last known result + its age.
        report = await collect(db, with_usage=False, provider_inline=False)
    components = [
        {
            "name": name,
            "status": str(c.get("status", "unknown")),
            "checked_at": (c.get("detail") or {}).get("checked_at"),
        }
        for name, c in (report.get("components") or {}).items()
    ]
    family = (settings.llm_provider or "").strip().lower()
    checked = report.get("checked_at")
    health = {
        "status": report.get("status", "unknown"),
        "version": report.get("version", ""),
        "edition": report.get("edition", ""),
        "topology": settings.applire_topology,
        "debug_log_on": bool(settings.llm_debug_log),
        "llm_provider": family,
        "llm_model": str(getattr(settings, f"{family}_model", "") or ""),
        "checked_at": datetime.fromisoformat(checked) if isinstance(checked, str) else None,
        "components": components,
    }
    count, items = await failed_jobs.collect(db)
    emails = {u.id: u.email for u in await accounts.list_users(db)}
    retention = await retention_block(db)
    notices = _health_notice(health["status"]) + _setting_notices(
        last_run_skipped=retention["last_run_skipped"]
    )
    if count:
        notices.append({"code": "failed_jobs", "severity": _WARNING})
    return {
        "health": health,
        "users": await _user_counts(db),
        "usage_30d": await usage.totals_since(db, datetime.now(timezone.utc) - timedelta(days=30)),
        "failed_jobs": {
            "window_days": failed_jobs.WINDOW_DAYS,
            "count": count,
            "items": [
                {
                    "kind": j.kind,
                    "id": j.id,
                    "user_id": j.user_id,
                    "user_email": emails.get(j.user_id) if j.user_id else None,
                    "failed_at": j.failed_at,
                    "error_code": j.error_code,
                }
                for j in items
            ],
        },
        "upgrade_notice": _upgrade_notice(),
        "retention": retention,
        "notices": _order(notices),
    }
