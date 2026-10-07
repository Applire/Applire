# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The origin check (ADR-091 cl. 12, revision 3; SF-IAM.5)."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from applire.auth import csrf
from applire.auth.csrf import check_origin, needs_cookie_csrf_check


def _req(method="POST", **headers):
    return SimpleNamespace(method=method, headers={k.replace("_", "-"): v for k, v in headers.items()})


@pytest.fixture
def base(monkeypatch):
    def set_base(value):
        monkeypatch.setattr(csrf.settings, "applire_base_url", value)

    # The shipped default = "not set". Presence, not value (ruling S-1): the marker
    # object, never a string that merely equals it.
    set_base(csrf.SHIPPED_DEFAULT_BASE_URL)
    return set_base


def _refused(req):
    with pytest.raises(HTTPException) as exc:
        check_origin(req)
    assert exc.value.status_code == 403
    assert exc.value.detail["error_code"] == "origin_mismatch"
    return exc.value.detail["message"]


def test_safe_methods_pass_without_any_header(base):
    check_origin(_req("GET"))
    check_origin(_req("HEAD"))


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_unsafe_without_origin_or_referer_is_refused(base, method):
    msg = _refused(_req(method, host="nas.local"))
    assert "forward the Host header" in msg and "APPLIRE_BASE_URL" in msg


def test_origin_null_is_refused(base):
    msg = _refused(_req(host="nas.local", origin="null"))
    assert "Origin: null" in msg  # refused for being opaque, named as such


def test_same_netloc_passes_and_the_port_matters(base):
    check_origin(_req(host="nas.local:8090", origin="http://nas.local:8090"))
    _refused(_req(host="nas.local:8090", origin="http://nas.local:3000"))  # same-site, other app
    _refused(_req(host="nas.local", origin="http://nas.local:8090"))  # nginx $host drops the port


def test_default_ports_are_normalised(base):
    check_origin(_req(host="nas.local", origin="http://nas.local:80"))
    check_origin(_req(host="nas.local:443", origin="https://nas.local"))


def test_referer_is_the_fallback(base):
    check_origin(_req(host="nas.local", referer="http://nas.local/login?next=/"))
    _refused(_req(host="nas.local", referer="http://evil.example/x"))


def test_cross_site_is_refused(base):
    _refused(_req(host="nas.local", origin="http://evil.example"))


def test_a_configured_base_url_is_also_accepted(base):
    base("https://applire.example.org")
    # TLS proxy rewrote Host to the upstream — the base URL still matches.
    check_origin(_req(host="localhost", origin="https://applire.example.org"))


def test_a_configured_base_url_allow_lists_host_against_rebinding(base):
    base("https://applire.example.org")
    _refused(_req(host="rebind.attacker.example", origin="http://rebind.attacker.example"))
    check_origin(_req(host="127.0.0.1:8001", origin="http://127.0.0.1:8001"))


def test_the_shipped_default_base_url_counts_as_unset(base):
    # With the default, Host is not allow-listed — a LAN name works.
    check_origin(_req(host="nas.local", origin="http://nas.local"))
    _refused(_req(host="nas.local", origin="http://localhost:8001"))


def test_cookie_check_applies_only_to_unsafe_cookie_requests_without_authorization():
    assert needs_cookie_csrf_check(_req("POST", cookie="applire_session=x")) is True
    assert needs_cookie_csrf_check(_req("GET", cookie="applire_session=x")) is False
    assert needs_cookie_csrf_check(_req("POST")) is False
    assert needs_cookie_csrf_check(
        _req("POST", cookie="applire_session=x", authorization="Bearer apl_x")
    ) is False
