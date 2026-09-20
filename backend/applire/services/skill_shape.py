# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""F-9 (#672 line 126) — is a delivered skills entry a COMPETENCE, or a phrase lifted
out of the document's own prose?

**The producer, named on the real path before a line of this was written.** The founder
UAT of 2026-09-20 delivered `roadmap`, `budget estimation`, `AI automation use case`,
`application processes`, `enterprise-scale`, `vendor selection`, `System Owner` and
`Engineering` as skill chips. Six of the eight appear nowhere in the KION posting, so
they are not JD echoes; none is a plausible vault skill name. Running the REAL skills
pipeline over that shape (2026-09-20, no provider call) gives a no-op at every stage:

===========================================  ==========================================
pass                                          why it cannot remove such an entry
===========================================  ==========================================
``_dedup_skills``                             there is no duplicate
``_drop_ungrounded_jd_echo_skills`` (#250)    scoped to JD ECHOES by design; a phrase the
                                              posting does not contain is not an echo
``_tailor_skills_to_jd``                      reorders and caps; its pool is the writer's
                                              own tags plus vault skills
``_restore_skill_spelling``                   rewrites only on an exact or acronym match
``_restore_narrative_named_skills``           appends only, never removes
===========================================  ==========================================

So the **producer is the writer's own ``skills`` output**, and ``prompts/cv_tailoring.py``
rule 7 already forbids it in words: *"a skill is a named competence, tool or method the
candidate holds in the vault"*, *"A requirement phrase … is not a skill"*. Category **C**
(`applire-prompt-first`): the rule exists and was violated. What was missing is the
reader — and worse than missing: ``prompts/review_cv_tailoring.py``'s SKILLS-LIST SCOPE
paragraph read as a blanket *"never flag such a skill as fabricated, ungrounded…"*, which
covered exactly this shape. That contradiction across the writer/reviewer seam (ADR-062
clause 4) is fixed in the same change; this module supplies the fact the new check reads.

**Measured side-finding, recorded because it bounds what a deterministic fix could do.**
Two of the eight (`roadmap`, `Engineering`) survive the #250 echo-drop even when they ARE
JD echoes, because ``ats_audit.skills_page_dupe`` counts bare single-token containment as
a vault tie: `Engineering` ⊂ `Requirements Engineering`. That leniency is deliberate and
correct for the DUPE question it was built for, and tightening it here is not available —
"is `Engineering` the same competence as `Requirements Engineering`?" is a judgement, and
so is "is `Python` the same as `Python 3`?", which the same rule protects. ADR-062 clause
1 puts that with the model. Hence a FACT to the reviewer, never a deletion pass.

**What the fact is, exactly** (and what it is not): an entry is reported as
*prose-derived* when BOTH hold — it page-dupes NO attested vault form (the same
``skills_page_dupe`` tie ``_drop_ungrounded_jd_echo_skills`` uses, over the same
``claimable_skill_names`` + ``WorkEntry.technologies`` pool, ADR-066) AND its own text
occurs verbatim inside a bullet or the summary of the delivered document. Both halves are
literal presence, the class ADR-062 calls a fact. Whether the phrase nevertheless names a
real competence stays the reviewer's call, and the block says so.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable

from applire.services.ats_audit import _norm, join_corpus_fragments, skills_page_dupe, surface_present

logger = logging.getLogger(__name__)

#: How many prose-derived entries the block names before it starts counting. A skills
#: list is capped at ``CV_MAX_SKILLS`` (24); naming more than six turns the block into a
#: wall and the reviewer stops reading it. The full count always survives in the text.
_NAMED_MAX = 6


def _vault_forms(profile_json: dict[str, Any]) -> list[str]:
    """The attested vault forms a skills entry may tie to — THE same pool
    ``_drop_ungrounded_jd_echo_skills`` uses, so the two can never disagree about what
    counts as vault-backed (ADR-066)."""
    from applire.services.profile.reconcile.stance import claimable_skill_names

    forms: list[str] = list(claimable_skill_names(profile_json))
    for entry in profile_json.get("work_experience") or []:
        if not isinstance(entry, dict):
            continue
        for tech in entry.get("technologies") or []:
            if isinstance(tech, str) and tech.strip():
                forms.append(tech)
    return forms


def _prose_texts(document: dict[str, Any]) -> list[str]:
    """The document's own PROSE: the summary plus every role and project bullet.

    Deliberately not "every leaf string": the skills list itself is a leaf string set, and
    an entry always occurs in it.
    """
    texts: list[str] = []
    summary = document.get("summary")
    if isinstance(summary, str):
        texts.append(summary)
    for entry in document.get("work_history") or []:
        if not isinstance(entry, dict):
            continue
        for bullet in entry.get("bullets") or []:
            if isinstance(bullet, str):
                texts.append(bullet)
        for project in entry.get("projects") or []:
            if not isinstance(project, dict):
                continue
            for bullet in project.get("bullets") or []:
                if isinstance(bullet, str):
                    texts.append(bullet)
    return texts


def _ledger_siblings(
    skill: str, keyword_ledger: list[dict[str, Any]] | None
) -> list[str]:
    """The other surface forms of the Keyword Ledger row this skills entry IS a form of.

    Measured need (2026-09-20, real-provider replay n=3):
    ``_restore_narrative_named_skills`` appends a ledger row's surface form as a chip
    when ANY form of that row is present in the narrative — so the delivered chip can be
    ``roadmap`` while the bullet the presence test matched says ``budget estimation``.
    Without this the scan below reported nothing on 3 of 3 runs whose delivered list
    carried exactly those two chips. This is not a widening of the flagged population by
    judgement: the chip must still tie to no attested vault form, and the presence test
    is the producer's OWN predicate over the producer's OWN corpus, so the fact answers
    "did the pass that appended this chip have its reason in this document's prose".
    """
    if not keyword_ledger:
        return []
    needle = _norm(skill)
    out: list[str] = []
    for entry in keyword_ledger:
        if not isinstance(entry, dict):
            continue
        forms = [entry.get("concept"), *(entry.get("surface_forms") or [])]
        forms = [f for f in forms if isinstance(f, str) and f.strip()]
        if any(_norm(f) == needle for f in forms):
            out.extend(f for f in forms if _norm(f) != needle)
    return out


def prose_derived_skills(
    document: dict[str, Any],
    profile_json: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str]]:
    """Skills entries with no vault tie whose text — or the text of a sibling surface form
    of their own ledger row — occurs in the document's own prose. Pure; a FACT.

    ``keyword_ledger`` is optional and back-compat: without it the scan is the verbatim
    one, which is a strict subset.
    """
    skills = [s for s in (document.get("skills") or []) if isinstance(s, str) and s.strip()]
    if not skills:
        return []
    vault = _vault_forms(profile_json)
    prose_raw = _prose_texts(document)
    prose = [(text, _norm(text)) for text in prose_raw]
    # The producer's own corpus and predicate (`ats_audit.surface_present` over
    # `join_corpus_fragments`, ADR-066) for the sibling-form arm — #415's boundary marker
    # so two unrelated bullets cannot spell a form across their join.
    corpus_norm = _norm(join_corpus_fragments(prose_raw))
    out: list[tuple[str, str]] = []
    for skill in skills:
        if any(skills_page_dupe(skill, form) for form in vault):
            continue  # attested in the vault — never this check's business
        needle = _norm(skill)
        if not needle:
            continue
        hit: str | None = None
        for raw, norm in prose:
            if needle in norm:
                hit = raw
                break
        if hit is None:
            for sibling in _ledger_siblings(skill, keyword_ledger):
                if not surface_present(sibling, corpus_norm):
                    continue
                sib_norm = _norm(sibling)
                hit = next(
                    (raw for raw, norm in prose if sib_norm in norm),
                    f"(via the same ledger row's form \"{sibling}\")",
                )
                break
        if hit is not None:
            out.append((skill, hit))
    return out


