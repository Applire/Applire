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

**The payer truth table** (cl. 5). Policy ``none`` (default): rows 1, 3, 4, 7.
Policy ``allowance`` (phase 2, ADR-096 amended 2026-11-23): rows 2, 3 (metered,
K-2c), 5 and 6 — the instance pays only while it can count (else
``allowance_unavailable``) and the day's instance total is below
``ALLOWANCE_TOKENS_PER_DAY`` (else ``instance_daily_limit``); the monthly
``application`` / ``profile_import`` limits are counted at the debited doors
(:func:`debit_for_door`), allocated earliest-expiry first (MD-9):

====  ==============  =====  =====  ======================  ==========================
row   LLM_KEY_MODE    grant  key    payer                   limits
====  ==============  =====  =====  ======================  ==========================
1     instance        any    any    instance                none (policy ``none``)
2     instance        any    any    instance                monthly + daily (``allowance``)
3     user            yes    any    instance                none / monthly + daily
4     user            no     yes    user                    none (policy ``none``)
5     user            no     yes    user                    imports (``allowance``)
6     user            no     no     instance                monthly + daily (``allowance``)
7     user            no     no     refused                 ``provider_key_required``
====  ==============  =====  =====  ======================  ==========================

**There is never a silent fallback to the instance key** (S0-10): a key that no
longer decrypts reads as unset (row 7), an unset ``PROVIDER_KEY_SECRET`` refuses
with ``provider_key_unreadable``, and neither ever becomes an instance call.

The ledger (cl. 8–10) is here too: :func:`grant` is the one function a plan
provider (``allowance_seam``) writes allowance through; :func:`balance` reads
it. With policy ``none`` nothing debits.
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
from datetime import date as date_type
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
    # ADR-096 phase 2 (amended 2026-11-23): the allowance policy's refusals.
    "allowance_exhausted": (
        "This month's allowance for this action is used up. It renews on the 1st of "
        "next month. Applications you already started can still be finished."
    ),
    "plan_required": "Choose a plan in Settings before you start your first application.",
    "instance_daily_limit": (
        "This instance has reached its daily limit for AI work. This affects everyone. "
        "It continues tomorrow; your work is saved."
    ),
    "allowance_unavailable": (
        "This instance cannot count usage right now, so no AI work can start. "
        "Try again later; your work is saved."
    ),
    # ADR-096 amended 2026-11-18 (WP-L): checked before the payer table, only
    # while the instance writes content logs (LOG_LEVEL=DEBUG).
    "log_consent_required": (
        "This test instance keeps content logs, and you have not agreed to them "
        "(or you withdrew). Agree in Settings under 'Logs during the test phase' "
        "to start AI work again. Reading, downloading and deleting still work."
    ),
}


#: A refusal whose wording depends on the metered action (adv/plans F7): the
#: import's "used up" must not talk about applications.
_ACTION_MESSAGES: dict[tuple[str, object], str] = {
    ("allowance_exhausted", "profile_import"): (
        "This month's CV imports are used up. They renew on the 1st of next month. "
        "You can still edit your profile by hand."
    ),
}


#: Refusals that concern the whole instance, not the person's own allowance.
_INSTANCE_REFUSALS = frozenset({"instance_daily_limit", "allowance_unavailable"})


