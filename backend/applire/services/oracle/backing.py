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


def ledger_denial_forms(keyword_ledger: list[dict[str, Any]] | None) -> tuple[tuple[str, str], ...]:
    """The ledger's stored scope decisions as ``(normalised form, recorded denial)``:
    every form (concept + surface forms) of a row the ledger holds as ``denied``.
    The record is the row's ``denied_by`` (the gap classifier bound it, ADR-100 M-2)
    or, for a row the record's own words declare, the row's concept. Never raises:
    a malformed ledger is no decisions."""
    if not keyword_ledger:
        return ()
    try:
        from applire.services.keyword_ledger import DENIED_BY_KEY, _norm

        out: list[tuple[str, str]] = []
        for row in keyword_ledger:
            if not isinstance(row, dict) or row.get("status") != "denied":
                continue
            concept = row.get("concept") if isinstance(row.get("concept"), str) else ""
            record = row.get(DENIED_BY_KEY) if isinstance(row.get(DENIED_BY_KEY), str) else ""
            for form in [concept, *(row.get("surface_forms") or [])]:
                key = _norm(form) if isinstance(form, str) else ""
                if key:
                    out.append((key, record.strip() or concept))
        return tuple(dict.fromkeys(out))
    except Exception:  # pragma: no cover — defensive: no ledger decisions
        logger.exception("oracle: ledger denials unreadable — reading declared denials only")
        return ()


def with_ledger_denials(index: VaultIndex, keyword_ledger: list[dict[str, Any]] | None) -> VaultIndex:
    """``index`` carrying the ledger's stored scope decisions (a new index; the
    given one is not changed). The identity when there is no ledger."""
    forms = ledger_denial_forms(keyword_ledger)
    if not forms:
        return index
    import dataclasses

    return dataclasses.replace(index, ledger_denials=forms)


def stored_denial_cover(texts: list[str], index: VaultIndex) -> list[str]:
    """The recorded denials whose STORED scope covers one of ``texts`` — the one
    scope reader of the Oracle, the delivery chip drop and the ledger-backing arm
    (Sorbet WP-Q5 (c), ADR-064 amended 2026-11-15: a denial is scoped to the level
    it denies, and that scope is decided once).

    Two arms, both the keyword ledger's own decisions, never a second matcher:

    - **declared**: the record's own words name the term
      (``stance.declared_denial_matches`` — the predicate the ledger floor's
      declared arm reads, ``keyword_ledger._declared_denial_record``);
    - **bound**: the job's ledger holds the term's row as ``denied``
      (``VaultIndex.ledger_denials``; the gap classifier's ``denied_by``
      Judgment, or the declared arm on that row).

    The compound-containment reading (a term that is only a PART of the record's
    words: "Projektcontrolling" inside "Weitere eigene Projektcontrolling-Projekte
    geleitet") is not a cover: the ledger withdrew it in G-1 (#821), and the
    Oracle reading it alone graded a claimable chip ``unbacked`` and let the
    delivery drop remove it (delivery 1b re-run). Longest first. Never raises."""
    denials = [d for d in (getattr(index, "denied_concepts", ()) or ()) if isinstance(d, str) and d.strip()]
    decided = tuple(getattr(index, "ledger_denials", ()) or ())
    if not denials and not decided:
        return []
    try:
        from applire.services.keyword_ledger import _norm
        from applire.services.profile.reconcile.stance import declared_denial_matches

        out: list[str] = []
        for t in texts:
            if not isinstance(t, str) or not t.strip():
                continue
            if denials:
                out.extend(declared_denial_matches(t, denials))
            key = _norm(t)
            out.extend(record for form, record in decided if form == key)
        return sorted(dict.fromkeys(out), key=len, reverse=True)
    except Exception:  # pragma: no cover — defensive: no cover (today's grading)
        logger.exception("oracle: stored denial cover unreadable")
        return []


def _denied(texts: list[str], index: VaultIndex) -> bool:
    return bool(stored_denial_cover(texts, index))


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
    index = with_ledger_denials(index, keyword_ledger)
    if _denied([name], index):
        return None
    hit = backing_for_skill(name, ledger_backings(keyword_ledger, profile, index))
    return hit.unit if hit is not None else None
