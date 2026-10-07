# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Guards behind the adv-admin fixes (Strawberry build 2, fix-admin).

Each test pins a property the adversarial tests (``test_adv_admin_b2.py``) do not
reach on their own — the second spelling, the refusal's shape, the
single-flight — so a mutation of the guard turns a NAMED test red here.
Zero provider calls, zero network.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from applire import config as app_config
from applire.config import settings
from applire.models.audit import AuditEvent
from applire.models.instance_settings import InstanceSetting
from applire.models.retention_run import RetentionRun
from applire.services import instance_settings as svc
from applire.services import safe_fetch, scraper
from applire.services.ops import probes
from applire.services.scraper import ScraperError, scrape_job_url
from tests.unit.test_c1_instance_settings import (  # noqa: F401 — fixtures reused
    ORIGIN,
    SENTINEL,
    _clean_overlay,
    _put,
    env,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- ADM-2: the LinkedIn switch per hop, per tier-2 request, per URL reading ----------


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com\\@evil.example/jobs/view/1",   # WHATWG: \ is /
    "https://www.link%65din.com/jobs/view/1",                 # WHATWG percent-decodes the host
    "https://www.linked\tin.com/jobs/view/1",                 # WHATWG removes tab/CR/LF
    " https://lnkd.in/abc",                                   # WHATWG strips leading C0/space
])
def test_every_url_reading_is_judged(url):
    assert scraper.is_linkedin_url(url) is True


@pytest.mark.parametrize("url", [
    "https://linkedin.com.evil.example/",
    "https://notlinkedin.com/",
    "https://evil.example/?next=https://www.linkedin.com/",
])
def test_lookalikes_are_not_linkedin(url):
    assert scraper.is_linkedin_url(url) is False


@pytest.mark.asyncio
async def test_a_parser_differential_url_is_refused_before_any_fetch(monkeypatch):
    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", False)

    async def boom(*_a, **_k):
        raise AssertionError("fetched although the switch is off")

    monkeypatch.setattr(scraper, "_fetch_tier1", boom)
    monkeypatch.setattr(scraper, "_fetch_tier2", boom)
    with pytest.raises(ScraperError) as exc:
        await scrape_job_url("https://www.linkedin.com\\@evil.example/jobs/view/1")
    assert exc.value.code == "linkedin_guest_fetch_disabled"


@pytest.mark.asyncio
async def test_safe_get_asks_the_policy_for_every_hop_before_connecting(monkeypatch):
    async def _resolve(host, port):  # noqa: ARG001
        return ["93.184.216.34"]

    monkeypatch.setattr(safe_fetch, "_resolve", _resolve)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["host"])
        return httpx.Response(302, headers={"location": "https://blocked.example/x"})

    monkeypatch.setattr(safe_fetch, "_TRANSPORT", httpx.MockTransport(handler))
    asked: list[str] = []

    def policy(url: str) -> str | None:
        asked.append(url)
        return "nope" if "blocked.example" in url else None

    with pytest.raises(safe_fetch.PolicyRefused) as exc:
        await safe_fetch.safe_get("https://ok.example/a", timeout=2, headers=None, refuse=policy)
    assert exc.value.code == "nope"
    assert asked == ["https://ok.example/a", "https://blocked.example/x"]
    assert seen == ["ok.example"]  # the refused hop was never connected to


