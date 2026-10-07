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

"""Runtime instance settings — the admin override store (ADR-093).

The one module that reads and writes ``instance_settings``. It owns:

* **refresh** — read every override row into the process-wide overlay snapshot
  that ``config.Settings`` resolves panel fields through (cl. 5/6). Never raises:
  a missing or unreadable table keeps the last good snapshot (empty at start).
* **pinning** — the web middleware (:class:`InstanceSettingsPin`) and the MCP
  wrapper (:func:`pinned_call`) pin the snapshot for one request / tool call, so
  work started under it — including its background tasks — keeps it (cl. 5,
  ruling C1-4).
* **writes** — validated against the registry's ``PANEL_KEYS``, atomic, audited
  in the same transaction (cl. 7). Secrets are Fernet-encrypted under a key
  purpose-derived from the instance secret and are **never** returned, logged or
  audited by value (cl. 4).
* **boot observation** — env-sourced changes of the tracked keys become audit
  rows (cl. 8).

Logging rule for this module: key NAMES and sources only, never a value of a
secret key — and, to keep the rule simple to review, never any value at all.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from applire import config as _config
from applire import settings_registry as registry
from applire.config import HAS_CLOUD, settings
from applire.models.instance_settings import InstanceSetting

logger = logging.getLogger("applire.instance_settings")

Source = Literal["panel", "env", "default"]
Kind = Literal["enum", "string", "bool", "secret"]

#: ADR-009's selectable providers — ``mock`` is never offered on the panel.
PROVIDERS: tuple[str, ...] = ("mistral", "openrouter", "requesty", "anthropic", "openai", "ollama")
#: A provider whose key is REQUIRED to be ready. ``openai`` may point at a keyless
#: local server (``OPENAI_BASE_URL``), ``ollama`` has no key.
KEY_REQUIRED: dict[str, bool] = {
    "mistral": True,
    "openrouter": True,
    "requesty": True,
    "anthropic": True,
    "openai": False,
    "ollama": False,
}
BOOL_KEYS = frozenset({"SCRAPER_FETCH_LINKEDIN_GUEST_PAGES", "RETENTION_ENABLED"})
#: ADR-093 cl. 8 — tracked across boots for the "stayed on" proof.
TRACKED_KEYS: tuple[str, ...] = (
    "LLM_PROVIDER",
    "SCRAPER_FETCH_LINKEDIN_GUEST_PAGES",
    "RETENTION_ENABLED",
)
MAX_STRING_LEN = 200
#: Web workers re-read the table at most this often (cl. 6).
WEB_REFRESH_SECONDS = 2.0


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class SettingsError(Exception):
    """A refused settings write; ``code`` is the contract's ``error_code``."""

    def __init__(self, code: str, status: int, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.extra = extra


# --------------------------------------------------------------------------
# Metadata per panel key
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyMeta:
    key: str
    field: str
    kind: Kind
    group: Literal["llm", "scraper", "retention"]
    provider: str | None
    secret: bool


def _meta_for(key: str) -> KeyMeta:
    entry = registry.get(key)
    if entry is None or not entry.panel:
        raise KeyError(key)
    provider = None
    for family in PROVIDERS:
        if key.startswith(family.upper() + "_"):
            provider = family
    if entry.secret:
        kind: Kind = "secret"
    elif key == "LLM_PROVIDER":
        kind = "enum"
    elif key in BOOL_KEYS:
        kind = "bool"
    else:
        kind = "string"
    group = "llm" if key.startswith(("LLM_",)) or provider else (
        "scraper" if key.startswith("SCRAPER_") else "retention"
    )
    return KeyMeta(key=key, field=key.lower(), kind=kind, group=group, provider=provider, secret=entry.secret)


META: dict[str, KeyMeta] = {k: _meta_for(k) for k in registry.PANEL_KEYS}


def model_key(provider: str) -> str:
    return f"{provider.upper()}_MODEL"


def api_key_key(provider: str) -> str | None:
    key = f"{provider.upper()}_API_KEY"
    return key if key in META else None


# --------------------------------------------------------------------------
# Encryption (ADR-093 cl. 4)
# --------------------------------------------------------------------------


class SecretUnavailable(RuntimeError):
    """The instance secret is not loaded — a secret cannot be encrypted."""


def _fernet():
    from cryptography.fernet import Fernet

    from applire.auth import links

    try:
        raw = links.derive_key("instance-settings")
    except links.InstanceSecretMissing as exc:
        raise SecretUnavailable("instance secret not loaded") from exc
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str | None:
    """The plaintext, or ``None`` when the token no longer decrypts."""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, SecretUnavailable, ValueError):
        return None


