# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in, setup and link JSON shapes (ADR-091; frozen interface F1).

The prose contract — status codes, cookies, which dependency guards which route —
is ``docs/dev/api-contract-strawberry.md``. Changing a shape here is a
CONTRACT-CHANGE (Strawberry COMMON-BRIEF §9).

Routes: ``GET /api/auth/state`` · ``POST /api/auth/login`` · ``POST /api/auth/logout`` ·
``GET /api/auth/me`` · ``POST /api/auth/password`` · ``POST /api/auth/forgot`` ·
``POST /api/auth/links/inspect`` · ``POST /api/auth/links/redeem`` ·
``GET /api/auth/oidc/start`` · ``GET /api/auth/oidc/callback`` · ``POST /api/setup``.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------

Role = Literal["admin", "user"]
LinkPurpose = Literal["invite", "reset"]
LinkState = Literal["valid", "expired", "used"]

#: RD-8: 12–256 characters, no composition rules, not equal to the email
#: (the email rule needs the account and is enforced by the route, 422 ``password_policy``).
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256
EMAIL_MAX_LENGTH = 255

#: Error codes (``detail.error_code``) — the closed list is in the contract doc §Errors.
ErrorCode = Literal[
    "unauthenticated",
    "forbidden",
    "invalid_credentials",
    "setup_done",
    "harness_active",
    "harness_disabled",
    "invalid_setup_token",
    "last_admin",
    "link_used",
    "link_expired",
    "link_invalid",
    "origin_mismatch",
    "email_taken",
    "password_policy",
    "reauth_required",
    "oidc_failed",
    # TODO(FOUNDER-QUESTION pending): "account_disabled" (403) — whether a disabled
    # person is told so at sign-in or sees invalid_credentials. A RULING settles it.
]


def _normalise_email(value: str) -> str:
    value = value.strip()
    if "@" not in value or value.startswith("@") or value.endswith("@") or " " in value:
        raise ValueError("not an email address")
    return value


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(BaseModel):
    """The ``detail`` object of every Strawberry error response.

    Body: ``{"detail": {"error_code": "<ErrorCode>", "message": "<English text>"}}`` —
    the codebase's existing error shape (``routers/job.py``, ``routers/session.py``).
    The frontend translates by ``error_code``; ``message`` is for logs and scripts.
    Exception: a missing or foreign resource id stays ``{"detail": "<kind> not found"}``
    (ADR-092 cl. 6, identical to today's 404s).
    """

    error_code: ErrorCode
    message: str


# ---------------------------------------------------------------------------
# GET /api/auth/state (public)
# ---------------------------------------------------------------------------


class AuthStateResponse(BaseModel):
    """What the login/setup pages need before anyone is signed in.

    Reveals only instance mode (adversarial-security: HOLDS) — never user data.
    """

    setup_required: bool
    oidc_enabled: bool
    #: ``OIDC_BUTTON_LABEL`` (default "Single sign-on"); meaningful only when
    #: ``oidc_enabled``. Rendered as "Sign in with <label>" by the frontend copy.
    oidc_button_label: str
    smtp_enabled: bool
    #: ADR-091 cl. 3(d): the test harness is serving this instance (red banner).
    harness: bool


# ---------------------------------------------------------------------------
# POST /api/auth/login · POST /api/setup · POST /api/auth/password
# ---------------------------------------------------------------------------


class LoginRequest(_Strict):
    email: str = Field(max_length=EMAIL_MAX_LENGTH)
    #: Not policy-checked at login (an old password may predate the policy);
    #: only the upper bound guards the hash cost.
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class SetupRequest(_Strict):
    """Claim the instance with the per-boot setup code (ADR-091 cl. 14)."""

    #: As printed in the backend log: base32 groups; case and ``-``/spaces ignored.
    setup_token: str = Field(min_length=1, max_length=64)
    email: str = Field(max_length=EMAIL_MAX_LENGTH)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)

    _email = field_validator("email")(_normalise_email)


class PasswordChangeRequest(_Strict):
    """Self-service change, session only (ADR-091 cl. 23)."""

    current: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    new: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


# ---------------------------------------------------------------------------
# GET /api/auth/me
# ---------------------------------------------------------------------------


class MeResponse(BaseModel):
    """The signed-in person (``ShellUserContext`` in the frontend)."""

    id: uuid.UUID
    email: str
    role: Role
    #: False for OIDC-only accounts (no password to change; re-auth goes via OIDC).
    has_password: bool
    #: True when an ``(oidc_issuer, oidc_subject)`` binding exists.
    oidc_linked: bool
    #: ``user_settings.ui_language`` when set (``"de"``/``"en"``), else ``None``.
    ui_language: str | None = None


# ---------------------------------------------------------------------------
# Forgot / invite / reset links (token only in the page fragment + POST bodies)
# ---------------------------------------------------------------------------


class ForgotRequest(_Strict):
    """Always answered 202 with no body (no account oracle, ADR-091 cl. 23)."""

    email: str = Field(max_length=EMAIL_MAX_LENGTH)


class LinkInspectRequest(_Strict):
    token: str = Field(min_length=1, max_length=128)


class LinkInspectResponse(BaseModel):
    """What the ``/invite`` and ``/reset`` pages show before asking for a password.

    An unknown/garbled token is 404 ``link_invalid`` — not a ``state``.
    """

    purpose: LinkPurpose
    #: The account's email, so the person sees which account they set a password for.
    email: str
    state: LinkState


class LinkRedeemRequest(_Strict):
    token: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
