# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Admin surface and the split health payload (ADR-091 cl. 19, 22, 27; F1).

Routes: ``GET/POST /api/admin/users`` · ``PATCH/DELETE /api/admin/users/{user_id}`` ·
``POST /api/admin/users/{user_id}/reinvite`` · ``POST …/reset-link`` ·
``POST …/revoke-tokens`` · ``GET/POST /api/admin/probe-tokens`` ·
``DELETE /api/admin/probe-tokens/{token_id}`` · ``GET /health`` · ``GET /api/ops/health``.

**Metadata only (S-4).** Nothing here carries profile content, document text,
posting text or a token secret of a person; the admin routers' imports and the
compiled SQL of ``services/admin/metadata.py`` are tested against that (cl. 27).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from applire.schemas.auth import EMAIL_MAX_LENGTH, Role, _normalise_email

#: ``pending`` = invited, never signed in, no credential; ``active``; ``disabled``.
#: Tombstoned (deleted) accounts are not listed.
UserStatus = Literal["pending", "active", "disabled"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class AdminUserMetadata(BaseModel):
    """Counts and sizes only (S-4). Further fields are a CONTRACT-CHANGE."""

    application_count: int
    #: Generated CVs + cover letters.
    document_count: int
    #: Bytes of the person's uploads + rendered files, when measurable.
    storage_bytes: int | None = None
    #: LLM tokens (``llm_usage.total_tokens``) of the last 30 days (W0B-1, CONTRACT-CHANGE
    #: 1b-1); ``None`` while ``llm_usage`` carries no ``user_id`` (before migration 0075).
    ai_tokens_30d: int | None = None


class AdminUserItem(BaseModel):
    id: uuid.UUID
    email: str
    role: Role
    status: UserStatus
    created_at: datetime
    last_login_at: datetime | None = None
    last_active_at: datetime | None = None
    #: Pending accounts: expiry of the newest unused invite link (CONTRACT-CHANGE 1b-4,
    #: users-list mock "Invitation valid until {date}"); ``None`` otherwise.
    invite_expires_at: datetime | None = None
    metadata: AdminUserMetadata


class AdminUserListResponse(BaseModel):
    users: list[AdminUserItem]


class AdminUserCreateRequest(_Strict):
    email: str = Field(max_length=EMAIL_MAX_LENGTH)
    role: Role = "user"
    #: MD-28: the add-person dialog's "send the invitation by email" checkbox. Only
    #: decides about the mail (SMTP configured AND this flag); the link is always
    #: returned (ADR-091 cl. 22). Default true keeps every earlier caller unchanged.
    send_mail: bool = True

    _email = field_validator("email")(_normalise_email)


class AdminUserPatchRequest(_Strict):
    """At least one field; 409 ``last_admin`` when it would leave no active admin."""

    role: Role | None = None
    disabled: bool | None = None


class IssuedLink(BaseModel):
    """A single-use set-password link shown to the admin (S-6, S-7).

    ``url`` = ``<origin>/invite#<token>`` or ``<origin>/reset#<token>`` — the token
    only ever in the fragment (ADR-091 cl. 22). The admin hands it over; the admin
    never sees or sets the password.
    """

    purpose: Literal["invite", "reset"]
    url: str
    expires_at: datetime
    #: True when SMTP is configured and the mail was handed to the server.
    mailed: bool
    #: True when SMTP is configured but sending failed ("hand over the link instead").
    mail_failed: bool = False
    #: MD-32/MD-38: why ``mail_failed`` — ``base_url_unset`` (SMTP on, no
    #: APPLIRE_BASE_URL: no mail is ever built from a request header) or
    #: ``send_failed`` (the SMTP server refused or was unreachable); else null.
    mail_failed_reason: Literal["base_url_unset", "send_failed"] | None = None


class AdminUserCreatedResponse(BaseModel):
    """``POST /api/admin/users`` (201)."""

    user: AdminUserItem
    link: IssuedLink


class RevokeTokensResponse(BaseModel):
    """``POST /api/admin/users/{id}/revoke-tokens`` — every agent/api token of the
    person; bumps ``users.link_epoch`` so signed document links die too."""

    revoked: int


# ---------------------------------------------------------------------------
# Probe tokens (S-16) — scope ``probe``, accepted only on GET /api/ops/health
# ---------------------------------------------------------------------------


class ProbeTokenCreateRequest(_Strict):
    name: str = Field(min_length=1, max_length=80)


class ProbeTokenItem(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None = None


class ProbeTokenCreatedResponse(ProbeTokenItem):
    """Shown once (201)."""

    token: str


class ProbeTokenListResponse(BaseModel):
    tokens: list[ProbeTokenItem]


# ---------------------------------------------------------------------------
# Health split (RD-1 / ADR-091 cl. 19)
# ---------------------------------------------------------------------------


class LivenessResponse(BaseModel):
    """``GET /health`` — public, and nothing else (RD-1)."""

    status: str
    edition: str
    version: str


class OpsHealthResponse(BaseModel):
    """``GET /api/ops/health`` (``admin_or_probe``) — today's ops report
    (``services/ops/aggregate.collect``: ``status``, ``edition``, ``version``,
    ``llm_provider``, ``checked_at``, ``components``, …) **plus** the fields that
    moved off ``/health``. 503 when ``status`` is ``down`` (unchanged)."""

    model_config = ConfigDict(extra="allow")

    status: str
    edition: str
    version: str
    llm_provider: str
    # --- moved from /health (RD-1) ---------------------------------------
    upgrade_notice: dict[str, Any] | None = None
    debug_log_on: bool = False
    topology: str = "production"
    #: RD-9 / MD-27: number of older duplicate master profiles migration 0074 set
    #: aside on upgrade (count only — never ids); 0 when nothing was retired.
    retired_profiles: int = 0


# ===========================================================================
# Epic C — instance settings, audit view, usage, dashboard (ADR-093; build 2)
# Contract: docs/dev/api-contract-admin.md. Everything below is metadata only
# (S-4, RD-6): no vault content, no document text, no error message text, and
# NEVER a secret value — a secret is reported as ``is_set`` only (ADR-093 cl. 4).
# ===========================================================================

SettingKind = Literal["enum", "string", "bool", "secret"]
SettingGroup = Literal["llm", "scraper", "retention"]
#: Where the effective value comes from (ADR-093 cl. 3, ruling C1-2):
#: ``panel`` = an admin override stored in the database (wins over env);
#: ``env`` = the operator set it in the environment / ``.env``;
#: ``default`` = neither — the code default (``settings_registry``).
SettingSource = Literal["panel", "env", "default"]
SettingValue = str | bool


class InstanceSettingItem(BaseModel):
    """One panel-editable setting. ``key`` is the registry env-var name."""

    key: str
    group: SettingGroup
    kind: SettingKind
    #: For per-provider fields (``OPENROUTER_MODEL``, ``OPENROUTER_API_KEY``):
    #: the provider id they belong to; ``None`` otherwise.
    provider: str | None = None
    #: ``kind == "enum"`` only: the accepted values.
    choices: list[str] | None = None
    #: The EFFECTIVE value. **Always ``None`` for ``kind == "secret"``** — a
    #: secret is never echoed, not even masked or truncated.
    value: SettingValue | None = None
    source: SettingSource
    #: What ``DELETE /api/admin/settings/{key}`` would restore (env or default).
    #: Always ``None`` for a secret.
    env_value: SettingValue | None = None
    #: Secret: an effective non-empty value exists. Non-secret: always true.
    is_set: bool = True
    #: Secret: the environment carries a non-empty value (what reset falls back to).
    env_is_set: bool = True
    #: ``source == "panel"`` only.
    updated_at: datetime | None = None
    updated_by_user_id: uuid.UUID | None = None
    #: CONTRACT-CHANGE MD2-6 (4): the author's CURRENT email, resolved live;
    #: ``None`` when the source is not ``panel`` or the account was erased.
    updated_by_email: str | None = None


Qualification = Literal["qualified", "not_qualified", "unmeasured"]


class ProviderStatus(BaseModel):
    """One selectable LLM provider (ADR-009 list minus ``mock``)."""

    id: str
    #: Effective model id for this provider.
    model: str
    #: False for providers that need no key (``ollama``; ``openai`` may point
    #: at a keyless local server, so its key is optional too).
    key_required: bool
    has_key: bool
    #: ``has_key or not key_required`` — the provider can be made active.
    ready: bool
    active: bool
    #: CONTRACT-CHANGE MD2-6 (1): what the published matrix (docs/llm-models.md,
    #: #688) says about THIS provider's effective model. Data:
    #: ``backend/applire/data/model_qualification.json``.
    qualification: Qualification = "unmeasured"
    qualification_reason: str | None = None
    #: The date the qualification data describes (ISO date), e.g. "2026-09-16".
    qualification_as_of: str | None = None


class SettingDependency(BaseModel):
    """A dependency the panel shows instead of breaking silently (#710).

    ``code``: ``ocr_needs_mistral_key`` (``OCR_BACKEND=mistral_vision`` needs a
    Mistral key whatever the LLM provider is). More codes are additive.
    """

    code: str
    satisfied: bool
    keys: list[str]


class InstanceSettingsResponse(BaseModel):
    """``GET /api/admin/settings`` and the body of every successful write."""

    items: list[InstanceSettingItem]
    providers: list[ProviderStatus]
    dependencies: list[SettingDependency]


class InstanceSettingsUpdate(_Strict):
    """``PUT /api/admin/settings`` — applied atomically (all or nothing).

    ``changes`` maps a registry key to its new value: ``str`` for enum/string/
    secret, ``bool`` for bool. A secret cannot be set to ``""`` here — removing
    an override is ``DELETE /api/admin/settings/{key}``.
    """

    changes: dict[str, SettingValue] = Field(min_length=1, max_length=20)


class AuditEventItem(BaseModel):
    """One ``audit_events`` row (ADR-091 cl. 26). ``detail`` holds ids, roles,
    reasons, counts, setting keys and non-secret values only (services/audit.py)."""

    id: uuid.UUID
    at: datetime
    action: str
    actor_user_id: uuid.UUID | None = None
    #: Resolved live from ``users``; ``None`` = the system, or an erased account.
    actor_email: str | None = None
    target_user_id: uuid.UUID | None = None
    target_email: str | None = None
    detail: dict[str, Any]


class AuditPageResponse(BaseModel):
    """``GET /api/admin/audit`` — newest first, keyset-paged."""

    items: list[AuditEventItem]
    #: Pass as ``cursor`` for the next (older) page; ``None`` = no more rows.
    next_cursor: str | None = None
    #: Every action the audit service knows — for the filter dropdown.
    actions: list[str]


class UsageTotals(BaseModel):
    calls: int = 0
    failed_calls: int = 0
    #: Calls whose token counts are a length estimate (SF-OPS.8).
    estimated_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0


class UsageUserRow(BaseModel):
    user_id: uuid.UUID
    email: str
    role: Role
    status: UserStatus
    totals: UsageTotals
    last_call_at: datetime | None = None


class UsageProviderRow(BaseModel):
    provider: str
    model: str
    totals: UsageTotals


class AdminUsageResponse(BaseModel):
    """``GET /api/admin/usage?days=N`` — aggregates of ``llm_usage`` (S-8, B2-2).

    ``users`` lists every account that is not erased (zero rows included),
    sorted by ``totals.total_tokens`` descending. ``unattributed`` = calls with
    ``user_id`` NULL (system work, pre-0075 rows, erased accounts).
    """

    window_days: int
    since: datetime
    totals: UsageTotals
    users: list[UsageUserRow]
    unattributed: UsageTotals
    by_provider: list[UsageProviderRow]
    #: CONTRACT-CHANGE MD2-6 (2): ``llm_usage.document_kind`` buckets — ``cv``,
    #: ``cover_letter``, and ``other`` (every call not attributed to a document).
    by_document_kind: dict[Literal["cv", "cover_letter", "other"], UsageTotals]


AdminNoticeCode = Literal[
    "health_down",
    "health_degraded",
    "upgrade_notice",
    "retention_disabled",
    "failed_jobs",
    "debug_log_on",
    "dev_topology",
    "provider_not_ready",
    "ocr_needs_mistral_key",
    "settings_secret_unreadable",
]


class AdminNotice(BaseModel):
    code: AdminNoticeCode
    severity: Literal["warning", "critical"]


class AdminNoticesResponse(BaseModel):
    """``GET /api/admin/notices`` — the cheap call behind the admin-only one-line
    signal on the user dashboard ("Instanz: 2 Hinweise → Admin", #694)."""

    count: int
    items: list[AdminNotice]


class DashboardComponent(BaseModel):
    name: str
    status: str


class DashboardHealth(BaseModel):
    status: str
    version: str
    edition: str
    topology: str
    debug_log_on: bool
    llm_provider: str
    llm_model: str
    checked_at: datetime | None = None
    components: list[DashboardComponent]


class DashboardUsers(BaseModel):
    total: int
    active: int
    pending: int
    disabled: int
    admins: int


FailedJobKind = Literal["cv", "cover_letter", "import", "gap"]


class FailedJobItem(BaseModel):
    """A failed background job — kind, ids, time and a stable code. Never the
    error message text (it can quote document content)."""

    kind: FailedJobKind
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    user_email: str | None = None
    failed_at: datetime | None = None
    error_code: str | None = None


class DashboardFailedJobs(BaseModel):
    window_days: int
    count: int
    #: Newest first, at most 20.
    items: list[FailedJobItem]


class RetentionTtlDays(BaseModel):
    """CONTRACT-CHANGE MD2-8: the effective TTLs (env, ``constants.py``), in days;
    0 = that rule never expires anything."""

    uploads: int
    interview_sessions: int
    generated_documents: int
    cancelled_applications: int
    profile_inactivity: int
    audit_log: int


class DashboardRetention(BaseModel):
    """#738: is the GDPR retention sweep on, and did it run."""

    enabled: bool
    source: SettingSource
    last_run_at: datetime | None = None
    last_run_ok: bool | None = None
    #: The last run found retention disabled and skipped the personal-data TTLs.
    last_run_skipped: bool | None = None
    #: CONTRACT-CHANGE MD2-6 (3): while ON, the start of the current uninterrupted
    #: ON period — the newest audit row that turned it on (``settings.changed`` /
    #: ``settings.reset`` / ``settings.env_observed``), else the instance claim
    #: time (``setup.claimed``), else ``None``. ``None`` while OFF.
    enabled_since: datetime | None = None
    #: While OFF: the current email of the admin whose panel change turned it
    #: off; ``None`` when ON, or when it was turned off through the environment.
    changed_by_email: str | None = None
    ttl_days: RetentionTtlDays


class AdminDashboardResponse(BaseModel):
    """``GET /api/admin/dashboard`` (#694)."""

    health: DashboardHealth
    users: DashboardUsers
    usage_30d: UsageTotals
    failed_jobs: DashboardFailedJobs
    upgrade_notice: dict[str, Any] | None = None
    retention: DashboardRetention
    notices: list[AdminNotice]
