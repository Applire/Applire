# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US259 (ADR-058 "machinery within the existing engine" exception, PO-directed
2026-07-24) — deterministic sufficiency + question-ordering helpers for the
interview termination gate.

Run-4 ground truth: the interview stopped at the count-based hard ceiling
(``INTERVIEW_HARD_CEILING_TARGETED`` = 12) one question before eliciting a
JD-required capability's quantification — the fact both blind panel reviewers
named as invite-flipping. This module supplies two DETERMINISTIC (no LLM)
pieces of machinery that fix that shape without touching question GENERATION
content:

  1. ``concept_is_required`` / ``cluster_needs_priority`` — drive question
     ORDERING (``interview_graph.gap_detector``): a cluster holding a
     JD-hard-requirement concept that is keyword-only (never evidenced in the
     vault) or unquantified (evidenced, no figure — reuses US265's
     ``detect_unquantified_concepts``) is promoted to the front of its C/B
     bucket, so a budget cut always lands on a lower-value question first.
     Absorbs #257 option a.

  2. ``is_interview_sufficient`` — names the deterministic termination
     predicate ("every critical gap from here on is addressed, denied, or
     triaged as a true gap") so "termination = sufficiency OR budget OR
     user-done" is an explicit, independently testable seam rather than an
     implicit fall-through of loop arithmetic in services/session.py.

Boundary note (ADR-058): this module does NOT force a second question on an
already-asked cluster to chase a missing number. US265 deliberately asks the
quantification nudge ONCE per cluster and never re-asks it (see
tests/unit/test_interview_quant_elicitation.py::
test_followup_path_never_carries_the_quantification_instruction — a pinned,
PO-approved invariant). This module respects that boundary: it improves
sufficiency by asking the highest-value question EARLIER (ordering), not by
adding new question-generation turns or new prompt content.
"""
from __future__ import annotations

from applire.services.interview_quant import detect_unquantified_concepts


def _norm(s: str) -> str:
    return (s or "").strip().casefold()


def _concept_matches_ledger_key(concept: str, ledger_concept: str) -> bool:
    """Casefold exact-or-substring match, mirroring keyword_ledger.py's own
    ``_matches`` — cluster gap labels (clustering LLM) and ledger concepts
    (classification LLM) are independently generated text, so a
    byte-identical key can't be assumed."""
    a, b = _norm(concept), _norm(ledger_concept)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _ledger_entry_for(concept: str, keyword_ledger: list[dict] | None) -> dict | None:
    for entry in keyword_ledger or []:
        if _concept_matches_ledger_key(concept, entry.get("concept", "")):
            return entry
    return None


def concept_is_required(concept: str, keyword_ledger: list[dict] | None) -> bool:
    """True if ``concept`` matches a keyword_ledger entry sourced "required"
    — a JD hard requirement. ``sources`` is Python-authoritative on the
    ledger (services/keyword_ledger.py — never LLM-guessed), so this is a
    deterministic read, not a heuristic."""
    entry = _ledger_entry_for(concept, keyword_ledger)
    return bool(entry and "required" in (entry.get("sources") or []))


def cluster_needs_priority(
    cluster: dict,
    profile: dict,
    keyword_ledger: list[dict] | None,
) -> bool:
    """True if ``cluster`` holds a JD-required concept that is either
    keyword-only (ledger status == "gap" — no evidence at all in the vault)
    or unquantified (evidenced, no figure — US265's
    ``detect_unquantified_concepts``). Drives question ORDERING: these
    clusters ask FIRST within their C/B bucket, so if the operator's budget
    runs out, it cuts a lower-value question, never this one.

    Scoped to JD-required concepts only (mirrors the sufficiency criterion's
    own required-only scope) — promoting every nice-to-have keyword-only gap
    would defeat prioritisation by flattening it back to breadth-first.
    """
    concepts = [c for c in (cluster.get("gaps") or []) if c]
    if not concepts:
        return False
    required = [c for c in concepts if concept_is_required(c, keyword_ledger)]
    if not required:
        return False
    unquantified = set(detect_unquantified_concepts(cluster, profile))
    for concept in required:
        if concept in unquantified:
            return True
        entry = _ledger_entry_for(concept, keyword_ledger)
        if entry is not None and entry.get("status") == "gap":
            return True
    return False


def is_interview_sufficient(
    critical_gaps: list[str],
    from_index: int,
    skipped_gaps: set,
) -> bool:
    """True once every gap from ``from_index`` onward is resolved — addressed,
    denied, or triaged as a true gap (all three land in ``skipped_gaps`` /
    an advanced index by the time this is checked). Names the deterministic
    termination check that services/session.py's own advance loop already
    computes via gap-index/skip bookkeeping (mirrors ``_count_remaining(...)
    <= 0``), so "termination = sufficiency OR budget OR user-done" (issue
    #259) is an explicit, independently testable seam.
    """
    return all(g in skipped_gaps for g in critical_gaps[from_index:])
