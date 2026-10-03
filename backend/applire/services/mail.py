# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional SMTP for invite and reset links (ADR-091 cl. 23–24, S-7, D-7).

Stdlib only: ``smtplib`` in a worker thread, 10 s timeout. ``SMTP_HOST`` empty =
mail is off — the admin hands the link over instead, and ``POST /api/auth/forgot``
sends nothing. A failed send is an ERROR log line and ``False`` — never an
exception into the request: the admin sees "pass the link on yourself instead"
(``IssuedLink.mail_failed``).

Bodies are the founder-approved texts in ``templates/mail/{invite,reset}.{de,en}.txt``
(W0-B gate): line 1 ``Subject: …``, a blank line, then the body; placeholders
``{link}``, ``{email}``, ``{expires_at}``, ``{instance_url}``. No tracking, no
remote content. The log never names the recipient or the link (the link is a
credential).
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Literal

from applire.config import settings

logger = logging.getLogger(__name__)

__all__ = ["MailConfig", "mail_config", "render", "send_link_mail", "send_mail", "smtp_enabled"]

Purpose = Literal["invite", "reset"]
SUPPORTED_LANGUAGES = ("de", "en")
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "mail"
SMTP_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class MailConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    security: str  # starttls | tls | none


def mail_config() -> MailConfig:
    """Read ``SMTP_*`` (declared by 1a in the ADR-087 registry; defaults per cl. 24)."""
    return MailConfig(
        host=(getattr(settings, "smtp_host", "") or "").strip(),
        port=int(getattr(settings, "smtp_port", 587) or 587),
        username=getattr(settings, "smtp_username", "") or "",
        password=getattr(settings, "smtp_password", "") or "",
        sender=(getattr(settings, "smtp_from", "") or "").strip(),
        security=(getattr(settings, "smtp_security", "starttls") or "starttls").strip().lower(),
    )


def smtp_enabled() -> bool:
    return bool(mail_config().host)


def _language(lang: str | None) -> str:
    code = (lang or "").split("-")[0].lower()
    return code if code in SUPPORTED_LANGUAGES else "en"


def format_expiry(expires_at: datetime, lang: str) -> str:
    """The instance's local time, in the recipient's language convention."""
    local = expires_at.astimezone()
    tz = local.strftime("%Z")
    if _language(lang) == "de":
        text = local.strftime("%d.%m.%Y, %H:%M Uhr")
    else:
        text = local.strftime("%Y-%m-%d %H:%M")
    return f"{text} ({tz})" if tz else text


def render(
    purpose: Purpose,
    lang: str | None,
    *,
    link: str,
    email: str,
    expires_at: datetime,
    instance_url: str,
) -> tuple[str, str]:
    """Return ``(subject, body)`` from the approved template."""
    code = _language(lang)
    raw = (TEMPLATE_DIR / f"{purpose}.{code}.txt").read_text(encoding="utf-8")
    head, _, body = raw.partition("\n\n")
    if not head.startswith("Subject: "):
        raise ValueError(f"mail template {purpose}.{code} has no Subject line")
    values = {
        "link": link,
        "email": email,
        "expires_at": format_expiry(expires_at, code),
        "instance_url": instance_url,
    }
    return head[len("Subject: "):].strip(), body.format_map(values)


def _send_sync(cfg: MailConfig, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.sender or cfg.username
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    if cfg.security == "tls":
        client: smtplib.SMTP = smtplib.SMTP_SSL(
            cfg.host, cfg.port, timeout=SMTP_TIMEOUT_SECONDS, context=ssl.create_default_context()
        )
    else:
        client = smtplib.SMTP(cfg.host, cfg.port, timeout=SMTP_TIMEOUT_SECONDS)
    with client:
        if cfg.security == "starttls":
            client.starttls(context=ssl.create_default_context())
        if cfg.username:
            client.login(cfg.username, cfg.password)
        client.send_message(msg)


async def send_mail(to: str, subject: str, body: str) -> bool:
    """Send one plain-text mail; ``False`` (and an ERROR log) on any failure."""
    cfg = mail_config()
    if not cfg.host:
        return False
    try:
        await asyncio.to_thread(_send_sync, cfg, to, subject, body)
    except Exception as exc:  # noqa: BLE001 — any SMTP/network failure means "hand it over"
        logger.error("SMTP send failed (%s: %s) — the link must be handed over instead",
                     type(exc).__name__, str(exc)[:200])
        return False
    return True


async def send_link_mail(
    purpose: Purpose,
    *,
    to: str,
    lang: str | None,
    link: str,
    expires_at: datetime,
    instance_url: str,
) -> bool:
    subject, body = render(purpose, lang, link=link, email=to, expires_at=expires_at,
                           instance_url=instance_url)
    return await send_mail(to, subject, body)
