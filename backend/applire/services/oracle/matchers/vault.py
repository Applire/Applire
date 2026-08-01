# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US244 — the vault evidence index.

Flattens the master profile into addressable evidence units (path + normalized
text + figures) and attaches ADR-046 provenance receipt ids from
``metadata.enrichment_history`` so every verdict can point at *how* its
evidence entered the vault.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from applire.schemas.profile import MasterProfileData
from applire.services.ats_audit import _norm
from applire.services.oracle.extract import _core_company_name
from applire.services.oracle.matchers.figures import (
    Figure,
    extract_figures,
    extract_spelled_figures,
)
from applire.utils.language_detection import detect_language


@dataclass
class EvidenceUnit:
    path: str
    text: str
    text_norm: str
    figures: list[Figure] = field(default_factory=list)
    receipt_ids: list[str] = field(default_factory=list)
    # Ids of the experience entries this unit belongs to — anchors the v2
    # role-attribution matcher (#196). A work/volunteer unit owns one id; an
    # ASSOCIATED project's units also carry the resolved parent work id,
    # because US187 nests its bullets under that position in the rendered CV.
    # Empty for role-agnostic evidence (summary, skills, education, stories,
    # …), which may ground a claim under any position.
    owner_ids: frozenset[str] = frozenset()


@dataclass
class VaultIndex:
    units: list[EvidenceUnit]
    all_text_norm: str
    skill_names: list[str]
    # (kind, canonical value) -> units carrying that figure
    figure_map: dict[tuple[str, str], list[EvidenceUnit]]
    # Every experience id the vault knows — a claim's stamped source id must be
    # a member or the attribution matcher stays silent (fail open on ids from
    # backfill heuristics / stale data, #196 adversarial review).
    experience_ids: frozenset[str] = frozenset()
    # #237 round-3 (live MCP probe residual): work_experience id -> every id
    # (INCLUDING itself) sharing that SAME company (legal-form-suffix
    # tolerant grouping, mirrors extract.py's loose anchor matching) — a long
    # tenure held across several internal roles. The attribution matcher
    # (:func:`matchers.attribution.find_foreign_owner`) treats a claim's
    # anchor and any of its company siblings as equally "not foreign": a
    # sentence anchored to the CURRENT role (the extract.py current-role
    # tie-break) whose evidence actually lives on a PAST role at the SAME
    # company is an ordinary tenure-spanning claim, not a cross-employer
    # blend. Empty for an id with no siblings (the common single-role case).
    same_employer_ids: dict[str, frozenset[str]] = field(default_factory=dict)
    # ADR-068 clause 2a — the vault's OWN dominant language, computed ONCE per
    # audit over the CONCATENATED text of every evidence unit (never
    # per-unit: a short label like a skill name or a year span defaults to
    # 'de' on its own and would swing a per-unit vote on noise alone — see
    # ``applire.utils.language_detection.detect_language``'s own DE-default
    # tie-break). This is the comparison side of the cross-language
    # judgement seam's trigger: a document written in a DIFFERENT language
    # than this one.
    dominant_language: str = "de"


def _coerce_profile(profile: MasterProfileData | dict[str, Any]) -> MasterProfileData:
    if isinstance(profile, MasterProfileData):
        return profile
    return MasterProfileData.model_validate(profile or {})


def _receipt_blobs(profile: MasterProfileData) -> list[tuple[str, str]]:
    """(enrichment record id, normalized blob of that record's new values)."""
    blobs: list[tuple[str, str]] = []
    if not profile.metadata:
        return blobs
    for rec in profile.metadata.enrichment_history:
        parts: list[str] = []
        for change in rec.changes:
            if change.new_value is None:
                continue
            if isinstance(change.new_value, str):
                parts.append(change.new_value)
            else:
                try:
                    parts.append(json.dumps(change.new_value, ensure_ascii=False, default=str))
                except (TypeError, ValueError):
                    continue
        blob = _norm(" ".join(parts))
        if blob:
            blobs.append((rec.id, blob))
    return blobs


# Receipts are only attached to units long enough to be distinctive — a bare
# year or a two-letter fragment would "match" nearly every record blob.
_RECEIPT_MIN_CHARS = 8


