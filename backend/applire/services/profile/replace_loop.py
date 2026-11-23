# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The bullet-replace loop for a REWORDED bullet (Sorbet invite-gate WP-V;
founder rulings Q3-4, Q8-3, Q9-1 … Q9-7; the GARH loop).

**Where it starts.** The interview door's restatement call (ADR-046 D-17) names
a NEW bullet of the turn that adds detail to one HELD bullet of the same work
entry. When the new bullet keeps every token of the held one in order
(``restatement.blocking_check``) the replace happens directly, with the
"Ersetzt" line and its undo (Q3-4, unchanged). Measured on real data that path
passes 0 of 87 candidates (q9): a person who tells the same fact with more
detail rewords it. Those candidates come here.

**The loop (GARH).**

1. The AUTHOR (``prompts/profile_replace``) writes ONE bullet from the two,
   from the two bullets only (never the answer — q9 hole 1), in the held
   bullet's language; or withdraws when they are different facts.
2. The deterministic CHECK (:func:`check_proposal`) judges what the proposal
   KEPT (every held token in order; every fact word of the new bullet) and
   what it ADDED (no word, figure, negation, first person or wish the two
   bullets do not hold — q9 hole 1's second half). Clean → done.
3. A HARD finding (something added) goes straight back to the author — the
   reviewer cannot wave an added fact through. Soft findings only (a reworded
   or translated part) go to the REVIEWER, which confirms with quotes for every
   word of both bullets (:func:`verify_evidence`) or hands back a finding.
4. The author revises against the finding; the revision is checked again,
   added side included.
5. Bound: ``BULLET_REPLACE_REVIEW_MAX_RETRIES`` revisions (default 2, the q9
   measurement). Unresolved → the proposal is the new bullet as written.

**The person decides every reworded replace** (WP-V brief; FOUNDER-QUESTION
V-1 sent with the recommendation built here): the loop only decides WHICH
bullet the person is shown next to the held one. Nothing is written by this
module; it returns proposals, and the interview asks one closed question per
proposal (``services/interview/scope_question`` → bullet-replace cluster).
Until then both bullets stay, which is the behaviour before Q3-4.

Why a person and not the reviewer (q9): the reviewer executed the moved word
"Anleitung" 3/3 and glued a different fact on about 1 in 10.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from applire.constants import BULLET_REPLACE_REVIEW_MAX_RETRIES
from applire.content_log import content_logger
from applire.prompts.profile_replace import (
    REPLACE_AUTHOR_SYSTEM,
    REPLACE_REVIEWER_SYSTEM,
    build_author_prompt,
    build_reviewer_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import llm_log_stage
from applire.services.oracle.matchers.figures import extract_figures
from applire.services.profile.reconcile.restatement import (
    _NEGATORS,
    _same_word,
    _tokens,
    blocking_check,
)

logger = logging.getLogger(__name__)
clog = content_logger(__name__)

AUTHOR_MAX_TOKENS = 1024
REVIEWER_MAX_TOKENS = 2048

#: First-person words. A profile bullet has no grammatical subject; a first
#: person in a proposal that neither bullet carries is answer prose (q9: "Die
#: dauerhafte Spanne von 120 wäre für mich der nächste Schritt" was executed).
_FIRST_PERSON = frozenset({
    "ich", "mich", "mir", "mein", "meine", "meinen", "meinem", "meiner", "meines",
    "wir", "uns", "unser", "unsere", "unseren", "unserem", "unserer",
    "i", "me", "my", "mine", "myself", "we", "us", "our", "ours",
})
#: Wish / aim words (q9: "… wäre für mich der nächste Schritt", "plan to").
#: Lexical on purpose and closed: it can only send a proposal back (MD-Q17's
#: precedent for a lexical blocker that only blocks).
_WISH = frozenset({
    "würde", "würden", "wäre", "wären", "möchte", "möchten", "wünsche", "wünschen",
    "hoffe", "hoffen", "would", "wish", "wishes", "hope", "hopes", "want", "wants",
})
#: A fact word: the restatement tokens that carry content — a number, a name
#: (two capitals), a negation, a number word, or any word of 4+ letters.
_MIN_WORD = 4


@dataclass(frozen=True)
class Finding:
    kind: str
    hard: bool
    words: tuple[str, ...] = ()

    def describe(self) -> str:
        what = ", ".join(f"„{w}“" for w in self.words[:8])
        text = {
            "unchanged": "Your bullet only repeats HELD; it must also state NEW's facts.",
            "added_negation": f"Your bullet adds a negation HELD does not state: {what}. If NEW says no where HELD says yes, they are different facts.",
            "dropped_negation": f"Your bullet drops a negation of HELD: {what}.",
            "first_person": f"Your bullet speaks in the first person ({what}); a profile bullet has no 'I'.",
            "wish": f"Your bullet states a wish or aim ({what}); state only what the bullets state.",
            "figure_unbacked": f"Your bullet carries figures neither bullet states: {what}.",
            "held_order": f"Your bullet loses or reorders words of HELD: {what}.",
            "new_missing": f"Your bullet leaves out facts of NEW: {what}.",
            "unbacked_words": f"Your bullet has words neither bullet holds: {what}.",
        }[self.kind]
        return text

    def signal(self) -> dict[str, Any]:
        return {"kind": self.kind, "words": list(self.words[:12])}


def _fact_tokens(text: str) -> list[tuple[str, bool, int, int]]:
    return [t for t in _tokens(text) if t[1] or len(t[0]) >= _MIN_WORD]


def _present(tok: tuple[str, bool, int, int], pool: list[tuple[str, bool, int, int]]) -> bool:
    return any(
        (tok[0] == p[0]) if (tok[1] or p[1]) else _same_word(tok[0], p[0]) for p in pool
    )


def check_proposal(held: str, new: str, proposal: str) -> list[Finding]:
    """Every finding the proposal earns; ``[]`` when it may go to the person
    without a reviewer. FACTS only (ADR-062 cl. 1): token presence, figures by
    kind and value, closed word lists that can only send a proposal back."""
    p_toks, h_toks, n_toks = _tokens(proposal), _tokens(held), _tokens(new)
    both = h_toks + n_toks
    findings: list[Finding] = []
    if not p_toks or [t[0] for t in p_toks] == [t[0] for t in h_toks]:
        return [Finding("unchanged", True)]
    held_words = {t[0] for t in both}
    # Polarity is HELD's (q9 adversarial "added keine": the reviewer confirmed
    # "Keine Budgetverantwortung …" for the held "Budgetverantwortung …" because
    # NEW carried the "keine"). A negation the held bullet lacks, or a held
    # negation the proposal lacks, can never pass — two bullets that disagree on
    # yes/no are not one bullet; the author may withdraw, else the person decides.
    held_neg = {t[0] for t in h_toks if t[0] in _NEGATORS}
    neg = tuple(t[0] for t in p_toks if t[0] in _NEGATORS and t[0] not in held_neg)
    if neg:
        findings.append(Finding("added_negation", True, neg))
    prop_neg = {t[0] for t in p_toks if t[0] in _NEGATORS}
    lost = tuple(sorted(held_neg - prop_neg))
    if lost:
        findings.append(Finding("dropped_negation", True, lost))
    first = tuple(t[0] for t in p_toks if t[0] in _FIRST_PERSON and t[0] not in held_words)
    if first:
        findings.append(Finding("first_person", True, first))
    wish = tuple(t[0] for t in p_toks if t[0] in _WISH and t[0] not in held_words)
    if wish:
        findings.append(Finding("wish", True, wish))
    backed = {(f.kind, f.value) for f in extract_figures(held)} | {
        (f.kind, f.value) for f in extract_figures(new)
    }
    figs = tuple(
        f.raw for f in extract_figures(proposal)
        if (f.kind, f.value) not in backed
    )
    if figs:
        findings.append(Finding("figure_unbacked", True, figs))
    if blocking_check(held, proposal) is not None:
        missing = tuple(held[t[2]:t[3]] for t in h_toks if not _present(t, p_toks))
        findings.append(Finding("held_order", False, missing or ("(order)",)))
    new_missing = tuple(new[t[2]:t[3]] for t in _fact_tokens(new) if not _present(t, p_toks))
    if new_missing:
        findings.append(Finding("new_missing", False, new_missing))
    unbacked = tuple(
        proposal[t[2]:t[3]] for t in _fact_tokens(proposal) if not _present(t, both)
    )
    if unbacked:
        findings.append(Finding("unbacked_words", False, unbacked))
    return findings


_WORD = re.compile(r"\w+")


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split())


def verify_evidence(held: str, new: str, proposal: str, evidence: Any) -> str | None:
    """``None`` when every quote is verbatim in PROPOSAL, every part verbatim in
    HELD or NEW, and the parts cover every word of HELD and of NEW; else the
    reason. A quote may translate its part (cross-language) — it must only be
    the proposal's own words, never the answer's (the q9 evidence rule)."""
    H, N, P = _norm(held), _norm(new), _norm(proposal)
    if not isinstance(evidence, list) or not evidence:
        return "no evidence"
    covered = {"h": [False] * len(H), "n": [False] * len(N)}
    for item in evidence:
        if not isinstance(item, dict):
            return "malformed evidence"
        part, quote = _norm(item.get("part")), _norm(item.get("quote"))
        if not quote or quote not in P:
            return "a quote is not verbatim in the proposal"
        if not part:
            return "an evidence item names no part"
        hit = False
        for key, text in (("h", H), ("n", N)):
            start = text.find(part)
            while start >= 0:
                hit = True
                for i in range(start, start + len(part)):
                    covered[key][i] = True
                start = text.find(part, start + 1)
        if not hit:
            return "a part is not verbatim in HELD or NEW"
    for key, text in (("h", H), ("n", N)):
        for m in _WORD.finditer(text):
            if not all(covered[key][m.start():m.end()]):
                return "a word of HELD or NEW is not covered"
    return None


@dataclass
class ReplaceProposal:
    """What the person is asked about. ``absorbs`` — the proposal carries every
    fact of the new bullet (check or verified reviewer), so on "Ersetzen" the new
    bullet goes too; otherwise only the held one is replaced."""

    entry_id: str
    field: str
    held: str
    new: str
    proposal: str
    absorbs: bool
    path: str  # "check" | "reviewer" | "unresolved"
    rounds: int = 0
    trail: list[dict] = field(default_factory=list)

    def as_state(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id, "field": self.field, "held": self.held,
            "new": self.new, "proposal": self.proposal, "absorbs": self.absorbs,
            "path": self.path,
        }


async def _call(provider: LLMProvider, stage: str, prompt: str, system: str, max_tokens: int) -> Any:
    with llm_log_stage(stage):
        return await provider.aparse_json(prompt, system=system, temperature=0.0, max_tokens=max_tokens)


async def _author(provider: LLMProvider, held: str, new: str, previous: str | None,
                  finding: str | None) -> tuple[str | None, bool]:
    """``(bullet, withdrawn)``; ``(None, False)`` on a failed or malformed call."""
    try:
        data = await _call(
            provider, "bullet_replace_author",
            build_author_prompt(held, new, previous=previous, finding=finding),
            REPLACE_AUTHOR_SYSTEM, AUTHOR_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open: both bullets stay
        logger.warning("bullet replace: author call failed (%s); both bullets stay", type(exc).__name__)
        return None, False
    if not isinstance(data, dict) or "new" not in data:
        return None, False
    text = data.get("new")
    if text is None:
        return None, True
    if not isinstance(text, str) or not text.strip():
        return None, False
    return " ".join(text.split()), False


async def _reviewer(provider: LLMProvider, held: str, new: str, proposal: str,
                    findings: list[Finding]) -> tuple[bool, str]:
    """``(confirmed, finding_for_the_author)``. A confirm whose evidence does
    not verify is a finding, never a confirm (q9: "send_back_verify")."""
    signals = {"findings": [f.signal() for f in findings]}
    try:
        data = await _call(
            provider, "bullet_replace_reviewer",
            build_reviewer_prompt(held, new, proposal, signals),
            REPLACE_REVIEWER_SYSTEM, REVIEWER_MAX_TOKENS,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("bullet replace: reviewer call failed (%s)", type(exc).__name__)
        return False, ""
    if not isinstance(data, dict):
        return False, ""
    if data.get("decision") == "confirm":
        bad = verify_evidence(held, new, proposal, data.get("evidence"))
        if bad is None:
            return True, ""
        return False, (
            f"The review could not verify your bullet ({bad}); keep every part of HELD and every "
            "fact of NEW, and add nothing."
        )
    finding = data.get("finding")
    return False, finding.strip() if isinstance(finding, str) else ""


async def run_replace_loop(
    provider: LLMProvider,
    *,
    entry_id: str,
    field_name: str,
    held: str,
    new: str,
    max_retries: int | None = None,
) -> ReplaceProposal | None:
    """One candidate through the loop; ``None`` when the author withdraws (both
    bullets stay, no question) or the very first author call fails."""
    bound = BULLET_REPLACE_REVIEW_MAX_RETRIES if max_retries is None else max_retries
    trail: list[dict] = []
    proposal, withdrawn = await _author(provider, held, new, None, None)
    for round_ in range(bound + 1):
        if withdrawn:
            trail.append({"round": round_, "outcome": "withdrawn"})
            _log(entry_id, "withdrawn", round_)
            return None
        if proposal is None:
            break
        findings = check_proposal(held, new, proposal)
        step: dict[str, Any] = {"round": round_, "proposal": proposal,
                                "findings": [f.signal() for f in findings]}
        trail.append(step)
        if not findings:
            step["outcome"] = "check"
            _log(entry_id, "check", round_)
            return ReplaceProposal(entry_id, field_name, held, new, proposal, True, "check", round_, trail)
        hard = [f for f in findings if f.hard]
        if hard:
            finding = " ".join(f.describe() for f in hard)
            step["outcome"] = "hard"
        else:
            confirmed, finding = await _reviewer(provider, held, new, proposal, findings)
            if confirmed:
                step["outcome"] = "reviewer"
                _log(entry_id, "reviewer", round_)
                return ReplaceProposal(entry_id, field_name, held, new, proposal, True, "reviewer", round_, trail)
            step["outcome"] = "revise"
            finding = finding or " ".join(f.describe() for f in findings)
        if round_ == bound:
            break
        previous = proposal
        proposal, withdrawn = await _author(provider, held, new, previous, finding)
    if not trail:
        return None
    _log(entry_id, "unresolved", len(trail))
    return ReplaceProposal(entry_id, field_name, held, new, new, False, "unresolved", len(trail), trail)


def _log(entry_id: str, outcome: str, rounds: int) -> None:
    # Never bullet text in the application log (personal data): ids and counts.
    logger.info("bullet replace loop: %s after %d round(s) (entry %s)", outcome, rounds, entry_id)


def still_held(profile_json: Any, candidates: list[dict[str, str]]) -> list[dict[str, str]]:
    """The candidates whose two bullets the COMMITTED profile still holds on the
    same work entry: the held one in its field, the new one in any bullet field
    (the employer guard or another check may have held the new bullet back)."""
    entries = {
        str(e.get("id")): e
        for e in (profile_json or {}).get("work_experience") or []
        if isinstance(e, dict)
    }
    out = []
    for c in candidates:
        e = entries.get(str(c.get("entry_id")))
        if e is None:
            continue
        bullets = [b for f in ("responsibilities", "achievements") for b in e.get(f) or []]
        if c.get("held") in (e.get(c.get("field")) or []) and c.get("new") in bullets:
            out.append(c)
    return out


async def propose_replacements(
    provider: LLMProvider | None, candidates: list[dict[str, str]]
) -> list[ReplaceProposal]:
    """Every reworded candidate through the loop, one after the other (a turn
    has about one; q9: ~1.1 per interview)."""
    if provider is None:
        return []
    out: list[ReplaceProposal] = []
    for c in candidates:
        result = await run_replace_loop(
            provider, entry_id=c["entry_id"], field_name=c["field"], held=c["held"], new=c["new"],
        )
        if result is not None:
            clog.debug("bullet replace loop: %s proposal %r for held %r", result.path, result.proposal, result.held)
            out.append(result)
    return out
