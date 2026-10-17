# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E068 / ADR-094 (draft) — CV pipeline v2: the model writes, code checks.

Behind ``CV_PIPELINE=v2`` (default ``v1``; an internal dev switch until US368 flips
it — not operator surface). v1 stays byte-for-byte the 0.43 pipeline (RULING R-3).

What v2 changes, and only that (the per-step inventory is US363's,
``w1/inventory-cv.md``):

* **The writer writes the vault-sourced fields** (US364): each work entry's ``role``
  (a stored or recorded name — ADR-094 cl. 5, ruling W1-2) and ``industry_context``,
  the ``education`` rows (degree/institution as recorded names, field of study in the
  document language) and the ``languages`` rows (name and level in the document
  language). :func:`compose_v2` joins them by id; nothing is translated by code.
* **No compose-tail pen.** :func:`compose_v2` is the join by id only (contact,
  employer, dates, certifications, role figures, photo, the writer's own project
  nesting). No restore, no cap, no skill re-add/drop/dedup/re-spell, no measured-
  outcome rewrite, no project append, no language preseed.
* **The tail's detectors become checks** (US365/US366): :func:`v2_checks` runs the v1
  passes on a COPY and reports what they would have changed as
  :class:`~applire.services.reviewer.CheckFinding`s — one implementation per detector
  (ADR-066), no new heuristic. They feed the terminal review loop through
  ``review_and_refine(checks_fn=…)``; the loop never edits a draft for them.

After the last model call no code changes the document's text (ADR-094 cl. 1). The
only values code chooses are vault values chosen BY ID, and — when the model fails
to supply a field — the stored value (a block, never a rewrite of model text).
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence

from applire.services.reviewer import CheckFinding

if TYPE_CHECKING:
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv_budget import BudgetResult

logger = logging.getLogger(__name__)

PIPELINE_V1 = "v1"
PIPELINE_V2 = "v2"

#: The terminal loop's retry budget under v2 (two corrector rounds). v1's terminal
#: budget (CV_TERMINAL_REVIEW_MAX_RETRIES, default 1) assumed the deterministic tail
#: repairs what the loop leaves; v2 has no tail, so the loop gets the drafting loop's
#: budget instead (LLM_REVIEW_MAX_RETRIES' default).
V2_TERMINAL_MAX_RETRIES = 2

#: Bound on findings per round (SF-WRITE.45 draft): enough to cover every class once,
#: never a wall of demands that starves the reviewer's own blocking issues.
MAX_FINDINGS_PER_ROUND = 8
_MAX_ITEMS_PER_FINDING = 6


def cv_pipeline() -> str:
    """``"v2"`` when ``CV_PIPELINE=v2``, else ``"v1"`` (read at call time)."""
    raw = (os.environ.get("CV_PIPELINE", PIPELINE_V1) or "").strip().lower()
    return PIPELINE_V2 if raw == PIPELINE_V2 else PIPELINE_V1


# --------------------------------------------------------------------------- #
# names: stored + recorded aliases (ADR-094 cl. 5, ruling W1-2 = B)
# --------------------------------------------------------------------------- #
def norm_name(value: object) -> str:
    """The closed, mechanical normalisation ADR-076 cl. 4 allows: unicode NFKC,
    case, whitespace, surrounding punctuation. Nothing semantic."""
    if not isinstance(value, str):
        return ""
    s = unicodedata.normalize("NFKC", value).casefold()
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" .,;:-–—")


def recorded_names(stored: object, aliases: object) -> list[str]:
    """The stored name first, then each recorded alias, deduplicated by norm."""
    out: list[str] = []
    seen: set[str] = set()
    for v in [stored, *(aliases if isinstance(aliases, list) else [])]:
        n = norm_name(v)
        if n and n not in seen:
            seen.add(n)
            out.append(str(v).strip())
    return out


def pick_recorded(model_value: object, stored: object, aliases: object) -> tuple[str, bool]:
    """``(name, member)``: the recorded form the model chose (its recorded spelling),
    or the stored name with ``member=False`` when the model returned anything else
    (a finding; on the last round the stored name stays — a block, not a rewrite).
    An empty model value is not a choice: the stored name, ``member=True``."""
    names = recorded_names(stored, aliases)
    stored_s = str(stored or "").strip()
    n = norm_name(model_value)
    if not n:
        return stored_s, True
    for name in names:
        if norm_name(name) == n:
            return name, True
    return stored_s, False


