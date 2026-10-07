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

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.config import HAS_CLOUD, settings
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


async def retention_block(db: AsyncSession) -> dict[str, Any]:
    enabled, source = instance_settings.retention_state()
    run = await _last_retention_run(db)
    report = (run.report or {}) if run is not None else {}
    skipped = report.get("retention_enabled") is False if run is not None else None
    return {
        "enabled": enabled,
        "source": source,
        "last_run_at": _aware(run.run_at) if run is not None else None,
        "last_run_ok": run.ok if run is not None else None,
        "last_run_skipped": skipped,
    }


def _setting_notices() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    enabled, _source = instance_settings.retention_state()
    if not enabled and not HAS_CLOUD:
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
    notices += _setting_notices()
    count, _items = await failed_jobs.collect(db)
    if count:
        notices.append({"code": "failed_jobs", "severity": _WARNING})
    return _order(notices)


async def dashboard(db: AsyncSession) -> dict[str, Any]:
    from applire.services.ops.aggregate import collect

    with unscoped("ops-aggregate"):
        report = await collect(db, with_usage=False)
    components = [
        {"name": name, "status": str(c.get("status", "unknown"))}
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
    notices = _health_notice(health["status"]) + _setting_notices()
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
        "retention": await retention_block(db),
        "notices": _order(notices),
    }
