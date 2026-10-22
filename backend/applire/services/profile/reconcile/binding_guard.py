# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bug #801 (ADR-046 amended 2026-10-22) — the committer refuses a model skill
binding that names a different thing.

**Earned (applire-prompt-first, category C).** No prompt rule asks for a binding
of "REST APIs" to "Mentoring" — the `match_existing` and `upsert_skill.target`
descriptions allow it only for the same skill under another name. The UX audit
(2026-10-20, luna, reasoning off) still wrote that binding, and the alias with
it. Replays of the persisted input (the audit's pre-import snapshot + the same
persona's LinkedIn extraction, the stack's op schema, n=10 on the audit's own
code, n=15 on today's): every run that bound "REST APIs" bound it to "REST API
design" (27/27) — the model's INTENT was right and the ID it wrote slipped. The
same slip shows elsewhere: "Amazon Web Services (AWS)" targeted at the id of the
adjacent "ECS" (replay), "Stripe" bound to "PostgreSQL" (captured interview call
2026-10-21). An id is the weakest channel the model writes, and a slip is not a
rule gap a sentence can close.

Two FACTS (ADR-062 cl. 1), never a judgement of whether two skills are the same:

(a) ``target_mismatch`` — the op's ``existing`` (the name the model says it
    means, asked for since this amendment) equals neither the target's own name
    nor one of its recorded aliases;
(b) ``category_mismatch`` — the incoming skill's stated category and the
    target's are ``technical`` and ``soft``. Only that pair: technical↔domain and
    domain↔soft drift between two extractions of the same skill (four genuine
    such bindings in the captured logs, none technical↔soft).

A binding whose incoming name already IS one of the target's names is never
refused — identity by the same words is a fact (a category that drifted between
two extractions of "Mentoring" must not split it).

A refused binding keeps the entry (``refuse_false_skill_bindings``): the import
door rewrites it to an ordinary, untargeted ``upsert_skill`` from the incoming
DOCUMENT skill, which then takes the near-dupe / containment / new-entry path
like any other skill; no alias, no receipt. Callers, one seam test each
(``tests/unit/test_801_binding_guard.py``): this pre-pass on both import paths,
``apply._targeted_skill`` (every door), ``apply._apply_match_existing`` (every
door, check (a) only — the op carries no category).
"""
from __future__ import annotations

import logging
from typing import Any, Sequence

from applire.services.profile.reconcile import aliases as _aliases
from applire.services.profile.reconcile.ops import MatchExisting, UpsertSkill

logger = logging.getLogger(__name__)

#: The one category pair a skill binding may not cross (see the module docstring).
_EXCLUSIVE_CATEGORIES = frozenset({"technical", "soft"})


def skill_binding_refusal(
    incoming_name: str | None,
    incoming_category: str | None,
    existing_echo: str | None,
    target: Any,
) -> str | None:
    """The reason a model binding of ``incoming_name`` to the skill ``target`` is
    refused, or ``None`` when it stands. Facts only — see the module docstring."""
    target_names = _aliases.names_of(target, "name", "skills")
    if _aliases.norm(incoming_name) in target_names:
        return None
    if existing_echo is not None and _aliases.norm(existing_echo) and (
        _aliases.norm(existing_echo) not in target_names
    ):
        return "target_mismatch"
    pair = {_aliases.norm(incoming_category), _aliases.norm(getattr(target, "category", None))}
    if pair == _EXCLUSIVE_CATEGORIES:
        return "category_mismatch"
    return None


def _document_skill(incoming: Any, name: str | None) -> Any | None:
    """The ONE incoming document skill named ``name`` (exact ``_norm``), or None —
    the binder's exactly-one rule (``import_witness.match_existing_bindings``)."""
    wanted = _aliases.norm(name)
    hits = [s for s in (getattr(incoming, "skills", None) or []) if _aliases.norm(s.name) == wanted]
    return hits[0] if len(hits) == 1 else None


def refuse_false_skill_bindings(
    incoming: Any,
    profile: Any,
    ops: Sequence[Any],
) -> tuple[list[Any], list[dict[str, str]]]:
    """Import door (fast path and every ADR-047 slice): the op batch with every
    refused skill binding rewritten, plus one record per refusal.

    * a refused ``match_existing`` on a skill -> an untargeted ``upsert_skill``
      built from the ONE incoming document skill of that name (its own name,
      category, stated level and span); when there is not exactly one, the op
      bound no entry to begin with and is removed (never a skill under the
      model's own spelling);
    * a refused targeted ``upsert_skill`` -> the same op without ``target`` /
      ``existing``.

    The returned list is what the applier, the alias writer and the witness all
    see, so none of them can bind through a refused op.
    """
    skills_by_id = {s.id: s for s in getattr(profile, "skills", None) or [] if getattr(s, "id", None)}
    out: list[Any] = []
    refused: list[dict[str, str]] = []
    for op in ops:
        if isinstance(op, MatchExisting) and op.target in skills_by_id:
            entry = _document_skill(incoming, op.incoming)
            reason = skill_binding_refusal(
                op.incoming, getattr(entry, "category", None), op.existing, skills_by_id[op.target]
            )
            if reason is None:
                out.append(op)
                continue
            if entry is not None:
                out.append(UpsertSkill(
                    name=entry.name,
                    category=entry.category,
                    proficiency=getattr(entry, "proficiency", None),
                    years_experience=getattr(entry, "years_experience", None),
                ))
        elif isinstance(op, UpsertSkill) and op.target in skills_by_id:
            reason = skill_binding_refusal(op.name, op.category, op.existing, skills_by_id[op.target])
            if reason is None:
                out.append(op)
                continue
            out.append(op.model_copy(update={"target": None, "existing": None}))
        else:
            out.append(op)
            continue
        target = skills_by_id[op.target]
        incoming_name = op.incoming if isinstance(op, MatchExisting) else op.name
        refused.append({"op": op.op, "incoming": incoming_name, "target": op.target,
                        "target_name": target.name, "reason": reason})
        logger.warning(
            "reconcile: refused %s binding %r -> %r (%s); the entry is kept as its own skill (#801)",
            op.op, incoming_name, target.name, reason,
        )
    return out, refused