# --------------------------------------------------------------------------- #
# carry: a later model pass must not silently drop a v2 field
# --------------------------------------------------------------------------- #
_V2_WORK_FIELDS = ("role", "company", "industry_context", "budget_managed")
_V2_TOP_FIELDS = ("education", "languages")


def carry_v2_fields(new: Any, previous: Any) -> Any:
    """Restore v2 fields a later model pass (language pass, attribution round,
    corrector) dropped, from the PREVIOUS model draft — a selection between two model
    outputs, never code-authored text. A no-op for a v1 draft (it has no such field)."""
    if not isinstance(new, dict) or not isinstance(previous, dict):
        return new
    out = dict(new)
    for key in _V2_TOP_FIELDS:
        if key in previous and key not in out:
            out[key] = copy.deepcopy(previous[key])
    prev_by_id = {
        str(w.get("id") or ""): w for w in previous.get("work") or [] if isinstance(w, dict)
    }
    if isinstance(out.get("work"), list):
        work = []
        for w in out["work"]:
            if isinstance(w, dict):
                pw = prev_by_id.get(str(w.get("id") or ""))
                if pw is not None:
                    missing = {f: pw[f] for f in _V2_WORK_FIELDS if f in pw and f not in w}
                    if missing:
                        w = {**w, **missing}
            work.append(w)
        out["work"] = work
    return out


# --------------------------------------------------------------------------- #
# compose: the join by id, nothing else
# --------------------------------------------------------------------------- #
@dataclass
class ComposeNotes:
    """Facts compose_v2 established while joining — read by :func:`v2_checks`."""

    names_not_recorded: list[tuple[str, str, str, list[str]]] = field(default_factory=list)
    # (location, model value, stored value, recorded names)
    rows_not_returned: list[tuple[str, str]] = field(default_factory=list)
    # (location, what the writer should return)


def _by_id(rows: Any) -> dict[str, dict]:
    return {
        str(r.get("id") or ""): r for r in rows or [] if isinstance(r, dict) and r.get("id")
    }


