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
from applire.services.oracle.matchers.figures import Figure, extract_figures


@dataclass
class EvidenceUnit:
    path: str
    text: str
    text_norm: str
    figures: list[Figure] = field(default_factory=list)
    receipt_ids: list[str] = field(default_factory=list)


@dataclass
class VaultIndex:
    units: list[EvidenceUnit]
    all_text_norm: str
    skill_names: list[str]
    # (kind, canonical value) -> units carrying that figure
    figure_map: dict[tuple[str, str], list[EvidenceUnit]]


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

    def _add(path: str, text: Any) -> None:
        if not isinstance(text, str):
            return
        stripped = text.strip()
        if not stripped:
            return
        units.append(
            EvidenceUnit(
                path=path,
                text=stripped,
                text_norm=_norm(stripped),
                figures=extract_figures(stripped),
            )
        )

    summary = p.professional_summary
    _add("professional_summary.de", getattr(summary, "de", None))
    _add("professional_summary.en", getattr(summary, "en", None))

    def _add_experience(prefix: str, entry: Any) -> None:
        _add(f"{prefix}.role", getattr(entry, "role", None))
        org = entry.org_label()
        if org:
            _add(f"{prefix}.org", org)
        for j, r in enumerate(getattr(entry, "responsibilities", []) or []):
            _add(f"{prefix}.responsibilities[{j}]", r)
        for j, a in enumerate(getattr(entry, "achievements", []) or []):
            _add(f"{prefix}.achievements[{j}]", a)
        for j, t in enumerate(getattr(entry, "technologies", []) or []):
            _add(f"{prefix}.technologies[{j}]", t)
        span = " – ".join(
            s for s in (getattr(entry, "start_date", None), getattr(entry, "end_date", None)) if s
        )
        if span:
            _add(f"{prefix}.dates", span)

    for i, w in enumerate(p.work_experience):
        _add_experience(f"work_experience[{i}]", w)
        _add(f"work_experience[{i}].budget_managed", w.budget_managed)
    for i, pr in enumerate(p.projects):
        _add_experience(f"projects[{i}]", pr)
        _add(f"projects[{i}].description", pr.description)
    for i, v in enumerate(p.volunteer_activities):
        _add_experience(f"volunteer_activities[{i}]", v)

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

    return VaultIndex(
        units=units,
        all_text_norm=_norm(" ".join(u.text for u in units)),
        skill_names=skill_names,
        figure_map=figure_map,
    )
