# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ops layer's probes, each with its faked failure (ADR-086 clause 6, US312).

Hermetic: an in-memory SQLite database, a faked ``shutil.disk_usage``, a stubbed
provider. No Docker, no network, no real provider.

The load-bearing assertions are not "the happy path returns ok" — they are the
three properties the FMEA credits rest on:

* ``unknown`` never counts as ``ok`` (``SF-OPS.4``);
* no probe may raise, and the exception's MESSAGE never reaches the payload
  (``SF-OPS.5``);
* every probe in the registry reaches the verdict, so a probe added in a later
  flavour fails a named test instead of silently not counting.
"""

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.db.session import Base
from applire.models.llm_usage import LlmUsage
from applire.models.retention_run import RetentionRun
from applire.services.ops import aggregate, probes
from applire.services.ops import config as ops_config

_SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(_SQLITE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[RetentionRun.__table__, LlmUsage.__table__],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def db_with_instance_state():
    """A database that also has US310's `instance_state` table (migration 0062)."""
    engine = create_async_engine(_SQLITE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[RetentionRun.__table__, LlmUsage.__table__],
        )
        await conn.run_sync(probes._ops_metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clean_module_state():
    probes.reset_provider_cache()
    aggregate.reset_state()
    from applire.services.ops import errors

    errors.reset()
    yield
    probes.reset_provider_cache()
    aggregate.reset_state()


# ── run_probe: nothing may raise, and nothing may leak ────────────────────────


@pytest.mark.asyncio
async def test_a_raising_probe_becomes_unknown_and_never_propagates():
    async def boom():
        raise RuntimeError("connect to postgres://applire:hunter2@db:5432/applire failed")

    result = await probes.run_probe("boom", boom)
    assert result.status == probes.UNKNOWN


@pytest.mark.asyncio
async def test_a_raising_probe_does_not_echo_the_exception_message():
    """SF-OPS.5: an exception message can carry a DSN, a path or a key."""

    async def boom():
        raise RuntimeError("postgres://applire:hunter2@db:5432/applire")

    result = await probes.run_probe("boom", boom)
    serialised = f"{result.status} {result.message} {result.detail}"
    assert "hunter2" not in serialised
    assert "postgres://" not in serialised
    assert "RuntimeError" in result.message


# ── database ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_database_probe_ok(db):
    result = await probes.probe_database(db)
    assert result.status == probes.OK
    assert result.detail["latency_ms"] >= 0


@pytest.mark.asyncio
async def test_database_probe_reports_down_not_unknown_when_the_db_is_gone(db):
    """The one condition that is unambiguously `down` — JF-O-3.1's whole point."""

    class _Dead:
        async def execute(self, *_a, **_k):
            raise OSError("connection refused to postgres://user:pw@host/db")

        async def rollback(self):
            return None

    result = await probes.probe_database(_Dead())
    assert result.status == probes.DOWN
    assert "pw@host" not in f"{result.message} {result.detail}"


# ── migrations ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migrations_probe_ok_when_heads_match(db, monkeypatch):
    await db.execute(_ddl_alembic_version())
    await db.execute(_insert_version("0063"))
    monkeypatch.setattr(probes, "_code_head", lambda: "0063")
    result = await probes.probe_migrations(db)
    assert result.status == probes.OK
    assert result.detail == {"code_head": "0063", "db_head": "0063"}


@pytest.mark.asyncio
async def test_migrations_probe_degrades_not_downs_when_the_db_is_behind(db, monkeypatch):
    """A behind schema is `degraded`: the app runs, it just needs a restart.

    Deliberate — an external probe that paged at 3 a.m. for a container that had
    not been restarted after an image pull would be muted within a week.
    """
    await db.execute(_ddl_alembic_version())
    await db.execute(_insert_version("0061"))
    monkeypatch.setattr(probes, "_code_head", lambda: "0063")
    result = await probes.probe_migrations(db)
    assert result.status == probes.DEGRADED
    assert result.detail["db_head"] == "0061"
    assert result.detail["code_head"] == "0063"


@pytest.mark.asyncio
async def test_migrations_probe_unknown_when_the_scripts_are_missing(db, monkeypatch):
    await db.execute(_ddl_alembic_version())
    await db.execute(_insert_version("0063"))
    monkeypatch.setattr(probes, "_code_head", lambda: None)
    result = await probes.probe_migrations(db)
    assert result.status == probes.UNKNOWN


def _ddl_alembic_version():
    from sqlalchemy import text

    return text("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")


def _insert_version(value: str):
    from sqlalchemy import text

    return text(f"INSERT INTO alembic_version (version_num) VALUES ('{value}')")


# ── retention ─────────────────────────────────────────────────────────────────


async def _add_run(db, *, hours_ago: float, report: dict, ok: bool = True):
    db.add(
        RetentionRun(
            run_at=datetime.now(timezone.utc) - timedelta(hours=hours_ago),
            report=report,
            duration_ms=10,
            ok=ok,
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_retention_probe_unknown_before_any_run(db):
    result = await probes.probe_retention(db)
    assert result.status == probes.UNKNOWN
    assert result.detail["last_run_at"] is None


@pytest.mark.asyncio
async def test_retention_probe_ok_for_a_fresh_run(db):
    await _add_run(db, hours_ago=2, report={"uploads_deleted": 3})
    result = await probes.probe_retention(db)
    assert result.status == probes.OK
    assert result.detail["deleted"] == {"uploads_deleted": 3}


@pytest.mark.asyncio
async def test_retention_probe_degrades_after_two_missed_intervals(db):
    """SF-RET.1 / JF-O-5.1 — the worker died and nothing said so."""
    await _add_run(db, hours_ago=51, report={"uploads_deleted": 1})
    result = await probes.probe_retention(db)
    assert result.status == probes.DEGRADED
    assert "51 h" in result.message
    assert result.detail["age_seconds"] > 2 * 86400


@pytest.mark.asyncio
async def test_retention_probe_degrades_when_the_last_run_failed(db):
    await _add_run(db, hours_ago=1, report={}, ok=False)
    result = await probes.probe_retention(db)
    assert result.status == probes.DEGRADED
    assert "failed" in result.message


@pytest.mark.asyncio
async def test_retention_probe_flags_a_gross_deletion_spike(db):
    """SF-RET.2 — 'the worker deletes too much', at the gross end."""
    for hours in (96, 72, 48):
        await _add_run(db, hours_ago=hours, report={"generated_cvs_deleted": 2})
    await _add_run(db, hours_ago=1, report={"generated_cvs_deleted": 400})
    result = await probes.probe_retention(db)
    assert result.status == probes.DEGRADED
    assert "generated_cvs_deleted" in result.detail["anomalies"]


@pytest.mark.asyncio
async def test_retention_probe_does_not_flag_a_modest_rise(db):
    """The detector is proportional, and the FMEA row says so (D=2, not D=1)."""
    for hours in (96, 72, 48):
        await _add_run(db, hours_ago=hours, report={"generated_cvs_deleted": 20})
    await _add_run(db, hours_ago=1, report={"generated_cvs_deleted": 60})
    result = await probes.probe_retention(db)
    assert result.status == probes.OK
    assert "anomalies" not in result.detail


@pytest.mark.asyncio
async def test_retention_anomaly_needs_a_history_so_a_fresh_install_is_quiet(db):
    await _add_run(db, hours_ago=48, report={"generated_cvs_deleted": 1})
    await _add_run(db, hours_ago=1, report={"generated_cvs_deleted": 999})
    result = await probes.probe_retention(db)
    assert "anomalies" not in result.detail


# ── disk ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_disk_probe_degrades_below_the_threshold(monkeypatch):
    import shutil

    monkeypatch.setattr(
        shutil, "disk_usage", lambda _p: type("U", (), {"free": 4, "total": 100})()
    )
    result = probes.probe_disk()
    assert result.status == probes.DEGRADED
    assert result.detail["free_percent"] == 4.0


@pytest.mark.asyncio
async def test_disk_probe_ok_above_the_threshold(monkeypatch):
    import shutil

    monkeypatch.setattr(
        shutil, "disk_usage", lambda _p: type("U", (), {"free": 60, "total": 100})()
    )
    assert probes.probe_disk().status == probes.OK


@pytest.mark.asyncio
async def test_disk_probe_never_puts_the_path_in_the_payload(monkeypatch):
    """ADR-086 clause 4 — a path is reconnaissance on an unauthenticated surface."""
    import shutil

    monkeypatch.setattr(
        shutil, "disk_usage", lambda _p: type("U", (), {"free": 60, "total": 100})()
    )
    assert not any(
        isinstance(v, str) and "/" in v for v in probes.probe_disk().detail.values()
    )


# ── backup (founder ruling F7 / JF-O-7.1 detection half) ──────────────────────


@pytest.mark.asyncio
async def test_backup_probe_says_never_when_the_table_does_not_exist(db):
    """An instance part-way through the 0.42 upgrade must not 500 its own health."""
    result = await probes.probe_backup(db)
    assert result.status == probes.DEGRADED
    assert result.detail["last_backup_at"] is None
    assert "has ever been recorded" in result.message


@pytest.mark.asyncio
async def test_backup_probe_says_never_when_the_key_is_absent(db_with_instance_state):
    result = await probes.probe_backup(db_with_instance_state)
    assert result.status == probes.DEGRADED
    assert result.detail["last_backup_at"] is None


@pytest.mark.asyncio
async def test_backup_probe_ok_for_a_recent_backup(db_with_instance_state):
    await _write_backup_stamp(db_with_instance_state, days_ago=2)
    result = await probes.probe_backup(db_with_instance_state)
    assert result.status == probes.OK
    assert result.detail["age_days"] == 2


@pytest.mark.asyncio
async def test_backup_probe_warns_after_thirty_days(db_with_instance_state):
    await _write_backup_stamp(db_with_instance_state, days_ago=45)
    result = await probes.probe_backup(db_with_instance_state)
    assert result.status == probes.DEGRADED
    assert result.detail["age_days"] == 45
    assert "45 days ago" in result.message


async def _write_backup_stamp(db, *, days_ago: int):
    stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    await db.execute(
        probes._INSTANCE_STATE.insert().values(
            key=probes.LAST_BACKUP_KEY,
            value=stamp,
            updated_at=datetime.now(timezone.utc),
        )
    )
    await db.commit()


# ── provider ──────────────────────────────────────────────────────────────────


class _StubProvider:
    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc
        self.calls = 0

    async def acomplete(self, *_a, **_k):
        self.calls += 1
        if self._exc:
            raise self._exc
        return "pong"


def _install_provider(monkeypatch, stub, family: str = "mistral"):
    import applire.config as cfg
    import applire.providers.llm as llm

    monkeypatch.setattr(cfg.settings, "llm_provider", family)
    monkeypatch.setattr(llm, "get_provider", lambda: stub)


@pytest.mark.asyncio
async def test_provider_probe_ok_and_reports_unknown_credit(monkeypatch):
    """O1-6: `unknown` is a DISPLAYED state, not an absent field."""
    _install_provider(monkeypatch, _StubProvider())
    result = await probes.probe_provider(force=True)
    assert result.status == probes.OK
    assert result.detail["reachability"] == "ok"
    assert result.detail["credit"] == "unknown"
    assert result.detail["credit_reason"]


@pytest.mark.asyncio
async def test_provider_probe_reads_a_402_as_out_of_credit(monkeypatch):
    """SF-LLM.1 — the incident this row exists for: a 402 read as latency."""
    from applire.exceptions import LLMProviderUnavailableError

    _install_provider(
        monkeypatch, _StubProvider(LLMProviderUnavailableError("402 insufficient credits"))
    )
    result = await probes.probe_provider(force=True)
    assert result.status == probes.DOWN
    assert result.detail["reachability"] == "unauthorised"
    assert "credit" in result.message


@pytest.mark.asyncio
async def test_provider_probe_reads_a_401_as_a_rejected_key(monkeypatch):
    from applire.exceptions import LLMProviderUnavailableError

    _install_provider(
        monkeypatch, _StubProvider(LLMProviderUnavailableError("401 Unauthorized"))
    )
    result = await probes.probe_provider(force=True)
    assert result.detail["reachability"] == "unauthorised"
    assert "key" in result.message


@pytest.mark.asyncio
async def test_provider_probe_reads_a_timeout_as_unreachable(monkeypatch):
    from applire.exceptions import LLMTimeoutError

    _install_provider(monkeypatch, _StubProvider(LLMTimeoutError("timed out")))
    result = await probes.probe_provider(force=True)
    assert result.detail["reachability"] == "unreachable"
    assert result.status == probes.DOWN


@pytest.mark.asyncio
async def test_provider_probe_degrades_on_a_rate_limit(monkeypatch):
    from applire.exceptions import LLMRateLimitError

    _install_provider(monkeypatch, _StubProvider(LLMRateLimitError("429")))
    result = await probes.probe_provider(force=True)
    assert result.detail["reachability"] == "rate_limited"
    assert result.status == probes.DEGRADED


@pytest.mark.asyncio
async def test_provider_probe_never_echoes_the_provider_error(monkeypatch):
    """SF-OPS.5 — a provider error can carry a URL or a key fragment."""
    from applire.exceptions import LLMProviderUnavailableError

    _install_provider(
        monkeypatch,
        _StubProvider(
            LLMProviderUnavailableError(
                "401 from https://openrouter.ai/api/v1 key sk-or-v1-DEADBEEF"
            )
        ),
    )
    result = await probes.probe_provider(force=True)
    serialised = f"{result.message} {result.detail}"
    assert "sk-or-v1-DEADBEEF" not in serialised
    assert "openrouter.ai" not in serialised


@pytest.mark.asyncio
async def test_provider_probe_is_cached_so_a_request_cannot_spend_credit(monkeypatch):
    """SF-OPS.6 — curling the endpoint must not cost the operator anything."""
    stub = _StubProvider()
    _install_provider(monkeypatch, stub)
    await probes.probe_provider(force=True)
    for _ in range(5):
        await probes.probe_provider()
    assert stub.calls == 1


@pytest.mark.asyncio
async def test_provider_probe_spends_nothing_when_switched_off(monkeypatch):
    stub = _StubProvider()
    _install_provider(monkeypatch, stub)
    monkeypatch.setattr(ops_config, "OPS_PROVIDER_PROBE", "off")
    result = await probes.probe_provider(force=True)
    assert stub.calls == 0
    assert result.status == probes.UNKNOWN
    assert result.detail["credit"] == "unknown"


@pytest.mark.asyncio
async def test_provider_probe_attributes_its_own_cost(monkeypatch):
    """A monitoring call that hides its own spend is dishonest."""
    from applire.providers.llm import usage as usage_module

    seen: list[dict] = []

    async def _sink(row):
        seen.append(row)

    stub_inner = _StubProvider()
    recorded = usage_module.UsageRecordingProvider(stub_inner)
    _install_provider(monkeypatch, recorded)
    monkeypatch.setattr(usage_module, "_sink", _sink)

    await probes.probe_provider(force=True)
    assert seen and seen[0]["stage"] == "ops_probe"


# ── the registry, and the verdict ─────────────────────────────────────────────


def test_every_registered_probe_has_a_unique_name():
    assert len(probes.PROBE_NAMES) == len(set(probes.PROBE_NAMES))
    assert set(probes.PROBE_NAMES) == set(probes.DB_PROBES) | set(probes.PLAIN_PROBES)


@pytest.mark.asyncio
async def test_every_registered_probe_reaches_the_verdict(db, monkeypatch):
    """SF-OPS.4 — a probe added later must fail THIS test, not silently not count."""
    monkeypatch.setattr(probes, "_code_head", lambda: None)
    _install_provider(monkeypatch, _StubProvider())
    report = await aggregate.collect(db)
    assert set(report["components"]) == set(probes.PROBE_NAMES)


def test_unknown_is_never_folded_into_ok():
    """The one way this layer could be worse than no layer at all."""
    from applire.services.ops.probes import ProbeResult

    results = [
        ProbeResult("a", probes.OK),
        ProbeResult("b", probes.UNKNOWN),
    ]
    assert aggregate.verdict_of(results) == probes.DEGRADED


def test_a_single_down_component_downs_the_verdict():
    from applire.services.ops.probes import ProbeResult

    results = [
        ProbeResult("a", probes.OK),
        ProbeResult("b", probes.DEGRADED),
        ProbeResult("c", probes.DOWN),
    ]
    assert aggregate.verdict_of(results) == probes.DOWN


def test_all_ok_is_ok():
    from applire.services.ops.probes import ProbeResult

    assert aggregate.verdict_of([ProbeResult("a", probes.OK)]) == probes.OK
