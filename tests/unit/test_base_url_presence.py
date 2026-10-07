# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""`APPLIRE_BASE_URL`: new default, presence-based "unset" (founder ruling S-1, 2026-10-07).

The default moved from `http://localhost:8001`, a port only the dev override
publishes, to `http://localhost`, the stock nginx. Three controls ask whether the
operator set the variable:
* MD-32: no mail without an explicit base URL.
* The origin check's Host allow-list (anti-rebinding).
* OIDC's redirect-URI check.

They used to ask by comparing the value with the default. They now ask whether a
source supplied it (`config.configured_base_url`). The ruling binds us to keep
MD-32 and the CSRF Host fallback exactly as they were for an install that does
not set the variable. These tests pin both directions, through the real
`Settings` class rather than a hand-set attribute where presence is the point.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from applire import config
from applire.auth import csrf, oidc
from applire.config import SHIPPED_DEFAULT_BASE_URL, Settings, configured_base_url, settings
from applire.services import mail
from applire.services.admin import links


def _fresh(monkeypatch, value: str | None) -> Settings:
    """A Settings built the way the app builds it, from the environment only."""
    if value is None:
        monkeypatch.delenv("APPLIRE_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("APPLIRE_BASE_URL", value)
    return Settings(_env_file=None)


def _post(host: str, origin: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/auth/login",
            "query_string": b"",
            "headers": [(b"host", host.encode()), (b"origin", origin.encode())],
        }
    )


@pytest.fixture
def smtp_on(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.org")
    monkeypatch.setattr(settings, "smtp_from", "applire@example.org")
    assert mail.smtp_enabled()


# ---------------------------------------------------------------------------
# The default and how "unset" is read
# ---------------------------------------------------------------------------


def test_the_shipped_default_is_the_stock_nginx_address(monkeypatch):
    s = _fresh(monkeypatch, None)
    assert s.applire_base_url == "http://localhost"
    # Presence, not value: the default survives as the marker object. Without
    # `validate_default=False` pydantic hands back a plain str and every install
    # would read as configured, so MD-32 would mail localhost links.
    assert isinstance(s.applire_base_url, config.ShippedDefaultBaseUrl)
    assert configured_base_url(s.applire_base_url) is None


def test_an_explicit_value_equal_to_the_default_counts_as_set(monkeypatch):
    """The reason for the ruling: README's `APPLIRE_BASE_URL=http://localhost` is a setting."""
    s = _fresh(monkeypatch, "http://localhost")
    assert type(s.applire_base_url) is str
    assert configured_base_url(s.applire_base_url) == "http://localhost"


def test_an_empty_value_is_unset_as_before(monkeypatch):
    s = _fresh(monkeypatch, "")
    assert configured_base_url(s.applire_base_url) is None
    assert configured_base_url("   ") is None


def test_the_old_default_written_out_is_now_a_setting(monkeypatch):
    """The one behaviour change, named in the upgrade note: an `.env` that spells out
    `http://localhost:8001` was read as unset by the value compare and is now set."""
    s = _fresh(monkeypatch, "http://localhost:8001/")
    assert configured_base_url(s.applire_base_url) == "http://localhost:8001"


# ---------------------------------------------------------------------------
# MD-32 — mail stays off while the variable is unset (unchanged behaviour)
# ---------------------------------------------------------------------------


def test_md32_unset_base_url_sends_no_mail_and_warns(monkeypatch, smtp_on):
    monkeypatch.setattr(settings, "applire_base_url", _fresh(monkeypatch, None).applire_base_url)
    assert links.mail_origin() is None
    assert links.mail_without_base_url_warning() == links.MAIL_WITHOUT_BASE_URL_WARNING


def test_md32_explicit_base_url_enables_mail(monkeypatch, smtp_on):
    monkeypatch.setattr(settings, "applire_base_url", _fresh(monkeypatch, "http://localhost").applire_base_url)
    assert links.mail_origin() == "http://localhost"
    assert links.mail_without_base_url_warning() is None


def test_md32_the_reexported_default_still_reads_unset(monkeypatch, smtp_on):
    """Existing tests simulate "unset" with `links.SHIPPED_DEFAULT_BASE_URL`; it is the marker."""
    monkeypatch.setattr(settings, "applire_base_url", links.SHIPPED_DEFAULT_BASE_URL)
    assert links.SHIPPED_DEFAULT_BASE_URL is SHIPPED_DEFAULT_BASE_URL
    assert csrf.SHIPPED_DEFAULT_BASE_URL is SHIPPED_DEFAULT_BASE_URL
    assert links.mail_origin() is None


# ---------------------------------------------------------------------------
# CSRF — the Host fallback is unchanged while the variable is unset
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["192.168.1.5", "applire.lan:8080", "localhost"])
def test_csrf_unset_base_url_keeps_the_host_fallback(monkeypatch, host):
    monkeypatch.setattr(settings, "applire_base_url", _fresh(monkeypatch, None).applire_base_url)
    assert csrf.configured_base_netloc() is None
    csrf.check_origin(_post(host, f"http://{host}"))  # same-origin: allowed, as before


def test_csrf_unset_base_url_still_refuses_a_cross_origin_post(monkeypatch):
    monkeypatch.setattr(settings, "applire_base_url", _fresh(monkeypatch, None).applire_base_url)
    with pytest.raises(Exception) as exc:
        csrf.check_origin(_post("192.168.1.5", "http://evil.example"))
    assert "origin_mismatch" in str(getattr(exc.value, "detail", exc.value))


def test_csrf_an_explicit_base_url_turns_on_the_host_allow_list(monkeypatch):
    monkeypatch.setattr(settings, "applire_base_url", _fresh(monkeypatch, "http://localhost").applire_base_url)
    assert csrf.configured_base_netloc() == ("http", "localhost")
    with pytest.raises(Exception):
        csrf.check_origin(_post("192.168.1.5", "http://192.168.1.5"))


# ---------------------------------------------------------------------------
# OIDC — needs an explicit base URL, and the new default is not one
# ---------------------------------------------------------------------------


def _oidc_on(monkeypatch):
    monkeypatch.setattr(settings, "oidc_issuer", "https://idp.example.org")
    monkeypatch.setattr(settings, "oidc_client_id", "applire")
    monkeypatch.setattr(settings, "oidc_client_secret", "secret")


def test_oidc_refuses_the_shipped_default(monkeypatch):
    _oidc_on(monkeypatch)
    monkeypatch.setattr(settings, "applire_base_url", _fresh(monkeypatch, None).applire_base_url)
    with pytest.raises(oidc.OidcConfigError):
        oidc.validate_config()


def test_oidc_accepts_an_explicit_localhost(monkeypatch):
    _oidc_on(monkeypatch)
    monkeypatch.setattr(settings, "applire_base_url", _fresh(monkeypatch, "http://localhost").applire_base_url)
    oidc.validate_config()
