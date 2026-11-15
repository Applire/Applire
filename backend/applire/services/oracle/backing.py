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
import re
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


def ledger_claimable_forms(keyword_ledger: list[dict[str, Any]] | None) -> frozenset[str]:
    """MD-Q13 N2: every form (concept + surface forms) of a CLAIMABLE ledger row,
    folded by ``ats_audit._norm``. Never raises."""
    if not keyword_ledger:
        return frozenset()
    try:
        from applire.services.ats_audit import _norm as _fold

        return frozenset(
            k
            for row in keyword_ledger
            if isinstance(row, dict) and row.get("claimable") is True
            for form in [row.get("concept"), *(row.get("surface_forms") or [])]
            if isinstance(form, str) and (k := _fold(form))
        )
    except Exception:  # pragma: no cover — defensive: no claimable forms
        logger.exception("oracle: ledger claimable forms unreadable")
        return frozenset()


def with_ledger_denials(index: VaultIndex, keyword_ledger: list[dict[str, Any]] | None) -> VaultIndex:
    """``index`` carrying the ledger's stored scope decisions (a new index; the
    given one is not changed): its ``denied`` rows' forms, and (MD-Q13 N2) its
    claimable rows' forms. The identity when there is no ledger."""
    forms = ledger_denial_forms(keyword_ledger)
    claimable = ledger_claimable_forms(keyword_ledger)
    if not forms and not claimable:
        return index
    import dataclasses

    return dataclasses.replace(index, ledger_denials=forms, ledger_claimable=claimable)


# ── MD-Q11 F6: the shorter name and the denial sentence ─────────────────────
#
# The one containment the stored-scope reader keeps, on WHOLE tokens of the
# record (a hyphen-part is never split off: "Projektcontrolling" is not a token
# of "Projektcontrolling-Projekte geleitet"), in two closed shapes. Closed lists,
# the FS-7 precedent (ADR-059 amended 2026-10-24): a new word is a code change
# with a test, never a runtime list.

#: Words a denial SENTENCE may carry around the one term it names: FS-7's head
#: nouns, quantity/ownership qualifiers and prepositions, plus the pronouns,
#: auxiliaries and negations of a first-person "I have no X" frame. A word
#: outside this list ("weitere", "praktische", "Projekte", "geleitet") narrows the
#: denial to an aspect, and an aspect denial never covers the bare term.
_DENIAL_FRAME_WORDS = frozenset(
    "erfahrung erfahrungen kenntnisse kenntnis wissen know how expertise experience "
    "experiences knowledge background skills skill "
    "eigene eigenen keine kein keinen keinerlei own no any none "
    "mit in im von zu zum zur with of on "
    "ich habe hab hatte i have had has do dont don't did didn't "
    "nicht nie never not ohne without bisher bislang noch leider damit dazu gar je jemals yet ever".split()
)
#: MD-Q13 N2 — the ONE trailing word a product NAME may carry beyond its shorter
#: name ("Google Cloud Platform" -> "Google Cloud", "Adobe Premiere Pro" -> "Adobe
#: Premiere"). Any other trailing word is a specialisation, level or role ("SAP
#: S/4HANA" is not "SAP"; "Scrum Master" is not "Scrum", ADR-064 / ADR-052: the
#: role is not the method; "Lean Six Sigma Black Belt" is not "Six Sigma").
_NAME_EDITION_WORDS = frozenset("platform pro suite edition software".split())
#: MD-Q13 N2 — the ONE leading vendor word a product NAME may carry beyond its
#: shorter name ("Microsoft Power BI" -> "Power BI", "SAP S/4HANA" -> "S/4HANA").
#: Closed: a product family ("Azure", "Power") is not a vendor, so "Azure DevOps"
#: never covers "DevOps" and "Microsoft Power BI" never covers "BI".
_NAME_BRAND_WORDS = frozenset(
    "microsoft sap google adobe amazon oracle ibm atlassian salesforce apple autodesk cisco "
    "siemens apache".split()
)
_NAME_MAX_TOKENS = 5