def compose_v2(
    prose: dict,
    profile_json: dict,
    *,
    raw_profile_json: dict | None = None,
    notes: ComposeNotes | None = None,
) -> "TailoredCVData":
    """The v2 compose: join the writer's draft onto the vault BY ID (ADR-094 cl. 2).

    Vault values joined: contact, employer, dates (``assemble_tailored_cv``),
    certifications, ``team_size`` / ``budget_managed``, photo. Model values joined by
    id: work ``role`` / ``company`` (recorded names only), ``industry_context`` (only
    for a role whose vault entry states one — #229's furniture rule), education
    ``degree`` / ``institution`` (recorded names) and ``field``, language ``language``
    (recorded name or the closed DE/EN table) and ``level``. A field the model did not
    return keeps the stored value and is noted for the checks."""
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import (
        _apply_certifications,
        _apply_role_facts,
        _dedup_languages,
        assemble_tailored_cv,
    )
    from applire.services.profile.language_names import LANGUAGE_NAME_DE_EN

    notes = notes if notes is not None else ComposeNotes()
    tailored = TailoredCVData.model_validate(assemble_tailored_cv(prose, profile_json))
    tailored = _apply_certifications(tailored, profile_json)
    tailored = _apply_role_facts(tailored, profile_json, preseed=None)

    vault_work = _by_id(profile_json.get("work_experience"))
    prose_work = _by_id(prose.get("work"))
    work = []
    for i, entry in enumerate(tailored.work_history):
        v = vault_work.get(entry.id) or {}
        p = prose_work.get(entry.id) or {}
        update: dict[str, Any] = {}
        for fld, alias_key in (("role", "role_aliases"), ("company", "company_aliases")):
            picked, member = pick_recorded(p.get(fld), v.get(fld), v.get(alias_key))
            update[fld] = picked
            if not member:
                notes.names_not_recorded.append(
                    (f"work {entry.id} {fld}", str(p.get(fld)), str(v.get(fld) or ""),
                     recorded_names(v.get(fld), v.get(alias_key)))
                )
        vault_industry = v.get("industry_context")
        if vault_industry:
            model_industry = p.get("industry_context")
            if isinstance(model_industry, str) and model_industry.strip():
                update["industry_context"] = model_industry.strip()
            else:
                notes.rows_not_returned.append(
                    (f"work {entry.id} industry_context",
                     "the role's industry line, in the document language")
                )
        vault_budget = v.get("budget_managed")
        if vault_budget:
            from applire.services.cv import _figures_preserved

            model_budget = p.get("budget_managed")
            if isinstance(model_budget, str) and model_budget.strip() and _figures_preserved(
                vault_budget, model_budget
            ):
                update["budget_managed"] = model_budget.strip()
            elif isinstance(model_budget, str) and model_budget.strip():
                # A figure changed in translation (SF-WRITE.44): the stored line stays.
                notes.names_not_recorded.append(
                    (f"work {entry.id} budget_managed", model_budget, str(vault_budget),
                     [str(vault_budget)])
                )
            else:
                notes.rows_not_returned.append(
                    (f"work {entry.id} budget_managed",
                     "the budget line, words in the document language, every figure unchanged")
                )
        work.append(entry.model_copy(update=update))

    vault_edu = [e for e in profile_json.get("education") or [] if isinstance(e, dict)]
    prose_edu = _by_id(prose.get("education"))
    education = []
    for e_vault, e_out in zip(vault_edu, tailored.education):
        p = prose_edu.get(str(e_vault.get("id") or ""))
        if p is None:
            notes.rows_not_returned.append(
                (f"education {e_vault.get('id')}",
                 "degree, institution and field of study, in the document language")
            )
            education.append(e_out)
            continue
        update = {}
        for fld, alias_key in (("degree", "degree_aliases"), ("institution", "institution_aliases")):
            picked, member = pick_recorded(p.get(fld), e_vault.get(fld), e_vault.get(alias_key))
            update[fld] = picked
            if not member:
                notes.names_not_recorded.append(
                    (f"education {e_vault.get('id')} {fld}", str(p.get(fld)),
                     str(e_vault.get(fld) or ""),
                     recorded_names(e_vault.get(fld), e_vault.get(alias_key)))
                )
        if e_vault.get("field"):
            fld_val = p.get("field")
            if isinstance(fld_val, str) and fld_val.strip():
                update["field"] = fld_val.strip()
            else:
                notes.rows_not_returned.append(
                    (f"education {e_vault.get('id')} field", "the field of study, in the document language")
                )
        education.append(e_out.model_copy(update=update))

    vault_lang = _dedup_languages(
        [l for l in profile_json.get("languages") or [] if isinstance(l, dict)]
    )
    prose_lang = _by_id(prose.get("languages"))
    languages = []
    for l_vault, l_out in zip(vault_lang, tailored.languages):
        p = prose_lang.get(str(l_vault.get("id") or ""))
        if p is None:
            notes.rows_not_returned.append(
                (f"languages {l_vault.get('id')}", "the language name and level, in the document language")
            )
            languages.append(l_out)
            continue
        update = {}
        stored = l_vault.get("language") or ""
        picked, member = pick_recorded(p.get("language"), stored, l_vault.get("aliases"))
        if not member:
            # The closed DE/EN language-name table is a fact (ADR-062 amended 2026-10-07).
            canon = LANGUAGE_NAME_DE_EN.get(norm_name(stored), norm_name(stored))
            if LANGUAGE_NAME_DE_EN.get(norm_name(p.get("language")), norm_name(p.get("language"))) == canon:
                picked, member = str(p.get("language")).strip(), True
        update["language"] = picked
        if not member:
            notes.names_not_recorded.append(
                (f"languages {l_vault.get('id')} language", str(p.get("language")), str(stored),
                 recorded_names(stored, l_vault.get("aliases")))
            )
        if isinstance(p.get("level"), str) and p["level"].strip():
            update["level"] = p["level"].strip()
        elif l_vault.get("level"):
            notes.rows_not_returned.append(
                (f"languages {l_vault.get('id')} level", "the level, in the document language")
            )
        languages.append(l_out.model_copy(update=update))

    tailored = tailored.model_copy(
        update={"work_history": work, "education": education, "languages": languages}
    )
    photo_url = ((raw_profile_json or {}).get("personal_info") or {}).get("photo_url")
    if photo_url:
        tailored = tailored.model_copy(
            update={"contact": tailored.contact.model_copy(update={"photo_url": photo_url})}
        )
    return tailored


