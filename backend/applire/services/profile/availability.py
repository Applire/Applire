# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The notice period's reminder condition (ADR-063 amended 2026-10-22, D-B3V7;
rulings 787:L3 + V-4).

ONE function decides when the dashboard reminds the candidate that the notice
period is missing or out of date — the profile response carries its answer
(``availability_reminder``) and no other reader re-derives it (ADR-066).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from applire.constants import AVAILABILITY_STALE_AFTER_DAYS
from applire.schemas.profile import MasterProfileData

Reminder = Literal["missing", "stale"]


def _parse(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def availability_reminder(
    profile: MasterProfileData, *, today: date | None = None
) -> Reminder | None:
    """``"missing"`` (no text), ``"stale"`` (recorded more than
    ``AVAILABILITY_STALE_AFTER_DAYS`` ago) or ``None``.

    A close (``reminder_dismissed_at``) hides the reminder until the value
    changes (every write clears the close) or another stale period has passed
    since the close (ruling V-4)."""
    today = today or today_utc()
    period = timedelta(days=AVAILABILITY_STALE_AFTER_DAYS)
    availability = profile.availability
    text = (availability.text or "").strip() if availability else ""
    if not text:
        reason: Reminder = "missing"
    else:
        recorded = _parse(availability.recorded_at)
        if recorded is not None and today - recorded <= period:
            return None
        reason = "stale"
    dismissed = _parse(availability.reminder_dismissed_at) if availability else None
    if dismissed is not None and today - dismissed <= period:
        return None
    return reason
