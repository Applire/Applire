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

"""ADR-072 clauses 1 & 4 — THE cut ranking for every deterministic bullet
removal, and the audit trail every such removal leaves.

Two passes cap a role's bullets after the last LLM reviewer has approved the
draft: ``cv._cap_bullets`` (the ``RoleBudget`` ceiling enforced unconditionally
by ``_restore_ledger_bullets``) and ``cv_budget.condense_to_budget`` (the
page-overrun path). ADR-066 — one logical operation, one implementation: they
share this module rather than each carrying their own copy of the order, which
is how they drifted apart before #377.

**Why coverage ranks above figure-presence.** #377 replaced a keyword-hit
ranking with ``bullet_carries_figure`` because deterministic code may compute a
FACT but may not judge which evidence is strongest (ADR-062 clause 1). That
correction was right and is kept — but on its own it condemned #423's packaging
bullet, *"Verantwortung für den Sauberraumbereich (Kunststoff- und
Kosmetik-Verpackungen) seit 2021"*: a bare year is not a quantified figure, and
it was listed last, so it lost on both criteria while being the candidate's
ONLY packaging evidence against a packaging manufacturer's JD.

Being the **sole carrier** of a claimable concept is also a fact, not a
judgement — it is a presence count computed with the shared predicate
(``ats_audit.surface_present``, US212/#122: consumers may never disagree on
presence by construction). It is deliberately NOT the retired keyword-hit
ranking: a bullet that merely repeats a ledger surface form gets nothing here,
because the concept is covered elsewhere. Only the *last* carrier is protected,
and only until it is no longer the last.

**Why this is not #303.** #303 demanded the writer PRODUCE literal ledger
surface forms in narrative prose, which honest German cannot satisfy, and drove
both review loops to exhaustion. This ranking never asks for content: it only
reorders what is cut among bullets that already exist. Doing nothing is always
a valid outcome, and nothing here can reach generation, because it runs after it.

**Where the two rules actually fork.** Coverage sits ABOVE figure-presence, so
a figure-less sole carrier survives a figure-bearing bullet that carries no
claimable concept — and the number is then the content lost. That is the trade
the blind panel asked for: across two runs all four reviewers made the missing
packaging evidence their single shared reservation, and none of them asked for
another figure. #377's own case is untouched, because filler that merely
repeats a covered surface form is never protected, and within the unprotected
tier the figure order is exactly what it was. The exposure is bounded: the
ledger is model-derived, so a mis-classified concept can protect a weak bullet
— but only ever by changing WHICH bullet is cut when the ceiling binds, never
how many.

**Why the status is recomputed after every removal.** Two bullets carrying one
concept are each "not the sole carrier"; cutting both on a status computed once
loses the concept while every individual decision looked safe. The greedy loop
below re-counts the survivors before each removal, so the second-to-last carrier
becomes protected the moment the last one is taken.
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: One group per claimable ledger entry — ``keyword_ledger.retention_forms``,
#: never flattened. A group is ONE competence: #386 shipped 'Dreischichtbetrieb'
#: and 'Schichtbetrieb' as two skills because a flat list makes every sibling
#: form an independent candidate. Here it would also double-count a concept's
#: carriers and defeat the sole-carrier test.
ConceptGroups = Sequence[Sequence[str]]


@dataclass(frozen=True)
class Cut:
    """One removal decision, in the order the pass made it.

    ``sole_carrier`` records whether the removed text was, at the moment it was
    chosen, the only carrier of a claimable concept. True means the ceiling was
    tighter than the protected set and the clause could not be honoured — the
    case that must never be silent (clause 4).
    """

    index: int
    text: str
    tier: tuple[Any, ...]
    sole_carrier: bool


def _concepts_carried(text: str, groups: ConceptGroups) -> frozenset[int]:
    """Indices of the concept groups whose evidence ``text`` carries.

    Uses the shared presence predicate, so this can never disagree with the ATS
    panel, the coverage reviewer or ``verified_missing_claimable`` about whether
    a concept is present.
    """
    from applire.services.ats_audit import _norm, surface_present

    n = _norm(text)
    if not n:
        return frozenset()
    return frozenset(
        gi for gi, group in enumerate(groups)
        if any(surface_present(f, n) for f in group if isinstance(f, str))
    )


def demanded_exempt_indices(
    texts: Sequence[str],
    *,
    concept_groups: ConceptGroups,
    demanded_groups: ConceptGroups,
    narrative_external_text: str,
    coverage_demanded_groups: ConceptGroups = (),
    external_text: str = "",
) -> set[int]:
    """ADR-072 clause 4 amended 2026-09-08 (#666) — the indices the cap may not take.

    Founder ruling 1 of 2026-09-05: *the cap yields to the signal.* Two accepted
    decisions were setting the same number, and the loop could not leave the conflict,
    because the deletion happens inside the round that made the addition.

    ``demanded_groups`` is PROVENANCE, not a property: the retention forms of the
    concepts THIS ROUND's ADR-076 clause-5 under-claim signal actually raised, recorded
    at the signal's own call and threaded through ``BudgetResult.demanded_concepts``.
    A text is exempt when it is the EARLIEST carrier of such a group that nothing
    outside this call carries in NARRATIVE form (``narrative_external_text``:
    work-entry and nested-project bullets only).

    **Provenance rather than "every REQUIRED concept", and the difference was
    measured.** The property form — exempt the sole narrative carrier of any concept at
    ``fit_weight >= REQUIRED_WEIGHT`` — was built first and run over the captured RC
    document: 19 required concepts spread across 8 bullets made **7 of the 8** exempt
    against a ceiling of 5, i.e. it did not amend the cap, it switched it off. The
    ruling exempts the bullet a demand produced, and only that.

    The NARRATIVE restriction is the second half, and it is what makes the exemption
    bite at all: ``rank_cuts``' existing sole-carrier tier computes coverage over the
    whole document, so a concept sitting in the skills list reads as covered and its
    only real bullet is cut — SF-WRITE.30's cause ("a bare tag ends the demand")
    arriving one pass later. The clause-5 demand's own text says *"a skills-list entry
    does NOT satisfy this"*, so a cap that accepts one cannot honour it.

    EARLIEST carrier, not every carrier: one bullet per concept is what the demand asks
    for, and the writer's own order is its relevance judgement (this module's standing
    tie-break). Computed once, because this is a PARTITION — ADR-077 clause 4's
    precedent, named by the ruling itself — not a ranking tier: a tier is silently
    defeated by a tight ceiling, which is the whole defect.

    **Amended 2026-09-17 (#415, founder ruling W-1): there are TWO provenances, and each
    carries the corpus its OWN producer measures absence in.** The 2026-09-05 ruling this
    exemption implements says *"a bullet introduced in this round in response to a COVERAGE
    or under-claim signal"* — two demand kinds in one sentence, of which #666 built one.
    ``coverage_demanded_groups`` is the ADR-048/US213 VERIFIED COVERAGE demand
    (``keyword_ledger.verified_missing_claimable`` through
    ``coverage_reviewer_prompt_fn(on_demand=…)``), and its absence test is the WHOLE
    serialised document (``external_text``), because that is the corpus that demand is
    computed over. Giving it the narrative corpus instead would make the exemption wider
    than the demand — the direction that measured 7 of 8 in #666, i.e. the cap switched off
    rather than amended. A demand's exemption may never be wider than the demand.

    The two sets are unioned, not merged: the exempt INDEX is the earliest carrier either
    way, so a bullet answering both demands occupies one budget slot, not two.
    """
    if not concept_groups or not (demanded_groups or coverage_demanded_groups):
        return set()
    groups = [list(g) for g in concept_groups]
    per_text = [_concepts_carried(t, groups) for t in texts]

    exempt: set[int] = set()
    for demanded, covered_elsewhere in (
        (demanded_groups, _concepts_carried(narrative_external_text, groups)),
        (coverage_demanded_groups, _concepts_carried(external_text, groups)),
    ):
        if not demanded:
            continue
        demanded_keys = {tuple(g) for g in demanded}
        for gi, group in enumerate(groups):
            if tuple(group) not in demanded_keys or gi in covered_elsewhere:
                continue
            carriers = [i for i, carried in enumerate(per_text) if gi in carried]
            if carriers:
                exempt.add(carriers[0])
    return exempt


def rank_cuts(
    texts: Sequence[str],
    tiers: Sequence[tuple[Any, ...]],
    keep: int,
    *,
    concept_groups: ConceptGroups = (),
    external_text: str = "",
    pinned: Sequence[int] | set[int] = (),
    demanded_groups: ConceptGroups = (),
    narrative_external_text: str = "",
    coverage_demanded_groups: ConceptGroups = (),
    evidence_external_text: str | None = None,
) -> list[Cut]:
    """Choose which of ``texts`` to remove so that ``keep`` survive.

    ``tiers[i]`` is the caller's own ascending ranking key for ``texts[i]`` —
    lower sorts earlier and is therefore cut FIRST. Callers pass their existing
    key unchanged (``(carries_figure, -order)`` for the role cap;
    ``(carries_figure, is_role, -order)`` for the page-overrun condense), and
    this function prepends the coverage criterion above it.

    ``external_text`` is everything in the document that this call cannot cut —
    the summary, the skills list, other roles' surviving bullets. Since 2026-09-17 it
    answers exactly one question: whether a concept a VERIFIED COVERAGE demand raised is
    still absent from the whole document (that demand's own corpus, see
    :func:`demanded_exempt_indices`).

    ``evidence_external_text`` (ADR-072 clause 1 amended 2026-09-17, #415, founder ruling
    W-1) is the corpus the SOLE-CARRIER TIER reads: the same text the delivered
    ``narrative-evidence`` check grades as evidence — work-entry and project bullets ∪ the
    vault-joined ``languages``/``certifications``/``education`` sections, with ``skills``
    and ``summary`` excluded (ADR-076 clause 5's founding rule, as amended by RULING W1-3).
    ``None`` falls back to ``external_text``, which is the pre-2026-09-17 behaviour exactly
    and is what every non-production caller gets.

    **Why the tier needed its own corpus.** 2026-09-11 delivery run: the cap removed the
    role's only ISO-9001 bullet at ``sole_carrier=False`` — false because the skills list
    carried the tag — and the delivered report then read
    ``narrative-evidence: fail … ISO 9001 (claimed but not evidenced)``. Two instruments,
    one question, two populations of the same document; ``bullet_cuts``' own docstring had
    named the hazard since 2026-08-02 and that run is its first occurrence on a delivered
    document. This stays a **ranking tier and is deliberately not promoted to a partition**:
    a partition is right for a bounded provenance and wrong for an unbounded property — the
    property form was measured at 7 of 8 bullets exempt against a ceiling of 5. So the
    per-role ceiling ALWAYS holds here; when every bullet is a sole carrier the cut is made
    by the caller's own key and logged as a budget-vs-coverage conflict (see
    :func:`log_cuts`).

    ``pinned`` (ADR-077 clause 4) — indices of fact-pin carriers. This is a
    PARTITION, not a ranking tier: pinned indices never enter the removable
    set, the ``keep`` ceiling applies to the rest only (each pin occupies one
    budget slot), and when pins alone exceed the ceiling, the ceiling is
    violated by design — that violation IS "pin beats budget", logged here
    at WARNING and reported via the clause-5 driver. A tier implementation
    would be silently defeated by a tight ceiling (the sole-carrier WARNING
    boundary), which the 2026-08-24 adversarial pass proved.

    Returns the removals in the order they were decided (never sorted by index),
    so a caller logging them reports the same sequence the pass reasoned in.
    Empty list when already within budget — the caller then leaves its input
    object untouched, order included.
    """
    keep = max(0, keep)
    if len(texts) <= keep:
        return []

    pinned_set = {i for i in pinned if 0 <= i < len(texts)}
    if len(pinned_set) > keep:
        logger.warning(
            "PIN_CEILING_VIOLATED (ADR-077 clause 4) pinned=%d keep=%d — "
            "the ceiling yields; pins beat the budget",
            len(pinned_set),
            keep,
        )
    # ADR-072 clause 4 amended 2026-09-08 (#666): a demanded bullet joins the pin
    # PARTITION rather than a ranking tier. `pinned_set |= …` deliberately, so a
    # bullet that is both pinned and demanded occupies one slot, not two.
    exempt_set = demanded_exempt_indices(
        texts,
        concept_groups=concept_groups,
        demanded_groups=demanded_groups,
        narrative_external_text=narrative_external_text,
        # ADR-072 clause 4 amended 2026-09-17 (#415): the second provenance, with the
        # whole-document corpus its own producer measures absence in.
        coverage_demanded_groups=coverage_demanded_groups,
        external_text=external_text,
    ) - pinned_set
    if exempt_set:
        pinned_set = pinned_set | exempt_set
        if len(pinned_set) > keep:
            logger.warning(
                "BUDGET_VS_SIGNAL_CONFLICT (ADR-072 clause 4 / ADR-076 clause 5) "
                "demanded=%d pinned=%d keep=%d — the per-role ceiling (ADR-051 §3, "
                "producer: cv_budget.RoleBudget) is tighter than the set the "
                "under-claim signal raised THIS round and will raise again "
                "(producers: cv_gap_hints.verified_narrative_underclaim, "
                "keyword_ledger.verified_missing_claimable). "
                "The ceiling yields — "
                "founder ruling 1 of 2026-09-05, ADR-077 clause 4's partition precedent.",
                len(exempt_set),
                len(pinned_set),
                keep,
            )
    keep = max(0, keep - len(pinned_set))

    groups = [list(g) for g in concept_groups]
    per_text = [_concepts_carried(t, groups) for t in texts]
    # ADR-072 clause 1 amended 2026-09-17 (#415, ruling W-1): the sole-carrier TIER reads
    # the EVIDENCE corpus — what the delivered `narrative-evidence` check grades — while
    # `external_text` stays the whole document for the coverage provenance above. `None`
    # (every non-production caller) reproduces the pre-amendment behaviour exactly.
    tier_external_text = (
        external_text if evidence_external_text is None else evidence_external_text
    )
    external = _concepts_carried(tier_external_text, groups) if groups else frozenset()
    # A pinned (or #666-exempt) bullet survives by construction, so the concepts it
    # carries are covered exactly like external text — a rest bullet repeating them is
    # not a sole carrier.
    for i in pinned_set:
        external = external | per_text[i]

    surviving = set(range(len(texts))) - pinned_set
    cuts: list[Cut] = []
    while len(surviving) > keep:
        # Recount before every removal: the second-to-last carrier of a concept
        # becomes protected the moment the last one is taken.
        counts: Counter[int] = Counter()
        for i in surviving:
            counts.update(per_text[i])

        def _is_sole(i: int) -> bool:
            return any(
                counts[c] == 1 and c not in external for c in per_text[i]
            )

        # Ascending: unprotected before protected, then the caller's own key.
        victim = min(surviving, key=lambda i: (_is_sole(i),) + tuple(tiers[i]))
        sole = _is_sole(victim)
        surviving.discard(victim)
        cuts.append(Cut(index=victim, text=texts[victim],
                        tier=tuple(tiers[victim]), sole_carrier=sole))
    return cuts


def apply_cuts(texts: Sequence[str], cuts: Sequence[Cut]) -> list[str]:
    """The survivors, in their ORIGINAL relative order.

    Order is a judgement the writer already made; a cap may take bullets away
    but must never permute the ones it leaves.
    """
    removed = {c.index for c in cuts}
    return [t for i, t in enumerate(texts) if i not in removed]


def log_cuts(pass_name: str, cuts: Sequence[Cut], **context: Any) -> None:
    """ADR-072 clause 4 — every deterministic deletion in the post-review tail
    leaves a trace naming the pass, the removed content and the predicate that
    fired.

    Not a report surface and not a user-visible warning. #423, #377 and the
    project-nesting collision were each expensive for the same reason: the
    deletion left nothing behind, so attribution needed a full input replay of
    four captured runs. A cut that removes a PROTECTED bullet logs at WARNING —
    the ceiling was tighter than the protected set and clause 1 could not be
    honoured, which is a real constraint conflict, not routine trimming.

    **Amended 2026-09-17 (#415, ruling W-1).** Now that the sole-carrier tier reads the
    EVIDENCE corpus, a ``sole_carrier=True`` cut is exactly the case the delivered
    ``narrative-evidence`` check will go on to report — so the WARNING names **both
    producers**, the ceiling's and the coverage grader's, which is the standard
    ``BUDGET_VS_SIGNAL_CONFLICT`` and ``PIN_CEILING_VIOLATED`` already set. The
    ``TAIL_DELETE`` line's own shape is unchanged; the conflict clause is appended, so every
    existing reader and log assertion still parses it.
    """
    ctx = " ".join(f"{k}={v!r}" for k, v in context.items())
    for c in cuts:
        level = logging.WARNING if c.sole_carrier else logging.INFO
        conflict = (
            " BUDGET_VS_COVERAGE (ADR-051 §3 vs ADR-076 clause 5) — the per-role ceiling "
            "(producer: cv_budget.RoleBudget) is tighter than the evidence this document "
            "needs; the removed bullet was the LAST carrier of a claimable concept in the "
            "corpus the delivered report grades (producers: "
            "cv_gap_hints.verified_narrative_underclaim, "
            "ats_audit._narrative_evidence_check). The ceiling holds — ADR-072 clause 1 is "
            "a ranking tier, not a partition."
            if c.sole_carrier
            else ""
        )
        logger.log(
            level,
            "TAIL_DELETE (ADR-072 clause 4) pass=%s %s sole_carrier=%s tier=%r removed=%r%s",
            pass_name, ctx, c.sole_carrier, c.tier, c.text, conflict,
        )


def log_deletion(pass_name: str, predicate: str, removed: Any, **context: Any) -> None:
    """Clause 4 for a deletion that is not a ranked bullet cut — a deduped
    skill, a project dropped after losing every bullet, a restoration cancelled
    by a ceiling. ``predicate`` names the test that fired, in the terms the code
    uses (e.g. ``"_compound_suffix_dupe"``), so a log line leads straight to the
    branch that made the decision.
    """
    ctx = " ".join(f"{k}={v!r}" for k, v in context.items())
    logger.info(
        "TAIL_DELETE (ADR-072 clause 4) pass=%s predicate=%s %s removed=%r",
        pass_name, predicate, ctx, removed,
    )
