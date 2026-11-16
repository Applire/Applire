# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sorbet WP-Q7 — the posting's words are not the person's claim (ruling Q7-1,
MD-Q15; ADR-052 amended 2026-11-16).

Delivery 2a captured three statements a person never made, each in the job
posting's own words, and the Oracle graded two of them ``grounded``:

- the target title as the CV's headline: "Leiter Operations mit 14 Jahren
  Erfahrung in der diskreten Fertigung …" for a Produktionsleiter (run 3,
  ``summary[0]``) — grounded by token coverage against the vault's own summary
  sentence "Produktions- und Betriebsleiter mit 14 Jahren Erfahrung in der
  diskreten Fertigung", where every token but the title matches;
- a requirement met only in part, written with the posting's label: "…
  übernahm Führung und Entwicklung der Mitarbeitenden …", "wertschätzende
  Führung von 38 Mitarbeitenden", "demonstrating assertive leadership" —
  grounded by the figure arm (38 traces; nothing else in the sentence is read).

Both arms read what IS in the sentence and never what the sentence adds. This
module adds the two facts that name the addition, read after every other arm
has spoken. Each can only make a verdict stricter (MD-Q11):

1. **title not held** (CV claims only, same-language pair only): the sentence
   states a position title FOR THE PERSON (``position_terms.position_claims``, the
   closed T-1 vocabulary) that the vault's own text never holds
   (``position_terms.unheld_title_term``, the reader ``enforce_title_stance``
   already uses) → ``unbacked``. A letter is not read: "Als Leiter Operations
   würde ich …" speaks of the job applied for. A cross-language pair is not read:
   a translated title ("Head of Production" for "Produktionsleiter") is ruled
   behaviour (R-2 / Q3-1) the vocabulary cannot match across languages.
   MD-Q17 F3 adds the posting's own title to the CV's headline (the summary):
   a summary that carries the posting's role title is the person's only when a
   title they hold or held EQUALS it, modulo case and gender markers
   (:func:`posting_title_unheld`) — "Senior Backend Engineer" over a held
   "Backend Engineer", "Teamleiter" over a held "Teamleiter (in Vertretung)".
   The titles are threaded from the CV service (``posting_titles``); without
   them (letters, the external audit) the T-1 vocabulary alone reads.
2. **posting word for a partly met requirement** (CV and letter): the sentence
   names, as a whole token, a form of a posting-word-only partial row
   (``keyword_ledger.posting_word_partial_rows`` — a below-the-bar ``partial``
   none of whose forms the vault ever uses) and was graded ``grounded`` →
   ``unverifiable``. Not ``unbacked``: a near synonym of the person's own word
   ("Lean-Produktion" beside a vault's "Lean Management") can carry such a row,
   and a word choice is not a false statement; it is left open, not accused.
   On a pair whose vault holds another language the literal fact "a word your
   profile never uses" cannot be established (the faithful translation misses
   it), so the same verdict carries ``postingTermOtherLanguage`` (ADR-068).
   The rows are computed with the person's denial wording counted as theirs
   (MD-Q17 F2); the ledger stamp does not count it.

