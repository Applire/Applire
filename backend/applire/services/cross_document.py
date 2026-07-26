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
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from applire.services.ats_audit import _norm as ats_norm
from applire.services.ats_audit import surface_present
from applire.services.oracle.extract import (
    extract_claims_from_letter,
    extract_claims_from_tailored,
    split_clauses,
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

ConflictKind = Literal["bare_denial_of_claimable", "assert_vs_deny"]


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
    """Deterministic bare-denial / assert-vs-deny findings across CV + letter.

    Two conflict kinds, both scoped to ledger-CLAIMABLE concepts only — a
    concept the ledger marks ``claimable: false`` being denied is legitimate
    honesty and is NEVER flagged, in either document:

    * ``bare_denial_of_claimable`` — a claimable concept's surface form
      appears inside a NEGATED clause, in EITHER document (fires intra-
      document too — the run-5 defect: the letter itself both asserts and
      then bare-denies "retrieval systems").
    * ``assert_vs_deny`` — the SAME concept is asserted (non-negated
      occurrence) in one document and denied (negated occurrence) in the
      OTHER document.

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
        forms = _ledger_forms(entry)
        if not forms:
            continue
        concept = entry.get("concept", "") or ""
        evidence = entry.get("evidence", "") or ""
        remedy = _remedy(concept, evidence)

        asserted_in: dict[str, tuple[str, str]] = {}
        denied_in: dict[str, tuple[str, str]] = {}
        seen_bare: set[tuple[str, str]] = set()

        for doc, loc, text in units:
            text_norm = ats_norm(text)
            matched_form = next((f for f in forms if f and surface_present(f, text_norm)), None)
            if matched_form is None:
                continue
            if _is_negated_clause(text):
                denied_in.setdefault(doc, (loc, text))
                if (doc, loc) not in seen_bare:
                    seen_bare.add((doc, loc))
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
        "CROSS-DOCUMENT CONSISTENCY CHECK (#270, deterministic — this is ground "
        "truth, do not re-derive it). Every concept named below is CLAIMABLE per "
        "the Keyword Ledger — it is NEVER a DO-NOT-CLAIM term, and you must NEVER "
        "instruct the writer to name it as an absence. Each finding must be "
        "resolved by rendering the scoped claim named in its remedy — never by "
        "leaving or introducing a bare denial:",
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
        return prompt

    return fn