class AdmissionRefused(HTTPException):
    """A door refused the action (cl. 11). An ``HTTPException`` (the
    ``OwnedNotFound`` precedent), so REST renders it without a handler:
    ``409 {"detail": {"error_code": code, "message": message}}``; MCP answers
    ``mcp.errors.admission_refused`` with the code first in the message."""

    def __init__(self, code: str, *, status: int = 409, **extra: object) -> None:
        self.code = code
        self.message = _ACTION_MESSAGES.get((code, extra.get("action")), REFUSAL_MESSAGES.get(code, code))
        self.extra = dict(extra)
        if code in _INSTANCE_REFUSALS and "purchasing_available" not in self.extra:
            # Which wording the dialog shows ("Das Testsystem …" / "Diese
            # Instanz …", A-4) travels with the refusal: it must be right before
            # the person's allowance has been read, and while that read fails.
            from applire.services.allowance_seam import purchasing_available

            self.extra["purchasing_available"] = purchasing_available()
        detail: dict[str, object] = {"error_code": code, "message": self.message}
        detail.update(self.extra)  # e.g. ``action`` / ``renews_on`` (phase 2)
        super().__init__(status_code=status, detail=detail)

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
    #: ADR-096 amended 2026-11-18: the person may be processed (and logged) —
    #: always True while the instance writes no content logs.
    log_consent_ok: bool = True
    #: ADR-096 phase 2: under ``ALLOWANCE_POLICY=allowance`` the door also loads
    #: whether the instance's daily token total is reached, and whether the usage
    #: sink can count at all (cl. 8 fail closed). Both neutral under ``none``.
    day_cap_reached: bool = False
    counting_ok: bool = True


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


def _refuse_without_consent_facts() -> None:
    """At DEBUG an owner without facts has an unknown consent: refused, never
    run (ADR-096 amended 2026-11-18). Above DEBUG this is a no-op."""
    from applire.content_log import content_logging_on

    if content_logging_on():
        logger.warning(
            "model work for an owner without payer facts while content logging is on "
            "— refused until the person's consent is known (ADR-096 amended)",
            stack_info=True,
        )
        raise AdmissionRefused("log_consent_required")


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


@contextlib.contextmanager
def tooling_owner(user_id: uuid.UUID) -> Iterator[None]:
    """Operator tooling only (bench, model matrix, replays — never a request):
    act for a seeded synthetic person whose payer facts say "instance pays,
    consented to content logs", so a run at ``LOG_LEVEL=DEBUG`` writes the LLM
    call log under that person's id. No production code path calls this
    (pinned by a test, like ``unscoped("tooling")``)."""
    from applire.ownership import owner_context

    with owner_context(user_id):
        token = _facts.set(PayerFacts(owner_id=user_id, mode="instance", log_consent_ok=True))
        try:
            yield
        finally:
            _facts.reset(token)


def reset_context_for_tests() -> None:
    """Test seam: drop any decision/facts the running context carries."""
    _decision.set(None)
    _facts.set(None)
    from applire.services import log_consent_live

    log_consent_live.reset_for_tests()


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
    from applire.services.log_consent import consent_ok

    mode = decision_mode()
    consent = consent_ok(user)
    limits: dict[str, bool] = {}
    if policy() == "allowance":
        limits = {
            "day_cap_reached": await day_cap_reached(db),
            "counting_ok": usage_counting_ok(),
        }
    if mode in ("instance", "unknown"):
        facts = PayerFacts(owner_id=user.id, mode=mode, log_consent_ok=consent, **limits)
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
            log_consent_ok=consent,
            **limits,
        )
    _facts.set(facts)
    from applire.services import log_consent_live

    log_consent_live.note_door(user.id, consent)
    return facts


def _instance_pays(facts: PayerFacts, action: str, ref: str | None) -> PayerDecision:
    """Rows 1/2/3/6: the instance pays — under ``allowance`` only while it can
    count (cl. 8 fail closed) and the day's instance total is not reached
    (K-2b). The per-person monthly limits are checked at the debited doors
    (:func:`debit_for_door`), not here."""
    if policy() == "allowance":
        if not facts.counting_ok:
            raise AdmissionRefused("allowance_unavailable", status=503)
        if facts.day_cap_reached:
            raise AdmissionRefused("instance_daily_limit")
    return PayerDecision("instance", action, facts.owner_id, ref=ref)


