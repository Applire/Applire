# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""F8 — services.safe_fetch.safe_get, Strawberry W0 form (signature + passthrough).

Package 4a (W2) owns this file afterwards: it replaces the passthrough test
with the refusal/redirect-re-check suite (ADR-092 cl. 15, RD-7).
"""

import inspect

import httpx
import pytest

from applire.services import safe_fetch
from applire.services.safe_fetch import UnsafeFetchRefused, safe_get


def test_frozen_signature():
    sig = inspect.signature(safe_get)
    params = sig.parameters
    assert list(params) == ["url", "timeout", "headers", "max_redirects"]
    assert params["url"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("timeout", "headers", "max_redirects"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["timeout"].default is inspect.Parameter.empty
    assert params["headers"].default is inspect.Parameter.empty
    assert params["max_redirects"].default == 5
    assert inspect.iscoroutinefunction(safe_get)
    assert issubclass(UnsafeFetchRefused, Exception)


@pytest.mark.asyncio
async def test_passthrough_follows_redirects_and_sends_headers(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "https://jobs.example/final"})
        return httpx.Response(200, text="posting body")

    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        captured.update(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    captured: dict = {}
    monkeypatch.setattr(safe_fetch.httpx, "AsyncClient", client_factory)

    resp = await safe_get(
        "https://jobs.example/start", timeout=3.0, headers={"User-Agent": "Applire-test"}
    )

    assert resp.status_code == 200
    assert resp.text == "posting body"
    assert [r.url.path for r in seen] == ["/start", "/final"]
    assert all(r.headers["User-Agent"] == "Applire-test" for r in seen)
    assert captured["max_redirects"] == 5
    assert captured["timeout"] == 3.0
