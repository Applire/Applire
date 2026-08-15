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

This module shipped SCAFFOLDING ONLY in #537: the typed registration surface and its
enforcement (a signal with no declared disposition is a visible error, never a silent
default), with nothing calling it — no pass had migrated, and the registry was wired
into no pipeline behaviour.

**2026-08-15 amendment (#540, ADR-076 clause 2):** clause 2 was amended so that an
unmeasurable FALLBACK_APPLY signal may migrate only when its fallback is "implemented,
logged, and proven to fire on EVERY early-settle path of the review loop — not just
literal retry-exhaustion." ``services/reviewer.py``'s ``review_and_refine()`` now calls
into this registry (see its ``signal_ids`` parameter and ``_apply_signal_fallbacks``)
so a registered FALLBACK_APPLY signal's ``fallback_fn`` fires at settle time on any
early-settle path where that signal's issue is still outstanding — the wiring itself,
not any particular pass's migration: **the registry still ships EMPTY in production**
(#537's own claim continues to hold; nothing has migrated yet), so this amendment is
behaviour-neutral for every existing caller until the first real signal registers.

This is also why :class:`SignalDispositionRecord` grew an ``issue_matches`` field
(required for FALLBACK_APPLY, see :func:`register_signal_disposition`): the loop needs
a way to decide "is THIS signal's issue among the ones still open when it settles",
and that answer has to be supplied by the migration, not invented here (see the
field's own docstring for the marker-convention rationale and its known failure mode).

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
    deterministic edit, callable once on loop settlement — and is REQUIRED for
    ``FALLBACK_APPLY`` (there is nothing to apply otherwise) and FORBIDDEN for
    ``SHIP_AND_REPORT`` (a ship-and-report signal never writes to the artifact, not even
    as a fallback — that would be a silent second write path around clause 2's own
    disposition). ``rationale`` is required non-empty prose: the declaration is the
    reviewable artefact (mirrors ADR-062 clause 6 — "the classification is declared at
    the call site... invisible in a diff and obvious in a docstring"), so a bare enum
    value with no reasoning defeats the point of requiring one.

    ``fallback_fn`` call signature is pinned: ``fallback_fn(draft: dict) -> dict``.
    It receives ONLY the settled draft (the same shape ``review_and_refine`` is about
    to return) and must return a replacement draft of the same shape — never ``None``,
    never a mutation-in-place assumption (the caller treats the return value as the
    new draft, full stop). Anything else a migration's fallback needs — the profile,
    a vault index, a compiled regex — it closes over at REGISTRATION time (the module
    docstring of ``letter_figure_guard.py`` is the reference shape: a closure built
    once per profile, not re-derived per call). ``services/reviewer.py`` calls it
    inside a ``try/except Exception`` — a raising ``fallback_fn`` is logged and the
    UN-fallbacked draft ships, exactly like every other failure mode this loop
    already treats as "degrade, never crash" (ADR-021).

    ``issue_matches`` (2026-08-15 amendment, #540) answers "does this text, from
    ``review_and_refine``'s ``last_issues``, belong to THIS signal's still-open
    complaint" — REQUIRED for ``FALLBACK_APPLY`` (there is otherwise no way to tell
    the fallback ever applies) and unconstrained for ``SHIP_AND_REPORT`` (never
    consulted — that disposition's whole point is that nothing here acts on its
    issue). It is called against reviewer-authored ``ReviewIssue.text`` strings, which
    is a KNOWN failure mode: an LLM reviewer can paraphrase away any marker a matcher
    looks for, silently defeating a naive ``"exact phrase" in text`` check. Two
    mitigations, both the migration's responsibility, not this module's:

    1. The deterministic layer that injects this signal's material into the reviewer
       prompt (mirrors ``letter_figure_guard.figure_ownership_reviewer_prompt_fn`` —
       ADR-076's "detections enter as issues with pre-selected material attached")
       should embed a STABLE marker (the ``signal_id`` itself is already unique and
       stable — reusing it as the marker needs no new vocabulary) and instruct the
       reviewer, explicitly, to reproduce that token verbatim in any issue text that
       flags it.
    2. ``issue_matches`` should therefore be written as a SUBSTRING/marker check
       against that stable token, never a semantic/fuzzy match — and the FAIL-SAFE
       direction is built in on purpose: if the reviewer drops the marker anyway, the
       matcher returns ``False`` for every issue, and the fallback simply does not
       fire. A missed fallback ships the same draft ``review_and_refine`` would have
       shipped anyway (no worse than today); a WRONGLY fired fallback would apply a
       deterministic edit to a draft that never needed it, which is the direction that
       actually costs something. When the marker convention alone isn't trustworthy
       enough for a given migration, the stronger alternative is to ignore issue text
       entirely and have ``issue_matches`` (or ``fallback_fn`` itself, since it also
       receives the draft) re-run the SAME deterministic detector the fallback is a
       last resort for, directly against the settled draft — a re-check has no
       paraphrase to lose.
    """

    signal_id: str
    disposition: ExhaustionDisposition
    rationale: str
    fallback_fn: Callable[..., Any] | None = None
    issue_matches: Callable[[str], bool] | None = None


_REGISTRY: dict[str, SignalDispositionRecord] = {}


def register_signal_disposition(
    signal_id: str,
    disposition: ExhaustionDisposition,
    rationale: str,
    fallback_fn: Callable[..., Any] | None = None,
    issue_matches: Callable[[str], bool] | None = None,
) -> SignalDispositionRecord:
    """Declare ONE signal's exhaustion disposition (ADR-076 clause 2's floor).

    Call this once, at the signal's migration site (the future issue that moves a
    pass's edit into the corrector round), not from this module or from any pipeline
    code today — #537 ships the registry empty by design; nothing has migrated yet.

    Raises ``ValueError`` for every shape clause 2 does not sanction:
      * an empty/whitespace-only rationale (the declaration must reason, not just tag);
      * ``FALLBACK_APPLY`` with no ``fallback_fn`` (nothing to apply);
      * ``FALLBACK_APPLY`` with no ``issue_matches`` (2026-08-15 amendment, #540 — no
        way to tell whether the fallback's signal is among the issues still open at
        settle time, see :class:`SignalDispositionRecord` for the matching contract);
      * ``SHIP_AND_REPORT`` WITH a ``fallback_fn`` (a ship-and-report signal must never
        carry a hidden write path — the whole point of that disposition is that the
        defect ships and is reported, not quietly patched). ``issue_matches`` is left
        unconstrained for ``SHIP_AND_REPORT`` — it is simply never consulted for that
        disposition.

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
    if disposition is ExhaustionDisposition.FALLBACK_APPLY and issue_matches is None:
        raise ValueError(
            f"signal_id={signal_id!r}: fallback-apply requires issue_matches — the "
            "2026-08-15 clause 2 amendment (#540) wires the fallback to fire only "
            "when THIS signal's issue is still outstanding at settle time, and there "
            "is no way to answer that without a matcher"
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
        issue_matches=issue_matches,
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
