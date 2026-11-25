# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The plan seam — ADR-096 amended 2026-11-23 (phase 2, ruling K-3e).

Core keeps admission, the ledger and ``allowance.grant()``. A **plan provider**
(the managed service's subscriptions and purchases) plugs in here, in the shape
of the auth ABC (ADR-008): Core names no provider and imports nothing of one.

* :class:`PlanProvider` — what a provider answers.
* :func:`register_plan_provider` — the one registration point. A provider
  package calls it when it is imported; the factory runs lazily on first use,
  so registering never imports the database layer (no cycle with
  ``applire.config``, which is where the edition gate imports packages).
* :func:`plan_provider` — the registered provider, or ``None``. ``None`` is the
  Community default: ``purchasing_available`` is false, the plan and purchase
  routes answer 402, and the tester dialogs never render.

This module must stay import-light (``abc`` and ``typing`` at module level only).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import APIRouter
    from sqlalchemy.ext.asyncio import AsyncSession

    from applire.models.user import User


class PlanError(Exception):
    """A refused plan or purchase request; ``code`` is the contract's
    ``error_code``, ``status`` the HTTP status."""

    def __init__(self, code: str, status: int = 409, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.status = status
        self.message = message or code


class PlanProvider(ABC):
    """Subscriptions and purchases for the ``allowance`` policy.

    Every method that writes allowance does so through ``allowance.grant()``
    (ADR-096 cl. 10): a provider never writes debits and never decides admission.
    The caller commits.
    """

    #: Short name for logs and the status payload.
    name: str = "plans"

    @abstractmethod
    async def ensure_month_grant(
        self,
        db: "AsyncSession",
        *,
        user: "User",
        subject: str,
        period: str,
        period_start: date,
        expires_at: Any,
    ) -> bool:
        """Make sure this period's plan grant for ``application`` exists
        (idempotent, through ``allowance.grant``). Return ``False`` when the
        person has no plan for the period (admission answers ``plan_required``)."""

    @abstractmethod
    async def status(self, db: "AsyncSession", *, user: "User", period_start: date) -> dict:
        """The person's plan block for ``GET /api/me/allowance``: ``plan``
        (``{id, applications, price_cents, currency}`` or ``None``) and
        ``pending`` (``{plan_id, applications, from}`` or ``None``)."""

    @abstractmethod
    async def catalog(self, db: "AsyncSession") -> dict:
        """``{currency, plans: [{id, label, applications, price_cents}],
        packs: [{id, applications, price_cents}], simulated: bool}``."""

    @abstractmethod
    async def change_plan(
        self, db: "AsyncSession", *, user: "User", subject: str, plan_id: str
    ) -> dict:
        """Choose, upgrade (at once) or downgrade (from the next period), or
        take a pending downgrade back. Returns ``{change: chosen|upgraded|
        downgrade_scheduled|downgrade_cancelled|unchanged}``."""

    @abstractmethod
    async def purchase(
        self,
        db: "AsyncSession",
        *,
        user: "User",
        subject: str,
        pack_id: str,
        idempotency_key: str,
    ) -> dict:
        """Record a purchase and grant its applications (never expiring).
        Idempotent per ``idempotency_key``. Returns ``{purchase_id,
        applications, price_cents, amount_due_cents}``."""

    async def startup(self) -> None:
        """Run once in the web app's start, after Core's migrations (e.g. the
        provider's own schema)."""
        return None

    def admin_router(self) -> "APIRouter | None":
        """The provider's operator API; Core mounts it under
        ``/api/admin/billing`` behind ``require_admin_session``."""
        return None


_factory: Callable[[], PlanProvider] | None = None
_provider: PlanProvider | None = None


def register_plan_provider(factory: Callable[[], PlanProvider]) -> None:
    """Register the provider factory (one provider per process; a second
    registration replaces the first, which only tests do)."""
    global _factory, _provider
    _factory = factory
    _provider = None


def plan_provider() -> PlanProvider | None:
    """The registered provider, built on first use, or ``None`` (Community)."""
    global _provider
    if _provider is None and _factory is not None:
        _provider = _factory()
    return _provider


def purchasing_available() -> bool:
    """The capability field of ``GET /api/me/allowance`` (K-3e): true only when
    a plan provider is registered. Never true on a Community install."""
    return _factory is not None


def reset_for_tests() -> None:
    global _factory, _provider
    _factory = None
    _provider = None
