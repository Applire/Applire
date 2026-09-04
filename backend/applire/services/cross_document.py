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
the cover-letter call site (Fix A — the regression fix), (b) hands the writer
the candidate's STATED LIMITS verbatim (:func:`collect_stated_limits`), and
(c) finds JD hard requirements the letter never addresses at all
(:func:`find_unaddressed_hard_requirements`). Everything here flags and
instructs — none of it rewrites prose (the guardrail: never make a gap sound
smaller than it is, never make a claim sound more precise than it is).

**ADR-062 (2026-07-28) governs what may live in this module.** Every function
here computes a FACT — a ledger status, an exact-equality match, a literal
surface-form presence check via the shared ``surface_present`` predicate, a
verbatim quote from the vault. None of them judges what a sentence MEANS.
That line is the whole point of the module's current shape, because it did not
hold before, and the two things it lost are worth naming so neither grows back.

*Deleted 2026-07-28, charter run #8 — the boundary matcher.*
``ScopedBoundary`` / ``find_scoped_boundaries`` / ``render_scoped_boundary_block``
and the ``unqualified_cv_vs_scoped_letter`` conflict kind (#277) built on them
decided which claimable concept a vault denial "limits", by testing text
overlap between the two. On real data that signal runs BACKWARDS: an honest
denial names the adjacent strengths that transfer, so the concepts it overlaps
hardest are precisely the ones it does not limit. Four boundaries emitted on
the run-8 vault, four false, and the writer — ordered to render "both halves"
for each — invented limits on the candidate's strongest evidence. See
:func:`collect_stated_limits`.

*Deleted 2026-07-28, same run — the negation matcher.*
``_is_negated_clause`` / ``_negation_attached_to_form`` / ``_bounded_spans`` /
``_is_specific_enough``, and the ``find_cross_document_conflicts`` findings
(``bare_denial_of_claimable``, ``assert_vs_deny``) they produced, asked "does
this clause DENY this concept?" — a question about meaning — with a proximity
rule: is a negation token within ``_NEGATION_ATTACH_WINDOW = 6`` word-tokens of
the concept. It had already been narrowed twice after real-model incidents
(#207 word boundaries after ``'ai' ⊂ 'domain'``; #278 the attachment window and
a specificity floor), which is the shape of a dependency parser being
reinvented one incident at a time. It cannot converge, because syntactic scope
is not a distance: German ``nicht …, doch/aber …`` closes negation at the
comma, and measured across the construction three of four contrastive transfer
arguments (DE ``doch``, DE ``aber``, EN ``while``) were read as denials — the
control fired on the honest output this product exists to produce. In run #8 it
flagged a sentence AFFIRMING ``Digitalisierung`` as a bare denial of it, and
the reviewer, told the flag was ground truth, could never approve; the loop ran
to exhaustion for ten rounds.

What replaced the second one is not code. The claimable concepts and their
evidence already reach the reviewer through the Keyword Ledger block
(``keyword_ledger.render_ledger_prompt_block``), which states the honest-gap
rule correctly and even handles the adjacent-partial case. The reviewer also
already holds both documents. So the cross-document rule is stated once, in
``prompts/review_cover_letter.py``, and the model applies it — per ADR-062
clause 2, the facts plus one rule.
"""
from __future__ import annotations

import logging
import unicodedata
from typing import Any

from applire.services.ats_audit import _norm as ats_norm
from applire.services.ats_audit import surface_present

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


# ── Fix B.1 — stated limits (was: scoped boundaries) ─────────────────────────


def collect_stated_limits(denied_concepts: list[Any] | None) -> list[str]:
    """The candidate's verbatim denial statements, deduped, order preserved.

    Replaces ``find_scoped_boundaries`` (deleted 2026-07-28, charter run #8).
    That function tried to decide WHICH claimable concept each denial limits,
    by testing whether the denial's text and the ledger entry's text share a
    surface form. Text overlap cannot answer that question, and on real data
    it answers it *backwards*:

    An honest denial statement names the candidate's adjacent STRENGTHS,
    because that is what an honest denial sounds like — "no IFS/BRC
    experience, but ten years of ISO-9001 audit practice". So the strongest
    overlap signal is produced by exactly the concepts the denial does NOT
    limit. Run-8 ground truth (``backend/logs/llm/2026-07-28.jsonl``,
    ``operations_marcus_de``): four boundaries emitted, all four false —
    ``ISO 9001``, ``Produktion``, ``Supply Chain``, ``Qualität``, each one a
    load-bearing strength. For ``Supply Chain`` and ``Qualität`` the rendered
    "POSITIVE evidence" and "STATED LIMIT" were the *same clause quoted
    twice*, and the writer was nonetheless instructed to name "both halves" —
    so it manufactured a limit that does not exist, and the delivered letter
    denied the candidate's own strongest evidence.

    The vault knows two things for certain: which concepts are claimable (the
    ledger) and what the candidate said they cannot claim (these statements).
    The *relation* between the two is a question about meaning, so it belongs
    to the model, not to a matcher — see :func:`render_stated_limits_block`,
    which hands over both facts and one rule. Pure; ``None``/empty tolerant.
    """
    seen: set[str] = set()
    out: list[str] = []
    for denial in denied_concepts or []:
        if isinstance(denial, str):
            text = denial
        else:
            text = _get(denial, "statement", "") or _get(denial, "concept", "") or ""
        text = (text or "").strip()
        if not text:
            continue
        key = ats_norm(text)
        if key in seen:
            continue  # one interview answer is persisted once per concept it denies
        seen.add(key)
        out.append(text)
    return out


# ── Fix B.3 — unaddressed hard requirements ─────────────────────────────────

_MAX_UNADDRESSED_REPORTED = 3


def find_unaddressed_hard_requirements(
    keyword_ledger: list[dict[str, Any]] | None,
    letter_data: dict[str, Any] | None,
    *,
    cap: int = _MAX_UNADDRESSED_REPORTED,
) -> list[dict[str, Any]]:
    """JD hard requirements (``"required" in sources``) that need an explicit
    positioning decision and whose surface forms appear NOWHERE in the letter
    body.

    Two kinds qualify (ADR-048 amended 2026-07-27, clause 6):

      * ``claimable: false`` — a genuine honest gap, whether ``gap`` (nobody
        knows) or ``denied`` (the candidate said no in their own words);
      * an ADJACENT ``partial`` — claimable, but only because the candidate has
        a *different* capability standing in for the named one. It reads as
        "covered" to every other mechanism while the JD's actual requirement is
        unmet, so it needs positioning just as much: promote the adjacent
        capability, never assert the JD's term. A below-the-bar ``partial``
        (no pointer) is genuinely the named skill and is deliberately excluded.

    Capped at the ``cap`` (default 3) highest ``fit_weight`` entries — this
    never silently truncates: every dropped entry is logged at ``info``
    level. Pure, deterministic; ``None``/empty/malformed-shape tolerant.
    """
    body = _get(letter_data or {}, "body", {}) or {}
    paragraphs = _get(body, "paragraphs", None)
    text_norm = ats_norm(" ".join(p for p in (paragraphs or []) if isinstance(p, str)))

    from applire.services.keyword_ledger import is_scope_entry, is_unasked_requirement

    unaddressed: list[dict[str, Any]] = []
    for entry in keyword_ledger or []:
        if not isinstance(entry, dict):
            continue
        # ADR-070 clause 5: a scope entry's concept label embeds the JD's own
        # number — rendering it here would put that figure into the letter
        # prompt (a scope `gap` is claimable:false + required, so it WOULD
        # qualify below). Scope positioning is owned solely by
        # render_scope_positioning_block; a persistent scope gap is positioned
        # nowhere, deliberately (ADR-070's explicit limitation).
        if is_scope_entry(entry):
            continue
        # ADR-074 (#526): a hard requirement we hold NOTHING on and never asked
        # about is ignored at generation. Every remaining move is a truthfulness
        # defect — asserting the term is ungrounded, denying it invents a limit
        # the candidate never stated, and this block's own instruction forbids
        # silence — so the honest one is to write the letter as though the
        # requirement had not been named, and tell the CANDIDATE instead
        # (GapAnalysisResponse.unasked_requirements). Gate charter run 1 spent
        # ten reviewer rounds and 37 of 68 blocking issues discovering that this
        # cell has no fourth move.
        if is_unasked_requirement(entry):
            continue
        if entry.get("claimable") and not entry.get("adjacent_evidence"):
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


# ── denial transfer bridges — DELETED 2026-07-28 (ADR-062) ──────────────────
# `find_denial_transfer_bridge` extracted the "what I do bring instead" half out
# of a persisted denial statement: take the statement's LAST sentence, but only
# if some sentence in the statement is negated, and the last one is not, and it
# does not match an availability phrase. Position plus three prose guards, all
# answering "which part of this paragraph is the transfer argument?" — a
# question about meaning, and therefore the model's under ADR-062 clause 1.
#
# Nothing replaces it, because nothing needs to: `render_stated_limits_block`
# already hands the writer, the reviewer and the corrector the ENTIRE denial
# statement verbatim, transfer argument included. Extracting one sentence from
# a paragraph the model can already read was never buying anything except a way
# to pick the wrong sentence. The UNADDRESSED HARD REQUIREMENTS block now points
# at the statements instead of quoting a span out of them.

# ── shared wording — #270(c): every unmet hard requirement gets a decision ──
# The permitted responses are EXACTLY two — a transfer argument grounded in
# the candidate's OWN testimony, or a brief, honest de-emphasis that names
# the gap without dwelling on it. Never an assertion (these concepts are
# ``claimable: false`` by construction — find_unaddressed_hard_requirements
# only ever selects honest gaps), never a softened/vaguer denial, and never
# a litany — every response folds into the SAME single honest-gap paragraph.
_UNADDRESSED_INSTRUCTION = (
    "JD hard requirements the letter does not mention anywhere. The candidate "
    "does NOT have the named requirement itself — either the Keyword Ledger "
    "marks it unclaimable, or it is claimable only through a DIFFERENT, "
    "adjacent capability named below. Never assert the requirement's own term "
    "as something they have, have done, or know. "
    "For each, make an explicit positioning decision: a transfer argument "
    "grounded in the candidate's own words, or a brief, honest de-emphasis "
    "that names the gap without dwelling on it or implying a JD-critical "
    "requirement is negligible. Silence is not one of the options. "
    "Where a STATED LIMITS block is present, the candidate's own wording for "
    "the transfer argument is in it, verbatim — read it there and build on it; "
    "an honest denial normally states the gap and the adjacent strength in one "
    "breath, and that is the shape to reproduce, gap acknowledged first. "
    "Fold every response into the SAME single honest-gap paragraph — never a "
    "litany of separate gap admissions. "
    "A concept NOT listed here is not a gap: if the Keyword Ledger marks it "
    "claimable with no adjacent-capability note, it is supported by the vault "
    "and must be claimed plainly, never hedged."
)


def render_unaddressed_hard_requirements_block(
    entries: list[dict[str, Any]],
) -> str:
    """Render unmet JD hard requirements (#270(c)) as a deterministic block.

    Dual use, same rendering both times:
      * WRITER (pre-draft): the caller passes ``find_unaddressed_hard_
        requirements(keyword_ledger, None)`` — before any letter exists every
        required honest gap is trivially "unaddressed", so the writer gets
        the same top-``cap`` list its first draft will later be re-checked
        against (a chance to get it right without a correction round).
      * REVIEWER (post-draft, via :func:`unaddressed_requirements_reviewer_prompt_fn`):
        recomputed against the CURRENT draft each iteration — the block
        disappears once the writer/corrector has given each concept its
        positioning decision, the same convergence signal
        ``keyword_ledger.coverage_reviewer_prompt_fn`` already uses.

    The ``denied_concepts`` parameter was **deleted 2026-08-13** (ADR-062
    clause 3). It had been accepted and unused since 2026-07-28: the candidate's
    own transfer-argument wording reaches the prompt whole via the STATED LIMITS
    testimony line, so nothing here ever read it. A parameter every caller
    passes and no body consults is a control that cannot fire.

    Returns ``""`` when ``entries`` is empty so a fully-addressed draft (or a
    JD with no unmet hard requirements) adds nothing.
    """
    if not entries:
        return ""
    # ADR-084 embedding point 20 (Form B): every `concept` is a JD hard
    # requirement and every `context` is the classifier's free text about it —
    # both the posting's derivatives, reaching the letter writer AND (through
    # `unaddressed_requirements_reviewer_prompt_fn`) its reviewer.
    from applire.services.untrusted_text import items_note

    lines = [
        "=== UNADDRESSED HARD REQUIREMENTS (deterministic — #270(c)) ===",
        _UNADDRESSED_INSTRUCTION,
        items_note("requirement terms and context quotes"),
    ]
    for e in entries:
        # ADR-074 / ADR-062 clause 3: the no-vault-context fallback string is
        # DELETED. `find_unaddressed_hard_requirements` now excludes the only
        # rows that could reach it, so it was a branch no input can select — and
        # while it existed it was this block admitting it had nothing to offer
        # while still instructing the writer to produce a transfer argument
        # grounded in the candidate's own words. That contradiction is what ran
        # both letter loops to exhaustion in gate charter run 1.
        evidence = e.get("evidence", "") or e.get("adjacent_evidence", "")
        lines.append(f"  - {e.get('concept', '')} — context: {evidence}")
        if e.get("status") == "denied":
            lines.append(
                "    THE CANDIDATE WAS ASKED AND STATED THEY DO NOT HAVE THIS. It is "
                "their own position, not an unknown — name it plainly if you name it "
                "at all, and never soften or walk it back."
            )
        if e.get("adjacent_evidence"):
            lines.append(
                "    ADJACENT CAPABILITY IN THE VAULT: "
                f"{e['adjacent_evidence']} — the candidate does NOT have "
                f"{e.get('concept', '')} itself. Give "
                f"{e['adjacent_evidence']} prominence on its own merits; never "
                "assert the requirement's own term as something they have."
            )
    return "\n".join(lines)


# ── unaddressed_hard_requirements_positioning — DELETED 2026-08-13 (ADR-021) ──
# It built the ``positioning_requested['unaddressed_hard_requirements']`` entry:
# the top-cap list of unmet hard requirements, snapshotted into the letter's
# ``grounding_source`` so the reviewer and corrector would treat each concept's
# positioning sentence as REQUIRED content (#270(c), on the #255 pattern).
#
# The #255 pattern is right for a STANDING obligation and wrong for this one.
# ``review_and_refine`` builds ``source`` once and hands it, unchanged, to the
# reviewer AND the corrector on every round — so this entry was computed with
# ``letter_data=None`` (no draft existed yet) and then asserted as current for
# the whole loop. Gate charter run 1 measured the collision: in two rounds
# :func:`unaddressed_requirements_reviewer_prompt_fn` correctly emitted nothing
# — the draft addressed both concepts — while the frozen entry in the SAME
# prompt still demanded them, and reviewer check 4 reads the frozen one.
#
# Nothing replaces it. Across the same ten rounds the frozen entry never
# produced corrector content the reviewer's own feedback had not already
# demanded; the corrector dropped both concepts entirely in three rounds with
# "Silence is not one of the options" verbatim in its prompt; and the delivered
# letter never named one of them while the entry still read ``required: true``.
# A per-round corrector twin (the #306 shape) was considered and rejected on the
# same evidence: in the one round that destroyed a correct honest-gap sentence,
# the recomputed list was EMPTY, because the draft still carried both terms — a
# recomputed block cannot retain what it can no longer see.
#
# ADR-021 amended 2026-08-13 states the general rule for all eight call sites:
# an input that is an assertion ABOUT THE CURRENT DRAFT belongs in a per-round
# prompt wrapper, never in ``source``.


# ── render helpers ───────────────────────────────────────────────────────────


def render_stated_limits_block(limits: list[str]) -> str:
    """Render the candidate's verbatim stated limits for a WRITER prompt.

    Facts, plus the one rule that keeps them from being over-read. Nothing here
    pairs a limit with a concept — that judgement is the model's (see
    :func:`collect_stated_limits` for why the deterministic pairing was removed).

    Returns ``""`` when empty so a vault with no denials adds nothing.
    """
    if not limits:
        return ""
    lines = [
        "=== STATED LIMITS (the candidate's own words, verbatim) ===",
        "In an interview the candidate said each of the following about what they "
        "cannot claim. These are the ONLY limits the vault holds.",
        "  1. Never write a claim one of these statements contradicts.",
        "  2. Never manufacture a limit they do not state. A concept named INSIDE one "
        "of these statements as something the candidate DOES have is a STRENGTH, not "
        "a limit — an honest denial names the adjacent strengths that transfer.",
        "Everything the Keyword Ledger marks claimable stays fully claimable unless a "
        "statement below denies it. When in doubt, claim it plainly and without "
        "qualification: an invented limit is exactly as untrue as an invented claim, "
        "and it costs the candidate their own best evidence.",
    ]
    lines.extend(f"  - {text}" for text in limits)
    return "\n".join(lines)


# ── reviewer wrapper ─────────────────────────────────────────────────────────


def unaddressed_requirements_reviewer_prompt_fn(
    base_fn: Any,
    *,
    keyword_ledger: list[dict[str, Any]] | None,
):
    """Wrap a ``reviewer_prompt_fn`` so every ADR-021 iteration carries the JD
    hard requirements the CURRENT draft has not addressed (#270(c)).

    Composes with (does not replace) ``keyword_ledger.coverage_reviewer_prompt_fn``
    — the established pattern (US213/#122): ``review_and_refine`` calls
    ``reviewer_prompt_fn(source, draft)`` fresh each iteration, so the list is
    recomputed against the LATEST draft and disappears once the corrector has
    addressed a concept. Deterministic convergence riding the existing bounded
    loop; no new loop, no new LLM pass.

    ADR-062 classification: **fact.** Presence of a surface form in the draft
    body is a coverage question, answered by the shared literal predicate
    (``surface_present``) over a ledger status. It never judges what a sentence
    means.

    This was ``cross_document_reviewer_prompt_fn`` until 2026-07-28. It also
    appended ``find_cross_document_conflicts`` output — bare-denial and
    assert-vs-deny findings derived from ``_negation_attached_to_form`` — which
    ADR-062 removed as a judgement dressed as a fact. See the module docstring.
    """

    def fn(source: str, draft: dict[str, Any]) -> str:
        prompt = base_fn(source, draft)
        unaddressed = find_unaddressed_hard_requirements(keyword_ledger, draft)
        block = render_unaddressed_hard_requirements_block(unaddressed)
        if block:
            logger.info(
                "unaddressed hard requirements: %d concept(s) missing from "
                "current letter draft: %s",
                len(unaddressed), [e.get("concept", "") for e in unaddressed],
            )
            prompt = f"{prompt}\n\n{block}"
        return prompt

    return fn