# --------------------------------------------------------------------------
# Refresh + pinning (ADR-093 cl. 5/6)
# --------------------------------------------------------------------------


@dataclass
class _ProcessState:
    last_refresh: float = 0.0
    #: Keys whose stored secret did not decrypt at the last refresh.
    unreadable: frozenset[str] = frozenset()


_state = _ProcessState()


def _session_factory():
    """Indirection so tests can point refresh at their own engine."""
    from applire.db.session import AsyncSessionLocal

    return AsyncSessionLocal


def unreadable_secrets() -> frozenset[str]:
    return _state.unreadable


def _coerce(meta: KeyMeta, value: Any) -> Any:
    if meta.kind == "bool":
        return bool(value)
    return str(value)


async def _ensure_instance_secret(db: AsyncSession) -> None:
    from applire.auth import links

    try:
        links.derive_key("instance-settings")
    except links.InstanceSecretMissing:
        await links.load_instance_secret(db)


async def load_rows(db: AsyncSession) -> list[InstanceSetting]:
    return list((await db.execute(select(InstanceSetting))).scalars().all())


async def _build_snapshot(db: AsyncSession) -> tuple[dict[str, Any], frozenset[str]]:
    rows = await load_rows(db)
    snapshot: dict[str, Any] = {}
    unreadable: set[str] = set()
    if any(r.secret_ciphertext for r in rows):
        await _ensure_instance_secret(db)
    for row in rows:
        meta = META.get(row.key)
        if meta is None:
            logger.warning("instance_settings: ignoring unknown key %s", row.key)
            continue
        if meta.secret:
            plain = decrypt_secret(row.secret_ciphertext or "")
            if plain is None:
                unreadable.add(row.key)
                continue  # reads as unset — never a plain-text fallback
            snapshot[meta.field] = plain
        elif row.value is not None:
            snapshot[meta.field] = _coerce(meta, row.value)
    if HAS_CLOUD:
        snapshot.pop("retention_enabled", None)  # ADR-005: mandatory in Cloud
    return snapshot, frozenset(unreadable)


async def refresh(*, max_age: float = 0.0) -> Mapping[str, Any]:
    """Re-read the overrides into the process-wide snapshot; return it.

    ``max_age`` > 0 skips the read when the last refresh is younger (web
    middleware). Never raises: on any failure the previous snapshot stays.
    """
    if max_age and (time.monotonic() - _state.last_refresh) < max_age:
        return _config.overlay_latest()
    try:
        async with _session_factory()() as db:
            snapshot, unreadable = await _build_snapshot(db)
    except Exception as exc:  # table absent (pre-0080), DB blip — never into a request
        logger.warning(
            "instance_settings refresh failed (%s) — keeping the previous overrides",
            type(exc).__name__,
        )
        _state.last_refresh = time.monotonic()
        return _config.overlay_latest()
    _config.set_overlay_latest(snapshot)
    _state.unreadable = unreadable
    _state.last_refresh = time.monotonic()
    return _config.overlay_latest()


def invalidate() -> None:
    """Force the next ``refresh(max_age=…)`` to read (after a local write)."""
    _state.last_refresh = 0.0


@contextlib.asynccontextmanager
async def pinned_call() -> AsyncIterator[None]:
    """Refresh, then pin the snapshot for this call (MCP ``_agent_call``)."""
    snapshot = await refresh()
    token = _config.pin_overlay(snapshot)
    try:
        yield
    finally:
        _config.unpin_overlay(token)


class InstanceSettingsPin:
    """Pure ASGI middleware: refresh (≤ every 2 s) and pin per HTTP request.

    Pure ASGI rather than ``BaseHTTPMiddleware`` so the ContextVar set here is
    the context the route — and every ``BackgroundTasks`` job it schedules —
    runs in (ADR-093 cl. 5).
    """

    def __init__(self, app) -> None:  # noqa: ANN001 — ASGI app
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        snapshot = await refresh(max_age=WEB_REFRESH_SECONDS)
        token = _config.pin_overlay(snapshot)
        try:
            await self.app(scope, receive, send)
        finally:
            _config.unpin_overlay(token)


