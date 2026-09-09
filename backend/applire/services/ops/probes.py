# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ops layer's probes (ADR-086 clause 6).

Seven independent facts about the instance. Each probe is a small function with
its own hermetic test and its own faked failure; none of them writes anything;
**none of them may raise** — ``run_probe`` turns any exception into a
``ProbeResult`` with status ``unknown``, because a health endpoint that 500s
because a probe threw is the failure it was built to detect (``SF-OPS.4``).

``unknown`` is never folded into ``ok``. It degrades the verdict, so a probe that
silently stops working cannot make the instance look healthy.

The disclosure boundary of ADR-086 clause 4 is a property of what these
functions put in ``detail``: versions, statuses, gauges and opaque ids — never a
key, a path, a hostname or the exact model id. ``test_ops_endpoint.py`` asserts
it over a settings object full of recognisable sentinels.
"""

from __future__ import annotations

import logging
import shutil
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from sqlalchemy import Column, DateTime, JSON, MetaData, String, Table, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from applire.services.ops import config as ops_config

logger = logging.getLogger(__name__)

OK = "ok"
DEGRADED = "degraded"
DOWN = "down"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProbeResult:
    name: str
    status: str
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


async def run_probe(
    name: str, fn: Callable[..., Awaitable[ProbeResult] | ProbeResult], *args: Any
) -> ProbeResult:
    """Run one probe, turning any exception into ``unknown``. Never raises."""
    try:
        result = fn(*args)
        if isinstance(result, ProbeResult):
            return result
        return await result  # type: ignore[misc]
    except Exception as exc:
        logger.debug("ops probe %s failed: %s: %s", name, type(exc).__name__, exc)
        # The exception TYPE only. Its message may carry a DSN, a path or a URL,
        # and this payload is unauthenticated (ADR-086 clause 4, SF-OPS.5).
        return ProbeResult(
            name=name,
            status=UNKNOWN,
            message=f"probe raised {type(exc).__name__}",
        )


# ── database ──────────────────────────────────────────────────────────────────


async def probe_database(db: AsyncSession) -> ProbeResult:
    """Is the database answering?

    Handles its own failure rather than letting ``run_probe`` turn it into
    ``unknown``: an unreachable database is the one condition that is
    unambiguously ``down``, and the whole point of this probe is that
    ``/health`` today returns ``"ok"`` with Postgres gone (``JF-O-3.1``).
    """
    started = time.monotonic()
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:
        try:
            await db.rollback()
        except Exception:  # pragma: no cover - the session is already broken
            pass
        # The exception TYPE only; its message carries the DSN.
        return ProbeResult(
            "database", DOWN, f"database unreachable ({type(exc).__name__})"
        )
    latency_ms = int((time.monotonic() - started) * 1000)
    return ProbeResult("database", OK, detail={"latency_ms": latency_ms})


# ── migrations ────────────────────────────────────────────────────────────────


def _alembic_dir() -> Path:
    """The migration script directory, anchored to the package, not the CWD.

    `applire` is a PEP 420 namespace package, so it has no ``__file__`` of its
    own; ``applire.config`` is the same anchor ``config.resolve_static_dir``
    uses for exactly this reason.
    """
    import applire.config as cfg

    return Path(cfg.__file__).resolve().parents[1] / "alembic"


def _code_head() -> str | None:
    from alembic.script import ScriptDirectory

    directory = _alembic_dir()
    if not directory.is_dir():
        return None
    return ScriptDirectory(str(directory)).get_current_head()


async def probe_migrations(db: AsyncSession) -> ProbeResult:
    """Is the schema at the revision this code expects?

    A mismatch is ``degraded``, not ``down``: the app is running, it is behind.
    That distinction is what keeps an external probe from paging at 3 a.m. for a
    container that has not been restarted after an image pull.
    """
    code_head = _code_head()
    row = await db.execute(text("SELECT version_num FROM alembic_version"))
    db_head = row.scalar_one_or_none()
    detail = {"code_head": code_head, "db_head": db_head}
    if code_head is None:
        return ProbeResult(
            "migrations", UNKNOWN, "migration scripts not found", detail
        )
    if db_head is None:
        return ProbeResult("migrations", DOWN, "database has no revision", detail)
    if db_head != code_head:
        return ProbeResult(
            "migrations",
            DEGRADED,
            f"database at {db_head}, code expects {code_head}",
            detail,
        )
    return ProbeResult("migrations", OK, detail=detail)


# ── retention worker ──────────────────────────────────────────────────────────


def _anomalous_counters(
    latest: dict[str, Any], history: list[dict[str, Any]]
) -> dict[str, dict[str, float]]:
    """Counters in ``latest`` far above the median of ``history``.

    A gross-deviation check, and the FMEA row says so: it cannot see a small
    absolute over-deletion, which is why ``SF-RET.2`` moves to D=2 and not D=1.
    Needs ``OPS_RETENTION_ANOMALY_MIN_RUNS`` of history, so a fresh install with
    two runs never cries wolf.
    """
    if len(history) < ops_config.OPS_RETENTION_ANOMALY_MIN_RUNS:
        return {}
    flagged: dict[str, dict[str, float]] = {}
    factor = ops_config.OPS_RETENTION_ANOMALY_FACTOR
    for key, value in latest.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        past = [
            report.get(key)
            for report in history
            if isinstance(report.get(key), (int, float))
            and not isinstance(report.get(key), bool)
        ]
        if len(past) < ops_config.OPS_RETENTION_ANOMALY_MIN_RUNS:
            continue
        median = statistics.median(past)
        # A median of 0 has no meaningful multiple; require an absolute floor so
        # "0, 0, 0 -> 1" is not reported as an infinite spike.
        threshold = max(median * factor, factor)
        if value > threshold:
            flagged[key] = {"value": float(value), "median": float(median)}
    return flagged


async def probe_retention(db: AsyncSession) -> ProbeResult:
    """Did the GDPR worker run, and did it delete a plausible amount?

    This is the consumer ``SF-RET.1``'s and ``SF-RET.2``'s control cells have
    been waiting for since the worker was built: the stdout report has always
    existed and nothing has ever read it.
    """
    from applire.models.retention_run import RetentionRun

    rows = (
        (
            await db.execute(
                select(RetentionRun).order_by(RetentionRun.run_at.desc()).limit(11)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return ProbeResult(
            "retention",
            UNKNOWN,
            "no retention run recorded yet",
            {"last_run_at": None, "age_seconds": None},
        )
    latest = rows[0]
    run_at = latest.run_at
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - run_at).total_seconds()
    interval = ops_config.OPS_RETENTION_INTERVAL_SECONDS
    detail: dict[str, Any] = {
        "last_run_at": run_at.isoformat(),
        "age_seconds": int(age),
        "expected_interval_seconds": interval,
        "last_run_ok": bool(latest.ok),
        "deleted": {
            k: v
            for k, v in (latest.report or {}).items()
            if isinstance(v, int) and not isinstance(v, bool)
        },
    }
    anomalies = _anomalous_counters(
        latest.report or {}, [r.report or {} for r in rows[1:]]
    )
    if anomalies:
        detail["anomalies"] = anomalies

    if not latest.ok:
        return ProbeResult("retention", DEGRADED, "last run failed", detail)
    if age > 2 * interval:
        hours = int(age // 3600)
        return ProbeResult(
            "retention", DEGRADED, f"no run for {hours} h", detail
        )
    if anomalies:
        return ProbeResult(
            "retention",
            DEGRADED,
            "deletion counts far above the usual run",
            detail,
        )
    return ProbeResult("retention", OK, detail=detail)


# ── disk ──────────────────────────────────────────────────────────────────────


def _existing_ancestor(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def probe_disk() -> ProbeResult:
    """Free space on the filesystem holding the uploads directory.

    On the shipped compose topology that is the ``applire_uploads`` volume,
    which on a default single-disk host is the same device as ``postgres_data``
    — but not by construction. The runbook says so; this probe measures the one
    device it can reach from inside the container.
    """
    from applire.config import settings

    target = _existing_ancestor(Path(settings.upload_dir).resolve())
    usage = shutil.disk_usage(target)
    free_percent = (usage.free / usage.total * 100) if usage.total else 0.0
    # The PATH is deliberately not in the payload (ADR-086 clause 4).
    detail = {
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "free_percent": round(free_percent, 1),
        "warn_below_percent": ops_config.OPS_DISK_FREE_WARN_PERCENT,
    }
    if free_percent < ops_config.OPS_DISK_FREE_WARN_PERCENT:
        return ProbeResult(
            "disk", DEGRADED, f"{free_percent:.1f} % free", detail
        )
    return ProbeResult("disk", OK, detail=detail)


# ── backup ────────────────────────────────────────────────────────────────────

# `instance_state` belongs to US310 (migration 0062). Declared here on its OWN
# MetaData rather than imported: two work packages must not share a model
# object, and this table may legitimately not exist yet on an instance upgraded
# from a pre-0.42 image. Never attached to `Base.metadata`.
_ops_metadata = MetaData()
_INSTANCE_STATE = Table(
    "instance_state",
    _ops_metadata,
    Column("key", String(64), primary_key=True),
    Column("value", JSONB().with_variant(JSON(), "sqlite"), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
LAST_BACKUP_KEY = "last_backup_at"


async def probe_backup(db: AsyncSession) -> ProbeResult:
    """How long since ``scripts/backup.sh`` last succeeded (founder ruling F7).

    A missing key and a missing table both mean "never". Tolerating the missing
    table is not defensive padding: US310's migration and this probe ship in the
    same release, and an instance part-way through an upgrade must not 500 its
    own health endpoint.
    """
    try:
        row = await db.execute(
            select(_INSTANCE_STATE.c.value).where(
                _INSTANCE_STATE.c.key == LAST_BACKUP_KEY
            )
        )
        raw = row.scalar_one_or_none()
    except Exception:
        # No instance_state table on this instance yet.
        await db.rollback()
        raw = None

    stamp = _parse_backup_stamp(raw)
    if stamp is None:
        return ProbeResult(
            "backup",
            DEGRADED,
            "no backup has ever been recorded",
            {"last_backup_at": None, "age_days": None,
             "warn_after_days": ops_config.OPS_BACKUP_WARN_DAYS},
        )
    age_days = (datetime.now(timezone.utc) - stamp).total_seconds() / 86400
    detail = {
        "last_backup_at": stamp.isoformat(),
        "age_days": int(age_days),
        "warn_after_days": ops_config.OPS_BACKUP_WARN_DAYS,
    }
    if age_days >= ops_config.OPS_BACKUP_WARN_DAYS:
        return ProbeResult(
            "backup", DEGRADED, f"last backup {int(age_days)} days ago", detail
        )
    return ProbeResult("backup", OK, detail=detail)


def _parse_backup_stamp(raw: Any) -> datetime | None:
    """`backup.sh` writes an ISO-8601 string into the JSON `value` column."""
    if raw is None:
        return None
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get(LAST_BACKUP_KEY)
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ── provider ──────────────────────────────────────────────────────────────────

# (expires_at_monotonic, ProbeResult)
_provider_cache: tuple[float, ProbeResult] | None = None


def reset_provider_cache() -> None:
    """Test hook."""
    global _provider_cache
    _provider_cache = None


async def probe_provider(force: bool = False) -> ProbeResult:
    """Is the configured provider reachable, and is there credit left?

    **The only probe that spends money.** Cached for
    ``OPS_PROVIDER_PROBE_INTERVAL_MINUTES`` and never triggered synchronously by a
    request — the endpoint reads whatever the background refresher last left
    here, so an unauthenticated caller can neither spend the operator's credit
    nor use the endpoint as an amplifier (``SF-OPS.6``).
    """
    global _provider_cache
    now = time.monotonic()
    if not force and _provider_cache is not None and _provider_cache[0] > now:
        return _provider_cache[1]

    from applire.config import settings

    family = (settings.llm_provider or "").strip().lower()
    if not ops_config.provider_probe_enabled():
        return ProbeResult(
            "provider",
            UNKNOWN,
            "provider probe switched off",
            {
                "provider": family,
                "model": _configured_model(family),
                "reachability": "unknown",
                "credit": "n/a" if family not in _CREDIT_READERS else "unknown",
            },
        )

    if ops_config.reachability_probe_enabled():
        reachability, message = await _probe_reachability()
    else:
        reachability, message = "unknown", ""
    credit, credit_detail = await _probe_credit(family)
    detail: dict[str, Any] = {
        "provider": family,
        # The configured model id IS published (ADR-086 clause 4, founder ruling
        # O1-2, 2026-09-09). ADR-085 clause 3 keeps the model id OFF the PDF
        # mark for a different reason: that artefact is handed to a third party.
        # This one is the operator's own instance, and the model id is what lets
        # them check their model against US311's published list.
        "model": _configured_model(family),
        "reachability": reachability,
        "credit": credit,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    detail.update(credit_detail)

    if reachability in ("unauthorised", "unreachable"):
        status = DOWN
    elif reachability == "rate_limited" or credit == "low":
        status = DEGRADED
    elif reachability == "unknown":
        status = UNKNOWN
    else:
        status = OK

    result = ProbeResult("provider", status, message, detail)
    _provider_cache = (
        now + ops_config.OPS_PROVIDER_PROBE_INTERVAL_MINUTES * 60,
        result,
    )
    return result


def _configured_model(family: str) -> str:
    """The model id the operator configured, for the family in use.

    Published on the ops payload by founder ruling O1-2: the operator needs it
    to match their instance against US311's published "which models work" list,
    and a later flavour may flag a sub-par model on the panel directly. It is
    deployment configuration, not user data.
    """
    from applire.config import settings

    return str(getattr(settings, f"{family}_model", "") or "")


async def _probe_reachability() -> tuple[str, str]:
    """One tiny generation. Chrome-sized: about 96 a day at the default TTL."""
    from applire.exceptions import (
        LLMProviderUnavailableError,
        LLMRateLimitError,
        LLMTimeoutError,
    )
    from applire.providers.llm import get_provider
    from applire.providers.llm.usage import llm_usage_context

    try:
        provider = get_provider()
    except Exception as exc:
        return "unknown", f"provider not configured ({type(exc).__name__})"

    try:
        # Attributed so the probe's own cost is visible in the same table it
        # reports — a monitoring call that hides its own spend is dishonest.
        with llm_usage_context(stage="ops_probe"):
            await provider.acomplete("ping", max_tokens=1, temperature=0.0)
        return "ok", ""
    except LLMRateLimitError:
        return "rate_limited", "provider rate-limited the probe"
    except LLMTimeoutError:
        return "unreachable", "provider timed out"
    except LLMProviderUnavailableError as exc:
        return _classify_unavailable(str(exc))
    except Exception as exc:
        return _classify_unavailable(f"{type(exc).__name__}: {exc}")


def _classify_unavailable(text_form: str) -> tuple[str, str]:
    """Map a provider error to a state without echoing the error itself.

    The message is deliberately ours, not the provider's: a provider error can
    carry a URL, a key fragment or a host name, and this ends up on an
    unauthenticated payload (``SF-OPS.5``).
    """
    lowered = text_form.lower()
    if "402" in lowered or "credit" in lowered or "insufficient" in lowered:
        return "unauthorised", "provider refused the call — out of credit"
    if "401" in lowered or "403" in lowered or "unauthor" in lowered or "api key" in lowered:
        return "unauthorised", "provider refused the call — key rejected"
    if "429" in lowered or "rate limit" in lowered:
        return "rate_limited", "provider rate-limited the probe"
    return "unreachable", "provider did not answer"


# Which providers publish a balance a third party can read. Everything else
# reports "unknown" — a displayed state, never a hidden field, because Ollama
# and an OpenAI-compatible endpoint have no balance at all and an absent field
# reads as a bug (founder question O1-6).
_CREDIT_READERS: dict[str, str] = {
    "openrouter": "credits",
}


async def _probe_credit(family: str) -> tuple[str, dict[str, Any]]:
    """`ok` | `low` | `n/a` | `unknown` (founder ruling O1-6, 2026-09-09).

    `n/a` and `unknown` are deliberately different states. **`n/a`** means the
    question does not apply — Ollama and any OpenAI-compatible endpoint have no
    balance at all, and reporting "unknown" there would read as a fault the
    operator should go and fix. **`unknown`** means the question applies and we
    do not have the answer: the credit half is switched off, or the balance read
    failed. Both are *displayed*; neither is a hidden field.
    """
    reader = _CREDIT_READERS.get(family)
    if reader is None:
        return "n/a", {"credit_reason": "this provider has no balance to read"}
    if not ops_config.credit_probe_enabled():
        return "unknown", {"credit_reason": "credit check switched off"}
    try:
        remaining = await _openrouter_credit()
    except Exception:
        return "unknown", {"credit_reason": "balance could not be read"}
    if remaining is None:
        return "unknown", {"credit_reason": "balance could not be read"}
    detail = {"credit_remaining": round(remaining, 4)}
    if remaining <= ops_config.OPS_PROVIDER_CREDIT_LOW_THRESHOLD:
        return "low", detail
    return "ok", detail


async def _openrouter_credit() -> float | None:
    """Remaining OpenRouter credit, in USD. Free — it is not a model call."""
    import httpx

    from applire.config import settings

    key = (settings.openrouter_api_key or "").strip()
    if not key:
        return None
    base = (settings.openrouter_base_url or "https://openrouter.ai/api/v1").rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{base}/credits", headers={"Authorization": f"Bearer {key}"}
        )
        response.raise_for_status()
        data = (response.json() or {}).get("data") or {}
    total = data.get("total_credits")
    used = data.get("total_usage")
    if not isinstance(total, (int, float)) or not isinstance(used, (int, float)):
        return None
    return float(total) - float(used)


# ── errors ────────────────────────────────────────────────────────────────────


def probe_errors() -> ProbeResult:
    """Handled failures in the rolling window. Never degrades the verdict alone.

    A number, not a verdict: two failed LLM calls in an hour is a bad provider
    day, not an unhealthy instance, and a threshold here would either page
    constantly or never. The provider probe is what states the provider's health.
    """
    from applire.services.ops.errors import error_counts

    counts = error_counts()
    return ProbeResult(
        "errors",
        OK,
        detail={
            "window_minutes": ops_config.OPS_ERROR_WINDOW_MINUTES,
            "counts": counts,
            # Said out loud: the counter lives in this process only.
            "scope": "this backend process since start",
        },
    )


# The authoritative probe set. `aggregate.collect` iterates THIS, and
# `test_ops_probes.py` asserts every entry reaches the verdict — so a probe
# added in a later flavour fails a named test instead of silently not counting
# (SF-OPS.4).
DB_PROBES: dict[str, Callable[[AsyncSession], Awaitable[ProbeResult]]] = {
    "database": probe_database,
    "migrations": probe_migrations,
    "retention": probe_retention,
    "backup": probe_backup,
}
PLAIN_PROBES: dict[str, Callable[[], Any]] = {
    "disk": probe_disk,
    "provider": probe_provider,
    "errors": probe_errors,
}
PROBE_NAMES: tuple[str, ...] = tuple(DB_PROBES) + tuple(PLAIN_PROBES)
