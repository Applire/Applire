# Copyright (C) 2024-2026 Tobias Rosenbaum
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

import os
from contextvars import ContextVar, Token
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from applire.constants import (
    INTERVIEW_HARD_CEILING_GUIDED,
    INTERVIEW_HARD_CEILING_TARGETED,
)

# Backend package root — the directory that contains the `applire/` package.
# config.py lives at <backend>/applire/config.py, so two parents up is <backend>.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent


def resolve_static_dir() -> Path:
    """Return the absolute path to the static-asset directory.

    Honours the STATIC_DIR env var when set; otherwise defaults to the
    package-relative ``<backend>/data/static``. Anchoring to the package root
    (rather than the CWD-relative "./data/static") keeps the path stable no
    matter which directory the process is launched from — launching uvicorn
    from the repo root used to serve an empty directory and 404 every template
    thumbnail.
    """
    env = os.getenv("STATIC_DIR")
    if env:
        return Path(env).resolve()
    return _BACKEND_ROOT / "data" / "static"


# --- ADR-093: runtime instance settings (the admin override overlay) ---------
#
# A closed set of settings an admin may override at runtime (the registry's
# ``panel=True`` entries; ``settings_registry.PANEL_KEYS``). Their FIELD names
# are resolved by ``Settings.__getattribute__`` through an overlay snapshot, so
# every existing reader (``settings.openrouter_model`` in a provider constructor,
# the OCR factory, the ops probes) sees the effective value without being
# edited. Two sources, in order:
#
# 1. the PIN — a ContextVar set per web request (middleware) and per MCP tool
#    call (``_agent_call``). Background tasks inherit the context they were
#    created in, so work already running keeps the settings it started with
#    (ADR-093 cl. 5, ruling C1-4);
# 2. the process-wide LATEST snapshot, refreshed from ``instance_settings``.
#
# Only names in ``PANEL_FIELDS`` take the slow path; every other attribute is a
# plain lookup. The overlay never holds a key that is not a panel field.

_EMPTY: Mapping[str, Any] = MappingProxyType({})
_overlay_pin: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "applire_settings_pin", default=None
)
_overlay_latest: list[Mapping[str, Any]] = [_EMPTY]


def _panel_fields() -> frozenset[str]:
    from applire.settings_registry import PANEL_KEYS

    return frozenset(k.lower() for k in PANEL_KEYS)


PANEL_FIELDS: frozenset[str] = _panel_fields()


def overlay_latest() -> Mapping[str, Any]:
    """The process-wide latest override snapshot (field name -> value)."""
    return _overlay_latest[0]


def set_overlay_latest(snapshot: Mapping[str, Any]) -> None:
    """Replace the process-wide snapshot (atomic: one list-slot assignment)."""
    unknown = set(snapshot) - PANEL_FIELDS
    if unknown:
        raise ValueError(f"not panel fields: {sorted(unknown)}")
    _overlay_latest[0] = MappingProxyType(dict(snapshot))


def pin_overlay(snapshot: Mapping[str, Any] | None = None) -> Token:
    """Pin ``snapshot`` (default: the latest) for the current context."""
    return _overlay_pin.set(overlay_latest() if snapshot is None else snapshot)


def unpin_overlay(token: Token) -> None:
    _overlay_pin.reset(token)


def active_overlay() -> Mapping[str, Any]:
    """The snapshot a read in this context resolves through."""
    pinned = _overlay_pin.get()
    return overlay_latest() if pinned is None else pinned


class ShippedDefaultBaseUrl(str):
    """Marks the code default of ``APPLIRE_BASE_URL`` (founder ruling S-1, 2026-10-07).

    "The operator left APPLIRE_BASE_URL unset" is decided by whether the
    environment or ``.env`` supplied the field, never by comparing strings: a
    value read from a source is a plain ``str``, the code default is this marker
    (the field skips default validation, so pydantic keeps the object). An
    operator who sets exactly ``http://localhost`` has therefore configured it.
    """

    __slots__ = ()


#: The shipped default: the stock install's nginx on port 80 (S-1; was
#: ``http://localhost:8001``, a port only the dev override publishes). Links built
#: from it are right when the agent or browser runs on the server itself.
SHIPPED_DEFAULT_BASE_URL = ShippedDefaultBaseUrl("http://localhost")


