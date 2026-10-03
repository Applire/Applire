# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Optional SMTP and the approved mail texts (ADR-091 cl. 24, S-7)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest

from applire.services import mail

EXP = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


class FakeSMTP:
    sent: list = []
    calls: list = []
    fail: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        FakeSMTP.calls.append(("connect", host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        FakeSMTP.calls.append(("starttls",))

    def login(self, u, p):
        FakeSMTP.calls.append(("login", u))

    def send_message(self, msg):
        if FakeSMTP.fail:
            raise FakeSMTP.fail
        FakeSMTP.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.calls, FakeSMTP.fail = [], [], None
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(mail, "mail_config", lambda: mail.MailConfig(
        host="smtp.example.org", port=587, username="u", password="p",
        sender="applire@example.org", security="starttls"))
    return FakeSMTP


def test_template_set_is_complete_and_shaped():
    names = sorted(p.name for p in mail.TEMPLATE_DIR.glob("*.txt"))
    assert names == ["invite.de.txt", "invite.en.txt", "reset.de.txt", "reset.en.txt"]
    for name in names:
        raw = (mail.TEMPLATE_DIR / name).read_text("utf-8")
        assert raw.startswith("Subject: ") and "\n\n" in raw
        for ph in ("{link}", "{email}", "{expires_at}", "{instance_url}"):
            assert ph in raw, (name, ph)
        assert "kontakt@" not in raw and "http" not in raw  # no fixed address, no remote content


@pytest.mark.parametrize("purpose", ["invite", "reset"])
@pytest.mark.parametrize("lang,subject_word", [("de", "Applire"), ("en", "Applire"), ("fr", "Applire")])
def test_render_fills_every_placeholder(purpose, lang, subject_word):
    subject, body = mail.render(purpose, lang, link="http://h/x#tok", email="p@example.org",
                                expires_at=EXP, instance_url="http://h")
    assert subject_word in subject and not subject.startswith("Subject")
    assert "http://h/x#tok" in body and "http://h" in body
    assert "{" not in body and "}" not in body


def test_render_falls_back_to_english():
    s_en, _ = mail.render("invite", "en", link="l", email="e", expires_at=EXP, instance_url="i")
    s_fr, _ = mail.render("invite", "fr-FR", link="l", email="e", expires_at=EXP, instance_url="i")
    s_de, _ = mail.render("invite", "de-DE", link="l", email="e", expires_at=EXP, instance_url="i")
    assert s_fr == s_en and s_de != s_en


@pytest.mark.asyncio
async def test_mail_off_sends_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(mail.smtplib, "SMTP", lambda *a, **k: called.append(a))
    monkeypatch.setattr(mail, "mail_config", lambda: mail.MailConfig("", 587, "", "", "", "starttls"))
    assert mail.smtp_enabled() is False
    assert await mail.send_mail("p@example.org", "s", "b") is False
    assert called == []


@pytest.mark.asyncio
async def test_send_uses_starttls_login_and_timeout(smtp):
    ok = await mail.send_link_mail("reset", to="p@example.org", lang="de", link="http://h/reset#t",
                                   expires_at=EXP, instance_url="http://h")
    assert ok is True
    assert smtp.calls[0] == ("connect", "smtp.example.org", 587, 10)
    assert ("starttls",) in smtp.calls and ("login", "u") in smtp.calls
    msg = smtp.sent[0]
    assert msg["To"] == "p@example.org" and msg["From"] == "applire@example.org"
    assert "http://h/reset#t" in msg.get_content()


@pytest.mark.asyncio
async def test_send_failure_returns_false_and_logs_no_recipient_or_link(smtp, caplog):
    smtp.fail = OSError("connection refused")
    with caplog.at_level(logging.ERROR, logger="applire.services.mail"):
        ok = await mail.send_link_mail("invite", to="secret-person@example.org", lang="en",
                                       link="http://h/invite#SECRETTOKEN", expires_at=EXP,
                                       instance_url="http://h")
    assert ok is False
    text = caplog.text
    assert "SMTP send failed" in text
    assert "secret-person" not in text and "SECRETTOKEN" not in text
