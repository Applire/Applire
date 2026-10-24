# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E068 / ADR-094 (draft) — CV pipeline v2: the model writes, code checks.

``CV_PIPELINE=v2`` is the default since ruling W3-7 (2026-10-18); ``CV_PIPELINE=v1``
opts out (in ``.env.example``). v1 stays byte-for-byte the 0.43 pipeline (RULING R-3).

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
    """``"v1"`` when ``CV_PIPELINE=v1``, else ``"v2"`` (read at call time).

    v2 is the default since ruling W3-7 (founder 2026-10-18, US368 gate re-run at
    5c2f056b); v1 stays the one-setting opt-out until its tails are deleted."""
    raw = (os.environ.get("CV_PIPELINE", PIPELINE_V2) or "").strip().lower()
    return PIPELINE_V1 if raw == PIPELINE_V1 else PIPELINE_V2


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
    levels_code_added: list[tuple[str, str, str, list[str]]] = field(default_factory=list)
    # (location, model level, stored level, codes the vault level does not state)


#: CEFR level codes (Council of Europe). A code is a CLAIM about a tested level.
_CEFR_RE = re.compile(r"(?<![A-Za-z0-9])([ABC][12])(?![A-Za-z0-9])")


def cefr_codes_added(model_level: object, stored_level: object) -> list[str]:
    """Codes the model's level carries that the stored level does not — a fact
    (adversarial finding 7: "Fluent" delivered as "Fließend (C1)")."""
    have = set(_CEFR_RE.findall(str(stored_level or "")))
    return sorted(set(_CEFR_RE.findall(str(model_level or ""))) - have)


def cefr_codes_changed(model_level: object, stored_level: object) -> list[str]:
    """Every code on which the two levels differ — added OR dropped. A dropped code
    is an upsell too: "Learning (A2, B1 exam planned)" delivered as "Lernend (B1)"
    states a planned exam as an attained level (measured after the first fix)."""
    have = set(_CEFR_RE.findall(str(stored_level or "")))
    got = set(_CEFR_RE.findall(str(model_level or "")))
    return sorted(have ^ got)


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
        added = cefr_codes_changed(p.get("level"), l_vault.get("level"))
        if isinstance(p.get("level"), str) and p["level"].strip() and added:
            # An upsold level is not delivered: the stored level stays (a selection,
            # like a non-recorded name — ruling W1-2's disposition) and the check says why.
            notes.levels_code_added.append(
                (f"languages {l_vault.get('id')} level", p["level"].strip(),
                 str(l_vault.get("level") or ""), added)
            )
        elif isinstance(p.get("level"), str) and p["level"].strip():
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


def unmentioned_identifiers(texts: Sequence[str], profile_json: dict | None) -> tuple[list[str], list[str]]:
    """786:L136 (Hazelnut build 2): the standard/regulation identifiers ("ISO 4501",
    "DIN EN 9100", "21 CFR") ``texts`` name whose digits the vault never states —
    and, as the remedy's candidates, the identifiers the vault DOES state.

    A FACT (ADR-062 cl. 1: token presence). One implementation (ADR-066): the
    Oracle's #701 predicate ``audit._unmentioned_identifiers`` over the Oracle's own
    vault index, so a draft this check passes is a draft the Oracle will not grade
    ``unbacked`` for an identifier, and vice versa. Measured before the check (H,
    build 1): 3 writer typos of the vault's "ISO 45001" in 1,219 documents, one
    delivered — the Oracle reported them, nothing prevented them."""
    if not isinstance(profile_json, dict):
        return [], []
    from applire.services.oracle.audit import _identifier_label, _unmentioned_identifiers
    from applire.services.oracle.matchers.figures import _identifier_spans
    from applire.services.oracle.matchers.vault import build_vault_index

    try:
        index = build_vault_index(profile_json)
    except Exception:  # noqa: BLE001 — a detector failure loses its finding only
        logger.exception("identifier check: vault index failed; finding skipped")
        return [], []
    named: list[str] = []
    for text in texts:
        if not isinstance(text, str) or not text.strip():
            continue
        for label in _unmentioned_identifiers(text, index):
            if label not in named:
                named.append(label)
    if not named:
        return [], []
    stated: list[str] = []
    for unit in index.units:
        t = unit.text or ""
        for a, b in _identifier_spans(t):
            label = _identifier_label(t, a, b)
            if label not in stated:
                stated.append(label)
    return named, stated