# --------------------------------------------------------------------------
# Effective values and sources
# --------------------------------------------------------------------------


def _env_snapshot() -> dict[str, str]:
    return registry.current_environment()


def _env_source(key: str, env: Mapping[str, str]) -> Source:
    return "env" if key in env else "default"


def effective(field: str) -> Any:
    """The effective value of a panel field in the current context."""
    return getattr(settings, field)


def retention_state() -> tuple[bool, Source]:
    """``(enabled, source)`` of the GDPR retention toggle (#738). Cloud: always on."""
    if HAS_CLOUD:
        return True, "default"
    overlay = _config.active_overlay()
    if "retention_enabled" in overlay:
        return bool(overlay["retention_enabled"]), "panel"
    return bool(settings.env_value("retention_enabled")), _env_source(
        "RETENTION_ENABLED", _env_snapshot()
    )


def _has_effective_key(provider: str) -> bool:
    key = api_key_key(provider)
    if key is None:
        return False
    return bool((effective(key.lower()) or "").strip())


def provider_ready(provider: str) -> bool:
    return (not KEY_REQUIRED.get(provider, True)) or _has_effective_key(provider)


_QUALIFICATION_FILE = "model_qualification.json"
_qualification_cache: dict[str, Any] | None = None


def _qualification_data() -> dict[str, Any]:
    """``data/model_qualification.json`` (CONTRACT-CHANGE MD2-6; #688 owns it)."""
    global _qualification_cache
    if _qualification_cache is None:
        import json
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "data" / _QUALIFICATION_FILE
        try:
            _qualification_cache = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            logger.warning("model qualification data unreadable — every model reads as unmeasured")
            _qualification_cache = {"entries": []}
    return _qualification_cache


def qualification_for(provider: str, model: str) -> dict[str, Any]:
    """``{qualification, qualification_reason, qualification_as_of}`` for a model."""
    data = _qualification_data()
    wanted = (model or "").strip().lower()
    for entry in data.get("entries", []):
        if entry.get("provider") != provider:
            continue
        pattern = str(entry.get("model", "")).lower()
        hit = wanted == pattern if entry.get("match") == "exact" else bool(pattern) and pattern in wanted
        if wanted and hit:
            return {
                "qualification": entry.get("qualification", "unmeasured"),
                "qualification_reason": entry.get("reason"),
                # #688 (MD2-13): each entry carries the date IT was measured;
                # the file-level date is the newest and would misdate the rest.
                "qualification_as_of": entry.get("as_of") or data.get("as_of"),
            }
    return {"qualification": "unmeasured", "qualification_reason": None,
            "qualification_as_of": data.get("as_of")}


def providers_status() -> list[dict[str, Any]]:
    active = (effective("llm_provider") or "").strip().lower()
    out = []
    for p in PROVIDERS:
        model = str(effective(model_key(p).lower()) or "")
        out.append(
            {
                "id": p,
                "model": model,
                "key_required": KEY_REQUIRED[p],
                "has_key": _has_effective_key(p),
                "ready": provider_ready(p),
                "active": p == active,
                **qualification_for(p, model),
            }
        )
    return out


def dependencies() -> list[dict[str, Any]]:
    out = []
    if (getattr(settings, "ocr_backend", "") or "").strip().lower() == "mistral_vision":
        out.append(
            {
                "code": "ocr_needs_mistral_key",
                "satisfied": _has_effective_key("mistral"),
                "keys": ["OCR_BACKEND", "MISTRAL_API_KEY"],
            }
        )
    return out


def _env_value(meta: KeyMeta) -> Any:
    value = settings.env_value(meta.field)
    if meta.kind == "bool":
        return bool(value)
    return "" if value is None else str(value)


async def _emails(db: AsyncSession, ids: set) -> dict[uuid.UUID, str]:
    """Current emails of live accounts (resolved, never stored with the row)."""
    from applire.models.user import User

    ids = {i for i in ids if i is not None}
    if not ids:
        return {}
    rows = await db.execute(
        select(User.id, User.email).where(User.id.in_(ids), User.deleted_at.is_(None))
    )
    return {uid: email for uid, email in rows.all()}


