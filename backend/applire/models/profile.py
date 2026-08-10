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
from types import FrameType

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, JSON, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from applire.db.session import Base

try:  # greenlet is a hard dependency of SQLAlchemy's asyncio extension.
    import greenlet
except ImportError:  # pragma: no cover — defensive; see `_guard_frames`
    greenlet = None  # type: ignore[assignment]

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


# ── The write guard (ADR-063 clause 6 — STRICT since 2026-08-10) ──────────────
#
# `master_profiles.profile_json` is the vault. ADR-063 makes `commit_ops` its
# single assigner; this guard is the control that makes the claim checkable
# instead of grep-maintained.
#
# STRICT (#480 PR 9, 2026-08-10). An unauthorised write RAISES
# `UnauthorizedProfileWriteError`. There is no warn mode left, no configuration
# flag and no environment escape hatch: a mode you can turn off is a mode that
# gets turned off. Strict became landable once #480 PRs 2-8 routed every
# production writer — including the three first-profile-creation sites, whose
# KEYWORD-ARGUMENT constructors would otherwise have made this guard refuse
# profile creation outright.
#
# Two authorisations, in order of preference:
#
#   1. the contextvar TOKEN, held by `commit_ops` across the assignment — the
#      real mechanism;
#   2. the named MODULE exceptions — `services/photo.py` (a `Binary` write under
#      GDPR Art. 9(2)(a)) and `services/profile/snapshots.py` (the undo restore).
#      `commit.py` is listed too, so the committer is authorised belt-and-braces
#      even if a future refactor moves the assignment out of the token block.
#
# The exception set is a gate, not a convenience: `test_profile_write_guard.py`
# asserts it is exactly those three modules, so the day a twelfth writer grants
# itself an exception the suite says so.
#
# Three mechanisms, because SQLAlchemy offers a write three different ways in
# and no single event sees all three:
#
#   * the `set` event fires on plain assignment AND on keyword construction
#     (`MasterProfile(profile_json=…)` goes through `setattr`) — the case that
#     defeated the original clause 6 and made the writer count 19, not 16. It
#     raises before the value reaches the instance, so a refused write leaves no
#     trace;
#   * `before_flush` raises on a dirty `profile_json` that never passed the
#     setter — an ORM/instrumentation-level bypass, the realistic one being an
#     in-place mutation of the JSON dict plus a hand-rolled `flag_modified`.
#     Raising inside the flush aborts the transaction (PO ruling Q1(a),
#     2026-08-10), so the write cannot reach the database; the session recovers
#     with an ordinary `rollback()`, which the guard's tests pin;
#   * `do_orm_execute` raises on a bulk `update(MasterProfile)` OR
#     `insert(MasterProfile)` whose assigned columns include `profile_json`.
#     PR 9 measured the UPDATE shape against SQLAlchemy 2.0.36 and found it
#     reaches NEITHER of the other two: it is emitted straight from
#     `Session.execute`, never enters the unit of work, and leaves the instance
#     clean, so the setter never fires and `before_flush` has nothing to see.
#     #480 §5 claimed `before_flush` covered it; the code was right and the
#     design was wrong. PO ruling 1 (2026-08-10) closed the gap with this third
#     listener. INSERT was added after an adversarial pass REPRODUCED the same
#     bypass one verb over — the listener gated on `is_update` alone, and
#     `insert(MasterProfile).values(profile_json=…)` persisted unauthorised. A
#     vault conjured out of nothing is exactly as unrouted as a vault
#     overwritten. It raises before the statement is emitted, so — like the
#     setter — a refused write leaves nothing behind.
#
# Gating INSERT is safe only because unit-of-work persistence does NOT come
# through `do_orm_execute`: `db.add()` + flush emits its INSERT from the flush
# machinery, so the fixture factory and `create_profile_record` are untouched.
# That is load-bearing, not incidental, and a spy test asserts it directly.
#
# The one production caller of the bulk shape is the retention worker's
# `update(MasterProfile)…values(deleted_at=…)`, which touches `deleted_at` only
# and is therefore silent (ADR-063 amendment 5). That silence is not left to
# inspection: a per-site sentinel test calls `_tombstone_inactive_profiles` and
# fails if the listener ever speaks up for it.
#
# What the third mechanism still does NOT see, stated rather than implied —
# each measured, not assumed:
#
#   * raw `text("UPDATE master_profiles SET profile_json = …")`, and any
#     statement emitted on a bare Engine/Connection instead of a Session. Both
#     are outside every ORM event;
#   * the legacy `Session.bulk_insert_mappings` / `bulk_update_mappings`. Both
#     reproduce the bypass today: they skip the ORM event layer entirely. Zero
#     production callers;
#   * `delete(MasterProfile)` — row lifecycle, not content. GDPR erasure
#     (`routers/profile.py:855`) deletes profiles on purpose, so a content
#     guard that blocked it would be a bug. Deliberately out of scope.
#
# The control against all of these is the same one: the codebase has no such
# caller, which arc42 §5.3.19a's write-surface matrix asserts.
#
# NOT on that list, because it was measured and turned out to be covered:
# `session.merge()` on a `MasterProfile`. The merge's state copy goes through
# the instrumented setter, so mechanism 1 refuses it ("attribute set") even when
# the source object was itself built under the token — verified 2026-08-10
# against the adversarial pass, which had only shown that CONSTRUCTING the
# source is refused and never reached the state-copy path.
#
# Tests construct fixture profiles through the same public door
# (`tests/support/profile_factory.py` wraps `authorized_profile_write()`); there
# is deliberately no test-only bypass and no fourth exception module.

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

