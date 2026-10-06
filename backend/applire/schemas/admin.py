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