def identifier_finding_text(named: Sequence[str], stated: Sequence[str]) -> str:
    """The remedy for :func:`unmentioned_identifiers` — the same words for CV and letter."""
    candidates = (
        f" The profile states: {_fmt(stated)}." if stated else " The profile states no identifier."
    )
    return (
        f"Names {_fmt(named)} — a standard or regulation identifier the candidate's profile "
        "never states (computed by code)." + candidates + " Write each identifier exactly as "
        "the profile states it, or leave it out; never shorten, re-number or invent one."
    )


def _cv_texts(composed: "TailoredCVData") -> list[str]:
    """Every model-written text of a composed CV (summary, skills, bullets, projects)."""
    texts: list[str] = [composed.summary or ""]
    texts.extend(x for x in composed.skills if isinstance(x, str))
    for w in composed.work_history:
        texts.extend(w.bullets)
        for p in w.projects:
            texts.append(p.name)
            texts.extend(p.bullets)
    for p in composed.projects:
        texts.append(p.name)
        texts.extend(p.bullets)
    return texts


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
        over_companies: list[str] = []
        for w in composed.work_history:
            rb = ctx.budget.roles.get(w.id)
            if rb is None:
                continue
            n = len(w.bullets) + sum(len(p.bullets) for p in w.projects)
            if n > rb.max_bullets:
                over.append(f"{w.id} ({w.company}): {n} bullets, budget {rb.max_bullets}")
                over_companies.append(w.company)
        if over:
            findings.append(CheckFinding(
                check="cv.role_budget",
                subjects=tuple(over_companies),
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
            subjects=tuple(got for _loc, got, _s, _n in notes.names_not_recorded[:_MAX_ITEMS_PER_FINDING]),
            text=(
                "; ".join(parts)
                + ". Pick the recorded form that is in the document language when one "
                "exists; never translate or invent a name."
            ),
        ))
    if notes.levels_code_added:
        parts = [
            f"{loc}: “{got}” does not carry the profile's level codes as stated (“{stored}”; differs on {', '.join(codes)})"
            for loc, got, stored, codes in notes.levels_code_added[:_MAX_ITEMS_PER_FINDING]
        ]
        findings.append(CheckFinding(
            check="cv.level_code_changed",
            text=(
                "; ".join(parts)
                + ". Translate the level's words only: keep every code and qualifier the "
                "profile states, add none."
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

    # 3b. 786:L136: a standard/regulation identifier the vault never states (a typo
    #     of the vault's own "ISO 45001", or an invented one) — blocking: writing the
    #     identifier as the profile states it is always right.
    named, stated = unmentioned_identifiers(_cv_texts(composed), ctx.profile_json)
    if named:
        findings.append(CheckFinding(
            check="cv.identifier_not_in_profile",
            subjects=tuple(named),
            text=identifier_finding_text(named, stated),
        ))

    # 4b. C5 (ruling W1-1 r1, #234): a claimable concept the vault evidences is absent
    #     from the CV. Detection and the vault-sentence selection are v1's own
    #     (``ledger_restore.select_restore_candidates`` — ADR-066, one definition); the
    #     corrector writes the bullet. No verbatim append; on exhaustion the CV ships
    #     and the ATS ``narrative-evidence`` check reports the absence (ADR-039).
    claim = _claim_missing_findings(composed, ctx)
    findings.extend(claim)
    # 4c. C2 (ruling W1-1 r2): a vault project neither nested nor mentioned.
    findings.extend(_project_findings(composed, ctx))

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
            subjects=tuple(empty),
            text=f"Projects without a single bullet: {_fmt(empty)}. Give each one bullet or leave it out.",
        ))

    blocking = [f for f in findings if f.blocking]
    advisory = [f for f in findings if not f.blocking]
    return (blocking + advisory)[:MAX_FINDINGS_PER_ROUND]


def _claim_missing_findings(composed: "TailoredCVData", ctx: V2CheckContext) -> list[CheckFinding]:
    """C5 as a finding: what ``_restore_ledger_bullets`` would append verbatim becomes
    one blocking finding naming the concept, the vault sentence and the role id."""
    if not ctx.keyword_ledger:
        return []
    from applire.services.ledger_restore import select_restore_candidates

    try:
        candidates = select_restore_candidates(
            composed.model_dump(mode="json"), ctx.profile_json, ctx.keyword_ledger, ctx.budget,
        )
    except Exception:  # noqa: BLE001 — a detector failure loses its finding only
        logger.exception("v2 check detector select_restore_candidates raised; finding skipped")
        return []
    companies = {w.id: w.company for w in composed.work_history}
    parts = [
        f"claimable concept “{c.concept}” is absent from the CV — the vault says: "
        f"“{c.vault_text}”, under {eid} ({companies.get(eid, '')})"
        for eid, cands in candidates.items()
        for c in cands
    ]
    if not parts:
        return []
    return [CheckFinding(
        check="cv.claim_missing",
        subjects=tuple(dict.fromkeys(c.concept for cands in candidates.values() for c in cands)),
        text=(
            "; ".join(parts[:_MAX_ITEMS_PER_FINDING])
            + (f" (+{len(parts) - _MAX_ITEMS_PER_FINDING} more)" if len(parts) > _MAX_ITEMS_PER_FINDING else "")
            + ". Write each into that role as one bullet in the document language, naming "
            "the concept as the posting does; if the role is at its bullet budget, replace "
            "its weakest bullet instead of adding."
        ),
    )]


def _project_text(p: dict) -> str:
    parts: list[str] = [str(p.get("name") or ""), str(p.get("description") or "")]
    for key in ("responsibilities", "achievements", "technologies", "highlights", "bullets"):
        parts.extend(str(x) for x in (p.get(key) or []) if isinstance(x, str))
    return "\n".join(x for x in parts if x.strip())


def _project_findings(composed: "TailoredCVData", ctx: V2CheckContext) -> list[CheckFinding]:
    """C2 as findings (inventory C2, ruling W1-1 r2) — v1's ``_nest_projects`` appended
    an untailored vault project's own text; v2 hands projects to the writer and checks.

    * ``cv.project_missing`` (BLOCKING): a JD-required claimable concept the CV states
      in no bullet (``cv_gap_hints.verified_narrative_underclaim`` — the instrument
      behind the ATS ``narrative-evidence`` row, ADR-066) is evidenced by a vault
      project the CV does not carry. Complying is always right: the claim is the
      posting's own requirement and the project is the candidate's own evidence.
    * ``cv.project_not_mentioned`` (advisory): a vault project whose name is neither a
      project heading nor mentioned anywhere — only when the document is in the vault's
      language (a translated heading cannot be matched by a closed normalisation, and a
      false "missing" would ask for a duplicate)."""
    from applire.services import cv as cv_mod
    from applire.services.ats_audit import _norm, surface_present
    from applire.services.cv_gap_hints import verified_narrative_underclaim

    vault_projects = [p for p in (ctx.profile_json.get("projects") or []) if isinstance(p, dict)]
    if not vault_projects:
        return []
    doc = composed.model_dump(mode="json")
    work_ids = {str(w.get("id") or "") for w in ctx.profile_json.get("work_experience") or []}

    def _where(p: dict) -> str:
        parent = str(p.get("associated_experience") or "")
        return f"under {parent}" if parent in work_ids else "as a standalone project"

    out: list[CheckFinding] = []
    parts: list[str] = []
    missing_concepts: list[str] = []
    if ctx.keyword_ledger:
        try:
            under = verified_narrative_underclaim(doc, ctx.keyword_ledger, structured_document=doc)
        except Exception:  # noqa: BLE001 — a detector failure loses its finding only
            logger.exception("v2 check detector verified_narrative_underclaim raised; finding skipped")
            under = []
        texts = [(p, _norm(_project_text(p))) for p in vault_projects]
        for c in under:
            hit = next((p for p, t in texts if t and any(surface_present(f, t) for f in c.surface_forms)), None)
            if hit is None:
                continue
            desc = str(hit.get("description") or "").strip()
            missing_concepts.append(c.concept)
            parts.append(
                f"“{c.concept}” is required by the posting and no bullet states it — the vault "
                f"project “{hit.get('name')}” ({hit.get('id')}, {_where(hit)}) is its evidence"
                + (f": “{desc[:240]}”" if desc else "")
            )
    if parts:
        out.append(CheckFinding(
            check="cv.project_missing",
            subjects=tuple(missing_concepts[:_MAX_ITEMS_PER_FINDING]),
            text=(
                "; ".join(parts[:_MAX_ITEMS_PER_FINDING])
                + ". Write that project into the CV (as a project of its role, one or two "
                "bullets in the document language) or state the concept in a bullet of that "
                "role; if the role is at its bullet budget, replace its weakest bullet."
            ),
        ))

    try:
        same_language = cv_mod._vault_dominant_language(ctx.profile_json) == ctx.language
    except Exception:  # noqa: BLE001
        same_language = False
    if same_language:
        headings = {norm_name(p.name) for w in composed.work_history for p in w.projects}
        headings |= {norm_name(p.name) for p in composed.projects}
        doc_norm = _norm(canonical(doc))
        unmentioned = [
            str(p.get("name"))
            for p in vault_projects
            if p.get("name") and norm_name(p.get("name")) not in headings
            and not surface_present(str(p.get("name")), doc_norm)
        ]
        if unmentioned:
            out.append(CheckFinding(
                check="cv.project_not_mentioned",
                subjects=tuple(unmentioned),
                text=(
                    f"Vault projects neither on the CV nor mentioned: {_fmt(unmentioned)}. "
                    "Include the ones that are evidence for this job; leaving out an "
                    "unrelated one is fine."
                ),
                blocking=False,
            ))
    return out


def language_shape_checks(handed: dict):
    """Inventory B under v2 (ruling W1-1 r2): the ``cv_language`` refiner must keep
    the shape of the draft it was HANDED (#759: it once nested three work entries in
    the first and dropped the skills). A shape change is a blocking finding — it opens
    a refiner round; on exhaustion the existing settle guard
    (``cv._settle_language_shape``) ships each changed container as the handed MODEL
    draft had it — a selection between two model outputs, no code-authored text. The
    comparison is v1's own (``cv._language_shape_fallbacks``, ADR-066)."""
    from applire.services import cv as cv_mod

    def _checks(draft: dict) -> list[CheckFinding]:
        _, changed = cv_mod._language_shape_fallbacks(draft, handed)
        if not changed:
            return []
        return [CheckFinding(
            check="cv.language_shape",
            text=(
                "The translation changed the document's structure: "
                + ", ".join(changed)
                + ". Translate in place: return every work entry by id at the top level, "
                "with the same number of bullets, the same projects with the same bullet "
                "counts, the same number of skills, and the summary — never add, drop, "
                "merge, nest or reorder an entry."
            ),
        )]

    return _checks


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
            subjects=tuple(untied),
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
            subjects=tuple(b for b, _a in respelled[:_MAX_ITEMS_PER_FINDING]),
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
            subjects=tuple(over_cap),
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
            subjects=tuple(dupes),
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
            subjects=tuple(narrated),
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

    def delivered_findings(self, prose: dict, tailored_data: dict) -> list[CheckFinding]:
        """The checks evaluated ONCE on the SHIPPED draft (adversarial finding 6,
        2026-10-17): the last terminal corrector round is unreviewed and unchecked, so
        what the loop settled on is not what ships. No rewrite, no round — a report."""
        from applire.schemas.cv import TailoredCVData

        try:
            composed = TailoredCVData.model_validate(tailored_data)
            notes = self.notes_by_draft.get(canonical(prose)) or ComposeNotes()
            return [f for f in v2_checks(prose, composed, notes, self.ctx) if f.blocking]
        except Exception:  # noqa: BLE001 — a report failure must never fail delivery
            logger.exception("v2 delivered-draft checks raised; nothing reported")
            return []

    def report_delivered(self, outcome, prose: dict, tailored_data: dict):
        """Attach the delivered draft's open blocking findings to the terminal-review
        outcome as ``notes`` — they reach the ATS ``terminal-review`` row's details on
        every status (the #664 precedent the letter uses)."""
        import dataclasses

        open_ = self.delivered_findings(prose, tailored_data)
        logger.info("CV_V2_DELIVERED_CHECKS open=%s", [f.check for f in open_])
        if not open_:
            return outcome
        # R2-5 (build 4, WP-T hunk): one structured note per open check, so the
        # review card says each in the UI language; ``details`` folds them into the
        # same EN sentence as before (``terminal_review_outcome._note_texts``).
        from applire.services.terminal_review_outcome import TerminalNote, TerminalReviewOutcome

        new_notes = tuple(
            TerminalNote(kind="code", key=f.check, text=f.text, subjects=tuple(f.subjects))
            for f in open_
        )
        if outcome is None:
            # No terminal verdict ran (review layer off, or a stubbed loop): the row stays
            # "unknown" (path None → not_applicable) and still names the open facts.
            return TerminalReviewOutcome(
                chain_id="cv_terminal_review", path=None, approved=False,
                blocking_issues=(), minor_issues=(), rounds=0, notes=new_notes,
            )
        fresh = tuple(n for n in new_notes if n not in outcome.notes)
        if not fresh:
            return outcome
        return dataclasses.replace(outcome, notes=outcome.notes + fresh)

    def on_checks_settle(self, report) -> None:
        self.open_findings = list(report.open_findings)
        logger.info(
            "CV_V2_CHECKS_SETTLED path=%s open=%s rounds_with_findings=%d",
            report.path, [f.check for f in report.open_findings], report.rounds_with_findings,
        )