def configured_base_url(value: object = None) -> str | None:
    """The operator's ``APPLIRE_BASE_URL`` (stripped, no trailing slash), or ``None``
    when it is unset: no source supplied it, or it is empty.

    The ONE predicate behind MD-32 (no mail without an explicit base URL), the
    origin check's Host allow-list and OIDC's redirect-URI check. ``value``
    defaults to the live setting; tests pass a value to probe the rule.
    """
    raw = settings.applire_base_url if value is None else value
    if not isinstance(raw, str) or isinstance(raw, ShippedDefaultBaseUrl):
        return None
    base = raw.strip().rstrip("/")
    return base or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def __getattribute__(self, name: str) -> Any:
        if name in PANEL_FIELDS:
            snapshot = _overlay_pin.get()
            if snapshot is None:
                snapshot = _overlay_latest[0]
            if name in snapshot:
                return snapshot[name]
        return super().__getattribute__(name)

    def env_value(self, name: str) -> Any:
        """The value from the environment / ``.env`` / code default — never the
        admin override (ADR-093 cl. 3: what "reset to environment value" restores)."""
        return super().__getattribute__(name)

    database_url: str
    llm_provider: str = "mistral"
    mistral_api_key: str = ""
    mistral_model: str = "mistral-small-latest"
    openai_api_key: str = ""
    openai_base_url: str = ""          # empty = use OpenAI default; set to point at LM Studio etc.
    openai_model: str = "gpt-4o"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.2"
    openrouter_api_key: str = ""
    openrouter_model: str = "mistralai/mistral-large-latest"
    openrouter_base_url: str = ""          # empty = use https://openrouter.ai/api/v1
    openrouter_disable_thinking: bool = False  # global default; emits reasoning:{enabled:false} (cross-vendor: Gemini/Qwen/DeepSeek). Per-call disable_thinking overrides it (F-B)
    # Reasoning ("thinking") effort for models that keep reasoning ON. Reasoning tokens
    # share the max_tokens budget, and some models (e.g. gemini-3.5-flash) over-think
    # simple transforms — burning the budget so the visible output truncates. Set "low"
    # (or "medium"/"high") to cap that via OpenRouter's cross-vendor reasoning.effort;
    # empty = let the model decide (current behaviour). Accepted even by models that
    # *mandate* reasoning and reject reasoning:{enabled:false}. (ADR-009 amendment)
    openrouter_reasoning_effort: str = ""  # "" = unset; "low" | "medium" | "high"
    # Requesty — EU-hosted OpenAI-compat gateway (ADR-009 amended 2026-06-14)
    requesty_api_key: str = ""
    requesty_model: str = "mistralai/mistral-large-latest"  # set an EU-region model for full residency
    requesty_base_url: str = ""            # empty = use https://router.eu.requesty.ai/v1 (EU residency)
    requesty_disable_thinking: bool = False  # emits reasoning_effort:"none"; per-call disable_thinking overrides (#179)
    requesty_reasoning_effort: str = ""      # "" = model default; "low"|"medium"|"high"|"max" bound reasoning when it stays on
    # Anthropic — native Messages API; BYO-API-key only (Claude subscriptions are not usable)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    llm_timeout: int = 120                 # seconds; raise for thinking/reasoning models (e.g. Qwen3, o3)
    # Operator-declared hard output cap for the chosen model, in tokens. 0 = unset
    # (no known cap). When set, every requested max_tokens is clamped to it so we never
    # ask a capped model for more than it can emit — which only trades truncation for
    # timeout (ADR-047 §2, cap-aware budgeting). Optional: segmentation already handles
    # capped models with no metadata; this lets an operator who knows the cap pre-empt it.
    llm_max_output_tokens: int = 0
    # Founder rulings M-3 and P-4 (2026-09-09) — structured output on the ONE
    # call whose response is a typed union (the ADR-046 reconciler). "auto"
    # sends the op union as a JSON schema alongside the prompt and latches it
    # off for the process the first time an endpoint rejects it; "off" keeps
    # free-form JSON mode.
    #
    # Default "auto" is a FOUNDER RULING taken against this package's own
    # recommendation of "off", with the price in front of him: it costs
    # +2,269 measured input tokens per reconcile call (~35 % more input on that
    # call), the schema is non-strict, and the Requesty route was unmeasured at
    # the time. What it buys, measured on the same fixtures: `ministral-8b`'s
    # station coverage on the one-employer shape went 0.10 -> 1.00 and its
    # malformed-op rate 20 % -> 10 %, with no regression on the two models that
    # were already qualified. See docs/llm-models.md for the row and for how to
    # turn it off. Only the OpenAI-compatible gateways act on it.
    llm_structured_output: str = "auto"  # "auto" | "off"
    # Developer-only: when True, every LLM call's full input/output is appended as a
    # JSON line to <llm_debug_log_dir>/<date>.jsonl (records CV PII — keep OFF in prod).
    llm_debug_log: bool = False
    llm_debug_log_dir: str = "logs/llm"
    embedding_provider: str = "noop"
    embedding_model: str = ""             # empty = use provider default
    # Combined score weights for GET /api/jobs/match (must sum to 1.0)
    matching_score_embedding_weight: float = 0.4
    matching_score_llm_weight: float = 0.6
    # --- Accounts (ADR-091; declared in settings_registry.py, introduced 0.43.0) ---
    # "local" = built-in accounts. The pre-0.43 value "none" now ALSO means
    # local (MD-2) — login is always on (S-2); the lifespan names the line to delete.
    auth_provider: str = "local"
    # Test harness only (ADR-091 cl. 3) — fenced: no credential anywhere, a test
    # database, boot latch. Withheld from .env.example.
    auth_harness: bool = False
    # Session cookie `Secure` attribute (ruling S-15: default off for plain-http LANs).
    cookie_secure: bool = False
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid email profile"
    oidc_button_label: str = "Single sign-on"
    smtp_host: str = ""                     # empty = no mail (S-7)
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_security: str = "starttls"         # starttls | tls | none
    agent_link_ttl_minutes: int = 60        # signed document links (RD-8)
    audit_log_retention_days: int = 730     # 0 = keep forever
    applire_agent_token: str = ""           # MCP stdio process only (S-5)
    mcp_transport: str = "stdio"
    # S-1: unset is decided by presence, see ShippedDefaultBaseUrl. validate_default
    # must stay False, or pydantic turns the marker into a plain str and every
    # install reads as "configured" (mail on with localhost links, MD-32 broken).
    applire_base_url: str = Field(default=SHIPPED_DEFAULT_BASE_URL, validate_default=False)

    @field_validator("applire_base_url", mode="after")
    @classmethod
    def _blank_base_url_is_the_shipped_default(cls, value: str) -> str:
        """adv-admin ADM-6: ``APPLIRE_BASE_URL=`` (present, blank) is UNSET — for
        ``configured_base_url()`` and for every link builder alike. Mapping it to the
        marker gives both readers one answer: the S-1 default origin for links,
        "unset" for MD-32 / the origin check / OIDC."""
        if isinstance(value, str) and not value.strip():
            return SHIPPED_DEFAULT_BASE_URL
        return value
    upload_dir: str = "./data/uploads"
    storage_backend: str = "local"
    ocr_backend: str = "mistral_vision"
    cors_origins: str = "*"
    log_level: str = "INFO"  # DEBUG | INFO | WARNING | ERROR — applied to all applire.* loggers
    # Which compose topology this instance runs (ADR-087 cl. 9, JF-O-1.2).
    # "production" is the code default and therefore true of docker-compose.yml
    # alone; docker-compose.override.yml — which Compose auto-applies whenever
    # it sits beside the compose file, i.e. in every source clone — sets "dev".
    # A "dev" value logs a startup WARNING and is reported at GET /health, because
    # the dev topology publishes :8001 (unauthenticated API) and :5433 (Postgres
    # with the default credentials) and today said so nowhere. Never set by hand.
    applire_topology: str = "production"
    # Seconds after which an unattended in-app notice pop-up hides itself; 0 = never
    # (founder ruling V-1, 2026-09-09). The candidate's pending-decision pop-up on the
    # gaps page reads it. Served read-only on GET /api/settings as
    # `notice_auto_dismiss_seconds` — it is an INSTANCE setting, not a user preference,
    # so PATCH does not accept it: an operator who needs a longer read (accessibility,
    # a shared screen) sets it in the environment for the whole instance.
    notice_auto_dismiss_seconds: int = 30
    # Interview question-count budget (issue #259 / PO: "if the ceiling is a
    # bottleneck, it's artificial"). This is now a COST GUARD, not the primary
    # termination driver: the interview ends on sufficiency (every JD-critical
    # concept addressed/denied/triaged — services/interview/sufficiency.py) OR
    # this budget OR an explicit user 'done', whichever comes first. Defaults
    # mirror the pre-#259 hardcoded ceilings (constants.py) so an operator who
    # sets nothing sees unchanged behaviour; raise these if a real run is
    # hitting the ceiling before sufficiency (the run-4 finding this issue
    # fixes) rather than editing constants.py.
    interview_max_questions_targeted: int = INTERVIEW_HARD_CEILING_TARGETED  # MODE A
    interview_max_questions_guided: int = INTERVIEW_HARD_CEILING_GUIDED     # MODE B
    # ADR-093 / ADR-001 amended 2026-10-07 (#726, ruling E-3): fetch LinkedIn's
    # guest posting pages. Off -> both doors refuse a LinkedIn URL with the
    # manual-paste message. Panel-editable.
    scraper_fetch_linkedin_guest_pages: bool = True
    # ADR-093 / ADR-005 amended 2026-10-07 (#738): the GDPR retention sweep of
    # personal-data TTLs. Off suspends the calendar TTLs only, never erasure,
    # the cancelled-application path or housekeeping. Cloud ignores False.
    retention_enabled: bool = True


settings = Settings()

# Edition detection: presence of the applire.cloud package IS the gate (ADR 012).
# APPLIRE_EDITION env var has been removed — do not re-add it.
try:
    import applire.cloud  # type: ignore[import-not-found]
    HAS_CLOUD = True
except ImportError:
    HAS_CLOUD = False