A skill chip that IS such a form and was graded ``grounded`` (the ledger arm,
``backing.backing_for_skill``, grounds a chip on the partial row's quote — the
quote shows the part the person holds, never the posting's word) →
``unverifiable`` with the same key. The ledger arm itself is left as it is:
refusing the quote there would send a same-language chip to ``unbacked``
(``skillNoEvidence``), a new blocking verdict the captured documents give no
evidence for (MD-Q11), and would make the result depend on whether the row
carried a verified quote. Here the only change either fact can make to a chip
is ``grounded → unverifiable``.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from applire.schemas.oracle import ClaimResult, ClaimVerdict

logger = logging.getLogger(__name__)

#: The verdicts a fact here may make stricter. A flag already raised stays as it is.
_OPEN = frozenset({"grounded", "unverifiable"})
_READ_KINDS = frozenset({"sentence", "bullet", "clause"})


def _profile_dict(profile: Any) -> dict[str, Any] | None:
    raw = profile.model_dump(mode="json") if hasattr(profile, "model_dump") else profile
    return raw if isinstance(raw, dict) else None


def unheld_titles(text: str, profile_json: dict[str, Any] | None) -> list[str]:
    """The position titles *text* states for the person that the vault's own
    text holds no such title of. Never raises (a reader error is no finding)."""
    if not text or not profile_json:
        return []
    try:
        from applire.services.profile.reconcile.position_terms import (
            position_claims,
            unheld_title_term,
        )

        out: list[str] = []
        for _family, phrase in position_claims(text):
            hit = unheld_title_term(phrase, profile_json)
            if hit and hit not in out:
                out.append(hit)
        return out
    except Exception:  # pragma: no cover — defensive: no finding
        logger.exception("oracle: title reader failed — no title finding")
        return []


#: Gender markers a German posting title carries — "(m/w/d)", "(w/m/d)", "(m/f/d)",
#: "(all genders)", "(gn*)" — and the in-word forms "Leiter*in", "Leiter:in",
#: "Leiter/in", "Leiter/-in", "LeiterIn". Removed on BOTH sides of the equality, so
#: they are never a difference (MD-Q17 F3: "modulo case and gender markers").
_GENDER_BRACKET = re.compile(
    r"\(\s*(?:(?:[mwfdxi]|div)\s*[/|,]\s*)+(?:[mwfdxi]|div)\s*\)|\(\s*all\s+genders\s*\)|\(\s*gn\*?\s*\)",
    re.IGNORECASE,
)
_GENDER_INWORD = re.compile(r"(?<=\w)(?:[*:_]in(?:nen)?|/-?in(?:nen)?|In(?:nen)?)\b")
#: The feminine "-in"/"-innen" of a German agent noun ("Leiterin", "Controllerin",
#: "Ingenieurin") — closed to the agent endings -er/-or/-eur, so "Berlin",
#: "Admin" or "Origin" are never touched.
_GENDER_SUFFIX = re.compile(r"(?<=er|or|ur)in(?:nen)?\b")
#: Where a posting title's own label ends and its context begins:
#: "Senior Backend Engineer — Payments Platform", "Controller, Werk Nord".
_TITLE_CONTEXT = re.compile(r"\s+[—–|]\s+|\s+-\s+|,\s+|\s*\(")


def _title_norm(title: str) -> str:
    """Case- and gender-marker-free form of a title; everything else kept —
    "Teamleiter (in Vertretung)" keeps its qualifier and never equals
    "Teamleiter"."""
    t = _GENDER_BRACKET.sub(" ", title)
    t = _GENDER_INWORD.sub("", t)
    t = t.casefold()
    t = _GENDER_SUFFIX.sub("", t)
    return " ".join(re.findall(r"[\w/&+.#-]+|[()]", t)).replace("( ", "(").replace(" )", ")")


def _posting_title_forms(posting_titles: tuple[str, ...] | list[str]) -> list[str]:
    """Each posting title, and its own label when context follows it."""
    out: list[str] = []
    for raw in posting_titles or ():
        if not isinstance(raw, str) or not raw.strip():
            continue
        clean = _GENDER_BRACKET.sub(" ", raw)
        for cand in (clean, _TITLE_CONTEXT.split(clean.strip(), maxsplit=1)[0]):
            n = _title_norm(cand)
            if len(n) >= 3 and n not in out:
                out.append(n)
    return out


def _held_titles(profile_json: dict[str, Any]) -> list[str]:
    """The titles the person holds or held: each ``work_experience[].role``, and
    each part of a role written as alternatives ("Fertigungsmeister /
    Schichtleiter")."""
    out: list[str] = []
    for w in profile_json.get("work_experience") or []:
        role = w.get("role") if isinstance(w, dict) else None
        if not isinstance(role, str) or not role.strip():
            continue
        for cand in [role, *re.split(r"\s+/\s+|\s*;\s*|\s+\|\s+", role)]:
            n = _title_norm(cand)
            if n and n not in out:
                out.append(n)
    return out


def _spans(needle: str, hay: str) -> list[tuple[int, int]]:
    return [m.span() for m in re.finditer(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", hay)]


def posting_title_unheld(
    text: str, profile_json: dict[str, Any] | None, posting_titles: tuple[str, ...] | list[str]
) -> str | None:
    """MD-Q17 F3: the posting's role title as *text* carries it, when no title the
    person holds or held EQUALS it (modulo case and gender markers) — else None.

    The T-1 vocabulary reads leader and deputy titles only; this reads the one
    title the writer was handed. "Senior Backend Engineer" over a held "Backend
    Engineer" and "Teamleiter" over a held "Teamleiter (in Vertretung)" are not
    the person's. An occurrence that lies inside a held title's own occurrence in
    *text* is that held title ("Financial Controller" for a posting's
    "Controller")."""
    if not text or not profile_json:
        return None
    forms = _posting_title_forms(posting_titles)
    if not forms:
        return None
    held = _held_titles(profile_json)
    hay = _title_norm(text)
    held_spans = [sp for h in held for sp in _spans(h, hay)]
    for form in forms:
        # A held title EQUAL to the form occupies the form's own span, so the
        # containment below accepts it; no separate equality branch is needed.
        for a, b in _spans(form, hay):
            if not any(ha <= a and b <= hb for ha, hb in held_spans):
                return form
    return None


def posting_terms_named(text: str, rows: list[dict[str, Any]]) -> list[str]:
    """The forms of *rows* that *text* names as a whole token."""
    if not text or not rows:
        return []
    from applire.services.ats_audit import _norm, surface_present_whole_token

    norm = _norm(text)
    out: list[str] = []
    for row in rows:
        for form in [row.get("concept"), *(row.get("surface_forms") or [])]:
            if isinstance(form, str) and form.strip() and surface_present_whole_token(form, norm):
                out.append(form)
                break
    return out


def apply_posting_word_checks(
    results: list[ClaimResult],
    *,
    profile: Any,
    keyword_ledger: list[dict[str, Any]] | None,
    is_cv: bool,
    same_language: bool,
    posting_titles: tuple[str, ...] | list[str] = (),
) -> list[ClaimResult]:
    """*results* with the two facts applied (see the module docstring). Pure;
    a result neither fact touches is returned as the same object."""
    profile_json = _profile_dict(profile)
    if profile_json is None:
        return results
    rows: list[dict[str, Any]] = []
    if keyword_ledger:
        try:
            from applire.services.keyword_ledger import posting_word_partial_rows

            # MD-Q17 F2: the person's own denial wording counts as theirs here.
            rows = posting_word_partial_rows(keyword_ledger, profile_json, with_denial_wording=True)
        except Exception:  # pragma: no cover — defensive
            logger.exception("oracle: posting-word rows unreadable — no posting-word finding")
            rows = []
    out: list[ClaimResult] = []
    for res in results:
        claim, verdict = res.claim, res.verdict
        if claim.kind == "skill" and verdict.verdict == "grounded":
            # A chip that IS a posting-word-only form (see the module docstring).
            terms = posting_terms_named(claim.text, rows)
            if terms and _norm_eq(claim.text, terms[0]):
                out.append(_posting_term_result(claim, verdict, terms[0], same_language))
                continue
        if claim.kind not in _READ_KINDS or verdict.verdict not in _OPEN:
            out.append(res)
            continue
        titles = unheld_titles(claim.text, profile_json) if (is_cv and same_language) else []
        if not titles and is_cv and same_language and _is_headline(claim):
            hit = posting_title_unheld(claim.text, profile_json, posting_titles)
            titles = [hit] if hit else []
        if titles:
            title = titles[0]
            out.append(ClaimResult(claim=claim, verdict=ClaimVerdict(
                verdict="unbacked",
                checker="grounding",
                detail=(
                    f'States the title "{title}", which no position in your profile holds '
                    "— a title is yours only when you hold or held it."
                ),
                detail_key="titleNotHeld",
                detail_params={"title": title},
            )))
            continue
        terms = posting_terms_named(claim.text, rows) if verdict.verdict == "grounded" else []
        if terms:
            out.append(_posting_term_result(claim, verdict, terms[0], same_language))
            continue
        out.append(res)
    return out


def _is_headline(claim: Any) -> bool:
    """The CV's opening: the summary (its first sentence carries the title phrase)."""
    loc = getattr(claim, "location", "") or ""
    return loc.startswith("summary")


def _norm_eq(a: str, b: str) -> bool:
    from applire.services.ats_audit import _norm

    return _norm(a) == _norm(b)


def _posting_term_result(
    claim: Any, verdict: ClaimVerdict, term: str, same_language: bool
) -> ClaimResult:
    if same_language:
        detail, key = (
            f'Names "{term}" — the job posting\'s word for a requirement your profile '
            "meets only in part, and a word your profile never uses."
        ), "postingTermNotHeld"
    else:
        # ADR-068: a literal miss across languages is not a finding about the
        # person's words — "led three shift managers" is the faithful English of a
        # vault's "über drei Schichtleiter" (WP-Q7 replay, run 3 cross, 1 of 5).
        detail, key = (
            f'Names "{term}" — the job posting\'s word for a requirement your profile '
            "meets only in part. Your profile is in another language, so whether this "
            "is your own word could not be checked."
        ), "postingTermOtherLanguage"
    return ClaimResult(claim=claim, verdict=ClaimVerdict(
        verdict="unverifiable",
        checker="grounding",
        evidence=verdict.evidence,
        detail=detail,
        detail_key=key,
        detail_params={"term": term},
    ))