async def build_response(db: AsyncSession) -> dict[str, Any]:
    """The ``InstanceSettingsResponse`` payload. Never contains a secret value."""
    rows = {r.key: r for r in await load_rows(db)}
    env = _env_snapshot()
    unreadable = unreadable_secrets()
    emails = await _emails(db, {r.updated_by_user_id for r in rows.values()})
    items = []
    for key in registry.PANEL_KEYS:
        meta = META[key]
        row = rows.get(key)
        if HAS_CLOUD and key == "RETENTION_ENABLED":
            row = None
        source: Source = "panel" if row is not None else _env_source(key, env)
        if meta.secret and row is not None and key in unreadable:
            source = _env_source(key, env)
        env_val = _env_value(meta)
        item: dict[str, Any] = {
            "key": key,
            "group": meta.group,
            "kind": meta.kind,
            "provider": meta.provider,
            "choices": list(PROVIDERS) if meta.kind == "enum" else None,
            "source": source,
            "updated_at": row.updated_at if row is not None else None,
            "updated_by_user_id": row.updated_by_user_id if row is not None else None,
            "updated_by_email": emails.get(row.updated_by_user_id) if row is not None else None,
        }
        if meta.secret:
            item["value"] = None
            item["env_value"] = None
            item["is_set"] = bool((effective(meta.field) or "").strip())
            item["env_is_set"] = bool(str(env_val).strip())
        else:
            item["value"] = effective(meta.field) if meta.kind == "bool" else str(effective(meta.field) or "")
            if meta.kind == "bool":
                item["value"] = bool(item["value"])
            item["env_value"] = env_val
            item["is_set"] = True
            item["env_is_set"] = True
        items.append(item)
    return {"items": items, "providers": providers_status(), "dependencies": dependencies()}


# --------------------------------------------------------------------------
# Writes (ADR-093 cl. 7)
# --------------------------------------------------------------------------


def _validate(key: str, value: Any) -> Any:
    meta = META.get(key)
    if meta is None:
        raise SettingsError("unknown_setting", 422, "Unknown setting.", key=key)
    if meta.kind == "bool":
        if not isinstance(value, bool):
            raise SettingsError("invalid_setting_value", 422, "Expected true or false.", key=key)
        if key == "RETENTION_ENABLED" and HAS_CLOUD and value is False:
            raise SettingsError(
                "invalid_setting_value", 422, "Retention is mandatory in the Cloud Edition.",
                key=key, reason="mandatory_in_cloud",
            )
        return value
    if not isinstance(value, str):
        raise SettingsError("invalid_setting_value", 422, "Expected a string.", key=key)
    value = value.strip()
    if not value or len(value) > MAX_STRING_LEN or any(ch in value for ch in "\r\n\x00"):
        raise SettingsError(
            "invalid_setting_value", 422, f"Expected 1-{MAX_STRING_LEN} characters on one line.", key=key
        )
    if meta.kind == "enum":
        value = value.lower()
        if value not in PROVIDERS:
            raise SettingsError("invalid_setting_value", 422, "Not a selectable provider.", key=key)
    return value


def _recordable(value: Any) -> Any:
    """A non-secret value as an audit detail: scalars only, never email-shaped."""
    from applire.services.audit import _EMAIL_SHAPE

    if isinstance(value, str) and _EMAIL_SHAPE.match(value):
        return None
    return value


async def _audit(db: AsyncSession, actor_id: uuid.UUID | None, action: str, details: dict) -> None:
    from applire.services import audit

    await audit.record(
        db, actor_id=actor_id, action=action, target_type="instance", target_id=None, details=details
    )