def decide(facts: PayerFacts, action: str, ref: str | None = None) -> PayerDecision:
    """The truth table (cl. 5). Pure apart from decrypting."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    if not facts.log_consent_ok:  # step 0 (WP-L): before any payer row
        raise AdmissionRefused("log_consent_required")
    if facts.mode == "unknown":  # the panel could not be read: never instance
        _refuse_unknown_mode()
    if facts.mode == "instance":  # rows 1 and 2
        return _instance_pays(facts, action, ref)
    if facts.granted:  # row 3 (metered under ``allowance``, K-2c)
        return _instance_pays(facts, action, ref)
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
    if policy() == "allowance":  # row 6: the instance, within its limits
        return _instance_pays(facts, action, ref)
    raise AdmissionRefused("provider_key_required")  # row 7


def admit_from_facts(action: str, ref: str | None = None) -> PayerDecision:
    """Admit with the facts the auth door loaded for the current owner."""
    owner = current_owner()
    facts = _facts.get()
    if owner is None or owner.user_id is None or facts is None or facts.owner_id != owner.user_id:
        if decision_mode() == "unknown":
            _refuse_unknown_mode()
        _refuse_without_consent_facts()
        if key_mode() == "instance" and owner is not None and owner.user_id is not None:
            decision = PayerDecision("instance", action, owner.user_id, ref=ref)
            _decision.set(decision)
            return decision
        raise AdmissionMissing()
    decision = decide(facts, action, ref)
    _decision.set(decision)
    return decision


def admitted_or_none(action: str = "model_work") -> PayerDecision | None:
    """:func:`admit_from_facts` for model work a write does not depend on
    (ruling C-1): the decision when admitted, ``None`` when refused."""
    try:
        return admit_from_facts(action)
    except AdmissionRefused:
        return None


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
    _refuse_without_consent_facts()
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
    from dataclasses import replace

    facts = await load_facts(db, user)
    try:
        # Who WOULD pay: the logging consent is its own notice, not a payer.
        return decide(  # type: ignore[return-value]
            replace(facts, log_consent_ok=True, day_cap_reached=False, counting_ok=True),
            "model_work",
        ).payer
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




# --------------------------------------------------------------------------
# Phase 2 (ADR-096 amended 2026-11-23): the ``allowance`` policy
# --------------------------------------------------------------------------

#: The month a grant belongs to is the calendar month in this zone (approved
#: mock A2: "Calendar month, Europe/Berlin").
ALLOWANCE_TZ = "Europe/Berlin"
#: Grant sources (cl. 10). Spending order is by expiry, not by source (MD-9).
SOURCE_MONTHLY = "monthly"
SOURCE_SUBSCRIPTION = "subscription"
SOURCE_PURCHASE = "purchase"
#: The debited actions the policy meters at their door (cl. 7 as ruled, K-2b).
METERED_ACTIONS: tuple[str, ...] = ("application", "profile_import")


def policy() -> Literal["none", "allowance"]:
    """The effective ``ALLOWANCE_POLICY`` (unknown values read as ``none``)."""
    from applire.config import settings

    return "allowance" if str(settings.allowance_policy or "").strip().lower() == "allowance" else "none"


def month_period(now: datetime | None = None) -> tuple[str, date_type, datetime]:
    """``(YYYY-MM, first day, first instant of the next month as UTC)`` of the
    ``Europe/Berlin`` calendar month containing ``now``."""
    from datetime import date
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(ALLOWANCE_TZ)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    start = date(local.year, local.month, 1)
    nxt = date(local.year + (local.month == 12), local.month % 12 + 1, 1)
    expires = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz).astimezone(timezone.utc)
    return f"{start.year:04d}-{start.month:02d}", start, expires


def renews_on(now: datetime | None = None) -> date_type:
    """The first day of the next ``Europe/Berlin`` month (what the UI says)."""
    _, start, _ = month_period(now)
    from datetime import date

    return date(start.year + (start.month == 12), start.month % 12 + 1, 1)


def _today_local(now: datetime | None = None) -> date_type:
    from zoneinfo import ZoneInfo

    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(ALLOWANCE_TZ)).date()


def usage_counting_ok() -> bool:
    """cl. 8 fail closed: can the usage sink count instance-paid tokens now?"""
    from applire.providers.llm import usage as usage_mod
    from applire.services.ops.config import usage_tracking_enabled

    return usage_tracking_enabled() and usage_mod.sink_available()


async def day_total(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Instance-paid tokens today (the instance-total rows, subject NULL)."""
    from applire.models.allowance import AllowanceDaily

    total = (
        await db.execute(
            select(func.coalesce(func.sum(AllowanceDaily.tokens), 0)).where(
                AllowanceDaily.day == _today_local(now), AllowanceDaily.subject_hash.is_(None)
            )
        )
    ).scalar_one()
    return int(total)


