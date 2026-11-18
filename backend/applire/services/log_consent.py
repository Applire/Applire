# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A person's consent to content logs (ADR-091 amended 2026-11-18).

Exists only while the instance writes content logs (``LOG_LEVEL=DEBUG``,
:func:`applire.content_log.content_logging_on`). At ``INFO`` and above every
function here answers "not required" and nothing is stored or asked.

**States** (:func:`state`):

``not_required``  the level is above DEBUG
``missing``       never agreed
``outdated``      agreed to another version of the text, not withdrawn since
``withdrawn``     the last withdrawal is later than the last consent
``current``       agreed to the text shown today

**The version** is the copy's text version plus a short hash of the values the
text names (operator, contact, three recipients, the age limit). Changing a
recipient therefore asks everyone again.

**Gate.** :func:`consent_ok` feeds ``PayerFacts.log_consent_ok`` (ADR-096
amended): model work is admitted only with a current consent, and the content
writers drop the records of anyone else.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Literal

from applire.content_log import content_logging_on

logger = logging.getLogger(__name__)

#: Bumped when the consent copy (catalog ``logConsent.text*``) changes meaning.
TEXT_VERSION = "1"
#: The date printed on the text ("Stand 17.11.2026, Fassung 1").
TEXT_DATE = "2026-11-17"

State = Literal["not_required", "missing", "outdated", "withdrawn", "current"]

#: setting attribute -> environment variable (for the startup warning and the notice).
TEXT_SETTINGS: dict[str, str] = {
    "log_consent_operator_name": "LOG_CONSENT_OPERATOR_NAME",
    "log_consent_contact_email": "LOG_CONSENT_CONTACT_EMAIL",
    "log_consent_assistant_provider": "LOG_CONSENT_ASSISTANT_PROVIDER",
    "log_consent_log_host": "LOG_CONSENT_LOG_HOST",
    "log_consent_model_provider": "LOG_CONSENT_MODEL_PROVIDER",
}
#: The two whose absence the admin dashboard names (the controller and the contact).
NOTICE_SETTINGS = ("log_consent_operator_name", "log_consent_contact_email")


class ConsentVersionStale(Exception):
    """The person agreed to a text that is no longer the one shown."""


def retention_days() -> int:
    from applire.config import settings

    try:
        return max(0, int(settings.content_log_retention_days or 0))
    except (TypeError, ValueError):
        return 0


def text_values() -> dict:
    """The values the consent text names (empty string = unset → catalog fallback)."""
    from applire.config import settings

    return {
        "operator": (settings.log_consent_operator_name or "").strip(),
        "contact": (settings.log_consent_contact_email or "").strip(),
        "assistant_provider": (settings.log_consent_assistant_provider or "").strip(),
        "log_host": (settings.log_consent_log_host or "").strip(),
        "model_provider": (settings.log_consent_model_provider or "").strip(),
        "retention_days": retention_days(),
    }


def current_version() -> str:
    values = text_values()
    basis = "\x1f".join(
        str(values[k])
        for k in ("operator", "contact", "assistant_provider", "log_host", "model_provider", "retention_days")
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:8]
    return f"{TEXT_VERSION}-{digest}"


def missing_settings() -> list[str]:
    """The consent settings that are unset (environment variable names)."""
    from applire.config import settings

    return [env for attr, env in TEXT_SETTINGS.items() if not str(getattr(settings, attr, "") or "").strip()]


def missing_notice_settings() -> list[str]:
    from applire.config import settings

    return [
        TEXT_SETTINGS[attr]
        for attr in NOTICE_SETTINGS
        if not str(getattr(settings, attr, "") or "").strip()
    ]


def _withdrawn(user) -> bool:
    withdrawn = getattr(user, "log_consent_withdrawn_at", None)
    if withdrawn is None:
        return False
    agreed = getattr(user, "log_consent_at", None)
    return agreed is None or _aware(agreed) <= _aware(withdrawn)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def state(user) -> State:
    if not content_logging_on():
        return "not_required"
    if _withdrawn(user):
        return "withdrawn"
    if getattr(user, "log_consent_at", None) is None:
        return "missing"
    if getattr(user, "log_consent_version", None) != current_version():
        return "outdated"
    return "current"


def consent_ok(user) -> bool:
    """May this person's work run and be logged? Always True above DEBUG."""
    return state(user) in ("not_required", "current")


def record_consent(user, version: str | None) -> None:
    """Store an agreement to ``version`` (must be today's text)."""
    if version != current_version():
        raise ConsentVersionStale(version)
    user.log_consent_at = datetime.now(timezone.utc)
    user.log_consent_version = version


def withdraw(user) -> None:
    user.log_consent_withdrawn_at = datetime.now(timezone.utc)


def summary(user) -> dict:
    """``GET /api/me/log-consent`` and ``me.log_consent``."""
    st = state(user)
    out: dict = {"required": st != "not_required", "state": st}
    if st == "not_required":
        return out
    out.update(
        version=current_version(),
        consented_at=user.log_consent_at,
        consented_version=user.log_consent_version,
        withdrawn_at=user.log_consent_withdrawn_at,
        text=public_text(),
    )
    return out


def public_text() -> dict:
    """What the invite page needs before anyone is signed in (no personal data)."""
    values = text_values()
    return {
        "version": current_version(),
        "text_version": TEXT_VERSION,
        "text_date": TEXT_DATE,
        **values,
    }


def purge_horizon(row: dict | None) -> datetime | None | Literal["all"]:
    """How much of a person's log content the retention worker removes.

    ``row`` holds ``deleted_at``, ``log_consent_at``, ``log_consent_withdrawn_at``
    (``None`` = no such user). ``"all"``: every line; a datetime: lines older
    than it (the person withdrew and agreed again then); ``None``: nothing.
    """
    if row is None or row.get("deleted_at") is not None:
        return "all"
    withdrawn = row.get("log_consent_withdrawn_at")
    if withdrawn is None:
        return None
    agreed = row.get("log_consent_at")
    if agreed is None or _aware(agreed) <= _aware(withdrawn):
        return "all"
    return _aware(agreed)