async def apply_changes(
    db: AsyncSession, *, actor_id: uuid.UUID, changes: Mapping[str, Any]
) -> None:
    """Validate and upsert ``changes`` atomically, with one audit row per key.

    The caller commits; on any error nothing is written. A secret is encrypted
    before it touches a row; if the instance secret is not loaded the write is
    refused (503), never stored in plain text.
    """
    if not isinstance(changes, Mapping) or not changes:
        raise SettingsError("invalid_setting_value", 422, "No changes given.")
    clean = {key: _validate(key, value) for key, value in changes.items()}

    # provider_not_ready: judged on the state AFTER this request's own changes.
    target_provider = clean.get("LLM_PROVIDER")
    if target_provider is not None and KEY_REQUIRED.get(target_provider, True):
        key_key = api_key_key(target_provider)
        incoming = bool(key_key and clean.get(key_key))
        if not incoming and not _has_effective_key(target_provider):
            raise SettingsError(
                "provider_not_ready", 409, "The provider has no API key.", provider=target_provider
            )

    if any(META[k].secret for k in clean):
        await _ensure_instance_secret(db)
        try:
            _fernet()
        except SecretUnavailable:
            raise SettingsError(
                "settings_secret_unavailable", 503, "The instance secret is not loaded."
            ) from None

    env = _env_snapshot()
    rows = {r.key: r for r in await load_rows(db)}
    now = datetime.now(timezone.utc)
    for key, value in clean.items():
        meta = META[key]
        row = rows.get(key)
        from_source: Source = "panel" if row is not None else _env_source(key, env)
        details: dict[str, Any] = {
            "key": key,
            "write_only": meta.secret,
            "from_source": from_source,
            "to_source": "panel",
        }
        if not meta.secret:
            details["from_value"] = _recordable(effective(meta.field))
            details["to_value"] = _recordable(value)
        if row is None:
            row = InstanceSetting(key=key)
            db.add(row)
        if meta.secret:
            row.secret_ciphertext = encrypt_secret(value)
            row.value = None
        else:
            row.value = value
            row.secret_ciphertext = None
        row.updated_at = now
        row.updated_by_user_id = actor_id
        await _audit(db, actor_id, "settings.changed", details)
    await db.flush()
    logger.info("instance settings changed: %s", ", ".join(sorted(clean)))


async def reset(db: AsyncSession, *, actor_id: uuid.UUID, key: str) -> None:
    """Remove the override of ``key`` (back to env/default), audited. Caller commits."""
    meta = META.get(key)
    if meta is None:
        raise SettingsError("unknown_setting", 404, "Unknown setting.", key=key)
    row = await db.get(InstanceSetting, key)
    env = _env_snapshot()
    details: dict[str, Any] = {"key": key, "write_only": meta.secret, "to_source": _env_source(key, env)}
    if row is not None:
        if not meta.secret:
            details["from_value"] = _recordable(row.value)
        await db.delete(row)
        await _audit(db, actor_id, "settings.reset", details)
        await db.flush()
        logger.info("instance setting reset to environment: %s", key)


async def after_commit() -> None:
    """Make this process see its own write at once (cl. 6)."""
    invalidate()
    await refresh()


# --------------------------------------------------------------------------
# Boot observation (ADR-093 cl. 8)
# --------------------------------------------------------------------------


def _observed_value(key: str) -> Any:
    meta = META[key]
    return _env_value(meta)


async def observe_boot(db: AsyncSession) -> int:
    """Audit env/default-sourced changes of the tracked keys since the last boot.

    Returns the number of audit rows written. The caller commits. Never raises
    into the lifespan: a failure is logged and the boot continues.
    """
    from applire.models.instance_state import KEY_SETTINGS_OBSERVED
    from applire.services.instance_state import read_state, write_state

    try:
        env = _env_snapshot()
        previous = await read_state(db, KEY_SETTINGS_OBSERVED)
        previous = previous if isinstance(previous, dict) else {}
        current = {key: _observed_value(key) for key in TRACKED_KEYS}
        written = 0
        for key, value in current.items():
            if key in previous and previous[key] == value:
                continue
            details = {
                "key": key,
                "to_value": _recordable(value),
                "to_source": _env_source(key, env),
            }
            if key in previous:
                details["from_value"] = _recordable(previous[key])
            await _audit(db, None, "settings.env_observed", details)
            written += 1
        if written or previous != current:
            await write_state(db, KEY_SETTINGS_OBSERVED, current)
        return written
    except Exception as exc:  # the boot must not fail on an audit trace
        logger.warning("settings boot observation skipped (%s)", type(exc).__name__)
        with contextlib.suppress(Exception):
            await db.rollback()
        return 0