async def day_cap_reached(db: AsyncSession, *, now: datetime | None = None) -> bool:
    from applire.config import settings

    from applire.providers.llm.usage import pending_instance_tokens

    cap = int(settings.allowance_tokens_per_day or 0)
    if cap <= 0:  # refused at start under ``allowance``; never "no cap"
        return True
    # Queued usage rows are not in the table yet; they count all the same.
    return await day_total(db, now=now) + pending_instance_tokens() >= cap


async def add_daily_tokens(db: AsyncSession, *, payer: str, tokens: int, now: datetime | None = None) -> None:
    """The usage sink's increment (cl. 8): add an instance- or system-paid
    call's tokens to today's instance total. A no-op under ``none`` and for a
    user-paid call. The caller commits (same session as its ``llm_usage`` row)."""
    if policy() != "allowance" or payer == "user" or tokens <= 0:
        return
    from sqlalchemy import update

    from applire.models.allowance import AllowanceDaily

    day = _today_local(now)
    result = await db.execute(
        update(AllowanceDaily)
        .where(AllowanceDaily.day == day, AllowanceDaily.subject_hash.is_(None))
        .values(tokens=AllowanceDaily.tokens + int(tokens))
    )
    if result.rowcount:
        return
    try:
        async with db.begin_nested():
            db.add(AllowanceDaily(day=day, subject_hash=None, tokens=int(tokens)))
            await db.flush()
    except IntegrityError:  # a parallel first write of the day (PostgreSQL)
        await db.execute(
            update(AllowanceDaily)
            .where(AllowanceDaily.day == day, AllowanceDaily.subject_hash.is_(None))
            .values(tokens=AllowanceDaily.tokens + int(tokens))
        )


async def _lock_subject(db: AsyncSession, subject: str) -> None:
    """cl. 8: serialise count-and-debit per subject. PostgreSQL takes a
    transaction-scoped advisory lock; SQLite serialises writers anyway."""
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        from sqlalchemy import text

        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:s, 0))"), {"s": subject}
        )


async def lock_subject(db: AsyncSession, subject: str) -> None:
    """The per-subject lock for a plan provider (adv/plans F4): a plan change
    writes the subject's plan grants, so it is serialised with every other plan
    change and every debit of the subject. Held until the caller's commit."""
    await _lock_subject(db, subject)


def _remaining_columns():
    """Per grant row: ``amount − Σ allocated debits + Σ allocated refunds``."""
    from sqlalchemy import case
    from sqlalchemy.orm import aliased

    alloc = aliased(AllowanceLedger)
    spent = (
        select(
            func.coalesce(
                func.sum(
                    case((alloc.kind == "debit", alloc.amount), else_=-alloc.amount)
                ),
                0,
            )
        )
        .where(alloc.grant_id == AllowanceLedger.id, alloc.kind.in_(("debit", "refund")))
        .correlate(AllowanceLedger)
        .scalar_subquery()
    )
    return spent


async def _grants_with_remaining(
    db: AsyncSession, *, subject: str, action: str, now: datetime
) -> list[tuple[AllowanceLedger, int, int]]:
    """Unexpired grants for one action as ``(row, used, remaining)``, in
    spending order: earliest expiry first, never-expiring last, then oldest
    (MD-9; 2026-10-18: subscription before purchased)."""
    from sqlalchemy import case

    spent = _remaining_columns()
    rows = await db.execute(
        select(AllowanceLedger, spent)
        .where(
            AllowanceLedger.subject_hash == subject,
            AllowanceLedger.kind == "grant",
            AllowanceLedger.action == action,
            or_(AllowanceLedger.expires_at.is_(None), AllowanceLedger.expires_at > now),
        )
        .order_by(
            case((AllowanceLedger.expires_at.is_(None), 1), else_=0),
            AllowanceLedger.expires_at,
            AllowanceLedger.created_at,
        )
    )
    out = []
    for row, used in rows.all():
        used = int(used or 0)
        out.append((row, used, int(row.amount) - used))
    return out


