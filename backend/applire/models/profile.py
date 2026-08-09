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

import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, JSON, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from applire.db.session import Base

logger = logging.getLogger(__name__)

# JSONB on PostgreSQL (binary, indexed); falls back to JSON on SQLite for unit tests.
_ProfileJSON = JSONB().with_variant(JSON(), "sqlite")
# VECTOR(1024) on PostgreSQL; TEXT (always NULL) on SQLite — see migration 0016.
_VECTOR_1024 = Vector(1024).with_variant(sa.Text(), "sqlite")


class MasterProfile(Base):
    __tablename__ = "master_profiles"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_json: Mapped[dict] = mapped_column(_ProfileJSON, nullable=False)
    # Embedding vector for job-profile similarity scoring (migration 0016).
    # Re-computed on every upsert; NULL until first pass; always NULL on SQLite.
    embedding: Mapped[list[float] | None] = mapped_column(_VECTOR_1024, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ── The write guard (ADR-063 clause 6, amended 2026-08-09) ────────────────────
#
# `master_profiles.profile_json` is the vault. ADR-063 makes `commit_ops` its
# single assigner; this guard is the control that makes the claim checkable
# instead of grep-maintained.
#
# WARN MODE (#480 PR 1): an unauthorised write logs and increments a counter, it
# never raises. #480 PR 8 routed the last three — the first-profile-creation
# sites, whose KEYWORD-ARGUMENT constructors would have made a strict guard
# refuse profile creation outright (both CV-import doors and the Mode-B guided
# stub). No production writer outside the three modules below now reaches
# `profile_json`. PR 9 flips this to strict and adds the gate test asserting the
# exception set below is exactly those three modules.
#
# NOTE for PR 9: ~160 TEST fixtures still construct `MasterProfile(profile_json=…)`
# directly across ~80 files. They are unaffected in warn mode; strict mode has to
# decide their disposition (wrap in `authorized_profile_write()`, as the
# committer's own fixtures already do, or a test-scoped escape) before flipping.
#
# Two authorisations, in order of preference:
#
#   1. the contextvar TOKEN, held by `commit_ops` across the assignment — the
#      real mechanism, and the one PR 9 keeps;
#   2. the named MODULE exceptions — `services/photo.py` (a `Binary` write under
#      GDPR Art. 9(2)(a)) and `services/profile/snapshots.py` (the undo restore).
#      `commit.py` is listed too, so the committer is authorised belt-and-braces
#      even if a future refactor moves the assignment out of the token block.
#
# The `set` event fires on plain assignment AND on keyword construction
# (`MasterProfile(profile_json=…)` goes through `setattr`) — the case that
# defeated the original clause 6 and made the writer count 19, not 16.

AUTHORIZED_PROFILE_WRITE_MODULES: frozenset[str] = frozenset(
    {
        "applire/services/profile/commit.py",
        "applire/services/profile/snapshots.py",
        "applire/services/photo.py",
    }
)

_PROFILE_WRITE_TOKEN: ContextVar[object | None] = ContextVar(
    "applire_profile_write_token", default=None
)

# Per-instance record of what the setter decided, read (and consumed) by the
# `before_flush` backstop so an authorised write is silent and an already-warned
# one is not counted twice.
_WRITE_DECISION_KEY = "applire_profile_write_authorized"

# Bounded walk: the caller sits a handful of frames above the setter (plus
# SQLAlchemy's own instrumentation, plus the declarative constructor on the
# keyword-construction shape).
_MAX_GUARD_FRAMES = 40

_unauthorized_profile_writes = 0


def unauthorized_profile_writes() -> int:
    """How many unauthorised `profile_json` writes this process has seen."""
    return _unauthorized_profile_writes


def reset_unauthorized_profile_writes() -> None:
    """Test hook — the counter is process-global."""
    global _unauthorized_profile_writes
    _unauthorized_profile_writes = 0


@contextmanager
def authorized_profile_write() -> Iterator[None]:
    """Hold the write token for the duration of the block.

    Set by `commit_ops` around its assignment. Re-entrant and contextvar-scoped,
    so a concurrent request without the token is unaffected.
    """
    token = _PROFILE_WRITE_TOKEN.set(object())
    try:
        yield
    finally:
        _PROFILE_WRITE_TOKEN.reset(token)


def _caller_is_authorized_module() -> bool:
    frame = sys._getframe(1)
    depth = 0
    while frame is not None and depth < _MAX_GUARD_FRAMES:
        filename = frame.f_code.co_filename.replace("\\", "/")
        if any(filename.endswith(m) for m in AUTHORIZED_PROFILE_WRITE_MODULES):
            return True
        frame = frame.f_back
        depth += 1
    return False


def _caller_location() -> str:
    frame = sys._getframe(1)
    depth = 0
    while frame is not None and depth < _MAX_GUARD_FRAMES:
        filename = frame.f_code.co_filename.replace("\\", "/")
        if "/sqlalchemy/" not in filename and "/applire/models/profile.py" not in filename:
            return f"{filename}:{frame.f_lineno}"
        frame = frame.f_back
        depth += 1
    return "<unknown>"


def _count_unauthorized(reason: str, where: str) -> None:
    global _unauthorized_profile_writes
    _unauthorized_profile_writes += 1
    logger.warning(
        "ADR-063 clause 6 — unrouted write to MasterProfile.profile_json (%s) at "
        "%s. The vault's single write path is services/profile/commit.py; this "
        "writer is scheduled into #480 PRs 2-8. Warn mode: the write was NOT "
        "blocked (strict lands in PR 9).",
        reason,
        where,
    )


@event.listens_for(MasterProfile.profile_json, "set", propagate=True)
def _guard_profile_json_set(target, value, oldvalue, initiator):  # noqa: ANN001
    authorized = (
        _PROFILE_WRITE_TOKEN.get() is not None or _caller_is_authorized_module()
    )
    try:
        sa.inspect(target).info[_WRITE_DECISION_KEY] = authorized
    except sa.exc.NoInspectionAvailable:  # pragma: no cover — defensive
        pass
    if not authorized:
        _count_unauthorized("attribute set", _caller_location())


@event.listens_for(Session, "before_flush")
def _guard_profile_json_flush(session, flush_context, instances):  # noqa: ANN001
    """Belt and braces: a dirty ``profile_json`` that never passed the setter.

    (The retention worker's ``update(...).values(...)`` touches ``deleted_at``
    only — it is not a ``profile_json`` bypass, and this listener stays quiet
    for it.)
    """
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, MasterProfile):
            continue
        state = sa.inspect(obj)
        attr = state.attrs.get("profile_json")
        if attr is None or not attr.history.has_changes():
            continue
        decision = state.info.pop(_WRITE_DECISION_KEY, None)
        if decision is None:
            # Never went through the setter — an ORM-level bypass.
            _count_unauthorized("dirty at flush, no setter", repr(obj))


class ProfileSnapshot(Base):
    """Pre-merge snapshot of a Master Profile (US168 / ADR-042).

    Captured unconditionally before every merge commit so an accidental bad merge
    is recoverable via ``undo_last_merge``. Keyed to the merge's ``EnrichmentRecord``
    so undo can detect later edits. Profile-derived PII: cascades on profile delete,
    so it is purged with the profile under existing GDPR erasure (ADR-040) — no new
    retention surface. Bounded per profile (``SNAPSHOT_MAX_PER_PROFILE``).
    """

    __tablename__ = "profile_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    profile_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("master_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The merge enrichment record this snapshot precedes (ADR-042). UUID-as-text:
    # EnrichmentRecords live inside profile_json, not their own table — no FK.
    enrichment_record_id: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    profile_json: Mapped[dict] = mapped_column(_ProfileJSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
