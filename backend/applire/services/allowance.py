# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Who pays for a call, and may it run — ADR-096 (Sorbet WP-K, phase 1).

**One admission per action, at the door** (cl. 4). A door that starts model work
calls :func:`admit` once and gets a :class:`PayerDecision`, or a
:class:`AdmissionRefused`. The decision sits in a ContextVar, so the work's
``BackgroundTasks`` and ``asyncio`` tasks inherit it (the ADR-093 cl. 5
property) and **never re-admit**: a started action finishes on the decision it
was admitted with, whatever modes or grants change meanwhile (``SF-ALLOW.13``).

**The factory reads the decision** (:func:`resolve_for_factory`):

* an explicit decision in the context wins, and must belong to the current owner;
* with an owner and no explicit decision, the **safety net** decides
  ``model_work`` from the owner's *payer facts* — the grant and the stored key,
  read once at the auth door (``auth.deps._resolve``, ``mcp.server._agent_call``)
  because ``get_provider()`` is synchronous and cannot query. In ``user`` mode
  an owner with no facts (no auth door ran) is refused, never paid by the
  instance: :class:`AdmissionMissing`;
* with no owner, an ``unscoped(reason)`` whose reason is in
  :data:`LLM_SYSTEM_REASONS` pays as ``system`` (the instance key, never
  debited); anything else raises ``OwnerContextMissing`` — an unattributed
  generation is impossible rather than silently billed.

**The payer truth table** (cl. 5) — phase 1 ships ``ALLOWANCE_POLICY=none``, so
rows 1, 3, 4 and 7 apply; rows 2, 5 and 6 arrive with the ``allowance`` policy:

