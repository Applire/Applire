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
from dataclasses import dataclass
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

    Since G-1 (ADR-075 amended 2026-11-04) ONE kind qualifies: an ADJACENT
    ``partial`` — claimable, but only because the candidate has a *different*
    capability standing in for the named one. It reads as "covered" to every
    other mechanism while the JD's actual requirement is unmet: promote the
    adjacent capability, never assert the JD's term, never state it as a lack
    (DEL-2). A below-the-bar ``partial`` (no pointer) is genuinely the named
    skill and is deliberately excluded. A ``claimable: false`` row (``gap`` /
    ``denied``) is a GAP and belongs to :func:`key_requirement_gaps` alone.

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
        # G-1 (ADR-075 amended 2026-11-04): a NOT-claimable row is a gap, and gaps
        # are counted, ranked and named by `key_requirement_gaps` alone. Listed
        # here per concept, a denied row re-demanded each concept of a statement
        # the letter had already positioned (founder UAT 11-04: one concept per
        # reviewer round) — the per-concept enumeration MD6-4 forbids. What is
        # left is the ADJACENT partial: claimable through a stand-in (DEL-2).
        if not (entry.get("claimable") and entry.get("adjacent_evidence")):
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
# The permitted responses are a transfer argument grounded in the candidate's
# OWN testimony and — ONLY where the candidate was asked and said no — a
# brief, honest de-emphasis that names the gap without dwelling on it. Never
# an assertion, never a softened/vaguer denial, and never a litany — every
# response folds into the SAME single honest-gap paragraph.
#
# #664 (2026-09-11): the comment that stood here claimed "these concepts are
# ``claimable: false`` by construction — find_unaddressed_hard_requirements
# only ever selects honest gaps". That was FALSE about its own function, which
# deliberately also selects ADJACENT PARTIALS (claimable through a different
# capability, the ``adjacent_evidence`` branch below). On the 2026-09-05
# delivery run `Qualitätsmanagement` reached the same corrector prompt twice:
# in the VERIFIED COVERAGE CHECK as claimable-and-missing, and here as a gap
# owing a positioning decision. The corrector chose the de-emphasis this
# instruction offered — "Qualitätsmanagement beanspruche ich nicht" — the next
# round's check 1 called it an invented limit, and the letter shipped it. An
# adjacent partial is not something the candidate said they lack, so the
# de-emphasis is not one of ITS options; the per-entry line below says so
# where the entry is rendered, rather than here, so the rule and the entry
# cannot drift apart (ADR-075 amended 2026-09-11).
_UNADDRESSED_INSTRUCTION = (
    "JD hard requirements the letter does not mention anywhere, each claimable only "
    "through a DIFFERENT, adjacent capability named below — the candidate does NOT "
    "have the named requirement itself. Never assert the requirement's own term as "
    "something they have, have done, or know, and never state it as something they "
    "lack either (ADR-076, RULING DEL-2): the letter may give the adjacent capability "
    "prominence on its own merits, in the candidate's own words, or leave the "
    "requirement out. A requirement listed here that the letter leaves out is NOT a "
    "finding. Gaps the letter names are listed ONLY in the KEY-REQUIREMENT GAPS "
    "content (RULING G-1), never here. "
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
        if e.get("adjacent_evidence"):
            lines.append(
                "    ADJACENT CAPABILITY IN THE VAULT: "
                f"{e['adjacent_evidence']} — the candidate does NOT have "
                f"{e.get('concept', '')} itself. Give "
                f"{e['adjacent_evidence']} prominence on its own merits; never "
                "assert the requirement's own term as something they have — and "
                "never state it as something they LACK either. They were not "
                f"asked about {e.get('concept', '')} and did not deny it, so a "
                "sentence disclaiming it is an INVENTED LIMIT (check 1), and it "
                f"throws away the {e['adjacent_evidence']} evidence the vault "
                "does hold. Surfacing it is the only positive move; leaving the "
                "requirement out is the other (RULING DEL-2)."
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


#: ADR-075 clause 4 — at most this many limits reach the letter, ranked by the
#: ledger's own ``fit_weight``. Deliberately the same number as
#: ``_MAX_UNADDRESSED_REPORTED`` above: a candidate with many denials must not
#: turn their letter into a list of things they cannot do.
_MAX_STATED_LIMITS_REPORTED = 3


def select_jd_relevant_limits(
    denied_concepts: list[Any] | None,
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """The candidate's own denial statements **this posting asks about** (ADR-075).

    ADR-075 clause 3: *selection is a FACT; the wording is a JUDGEMENT.* This
    function answers only the fact — which denials are relevant here — from two
    persisted, deterministic signals:

    * a ``DeniedConcept`` record (``metadata.denied_concepts``), whose
      ``statement`` is write-once from the candidate's own interview turn (#348);
    * a Keyword-Ledger row for THIS job with ``status == "denied"``, which
      ``keyword_ledger._denied_row`` writes unconditionally for a concept the
      candidate refused (ADR-059 amended 2026-07-27: the floor writes the
      STATUS, not merely the flag, precisely so a refusal stays distinguishable
      from a requirement nobody asked about).

    Ranking is the ledger's own ``fit_weight`` (``required`` 1.0 >
    ``nice_to_have`` 0.5 > …) — **any tier qualifies**, which is the gap ADR-075
    exists to close: IFS/BRC were classified ``nice_to_have_skills`` at job
    analysis, so `find_unaddressed_hard_requirements`' ``"required" in sources``
    gate could never reach them.

    What this function deliberately does NOT do:

    * it does not pair a limit with the strength that offsets it. That question
      was deleted on 2026-07-28 (``find_scoped_boundaries``) after answering it
      *backwards* on real data, and it stays deleted — an honest denial names
      its own adjacent strengths, so text overlap is strongest for exactly the
      concepts the denial does not limit. See :func:`collect_stated_limits`.
    * it does not reopen ADR-074. A *Restfall* (``gap``, empty ``evidence``,
      nobody ever asked) has no honest move and carries no denial record, so it
      cannot match here — ADR-074 clause 1's ``status != "denied"`` conjunct and
      this predicate are complements, not overlaps.

    Pure; ``None``/empty tolerant. Truncation is logged at INFO on the #264
    pattern, PII-free (counts and concepts, never the statement).
    """
    denied_rows: dict[str, float] = {}
    for entry in keyword_ledger or []:
        if not isinstance(entry, dict) or entry.get("status") != "denied":
            continue
        concept = (entry.get("concept") or "").strip()
        if not concept:
            continue
        weight = entry.get("fit_weight")
        weight = float(weight) if isinstance(weight, (int, float)) else 0.0
        key = ats_norm(concept)
        denied_rows[key] = max(denied_rows.get(key, 0.0), weight)
        # ADR-100 M-2 (WP2, 2026-11-04): a row the classifier bound to a record
        # by id joins that record under the record's own concept — the posting's
        # term and the candidate's words may be in different languages
        # ("Reference designs" bound to "Referenzdesigns").
        bound = entry.get("denied_by")
        if isinstance(bound, str) and bound.strip():
            bkey = ats_norm(bound.strip())
            denied_rows[bkey] = max(denied_rows.get(bkey, 0.0), weight)
    if not denied_rows:
        return []

    ranked: list[tuple[float, int, str, str]] = []
    seen: set[str] = set()
    for position, denial in enumerate(denied_concepts or []):
        concept = (_get(denial, "concept", "") or "").strip()
        statement = (_get(denial, "statement", "") or "").strip()
        if not concept or not statement:
            continue
        weight = denied_rows.get(ats_norm(concept))
        if weight is None:
            continue  # the candidate said no about something this posting
            # never asked for — not this letter's business
        key = ats_norm(statement)
        if key in seen:
            continue  # one interview answer is persisted once per concept
        seen.add(key)
        # negative weight so the sort is (strongest first, then intake order)
        ranked.append((-weight, position, concept, statement))

    ranked.sort()
    kept = ranked[:_MAX_STATED_LIMITS_REPORTED]
    dropped = ranked[_MAX_STATED_LIMITS_REPORTED:]
    if dropped:
        logger.info(
            "LETTER_STATED_LIMITS_TRUNCATED kept=%d dropped=%d dropped_concepts=%s",
            len(kept), len(dropped), [c for _, _, c, _ in dropped],
        )
    return [statement for _, _, _, statement in kept]


# ── G-1: the key-requirement gap ladder (ADR-075 amended 2026-11-04) ─────────
#
# Founder UAT 2026-11-04: the letter delivered every concept of one recorded
# statement as a single "… remain beyond the experience recorded in my profile"
# list. The writer's first draft named ONE gap
# with its transfer; the reviewer then demanded the rest of the one statement
# (recs 26/28) because the REQUIRED stated-limits entry read "each one gets ONE
# positioning decision … silence is not one of the options" over a statement that
# names seven concepts. Replay (luna, n=5 per arm): the per-concept ledger rows
# were not the cause (1/10 captured, 1/10 without them in the UNADDRESSED block,
# 2/10 without them in the profile too) — the obligation's unit was.
#
# G-1 moves the unit into code. Which requirements are gaps, how they are
# counted, which one is most urgent and how many are named are FACTS (ADR-062
# clause 1, computed here, one implementation for writer, reviewer, corrector and
# the limit fact); whether a transfer exists and the sentence are the model's.


@dataclass(frozen=True)
class KeyGap:
    """One key-requirement gap — the unit G-1 counts and names.

    ``label`` is the concept the letter names; ``concepts`` every ledger concept
    the unit covers (one recorded statement naming several concepts is ONE gap,
    MD6-4); ``statement`` the candidate's own words when the gap is recorded."""

    label: str
    concepts: tuple[str, ...]
    statement: str | None
    weight: float
    mentions: int
    first_position: int
    #: RULING D-2 = A: a concept of the posting's candidate-requirements section;
    #: then ``mentions``/``first_position`` count within that section (D-6 = A).
    in_requirements: bool = False

    @property
    def recorded(self) -> bool:
        return bool(self.statement)


#: G-1: up to this many gaps are each named; from one more on only the most urgent.
GAP_LADDER_NAME_ALL_UP_TO = 2

_NO_POSITION = 10**9


def _token_bounded(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    return not before.isalnum() and not after.isalnum()


def posting_mentions(forms: list[str], jd_norm: str) -> tuple[int, int]:
    """How often the posting names any of ``forms`` (distinct whole-token start
    positions over the shared singular/plural fold) and where it first does.

    A FACT about the posting text — the ranking signal G-1's "most urgent" uses
    (RULING L-1 = B): every key row weighs 1.0, so the order inside
    the key tier comes from the posting itself. ``(0, _NO_POSITION)`` when absent."""
    from applire.services.ats_audit import _fold_variants

    starts: set[int] = set()
    for form in forms:
        n = ats_norm(form or "")
        if not n:
            continue
        for variant in _fold_variants(n):
            i = jd_norm.find(variant)
            while i >= 0:
                if _token_bounded(jd_norm, i, i + len(variant)):
                    starts.add(i)
                i = jd_norm.find(variant, i + 1)
    if not starts:
        return 0, _NO_POSITION
    return len(starts), min(starts)


# RULING D-2 = A (founder 2026-11-04, delivery run build 6): a gap from the
# posting's requirements of the CANDIDATE ("Your profile", "A Perfect Match",
# "Ihr Profil") ranks ahead of one that occurs only in the responsibilities. The
# job analysis does not keep the two apart (Corvalis: "Capacity management", a
# responsibilities line, sits in ``required_skills`` beside the must-have
# SnapLogic), so the section is read off the posting's own headings — a fact
# about the text (ADR-062 cl. 1). Unrecognised layout → no section → L-1's order.
_REQUIREMENTS_HEADINGS = (
    "your profile", "your qualifications", "your skills", "your experience", "profile",
    "requirements", "qualifications", "minimum qualifications", "basic qualifications",
    "required qualifications", "required skills", "must have", "must-haves",
    "what you bring", "what you'll bring", "what you will bring", "what you need",
    "what we're looking for", "what we are looking for", "who you are", "about you",
    "a perfect match", "skills and experience", "your background",
    "ihr profil", "dein profil", "profil", "anforderungen", "anforderungsprofil",
    "qualifikationen", "ihre qualifikationen", "deine qualifikationen",
    "was sie mitbringen", "was du mitbringst", "das bringen sie mit",
    "das bringst du mit", "voraussetzungen", "ihre kompetenzen", "deine kompetenzen",
    "das zeichnet sie aus", "das zeichnet dich aus", "was wir erwarten",
    "was wir von ihnen erwarten", "was wir von dir erwarten",
)
#: Every other section heading — each one ENDS a requirements section.
_OTHER_HEADINGS = (
    "your responsibilities", "responsibilities", "key responsibilities", "your role",
    "the role", "about the role", "your tasks", "tasks", "your contribution",
    "your mission", "what you will do", "what you'll do", "your impact",
    "ihre aufgaben", "deine aufgaben", "aufgaben", "aufgabengebiet",
    "ihr aufgabengebiet", "dein aufgabengebiet", "das erwartet sie", "das erwartet dich",
    "was sie erwartet", "was dich erwartet", "ihre rolle", "deine rolle", "die rolle",
    "nice to have", "nice-to-have", "preferred qualifications", "bonus points",
    "wünschenswert", "von vorteil",
    "what we offer", "we offer", "our offer", "your benefits", "benefits", "why us",
    "why join us", "wir bieten", "was wir bieten", "unser angebot", "das bieten wir",
    "ihre benefits", "deine benefits", "ihre vorteile", "deine vorteile",
    "about us", "who we are", "über uns", "wer wir sind", "das sind wir",
    "contact", "kontakt", "how to apply", "interested?", "interessiert?",
    "ihre bewerbung", "deine bewerbung",
)


def _heading_regexes() -> tuple[Any, Any]:
    import re

    alts = sorted({*_REQUIREMENTS_HEADINGS, *_OTHER_HEADINGS}, key=len, reverse=True)
    body = "|".join(re.escape(a) for a in alts)
    # A heading on its own line (markdown marks allowed, a short "(must have)" tail),
    # or a capitalised heading run into the text and closed by a colon.
    line = re.compile(rf"(?im)^[ \t#*>]*(?P<h>{body})(?![\w-])[^\n:]{{0,30}}?:?[ \t*]*$")
    inline = re.compile(rf"(?i)(?<![\w-])(?P<h>{body})[ \t]*:")
    return line, inline


_HEADING_LINE_RE, _HEADING_INLINE_RE = _heading_regexes()


def posting_requirements_text(jd_text: str | None) -> str:
    """The posting's candidate-requirements section(s) — from each requirements
    heading to the next recognised heading — joined; ``""`` when the posting has no
    recognisable requirements heading (RULING D-2 = A). Pure."""
    text = jd_text or ""
    marks: dict[int, tuple[int, bool]] = {}
    for m in _HEADING_LINE_RE.finditer(text):
        marks.setdefault(m.start("h"), (m.end(), m.group("h").lower() in _REQUIREMENTS_HEADINGS))
    for m in _HEADING_INLINE_RE.finditer(text):
        if not m.group("h")[0].isupper():
            continue  # "… the following requirements:" is prose, not a heading
        marks.setdefault(m.start("h"), (m.end(), m.group("h").lower() in _REQUIREMENTS_HEADINGS))
    starts = sorted(marks)
    parts = []
    for i, start in enumerate(starts):
        end, is_requirements = marks[start]
        if not is_requirements:
            continue
        stop = next((s for s in starts[i + 1:] if s >= end), len(text))
        parts.append(text[end:stop])
    return "\n".join(parts)


def _is_key_gap_row(entry: Any) -> bool:
    from applire.services.keyword_ledger import REQUIRED_WEIGHT, is_aim_only, is_scope_entry

    if not isinstance(entry, dict) or entry.get("claimable"):
        return False
    if entry.get("status") not in ("gap", "denied"):
        return False
    if is_scope_entry(entry) or is_aim_only(entry):
        return False
    if entry.get("unbacked"):
        # MD6-9 (D-3): the classifier judged this supported and only the quote
        # failed (ADR-100 cl. 6) — not known to be missing, so never "X is new to
        # me"; the letter leaves it out, like DEL-2 leaves an unsupported row out.
        return False
    weight = entry.get("fit_weight")
    return isinstance(weight, (int, float)) and float(weight) >= REQUIRED_WEIGHT


def key_requirement_gaps(
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None,
    jd_text: str | None,
) -> list[KeyGap]:
    """Every key-requirement gap among the rows given, most urgent first (G-1, MD6-1/4).

    A gap is a key-tier row (``fit_weight >= REQUIRED_WEIGHT``) the ledger marks NOT
    claimable — ``gap`` or ``denied`` — never a scope or aim-only row. An ADJACENT
    partial is claimable through a stand-in and is not a gap (DEL-2 keeps it).
    RULING L-1 / L-2 = B (#820, ADR-075 amended 2026-11-07): the LETTER passes only
    the candidate's recorded DENIALS (:func:`letter_gap_ladder`) — a never-asked row
    is never named; this function ranks whatever it is given. A denied row's ``label``
    is the candidate's own scope wording when it differs from the concept (#821,
    RULING G-1 of build 7: "no PSD2 licence", never the bare "PSD2").

    Rows whose concept (or ``denied_by`` binding, ADR-100 M-2) is a concept of one
    recorded ``DeniedConcept`` share that record's statement and form ONE gap; every
    other row is its own gap. Ranked by the posting's requirements section first
    (RULING D-2 = A: a concept named there ahead of one only in the
    responsibilities); inside the section by weight, then mentions and first
    position counted WITHIN the section (RULING D-6 = A — a responsibilities mention
    never lifts a must-have gap); outside it by weight, posting mentions
    (descending), first position (L-1); then ledger order. A gap's ``label`` is its
    top concept by the same order; ``mentions``/``first_position`` are the counts its
    rank used (section-scoped when ``in_requirements``).
    A row the ADR-100 backing check demoted (``unbacked``) is no gap (MD6-9).
    Pure; ``None``/malformed tolerant."""
    jd_norm = ats_norm(jd_text or "")
    req_norm = ats_norm(posting_requirements_text(jd_text))
    statement_of: dict[str, str] = {}
    for denial in denied_concepts or []:
        concept = (_get(denial, "concept", "") or "").strip()
        statement = (_get(denial, "statement", "") or "").strip()
        if concept and statement:
            statement_of.setdefault(ats_norm(concept), statement)

    # #821 (RULING G-1, build 7): a denied row is named in the candidate's own scope
    # wording, never the bare concept, keyed by ledger index.
    label_of: dict[int, str] = {}
    groups: dict[str, list[tuple[bool, float, int, int, int, str]]] = {}
    group_statement: dict[str, str | None] = {}
    order: list[str] = []
    for index, entry in enumerate(keyword_ledger or []):
        if not _is_key_gap_row(entry):
            continue
        concept = (entry.get("concept") or "").strip()
        if not concept:
            continue
        statement = statement_of.get(ats_norm(concept))
        bound = entry.get("denied_by")
        if not statement and isinstance(bound, str) and bound.strip():
            statement = statement_of.get(ats_norm(bound.strip()))
        key = f"s:{ats_norm(statement)}" if statement else f"c:{ats_norm(concept)}"
        scope = _denial_scope_wording(entry)
        if scope:
            label_of[index] = scope
        forms = _ledger_forms(entry)
        # RULING D-6 = A: inside the requirements section, mentions and first
        # position are counted WITHIN the section only — a responsibilities mention
        # never lifts a must-have gap. Outside it, L-1 over the whole posting.
        req_mentions, req_first = posting_mentions(forms, req_norm) if req_norm else (0, _NO_POSITION)
        in_req = req_mentions > 0
        mentions, first = (req_mentions, req_first) if in_req else posting_mentions(forms, jd_norm)
        if key not in groups:
            groups[key] = []
            group_statement[key] = statement
            order.append(key)
        groups[key].append(
            (in_req, float(entry.get("fit_weight") or 0.0), mentions, first, index, concept)
        )

    gaps: list[tuple[tuple, KeyGap]] = []
    for key in order:
        members = groups[key]
        members.sort(key=lambda m: (not m[0], -m[1], -m[2], m[3], m[4]))
        top = members[0]
        concepts = tuple(dict.fromkeys(m[5] for m in members))
        pool = [m for m in members if m[0] == top[0]]  # D-6: counts of ONE context
        gap = KeyGap(
            label=label_of.get(top[4], top[5]),
            concepts=concepts,
            statement=group_statement[key],
            weight=top[1],
            mentions=max(m[2] for m in pool),
            first_position=min(m[3] for m in pool),
            in_requirements=top[0],
        )
        gaps.append((
            (not gap.in_requirements, -gap.weight, -gap.mentions, gap.first_position, top[4]),
            gap,
        ))
    gaps.sort(key=lambda g: g[0])
    return [g for _, g in gaps]


def _denial_scope_wording(entry: dict[str, Any]) -> str | None:
    """#821 contract (WP-G, build 7): ``keyword_ledger.denial_scope_wording``."""
    from applire.services.keyword_ledger import denial_scope_wording

    return denial_scope_wording(entry)


def recorded_ladder_rows(keyword_ledger: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """RULING L-1 / L-2 = B (#820, ADR-075 amended 2026-11-07): the ledger rows the
    letter's gap ladder may consider — every row except a not-claimable row that is
    not the candidate's recorded denial. A never-asked row (ADR-074's Restfall), a
    containment-floored row (status ``gap``, ADR-059/#486), a question the candidate
    walked away from and a member of an answered topic all drop out: only a
    ``status == "denied"`` row can be named. Claimable rows pass untouched (they are
    never gaps). Order kept. Pure.

    UX audit round 3 (2026-11-07, gpt-6-luna): before L-1 the ladder named never-asked
    rows (G-1, 2026-11-04) and the skip-path letter told the employer "I have not
    worked with Apache Kafka" for a candidate who ran a Kafka pipeline; two blind
    panels said no/no on it."""
    out: list[dict[str, Any]] = []
    for entry in keyword_ledger or []:
        if not isinstance(entry, dict):
            continue
        if not entry.get("claimable") and entry.get("status") == "gap":
            continue
        out.append(entry)
    return out


def letter_gap_ladder(
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None,
    jd_text: str | None,
) -> tuple[list[KeyGap], list[KeyGap]]:
    """THE one computation of a letter's gap ladder — ``(key_gaps, named_gaps)`` over
    the recorded denials only (:func:`recorded_ladder_rows`). Shared by the generation
    run (writer block, review entry, corrector, the invented-limit fact) and the
    letter's keyword audit (784:146), so both read the same named set. Pure."""
    gaps = key_requirement_gaps(recorded_ladder_rows(keyword_ledger), denied_concepts, jd_text)
    return gaps, gaps_to_name(gaps)


def gaps_to_name(gaps: list[KeyGap]) -> list[KeyGap]:
    """G-1: up to two gaps → each is named; three or more → only the most urgent."""
    if len(gaps) <= GAP_LADDER_NAME_ALL_UP_TO:
        return list(gaps)
    return list(gaps[:1])


def named_gap_concepts(named: list[KeyGap]) -> list[str]:
    """Every ledger concept a NAMED gap covers — the grounded set the invented-limit
    fact (``limit_grounding``) adds to the candidate's own denials (ADR-076 am.
    2026-11-04 clause 3). Order kept, deduped."""
    return list(dict.fromkeys(c for g in named for c in g.concepts))


#: RULING L-2 = C (founder 2026-11-04): the no-transfer clause is a DESCRIBED family,
#: free wording; these are examples only. Keyed by the letter language.
EINARBEITEN_EXAMPLES: dict[str, tuple[str, str]] = {
    "de": ("…; in {gap} werde ich mich schnell einarbeiten.", "…; in {gap} arbeite ich mich schnell ein."),
    "en": ("…, and I will get up to speed on {gap} quickly.", "…; I get up to speed on {gap} quickly."),
}

#: MD6-2: the one grounding carve-out, stated ONCE and rendered into every door that
#: judges a letter sentence (writer block, positioning entry; the reviewer, corrector
#: and Oracle prompts state it in their own register and reference this name).
EINARBEITEN_CLAUSE_RULE = (
    "THE EINARBEITEN CLAUSE (RULING G-1, MD6-2): where the profile holds no transfer "
    "for a NAMED gap, that gap's sentence closes with ONE forward-looking clause of "
    "intent to get up to speed on it quickly, in the letter language and the "
    "candidate's own voice. It is the ONLY sentence part the grounding rules allow "
    "without a source, so it says NOTHING about the candidate's past or abilities: no "
    "skill, tool, experience, duration, figure, employer, project, certificate or trait "
    "adjective in it (\"fast learner\", \"proven ability\", \"steile Lernkurve\", "
    "\"dank meiner Erfahrung mit X\" are not it — a transfer goes BEFORE the clause, "
    "as the sentence's grounded half). "
    "Free wording; it is never an unsupported generalisation, never filler, and never "
    "a claim to be verified."
)


def _einarbeiten_examples(language: str | None, label: str) -> str:
    a, b = EINARBEITEN_EXAMPLES.get((language or "de").lower()[:2], EINARBEITEN_EXAMPLES["en"])
    return f"\"{a.format(gap=label)}\" or \"{b.format(gap=label)}\""


def gap_ladder_rule(language: str | None, named: list[KeyGap]) -> str:
    """The G-1 sentence rule, with examples for the first named gap.

    Carries D-29's ONE HOME PER FACT over from the deleted REQUIRED block (Hazelnut
    build 5, delivery run 6: the writer re-introduced the candidate's main system in
    the gap paragraph; replay luna n=10×2, second introduction 8/20 -> 3/20)."""
    label = named[0].label if named else "X"
    return (
        "For EACH named gap write ONE sentence, inside the single honest-gap paragraph: "
        "the gap first, as a plain fact about the work (\"Mit X habe ich noch nicht "
        "gearbeitet\", \"X is new to me\"), then — in the SAME sentence — a transfer "
        "argument grounded in the profile (a recorded capability that carries over). "
        "ONE HOME PER FACT (D-29): when the transfer is a system or project the letter "
        "already introduced, the gap sentence names it back in a few words (\"no X "
        "experience, but that system …\") and never introduces it again (no second \"At "
        "<employer>, I lead …\"). "
        "Where the profile holds no such transfer, the sentence closes with the "
        f"einarbeiten clause instead, e.g. {_einarbeiten_examples(language, label)} "
        "Never a list of lacks, never several gaps in one clause, never \"not recorded\", "
        "\"beyond my profile\", \"cannot claim / prove / substantiate\" — even where the "
        "candidate's statement words it so. Name ONLY the gap(s) listed: every other "
        "gap of this posting, and every further concept a listed statement names, stays "
        "unnamed — silence there is correct, never a finding. "
        + EINARBEITEN_CLAUSE_RULE
    )


def render_gap_ladder_block(
    gaps: list[KeyGap], named: list[KeyGap], language: str | None
) -> str:
    """The WRITER's G-1 block (replaces the REQUIRED: STATED LIMITS block). ``""``
    when the posting has no key-requirement gap."""
    if not named:
        return ""
    from applire.services.untrusted_text import items_note

    total = len(gaps)
    head = (
        f"This posting has {total} key requirement(s) the candidate has said they do not "
        "have — a recorded no (counted by code; one recorded statement naming several "
        "concepts counts once; a requirement nobody asked about is not among them and "
        "stays unnamed — RULING L-1). "
        + ("Each is named below." if total <= GAP_LADDER_NAME_ALL_UP_TO else
           "From three on ONLY the most urgent one is named — the one below.")
    )
    lines = [
        "=== KEY-REQUIREMENT GAPS (computed — RULING G-1) ===",
        head,
        gap_ladder_rule(language, named),
        items_note("gap terms"),
        "NAME IN THE LETTER:",
    ]
    for gap in named:
        if gap.statement:
            lines.append(
                f"  - {gap.label} — the candidate's own words (recorded): \"{gap.statement}\""
            )
        else:
            lines.append(
                f"  - {gap.label} — the candidate's recorded no (no statement text on file)"
            )
    return "\n".join(lines)


#: RULING DEL-1 (founder 2026-10-18, Hazelnut delivery F1/F2): an interview answer
#: that only withholds detail was recorded as a limit, and the letter wrote the
#: withholding in ("konkrete Entwicklungsbeispiele kann ich nicht nennen"). Letter
#: only — the CV writer's stated-limits block is unchanged. The vault rows stay.
WITHHOLDING_IS_NOT_A_LIMIT = (
    "A statement that only withholds detail (\"kann ich nicht nennen\", \"can't say "
    "more\", no figure or example given) is NOT a limit: use the detail it does give, "
    "and never write the withholding into the letter."
)

#: RULING DEL-2 (founder 2026-10-18, ADR-076 positioning contract): de-emphasis
#: means the letter LEAVES the lacked requirement out; the letter states a lack ONLY
#: where the candidate's own recorded denial names it.
#: Narrowed 2026-11-04 (G-1, ADR-076 amended): the gap ladder's NAMED gaps are the
#: other source of a lack — recorded or not.
LACK_ONLY_FROM_OWN_DENIAL = (
    "The letter states that the candidate lacks something ONLY for a gap the "
    "KEY-REQUIREMENT GAPS content names (RULING G-1), and never as what they cannot "
    "name, prove or claim. For any other requirement the candidate lacks, de-emphasis "
    "means the letter leaves it out — leaving it out is not a defect."
)


# `render_required_limits_block` — DELETED 2026-11-04 (G-1, ADR-075 amended). The
# REQUIRED: STATED LIMITS block owed every JD-relevant recorded statement "ONE
# positioning decision"; over a statement naming seven concepts the reviewer read
# seven obligations (founder UAT 11-04). Its successor is `render_gap_ladder_block`:
# the unit, the count and the pick are code, and only the named gap is owed.


def letter_constraint_only_limits(
    denied_concepts: list[Any] | None,
    named: list[KeyGap] | None,
) -> list[str]:
    """The INPUT FILTER on the letter (RULING DEL-3, narrowed by G-1 2026-11-04).

    Every recorded statement reaches the writer — it can still be the one a claim
    contradicts — but only a statement of a NAMED gap reaches it as something the
    letter says (through :func:`render_gap_ladder_block`). Every other statement is
    returned here and rendered as a constraint the letter never discloses: a
    denial this posting never asked about (DEL-3), a key-requirement denial G-1
    does not name (three or more gaps), a nice-to-have denial (L-1 = A).

    Before DEL-3 the writer received every denial framed with "an honest denial
    names the adjacent strengths", and wrote non-JD interview non-answers into the
    letter as admissions (Hazelnut delivery, logged rows 69-78). Pure; ``None``/
    empty tolerant; order preserved.
    """
    owed = {ats_norm(g.statement) for g in named or [] if g.statement}
    return [s for s in collect_stated_limits(denied_concepts) if ats_norm(s) not in owed]


def render_stated_limits_block(limits: list[str], *, for_letter: bool = False) -> str:
    """Render the candidate's verbatim stated limits for a WRITER prompt.

    Facts, plus the one rule that keeps them from being over-read. Nothing here
    pairs a limit with a concept — that judgement is the model's (see
    :func:`collect_stated_limits` for why the deterministic pairing was removed).

    Returns ``""`` when empty so a vault with no denials adds nothing.
    """
    if not limits:
        return ""
    if for_letter:
        # DEL-3: the letter's copy carries only the statements this posting does
        # NOT ask about (`letter_constraint_only_limits`), so it is a constraint
        # and says so; the owed ones arrive in the REQUIRED block instead.
        lines = [
            "=== STATED LIMITS — CONSTRAINT ONLY (the candidate's own words, verbatim) ===",
            "In an interview the candidate said each of the following. This posting does "
            "not ask about them, so the letter NEVER mentions them: never write a claim "
            "one of them contradicts, and never write a disclosure, a gap or a 'cannot "
            "claim' sentence drawn from them.",
            "A concept named INSIDE one of these statements as something the candidate "
            "DOES have is a STRENGTH: claim it plainly, without qualification.",
            WITHHOLDING_IS_NOT_A_LIMIT,
        ]
        lines.extend(f"  - {text}" for text in limits)
        return "\n".join(lines)
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


# ── #731: the stated-limit adjudication fact ─────────────────────────────────


def claimed_concepts_in_draft(
    draft: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None,
) -> list[str]:
    """CLAIMABLE ledger concepts whose surface form THIS draft actually carries.

    The missing half of the stated-limit question, and a FACT in the ADR-062
    clause 1 sense: literal presence of a surface form in a text, computed with
    THE shared presence predicate (``ats_audit.surface_present`` over the
    serialised draft, ADR-066) — the same instrument
    :func:`keyword_ledger.forbidden_terms_in_draft` and
    :func:`keyword_ledger.verified_missing_claimable` use, one definition, not a
    second matcher that could quietly disagree about presence.

    **Why it exists (#731, measured 2026-09-20).** A denial of "vector databases"
    and "embedding and reranking" cannot floor a ledger row labelled "RAG
    methods": the labels share no token, so neither half of the denial machinery
    reaches it (``declared_denial_matches`` returns ``[]``, ``is_denied_concept``
    returns False — ``Runs/Nougat/founder-uat-fixes/c/probe_lemma.py``). And it
    must not: "is RAG methods the same capability as a retrieval pipeline" is a
    JUDGEMENT, which the deterministic layer may not make, and the matcher that
    tried to pair limits with concepts answered it BACKWARDS on real data and was
    deleted (see :func:`collect_stated_limits`). So the row stays claimable, the
    forbidden list does not contain it, and the DO-NOT-CLAIM PRESENCE block
    correctly says nothing about it.

    The consequence was a letter delivering "I manage an AI automation use case
    using Databricks, large language models and RAG methods" against a candidate
    who had said the retrieval work was a colleague's. Measured on the real
    provider with the reviewer isolated on that draft, n=5: the reviewer demanded
    the vector-database limit be ADDED 5 of 5 times and demanded the RAG claim be
    removed **0 of 5**, and it still demanded it 0 of 5 after a check-1 bullet
    naming the case verbatim was added. A prohibition — or an instruction — is not
    a substitute for supplying the presence answer the judgement presupposes;
    that is ``forbidden_terms_in_draft``'s own lesson (#531), and this is its
    claimable-side twin.

    Direction: a concept this returns IS surfaced in the draft. A concept it does
    not return is one the scan did not find, which is not the same as absent — the
    fold behind ``surface_present`` is an ENGLISH verb-form fold, so an inflection
    can defeat it. :func:`render_stated_limit_adjudication_block` states exactly
    that, so a missed form stays raisable at the price of quoting the draft.

    Pure; ``None``/empty tolerant. Scope entries appear in neither ledger half
    (ADR-069) and therefore never here.
    """
    from applire.services.ats_audit import _norm as ats_text_norm
    from applire.services.ats_audit import surface_present
    from applire.services.keyword_ledger import _draft_strings, split_ledger_for_prompt

    claimable, _ = split_ledger_for_prompt(keyword_ledger)
    if not claimable:
        return []
    text_norm = ats_text_norm("\n".join(_draft_strings(draft)))
    out: list[str] = []
    for entry in claimable:
        concept = (entry.get("concept") or "").strip()
        if not concept:
            continue
        forms = [concept, *(f for f in (entry.get("surface_forms") or []) if f)]
        if any(surface_present(f, text_norm) for f in forms):
            out.append(concept)
    return out


def render_stated_limit_adjudication_block(
    claimed: list[str],
    limits: list[str],
) -> str:
    """The reviewer's STATED-LIMIT ADJUDICATION block: the presence fact, then the
    one judgement that remains (#731; the ADR-021 amended 2026-08-13 clause-4
    shape, applied to the CLAIMABLE half of the ledger).

    Returns ``""`` when the vault holds no stated limit, or when the scan finds no
    claimable concept in the draft — a letter with nothing to adjudicate adds
    nothing, the ADR-074 discipline applied to this block too.
    """
    if not limits or not claimed:
        return ""
    from applire.services.untrusted_text import items_note

    lines = [
        "STATED-LIMIT ADJUDICATION (deterministic literal scan of THIS draft — the "
        "presence half is ground truth, do not re-derive it). The scan finds these "
        "CLAIMABLE Keyword-Ledger concepts surfaced in the draft:",
        items_note("concepts"),
    ]
    lines += [f"  - {concept}" for concept in claimed]
    lines += [
        "",
        "And these are the candidate's own stated limits, verbatim:",
    ]
    lines += [f"  {i}. {text}" for i, text in enumerate(limits, 1)]
    lines += [
        "",
        "Presence is settled. The judgement that remains is yours and it is NOT "
        "optional: for EACH concept listed above, does any statement above deny the "
        "candidate that CAPABILITY — the work itself, not merely that word? The "
        "SUBJECT TEST does not excuse you here: a sentence naming one of these "
        "concepts as part of a system, platform, project or use case the CANDIDATE "
        "owns, manages or runs is a sentence about the CANDIDATE, because a reader "
        "credits the owner with the technology they name. The employer branch of the "
        "SUBJECT TEST is the TARGET employer's own product, read from the job "
        "description — never the candidate's own project. \"The use case I manage uses "
        "X\" claims X for the candidate as surely as \"I do X\" does, and a statement "
        "denying them X contradicts it. Such a sentence is not simply struck: it is reformed "
        "into a SCOPED CLAIM — the scope the candidate does own, stated plainly, with the "
        "limited part attributed to whoever did it — which under ADR-075 as amended "
        "2026-09-20 DISCHARGES the positioning obligation for that limit, so no negation is "
        "owed beside it. "
        "ledger cannot answer this and its CLAIMABLE verdict is not a defence: a "
        "ledger status is computed from vault surface forms and never from these "
        "statements, so the very capability a statement denies is routinely marked "
        "claimable, under a label that shares no word with the statement. Where the "
        "answer is YES, the sentence asserting it as the candidate's own is an "
        "UNGROUNDED CANDIDATE CLAIM (check 1) however plainly the vault's own wording "
        "seems to back it — raise it, and QUOTE the statement. Where the answer is NO, "
        "the concept stays fully claimable and must be claimed plainly: an invented "
        "limit costs the candidate their own best evidence and is exactly as untrue as "
        "an inflated claim.",
        "The scan folds ENGLISH verb forms only, so an inflection or compound can "
        "defeat it. A concept a statement denies that is NOT listed above may still be "
        "raised — your issue MUST then quote the exact words of the draft that carry "
        "it.",
    ]
    return "\n".join(lines)


def stated_limit_adjudication_reviewer_prompt_fn(
    base_fn: Any,
    *,
    keyword_ledger: list[dict[str, Any]] | None,
    denied_concepts: list[Any] | None,
):
    """Wrap a ``reviewer_prompt_fn`` so every round carries which CLAIMABLE
    concepts the CURRENT draft surfaces, beside the candidate's verbatim limits
    (#731).

    Composes with — never replaces — the coverage, unaddressed-requirement,
    word-floor, figure-ownership and do-not-claim-presence wrappers, exactly the
    way those compose with each other: ``review_and_refine`` calls
    ``reviewer_prompt_fn(source, draft)`` fresh each round, so the block tracks
    the draft the corrector just produced. No new LLM call, no new pass, no new
    loop (ADR-058 freeze).

    The limits are the SELECTED, JD-relevant set when there is one
    (:func:`select_jd_relevant_limits`, ADR-075 clause 4's cap), falling back to
    every limit the vault holds: the adjudication question is about the draft's
    claims, so a denial this posting never asked about can still be the one a
    claim contradicts.
    """

    def fn(source: str, draft: dict[str, Any]) -> str:
        prompt = base_fn(source, draft)
        limits = (select_jd_relevant_limits(denied_concepts, keyword_ledger)
                  or collect_stated_limits(denied_concepts))
        if not limits:
            return prompt
        claimed = claimed_concepts_in_draft(draft, keyword_ledger)
        block = render_stated_limit_adjudication_block(claimed, limits)
        if not block:
            return prompt
        logger.info(
            "stated-limit adjudication (#731): %d claimable concept(s) surfaced in "
            "the draft against %d stated limit(s): %s",
            len(claimed), len(limits), claimed,
        )
        return f"{prompt}\n\n{block}"

    return fn


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
