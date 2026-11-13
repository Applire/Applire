# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A person's own provider key (ADR-096 cl. 2/3) — store, read, test.

* **Encrypted at rest** with Fernet under
  ``HMAC-SHA256(PROVIDER_KEY_SECRET, "applire/user-provider-keys")``
  (``auth.links.derive_key("user-provider-keys")``). ``PROVIDER_KEY_SECRET`` is
  environment-only, so a database dump, even with the instance secret in it,
  decrypts no key (FMEA ``SF-ALLOW.7``).
* **Write-only.** Nothing here returns the plaintext to a caller outside the
  payer decision: no mask, no last four characters. :func:`describe` is the
  only read the API serves.
* **Never logged.** The plaintext lives in a :class:`~applire.services.allowance.PayerDecision`
  (``repr=False``) and in ``redaction``'s scrub list while a call runs.
* **Fixed endpoints only** (S0-11): the five families in
  :data:`~applire.models.allowance.USER_KEY_FAMILIES`; no base URL, no ``ollama``.
"""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from applire.config import has_unprintable_key_char
from applire.models.allowance import USER_KEY_FAMILIES, UserProviderKey

logger = logging.getLogger(__name__)

#: Same bound as the panel's model field (ADR-093 ``MAX_STRING_LEN``).
MAX_MODEL_LEN = 200
#: A provider key longer than this is not a key (the panel's string bound is 200;
#: some gateway keys are long JWT-like strings, so allow more but stay bounded).
MAX_KEY_LEN = 512
#: The key test's token budget — the ops probe's figure (``probes._REACHABILITY_MAX_TOKENS``).
_TEST_MAX_TOKENS = 16


class ProviderKeyError(Exception):
    """A refused key write; ``code`` is the contract's ``error_code``."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


# --------------------------------------------------------------------------
# Encryption
# --------------------------------------------------------------------------


def _fernet():
    from cryptography.fernet import Fernet

    from applire.auth import links

    return Fernet(base64.urlsafe_b64encode(links.derive_key("user-provider-keys")))


def secret_configured() -> bool:
    from applire.config import settings

    return bool((settings.provider_key_secret or "").strip())