====  ==============  =====  =====  ======================  ==========================
row   LLM_KEY_MODE    grant  key    payer                   limits
====  ==============  =====  =====  ======================  ==========================
1     instance        any    any    instance                none (today's behaviour)
3     user            yes    any    instance                none (the grant)
4     user            no     yes    user                    none
7     user            no     no     refused                 ``provider_key_required``
====  ==============  =====  =====  ======================  ==========================

**There is never a silent fallback to the instance key** (S0-10): a key that no
longer decrypts reads as unset (row 7), an unset ``PROVIDER_KEY_SECRET`` refuses
with ``provider_key_unreadable``, and neither ever becomes an instance call.

The ledger (cl. 8–10) is here too: :func:`grant` is the one function the Cloud
seam calls; :func:`balance` reads it. With policy ``none`` nothing debits.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import uuid
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterator, Literal

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from applire.models.allowance import (
    LEDGER_ACTIONS,
    USER_KEY_FAMILIES,
    AllowanceLedger,
    UserProviderKey,
)
from applire.models.user import User
from applire.ownership import OwnerContextMissing, current_owner, unscoped

logger = logging.getLogger(__name__)

KeyMode = Literal["instance", "user"]
#: The mode a DECISION is made under. ``unknown``: the panel overrides were never
#: read in this process (a refresh failed) and the environment does not say
#: ``user`` — the panel might. Every decision then refuses
#: (``settings_unavailable``, 503), never pays as the instance (S0-10; adv-K F2).
#: Internal: the API and the panel still report ``key_mode()``.
DecisionMode = Literal["instance", "user", "unknown"]
Payer = Literal["user", "instance", "system"]
#: cl. 7's closed action set.
Action = Literal["application", "profile_import", "job_analysis", "model_work"]
ACTIONS: tuple[str, ...] = ("application", "profile_import", "job_analysis", "model_work")

#: cl. 4: ownerless model work allowed to run, as ``system`` (instance key, never
#: debited, never capped). ``ops-aggregate``: the ops reachability probe.
#: ``tooling``: operator scripts (bench, matrix, probes) run with the operator's
#: own ``.env`` — never reached by a request (no production code path declares
#: ``unscoped("tooling")``; pinned by a test).
LLM_SYSTEM_REASONS: frozenset[str] = frozenset({"ops-aggregate", "tooling"})

#: The refusal codes (cl. 11). Phase 2 adds ``allowance_exhausted`` and
#: ``allowance_unavailable``.
REFUSAL_MESSAGES: dict[str, str] = {
    "provider_key_required": (
        "Add your own AI provider key in Settings, or ask your admin to let you use "
        "this instance's key."
    ),
    "settings_unavailable": (
        "This instance's settings could not be read just now, so no AI work can "
        "start. Try again in a moment."
    ),
    "provider_key_unreadable": (
        "Your stored AI provider key cannot be read on this server right now. "
        "Ask your admin (PROVIDER_KEY_SECRET is not set)."
    ),
}


class AdmissionRefused(HTTPException):
    """A door refused the action (cl. 11). An ``HTTPException`` (the
    ``OwnedNotFound`` precedent), so REST renders it without a handler:
    ``409 {"detail": {"error_code": code, "message": message}}``; MCP answers
    ``mcp.errors.admission_refused`` with the code first in the message."""

    def __init__(self, code: str, *, status: int = 409) -> None:
        self.code = code
        self.message = REFUSAL_MESSAGES.get(code, code)
        super().__init__(status_code=status, detail={"error_code": code, "message": self.message})

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class AdmissionMissing(AdmissionRefused):
    """``user`` mode, an owner, no explicit decision and no payer facts: a code
    path started model work without passing an auth door. Refused (fail closed),
    never paid by the instance. Answered like ``provider_key_required`` so a
    person is never shown an internal error; the log names the path."""

    def __init__(self) -> None:
        super().__init__("provider_key_required")


class DecisionOwnerMismatch(RuntimeError):
    """The decision in the context belongs to another owner (``SF-ALLOW.2``)."""


# --------------------------------------------------------------------------
# The decision and the facts it is made from
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PayerDecision:
    """Who pays for one action (cl. 4). ``key`` is the plaintext user key for a
    ``user`` payer, else ``None``; it is never part of ``repr``."""

    payer: Payer
    action: str
    owner_id: uuid.UUID | None
    family: str | None = None
    model: str | None = None
    key: str | None = field(default=None, repr=False, compare=False)
    ref: str | None = None


@dataclass(frozen=True)
class PayerFacts:
    """What the safety net decides from, read once at the auth door."""

    owner_id: uuid.UUID
    mode: DecisionMode
    granted: bool = False
    family: str | None = None
    model: str | None = None
    ciphertext: str | None = field(default=None, repr=False, compare=False)


_decision: ContextVar[PayerDecision | None] = ContextVar("applire_payer_decision", default=None)
_facts: ContextVar[PayerFacts | None] = ContextVar("applire_payer_facts", default=None)

#: Plaintext user keys decrypted by this process, newest last — ``redaction``
#: scrubs them even from an error that surfaces outside the call's context
#: (defence in depth for ``SF-ALLOW.6``). Bounded; never logged.
_RECENT_KEYS: deque[str] = deque(maxlen=256)


def key_mode() -> KeyMode:
    """The effective ``LLM_KEY_MODE`` (panel override or environment; pinned per
    request by the ADR-093 overlay)."""
    from applire.config import settings

    return "user" if str(settings.llm_key_mode or "").strip().lower() == "user" else "instance"


def decision_mode() -> DecisionMode:
    """:func:`key_mode`, except ``unknown`` while this process never read the
    panel overrides (adv-K F2): an environment ``instance`` may be overridden by
    a panel ``user`` it could not read."""
    from applire.services.instance_settings import overlay_unknown

    mode = key_mode()
    if mode == "instance" and overlay_unknown():
        return "unknown"
    return mode


def _refuse_unknown_mode() -> None:
    raise AdmissionRefused("settings_unavailable", status=503)


def current_decision() -> PayerDecision | None:
    return _decision.get()


def current_facts() -> PayerFacts | None:
    return _facts.get()


@contextlib.contextmanager
def call_scope() -> Iterator[None]:
    """Start a call with no decision and no facts, and drop both when it ends —
    so a long-lived context (the MCP stdio server) never carries one call's
    payer into the next."""
    decision_token = _decision.set(None)
    facts_token = _facts.set(None)
    try:
        yield
    finally:
        _decision.reset(decision_token)
        _facts.reset(facts_token)


def reset_context_for_tests() -> None:
    """Test seam: drop any decision/facts the running context carries."""
    _decision.set(None)
    _facts.set(None)


def _remember_key(key: str) -> None:
    if key and key not in _RECENT_KEYS:
        _RECENT_KEYS.append(key)


def secret_values_in_context() -> list[str]:
    """Every user key ``redaction.scrub_secrets`` must remove right now: the
    current decision's key and the process's recently decrypted keys."""
    values: list[str] = list(_RECENT_KEYS)
    decision = _decision.get()
    if decision is not None and decision.key:
        values.append(decision.key)
    return values


# --------------------------------------------------------------------------
# Facts and the truth table
# --------------------------------------------------------------------------


async def load_facts(db: AsyncSession, user: User) -> PayerFacts:
    """Read the owner's grant and stored key once, at the auth door.

    ``instance`` mode issues no query (nothing on the default path changes).
    The owner context must already be set (the key table is owned).
    """
    mode = decision_mode()
    if mode in ("instance", "unknown"):
        facts = PayerFacts(owner_id=user.id, mode=mode)
    else:
        row = (
            await db.execute(select(UserProviderKey).where(UserProviderKey.user_id == user.id))
        ).scalar_one_or_none()
        facts = PayerFacts(
            owner_id=user.id,
            mode="user",
            granted=user.instance_key_granted_at is not None,
            family=row.family if row is not None else None,
            model=row.model if row is not None else None,
            ciphertext=row.secret_ciphertext if row is not None else None,
        )
    _facts.set(facts)
    return facts


def decide(facts: PayerFacts, action: str, ref: str | None = None) -> PayerDecision:
    """The truth table (cl. 5), policy ``none``. Pure apart from decrypting."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    if facts.mode == "unknown":  # the panel could not be read: never instance
        _refuse_unknown_mode()
    if facts.mode == "instance":  # row 1
        return PayerDecision("instance", action, facts.owner_id, ref=ref)
    if facts.granted:  # row 3
        return PayerDecision("instance", action, facts.owner_id, ref=ref)
    if facts.ciphertext and facts.family in USER_KEY_FAMILIES and facts.model:
        from applire.auth.links import ProviderKeySecretMissing
        from applire.services import provider_keys

        try:
            key = provider_keys.decrypt(facts.ciphertext)
        except ProviderKeySecretMissing:
            raise AdmissionRefused("provider_key_unreadable")
        if key:  # row 4
            _remember_key(key)
            return PayerDecision(
                "user", action, facts.owner_id, family=facts.family, model=facts.model,
                key=key, ref=ref,
            )
        # A key that no longer decrypts reads as unset (cl. 3) — row 7.
    raise AdmissionRefused("provider_key_required")  # row 7


def admit_from_facts(action: str, ref: str | None = None) -> PayerDecision:
    """Admit with the facts the auth door loaded for the current owner."""
    owner = current_owner()
    facts = _facts.get()
    if owner is None or owner.user_id is None or facts is None or facts.owner_id != owner.user_id:
        if decision_mode() == "unknown":
            _refuse_unknown_mode()
        if key_mode() == "instance" and owner is not None and owner.user_id is not None:
            decision = PayerDecision("instance", action, owner.user_id, ref=ref)
            _decision.set(decision)
            return decision
        raise AdmissionMissing()
    decision = decide(facts, action, ref)
    _decision.set(decision)
    return decision


async def admit(db: AsyncSession, user: User, action: str, ref: str | None = None) -> PayerDecision:
    """The door (cl. 4): decide once, set the decision for the rest of the work."""
    facts = _facts.get()
    if facts is None or facts.owner_id != user.id or facts.mode != decision_mode():
        facts = await load_facts(db, user)
    decision = decide(facts, action, ref)
    _decision.set(decision)
    return decision


def resolve_for_factory() -> PayerDecision:
    """The decision ``get_provider()`` builds from (cl. 4)."""
    owner = current_owner()
    decision = _decision.get()
    if decision is not None:
        if decision.payer != "system" and (owner is None or owner.user_id != decision.owner_id):
            # A decision may never pay for another owner's work (SF-ALLOW.2).
            raise DecisionOwnerMismatch(
                "the payer decision in this context belongs to a different owner"
            )
        return decision
    if owner is None:
        raise OwnerContextMissing(
            "model work with no owner context (ADR-096 cl. 4): run it inside "
            "owner_context(user_id) or an LLM-system unscoped(reason)"
        )
    if owner.is_unscoped:
        if owner.reason in LLM_SYSTEM_REASONS:
            return PayerDecision("system", "model_work", None)
        raise OwnerContextMissing(
            f"model work under unscoped({owner.reason!r}), which is not an LLM-system "
            f"reason {sorted(LLM_SYSTEM_REASONS)} (ADR-096 cl. 4)"
        )
    # The safety net: an owner, no explicit door decision.
    facts = _facts.get()
    if facts is not None and facts.owner_id == owner.user_id:
        return decide(facts, "model_work")
    if decision_mode() == "unknown":
        _refuse_unknown_mode()
    if key_mode() == "instance":
        return PayerDecision("instance", "model_work", owner.user_id)
    logger.warning(
        "model work reached the provider factory in LLM_KEY_MODE=user without an "
        "auth door (no payer facts for the owner) — refused (ADR-096 cl. 4)",
        stack_info=True,
    )
    raise AdmissionMissing()


def system_scope():
    """``unscoped("ops-aggregate")`` — for ownerless LLM-system work."""
    return unscoped("ops-aggregate")


# --------------------------------------------------------------------------
# What the admin panel and the person see
# --------------------------------------------------------------------------


async def key_mode_summary(db: AsyncSession) -> dict:
    """``InstanceSettingsResponse.key_mode`` (cl. 1)."""
    from applire.services import provider_keys

    live = and_(User.deleted_at.is_(None), User.disabled_at.is_(None))
    with unscoped("admin-metadata"):
        total = (await db.execute(select(func.count()).select_from(User).where(live))).scalar_one()
        granted = (
            await db.execute(
                select(func.count()).select_from(User).where(
                    live, User.instance_key_granted_at.is_not(None)
                )
            )
        ).scalar_one()
        with_key = (
            await db.execute(
                select(func.count())
                .select_from(User)
                .join(UserProviderKey, UserProviderKey.user_id == User.id)
                .where(live)
            )
        ).scalar_one()
        neither = (
            await db.execute(
                select(func.count())
                .select_from(User)
                .outerjoin(UserProviderKey, UserProviderKey.user_id == User.id)
                .where(live, User.instance_key_granted_at.is_(None), UserProviderKey.user_id.is_(None))
            )
        ).scalar_one()
    del total
    return {
        "mode": key_mode(),
        "secret_set": provider_keys.secret_configured(),
        "users_without_payer": int(neither),
        "users_with_grant": int(granted),
        "users_with_key": int(with_key),
    }


async def who_pays(db: AsyncSession, user: User) -> Literal["instance", "user", "none"]:
    """What would pay for this person's ``model_work`` now (the settings card)."""
    facts = await load_facts(db, user)
    try:
        return decide(facts, "model_work").payer  # type: ignore[return-value]
    except AdmissionRefused:
        return "none"


async def users_with_key(db: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Which of ``ids`` stored an own key (the admin users list: a flag, never the key)."""
    if not ids:
        return set()
    with unscoped("admin-metadata"):
        rows = await db.execute(select(UserProviderKey.user_id).where(UserProviderKey.user_id.in_(ids)))
    return {r[0] for r in rows.all()}


# --------------------------------------------------------------------------
# The subject (cl. 9; normalisation ADR-097 cl. 4)
# --------------------------------------------------------------------------

_GMAIL_DOMAINS = ("gmail.com", "googlemail.com")


def normalise_email(email: str) -> str:
    """Casefold; drop a ``+tag`` from the local part for every domain; for
    ``gmail.com``/``googlemail.com`` drop the dots and treat both as one."""
    text = (email or "").strip().casefold()
    local, sep, domain = text.rpartition("@")
    if not sep:
        return text
    local = local.split("+", 1)[0]
    if domain in _GMAIL_DOMAINS:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


def subject_hash(email: str) -> str:
    """``HMAC-SHA256(instance_secret, "applire/allowance-subject" ‖ normalised)``,
    hex. The key is the purpose-derived ``allowance-subject`` key."""
    from applire.auth.links import derive_key

    return hmac.new(
        derive_key("allowance-subject"), normalise_email(email).encode("utf-8"), hashlib.sha256
    ).hexdigest()


async def ensure_subject(db: AsyncSession, user: User) -> str:
    """The stored subject, stamped the first time the ledger needs it (cl. 9).
    The caller commits."""
    if user.allowance_subject:
        return user.allowance_subject
    user.allowance_subject = subject_hash(user.email)
    await db.flush()
    return user.allowance_subject


# --------------------------------------------------------------------------
# The ledger (cl. 8, 10)
# --------------------------------------------------------------------------


async def grant(
    db: AsyncSession,
    *,
    subject: str,
    action: str,
    amount: int,
    source: str,
    expires_at: datetime | None = None,
    ref: str | None = None,
    user_id: uuid.UUID | None = None,
) -> bool:
    """Write one ``kind='grant'`` row (the Cloud seam, cl. 10). Idempotent per
    ``ref``: a second call with the same ``(subject, action, ref)`` writes
    nothing and returns ``False``. The caller commits."""
    if action not in LEDGER_ACTIONS:
        raise ValueError(f"not a ledger action: {action!r}")
    if amount < 0:
        raise ValueError("a grant amount is never negative")
    row = AllowanceLedger(
        user_id=user_id,
        subject_hash=subject,
        kind="grant",
        action=action,
        ref=ref or f"grant:{uuid.uuid4()}",
        amount=amount,
        payer="instance",
        source=source,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
    except IntegrityError:
        return False
    return True


async def balance(db: AsyncSession, *, subject: str, action: str, now: datetime | None = None) -> int:
    """``sum(unexpired grant) − sum(debit) + sum(refund)`` for one action (cl. 8).
    The policy's static limit is added by the policy (phase 2), not here.

    Correct only for grants that never expire. Debits are not tied to the grant
    they spent, so last month's debits would eat this month's grant: a
    non-rolling allowance needs debit-to-grant allocation (phase 2, an ADR-096
    cl. 8 amendment). Phase 1 writes no debit."""
    when = now or datetime.now(timezone.utc)
    rows = await db.execute(
        select(AllowanceLedger.kind, func.coalesce(func.sum(AllowanceLedger.amount), 0))
        .where(
            AllowanceLedger.subject_hash == subject,
            AllowanceLedger.action == action,
            or_(
                AllowanceLedger.kind != "grant",
                AllowanceLedger.expires_at.is_(None),
                AllowanceLedger.expires_at > when,
            ),
        )
        .group_by(AllowanceLedger.kind)
    )
    sums = {kind: int(total) for kind, total in rows.all()}
    return sums.get("grant", 0) - sums.get("debit", 0) + sums.get("refund", 0)