@pytest.mark.asyncio
async def test_a_redirect_to_linkedin_ends_with_the_switch_reason(monkeypatch):
    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", False)

    async def _resolve(host, port):  # noqa: ARG001
        return ["93.184.216.34"]

    monkeypatch.setattr(safe_fetch, "_resolve", _resolve)
    monkeypatch.setattr(safe_fetch, "_TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(302, headers={"location": "https://de.linkedin.com/jobs/view/9"})))

    async def no_tier2(_url):
        raise AssertionError("tier 2 must not run after a policy refusal")

    monkeypatch.setattr(scraper, "_fetch_tier2", no_tier2)
    with pytest.raises(ScraperError) as exc:
        await scrape_job_url("https://short.example/j")
    assert exc.value.code == "linkedin_guest_fetch_disabled"


class _Req:
    def __init__(self, url, resource_type="document"):
        self.url, self.resource_type, self.method = url, resource_type, "GET"


class _Route:
    def __init__(self, url, resource_type="document"):
        self.request = _Req(url, resource_type)
        self.aborted = False

    async def abort(self):
        self.aborted = True

    async def fulfill(self, **_kw):
        raise AssertionError("fulfilled a refused request")


@pytest.mark.asyncio
async def test_tier2_records_a_refused_navigation_so_the_scrape_names_the_switch(monkeypatch):
    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", False)
    hits: list[str] = []
    token = scraper._tier2_refusals.set(hits)
    try:
        await scraper._tier2_route(_Route("https://www.linkedin.com/jobs/view/1"))
        await scraper._tier2_route(_Route("https://www.linkedin.com/pixel.js", "script"))
    finally:
        scraper._tier2_refusals.reset(token)
    assert hits == ["linkedin_guest_fetch_disabled"]  # the document only, not the script


@pytest.mark.asyncio
async def test_tier2_with_the_switch_on_still_fetches_linkedin(monkeypatch):
    monkeypatch.setattr(settings, "scraper_fetch_linkedin_guest_pages", True)
    fetched: list[str] = []

    async def spy(url, **_kw):
        fetched.append(url)
        return httpx.Response(200, text="<html></html>")

    monkeypatch.setattr(scraper, "safe_get", spy)
    route = _Route("https://www.linkedin.com/jobs/view/1")
    route.fulfill = lambda **_kw: asyncio.sleep(0)  # type: ignore[method-assign]
    await scraper._tier2_route(route)
    assert fetched == ["https://www.linkedin.com/jobs/view/1"] and not route.aborted


# --- ADM-3 / 3b: one readiness rule for PUT and DELETE ---------------------------------


@pytest.mark.asyncio
async def test_deleting_the_active_providers_panel_key_is_409_naming_the_key(env, monkeypatch):
    db, client, _, _ = env
    monkeypatch.setattr(settings, "openrouter_api_key", "")  # env has no key
    assert (await _put(client, {"LLM_PROVIDER": "openrouter", "OPENROUTER_API_KEY": SENTINEL})).status_code == 200
    r = await client.delete("/api/admin/settings/OPENROUTER_API_KEY", headers=ORIGIN)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == {"error_code": "provider_not_ready", "message": "The provider has no API key.",
                                  "provider": "openrouter", "key": "OPENROUTER_API_KEY"}
    assert SENTINEL not in r.text
    assert await db.get(InstanceSetting, "OPENROUTER_API_KEY") is not None  # nothing removed


@pytest.mark.asyncio
async def test_deleting_an_inactive_providers_key_or_a_switch_is_never_refused(env, monkeypatch):
    _, client, _, _ = env
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "")      # already unready (env)
    assert (await _put(client, {"OPENROUTER_API_KEY": SENTINEL, "RETENTION_ENABLED": False})).status_code == 200
    assert (await client.delete("/api/admin/settings/OPENROUTER_API_KEY", headers=ORIGIN)).status_code == 200
    assert (await client.delete("/api/admin/settings/RETENTION_ENABLED", headers=ORIGIN)).status_code == 200


@pytest.mark.asyncio
async def test_a_put_that_does_not_switch_the_provider_is_not_refused_on_an_unready_instance(env, monkeypatch):
    _, client, _, _ = env
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert (await _put(client, {"SCRAPER_FETCH_LINKEDIN_GUEST_PAGES": False})).status_code == 200


@pytest.mark.asyncio
async def test_openai_with_a_base_url_needs_no_key(env, monkeypatch):
    _, client, _, _ = env
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "openai_base_url", "http://lmstudio.local:1234/v1")
    r = await _put(client, {"LLM_PROVIDER": "openai"})
    assert r.status_code == 200, r.text
    row = next(p for p in r.json()["providers"] if p["id"] == "openai")
    assert row["key_required"] is False and row["ready"] is True


# --- ADM-4 (panel side): an API key is printable ASCII -------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["sk-a b", "sk-\x7fx", "sk-éx", "sk-\x0cx", "sk-\x1bx", "sk-​x"])
async def test_a_key_outside_printable_ascii_is_refused_without_echo(env, bad):
    db, client, _, _ = env
    value = "sk-or-CHARSET-SENTINEL-" + bad
    r = await _put(client, {"OPENROUTER_API_KEY": value})
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "invalid_setting_value"
    assert r.json()["detail"]["key"] == "OPENROUTER_API_KEY"
    assert "CHARSET-SENTINEL" not in r.text
    assert (await db.execute(select(InstanceSetting))).scalars().first() is None


@pytest.mark.asyncio
async def test_a_printable_key_with_every_allowed_class_is_stored(env):
    _, client, _, _ = env
    r = await _put(client, {"OPENROUTER_API_KEY": "sk-or-v1_AZaz09.~!#$%&*+/=?@^|{}"})
    assert r.status_code == 200, r.text


# --- ADM-5: no provider ping for the dashboard; cache dropped only for provider writes --


def test_provider_relevance_of_a_write(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "requesty")
    assert svc.provider_relevant({"LLM_PROVIDER"})
    assert svc.provider_relevant({"REQUESTY_MODEL"})
    assert svc.provider_relevant({"REQUESTY_API_KEY"})
    assert not svc.provider_relevant({"SCRAPER_FETCH_LINKEDIN_GUEST_PAGES"})
    assert not svc.provider_relevant({"RETENTION_ENABLED"})
    assert not svc.provider_relevant({"OPENROUTER_MODEL", "OPENROUTER_API_KEY"})  # not the active one