# --------------------------------------------------------------------------- #
# checks: the v1 passes as detectors on a copy
# --------------------------------------------------------------------------- #
@dataclass
class V2CheckContext:
    profile_json: dict
    keyword_ledger: list[dict]
    job_dict: dict
    language: str
    budget: "BudgetResult | None" = None
    pins: Sequence = ()
    #: (canonical prose of the measured draft, page_count, target) — the page check
    #: fires only for the exact draft that was rendered (a fact, never an estimate).
    measured: tuple[str, int, int] | None = None


def canonical(d: Any) -> str:
    return json.dumps(d, sort_keys=True, default=str, ensure_ascii=False)


def _fmt(items: Sequence[str]) -> str:
    items = list(items)
    shown = ", ".join(f"“{x}”" for x in items[:_MAX_ITEMS_PER_FINDING])
    more = len(items) - _MAX_ITEMS_PER_FINDING
    return shown + (f" (+{more} more)" if more > 0 else "")


def v2_checks(
    prose: dict,
    composed: "TailoredCVData",
    notes: ComposeNotes,
    ctx: V2CheckContext,
) -> list[CheckFinding]:
    """Every deterministic fact about one composed v2 draft, as loop findings.

    Each detector is the v1 pass itself, run on a COPY: its would-be edit is the
    finding, so the detection logic has one implementation (ADR-066) and the pen
    moves to the corrector (ADR-076 cl. 2's SIGNAL fate). Blocking = complying is
    always right; advisory = the consequence is a judgement (ADR-076 cl. 4)."""
    from applire.services import cv as cv_mod

    findings: list[CheckFinding] = []

    # 1. role budget (US365) — role bullets + nested project bullets, the count
    #    condense_to_budget uses.
    if ctx.budget is not None:
        over = []
        for w in composed.work_history:
            rb = ctx.budget.roles.get(w.id)
            if rb is None:
                continue
            n = len(w.bullets) + sum(len(p.bullets) for p in w.projects)
            if n > rb.max_bullets:
                over.append(f"{w.id} ({w.company}): {n} bullets, budget {rb.max_bullets}")
        if over:
            findings.append(CheckFinding(
                check="cv.role_budget",
                text=(
                    "Over the per-role bullet budget (role + nested project bullets): "
                    + "; ".join(over)
                    + ". Swap, do not add: keep the strongest evidence for this job, merge "
                    "two related facts into one bullet, or drop the weakest — never a "
                    "pinned fact or a measured outcome."
                    + _budget_candidates(composed, ctx)
                ),
            ))

    # 2. page length (fact only for the draft that was rendered)
    if ctx.measured is not None and ctx.measured[0] == canonical(prose):
        _, pages, target = ctx.measured
        if pages > target:
            findings.append(CheckFinding(
                check="cv.page_length",
                text=(
                    f"The rendered CV is {pages} pages; the target is {target}. Shorten "
                    "older or less relevant roles toward one strong line; keep measured "
                    "outcomes."
                ),
            ))

    # 3. names (ruling W1-2) and 4. vault rows not returned (US364)
    if notes.names_not_recorded:
        parts = [
            f"{loc}: “{got}” is not a recorded name — use one of {_fmt(names)}"
            for loc, got, _stored, names in notes.names_not_recorded[:_MAX_ITEMS_PER_FINDING]
        ]
        findings.append(CheckFinding(
            check="cv.recorded_name",
            text=(
                "; ".join(parts)
                + ". Pick the recorded form that is in the document language when one "
                "exists; never translate or invent a name."
            ),
        ))
    if notes.rows_not_returned:
        parts = [f"{loc} ({what})" for loc, what in notes.rows_not_returned[:_MAX_ITEMS_PER_FINDING]]
        findings.append(CheckFinding(
            check="cv.vault_field_missing",
            text=(
                "Return these profile fields, by id, written in the document language: "
                + "; ".join(parts)
            ),
        ))

    # 5–8. the v1 skill passes as detectors — run IN v1's ORDER on one copy, so the
    #    findings describe v1's own final skills decision and can never contradict each
    #    other (v1's echo-drop removes a chip that its narrative-restore re-adds — the
    #    SF-WRITE.37 shape; measured on the first v2 smoke run, where "SLOs" was both
    #    "remove" and "add"). A chip v1 would remove and then re-add yields no finding.
    findings.extend(_skill_findings(composed, ctx, cv_mod))

    # 9. a project heading with nothing under it
    empty = [
        p.name for w in composed.work_history for p in w.projects if not p.bullets
    ] + [p.name for p in composed.projects if not p.bullets]
    if empty:
        findings.append(CheckFinding(
            check="cv.empty_project",
            text=f"Projects without a single bullet: {_fmt(empty)}. Give each one bullet or leave it out.",
        ))

    blocking = [f for f in findings if f.blocking]
    advisory = [f for f in findings if not f.blocking]
    return (blocking + advisory)[:MAX_FINDINGS_PER_ROUND]