async def balance(db: AsyncSession, *, subject: str, action: str, now: datetime | None = None) -> int:
    """What is left for one action: the sum, over unexpired grants, of what is
    left of each (cl. 8 as amended by MD-9). Last month's debits stay with last
    month's grant, so a non-rolling month starts full."""
    when = now or datetime.now(timezone.utc)
    return sum(
        max(rem, 0) for _, _, rem in await _grants_with_remaining(db, subject=subject, action=action, now=when)
    )


async def _ensure_month_grants(
    db: AsyncSession, *, user: User, subject: str, action: str, now: datetime
) -> None:
    """cl. 3 of the amendment: this month's grant exists before counting.
    Imports: always Core's. Applications: the plan provider's, else Core's."""
    from applire.config import settings
    from applire.services.allowance_seam import plan_provider

    period, start, expires = month_period(now)
    if action == "profile_import":
        await grant(
            db, subject=subject, action="profile_import",
            amount=max(int(settings.allowance_imports_per_month or 0), 0),
            source=SOURCE_MONTHLY, expires_at=expires, ref=f"monthly:{period}", user_id=user.id,
        )
        return
    provider = plan_provider()
    if provider is None:
        await grant(
            db, subject=subject, action="application",
            amount=max(int(settings.allowance_applications_per_month or 0), 0),
            source=SOURCE_MONTHLY, expires_at=expires, ref=f"monthly:{period}", user_id=user.id,
        )
        return
    has_plan = await provider.ensure_month_grant(
        db, user=user, subject=subject, period=period, period_start=start, expires_at=expires
    )
    if not has_plan:
        raise AdmissionRefused("plan_required")


async def debit_for_door(
    db: AsyncSession, user_id: uuid.UUID | None, action: str, *, ref: str, now: datetime | None = None
) -> None:
    """The debited door (cl. 7 as ruled): count one ``application`` (per job) or
    ``profile_import`` (per import) under ``ALLOWANCE_POLICY=allowance``.

    A no-op under ``none``. An application paid by the person's own key (row 5)
    is not metered here (ADR-097's own-key limits are the demo's). A ``ref``
    that already carries a debit is admitted without a new one — a regeneration,
    also in a later month. Otherwise, under the subject lock: this month's
    grants are made sure of, the earliest-expiring grant with something left is
    debited (MD-9), or the door refuses ``allowance_exhausted`` /
    ``plan_required``. The caller commits, together with the row it creates.
    """
    if policy() != "allowance" or action not in METERED_ACTIONS:
        return
    if user_id is None:
        owner = current_owner()
        user_id = owner.user_id if owner is not None else None
    if user_id is None:
        raise OwnerContextMissing("a debited door needs the acting user (ADR-096 cl. 7)")
    decision = _decision.get()
    if (
        action == "application"
        and decision is not None
        and decision.owner_id == user_id
        and decision.payer == "user"
    ):
        return
    when = now or datetime.now(timezone.utc)
    user = await db.get(User, user_id)
    if user is None:
        raise OwnerContextMissing("the acting user does not exist")
    subject = await ensure_subject(db, user)
    await _lock_subject(db, subject)
    existing = (
        await db.execute(
            select(AllowanceLedger.id).where(
                AllowanceLedger.subject_hash == subject,
                AllowanceLedger.kind == "debit",
                AllowanceLedger.action == action,
                AllowanceLedger.ref == ref,
            )
        )
    ).first()
    if existing is not None:
        return
    await _ensure_month_grants(db, user=user, subject=subject, action=action, now=when)
    for row, _used, remaining in await _grants_with_remaining(
        db, subject=subject, action=action, now=when
    ):
        if remaining > 0:
            db.add(
                AllowanceLedger(
                    user_id=user.id, subject_hash=subject, kind="debit", action=action,
                    ref=ref, amount=1, payer=(decision.payer if decision else "instance"),
                    grant_id=row.id, created_at=when,
                )
            )
            await db.flush()
            return
    raise AdmissionRefused(
        "allowance_exhausted", action=action, renews_on=renews_on(when).isoformat()
    )