@pytest.mark.asyncio
async def test_a_cold_cache_starts_one_background_check_and_answers_without_waiting(monkeypatch):
    from applire.services.ops import config as ops_config

    monkeypatch.setattr(ops_config, "provider_probe_enabled", lambda: True)
    probes.reset_provider_cache()
    monkeypatch.setattr(probes, "_kick_task", None)
    release = asyncio.Event()
    started: list[int] = []

    async def slow_probe(force: bool = False):  # noqa: ARG001
        started.append(1)
        await release.wait()
        return probes.ProbeResult("provider", probes.OK, "ok", {})

    monkeypatch.setattr(probes, "probe_provider", slow_probe)
    first = await asyncio.wait_for(probes.provider_result_without_probing(), timeout=1)
    second = await asyncio.wait_for(probes.provider_result_without_probing(), timeout=1)
    await asyncio.sleep(0)
    assert first.status == probes.UNKNOWN and second.status == probes.UNKNOWN
    assert len(started) == 1  # single-flight
    release.set()
    await probes._kick_task


@pytest.mark.asyncio
async def test_a_result_about_another_provider_is_never_shown_as_this_ones(monkeypatch):
    from applire.services.ops import config as ops_config

    monkeypatch.setattr(ops_config, "provider_probe_enabled", lambda: True)
    monkeypatch.setattr(probes, "kick_provider_probe", lambda: True)
    app_config.set_overlay_latest({"llm_provider": "requesty"})
    probes._provider_cache = (
        10**12, probes.ProbeResult("provider", probes.DOWN, "requesty down", {}),
        probes._provider_fingerprint(), "2026-10-07T12:00:00+00:00",
    )
    try:
        same = await probes.provider_result_without_probing()
        assert same.status == probes.DOWN and same.detail["checked_at"] == "2026-10-07T12:00:00+00:00"
        app_config.set_overlay_latest({"llm_provider": "openrouter"})
        other = await probes.provider_result_without_probing()
        assert other.status == probes.UNKNOWN
    finally:
        probes.reset_provider_cache()


# --- ADM-1: the proof line and the notice follow the worker's runs --------------------


@pytest.mark.asyncio
async def test_a_skipped_run_raises_the_retention_notice_while_the_panel_reads_on(env):
    db, client, _, _ = env
    db.add(RetentionRun(run_at=_now() - timedelta(hours=3), ok=True, duration_ms=1,
                        report={"retention_enabled": False, "retention_source": "env"}))
    await db.commit()
    items = (await client.get("/api/admin/notices")).json()["items"]
    assert {"code": "retention_disabled", "severity": "warning"} in items


@pytest.mark.asyncio
async def test_enabled_since_is_the_first_run_after_the_skip_that_did_not_skip(env):
    db, client, _, _ = env
    skip = _now() - timedelta(days=3)
    ok_run = _now() - timedelta(days=2)
    db.add(RetentionRun(run_at=skip, ok=True, duration_ms=1, report={"retention_enabled": False}))
    db.add(RetentionRun(run_at=ok_run, ok=True, duration_ms=1, report={"retention_enabled": True}))
    await db.commit()
    since = (await client.get("/api/admin/dashboard")).json()["retention"]["enabled_since"]
    assert abs(datetime.fromisoformat(since.replace("Z", "+00:00")) - ok_run) < timedelta(seconds=1)


@pytest.mark.asyncio
async def test_many_provider_switches_do_not_push_the_retention_transition_out(env):
    db, client, _, _ = env
    on_at = _now() - timedelta(days=5)
    db.add(AuditEvent(id=uuid.uuid4(), at=on_at, action="settings.changed",
                      detail={"key": "RETENTION_ENABLED", "from_value": False, "to_value": True}))
    for i in range(520):
        db.add(AuditEvent(id=uuid.uuid4(), at=on_at + timedelta(minutes=i + 1), action="settings.changed",
                          detail={"key": "OPENROUTER_MODEL", "to_value": f"m{i}"}))
    await db.commit()
    since = (await client.get("/api/admin/dashboard")).json()["retention"]["enabled_since"]
    assert abs(datetime.fromisoformat(since.replace("Z", "+00:00")) - on_at) < timedelta(seconds=1)


# --- ADM-7: a write is an upsert ----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_second_first_write_of_a_key_updates_the_row(env):
    """The race needs Postgres (tests/unit/test_adv_admin_b2_pg.py); here the
    statement shape: a second write of a key that another session inserted after
    this one loaded its rows is an UPDATE, not a duplicate-key error."""
    db, _, _, admin = env
    stale = await svc.load_rows(db)  # this "session" saw no row
    assert stale == []
    db.add(InstanceSetting(key="MISTRAL_MODEL", value="first", updated_at=_now()))
    await db.commit()
    await svc.apply_changes(db, actor_id=admin.id, changes={"MISTRAL_MODEL": "second"})
    await db.commit()
    rows = await svc.load_rows(db)
    assert [(r.key, r.value) for r in rows] == [("MISTRAL_MODEL", "second")]
