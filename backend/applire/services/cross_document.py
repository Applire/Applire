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

"""Cross-document consistency (#270, ADR-058 exception (a)).

Charter run #5 produced a CV and a cover letter that were each individually
vault-grounded and jointly misleading: the CV said the candidate "architected
and designed the database for the RAG system ... with product ownership";
the letter said "I have not worked hands-on with retrieval systems". Both
statements were true in isolation. Neither the Oracle, the ATS panel, nor the
ADR-021 reviewer ever compares the CV and the letter to each other — each
operates one document at a time.

Root cause of the run-5 blocker: :func:`applire.services.gap.askable_gap_inputs`
deliberately folds #260 keyword LIABILITIES (JD hard-requirement concepts that
ARE claimable but lack narrative depth) into the clusterable gap-input list, so
a liability becomes reachable via ``resolve_gap``. The cover-letter call site
then fed that SAME augmented list straight into
:func:`applire.services.cover_letter_positioning.find_gap_testimony` — so a
claimable STRENGTH ("retrieval systems", ledger status "direct") was
positioned as the letter's honest gap, and the ADR-021 reviewer, seeing the
gap-transfer-argument instruction name it "as an absence", pushed the writer
toward a bare denial that directly contradicted the CV.

This module is entirely deterministic (no LLM, no new chain — ADR-058
exception (a)): it re-derives nothing the keyword ledger / denial floor
haven't already decided, it only (a) filters ``askable_gap_inputs`` output at
the cover-letter call site (Fix A — the regression fix), (b) finds genuine
SCOPED BOUNDARIES (a claimable concept the vault also states an explicit
limit on), (c) finds bare-denial / assert-vs-deny CONFLICTS across the CV and
letter text, and (d) finds hard requirements the letter never addresses at
all. Every check flags and instructs — none of them rewrite prose (the
guardrail: never make a gap sound smaller than it is, never make a claim
sound more precise than it is).

Reuses the codebase's single normalisation/segmentation instruments rather
than re-deriving them:
  * :mod:`applire.services.ats_audit` — ``_norm``/``surface_present`` (THE
    presence predicate every other component already agrees on).
  * :mod:`applire.services.oracle.extract` — ``split_clauses``,
    ``extract_claims_from_tailored``, ``extract_claims_from_letter`` (the
    Oracle's own deterministic claim segmentation).

Wave-7 (#278, #277 — charter run #6): two issues in this SAME module that
pull in opposite directions. #278 — the ``bare_denial_of_claimable`` check
was over-firing on legitimate honest denials because clause-wide
CO-OCCURRENCE ("a claimable surface form appears somewhere in this clause"
AND "this clause carries a negation marker somewhere") was being treated as
ATTRIBUTION; fixed by requiring a genuine WORD-BOUNDARY match
(:func:`_bounded_spans`, the #207 lesson) with the negation token genuinely
ATTACHED to that specific occurrence (:func:`_negation_attached_to_form`),
plus a minimum-specificity floor (:func:`_is_specific_enough`) so a very
short/generic concept can never trigger the finding alone. #277 — the CV can
over-claim what the letter honestly scopes; the fix is a THIRD, additive
conflict kind (``unqualified_cv_vs_scoped_letter``,
:func:`_find_unqualified_cv_vs_scoped_letter_conflicts`) over the EXISTING
:class:`ScopedBoundary` primitive, never a loosening of the #278 fix — see
each function's own docstring.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from applire.services.ats_audit import _FOLD_MIN_STEM
from applire.services.ats_audit import _fold_variants as ats_fold_variants
from applire.services.ats_audit import _norm as ats_norm
from applire.services.ats_audit import surface_present
from applire.services.cover_letter_positioning import _AVAILABILITY_PATTERNS
from applire.services.oracle.extract import (
    extract_claims_from_letter,
    extract_claims_from_tailored,
    split_clauses,
    split_sentences,
)

logger = logging.getLogger(__name__)


# ── punctuation normalisation ────────────────────────────────────────────────
# A real past bug (2026-07-11, oracle/extract.py's own ``_normalize_punct``):
# a curly apostrophe (U+2019, "haven't") defeated an ASCII-only marker list.
# Fold typographic punctuation to ASCII BEFORE any negation-marker check.
_APOSTROPHE_CHARS = "’ʼ‘‛´`"


def _normalize_punct(text: str) -> str:
    if not text:
        return ""
    out = unicodedata.normalize("NFKC", text)
    for ch in _APOSTROPHE_CHARS:
        out = out.replace(ch, "'")
    return out


# ── negation detection (deterministic, clause-scoped, EN + DE) ──────────────
_NEGATION_TOKENS = frozenset(
    {
        "not", "no", "never", "lack", "lacking", "without",
        "nicht", "kein", "keine", "keinen", "keiner", "keinem", "keines",
        "nie", "niemals", "ohne",
    }
)
_WORD_RE = re.compile(r"[a-zA-ZÀ-ÿ]+")


def _is_negated_clause(text: str) -> bool:
    """True iff this clause carries a negation marker (EN or DE).

    Clause-scoped by construction — callers pass one clause/sentence at a
    time (:func:`applire.services.oracle.extract.split_clauses`), never a
    whole paragraph, so a negation elsewhere in the document can never leak
    onto an unrelated assertion. ``n't`` is checked as a substring (a
    contraction has no word boundary of its own); every other marker is
    checked as a whole token so short markers ("no", "nie") never false-match
    inside an unrelated word.
    """
    norm = _normalize_punct(text).lower()
    if "n't" in norm:
        return True
    tokens = set(_WORD_RE.findall(norm))
    return bool(tokens & _NEGATION_TOKENS)


# ── #278 — negation ATTRIBUTION, not clause-wide co-occurrence ──────────────
# Charter run #6 ground truth (backend/logs/llm/2026-07-26.jsonl, pinned, not
# reproduced verbatim): two real false positives, both from the SAME defect
# class — `_is_negated_clause` above asks "does this clause carry a negation
# marker ANYWHERE", and the caller separately asks "does a claimable surface
# form appear ANYWHERE in this clause" — co-occurrence was being treated as
# attribution.
#
#   (a) 'AI' (surface_forms ['AI', 'Artificial Intelligence']) false-matched
#       via bare substring: 'ai' is literally inside 'domain' and inside
#       'claim' ("I lack direct LegalTech domain experience" / "I would not
#       claim production logging..."). Neither clause contains the word 'AI'
#       at all. Fixed by requiring a WORD-BOUNDARY occurrence of the surface
#       form — the #207 claim-guard lesson (stance.py's `_word_present`)
#       applied here.
#   (b) 'Software engineering' false-matched on a CV bullet whose EARLY,
#       POSITIVE mention ("...taught...software-engineering courses...") sits
#       many words before an UNRELATED negation late in the same clause
#       ("...team of engineers...with no prior IT/software experience") that
#       modifies a different noun phrase entirely (the trainees' own
#       background, not the candidate's). A real word-boundary match, but the
#       negation does not attach to it. Fixed by requiring the negation token
#       to be within a bounded WORD-DISTANCE window of the matched form's own
#       occurrence, rather than merely present anywhere in the clause.
_NEGATION_ATTACH_WINDOW = 6  # word-token distance; see module docstring above
_BOUNDARY_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ÿ]+(?:'[a-zA-Z]+)?")


def _bounded_spans(form: str, text_norm: str) -> list[tuple[int, int]]:
    """Word-boundary occurrence spans of ``form`` (any morphological fold
    variant) in an already-normalised ``text_norm`` — never a bare substring
    search. Mirrors ``profile/reconcile/stance.py``'s ``_word_present``
    (#207): a short/generic form must never false-match inside an unrelated
    word ('ai' ⊂ 'domain', 'ai' ⊂ 'claim'). Returns ``[]`` when absent.
    """
    n = ats_norm(form)
    if not n:
        return []
    spans: list[tuple[int, int]] = []
    for v in ats_fold_variants(n):
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(v)}(?![a-z0-9])", text_norm):
            spans.append((m.start(), m.end()))
    return spans


def _negation_attached_to_form(
    form: str, text_norm: str, *, window: int = _NEGATION_ATTACH_WINDOW
) -> bool:
    """True iff a negation TOKEN sits within ``window`` word-tokens of an
    actual WORD-BOUNDARY occurrence of ``form`` in ``text_norm`` — negation
    ATTACHED to this concept's own occurrence, not merely present somewhere
    in the same (possibly long, multi-idea) clause. See the module docstring
    above for the two real defects this fixes.
    """
    spans = _bounded_spans(form, text_norm)
    if not spans:
        return False
    tokens = list(_BOUNDARY_TOKEN_RE.finditer(text_norm))
    if not tokens:
        return False
    neg_idxs = [
        i for i, m in enumerate(tokens)
        if "n't" in m.group(0) or m.group(0) in _NEGATION_TOKENS
    ]
    if not neg_idxs:
        return False
    for start, end in spans:
        covered = [i for i, m in enumerate(tokens) if m.start() < end and m.end() > start]
        if not covered:
            continue
        lo, hi = min(covered), max(covered)
        if any(lo - window <= ni <= hi + window for ni in neg_idxs):
            return True
    return False


def _is_specific_enough(form: str) -> bool:
    """Minimum-specificity floor (#278): a very short/generic SINGLE-TOKEN
    surface form (normalized length below ``_FOLD_MIN_STEM`` — the same
    conservative floor ``ats_audit``'s own morphological fold already uses,
    e.g. 'AI', 'ML') can never, on its own, justify a
    ``bare_denial_of_claimable`` finding — reused, not re-derived: this is
    exactly the collision class ``stance.py`` deliberately excludes ml/ai
    from its alias groups for. Multi-word forms are always specific enough
    (a phrase is inherently harder to false-collide with).

    Deliberately scoped to the SAME-clause bare-denial finding only — the
    cross-document ``assert_vs_deny`` triangulated signal is a stronger,
    two-document corroboration and must never be suppressed by this floor.
    """
    n = ats_norm(form)
    if " " in n:
        return True
    return len(n) >= _FOLD_MIN_STEM


# ── shared helpers ───────────────────────────────────────────────────────────


def _get(obj: Any, key: str, default: Any = "") -> Any:
    """Duck-typed field read — tolerates a plain dict or a pydantic model."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _ledger_forms(entry: dict[str, Any]) -> list[str]:
    """A ledger entry's own concept + every surface form, deduped, order kept."""
    forms: list[str] = []
    seen: set[str] = set()
    concept = entry.get("concept", "") or ""
    for form in [concept, *(entry.get("surface_forms") or [])]:
        if form and form not in seen:
            seen.add(form)
            forms.append(form)
    return forms


def _claimable_entries(keyword_ledger: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [e for e in (keyword_ledger or []) if isinstance(e, dict) and e.get("claimable")]


# ── Fix A — the regression fix ───────────────────────────────────────────────


def exclude_claimable_concepts(
    gap_inputs: list[str] | None,
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """Drop any gap-input label the Keyword Ledger marks CLAIMABLE (#270 Fix A).

    ``askable_gap_inputs`` (services/gap.py) deliberately augments
    ``category_c`` with #260 keyword LIABILITIES — concepts that ARE
    claimable (required + claimable + no narrative depth) — so a liability
    stays reachable via ``resolve_gap``. That augmentation is correct for
    clustering/resolve_gap, but the cover letter's gap-positioning call site
    must never receive a claimable concept as "the gap" — the ledger already
    says the vault positively supports it (ADR-059 denial floor is baked into
    ``claimable``), so positioning it as an absence is the exact defect that
    produced the run-5 blocker ("I have not worked hands-on with retrieval
    systems" contradicting the CV's own claim).

    Matched by normalised EXACT equality against the entry's concept or any
    of its surface forms (never a substring match — a gap label is a whole
    concept string, not a fragment to fuzzy-collide against). A gap label
    with no claimable match (a genuine category-C/honest gap) passes through
    unchanged — this must never regress #255/US264's gap-transfer-argument
    selection. Pure; ``None``/empty tolerant.
    """
    claimable_norms: set[str] = set()
    for entry in _claimable_entries(keyword_ledger):
        for form in _ledger_forms(entry):
            n = ats_norm(form)
            if n:
                claimable_norms.add(n)
    return [g for g in (gap_inputs or []) if ats_norm(g) not in claimable_norms]


# ── Fix B.1 — scoped boundaries ──────────────────────────────────────────────


@dataclass(frozen=True)
class ScopedBoundary:
    """A claimable concept the vault ALSO states an explicit limit on.

    Carries both halves so a renderer can produce the one honest output —
    the scoped claim ("I designed the database that powers retrieval, but did
    not configure the embedding models myself") — rather than either a bare
    denial (discards ``evidence``) or an unqualified claim (discards
    ``denial_statement``).
    """

    concept: str
    surface_forms: tuple[str, ...]
    evidence: str
    denial_concept: str
    denial_statement: str


def find_scoped_boundaries(
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None,
) -> list[ScopedBoundary]:
    """Claimable ledger entries that are textually related to a persisted denial.

    A concept is a scoped boundary when the ledger marks it ``claimable: true``
    AND at least one persisted vault denial (``ProfileMetadata.denied_concepts``
    — a list of ``{concept, statement, ...}`` records) mentions one of the
    entry's surface forms in its own ``concept`` or verbatim ``statement``, or
    vice versa (the denial's concept appears in the ledger entry's own
    concept/evidence text). Both directions use THE shared presence predicate
    (:func:`applire.services.ats_audit.surface_present`) so this can never
    disagree with the ATS panel or the ledger about what counts as the same
    concept.

    ``denied_concepts`` entries may be plain dicts (the raw
    ``profile_json.metadata.denied_concepts`` shape) or bare strings — both
    are tolerated. Pure; ``None``/empty tolerant.
    """
    boundaries: list[ScopedBoundary] = []
    denials = denied_concepts or []
    for entry in _claimable_entries(keyword_ledger):
        forms = _ledger_forms(entry)
        if not forms:
            continue
        concept = entry.get("concept", "") or ""
        evidence = entry.get("evidence", "") or ""
        entry_text_norm = ats_norm(f"{concept} {evidence}")
        for denial in denials:
            if isinstance(denial, str):
                d_concept, d_statement = denial, ""
            else:
                d_concept = _get(denial, "concept", "") or ""
                d_statement = _get(denial, "statement", "") or ""
            if not d_concept and not d_statement:
                continue
            denial_text_norm = ats_norm(f"{d_concept} {d_statement}")
            related = any(surface_present(f, denial_text_norm) for f in forms if f)
            if not related and d_concept:
                related = surface_present(d_concept, entry_text_norm)
            if related:
                boundaries.append(
                    ScopedBoundary(
                        concept=concept,
                        surface_forms=tuple(forms),
                        evidence=evidence,
                        denial_concept=d_concept,
                        denial_statement=d_statement,
                    )
                )
                break  # one boundary per claimable concept is enough
    return boundaries


# ── Fix B.2 — cross-document conflicts ───────────────────────────────────────

ConflictKind = Literal[
    "bare_denial_of_claimable", "assert_vs_deny", "unqualified_cv_vs_scoped_letter"
]


@dataclass(frozen=True)
class Conflict:
    """One deterministic cross-document (or intra-document) finding.

    ``document`` is ``"cv"``/``"letter"`` for a single-document
    ``bare_denial_of_claimable`` finding, or ``"<asserting>+<denying>"`` for
    an ``assert_vs_deny`` finding spanning two documents.
    """

    kind: ConflictKind
    concept: str
    surface_form: str
    document: str
    location: str
    quote: str
    remedy: str


def _remedy(concept: str, evidence: str) -> str:
    if evidence:
        return (
            f"Render the SCOPED claim for '{concept}' from its own vault evidence "
            f"(\"{evidence}\") — never a bare denial that discards it."
        )
    return (
        f"'{concept}' is CLAIMABLE per the Keyword Ledger — never render it as an "
        "absence; render the scoped claim from the candidate's own evidence instead."
    )


def _clause_units(claims: list[Any]) -> list[tuple[str, str]]:
    """(location, clause_text) pairs, split to clause granularity.

    Letter claims (:func:`extract_claims_from_letter`) are already clause-
    decomposed (kind ``"clause"``/``"sentence"``) — used as-is. CV claims
    (bullets/summary sentences) are not, so each is further split with
    :func:`split_clauses` for the same clause-scoped negation precision the
    letter path already gets.
    """
    units: list[tuple[str, str]] = []
    for c in claims:
        text = getattr(c, "text", "") or ""
        if not text:
            continue
        if getattr(c, "kind", "") == "clause":
            units.append((c.location, text))
            continue
        clauses = split_clauses(text)
        if len(clauses) <= 1:
            units.append((c.location, text))
        else:
            for i, cl in enumerate(clauses):
                units.append((f"{c.location}.sub[{i}]", cl))
    return units


def find_cross_document_conflicts(
    cv_data: dict[str, Any] | None,
    letter_data: dict[str, Any] | None,
    *,
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None = None,
) -> list[Conflict]:
    """Deterministic bare-denial / assert-vs-deny / unqualified-vs-scoped
    findings across CV + letter.

    Three conflict kinds:

    * ``bare_denial_of_claimable`` — scoped to ledger-CLAIMABLE concepts
      only (a concept the ledger marks ``claimable: false`` being denied is
      legitimate honesty and is NEVER flagged, in either document). A
      claimable concept's surface form appears inside a clause, and a
      negation token is genuinely ATTACHED to THAT occurrence (#278 —
      :func:`_negation_attached_to_form`, not mere clause-wide co-occurrence)
      — fires intra-document too (the run-5 defect: the letter itself both
      asserts and then bare-denies "retrieval systems"). Gated by a
      minimum-specificity floor (#278, :func:`_is_specific_enough`) so a
      very short/generic single-token concept can never trigger this finding
      alone.
    * ``assert_vs_deny`` — the SAME claimable concept is asserted (non-
      negated occurrence) in one document and denied (negated occurrence) in
      the OTHER document. NOT gated by the specificity floor — a
      cross-document triangulation is a stronger signal than a single-clause
      co-occurrence.
    * ``unqualified_cv_vs_scoped_letter`` (#277, #270 inverted) — a claimable
      concept the vault ALSO holds an explicit limit on
      (:func:`find_scoped_boundaries`) appears as an UNQUALIFIED bare
      assertion in the CV (no negation, no inline scoping language of its
      own), while the CURRENT letter draft independently echoes that same
      vault-held limit — with NO negation token at all (an honest scoping
      sentence is not a denial, so it can never reach ``denied_in``/
      ``bare_denial_of_claimable``). The CV, read alone, over-claims what the
      letter has already, honestly, bounded.

    Pure, deterministic; tolerates ``None``/malformed ``cv_data``/
    ``letter_data`` (returns ``[]`` rather than raising).
    """
    claimable_entries = _claimable_entries(keyword_ledger)
    if not claimable_entries:
        return []

    try:
        cv_claims = extract_claims_from_tailored(cv_data or {}) if cv_data else []
    except Exception:
        logger.warning("find_cross_document_conflicts: CV claim extraction failed", exc_info=True)
        cv_claims = []
    try:
        letter_claims = (
            extract_claims_from_letter(letter_data or {}, None) if letter_data else []
        )
    except Exception:
        logger.warning("find_cross_document_conflicts: letter claim extraction failed", exc_info=True)
        letter_claims = []

    units = [("cv", loc, text) for loc, text in _clause_units(cv_claims)] + [
        ("letter", loc, text) for loc, text in _clause_units(letter_claims)
    ]

    conflicts: list[Conflict] = []
    for entry in claimable_entries:
        # Longest-first (#207 lesson, stance.py's alias-group precedent): the
        # more specific surface form wins when several match, so a shorter,
        # more collision-prone form is never preferred over an available
        # longer/more specific one.
        forms = sorted(_ledger_forms(entry), key=lambda f: len(ats_norm(f)), reverse=True)
        if not forms:
            continue
        concept = entry.get("concept", "") or ""
        evidence = entry.get("evidence", "") or ""
        remedy = _remedy(concept, evidence)

        asserted_in: dict[str, tuple[str, str]] = {}
        denied_in: dict[str, tuple[str, str]] = {}
        seen_bare: set[tuple[str, str]] = set()

        for doc, loc, text in units:
            # #278: curly-apostrophe folding applied BEFORE normalisation, up
            # front, so both the word-boundary match AND the negation-token
            # tokenisation below see the same ASCII apostrophe consistently
            # (previously only ``_is_negated_clause`` folded it, separately,
            # on the un-normalised text).
            text_norm = ats_norm(_normalize_punct(text))
            # #278: a real WORD-BOUNDARY occurrence, never a bare substring
            # (see _bounded_spans — the 'ai' ⊂ 'domain'/'claim' collisions).
            matched_form = next((f for f in forms if f and _bounded_spans(f, text_norm)), None)
            if matched_form is None:
                continue
            # #278: negation must be ATTACHED to this SPECIFIC occurrence of
            # the matched form, not merely present anywhere in the clause.
            if _negation_attached_to_form(matched_form, text_norm):
                denied_in.setdefault(doc, (loc, text))
                if (doc, loc) not in seen_bare:
                    seen_bare.add((doc, loc))
                    # #278: minimum-specificity floor — scoped to THIS
                    # finding only; denied_in above still feeds assert_vs_deny.
                    if _is_specific_enough(matched_form):
                        conflicts.append(
                            Conflict(
                                kind="bare_denial_of_claimable",
                                concept=concept,
                                surface_form=matched_form,
                                document=doc,
                                location=loc,
                                quote=text,
                                remedy=remedy,
                            )
                        )
            else:
                asserted_in.setdefault(doc, (loc, text))

        for doc_a, (loc_a, text_a) in asserted_in.items():
            for doc_b, (loc_b, text_b) in denied_in.items():
                if doc_a == doc_b:
                    continue
                conflicts.append(
                    Conflict(
                        kind="assert_vs_deny",
                        concept=concept,
                        surface_form=concept,
                        document=f"{doc_a}+{doc_b}",
                        location=f"{loc_a} vs {loc_b}",
                        quote=f"ASSERTS ({doc_a}): {text_a!r} | DENIES ({doc_b}): {text_b!r}",
                        remedy=remedy,
                    )
                )

    letter_units = [(loc, text) for doc, loc, text in units if doc == "letter"]
    conflicts.extend(
        _find_unqualified_cv_vs_scoped_letter_conflicts(
            cv_claims, letter_units,
            keyword_ledger=keyword_ledger, denied_concepts=denied_concepts,
        )
    )
    return conflicts


# ── #277 (#270 inverted) — CV over-claims what the letter honestly scopes ──


def _unqualified_remedy(concept: str, denial_concept: str, denial_statement: str) -> str:
    limit = denial_statement or denial_concept
    return (
        f"The CV asserts '{concept}' as an unqualified skill/claim while the letter "
        f"already, honestly, scopes it (\"{limit}\"). Make the CV claim as PRECISE as "
        f"the letter — add the SAME limiting language to the CV, grounded verbatim in "
        f"the candidate's own words. This is a CV-side fix ONLY: the letter's own "
        f"honest scoping is correct as written and must NEVER be edited, softened, or "
        f"removed to resolve this finding."
    )


def _find_unqualified_cv_vs_scoped_letter_conflicts(
    cv_claims: list[Any],
    letter_units: list[tuple[str, str]],
    *,
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None,
) -> list[Conflict]:
    """#277 — a claimable concept the vault ALSO holds an explicit limit on
    (:func:`find_scoped_boundaries`), asserted as an UNQUALIFIED bare tag in
    the CV, while the CURRENT letter draft independently echoes that SAME
    vault-held limit. The letter's honest-scoping sentence carries NO
    negation token at all (it is not a denial), so it can never reach
    ``denied_in``/``bare_denial_of_claimable`` above — a structurally
    different signal, hence a third, separate conflict kind.

    ``cv_claims`` are the UNSPLIT ``extract_claims_from_tailored`` claims
    (whole bullet/sentence/skill-tag text) — deliberately NOT the
    clause-split ``units`` the other two conflict kinds use: "already scoped
    inline" must be judged against the CV bullet's own FULL sentence, never
    a single sub-clause of it (a bullet reading "Designed the pipeline;
    embeddings were configured by our system engineer." is honestly scoped
    even though clause-splitting would separate the concept mention from its
    own qualifier into two different clause units).

    A CV occurrence is "unqualified" when:
      * the concept's own surface form matches, word-boundary, somewhere in
        the claim's full text;
      * the SPECIFIC matched sub-clause carries no locally-attached negation
        (a CV-side bare denial is the EXISTING ``bare_denial_of_claimable``
        kind's problem, not this one);
      * the claim's OWN FULL TEXT does not also mention the boundary's own
        limiting text anywhere (already scoped inline — not a gap at all).

    The letter "scopes" the boundary when some letter unit contains a
    word-boundary occurrence of the boundary's own ``denial_concept`` —
    deliberately negation-AGNOSTIC (an honest scoping sentence is not a
    denial) and deliberately anchored to the SAME persisted-denial concept
    text :func:`find_scoped_boundaries` already related to this ledger entry
    — never a second, independent matcher.
    """
    boundaries = find_scoped_boundaries(keyword_ledger, denied_concepts)
    if not boundaries:
        return []

    conflicts: list[Conflict] = []
    for boundary in boundaries:
        if not boundary.denial_concept:
            continue  # nothing reliable to search the letter for (safe skip)
        forms = sorted(boundary.surface_forms, key=lambda f: len(ats_norm(f)), reverse=True)
        if not forms:
            continue

        cv_hit: tuple[str, str] | None = None
        for claim in cv_claims:
            full_text = getattr(claim, "text", "") or ""
            if not full_text:
                continue
            full_text_norm = ats_norm(_normalize_punct(full_text))
            if not any(f and _bounded_spans(f, full_text_norm) for f in forms):
                continue
            if _bounded_spans(boundary.denial_concept, full_text_norm):
                continue  # already scoped inline, anywhere in this claim's own text
            for sub_loc, clause_text in _clause_units([claim]):
                clause_norm = ats_norm(_normalize_punct(clause_text))
                matched_form = next((f for f in forms if f and _bounded_spans(f, clause_norm)), None)
                if matched_form is None:
                    continue
                if _negation_attached_to_form(matched_form, clause_norm):
                    continue  # a CV-side denial is bare_denial_of_claimable's problem
                cv_hit = (sub_loc, clause_text)
                break
            if cv_hit is not None:
                break
        if cv_hit is None:
            continue

        letter_hit: tuple[str, str] | None = None
        for loc, text in letter_units:
            text_norm = ats_norm(_normalize_punct(text))
            if _bounded_spans(boundary.denial_concept, text_norm):
                letter_hit = (loc, text)
                break
        if letter_hit is None:
            continue

        cv_loc, cv_text = cv_hit
        letter_loc, letter_text = letter_hit
        conflicts.append(
            Conflict(
                kind="unqualified_cv_vs_scoped_letter",
                concept=boundary.concept,
                surface_form=forms[0],
                document="cv+letter",
                location=f"{cv_loc} vs {letter_loc}",
                quote=f"CV (unqualified): {cv_text!r} | LETTER (scoped): {letter_text!r}",
                remedy=_unqualified_remedy(
                    boundary.concept, boundary.denial_concept, boundary.denial_statement
                ),
            )
        )
    return conflicts


# ── Fix B.3 — unaddressed hard requirements ─────────────────────────────────

_MAX_UNADDRESSED_REPORTED = 3


def find_unaddressed_hard_requirements(
    keyword_ledger: list[dict[str, Any]] | None,
    letter_data: dict[str, Any] | None,
    *,
    cap: int = _MAX_UNADDRESSED_REPORTED,
) -> list[dict[str, Any]]:
    """JD hard requirements (``"required" in sources``) the ledger marks
    ``claimable: false`` (a genuine, honest gap) whose surface forms appear
    NOWHERE in the letter body.

    Capped at the ``cap`` (default 3) highest ``fit_weight`` entries — this
    never silently truncates: every dropped entry is logged at ``info``
    level. Pure, deterministic; ``None``/empty/malformed-shape tolerant.
    """
    body = _get(letter_data or {}, "body", {}) or {}
    paragraphs = _get(body, "paragraphs", None)
    text_norm = ats_norm(" ".join(p for p in (paragraphs or []) if isinstance(p, str)))

    unaddressed: list[dict[str, Any]] = []
    for entry in keyword_ledger or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("claimable"):
            continue
        if "required" not in (entry.get("sources") or []):
            continue
        forms = _ledger_forms(entry)
        if any(surface_present(f, text_norm) for f in forms if f):
            continue
        unaddressed.append(entry)

    unaddressed.sort(key=lambda e: e.get("fit_weight") or 0, reverse=True)
    if len(unaddressed) > cap:
        dropped = unaddressed[cap:]
        logger.info(
            "find_unaddressed_hard_requirements: capped at %d, dropped %d "
            "lower-weight concept(s): %s",
            cap, len(dropped), [e.get("concept", "") for e in dropped],
        )
        unaddressed = unaddressed[:cap]
    return unaddressed


# ── Fix B.3b (#270(c) follow-up, wave-6) — denial transfer-argument bridges ─
#
# find_unaddressed_hard_requirements tells the writer three concepts (a real
# run-5 vault: embeddings, ranking, observability) need an explicit
# positioning decision, but supplies no candidate testimony to argue a
# transfer from — the writer is left with only the weakest permitted option
# (brief de-emphasis). The transfer arguments exist: each persisted denial
# (``profile_json.metadata.denied_concepts[].statement``) is the candidate's
# OWN verbatim words, and a denial statement routinely carries the honest
# "what I do bring instead" bridge alongside the denial itself. Nothing read
# that until now.


def find_denial_transfer_bridge(
    ledger_entry: dict[str, Any], denied_concepts: list[Any] | None
) -> str | None:
    """The candidate's own transfer-argument SENTENCE for one unmet hard-
    requirement ledger entry, found inside a persisted denial — or ``None``.

    Relates ``ledger_entry`` to a denial the SAME direction
    :func:`find_scoped_boundaries` already uses: one of the entry's own
    surface forms (:func:`_ledger_forms`) must be ``surface_present`` in the
    denial's own ``concept``/``statement`` text (never a second matcher).

    For each related denial, in persisted order, the candidate bridge is
    that denial statement's own LAST sentence (deterministic split via
    :func:`applire.services.oracle.extract.split_sentences`), returned
    VERBATIM ONLY when ALL of the following hold — every guard exists to
    keep a false positive out of a signed letter, never to widen recall:

    * the statement carries at least one genuinely negated sentence
      somewhere (:func:`_is_negated_clause`) — proof the record is an
      actual denial, not unrelated prose that happened to share a token;
    * the LAST sentence itself is NOT negated — a statement that ends on
      its own denial, with nothing after it, has no bridge to give;
    * the LAST sentence does not match an availability/concurrent-
      commitment phrase (the same ``_AVAILABILITY_PATTERNS`` phrase list
      :func:`applire.services.cover_letter_positioning.find_availability_
      testimony` already searches denials for) — that sentence is already
      the dedicated availability-positioning slot's own testimony, never a
      transfer argument for an unrelated gap.

    This is deliberately POSITION-based (the statement's own last sentence),
    not a "first non-negated sentence" search: the run-5 RAG/embeddings
    denial OPENS with several sentences of unrelated positive/scoped content
    ("My contribution was architecture, database design and product
    ownership") before ever denying anything — that content describes a
    DIFFERENT, CLAIMABLE concept :func:`find_scoped_boundaries` already
    surfaces, and a "first non-negated sentence" rule would wrongly re-serve
    it here as if it were this gap's bridge. Reading only the statement's
    OWN final sentence, gated on it being neither the denial itself nor an
    already-claimed availability tail, is the conservative reading that
    still surfaces the true observability bridge — "What I do bring from
    regulated environments is the discipline around it: ..." — while leaving
    the RAG/embeddings and RAG/ranking case at ``None``.

    The first related denial with a qualifying last sentence wins. Returns
    ``None`` when no related denial exists, or none qualifies — a false
    negative is safe; a false positive is not. Pure; ``None``/empty/
    malformed-shape tolerant.
    """
    forms = _ledger_forms(ledger_entry)
    if not forms:
        return None
    for denial in denied_concepts or []:
        if isinstance(denial, str):
            continue
        statement = _get(denial, "statement", "") or ""
        if not statement:
            continue
        d_concept = _get(denial, "concept", "") or ""
        denial_text_norm = ats_norm(f"{d_concept} {statement}")
        related = any(surface_present(f, denial_text_norm) for f in forms if f)
        if not related:
            continue
        sentences = split_sentences(statement)
        if not sentences:
            continue
        if not any(_is_negated_clause(s) for s in sentences):
            continue  # no actual denial detected in this statement
        last = sentences[-1]
        if _is_negated_clause(last):
            continue  # the statement ends on the denial itself — no bridge
        if any(pattern.search(last) for pattern in _AVAILABILITY_PATTERNS):
            continue  # already the dedicated availability-testimony slot's own content
        return last
    return None


# ── shared wording — #270(c): every unmet hard requirement gets a decision ──
# The permitted responses are EXACTLY two — a transfer argument grounded in
# the candidate's OWN testimony, or a brief, honest de-emphasis that names
# the gap without dwelling on it. Never an assertion (these concepts are
# ``claimable: false`` by construction — find_unaddressed_hard_requirements
# only ever selects honest gaps), never a softened/vaguer denial, and never
# a litany — every response folds into the SAME single honest-gap paragraph.
_UNADDRESSED_INSTRUCTION = (
    "These are honest gaps (claimable: false per the Keyword Ledger) — JD hard "
    "requirements the letter does not address anywhere. This concept must NEVER "
    "be asserted or presented as something the candidate has, has done, or "
    "knows. For each, give an explicit positioning decision: a transfer "
    "argument grounded in the candidate's own testimony, or a brief, honest "
    "de-emphasis that names the gap without dwelling on it or suggesting a "
    "JD-critical requirement is negligible. When a concept below carries its "
    "own CANDIDATE'S OWN TRANSFER-ARGUMENT TESTIMONY (verbatim, quoted from a "
    "persisted denial), the permitted response upgrades: give the transfer "
    "argument grounded VERBATIM in that testimony, stated strictly AFTER the "
    "honest acknowledgement of the gap, never instead of it — brief "
    "de-emphasis is no longer the only reasonable choice for that concept. "
    "Where no such testimony is given, the brief de-emphasis wording stands "
    "unchanged. Silence is not one of the options for a hard requirement. "
    "Fold whichever response applies into the SAME single honest-gap "
    "paragraph — never a litany of separate gap admissions."
)


def render_unaddressed_hard_requirements_block(
    entries: list[dict[str, Any]],
    denied_concepts: list[Any] | None = None,
) -> str:
    """Render unmet JD hard requirements (#270(c)) as a deterministic block.

    Dual use, same rendering both times:
      * WRITER (pre-draft): the caller passes ``find_unaddressed_hard_
        requirements(keyword_ledger, None)`` — before any letter exists every
        required honest gap is trivially "unaddressed", so the writer gets
        the same top-``cap`` list its first draft will later be re-checked
        against (a chance to get it right without a correction round).
      * REVIEWER (post-draft, via :func:`cross_document_reviewer_prompt_fn`):
        recomputed against the CURRENT draft each iteration — the block
        disappears once the writer/corrector has given each concept its
        positioning decision, the same convergence signal
        ``keyword_ledger.coverage_reviewer_prompt_fn`` already uses.

    ``denied_concepts`` (wave-6 #270(c) follow-up): when given, each entry is
    additionally checked against :func:`find_denial_transfer_bridge` — a
    concept with a found bridge carries its own verbatim transfer-argument
    testimony line, upgrading its permitted response (see
    ``_UNADDRESSED_INSTRUCTION``). Optional and defaults to ``None`` so
    legacy callers keep the unchanged de-emphasis-only wording.

    Returns ``""`` when ``entries`` is empty so a fully-addressed draft (or a
    JD with no unmet hard requirements) adds nothing.
    """
    if not entries:
        return ""
    lines = [
        "=== UNADDRESSED HARD REQUIREMENTS (deterministic — #270(c)) ===",
        _UNADDRESSED_INSTRUCTION,
    ]
    for e in entries:
        evidence = e.get("evidence", "") or "(none — a pure keyword gap, no vault context)"
        lines.append(f"  - {e.get('concept', '')} — context: {evidence}")
        bridge = find_denial_transfer_bridge(e, denied_concepts)
        if bridge:
            lines.append(
                "    CANDIDATE'S OWN TRANSFER-ARGUMENT TESTIMONY (verbatim, from a "
                f'persisted denial): "{bridge}"'
            )
    return "\n".join(lines)


def unaddressed_hard_requirements_positioning(
    entries: list[dict[str, Any]],
    denied_concepts: list[Any] | None = None,
) -> dict[str, Any]:
    """The ``positioning_requested['unaddressed_hard_requirements']`` shape
    (#270(c), the established #255 pattern) — threaded to the reviewer AND
    corrector so each concept's positioning sentence is REQUIRED content,
    never stripped as unrequested/unrelated to the letter (the #255 lesson:
    a corrector that never received a positioning input could not tell a
    requested addition apart from an invented one).

    ``denied_concepts`` (wave-6 #270(c) follow-up): when given, each concept
    dict additionally carries a ``"transfer_bridge"`` key (the verbatim
    :func:`find_denial_transfer_bridge` result) whenever one was found —
    omitted entirely when there is none, so a degraded/legacy reader sees no
    shape change. Optional, defaults to ``None``.

    Returns ``{}`` when ``entries`` is empty — a legacy/degraded caller adds
    nothing to ``positioning_requested``.
    """
    if not entries:
        return {}
    concepts: list[dict[str, Any]] = []
    for e in entries:
        concept: dict[str, Any] = {
            "concept": e.get("concept", ""),
            "evidence": e.get("evidence", "") or "",
        }
        bridge = find_denial_transfer_bridge(e, denied_concepts)
        if bridge:
            concept["transfer_bridge"] = bridge
        concepts.append(concept)
    return {
        "concepts": concepts,
        "required": True,
        "instruction": _UNADDRESSED_INSTRUCTION,
    }


# ── render helpers ───────────────────────────────────────────────────────────


def render_scoped_boundary_block(boundaries: list[ScopedBoundary]) -> str:
    """Render scoped boundaries for the WRITER prompt (threaded via
    ``build_cover_letter_prompt``'s new ``scoped_boundary_block`` kwarg).

    Returns ``""`` when empty so legacy callers add nothing.
    """
    if not boundaries:
        return ""
    lines = [
        "=== SCOPED BOUNDARIES (deterministic — #270) ===",
        "For each concept below, the vault holds BOTH a positive contribution AND an "
        "explicit candidate-stated limit. This concept IS claimable — render the "
        "SCOPED claim naming both halves, grounded verbatim in the text given; never "
        "a bare denial that discards the positive half, and never an unqualified "
        "claim that ignores the limit. Never place this concept in the honest-gap/"
        "transfer-argument paragraph — it is not a gap.",
    ]
    for b in boundaries:
        lines.append(f"  - {b.concept}")
        lines.append(f"    POSITIVE (candidate's own vault evidence): {b.evidence}")
        lines.append(
            f"    STATED LIMIT (candidate's own words): {b.denial_statement or b.denial_concept}"
        )
    return "\n".join(lines)


def render_cross_document_conflicts_block(conflicts: list[Conflict]) -> str:
    """Render deterministic conflicts appended to the ADR-021 REVIEWER's source.

    Returns ``""`` when empty so a clean draft carries nothing extra.
    """
    if not conflicts:
        return ""
    lines = [
        "CROSS-DOCUMENT CONSISTENCY CHECK (#270/#277/#278, deterministic — this is "
        "ground truth, do not re-derive it). Every concept named below is CLAIMABLE "
        "per the Keyword Ledger — it is NEVER a DO-NOT-CLAIM term, and you must NEVER "
        "instruct the writer to name it as an absence. Each finding must be resolved "
        "EXACTLY as its own REMEDY instructs — never by leaving or introducing a bare "
        "denial, and never by softening, shortening, or removing an honest disclosure "
        "already present in either document (an 'unqualified_cv_vs_scoped_letter' "
        "finding is a CV-side fix ONLY — the letter's own honest scoping is correct "
        "as written):",
    ]
    for c in conflicts:
        lines.append(f"  - [{c.kind}] '{c.concept}' — {c.document} @ {c.location}: {c.quote!r}")
        lines.append(f"    REMEDY: {c.remedy}")
    return "\n".join(lines)


def cross_document_reviewer_prompt_fn(
    base_fn: Any,
    *,
    cv_data: dict[str, Any] | None,
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None = None,
):
    """Wrap a ``reviewer_prompt_fn`` so every ADR-021 review iteration carries
    the CURRENT draft's deterministic cross-document conflicts (#270).

    Composes with (does not replace) ``keyword_ledger.coverage_reviewer_
    prompt_fn`` — the established pattern (US213/#122): ``review_and_refine``
    calls ``reviewer_prompt_fn(source, draft)`` fresh each iteration, so the
    conflict list is recomputed against the LATEST draft and disappears once
    the corrector has resolved it — deterministic convergence riding the
    existing bounded ADR-047 loop (no new loop, no new LLM pass).
    """

    def fn(source: str, draft: dict[str, Any]) -> str:
        prompt = base_fn(source, draft)
        conflicts = find_cross_document_conflicts(
            cv_data, draft, keyword_ledger=keyword_ledger, denied_concepts=denied_concepts,
        )
        block = render_cross_document_conflicts_block(conflicts)
        if block:
            logger.info(
                "cross-document check: %d conflict(s) in current letter draft: %s",
                len(conflicts), [c.concept for c in conflicts],
            )
            prompt = f"{prompt}\n\n{block}"

        # #270(c): the deterministic backstop for "every unmet JD hard
        # requirement gets an explicit positioning decision" — computed
        # against the CURRENT draft each iteration so it disappears the
        # moment the writer/corrector addresses a concept (same convergence
        # signal as the verified-coverage check / the conflicts block above).
        unaddressed = find_unaddressed_hard_requirements(keyword_ledger, draft)
        unaddressed_block = render_unaddressed_hard_requirements_block(
            unaddressed, denied_concepts
        )
        if unaddressed_block:
            logger.info(
                "unaddressed hard requirements: %d concept(s) missing from "
                "current letter draft: %s",
                len(unaddressed), [e.get("concept", "") for e in unaddressed],
            )
            prompt = f"{prompt}\n\n{unaddressed_block}"
        return prompt

    return fn
