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
reconcile ENGINE actually produced and reports testimony content that is not
literally present in any of them.

**ADR-062 clause 1 — a fact, never a judgement.** This module answers
exactly one question per span: "is this literally present in an op's own
serialised payload (or, for a rejected raw op, did it fail schema
validation)". It never asks "was this paraphrased", "did this matter", or
"should this have applied" — those are judgements, and a deterministic rule
may not make them. Consequently an item in the returned list is NOT proof
that content was lost — see "False-positive shapes" below for the figure
channel's own, documented ways of over-reporting. The witness is a
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

**Two checks, two reasons** (`schemas.testimony.NotApplied`):

(a) ``figure`` / ``figure_not_in_any_op`` — every numeric figure in the
    testimony (integers >= 2 digits, decimals, and a currency/percent/
    magnitude-word form folded to its digit string, e.g. "1,35 Mio" ->
    "1350000") whose normalised digit string appears in NO op's serialised
    JSON.
(b) ``op`` / ``op_rejected`` — every raw op the model emitted that
    `engine._parse_ops` dropped for failing schema validation, named by its
    own declared `"op"` type string (`rejected_ops`, an optional out-of-band
    list the caller threads through from `ReconcileResult.rejected_ops`).

Two more corpora fold into the "carried" haystack for (a), alongside the
ops (`_ops_haystack`'s docstring has the full detail on both):

* `denials` (`ReconcileResult.denials`) — a denied STATEMENT can still name a
  FIGURE ("nie ein Budget von 2,5 Mio verantwortet"), carried by the denial
  receipt, not by an op. Without the fold, every denial-bearing submission
  containing a number reads as `partial` for the wrong reason (a receipted
  denial is a landed outcome, not a loss).
* `vault_text` — the serialised PRE-TURN profile, CONTENT sections only —
  `metadata` excluded ENTIRELY, not merely filtered to the prompt's usual
  allowlist (see `_ops_haystack`, non-negotiable: `metadata.denied_
  concepts[*].statement` echoes a prior turn's raw testimony verbatim, so
  including it would rescue exactly the figures this fold exists to catch).
  A figure the testimony restates that the model correctly emitted NO op
  for, because the vault already held it unchanged, otherwise reads
  identically to a genuinely dropped figure — this was false-positive shape
  1 until `vault_text` closed it (real-provider replay, 2026-08-28: a
  2-sentence control resubmission of an already-landed "1,35 Mio EUR" came
  back `no_change` with the figure still flagged). The reason literal stays
  `figure_not_in_any_op` — the ADR-063 amendment fixes the two-reason
  vocabulary — but now means "in no op, no denial, AND no pre-turn vault
  content".

**Sentence-level loss is deliberately NOT reported — ADR-063 amendment
(item 7), dropped after a refutation pass.** An earlier version of this
module also reported a testimony SENTENCE as `not_applied` when it shared no
content token with any op's serialised payload. That check is REMOVED: ops
carry no source spans back to the testimony text, so "does this sentence
share a token with some op's field value" is not the fact it was labelled as
— it silently INHERITS the reconciler's own judgement calls on paraphrase
("led the Kubernetes migration" vs. the testimony's own wording), translation
(a German sentence reconciled into English field values, or vice versa), and
id-targeted merges (an op that legitimately merges into an EXISTING entity by
`target: <id>` need not restate that entity's name/company at all — the
merge's correctness is exactly the judgement ADR-046 already delegates to the
model). Token overlap dressed up "is this the same fact, reworded" as a
mechanical presence check, which is a judgement wearing a fact's label
(ADR-062 clause 1). This module's OWN denials-fold fix (found and pinned
during #370's build, see `_ops_haystack` below) is that exact false-positive
class caught red-handed: a denial-carried sentence shared no literal token
with any `op` payload — the content was genuinely carried (on a different
channel), yet the removed sentence check could not tell the difference
without being widened, case by case, indefinitely. The figure channel does
not have this problem in the same way: a digit string is either present in
the haystack or it is not, with no paraphrase axis to smuggle a judgement
through (the "false-positive shapes" below are a narrower, named, closed set,
not an open-ended "the reconciler reworded it" category).

**Deduplication.** Missing figures are reported once each (keyed by their
normalised reading), using the first verbatim occurrence as the span — an
explicit, documented choice to keep the report actionable on a long dossier
that repeats a figure, rather than one entry per occurrence.

**Figures are digit-only.** Spelled-out numbers ("zwölf", "twelve") are
deliberately NOT extracted as figures here (unlike some grounding checks
elsewhere in this package) — the contract's own examples are all digit-form,
and treating a spelled number as a citable figure would need the same
magnitude/compound-word machinery this module intentionally keeps narrow. A
spelled-out figure is invisible to this witness entirely (there is no
sentence-level fallback any more — see the ADR-063 amendment above).

**False-positive shapes of the figure channel** (read before treating a
`not_applied` item as proof of loss):

1. **A figure folded into prose under another form** this module's narrow,
   independent digit-variant generation (`_canonical_digit_variants`,
   `_expand_with_magnitude`) does not anticipate — a rounding, a different
   currency conversion, a magnitude word outside the small DE/EN table below,
   or any other restatement the model produced that is not one of the
   variants this module generates.
2. **The UUID-substring haystack imprecision** (`_figure_variant_pool`'s own
   docstring, kept in full below) — the haystack's figure-variant pool is
   built from EVERY digit run in the ops'/vault's serialised JSON, including
   entity `id`/`ref`/`target` values, so a testimony figure can coincidentally
   "match" an unrelated id. This can only ever cause a MISSED report, never a
   spurious one — the opposite direction from shape 1 above.

**Closed: "a figure the vault already held."** This witness used to see only
`ops` (the CURRENT batch) and `denials` — never the profile/vault state — so
a figure the testimony restated, for which the model correctly emitted NO op
because the vault already held it unchanged, read identically to a genuinely
dropped figure. `vault_text` (folded into the haystack alongside `denials`,
see above and `_ops_haystack`) closes this: the pre-turn profile's own
model-facing CONTENT is now part of the "carried" corpus, so a figure
already attested there is no longer reported. Still narrower than a full fix
would need in principle — `vault_text` is a snapshot of the profile as
loaded BEFORE this turn's ops are applied, so it cannot see a figure this
SAME batch's ops are about to newly write (that case is already covered by
the `ops` haystack itself, so the two together are exhaustive for "was this
figure a fact anywhere at the end of this turn").

**CONTENT, not bookkeeping — `metadata` is excluded from `vault_text`.**
`metadata.denied_concepts[*].statement` is the RAW TEXT of a prior turn's
testimony, verbatim — including every figure the model read and correctly
DID NOT turn into a fact. Folding that in would rescue exactly the figures
this witness exists to catch (a real-provider replay, 2026-08-28, found this
live: five denial statements each echoing the whole prior ~10.5 KB dossier).
`vault_text` must be built via `prompt_profile_view(profile_json,
keep=frozenset())` — content sections only, no allowlisted metadata keys —
never the prompt's own default view. See `_ops_haystack`'s docstring for the
full reasoning and the `prompts/gap_analysis` precedent this mirrors.

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


def _norm_text(text: str) -> str:
    """NFKC-normalise then casefold — the same "normalise before matching"
    discipline the rest of the codebase applies (U+2019 lesson)."""
    return unicodedata.normalize("NFKC", text or "").casefold()


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


def _ops_haystack(
    ops: Sequence[CommitOp], denials: Sequence[str], vault_text: str
) -> str:
    """The ops' own serialised JSON, PLUS the engine's denial list, PLUS the
    pre-turn vault text, as ONE normalised corpus the figure check searches.
    Deliberately per-BATCH, not per-op: the contract asks whether ANY op
    carries the content, not which one (#370's Part B design).

    **Why `denials` folds in, even with the sentence channel gone:** a denied
    STATEMENT can itself name a FIGURE — "Ich habe nie ein Budget von 2,5
    Mio verantwortet" — and that figure is carried by the denial receipt
    (`ReconcileResult.denials`, written by `commit_ops` via `record_denials`
    into `metadata.denied_concepts`), not by any op. Without this fold, the
    figure channel would flag "2,5 Mio" as `figure_not_in_any_op` on every
    denial naming a number — a receipted denial is a landed outcome, not a
    loss. (This is also the exact false-positive class that got the REMOVED
    sentence channel refuted — see the module docstring's ADR-063 amendment
    note — but the figure channel's narrower, digit-only comparison is not
    itself the paraphrase/translation/id-merge problem that check had; the
    fold here is just making sure the "carried" corpus is complete, not
    working around a judgement smuggled into the comparison.)

    **Why `vault_text` folds in:** a figure the testimony restates that the
    model correctly emitted NO op for — because the vault ALREADY held it,
    unchanged — must not read as lost either (module docstring, "Closed: a
    figure the vault already held"). The caller passes an already-serialised
    STRING (this module stays profile-schema-agnostic, matching `CommitOp`
    being the only vault-shaped type it otherwise knows about) — the
    PRE-TURN profile, as a model-facing view with bookkeeping excluded
    (`services.prompt_view.prompt_profile_view`).

    **`metadata` MUST be excluded from `vault_text` — not merely filtered to
    the prompt's usual allowlist** (real-provider replay, 2026-08-28):
    `metadata.denied_concepts[*].statement` stores the PRIOR turn's entire
    raw testimony text verbatim, so a figure the model correctly DROPPED on
    an earlier turn — never written to any content field — still echoes
    inside that statement string. Folding an allowlisted `metadata` in (the
    prompt's own default) would make every such figure read as "already
    held" on a later resubmission, exactly the false positive this fold
    exists to close, reintroduced through a different door. Callers MUST
    pass `prompt_profile_view(profile_json, keep=frozenset())` — `prompts/
    gap_analysis` set this precedent first, for the identical shape (a
    denial's own text token-matches FOR the thing it denies). Bookkeeping is
    never content, however plainly it repeats one. An empty `vault_text` is
    a no-op fold, so callers with no profile in scope (the unit tests below)
    are unaffected.
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
    if vault_text:
        haystack += " " + vault_text
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

    Known, accepted imprecision (false-positive shape 3 in the module
    docstring): this also extracts digit runs from non-figure fields (entity
    ids/refs), so a testimony figure can coincidentally "match" an unrelated
    id that happens to share a digit run. This can only ever cause a MISSED
    report (an id coincidentally rescuing a genuinely-lost figure), never a
    spurious one, and is left as-is rather than parsing the JSON structure to
    exclude identifier-shaped keys (out of scope for #370's fix).
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
    vault_text: str = "",
) -> list[NotApplied]:
    """Every figure of ``text`` the ``ops``/``denials``/``vault_text`` do not
    literally carry, plus every raw op ``rejected_ops`` names as parse-dropped.

    ``denials`` (``ReconcileResult.denials``) and ``vault_text`` (the
    serialised PRE-TURN profile, CONTENT sections only — callers MUST exclude
    ``metadata`` entirely, see ``_ops_haystack``) both fold into the
    "carried" corpus alongside the ops — see ``_ops_haystack``'s docstring
    for why each is needed: a denied STATEMENT can itself name a figure, and
    a figure the vault already held needs no op to still count
    as carried.

    Pure and side-effect-free: no DB, no LLM, no mutation of its arguments.
    See the module docstring for the exact algorithm, its FACT-only scope
    (ADR-062 clause 1), why sentence-level loss is deliberately NOT checked
    (ADR-063 amendment), and the figure channel's own false-positive shapes.
    """
    items: list[NotApplied] = []

    for op_type in rejected_ops:
        label = op_type if isinstance(op_type, str) and op_type else "<unknown>"
        items.append(
            NotApplied(span=label[:_SPAN_MAX_CHARS], kind="op", reason="op_rejected")
        )

    haystack = _ops_haystack(ops, denials, vault_text)
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

    return items