def build_vault_index(profile: MasterProfileData | dict[str, Any]) -> VaultIndex:
    p = _coerce_profile(profile)
    units: list[EvidenceUnit] = []

    def _add(path: str, text: Any, owners: frozenset[str] = frozenset()) -> None:
        if not isinstance(text, str):
            return
        stripped = text.strip()
        if not stripped:
            return
        # #237 (run-4 residual): the vault's OWN prose may spell a small
        # count out ("a team of five tech leads") where generated document
        # prose (or a quantifier phrasing like "5+") uses a digit —
        # ``extract_spelled_figures`` bridges that ONLY on this, the vault-
        # indexing side (see its docstring), so the figure becomes citable
        # evidence instead of a fabricated-looking claim silently getting a
        # pass because the vault "doesn't have a 5" in digit form.
        figures = extract_figures(stripped) + extract_spelled_figures(stripped)
        units.append(
            EvidenceUnit(
                path=path,
                text=stripped,
                text_norm=_norm(stripped),
                figures=figures,
                owner_ids=owners,
            )
        )

    summary = p.professional_summary
    _add("professional_summary.de", getattr(summary, "de", None))
    _add("professional_summary.en", getattr(summary, "en", None))

    # #237 (run-4 residual): location/nationality claims ("Based in Germany",
    # "EU work authorization") were structurally unverifiable — not because
    # the vault lacks the fact, but because ``build_vault_index`` never
    # indexed ``personal_info`` at all. Role-agnostic (no owner_ids), like
    # summary/skills — a candidate's location isn't scoped to one position.
    personal_info = p.personal_info
    if personal_info is not None:
        _add("personal_info.location", getattr(personal_info, "location", None))
        _add("personal_info.address", getattr(personal_info, "address", None))
        _add("personal_info.nationality", getattr(personal_info, "nationality", None))

    def _safe_id(value: Any) -> str | None:
        return value.strip() if isinstance(value, str) and value.strip() else None

    # `associated_experience` is a WorkEntry id on the reconcile path but a
    # company NAME on the CV-extraction path — resolve it exactly like the
    # US187 nesting step (services/cv._nest_projects) so an associated
    # project's evidence clears the position its bullets are rendered under.
    work_id_by_company: dict[str, str] = {}
    work_ids: set[str] = set()
    for w in p.work_experience:
        wid = _safe_id(w.id)
        if wid is None:
            continue
        work_ids.add(wid)
        company = (w.company or "").strip().lower()
        if company:
            work_id_by_company.setdefault(company, wid)

    def _project_owners(pr: Any) -> frozenset[str]:
        owners = {o for o in (_safe_id(pr.id),) if o}
        ref = _safe_id(getattr(pr, "associated_experience", None))
        if ref is not None:
            parent = ref if ref in work_ids else work_id_by_company.get(ref.lower())
            if parent is not None:
                owners.add(parent)
        return frozenset(owners)

    def _add_experience(prefix: str, entry: Any, owners: frozenset[str]) -> None:
        _add(f"{prefix}.role", getattr(entry, "role", None), owners)
        org = entry.org_label()
        if org:
            _add(f"{prefix}.org", org, owners)
        for j, r in enumerate(getattr(entry, "responsibilities", []) or []):
            _add(f"{prefix}.responsibilities[{j}]", r, owners)
        for j, a in enumerate(getattr(entry, "achievements", []) or []):
            _add(f"{prefix}.achievements[{j}]", a, owners)
        for j, t in enumerate(getattr(entry, "technologies", []) or []):
            _add(f"{prefix}.technologies[{j}]", t, owners)
        span = " – ".join(
            s for s in (getattr(entry, "start_date", None), getattr(entry, "end_date", None)) if s
        )
        if span:
            _add(f"{prefix}.dates", span, owners)

    experience_ids: set[str] = set()

    def _owners_of(entry: Any) -> frozenset[str]:
        owners = frozenset(o for o in (_safe_id(entry.id),) if o)
        experience_ids.update(owners)
        return owners

    for i, w in enumerate(p.work_experience):
        owners = _owners_of(w)
        _add_experience(f"work_experience[{i}]", w, owners)
        _add(f"work_experience[{i}].budget_managed", w.budget_managed, owners)
    project_by_id: dict[str, Any] = {}
    for i, pr in enumerate(p.projects):
        owners = _project_owners(pr)
        experience_ids.update(owners)
        _add_experience(f"projects[{i}]", pr, owners)
        _add(f"projects[{i}].description", pr.description, owners)
        pid = _safe_id(pr.id)
        if pid is not None:
            project_by_id[pid] = pr
    for i, v in enumerate(p.volunteer_activities):
        _add_experience(f"volunteer_activities[{i}]", v, _owners_of(v))

    skill_names: list[str] = []
    for i, s in enumerate(p.skills):
        _add(f"skills[{i}]", s.name)
        skill_names.append(s.name)

    for i, e in enumerate(p.education):
        _add(f"education[{i}]", " ".join(x for x in (e.degree, e.field, e.institution) if x))
        span = " – ".join(x for x in (e.start_date, e.end_date) if x)
        if span:
            _add(f"education[{i}].dates", span)

    for i, c in enumerate(p.certifications):
        _add(f"certifications[{i}]", " ".join(x for x in (c.name, c.issuing_organization or "") if x))
        if c.date_obtained:
            _add(f"certifications[{i}].dates", str(c.date_obtained))

    for i, lang in enumerate(p.languages):
        _add(f"languages[{i}]", f"{lang.language} {lang.level or ''}")

    for i, pub in enumerate(p.publications):
        _add(f"publications[{i}]", getattr(pub, "title", None))

    # ADR-055 — signature stories: every prose field is an evidence unit, so a
    # document claim backed only by a story still verifies as grounded, and
    # figures stated in `outcome` become citable number provenance.
    #
    # #237 (F14): a story anchored to a specific experience via
    # ``experience_refs`` (the Skill provenance pattern, US172) now carries
    # that ownership too — an interview-derived story about a DIFFERENT role
    # is a foreign owner for a claim rendered under another position, exactly
    # like a work-entry achievement is (#196). A job-agnostic story (no
    # experience_refs) stays owner-neutral and grounds any position, same as
    # summary/skills — the presence of a real anchor is what changes, not the
    # unit kind.
    for i, story in enumerate(p.signature_stories):
        # #378/run-12 (sibling of #355, evidence on #378): a story's
        # ``experience_refs`` entry can name a PROJECT id (US172 provenance
        # pattern) rather than a work-experience id directly — the project's
        # OWN units already resolve to their parent work id via
        # ``_project_owners`` (US187 nesting), so a story ref pointing at
        # that same project must resolve the same way, or a claim anchored
        # to the parent position clears via the project's units but is
        # flagged "misattributed" via the story's units for the identical
        # underlying fact (run 12, controlling_emma_de: "Management-
        # Reporting auf Power BI umstellen", byte-identical detail text
        # reproduced by recon 2026-08-01). Reuses ``_project_owners``
        # itself rather than duplicating its resolution logic.
        #
        # A ref naming a work-experience id directly (or a volunteer
        # activity id — volunteer activities have no ``associated_experience``
        # parent to resolve to, so their own id already IS the correct owner,
        # same as before this fix) is kept as-is.
        #
        # #355: a ref matching NO known entity (dangling) is also kept
        # verbatim — removing it would flip fail-open/fail-closed behavior
        # for the attribution matcher, which is out of scope here.
        story_owners_set: set[str] = set()
        for r in story.experience_refs or []:
            if not isinstance(r, str):
                continue
            ref = r.strip()
            if not ref:
                continue
            pr = project_by_id.get(ref)
            if pr is not None:
                story_owners_set |= _project_owners(pr)
            else:
                story_owners_set.add(ref)
        story_owners = frozenset(story_owners_set)
        _add(f"signature_stories[{i}].title", story.title, story_owners)
        _add(f"signature_stories[{i}].challenge", story.challenge, story_owners)
        _add(f"signature_stories[{i}].mechanism", story.mechanism, story_owners)
        _add(f"signature_stories[{i}].outcome", story.outcome, story_owners)
        _add(f"signature_stories[{i}].benchmark", story.benchmark, story_owners)

    # ADR-046 receipts: attach enrichment record ids whose new-value blob
    # contains the unit's normalized text.
    for rec_id, blob in _receipt_blobs(p):
        for unit in units:
            if len(unit.text_norm) >= _RECEIPT_MIN_CHARS and unit.text_norm in blob:
                unit.receipt_ids.append(rec_id)

    figure_map: dict[tuple[str, str], list[EvidenceUnit]] = {}
    for unit in units:
        for fig in unit.figures:
            figure_map.setdefault((fig.kind, fig.value), []).append(unit)

    # #237 round-3: group WORK-EXPERIENCE ids by legal-form-suffix-tolerant
    # company name — every id in a group is a "same employer" sibling of
    # every other. Deliberately scoped to work_experience only (the concrete
    # bug shape); projects/volunteer entries have no comparable company
    # field to group by.
    company_groups: dict[str, set[str]] = {}
    for w in p.work_experience:
        wid = _safe_id(w.id)
        company = (w.company or "").strip()
        if wid is None or not company:
            continue
        key = _core_company_name(company).strip().lower()
        company_groups.setdefault(key, set()).add(wid)
    same_employer_ids: dict[str, frozenset[str]] = {
        wid: frozenset(group)
        for group in company_groups.values()
        for wid in group
    }

    all_text_norm = _norm(" ".join(u.text for u in units))
    return VaultIndex(
        units=units,
        all_text_norm=all_text_norm,
        skill_names=skill_names,
        figure_map=figure_map,
        experience_ids=frozenset(experience_ids),
        same_employer_ids=same_employer_ids,
        # ADR-068 clause 2a — corpus-level, once, over the SAME normalized
        # blob every grounding matcher already shares (never per-unit).
        dominant_language=detect_language(all_text_norm),
    )
