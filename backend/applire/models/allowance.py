# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Who pays for model work — the stored half of ADR-096 (migration ``0090``).

``user_provider_keys``
    One active provider key per person (cl. 2). Owned (ADR-092): read and written
    only for its owner, deleted by account erasure (ADR-091 cl. 25). The key is
    Fernet ciphertext under a key derived from ``PROVIDER_KEY_SECRET``, which is
    environment-only (cl. 3): a database dump alone decrypts nothing. There is
    no base-URL column and no ``ollama`` (S0-11): a user key only ever goes to
    its provider's fixed public endpoint.

``allowance_ledger``
    Grants, debits and refunds per *subject* (cl. 8/9). Not owned: the rows are
    instance accounting attributed to a person, and they outlive the account
    (``user_id`` is set NULL on erasure, ``subject_hash`` stays). The unique
    index on ``(subject_hash, kind, action, ref)`` makes every write idempotent.

``allowance_daily``
    Instance-paid tokens per day, per subject and (``subject_hash`` NULL) for the
    instance total (cl. 8). Written by the usage wrapper while an allowance
    policy is active (phase 2); with ``ALLOWANCE_POLICY=none`` nothing writes it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from applire.db.session import Base

#: The fixed-endpoint families a user key may belong to (S0-11). No ``ollama``,
#: no ``mock``, no base URL. Mirrored by the CHECK constraint below.
USER_KEY_FAMILIES: tuple[str, ...] = ("mistral", "openrouter", "requesty", "anthropic", "openai")

#: Ledger row kinds and the closed action set (cl. 7). ``model_work`` and
#: ``instance_tokens`` are never ledger rows; they are listed for the CHECK.
LEDGER_KINDS: tuple[str, ...] = ("grant", "debit", "refund")
LEDGER_ACTIONS: tuple[str, ...] = ("application", "profile_import", "job_analysis")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserProviderKey(Base):
    __tablename__ = "user_provider_keys"
    __owned__ = True  # ADR-092 cl. 3 — the statement guard's owned set
    __table_args__ = (
        CheckConstraint(
            "family IN ('mistral', 'openrouter', 'requesty', 'anthropic', 'openai')",
            name="ck_user_provider_keys_family",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", name="fk_user_provider_keys_user_id_users", ondelete="CASCADE"),
        primary_key=True,
    )
    family: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Fernet token; never returned, never logged (ADR-096 cl. 3).
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    def __repr__(self) -> str:  # never the ciphertext either
        return f"<UserProviderKey user_id={self.user_id} family={self.family}>"


class AllowanceLedger(Base):
    __tablename__ = "allowance_ledger"
    __table_args__ = (
        CheckConstraint("kind IN ('grant', 'debit', 'refund')", name="ck_allowance_ledger_kind"),
        CheckConstraint(
            "action IN ('application', 'profile_import', 'job_analysis')",
            name="ck_allowance_ledger_action",
        ),
        CheckConstraint("payer IN ('user', 'instance', 'system')", name="ck_allowance_ledger_payer"),
        CheckConstraint("amount >= 0", name="ck_allowance_ledger_amount"),
        Index(
            "uq_allowance_ledger_subject_kind_action_ref",
            "subject_hash",
            "kind",
            "action",
            "ref",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", name="fk_allowance_ledger_user_id_users", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: HMAC of the normalised email (cl. 9) — never the email itself.
    subject_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Idempotency key: the job analysis id, the import job id, or a grant's
    #: own reference (e.g. ``monthly:2026-12``).
    ref: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payer: Mapped[str] = mapped_column(String(16), nullable=False, default="instance")
    #: Grants only: where the allowance came from (``admin``, ``subscription``,
    #: ``purchase`` … — the spending order lives in the policy, cl. 10).
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: Grants only: after this instant the grant no longer counts (cl. 8).
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class AllowanceDaily(Base):
    __tablename__ = "allowance_daily"
    __table_args__ = (
        # NULL subject = the instance total; one row per day for it too.
        Index(
            "uq_allowance_daily_day_subject",
            "day",
            "subject_hash",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    #: NULL = the instance total for the day.
    subject_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