def _record_tokens(text: str) -> list[str]:
    """Whitespace tokens of a record, edge punctuation stripped, casefolded."""
    return [t for t in (w.strip(".,;:!?()\"'„“”‚‘’").casefold() for w in (text or "").split()) if t]


def _shorter_name_or_sentence_cover(
    token: str, denials: list[str], held: frozenset[str] = frozenset()
) -> list[str]:
    """The denials that name ``token`` as a shorter form of the denied NAME, or as
    the one term a denial SENTENCE names (MD-Q11 F6, narrowed by MD-Q13 N2).
    ``token`` must be a run of WHOLE tokens of the record. Then, either:

    - **sentence**: every other token of the record is a frame word
      (``_DENIAL_FRAME_WORDS``) — "Mit US-GAAP habe ich keine Erfahrung" names
      US-GAAP; "keine weitere Erfahrung mit SAP" does not cover SAP;
    - **shorter name**: the record is a name (at most five tokens, no function
      word of either language) and exactly ONE word is dropped: a leading vendor
      word (``_NAME_BRAND_WORDS``) or a trailing edition word
      (``_NAME_EDITION_WORDS``) — "Microsoft Power BI" covers "Power BI", "SAP
      S/4HANA" covers "S/4HANA", "Google Cloud Platform" covers "Google Cloud" —
      and the person does not hold that shorter name themselves: ``held`` (their
      own skills and the job's claimable ledger forms, ``ats_audit._norm``) wins,
      so a held "DevOps" or "Power BI" is the person's own skill, not the denied
      product's.

    Pure; longest record first."""
    from applire.services.ats_audit import _norm as _fold
    from applire.utils.language_detection import _ENGLISH_STOPWORDS, _GERMAN_STOPWORDS

    chip = _record_tokens(token)
    if not chip:
        return []
    out: list[str] = []
    for denial in denials:
        raw = [w.strip(".,;:!?()\"'„“”‚‘’") for w in (denial or "").split()]
        toks = [w.casefold() for w in raw if w]
        n = len(chip)
        for i in range(len(toks) - n + 1):
            if toks[i : i + n] != chip:
                continue
            lead, trail = toks[:i], toks[i + n :]
            rest = lead + trail
            if rest and all(w in _DENIAL_FRAME_WORDS for w in rest):
                out.append(denial)
                break
            is_name = len(toks) <= _NAME_MAX_TOKENS and not any(
                w in _GERMAN_STOPWORDS or w in _ENGLISH_STOPWORDS or w in _DENIAL_FRAME_WORDS for w in toks
            )
            one_dropped = (lead and not trail and len(lead) == 1 and lead[0] in _NAME_BRAND_WORDS) or (
                trail and not lead and len(trail) == 1 and trail[0] in _NAME_EDITION_WORDS
            )
            if is_name and one_dropped and _fold(token) not in held:
                out.append(denial)
                break
    return out


def _held_names(index: VaultIndex) -> frozenset[str]:
    """MD-Q13 N2: the names the person holds as their own — the claimable vault's
    skill units and the job's claimable ledger forms, ``ats_audit._norm``-ed."""
    from applire.services.ats_audit import _norm as _fold

    skills = {_fold(u.text) for u in getattr(index, "units", ()) or () if _SKILL_PATH_RE.match(u.path)}
    return frozenset(skills | set(getattr(index, "ledger_claimable", ()) or ()))


_SKILL_PATH_RE = re.compile(r"^skills\[\d+\]$")


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
        held = _held_names(index) if denials else frozenset()
        for t in texts:
            if not isinstance(t, str) or not t.strip():
                continue
            if denials:
                out.extend(declared_denial_matches(t, denials))
                out.extend(_shorter_name_or_sentence_cover(t, denials, held))
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