def _skill_findings(composed: "TailoredCVData", ctx: V2CheckContext, cv_mod: Any) -> list[CheckFinding]:
    """The v1 skills tail (dedup → echo-drop → JD tailoring → spelling → narrative
    restore, ``_compose_document``'s order) run on a copy; each step's would-be edit,
    filtered against the chain's FINAL list, is one finding."""
    def step(fn, data, *args, **kwargs):
        try:
            return fn(data.model_copy(deep=True), *args, **kwargs)
        except Exception:  # noqa: BLE001 — a detector failure loses its finding only
            logger.exception("v2 check detector %s raised; finding skipped", getattr(fn, "__name__", fn))
            return data

    s0 = composed.model_copy(deep=True)
    s1 = step(cv_mod._dedup_skills, s0, pins=ctx.pins)
    s2 = step(cv_mod._drop_ungrounded_jd_echo_skills, s1, ctx.profile_json, ctx.job_dict,
              ctx.keyword_ledger, pins=ctx.pins)
    s3 = step(cv_mod._tailor_skills_to_jd, s2, ctx.profile_json, ctx.job_dict,
              ctx.keyword_ledger, pins=ctx.pins)
    s4 = step(cv_mod._restore_skill_spelling, s3, ctx.profile_json)
    s5 = step(cv_mod._restore_narrative_named_skills, s4, ctx.profile_json,
              ctx.keyword_ledger, document_language=ctx.language)
    original = list(s0.skills)
    final = set(s5.skills)

    def removed(a, b):
        return [x for x in a.skills if x not in b.skills and x not in final]

    def added(a, b):
        return [x for x in b.skills if x not in a.skills and x not in original]

    out: list[CheckFinding] = []
    untied = removed(s1, s2)
    if untied:
        out.append(CheckFinding(
            check="cv.skill_untied",
            text=(
                f"Skills entries with no tie to the profile (a posting phrase, not a "
                f"competence the profile records): {_fmt(untied)}. Remove them from the "
                "skills list; where the profile backs the substance, say it in the "
                "bullet that owns the evidence."
            ),
        ))
    respelled = [(b, a) for b, a in zip(s3.skills, s4.skills) if b != a and b in original]
    if respelled:
        out.append(CheckFinding(
            check="cv.skill_spelling",
            text=(
                "Skills entries that alter a name the profile records: "
                + "; ".join(f"“{b}” — the profile's name is “{a}”" for b, a in respelled[:_MAX_ITEMS_PER_FINDING])
                + ". Names are copied exactly."
            ),
        ))
    over_cap = removed(s2, s3)
    if over_cap:
        out.append(CheckFinding(
            check="cv.skills_cap",
            text=(
                f"The skills list is over its cap of {cv_mod.CV_MAX_SKILLS} entries; the "
                f"least relevant to this posting are {_fmt(over_cap)}. Drop entries until "
                "it fits; keep every skill the posting requires."
            ),
        ))
    dupes = removed(s0, s1)
    if dupes:
        out.append(CheckFinding(
            check="cv.skill_duplicate",
            blocking=False,
            text=(
                f"These skills entries repeat another entry on the page (one is contained "
                f"in the other): {_fmt(dupes)}. If they name one competence, keep one form."
            ),
        ))
    narrated = added(s4, s5)
    if narrated:
        out.append(CheckFinding(
            check="cv.skill_named_in_bullet",
            blocking=False,
            text=(
                f"Named in a bullet but absent from the skills list: {_fmt(narrated)}. Add "
                "the ones a recruiter should find in the list, in the document language — "
                "unless the list already carries the same competence."
            ),
        ))
    return out