def render_skill_shape_check_block(found: Iterable[tuple[str, str]]) -> str:
    """The reviewer's deterministic block — ``""`` when nothing was found.

    States the fact and hands over the judgement in the same breath: a phrase can be
    lifted from a bullet AND still be a real competence the candidate holds, and only a
    reader can tell. English only — reviewer prompts are English.
    """
    found = list(found)
    if not found:
        return ""
    lines = [
        "SKILLS-LIST SHAPE (deterministic scan — this is ground truth, do not re-derive "
        f"it). {len(found)} skills entr{'y' if len(found) == 1 else 'ies'} tie to NO "
        "attested vault form, and each was placed on the page by this document's own "
        "prose — the phrase itself, or another form of the same Keyword Ledger row, is "
        "in the summary or a bullet quoted below. That is a fact, not a verdict: "
        "a lifted phrase can still name a real competence. Decide per entry, and raise "
        "check 12 as BLOCKING with ONE issue per entry, ONLY where the entry is not a "
        "competence, tool or method but a sentence fragment, a responsibility or "
        "activity, a job title, or a setting — the classes writer rule 7 excludes from "
        "the skills list. Say in `feedback` whether to drop the entry or replace it with "
        "the competence the candidate actually holds, in their own vault wording. An "
        "entry not listed below is never a check-12 finding, and a listed entry that DOES "
        "name a competence stays on the page — rule 7's closing line requires a "
        "competence named in a bullet to appear in the skills list:"
    ]
    for skill, source in found[:_NAMED_MAX]:
        lines.append(f'  - "{skill}" — from: "{source[:160]}"')
    if len(found) > _NAMED_MAX:
        lines.append(f"  … and {len(found) - _NAMED_MAX} more of the same shape.")
    return "\n".join(lines)


def skill_shape_reviewer_prompt_fn(
    base_fn: Callable[[str, dict], str],
    profile_json: dict[str, Any],
    keyword_ledger: list[dict[str, Any]] | None = None,
    *,
    structured_document_fn: Callable[[dict], dict[str, Any]] | None = None,
):
    """Wrap a ``reviewer_prompt_fn(source, draft)`` so every round sees the CURRENT
    document's skills-shape state (the ``coverage_reviewer_prompt_fn`` shape, #122).

    ``structured_document_fn`` maps the round's draft to the COMPOSED document, because
    the skills list the reader sees is the POST-pipeline one (`_tailor_skills_to_jd`,
    `_restore_narrative_named_skills`) — a prose-only scan would report a different list
    from the one that ships. Fail-safe: any exception ships the un-decorated prompt.
    """

    def fn(source: str, draft: dict) -> str:
        prompt = base_fn(source, draft)
        try:
            document = structured_document_fn(draft) if structured_document_fn else draft
            found = prose_derived_skills(document, profile_json, keyword_ledger)
            block = render_skill_shape_check_block(found)
            if not block:
                return prompt
            logger.info(
                "SKILLS-LIST SHAPE: %d prose-derived entr(y|ies) this round: %s",
                len(found), [s for s, _ in found],
            )
            return f"{prompt}\n\n{block}"
        except Exception:
            logger.exception(
                "skill_shape: scan failed this round; shipping the un-decorated reviewer "
                "prompt"
            )
            return prompt

    return fn
