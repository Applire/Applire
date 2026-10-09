# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The safe outbound fetcher (ADR-092 cl. 15, RD-7, SF-SCRAPER.2 / SF-SCRAPER.6; US334).

Hermetic: the resolver (``safe_fetch._resolve``) and the transport
(``safe_fetch._TRANSPORT``) are replaced — no DNS, no socket. Each refused range,
each redirect hop and the DNS-rebinding case has its own named test so a
mutation of the guard names the test it kills.
"""

from __future__ import annotations

import httpx
import pytest

from applire.services import safe_fetch
from applire.services.safe_fetch import UnsafeFetchRefused, check_address, safe_get

PUBLIC = "93.184.216.34"
PUBLIC_V6 = "2606:2800:220:1:248:1893:25c8:1946"


class _Net:
    """A fake DNS + web: ``dns`` maps host → list of answers (consumed in order
    when a list of lists), ``pages`` maps (pinned host, path) → response."""

    def __init__(self, dns: dict, pages: dict | None = None) -> None:
        self.dns = dns
        self.pages = pages or {}
        self.resolved: list[str] = []
        self.requests: list[httpx.Request] = []

    async def resolve(self, host: str, port: int) -> list[str]:
        self.resolved.append(host)
        answer = self.dns[host]
        if answer and isinstance(answer[0], list):  # successive answers (rebinding)
            return answer.pop(0)
        return answer

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.url.host, request.url.path)
        if key not in self.pages:
            return httpx.Response(404, text="nothing here")
        return self.pages[key]()


@pytest.fixture
def net(monkeypatch):
    def install(dns: dict, pages: dict | None = None) -> _Net:
        n = _Net(dns, pages)
        monkeypatch.setattr(safe_fetch, "_resolve", n.resolve)
        monkeypatch.setattr(safe_fetch, "_TRANSPORT", httpx.MockTransport(n.handler))
        return n

    return install


def _ok(text: str = "posting"):
    return lambda: httpx.Response(200, text=text)


def _redirect(location: str, status: int = 302):
    return lambda: httpx.Response(status, headers={"location": location})


# ---------------------------------------------------------------------------
# Address ranges — one test per refused range (each a mutation target)
# ---------------------------------------------------------------------------

REFUSED = {
    "loopback_v4": "127.0.0.1",
    "loopback_v4_other": "127.10.0.5",
    "loopback_v6": "::1",
    "private_10": "10.1.2.3",
    "private_172": "172.16.5.4",
    "private_192": "192.168.1.10",
    "ula_v6": "fd12:3456:789a::1",
    "link_local_v4": "169.254.10.20",
    "metadata_v4": "169.254.169.254",
    "link_local_v6": "fe80::1",
    "link_local_v6_scoped": "fe80::1%eth0",
    "cgnat": "100.64.0.1",
    "alibaba_metadata": "100.100.100.200",
    "unspecified_v4": "0.0.0.0",
    "unspecified_v6": "::",
    "multicast_v4": "224.0.0.1",
    "multicast_v6": "ff02::1",
    "broadcast": "255.255.255.255",
    "documentation": "192.0.2.10",
    "benchmarking": "198.18.0.1",
    "reserved_240": "240.0.0.1",
    "mapped_loopback": "::ffff:127.0.0.1",
    "mapped_private": "::ffff:10.0.0.1",
    "mapped_metadata": "::ffff:169.254.169.254",
    "nat64_loopback": "64:ff9b::7f00:1",
    "sixtofour_private": "2002:0a00:0001::1",
}


@pytest.mark.parametrize("address", list(REFUSED.values()), ids=list(REFUSED))
def test_check_address_refuses_the_range(address):
    with pytest.raises(UnsafeFetchRefused):
        check_address(address)


@pytest.mark.parametrize("address", [PUBLIC, PUBLIC_V6, "8.8.8.8", "2a00:1450:4001:80b::200e"])
def test_check_address_allows_public_unicast(address):
    check_address(address)  # no raise


@pytest.mark.asyncio
@pytest.mark.parametrize("address", list(REFUSED.values()), ids=list(REFUSED))
async def test_safe_get_refuses_a_host_resolving_into_the_range(net, address):
    n = net({"jobs.example": [address]}, {(address.split("%")[0], "/"): _ok()})
    with pytest.raises(UnsafeFetchRefused):
        await safe_get("http://jobs.example/", timeout=5, headers=None)
    assert n.requests == [], "a refused host must never be connected to"


@pytest.mark.asyncio
async def test_ip_literal_url_is_checked_too(net):
    n = net({"127.0.0.1": ["127.0.0.1"]})
    with pytest.raises(UnsafeFetchRefused):
        await safe_get("http://127.0.0.1:8001/health", timeout=5, headers=None)
    assert n.requests == []


@pytest.mark.asyncio
async def test_a_mixed_answer_is_refused_even_if_the_first_is_public(net):
    """A public + private answer is a rebinding set-up — never pick the public one."""
    n = net({"jobs.example": [PUBLIC, "10.0.0.5"]}, {(PUBLIC, "/"): _ok()})
    with pytest.raises(UnsafeFetchRefused):
        await safe_get("http://jobs.example/", timeout=5, headers=None)
    assert n.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://jobs.example/x", "gopher://x/", "http:///nohost"])
async def test_only_http_and_https_with_a_host(net, url):
    net({})
    with pytest.raises(UnsafeFetchRefused):
        await safe_get(url, timeout=5, headers=None)


@pytest.mark.asyncio
async def test_unresolvable_host_is_refused(monkeypatch):
    async def _fail(host, port):  # noqa: ANN001
        raise OSError("Name or service not known")

    monkeypatch.setattr(safe_fetch, "_resolve", _fail)
    with pytest.raises(UnsafeFetchRefused):
        await safe_get("http://nowhere.invalid/", timeout=5, headers=None)


# ---------------------------------------------------------------------------
# Pinning (DNS rebinding) — the connection goes to the CHECKED address
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connection_is_pinned_to_the_checked_address(net):
    n = net({"jobs.example": [PUBLIC]}, {(PUBLIC, "/job/1"): _ok("the posting")})
    resp = await safe_get("https://jobs.example/job/1", timeout=5, headers={"User-Agent": "UA"})
    assert resp.text == "the posting"
    [req] = n.requests
    assert req.url.host == PUBLIC, "connect to the IP that was checked, not the name"
    assert req.headers["host"] == "jobs.example"
    assert req.extensions.get("sni_hostname") == "jobs.example", "TLS verified against the name"
    assert req.headers["user-agent"] == "UA"
    assert str(resp.url) == "https://jobs.example/job/1", "callers see the name URL"


@pytest.mark.asyncio
async def test_rebinding_second_answer_is_never_used(net):
    """The name answers public on the check and loopback afterwards — the fetch
    must still go to the checked public address (resolved ONCE per hop)."""
    n = net(
        {"rebind.example": [[PUBLIC], ["127.0.0.1"], ["127.0.0.1"]]},
        {(PUBLIC, "/"): _ok("public page")},
    )
    resp = await safe_get("http://rebind.example/", timeout=5, headers=None)
    assert resp.text == "public page"
    assert [r.url.host for r in n.requests] == [PUBLIC]
    assert n.resolved == ["rebind.example"], "resolved once for the one hop"


@pytest.mark.asyncio
async def test_ipv6_answer_is_pinned_in_brackets_with_port(net):
    n = net({"v6.example": [PUBLIC_V6]}, {(PUBLIC_V6, "/p"): _ok("v6")})
    resp = await safe_get("http://v6.example:8080/p", timeout=5, headers=None)
    assert resp.text == "v6"
    [req] = n.requests
    assert req.url.host == PUBLIC_V6 and req.url.port == 8080
    assert req.headers["host"] == "v6.example:8080"


# ---------------------------------------------------------------------------
# Redirects — every hop re-checked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redirect_to_an_internal_host_is_refused(net):
    n = net(
        {"jobs.example": [PUBLIC], "internal.example": ["10.0.0.7"]},
        {(PUBLIC, "/"): _redirect("http://internal.example/admin")},
    )
    with pytest.raises(UnsafeFetchRefused):
        await safe_get("http://jobs.example/", timeout=5, headers=None)
    assert [r.url.host for r in n.requests] == [PUBLIC], "the internal hop is never connected"


@pytest.mark.asyncio
async def test_redirect_to_a_metadata_ip_literal_is_refused(net):
    n = net(
        {"jobs.example": [PUBLIC], "169.254.169.254": ["169.254.169.254"]},
        {(PUBLIC, "/"): _redirect("http://169.254.169.254/latest/meta-data/", 301)},
    )
    with pytest.raises(UnsafeFetchRefused):
        await safe_get("http://jobs.example/", timeout=5, headers=None)
    assert len(n.requests) == 1


@pytest.mark.asyncio
async def test_redirect_hop_is_resolved_and_pinned_again(net):
    other = "93.184.216.35"
    n = net(
        {"jobs.example": [PUBLIC], "cdn.example": [other]},
        {
            (PUBLIC, "/a"): _redirect("https://cdn.example/b", 307),
            (other, "/b"): _redirect("/c", 308),  # relative Location
            (other, "/c"): _ok("final"),
        },
    )
    resp = await safe_get("http://jobs.example/a", timeout=5, headers=None)
    assert resp.text == "final"
    assert [(r.url.host, r.url.path) for r in n.requests] == [
        (PUBLIC, "/a"), (other, "/b"), (other, "/c"),
    ]
    assert n.resolved == ["jobs.example", "cdn.example", "cdn.example"]
    assert str(resp.url) == "https://cdn.example/c"


@pytest.mark.asyncio
async def test_too_many_redirects_is_refused(net):
    pages = {(PUBLIC, f"/{i}"): _redirect(f"/{i + 1}") for i in range(10)}
    net({"loop.example": [PUBLIC]}, pages)
    with pytest.raises(UnsafeFetchRefused):
        await safe_get("http://loop.example/0", timeout=5, headers=None, max_redirects=3)


@pytest.mark.asyncio
async def test_non_redirect_status_is_returned_as_is(net):
    net({"jobs.example": [PUBLIC]})
    resp = await safe_get("http://jobs.example/missing", timeout=5, headers=None)
    assert resp.status_code == 404


def test_environment_proxies_are_ignored():
    """A proxy would resolve the name again and defeat the pinning."""
    import inspect

    assert "trust_env=False" in inspect.getsource(safe_get)


# ---------------------------------------------------------------------------
# Scraper seams (both tiers) — a refused URL ends the scrape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scraper_tier1_refused_url_is_a_scraper_error_and_never_reaches_tier2(net, monkeypatch):
    from applire.services import scraper

    net({"intranet.example": ["10.0.0.9"]})
    tier2_called = []

    async def _tier2(url):  # noqa: ANN001
        tier2_called.append(url)
        return "x" * 500

    monkeypatch.setattr(scraper, "_fetch_tier2", _tier2)
    with pytest.raises(scraper.ScraperError) as exc:
        await scraper.scrape_job_url("http://intranet.example/jobs/1")
    assert exc.value.code == "jd_fetch_failed"
    assert "cannot be fetched" in exc.value.reason
    assert tier2_called == []


@pytest.mark.asyncio
async def test_scraper_tier2_prechecks_before_launching_a_browser(net, monkeypatch):
    from applire.services import scraper

    net({"www.stepstone.de": ["127.0.0.1"]})

    def _no_browser(*_a, **_k):
        raise AssertionError("Chromium must not start for a refused URL")

    monkeypatch.setattr("playwright.async_api.async_playwright", _no_browser)
    with pytest.raises(scraper.ScraperError) as exc:
        await scraper.scrape_job_url("https://www.stepstone.de/job/1")
    assert "cannot be fetched" in exc.value.reason


class _FakeRoute:
    def __init__(self, url: str, method: str = "GET", resource_type: str = "document") -> None:
        self.request = type("R", (), {"url": url, "method": method, "resource_type": resource_type})()
        self.aborted = False
        self.fulfilled: dict | None = None
        self.continued = False

    async def abort(self, *_a, **_k):
        self.aborted = True

    async def fulfill(self, **kw):
        self.fulfilled = kw

    async def continue_(self, *_a, **_k):  # pragma: no cover — must never be called
        self.continued = True


@pytest.mark.asyncio
async def test_tier2_route_aborts_a_subrequest_to_an_internal_host(net):
    from applire.services.scraper import _tier2_route

    n = net({"internal.example": ["192.168.0.2"]})
    route = _FakeRoute("http://internal.example/api/secret", resource_type="xhr")
    await _tier2_route(route)
    assert route.aborted and route.fulfilled is None and not route.continued
    assert n.requests == []


@pytest.mark.asyncio
async def test_tier2_route_fulfils_public_gets_from_the_pinned_fetch(net):
    from applire.services.scraper import _tier2_route

    n = net(
        {"www.stepstone.de": [PUBLIC]},
        {(PUBLIC, "/job/1"): lambda: httpx.Response(200, text="<main>posting</main>",
                                                    headers={"content-type": "text/html"})},
    )
    route = _FakeRoute("https://www.stepstone.de/job/1")
    await _tier2_route(route)
    assert not route.aborted and not route.continued
    assert route.fulfilled["status"] == 200
    assert route.fulfilled["body"] == b"<main>posting</main>"
    assert [r.url.host for r in n.requests] == [PUBLIC], "Chromium never connects itself"


@pytest.mark.asyncio
async def test_tier2_route_aborts_non_get_and_skipped_resources(net):
    from applire.services.scraper import _tier2_route

    n = net({"www.stepstone.de": [PUBLIC]})
    post = _FakeRoute("https://www.stepstone.de/api", method="POST", resource_type="xhr")
    img = _FakeRoute("https://www.stepstone.de/logo.png", resource_type="image")
    await _tier2_route(post)
    await _tier2_route(img)
    assert post.aborted and img.aborted
    assert n.requests == []


@pytest.mark.asyncio
async def test_tier2_installs_the_route_guard_before_navigating(net, monkeypatch):
    """The guard must be on the page BEFORE goto, or the first request escapes."""
    from unittest.mock import AsyncMock, MagicMock

    from applire.services import scraper

    net({"www.stepstone.de": [PUBLIC]})
    calls: list[str] = []
    page = MagicMock()
    page.route = AsyncMock(side_effect=lambda *a, **k: calls.append("route"))
    page.goto = AsyncMock(side_effect=lambda *a, **k: calls.append("goto"))
    page.wait_for_selector = AsyncMock()
    page.content = AsyncMock(return_value="<html><body><main>" + "Engineer " * 60 + "</main></body></html>")
    browser = MagicMock()
    browser.new_page = AsyncMock(return_value=page)
    browser.close = AsyncMock()
    pw = MagicMock()
    pw.chromium.launch = AsyncMock(return_value=browser)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=pw)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("playwright.async_api.async_playwright", lambda: cm)
    text = await scraper._fetch_tier2("https://www.stepstone.de/job/1")
    assert "Engineer" in text
    assert calls == ["route", "goto"]
    assert page.route.call_args.args == ("**/*", scraper._tier2_route)


def test_frozen_signature():
    """F8 stays frozen — 3d's colour detection codes against it."""
    import inspect

    sig = inspect.signature(safe_get)
    params = sig.parameters
    # adv-admin ADM-2 (build 2): one ADDITIVE keyword-only ``refuse`` (default
    # None) — a per-hop policy predicate; every existing caller is unchanged.
    assert list(params) == ["url", "timeout", "headers", "max_redirects", "refuse"]
    assert params["refuse"].default is None
    assert params["url"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("timeout", "headers", "max_redirects", "refuse"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["timeout"].default is inspect.Parameter.empty
    assert params["headers"].default is inspect.Parameter.empty
    assert params["max_redirects"].default == 5
    assert inspect.iscoroutinefunction(safe_get)
    assert issubclass(UnsafeFetchRefused, Exception)