async def allowance_status(db: AsyncSession, user: User, *, now: datetime | None = None) -> dict:
    """``GET /api/me/allowance`` (cl. 11): what is left, from the ledger only.

    Read-only: a month whose grant is not written yet (it is written lazily at
    the first debited door) is shown with the amount it will get.
    """
    from applire.config import settings
    from applire.services.allowance_seam import plan_provider

    when = now or datetime.now(timezone.utc)
    pol = policy()
    provider = plan_provider()
    out: dict = {
        "policy": pol,
        # K-3e: the tester dialogs render only on this (a provider is registered
        # AND the policy meters). Never true on a Community install.
        "purchasing_available": pol == "allowance" and provider is not None,
        "renews_on": renews_on(when).isoformat(),
        "applications": None,
        "imports": None,
        "plan": None,
        "pending": None,
        "catalog": None,
    }
    if pol != "allowance":
        return out
    period, start, _ = month_period(when)
    subject = user.allowance_subject or subject_hash(user.email)
    plan_block: dict = {}
    if provider is not None:
        plan_block = await provider.status(db, user=user, period_start=start)
        out["plan"] = plan_block.get("plan")
        out["pending"] = plan_block.get("pending")
        out["catalog"] = await provider.catalog(db)

    async def _counts(action: str, default_amount: int | None) -> dict:
        rows = await _grants_with_remaining(db, subject=subject, action=action, now=when)
        monthly = [(r, u, rem) for r, u, rem in rows if r.expires_at is not None]
        purchased = sum(max(rem, 0) for r, _, rem in rows if r.expires_at is None)
        if monthly:
            limit = sum(int(r.amount) for r, _, _ in monthly)
            used = sum(u for _, u, _ in monthly)
        else:
            limit, used = (default_amount or 0), 0
        return {
            "limit": limit,
            "used": used,
            "remaining": max(limit - used, 0),
            "purchased_remaining": purchased,
        }

    if provider is None:
        app_default: int | None = int(settings.allowance_applications_per_month or 0)
    else:
        plan = out["plan"]
        app_default = int(plan["applications"]) if plan else 0
    out["applications"] = await _counts("application", app_default)
    out["applications"]["has_plan"] = provider is None or out["plan"] is not None
    out["imports"] = await _counts("profile_import", int(settings.allowance_imports_per_month or 0))
    del period
    return out


# --------------------------------------------------------------------------
# cl. 9: the subject is stamped at account creation and on an email change
# --------------------------------------------------------------------------

from sqlalchemy import event as _sa_event  # noqa: E402
from sqlalchemy import inspect as _sa_inspect  # noqa: E402


def _try_stamp(target: User) -> None:
    try:
        target.allowance_subject = subject_hash(target.email)
    except Exception:  # the instance secret is not loaded (admin CLI): lazy later
        logger.debug("allowance subject not stamped at write time; stamped on first use")


@_sa_event.listens_for(User, "before_insert")
def _stamp_subject_on_insert(_mapper, _connection, target: User) -> None:
    if not target.allowance_subject and target.email:
        _try_stamp(target)


@_sa_event.listens_for(User, "before_update")
def _stamp_subject_on_email_change(_mapper, _connection, target: User) -> None:
    state = _sa_inspect(target)
    if not state.attrs.email.history.has_changes():
        return
    if state.attrs.allowance_subject.history.has_changes():
        return  # the same flush sets it itself (the erasure tombstone clears it)
    if target.email:
        _try_stamp(target)
