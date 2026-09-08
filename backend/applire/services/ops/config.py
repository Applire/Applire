# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ops-layer settings (ADR-086).

Read straight from the environment, the way ``constants.py`` reads the five GDPR
TTLs — deliberately **not** fields on ``config.Settings``. Two reasons:

1. ``config.py`` is the configuration surface US310's settings registry derives
   its metadata from; the registry's ``register(...)`` hook exists precisely so a
   package can declare its own settings without the central class growing a
   section per feature. These entries are registered from here (see
   ``ops_registry_entries``), so ``env.example`` is still generated with them.
2. ``providers/llm/usage.py`` reads two of these, and a provider module importing
   ``applire.config`` at import time drags the whole settings object into every
   provider process, including the MCP stdio server. A module of plain ints and
   strings does not.

Every value is read **once at import**, like ``constants.py``. Tests that need a
different value monkeypatch the module attribute, not the environment.
"""

import os

# ── Ops aggregation ───────────────────────────────────────────────────────────

# Free-space percentage on the upload directory's filesystem below which the
# `disk` probe warns and the verdict degrades (SF-HOST.2).
OPS_DISK_FREE_WARN_PERCENT: float = float(
    os.environ.get("OPS_DISK_FREE_WARN_PERCENT", "10")
)

# The retention worker's expected cadence, in seconds. The shipped compose file
# runs `while true; do python -m applire.retention; sleep 86400; done`, so the
# default matches it. The `retention` probe warns at more than TWO of these
# (SF-RET.1 / JF-O-5.1): one missed run is a slow host, two is a dead worker.
OPS_RETENTION_INTERVAL_SECONDS: int = int(
    os.environ.get("OPS_RETENTION_INTERVAL_SECONDS", "86400")
)

# A counter in the newest retention report is anomalous when it exceeds this
# many times the median of the previous runs (SF-RET.2). Needs at least
# OPS_RETENTION_ANOMALY_MIN_RUNS of history, so a fresh install never cries wolf.
OPS_RETENTION_ANOMALY_FACTOR: float = float(
    os.environ.get("OPS_RETENTION_ANOMALY_FACTOR", "10")
)
OPS_RETENTION_ANOMALY_MIN_RUNS: int = int(
    os.environ.get("OPS_RETENTION_ANOMALY_MIN_RUNS", "3")
)

# How many `retention_runs` rows to keep (ADR-086 clause 10). 90 runs is three
# months at the daily cadence. 0 = keep forever.
OPS_RETENTION_RUNS_KEEP: int = int(os.environ.get("OPS_RETENTION_RUNS_KEEP", "90"))

# Days without a successful backup before the `backup` probe warns
# (founder ruling F7 / JF-O-7.1).
OPS_BACKUP_WARN_DAYS: int = int(os.environ.get("OPS_BACKUP_WARN_DAYS", "30"))

# Rolling window for the error counter, in minutes.
OPS_ERROR_WINDOW_MINUTES: int = int(os.environ.get("OPS_ERROR_WINDOW_MINUTES", "60"))

# ── Provider probe (the only probe that spends money) ─────────────────────────

# "on" | "off". While on, a minimal reachability call runs at most once per
# OPS_PROVIDER_PROBE_TTL_MINUTES — about 96 chrome-sized calls a day, against
# 89-105 for a single application. Set to "off" and SF-LLM.1's detection score
# goes back to D=3; that trade is stated in the ADR and in this description.
OPS_PROVIDER_PROBE: str = os.environ.get("OPS_PROVIDER_PROBE", "on").strip().lower()
OPS_PROVIDER_PROBE_TTL_MINUTES: int = int(
    os.environ.get("OPS_PROVIDER_PROBE_TTL_MINUTES", "15")
)
# Remaining provider credit (in the provider's own currency unit) below which
# the `credit` fact reports "low" rather than "ok". Only meaningful for the
# providers that expose a balance at all; everywhere else the fact is "unknown".
OPS_PROVIDER_CREDIT_LOW_THRESHOLD: float = float(
    os.environ.get("OPS_PROVIDER_CREDIT_LOW_THRESHOLD", "1.0")
)

# How often the background refresher recomputes the cached aggregate, in
# seconds. This is what makes `/health`'s `ops` summary and the state-change
# WARNING a push rather than something only a request can trigger.
OPS_REFRESH_SECONDS: int = int(os.environ.get("OPS_REFRESH_SECONDS", "60"))

# ── Token accounting (US313) ──────────────────────────────────────────────────

# "on" | "off". One INSERT per provider call. Numbers and opaque ids only.
LLM_USAGE_TRACKING: str = os.environ.get("LLM_USAGE_TRACKING", "on").strip().lower()

# Days an `llm_usage` row lives; enforced by the retention worker. 0 = forever.
# Not an ADR-005 PII clock — these rows carry no personal data — a growth bound.
LLM_USAGE_RETENTION_DAYS: int = int(os.environ.get("LLM_USAGE_RETENTION_DAYS", "365"))


def provider_probe_enabled() -> bool:
    """Whether the reachability/credit probe may spend a provider call."""
    return OPS_PROVIDER_PROBE not in ("off", "false", "0", "no")


def usage_tracking_enabled() -> bool:
    """Whether every provider call writes an `llm_usage` row."""
    return LLM_USAGE_TRACKING not in ("off", "false", "0", "no")


def ops_registry_entries() -> list[dict[str, object]]:
    """The ops layer's settings, in US310's registry shape.

    Called from ``settings_registry.register(...)`` so ``env.example`` is
    generated with these entries too (ADR-086, WORK-PACKAGES contract 3). Kept
    here rather than in the registry so the defaults have exactly one home.
    """
    return [
        {
            "name": "ops_disk_free_warn_percent",
            "env_var": "OPS_DISK_FREE_WARN_PERCENT",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 10,
            "source": "constants",
            "description": (
                "Free space on the uploads filesystem, in percent, below which "
                "the ops health endpoint reports a warning."
            ),
        },
        {
            "name": "ops_retention_interval_seconds",
            "env_var": "OPS_RETENTION_INTERVAL_SECONDS",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 86400,
            "source": "constants",
            "description": (
                "How often the retention worker is expected to run. The ops "
                "layer warns after more than two of these without a run."
            ),
        },
        {
            "name": "ops_retention_anomaly_factor",
            "env_var": "OPS_RETENTION_ANOMALY_FACTOR",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 10,
            "source": "constants",
            "description": (
                "A retention deletion counter is flagged when it exceeds this "
                "multiple of the median of previous runs."
            ),
        },
        {
            "name": "ops_retention_anomaly_min_runs",
            "env_var": "OPS_RETENTION_ANOMALY_MIN_RUNS",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 3,
            "source": "constants",
            "description": (
                "How many previous retention runs must exist before the "
                "anomaly check runs at all."
            ),
        },
        {
            "name": "ops_retention_runs_keep",
            "env_var": "OPS_RETENTION_RUNS_KEEP",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 90,
            "source": "constants",
            "description": "How many retention-run records to keep. 0 = keep all.",
        },
        {
            "name": "ops_backup_warn_days",
            "env_var": "OPS_BACKUP_WARN_DAYS",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 30,
            "source": "constants",
            "description": (
                "Days without a successful backup before the ops health "
                "endpoint warns. Requires scripts/backup.sh to have run."
            ),
        },
        {
            "name": "ops_error_window_minutes",
            "env_var": "OPS_ERROR_WINDOW_MINUTES",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 60,
            "source": "constants",
            "description": "Rolling window for the ops error-rate counter.",
        },
        {
            "name": "ops_provider_probe",
            "env_var": "OPS_PROVIDER_PROBE",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": "on",
            "source": "constants",
            "description": (
                "Whether the ops layer sends a small reachability call to the "
                "LLM provider (about 96 tiny calls a day). Set to 'off' to "
                "spend nothing; the instance then cannot tell you your "
                "provider credit ran out."
            ),
        },
        {
            "name": "ops_provider_probe_ttl_minutes",
            "env_var": "OPS_PROVIDER_PROBE_TTL_MINUTES",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 15,
            "source": "constants",
            "description": "Minimum minutes between two provider reachability calls.",
        },
        {
            "name": "ops_provider_credit_low_threshold",
            "env_var": "OPS_PROVIDER_CREDIT_LOW_THRESHOLD",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 1.0,
            "source": "constants",
            "description": (
                "Remaining provider credit below which the ops health endpoint "
                "reports 'low'. Only providers that publish a balance "
                "(OpenRouter, Requesty) can report anything but 'unknown'."
            ),
        },
        {
            "name": "ops_refresh_seconds",
            "env_var": "OPS_REFRESH_SECONDS",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 60,
            "source": "constants",
            "description": (
                "How often the backend recomputes its own health picture in "
                "the background, so the log warning and /health's ops summary "
                "do not wait for someone to open the dashboard."
            ),
        },
        {
            "name": "llm_usage_tracking",
            "env_var": "LLM_USAGE_TRACKING",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": "on",
            "source": "constants",
            "description": (
                "Whether every LLM call records its token counts. Numbers and "
                "ids only - no prompt or answer text is ever stored."
            ),
        },
        {
            "name": "llm_usage_retention_days",
            "env_var": "LLM_USAGE_RETENTION_DAYS",
            "introduced_in": "0.42.0",
            "semantics_changed_in": None,
            "default": 365,
            "source": "constants",
            "description": (
                "How many days token-usage records are kept. 0 = keep forever."
            ),
        },
    ]
