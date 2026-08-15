# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#537 (ADR-076 clause 2, "the floor (fail-safe)") — the exhaustion-disposition
registry.

ADR-076 clause 2 requires every migrated SIGNAL to declare, AT MIGRATION TIME, what
happens when the review loop exhausts its retries with that signal's issue still
outstanding:

* **fallback-apply** — on loop exhaustion the ORIGINAL deterministic edit runs once,
  logged, as the bounded sanctioned exception (the old pass's mechanism survives as a
  last resort, never as the normal path);
* **ship-and-report** — the defect ships, and is named in the reports at the send seat
  (ADR-076 clause 7).

This module is SCAFFOLDING ONLY, per #537's brief: it holds the typed registration
surface and its enforcement (a signal with no declared disposition is a visible error,
never a silent default), and nothing calls it yet — no pass migrates in this issue, and
this registry is wired into no pipeline behaviour. The first caller is whichever future
ADR-076 SIGNAL-migration issue (#541-#545 per the epic, #536) is cleared by #537's
compliance-measurement gate to actually move a pass's edit into the corrector round.

Why an in-code registry and not a config file or a doc table: a signal id that nobody
registered must fail LOUDLY the first time anything asks for its disposition — a doc
table can go stale silently (nothing reads it), a dict with a ``.get(id, DEFAULT)``
silently manufactures a disposition nobody decided on. :func:`get_signal_disposition`
raises :class:`UndeclaredSignalDispositionError` instead, so a migration that forgot
clause 2 breaks at the first call site that needs the answer, not in a review months
later.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class ExhaustionDisposition(str, Enum):
    """The two, and only two, sanctioned answers to "what happens when the loop
    exhausts with this signal's issue still open" (ADR-076 clause 2's floor)."""

    FALLBACK_APPLY = "fallback_apply"
    SHIP_AND_REPORT = "ship_and_report"


class UndeclaredSignalDispositionError(LookupError):
    """Raised when a signal id is looked up but never registered a disposition.

    A missing registration is a DEFECT IN THE MIGRATION, not a state the system should
    silently tolerate — ADR-076 clause 2 makes the declaration mandatory "at migration
    time", so an unregistered signal reaching this lookup means clause 2 was skipped."""


@dataclass(frozen=True)
class SignalDispositionRecord:
    """One SIGNAL's declared exhaustion disposition.

    ``fallback_fn`` is the bounded sanctioned exception itself — the original
    deterministic edit, callable once on loop exhaustion — and is REQUIRED for
    ``FALLBACK_APPLY`` (there is nothing to apply otherwise) and FORBIDDEN for
    ``SHIP_AND_REPORT`` (a ship-and-report signal never writes to the artifact, not even
    as a fallback — that would be a silent second write path around clause 2's own
    disposition). ``rationale`` is required non-empty prose: the declaration is the
    reviewable artefact (mirrors ADR-062 clause 6 — "the classification is declared at
    the call site... invisible in a diff and obvious in a docstring"), so a bare enum
    value with no reasoning defeats the point of requiring one.
    """

    signal_id: str
    disposition: ExhaustionDisposition
    rationale: str
    fallback_fn: Callable[..., Any] | None = None


_REGISTRY: dict[str, SignalDispositionRecord] = {}


def register_signal_disposition(
    signal_id: str,
    disposition: ExhaustionDisposition,
    rationale: str,
    fallback_fn: Callable[..., Any] | None = None,
) -> SignalDispositionRecord:
    """Declare ONE signal's exhaustion disposition (ADR-076 clause 2's floor).

    Call this once, at the signal's migration site (the future issue that moves a
    pass's edit into the corrector round), not from this module or from any pipeline
    code today — #537 ships the registry empty by design; nothing has migrated yet.

    Raises ``ValueError`` for every shape clause 2 does not sanction:
      * an empty/whitespace-only rationale (the declaration must reason, not just tag);
      * ``FALLBACK_APPLY`` with no ``fallback_fn`` (nothing to apply);
      * ``SHIP_AND_REPORT`` WITH a ``fallback_fn`` (a ship-and-report signal must never
        carry a hidden write path — the whole point of that disposition is that the
        defect ships and is reported, not quietly patched).

    Re-registering the SAME ``signal_id`` overwrites the prior record — deliberately: a
    migration that changes its own disposition mid-development should not have to know
    about an ``unregister`` call, and the record itself carries no version history this
    module needs to reconcile (that belongs to the ADR/epic issue, not to this
    scaffolding).
    """
    if not rationale or not rationale.strip():
        raise ValueError(
            f"signal_id={signal_id!r}: a disposition needs a non-empty rationale — "
            "ADR-076 clause 2's declaration is the reviewable artefact, not the enum "
            "value alone"
        )
    if disposition is ExhaustionDisposition.FALLBACK_APPLY and fallback_fn is None:
        raise ValueError(
            f"signal_id={signal_id!r}: fallback-apply requires a fallback_fn — the "
            "bounded sanctioned exception ADR-076 clause 2 names has to be something "
            "callable, not merely declared"
        )
    if disposition is ExhaustionDisposition.SHIP_AND_REPORT and fallback_fn is not None:
        raise ValueError(
            f"signal_id={signal_id!r}: ship-and-report must not carry a fallback_fn — "
            "a ship-and-report signal never writes to the artifact, not even as a "
            "fallback"
        )
    record = SignalDispositionRecord(
        signal_id=signal_id,
        disposition=disposition,
        rationale=rationale,
        fallback_fn=fallback_fn,
    )
    _REGISTRY[signal_id] = record
    return record


def get_signal_disposition(signal_id: str) -> SignalDispositionRecord:
    """Look up a signal's declared disposition, or fail LOUDLY if it never declared one.

    This is the enforcement half of the registry: a signal that reaches this call
    without having registered is a visible :class:`UndeclaredSignalDispositionError`,
    never a silent default toward either disposition."""
    try:
        return _REGISTRY[signal_id]
    except KeyError:
        raise UndeclaredSignalDispositionError(
            f"signal {signal_id!r} has no registered exhaustion disposition. Every "
            "ADR-076 SIGNAL must declare fallback-apply or ship-and-report via "
            "register_signal_disposition() at migration time (ADR-076 clause 2, the "
            "floor) before this lookup can answer for it."
        ) from None


def registered_signal_ids() -> frozenset[str]:
    """Every signal id currently holding a declared disposition — empty until the
    first SIGNAL migration registers one (#537 ships no registrations)."""
    return frozenset(_REGISTRY)


def _reset_registry_for_tests() -> None:
    """Test-only: clear every registration. The registry is process-global by design
    (a signal registers itself once, at import/migration time, like a plugin) — tests
    that register a fixture signal must call this in teardown so they cannot leak a
    registration into an unrelated test's lookup."""
    _REGISTRY.clear()
