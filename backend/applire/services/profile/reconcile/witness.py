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

"""#370 — the testimony write-loss witness.

An ~11,000-character testimony dossier returned `status: applied, changes:
75` while a whole budget/team-size section was silently discarded — nothing
counted or logged the loss, and the caller had no way to distinguish "all of
it landed" from "most of it landed" (#370). `compute_not_applied` is the
deterministic FACT-checker that closes that gap: a pure function, no I/O, no
LLM call, that compares the submitted testimony TEXT against the ops the
reconcile ENGINE actually produced and reports every piece of testimony
content that is not literally present in any of them.

**ADR-062 clause 1 — a fact, never a judgement.** This module answers
exactly one question per span: "is this literally present in an op's own
serialised payload (or, for a rejected raw op, did it fail schema
validation)". It never asks "was this paraphrased", "did this matter", or
"should this have applied" — those are judgements, and a deterministic rule
may not make them. Consequently an item in the returned list is NOT proof
that content was lost: a spelled-out figure ("zwölf" for 12), a heavily
paraphrased sentence, or a fact the model correctly decided did not belong in
the vault at all, all read identically to a genuine drop. The witness is a
recall-favouring instrument — "no semantic matching, no 'probably applied'"
— not a precision one; a human or an upstream judgement still decides what a
listed span means. That asymmetry is deliberate: #370's whole complaint was
that the caller could not even SEE the candidate spans.

**Scope — the ENGINE's ops, not the applier's final state.** `ops` is the
`ReconcileResult.ops` the reconcile engine returns: after `_parse_ops`, after
`enforce_stance`, after `enforce_attribution`, after the deterministic
`demote_skill` emission — i.e. exactly what `commit_ops`/`apply_ops` is about
to be handed. This witness does NOT re-check what `apply_ops` finally did
with each op (an `add_bullets` whose `target` never resolves to a real entity
is silently dropped there — see Part A of #370's trace, mechanism (4) — and
is invisible here by construction). Checking engine-output vs. testimony
catches the mechanism the reported bug actually was (the MODEL's own output
never mentioned the section at all); catching apply-time no-ops is a
DIFFERENT, not-yet-covered mechanism, named explicitly so nobody mistakes
this witness for a total loss-coverage guarantee.

**Three checks, three reasons** (`schemas.testimony.NotApplied`):

(a) ``figure`` / ``figure_not_in_any_op`` — every numeric figure in the
    testimony (integers >= 2 digits, decimals, and a currency/percent/
    magnitude-word form folded to its digit string, e.g. "1,35 Mio" ->
    "1350000") whose normalised digit string appears in NO op's serialised
    JSON.
(b) ``sentence`` / ``no_op_carried_it`` — every testimony sentence that
    shares NO content token (lower-cased, >= 5 chars, minus a small EN/DE
    stopword set) with any op's serialised JSON.
(c) ``op`` / ``op_rejected`` — every raw op the model emitted that
    `engine._parse_ops` dropped for failing schema validation, named by its
    own declared `"op"` type string (`rejected_ops`, an optional out-of-band
    list the caller threads through from `ReconcileResult.rejected_ops`).

A `denials` parameter (`ReconcileResult.denials`) is folded into the
"carried" corpus for (a)/(b) alongside the ops — see `_ops_haystack`'s
docstring for why this is a deliberate, documented widening past the Part B
contract's literal wording (a receipted denial is a landed outcome, not a
loss, and without this every denial-bearing submission reads as partial).

**Deduplication.** Both (a) and (b) report each DISTINCT missing figure/
sentence ONCE (keyed by its normalised reading), using the first verbatim
occurrence as the span — an explicit, documented choice to keep the report
actionable on a long dossier that repeats a figure, rather than one entry per
occurrence.

**Figures are digit-only.** Spelled-out numbers ("zwölf", "twelve") are
deliberately NOT extracted as figures here (unlike some grounding checks
elsewhere in this package) — the contract's own examples are all digit-form,
and treating a spelled number as a citable figure would need the same
magnitude/compound-word machinery this module intentionally keeps narrow. A
spelled-out figure can still be caught by the SENTENCE check if the rest of
its sentence shares no token with any op.

**Independent, narrow implementation — not imported from `services.oracle`.**
The Oracle's figure extractor (`services.oracle.matchers.figures.extract_
figures`) is the codebase's ONE shared instrument for citable-evidence figure
matching (ADR-066), but `oracle` depends on this write path's OUTPUT
(rendered documents), never the other way — importing it here would invert
that dependency (the same reasoning `reconcile/attribution.py`'s module
docstring gives for its own independent copy of oracle's abbreviation table).
This module's figure/number handling is therefore a deliberately narrower,
self-contained copy, scoped to exactly what a testimony-loss check needs.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Sequence

from applire.schemas.testimony import NotApplied
from applire.services.profile.reconcile.attribution import _split_sentences
from applire.services.profile.reconcile.ops import CommitOp

_SPAN_MAX_CHARS = 200

# A bare integer counts as a figure only at this floor ("the pre-existing
# US244 signal floor" the codebase's own figure extractor documents for the
# identical reason: a single digit is date-fragment/count-noise-prone). A
# DECIMAL counts regardless of its leading-group digit count ("8,2" is one
# leading digit but unambiguous quantified substance).
_MIN_INTEGER_DIGITS = 2

_DIGIT_RUN_RE = re.compile(r"\d+(?:[.,]\d+)*")

# A small, independent DE/EN magnitude-word table (see module docstring for
# why this is not imported from `services.oracle.matchers.figures`).
_MAGNITUDE_RE = re.compile(
    r"\s*(mio\.?|mrd\.?|tsd\.?|million(?:en)?|milliarde(?:n)?|tausend|k)\b",
    re.IGNORECASE,
)
_MAGNITUDE_FACTORS: dict[str, int] = {
    "mio": 1_000_000,
    "million": 1_000_000,
    "millionen": 1_000_000,
    "mrd": 1_000_000_000,
    "milliarde": 1_000_000_000,
    "milliarden": 1_000_000_000,
    "tsd": 1_000,
    "tausend": 1_000,
    "k": 1_000,
}

# A small, non-exhaustive EN/DE stopword set — only words >= 5 chars matter
# here (the length floor already drops every shorter function word), so this
# is deliberately short: it exists to stop generic connective words from
# reading as a "shared content token" between two otherwise-unrelated spans,
# not to be a general-purpose language stopword list.
_STOPWORDS = frozenset(
    {
        # EN
        "about", "after", "again", "being", "could", "every", "first",
        "other", "shall", "should", "their", "there", "these", "those",
        "three", "under", "until", "where", "which", "while", "would",
        "above", "along", "among", "since", "still", "doing", "having",
        "might", "small", "large", "within", "without", "across", "always",
        "before", "cannot", "either", "though", "through",
        # DE
        "diese", "dieser", "dieses", "diesem", "einer", "einem", "einen",
        "keine", "keiner", "meine", "meiner", "seine", "seiner", "sowie",
        "sowohl", "unter", "durch", "wurde", "wurden", "werden", "worden",
        "waren", "immer", "schon", "damit", "dabei", "davon", "daran",
        "dafür", "dahin", "danach", "gegen", "jedoch", "sondern", "während",
        "zudem", "zwischen", "zusätzlich", "dessen", "deren", "sodass",
        "sobald", "sofern", "weiter", "weitere", "weiteren", "weiterer",
        "welche", "welcher", "welchem", "welchen", "ebenso", "hierbei",
        "hierfür", "insgesamt",
    }
)

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _norm_text(text: str) -> str:
    """NFKC-normalise then casefold — the same "normalise before matching"
    discipline the rest of the codebase applies (U+2019 lesson)."""
    return unicodedata.normalize("NFKC", text or "").casefold()


def _content_tokens(text: str) -> frozenset[str]:
    """Lower-cased alphabetic tokens of length >= 5, minus the stopword set.

    Digits are deliberately excluded here — figure matching is check (a)'s
    job, on its own normalised-digit-string terms; blending the two would
    blur which reason a miss gets and let a coincidental shared digit rescue
    an unrelated sentence.
    """
    normalized = _norm_text(text)
    return frozenset(
        tok for tok in _TOKEN_RE.findall(normalized)
        if len(tok) >= 5 and tok not in _STOPWORDS
    )


@dataclass(frozen=True)
class _FigureOccurrence:
    span: str
    variants: frozenset[str]


def _canonical_digit_variants(raw_digits: str) -> set[str]:
    """Every plausible normalised reading of one raw digit run.

    Digit-group separators are locale-ambiguous (DE '.' = thousands grouping,
    EN ',' = thousands grouping; both languages also use the OTHER character
    for a decimal point) — so both the separators-fully-stripped reading and
    the comma-as-decimal reading are produced, and a match against either is
    accepted. Mirrors the two-variant scheme `reconcile.stance._figure_
    variants` already uses for the identical ambiguity (independent, narrow
    copy — see the module docstring).
    """
    return {
        raw_digits.replace(".", "").replace(",", ""),
        raw_digits.replace(".", "").replace(",", "."),
    }


def _expand_with_magnitude(variants: set[str], factor: int) -> set[str]:
    expanded: set[str] = set()
    for v in variants:
        try:
            scaled = Decimal(v) * factor
        except InvalidOperation:
            continue
        expanded.add(format(scaled.normalize(), "f"))
    return expanded


def _extract_figures(text: str) -> list[_FigureOccurrence]:
    """Every figure-shaped digit run in ``text`` at/above the counting floor.

    Returns one :class:`_FigureOccurrence` per OCCURRENCE (not yet deduped —
    the caller dedupes by variant set), each carrying every canonical digit
    reading a caller might find in an op's serialised JSON.
    """
    occurrences: list[_FigureOccurrence] = []
    for m in _DIGIT_RUN_RE.finditer(text):
        raw = m.group(0)
        has_separator = ("." in raw) or ("," in raw)
        digits_only = raw.replace(".", "").replace(",", "")
        if not has_separator and len(digits_only) < _MIN_INTEGER_DIGITS:
            continue

        variants = _canonical_digit_variants(raw)
        span_end = m.end()
        tail = text[span_end:span_end + 14]
        mag = _MAGNITUDE_RE.match(tail)
        verbatim_end = span_end
        if mag:
            factor = _MAGNITUDE_FACTORS.get(mag.group(1).lower().rstrip("."))
            if factor:
                variants |= _expand_with_magnitude(variants, factor)
                verbatim_end = span_end + mag.end()

        occurrences.append(
            _FigureOccurrence(
                span=text[m.start():verbatim_end].strip(),
                variants=frozenset(variants),
            )
        )
    return occurrences


def _ops_haystack(ops: Sequence[CommitOp], denials: Sequence[str]) -> str:
    """The ops' own serialised JSON PLUS the engine's denial list, as ONE
    normalised text — the corpus every figure/sentence check searches.
    Deliberately per-BATCH, not per-op: the contract asks whether ANY op
    carries the content, not which one (#370's Part B design).

    **Deviation from the literal Part B contract, flagged for the refutation
    pass:** the contract names only "the list of ops the engine produced" as
    input. A denial ("no blockchain experience") is real testimony that DID
    land — on `ReconcileResult.denials`, receipted by `commit_ops` via
    `record_denials` into `metadata.denied_concepts` — but it is a SEPARATE
    channel from `ops`, so a denial-only sentence shares no token with any op
    payload and would otherwise be reported as `not_applied` on every single
    denial-bearing submission (an `upsert_skill` for the affirmed half plus a
    denial for the negated half is the single most common testimony shape in
    this codebase's own fixtures). That would make `partial` fire
    constantly and for the wrong reason — a denial is not a loss, it is a
    different kind of landing. Folding `denials` into the haystack (as plain
    text, not run through figure/magnitude parsing — a denial is a token
    name, never a quantity) treats a receipted denial as "carried", exactly
    like an op payload.
    """
    payloads = []
    for op in ops:
        try:
            payloads.append(op.model_dump(mode="json"))
        except Exception:  # noqa: BLE001 — never let one odd op abort the witness
            continue
    haystack = json.dumps(payloads, ensure_ascii=False)
    if denials:
        haystack += " " + " ".join(str(d) for d in denials)
    return _norm_text(haystack)


def _figure_variant_pool(text: str) -> frozenset[str]:
    """Union of every canonical digit-variant reading found anywhere in
    ``text`` — used to turn the ops' haystack into a VALUE vocabulary rather
    than a literal string.

    A raw substring search would miss a testimony figure written
    "1,35 Mio" against an op that stored the same amount as "1350000" (or
    vice-versa a testimony "1350000" against an op bullet that kept
    "1,35 Mio EUR" verbatim, rule 12 of the reconcile prompt) — the two sides
    never share a literal substring even though they name the same number.
    Comparing by normalised VALUE (set intersection with `_extract_figures`'
    own variants) makes the match symmetric regardless of which side used
    which separator/magnitude convention.

    Known, accepted imprecision: this also extracts digit runs from
    non-figure fields (entity ids/refs), so a testimony figure can
    coincidentally "match" an unrelated id that happens to share a digit
    run. This is exactly the recall-over-precision trade-off the module
    docstring states — it can only ever cause a MISSED report (an id
    coincidentally rescuing a genuinely-lost figure), never a spurious one,
    and is left undocumented-narrower rather than parsing the JSON structure
    to exclude identifier-shaped keys (out of scope for #370's fix).
    """
    pool: set[str] = set()
    for occurrence in _extract_figures(text):
        pool |= occurrence.variants
    return frozenset(pool)


def compute_not_applied(
    text: str,
    ops: Sequence[CommitOp],
    *,
    rejected_ops: Sequence[str] = (),
    denials: Sequence[str] = (),
) -> list[NotApplied]:
    """Every span of ``text`` the ``ops``/``denials`` do not literally carry,
    plus every raw op ``rejected_ops`` names as parse-dropped.

    ``denials`` — ``ReconcileResult.denials`` — is folded into the "carried"
    corpus alongside the ops (see ``_ops_haystack``'s docstring for why: a
    receipted denial is a landed outcome, not a loss).

    Pure and side-effect-free: no DB, no LLM, no mutation of its arguments.
    See the module docstring for the exact algorithm, its FACT-only scope
    (ADR-062 clause 1), and its known blind spot (apply-time no-ops).
    """
    items: list[NotApplied] = []

    for op_type in rejected_ops:
        label = op_type if isinstance(op_type, str) and op_type else "<unknown>"
        items.append(
            NotApplied(span=label[:_SPAN_MAX_CHARS], kind="op", reason="op_rejected")
        )

    haystack = _ops_haystack(ops, denials)
    haystack_figures = _figure_variant_pool(haystack)

    seen_figures: set[frozenset[str]] = set()
    for occurrence in _extract_figures(text or ""):
        if occurrence.variants in seen_figures:
            continue
        if occurrence.variants & haystack_figures:
            continue
        seen_figures.add(occurrence.variants)
        items.append(
            NotApplied(
                span=occurrence.span[:_SPAN_MAX_CHARS],
                kind="figure",
                reason="figure_not_in_any_op",
            )
        )

    haystack_tokens = frozenset(_TOKEN_RE.findall(haystack))
    seen_sentences: set[str] = set()
    for sentence in _split_sentences(text or ""):
        tokens = _content_tokens(sentence)
        if not tokens:
            continue  # nothing to lose — a heading/punctuation-only "sentence"
        key = _norm_text(sentence)
        if key in seen_sentences:
            continue
        if tokens & haystack_tokens:
            continue
        seen_sentences.add(key)
        items.append(
            NotApplied(
                span=sentence.strip()[:_SPAN_MAX_CHARS],
                kind="sentence",
                reason="no_op_carried_it",
            )
        )

    return items