# Per-instance record that the setter authorised this write, consumed by the
# `before_flush` backstop. One authorisation covers one flush: the key is popped
# when the flush reads it, so a later bypass on the same instance cannot ride in
# on an earlier legitimate write.
_WRITE_DECISION_KEY = "applire_profile_write_authorized"

# Bounded walk: the caller sits a handful of frames above the setter (plus
# SQLAlchemy's own instrumentation, plus the declarative constructor on the
# keyword-construction shape).
_MAX_GUARD_FRAMES = 40


class UnauthorizedProfileWriteError(RuntimeError):
    """A write to `master_profiles.profile_json` from outside the write path.

    ADR-063 clause 6. Raised by the attribute `set` event (before the value
    reaches the instance) and by the `before_flush` backstop (aborting the
    transaction, PO ruling Q1(a) 2026-08-10). Not catchable-and-ignorable by
    design: there is no authorised way to write the vault other than holding the
    `authorized_profile_write()` token or being one of
    `AUTHORIZED_PROFILE_WRITE_MODULES`.
    """

    def __init__(self, reason: str, where: str) -> None:
        self.reason = reason
        self.where = where
        super().__init__(
            f"ADR-063 clause 6 — unauthorised write to MasterProfile.profile_json "
            f"({reason}) at {where}. The vault's single assigner is "
            f"applire/services/profile/commit.py: express the change as ops and "
            f"call commit_ops(), which owns the invariant set (trail, "
            f"completeness, denial floor). Holding authorized_profile_write() "
            f"around a raw assignment bypasses those invariants and is reserved "
            f"for the committer itself."
        )


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


def _guard_frames(frame: FrameType | None) -> Iterator[FrameType]:
    """Walk outward from `frame`, continuing onto the greenlet suspended below.

    The greenlet hop is not decoration. SQLAlchemy runs the sync half of an
    async session inside its own greenlet, so a listener that fires there — the
    `do_orm_execute` mechanism does — stands on a stack exactly two
    `orm/session.py` frames deep. The code that actually issued the statement is
    suspended on the parent greenlet. Without this continuation the module
    exception could never match on the async path and every refusal would report
    its location as `<unknown>`, which is the same failure mode the `<string>`
    skip was added to prevent.

    On a synchronous stack `greenlet.getcurrent().parent` is `None` and this
    degrades to exactly the plain `f_back` walk it replaces.
    """
    depth = 0
    current = greenlet.getcurrent() if greenlet is not None else None
    while depth < _MAX_GUARD_FRAMES:
        while frame is not None and depth < _MAX_GUARD_FRAMES:
            yield frame
            frame = frame.f_back
            depth += 1
        if current is None or current.parent is None:
            return
        current = current.parent
        frame = current.gr_frame


def _caller_is_authorized_module() -> bool:
    for frame in _guard_frames(sys._getframe(1)):
        filename = frame.f_code.co_filename.replace("\\", "/")
        if any(filename.endswith(m) for m in AUTHORIZED_PROFILE_WRITE_MODULES):
            return True
    return False


def _caller_location() -> str:
    """The first frame that is neither the guard nor SQLAlchemy's plumbing.

    `<string>` is skipped too: the declarative `__init__` SQLAlchemy compiles
    for each mapped class carries that filename, and on the keyword-construction
    shape it is the frame directly above the setter — reporting it would name
    every refused `MasterProfile(profile_json=…)` as `<string>:4`.
    """
    for frame in _guard_frames(sys._getframe(1)):
        filename = frame.f_code.co_filename.replace("\\", "/")
        if (
            "/sqlalchemy/" not in filename
            and "/applire/models/profile.py" not in filename
            and not filename.startswith("<")
        ):
            return f"{filename}:{frame.f_lineno}"
    return "<unknown>"


def _refuse(reason: str, where: str) -> None:
    """Log the refusal, then raise it. Logging first because the traceback the
    caller sees may be swallowed by an except-and-continue somewhere above."""
    logger.error(
        "ADR-063 clause 6 — REFUSED an unauthorised write to "
        "MasterProfile.profile_json (%s) at %s.",
        reason,
        where,
    )
    raise UnauthorizedProfileWriteError(reason, where)


