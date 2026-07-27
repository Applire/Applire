# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#306/#315 — the shared "load-bearing claim" concept.

Charter run #7, case 2 (German, ``operations_marcus_de``): the cover-letter
review loop exhausted all five retries on mostly self-contradicting reviewer
issues (#306), so the retention step fell back to substituting an EARLIER
round's draft — one that satisfied the structural ``retain_if``/``prefer_if``
predicates but was materially poorer in evidence, silently dropping the
case's designed Signature-Story OEE arc (``61 % -> 73 %``). Independently,
the CV chain's page-budget condense step (#315, ``services/cv_budget.py``)
dropped the run's budget figure (``6 Mio. €``) the same way — a cutting step
that knows the STRUCTURAL shape it must preserve, but nothing about which
content is load-bearing.

**Definition** (the two issues' shared vocabulary, so a fix for one names the
same thing the other reasons about): a **load-bearing claim** is a quantified
value (percentage, currency amount, or plain number — see
:mod:`applire.services.oracle.matchers.figures`) that appears in the evidence
text of a keyword-ledger entry with ``status == "direct"`` and
``claimable is True`` — i.e. a number the vault itself backs for THIS
candidate, not a number merely present somewhere in the source text. This
module is the retention-half's (letter/#306's) implementation of that
definition: it measures how many of a drafted document's load-bearing figures
survive into a given candidate draft, so a substitution/cutting step can
refuse to trade evidence away for a merely-cleaner shape.

The CV-side cutter (#315) is expected to consume the SAME
``load_bearing_universe_from_ledger`` / figure-canonicalisation this module
exports (or an equivalent keyed the same way — ``f"{kind}:{value}"`` from
:class:`applire.services.oracle.matchers.figures.Figure`) rather than invent
a second notion of "load-bearing" — reconcile at merge time if the two
diverge.

Deterministic only — no LLM call, and this module never MINTS a figure: it
only ever measures which of an already-known-grounded set survived into a
piece of text. Pair every retention comparison built on this module with the
existing MINTED guardrail (``services/letter_figure_guard.py`` and
``services/oracle/audit.py``) — this module protects RETENTION, not
grounding.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from applire.services.oracle.matchers.figures import extract_figures


def stringify_draft(draft: Any) -> str:
    """Flatten any JSON-able ``review_and_refine`` draft (dict/list/str,
    arbitrarily nested — every chain's draft shape: cover-letter, CV,
    job-analysis) to a single string of every leaf string value, joined by
    newlines. Chain-agnostic on purpose: a single figure-scan or count-check
    can run over ANY draft without a per-chain adapter.
    """
    parts: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                _walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                _walk(value)
        # numbers/bools/None carry no text to scan — ignored.

    _walk(draft)
    return "\n".join(parts)


def _figure_key(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def load_bearing_universe_from_ledger(ledger: list[dict[str, Any]] | None) -> frozenset[str]:
    """The set of load-bearing figures the vault backs: every quantified value
    found in the evidence text of a ``direct`` + ``claimable`` keyword-ledger
    entry. Canonical keys are ``f"{kind}:{value}"`` (see
    :class:`applire.services.oracle.matchers.figures.Figure`) — the SAME
    canonicalisation the Oracle's own figure matching uses, so this universe
    stays comparable with the rest of the grounding machinery.
    """
    universe: set[str] = set()
    for entry in ledger or []:
        if entry.get("status") != "direct" or not entry.get("claimable"):
            continue
        evidence = entry.get("evidence") or ""
        if not isinstance(evidence, str) or not evidence:
            continue
        for figure in extract_figures(evidence):
            universe.add(_figure_key(figure.kind, figure.value))
    return frozenset(universe)


def figures_present(text: str) -> frozenset[str]:
    """Every canonicalised figure found in ``text`` (percent/currency/number —
    years are excluded by :func:`extract_figures` upstream priority rules the
    same way the Oracle's own matcher excludes them: date spans are
    tenure-ambient, not load-bearing evidence)."""
    return frozenset(_figure_key(f.kind, f.value) for f in extract_figures(text))


def retained_load_bearing_figures(text: str, universe: frozenset[str]) -> frozenset[str]:
    """Which figures from the load-bearing ``universe`` are actually present in
    ``text`` — a chain-agnostic RETENTION measure, never a quality score and
    never a mint check (a figure present in ``text`` but absent from
    ``universe`` is simply not counted here; catching an invented figure is
    the job of the existing MINTED guardrails, not this function).
    """
    if not universe:
        return frozenset()
    return universe & figures_present(text)


def load_bearing_fn_from_ledger(
    ledger: list[dict[str, Any]] | None,
) -> Callable[[dict[str, Any]], frozenset[str]]:
    """Factory: build a ``review_and_refine(load_bearing_fn=...)`` closure over
    a fixed keyword ledger — callers pass the SAME ledger already routed to
    the reviewer prompt (e.g. ``services/cover_letter.py``'s
    ``coverage_reviewer_prompt_fn``), so "load-bearing" means exactly what the
    reviewer's own coverage check means; no second ledger snapshot.
    """
    universe = load_bearing_universe_from_ledger(ledger)

    def _load_bearing_fn(draft: dict[str, Any]) -> frozenset[str]:
        return retained_load_bearing_figures(stringify_draft(draft), universe)

    return _load_bearing_fn
