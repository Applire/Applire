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


def rank_cuts(
    texts: Sequence[str],
    tiers: Sequence[tuple[Any, ...]],
    keep: int,
    *,
    concept_groups: ConceptGroups = (),
    external_text: str = "",
    pinned: Sequence[int] | set[int] = (),
) -> list[Cut]:
    """Choose which of ``texts`` to remove so that ``keep`` survive.

    ``tiers[i]`` is the caller's own ascending ranking key for ``texts[i]`` —
    lower sorts earlier and is therefore cut FIRST. Callers pass their existing
    key unchanged (``(carries_figure, -order)`` for the role cap;
    ``(carries_figure, is_role, -order)`` for the page-overrun condense), and
    this function prepends the coverage criterion above it.

    ``external_text`` is everything in the document that this call cannot cut —
    the summary, the skills list, other roles' surviving bullets. A concept
    present there is covered no matter what happens here.

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
    keep = max(0, keep - len(pinned_set))

    groups = [list(g) for g in concept_groups]
    per_text = [_concepts_carried(t, groups) for t in texts]
    external = _concepts_carried(external_text, groups) if groups else frozenset()
    # A pinned bullet survives by construction, so the concepts it carries are
    # covered exactly like external text — a rest bullet repeating them is not
    # a sole carrier.
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
    """
    ctx = " ".join(f"{k}={v!r}" for k, v in context.items())
    for c in cuts:
        level = logging.WARNING if c.sole_carrier else logging.INFO
        logger.log(
            level,
            "TAIL_DELETE (ADR-072 clause 4) pass=%s %s sole_carrier=%s tier=%r removed=%r",
            pass_name, ctx, c.sole_carrier, c.tier, c.text,
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