@event.listens_for(MasterProfile.profile_json, "set", propagate=True)
def _guard_profile_json_set(target, value, oldvalue, initiator):  # noqa: ANN001
    if _PROFILE_WRITE_TOKEN.get() is None and not _caller_is_authorized_module():
        # Raised from inside the `set` event, so the value never reaches the
        # instance dict — a refused write leaves no partial state behind.
        _refuse("attribute set", _caller_location())
    try:
        sa.inspect(target).info[_WRITE_DECISION_KEY] = True
    except sa.exc.NoInspectionAvailable:  # pragma: no cover — defensive
        pass


@event.listens_for(Session, "before_flush")
def _guard_profile_json_flush(session, flush_context, instances):  # noqa: ANN001
    """Belt and braces: a dirty ``profile_json`` that never passed the setter.

    Raising here aborts the flush and therefore the transaction (PO ruling
    Q1(a), 2026-08-10) — the unauthorised write physically cannot reach the
    database. The session is poisoned in the ordinary SQLAlchemy sense and is
    recovered by ``rollback()``.

    (The retention worker's ``update(...).values(...)`` touches ``deleted_at``
    only — it is not a ``profile_json`` bypass, and this listener stays quiet
    for it. The ORM bulk-UPDATE shape is NOT visible here at all — see the
    KNOWN GAP note in the guard's header comment.)
    """
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, MasterProfile):
            continue
        state = sa.inspect(obj)
        attr = state.attrs.get("profile_json")
        if attr is None or not attr.history.has_changes():
            continue
        if state.info.pop(_WRITE_DECISION_KEY, None) is not True:
            # Never went through the setter — an ORM-level bypass.
            _refuse("dirty at flush, no setter", repr(obj))


def _bulk_write_columns(statement, parameters) -> set[str] | None:  # noqa: ANN001
    """The column names an INSERT or UPDATE assigns, or `None` if unreadable.

    Two shapes carry them, identically for both verbs.
    `update(...).values(profile_json=…)` / `insert(...).values(profile_json=…)`
    put them in `_values`, keyed by Column; the executemany form
    `session.execute(update(MasterProfile), [{...}])` leaves `_values` empty and
    passes them as the parameter dicts instead.

    `_values` is private API, and depending on it is a deliberate, pinned
    choice: there is no public accessor for a statement's SET clause, and the
    alternative — compiling the statement and reading the SQL back — is worse.
    Returning `None` makes the caller fail CLOSED, so a SQLAlchemy release that
    renames it turns the retention worker's sentinel test red rather than
    silently re-opening the gap. `.ordered_values()` lands here too: it stores
    pairs rather than a mapping, so it is refused as unreadable even when the
    columns it names are harmless.
    """
    values = getattr(statement, "_values", None)
    if values:
        return {getattr(c, "key", None) or getattr(c, "name", "") for c in values}
    if isinstance(parameters, dict):
        return set(parameters)
    if isinstance(parameters, (list, tuple)) and parameters:
        if all(isinstance(row, dict) for row in parameters):
            return {key for row in parameters for key in row}
    return None


@event.listens_for(Session, "do_orm_execute")
def _guard_profile_json_bulk_write(orm_execute_state):  # noqa: ANN001
    """The third mechanism: an INSERT or UPDATE that assigns the vault in bulk.

    Fires before the statement is emitted, so a refused bulk write never reaches
    the database — the same "no partial state" property the setter has.

    **INSERT is here because an adversarial pass proved it had to be.** The
    listener originally gated on `is_update` alone, and
    `insert(MasterProfile).values(profile_json=…)` reached the database
    unauthorised. A vault conjured out of nothing is exactly as unrouted as a
    vault overwritten — `commit_ops` owns creation too, since PR 8.

    Scoped narrowly on purpose: only INSERT/UPDATE, only against
    `master_profiles` (``profile_snapshots`` carries a `profile_json` column too
    and is NOT the vault), and only when `profile_json` is among the assigned
    columns — which is what keeps the retention worker's `values(deleted_at=…)`
    silent.

    Gating INSERT is safe ONLY because unit-of-work persistence does not come
    through here: `db.add()` + flush emits its INSERT from the flush machinery,
    not `Session.execute`, so this never fires for the fixture factory or for
    `create_profile_record`. That is load-bearing rather than incidental, so
    `test_the_unit_of_work_insert_never_reaches_the_listener` asserts it
    directly with a spy.
    """
    if orm_execute_state.is_update:
        verb = "UPDATE"
    elif orm_execute_state.is_insert:
        verb = "INSERT"
    else:
        return
    statement = orm_execute_state.statement
    table = getattr(statement, "table", None)
    if getattr(table, "name", None) != MasterProfile.__tablename__:
        return
    columns = _bulk_write_columns(statement, orm_execute_state.parameters)
    if columns is not None and "profile_json" not in columns:
        return
    if _PROFILE_WRITE_TOKEN.get() is None and not _caller_is_authorized_module():
        reason = f"ORM bulk {verb}"
        if columns is None:
            reason += " with unreadable SET columns"
        _refuse(reason, _caller_location())


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