def encrypt(plaintext: str) -> str:
    """Raises ``links.ProviderKeySecretMissing`` when the secret is unset — never
    stores plaintext and never falls back to the instance secret."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str | None:
    """The plaintext, or ``None`` when the token does not decrypt (a changed or
    lost ``PROVIDER_KEY_SECRET``: the key reads as unset, cl. 3).

    Raises ``links.ProviderKeySecretMissing`` when the secret is unset: that is
    ``provider_key_unreadable`` for the caller, not "no key".
    """
    from cryptography.fernet import InvalidToken

    fernet = _fernet()  # raises ProviderKeySecretMissing
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError):
        return None


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate(family: Any, model: Any, key: Any) -> tuple[str, str, str]:
    """Normalised ``(family, model, key)`` or :class:`ProviderKeyError` (422).

    The messages never quote the key.
    """
    if not isinstance(family, str) or family.strip().lower() not in USER_KEY_FAMILIES:
        raise ProviderKeyError(
            "invalid_provider_family", 422,
            "Choose one of: " + ", ".join(USER_KEY_FAMILIES) + ".",
        )
    family = family.strip().lower()
    if not isinstance(model, str):
        raise ProviderKeyError("invalid_model", 422, "Enter the model id.")
    model = model.strip()
    if not model or len(model) > MAX_MODEL_LEN or any(ch in model for ch in "\r\n\x00"):
        raise ProviderKeyError(
            "invalid_model", 422, f"Enter the model id (1-{MAX_MODEL_LEN} characters, one line)."
        )
    if not isinstance(key, str):
        raise ProviderKeyError("invalid_provider_key", 422, "Enter the API key.")
    key = key.strip()
    if not key or len(key) > MAX_KEY_LEN:
        raise ProviderKeyError("invalid_provider_key", 422, "Enter the API key.")
    if has_unprintable_key_char(key):
        raise ProviderKeyError(
            "invalid_provider_key", 422,
            "An API key may contain printable ASCII characters only (no spaces or control characters).",
        )
    return family, model, key


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyStatus:
    """What ``GET /api/me/provider-key`` may say about a stored key."""

    is_set: bool
    family: str | None
    model: str | None
    updated_at: datetime | None
    #: A row exists but does not decrypt (or the secret is unset): the person
    #: must enter the key again (cl. 3 "a notice").
    unreadable: bool


async def load_row(db: AsyncSession, user_id: uuid.UUID) -> UserProviderKey | None:
    return (
        await db.execute(select(UserProviderKey).where(UserProviderKey.user_id == user_id))
    ).scalar_one_or_none()


def row_status(row: UserProviderKey | None) -> KeyStatus:
    if row is None:
        return KeyStatus(False, None, None, None, False)
    from applire.auth.links import ProviderKeySecretMissing

    try:
        readable = decrypt(row.secret_ciphertext) is not None
    except ProviderKeySecretMissing:
        readable = False
    return KeyStatus(readable, row.family, row.model, row.updated_at, not readable)


async def describe(db: AsyncSession, user_id: uuid.UUID) -> KeyStatus:
    return row_status(await load_row(db, user_id))


async def store(
    db: AsyncSession, *, user_id: uuid.UUID, family: str, model: str, key: str
) -> UserProviderKey:
    """Upsert the one active key (the caller validated and commits)."""
    ciphertext = encrypt(key)
    row = await load_row(db, user_id)
    now = datetime.now(timezone.utc)
    if row is None:
        row = UserProviderKey(
            user_id=user_id, family=family, model=model, secret_ciphertext=ciphertext, updated_at=now
        )
        db.add(row)
    else:
        row.family = family
        row.model = model
        row.secret_ciphertext = ciphertext
        row.updated_at = now
    await db.flush()
    return row


async def remove(db: AsyncSession, user_id: uuid.UUID) -> bool:
    result = await db.execute(
        delete(UserProviderKey)
        .where(UserProviderKey.user_id == user_id)
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount)


# --------------------------------------------------------------------------
# The optional test call (cl. 2)
# --------------------------------------------------------------------------


async def test_key(*, family: str, model: str, key: str) -> tuple[bool, str]:
    """One minimal completion on the submitted key, before it is stored.

    Returns ``(ok, reason)``. The reason is ours, never the provider's text: a
    provider error can echo the key or a header (``SF-ALLOW.6``). The call is a
    user-paid call: it never touches a process-wide latch, and no ``llm_usage``
    row is written for it (it belongs to no action).
    """
    from applire.exceptions import (
        LLMProviderUnavailableError,
        LLMRateLimitError,
        LLMTimeoutError,
        LLMTruncatedError,
    )
    from applire.providers.llm import build_user_provider
    from applire.redaction import user_key_in_scope

    with user_key_in_scope(key):
        try:
            provider = build_user_provider(family=family, model=model, key=key)
            await provider.acomplete("ping", max_tokens=_TEST_MAX_TOKENS, temperature=0.0)
            return True, ""
        except LLMTruncatedError:
            return True, ""  # a length stop is an answer (ADR-086 amended 2026-09-17)
        except LLMRateLimitError:
            return False, "rate_limited"
        except LLMTimeoutError:
            return False, "timeout"
        except LLMProviderUnavailableError as exc:
            return False, _classify(str(exc))
        except Exception as exc:  # noqa: BLE001 — the reason is classified, never echoed
            return False, _classify(f"{type(exc).__name__}: {exc}")


def _classify(text_form: str) -> str:
    lowered = text_form.lower()
    if "402" in lowered or "credit" in lowered or "insufficient" in lowered:
        return "out_of_credit"
    if "401" in lowered or "403" in lowered or "unauthor" in lowered or "api key" in lowered or "authentication" in lowered:
        return "key_rejected"
    if "404" in lowered or "model" in lowered and ("not found" in lowered or "does not exist" in lowered):
        return "model_not_found"
    if "429" in lowered or "rate limit" in lowered:
        return "rate_limited"
    return "unreachable"
