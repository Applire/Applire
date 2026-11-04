# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADR-100 M-3 — the Oracle's one reader of the keyword ledger's verified backing.

ADR-100 cl. 1: one question, one instrument, one persisted verdict; downstream
stages read it and never re-judge. "Does the candidate have requirement R?" is
answered by the gap classifier with a verbatim vault quote, checked by the
ledger floor (``keyword_ledger.assert_claimable_backed`` →
``citation.quote_in_vault``) and persisted on the row as
``backing = {quote, unit_path, instrument}``. Before M-3 the Oracle asked the
same question again with the literal ``ground_skill_claim`` and, for a sentence,
with token coverage — instruments that cannot see a German bullet behind an
English claim (11-04 E2E: all five interview concepts ``noCloseEvidence``).

What this module decides is a **Fact** (ADR-100 cl. 1): which rows carry a quote
that STILL resolves, verbatim, in today's claimable vault. It never decides
whether a quote supports a sentence — that stays the equivalence judge's
(founder ruling R-4, ``audit.py`` seam ``backing``).

Every row the literal miss used to refuse implicitly is refused here by
predicate (memory: a new YES arm drops the old arm's implicit NOs):

- unclaimable rows (``gap``/``denied``) and rows without a quote (legacy rows,
  ADR-100 cl. 9 — their absence only means "no candidate", today's verdict);
- positioning-only rows (an ADJACENT ``partial``: its quote shows the
  substitute capability, never the posting's term — ADR-048 am. 2026-07-27);
- scope rows (ADR-069: the concept is a synthesised label with the JD's figure);
- aim-only rows and any quote inside a ``not_yet_reached`` line (D-12);
- a row whose concept, forms or quote a recorded denial covers (cl. 7: a later
  denial outranks a quote);
- a quote that no longer resolves in the CLAIMABLE vault: an edited or deleted
  bullet, an ``unconfirmed`` or ``denied`` entry (``_claimable_index`` holds
  neither).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from applire.services.oracle.matchers.vault import EvidenceUnit, VaultIndex

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LedgerBacking:
    """One claimable ledger row with a quote that resolves now."""

    concept: str
    forms: tuple[str, ...]
    quote: str
    unit: EvidenceUnit
    keyword_only: bool


def _denied(texts: list[str], index: VaultIndex) -> bool:
    denials = list(getattr(index, "denied_concepts", ()) or ())
    if not denials:
        return False
    from applire.services.profile.reconcile.stance import denials_covering

    release = getattr(index, "denial_release_text", "") or ""
    return any(denials_covering(t, [], denials, release) for t in texts if t)


def ledger_backings(
    keyword_ledger: list[dict[str, Any]] | None,
    profile: Any,
    index: VaultIndex,
) -> list[LedgerBacking]:
    """The job's claimable rows whose ``backing.quote`` passes ``quote_in_vault``
    against *index* (the Oracle's claimable index) now, with the unit it resolves
    in. Order follows the ledger. Never raises: a malformed ledger is no rows."""
    if not keyword_ledger:
        return []
    try:
        from applire.services.citation import quote_in_vault
        from applire.services.keyword_ledger import (
            _norm,
            is_aim_only,
            is_jd_keyword_only,
            is_positioning_only,
            is_scope_entry,
        )
        from applire.services.prompt_view import aim_and_other_lines

        raw = profile.model_dump(mode="json") if hasattr(profile, "model_dump") else profile
        aims = aim_and_other_lines(raw)[0] if isinstance(raw, dict) else []
        by_path = {u.path: u for u in index.units}
        out: list[LedgerBacking] = []
        for row in keyword_ledger:
            if not isinstance(row, dict) or row.get("claimable") is not True:
                continue
            if row.get("status") not in ("direct", "partial"):
                continue
            if is_positioning_only(row) or is_scope_entry(row) or is_aim_only(row):
                continue
            backing = row.get("backing")
            quote = backing.get("quote") if isinstance(backing, dict) else None
            if not isinstance(quote, str) or not quote.strip():
                continue
            concept = row.get("concept") if isinstance(row.get("concept"), str) else ""
            forms: list[str] = []
            seen: set[str] = set()
            for f in [concept, *(row.get("surface_forms") or [])]:
                key = _norm(f) if isinstance(f, str) else ""
                if key and key not in seen:
                    seen.add(key)
                    forms.append(f)
            if not forms:
                continue
            if _denied([*forms, quote], index):
                continue
            path = quote_in_vault(quote, index.units, exclude=aims)
            unit = by_path.get(path) if path else None
            if unit is None:
                continue
            out.append(
                LedgerBacking(
                    concept=concept or forms[0],
                    forms=tuple(forms),
                    quote=quote,
                    unit=unit,
                    keyword_only=is_jd_keyword_only(row),
                )
            )
        return out
    except Exception:  # pragma: no cover — defensive: no rows is today's behaviour
        logger.exception("oracle: ledger backing unreadable — grading against the vault alone")
        return []


def backing_for_skill(name: str, backings: list[LedgerBacking] | None) -> LedgerBacking | None:
    """The backed row a skill chip NAMES: its text equals one of the row's forms
    under the ledger's own normaliser. JD-keyword-only rows are excluded — the
    same rows the existing ``ledger_forms`` arm and the writer's skills gate
    refuse as chips (ADR-066 cl. 2)."""
    if not backings or not isinstance(name, str) or not name.strip():
        return None
    from applire.services.keyword_ledger import _norm

    key = _norm(name)
    for b in backings:
        if b.keyword_only:
            continue
        if any(_norm(f) == key for f in b.forms):
            return b
    return None


def backings_named_in(text: str, backings: list[LedgerBacking] | None) -> list[LedgerBacking]:
    """The backed rows a sentence NAMES: one of the row's forms is present in the
    claim as a whole token (``ats_audit.surface_present_whole_token``). A
    document-side literal SELECTION — it only chooses which quotes the judge sees
    first; a miss leaves today's verdict, so it vetoes nothing (ADR-100 cl. 1,
    Hint tier at most)."""
    if not backings or not isinstance(text, str) or not text.strip():
        return []
    from applire.services.ats_audit import _norm, surface_present_whole_token

    text_norm = _norm(text)
    return [b for b in backings if any(surface_present_whole_token(f, text_norm) for f in b.forms)]


def skill_backed_by_ledger(
    name: str,
    keyword_ledger: list[dict[str, Any]] | None,
    profile: Any,
    index: VaultIndex,
) -> EvidenceUnit | None:
    """The unit a backed ledger row puts behind skill *name*, or ``None`` — the
    chip arm of :func:`applire.services.oracle.audit._verify_claim_unchecked`
    for callers holding a raw ledger (``review_actions.skill_backed_now``)."""
    if _denied([name], index):
        return None
    hit = backing_for_skill(name, ledger_backings(keyword_ledger, profile, index))
    return hit.unit if hit is not None else None