def _budget_candidates(composed: "TailoredCVData", ctx: V2CheckContext) -> str:
    """ADR-076 cl. 2's split fate for the cap: the deterministic RANKING stays
    (``condense_to_budget`` — sole carriers of a claimable concept last, figure-bearing
    bullets last, later-listed first; the same ``rank_cuts`` ``_cap_bullets`` uses) and
    is handed over as candidates; the corrector decides. Fact: the ranking's output."""
    try:
        from applire.services.cv_budget import condense_to_budget

        before = composed.model_dump(mode="json")
        after, changed = condense_to_budget(before, ctx.budget, 1, pins=ctx.pins)
        if not changed:
            return ""
        kept = {
            b for w in after.get("work_history") or [] for b in (w.get("bullets") or [])
        } | {
            b for w in after.get("work_history") or [] for p in (w.get("projects") or [])
            for b in (p.get("bullets") or [])
        }
        cut = [
            b for w in before.get("work_history") or []
            for b in (w.get("bullets") or []) + [x for p in (w.get("projects") or []) for x in (p.get("bullets") or [])]
            if b not in kept
        ]
        if not cut:
            return ""
        return (
            " Lowest-ranked by the budget ranking (candidates to merge or drop; your call): "
            + _fmt([c[:120] for c in cut])
        )
    except Exception:  # noqa: BLE001
        logger.exception("v2 budget-candidate ranking failed; finding without candidates")
        return ""


# --------------------------------------------------------------------------- #
# wiring helpers (called from services/cv.py under CV_PIPELINE=v2 only)
# --------------------------------------------------------------------------- #
def v2_writer_schema() -> dict | None:
    """#767: the v2 writer/corrector response schema when ``LLM_STRUCTURED_OUTPUT=auto``
    (the reconcile seam's switch, read at call time), else ``None`` (JSON mode)."""
    try:
        from applire.config import settings

        if (settings.llm_structured_output or "off").lower() != "auto":
            return None
        from applire.prompts.cv_tailoring import V2_WRITER_JSON_SCHEMA

        return V2_WRITER_JSON_SCHEMA
    except Exception:  # noqa: BLE001 — a schema is an optimisation, never a failure
        logger.debug("cv v2: could not build the writer response schema; JSON mode")
        return None


def required_vault_skills(profile_json: dict, job_dict: dict, keyword_ledger: list[dict] | None) -> list[str]:
    """The JD-required skills the vault holds — #192's guarantee SET, handed to the
    writer as a fact instead of re-added after it (US365). Same tier predicate as the
    v1 guarantee (``cv._jd_required_tier_fn``, ADR-066)."""
    from applire.services.cv import _jd_required_tier_fn
    from applire.services.profile.reconcile.stance import claimable_skill_names

    tier = _jd_required_tier_fn(job_dict, keyword_ledger)
    out: list[str] = []
    for name in claimable_skill_names(profile_json):
        if tier(name) == 0 and name not in out:
            out.append(name)
    return out


def build_v2_fields_block_for(profile_json: dict, job_dict: dict, keyword_ledger: list[dict] | None) -> str:
    from applire.constants import CV_MAX_SKILLS
    from applire.prompts.cv_tailoring import build_v2_fields_block

    return build_v2_fields_block(
        profile_json,
        required_vault_skills=required_vault_skills(profile_json, job_dict, keyword_ledger),
        skills_cap=CV_MAX_SKILLS,
    )


@dataclass
class V2Terminal:
    """What the v2 terminal loop needs beyond v1's arguments."""

    ctx: V2CheckContext
    max_retries: int = V2_TERMINAL_MAX_RETRIES
    notes_by_draft: dict[str, ComposeNotes] = field(default_factory=dict)
    open_findings: list = field(default_factory=list)

    def compose(self, prose: dict, profile_json: dict, raw_profile_json: dict | None) -> "TailoredCVData":
        notes = ComposeNotes()
        composed = compose_v2(prose, profile_json, raw_profile_json=raw_profile_json, notes=notes)
        self.notes_by_draft[canonical(prose)] = notes
        return composed

    def note_measure(self, prose: dict, measured: Any) -> None:
        if measured is None:
            self.ctx.measured = None
        else:
            self.ctx.measured = (canonical(prose), measured.page_count, measured.target)

    def checks_fn(self, subject_for):
        def _fn(draft: dict) -> list[CheckFinding]:
            composed = subject_for(draft)
            notes = self.notes_by_draft.get(canonical(draft)) or ComposeNotes()
            return v2_checks(draft, composed, notes, self.ctx)
        return _fn

    def on_checks_settle(self, report) -> None:
        self.open_findings = list(report.open_findings)
        logger.info(
            "CV_V2_CHECKS_SETTLED path=%s open=%s rounds_with_findings=%d",
            report.path, [f.check for f in report.open_findings], report.rounds_with_findings,
        )
