# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#764 (ADR-061 cl. 7 amended 2026-10-16, FMEA SF-VAULT.16) — skill spans
labelled ``transcribed`` that no source states.

Before the 2026-10-07 fix (#747) the reconcile prompt's incoming view carried
skill enrichment's COMPUTED ``years_experience`` without its label; the
reconciler copied the number into ``upsert_skill`` and the applier stamped it
``transcribed`` (replay of the captured call: 5/5 laundered). The fix stops new
laundering; rows written before it keep the false label, and three consumers
trust that label — #684's precedence (a transcribed span is never recomputed),
the letter's hedge view (a transcribed span is stated flat) and the Oracle's
#747 escape. This module finds them and offers the one honest repair.

**The detector** (:func:`laundered_spans`, pure: no LLM, no DB — the Health
contract, ADR-041) flags a non-language skill whose span N is ``transcribed``
when BOTH hold:

(i) **no source text states N years** — no vault evidence unit, denial
    statement, or string in ``metadata.enrichment_history`` (old/new values,
    never the system-written rationale) carries a duration the Oracle's
    ``extract_tenure_claims`` reads within half a year of N; and no
    ``manual_edit`` record set this skill's span to N (the candidate typing it
    IS a statement);
(ii) **N is the re-derivation's fingerprint** — phase 1's own stored arithmetic
    (``skill_enrichment.stored_span_years``) gives c with c − 1 − d ≤ N ≤ c,
    d = whole years since 2026-09-08 (#684: the first day a reconcile op could
    stamp a span ``transcribed``). A laundered value only LAGS the growing
    derivation; it never exceeds it.

(ii) is what keeps the recompute safe (founder ruling H-2, option A): on the
captured corpus 17 of 36 unstated transcribed spans did NOT match the
derivation — the candidate's own CV figure ("Python 6", roles naming Python
span 11), whose source text the 7-day upload retention (ADR-005) has deleted.
Recomputing those would INFLATE the candidate's own number, the direction
ADR-061 cl. 5 forbids, so they are never flagged.

**The repair** (:func:`recompute_span`) is the ordinary manual skills edit —
``patch_profile_section`` → ``ReplaceSection`` → ``commit_ops`` (ADR-063: no new
op, no new write path) — with that one skill's span and label cleared; the
committer's deterministic enrichment (invariant 6) re-derives it as
``computed``. ADR-062: FACTS throughout.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterator

from applire.schemas.profile import MasterProfileData

#: #684 (ADR-061 amended 2026-09-08) — the first day the reconcile applier
#: could stamp a span ``transcribed``; laundering is impossible before it.
LAUNDERING_POSSIBLE_FROM = date(2026, 9, 8)
#: A stated duration counts as stating N when it reads within this of N.
STATED_TOLERANCE_YEARS = 0.5


@dataclass(frozen=True)
class LaunderedSpan:
    skill_id: str
    skill_name: str
    span_years: int
    recomputed_years: int


class SpanNotLaunderedError(Exception):
    """The addressed skill is not (or no longer) a laundered span — a stale
    click, or a skill id the vault does not hold. The door answers 409."""


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _source_texts(profile: MasterProfileData) -> list[str]:
    """Every text the candidate's own data holds: vault evidence units, denial
    statements, and the strings of the enrichment trail's old/new values."""
    from applire.services.oracle.matchers import build_vault_index

    index = build_vault_index(profile)
    texts = [u.text for u in index.units if u.text]
    texts += [u.text for u in index.denial_units if u.text]
    metadata = profile.metadata
    for record in (metadata.enrichment_history if metadata else []) or []:
        for change in record.changes or []:
            texts.extend(_strings(change.old_value))
            texts.extend(_strings(change.new_value))
    return texts


def _manually_set(profile: MasterProfileData, name: str, years: int) -> bool:
    """A ``manual_edit`` record set this skill's span to ``years``."""
    metadata = profile.metadata
    key = name.casefold()
    for record in (metadata.enrichment_history if metadata else []) or []:
        if record.source != "manual_edit":
            continue
        for change in record.changes or []:
            if change.section != "skills" or not isinstance(change.new_value, dict):
                continue
            new = change.new_value
            if str(new.get("name") or "").casefold() != key:
                continue
            old = change.old_value if isinstance(change.old_value, dict) else {}
            if new.get("years_experience") == years and old.get("years_experience") != years:
                return True
    return False


def _drift_years(today: date) -> int:
    return max(0, int((today - LAUNDERING_POSSIBLE_FROM).days // 365.25))


def laundered_spans(
    profile: MasterProfileData, today: date | None = None
) -> list[LaunderedSpan]:
    """Transcribed skill spans no source states and that equal the re-derivation."""
    from applire.services.oracle.matchers.figures import extract_tenure_claims
    from applire.services.skill_enrichment import stored_span_years

    today = today or date.today()
    candidates = [
        s for s in profile.skills
        if s.source == "transcribed"
        and s.years_experience is not None
        and s.category != "language"
        and s.status != "denied"
        and s.name
    ]
    if not candidates:
        return []
    stated = [
        t.years for text in _source_texts(profile) for t in extract_tenure_claims(text)
    ]
    drift = _drift_years(today)
    out: list[LaunderedSpan] = []
    for skill in candidates:
        n = skill.years_experience
        if any(abs(y - n) <= STATED_TOLERANCE_YEARS for y in stated):
            continue
        if _manually_set(profile, skill.name, n):
            continue
        c = stored_span_years(profile, skill.name, today)
        if c is None or not (c - 1 - drift <= n <= c):
            continue
        out.append(LaunderedSpan(skill.id, skill.name, n, c))
    return out


async def recompute_span(
    db: Any, skill_id: str, *, user_id: uuid.UUID | None = None
):
    """Re-derive one laundered span through the ordinary manual skills edit.

    Re-checks the detector on the CURRENT vault first, so a stale click (the
    span was already recomputed, edited, or the skill removed) raises
    :class:`SpanNotLaunderedError` instead of writing blind. Returns the
    ``MasterProfileResponse`` the section edit returns; the edit commits.
    """
    from applire.services.profile import get_profile_for_user, patch_profile_section

    record = await get_profile_for_user(db, user_id)
    if record is None:
        raise LookupError("No profile found")
    profile = MasterProfileData.model_validate(record.profile_json)
    if skill_id not in {s.skill_id for s in laundered_spans(profile)}:
        raise SpanNotLaunderedError(skill_id)
    skills: list[dict[str, Any]] = []
    for skill in profile.skills:
        entry = skill.model_dump(mode="json")
        if skill.id == skill_id:
            # Cleared together: a bare span with no source reads as the
            # extractor's own reading (transcribed), so the label must go too
            # for phase 1 to re-derive it (``_match_and_enrich``).
            entry["years_experience"] = None
            entry["source"] = None
        skills.append(entry)
    return await patch_profile_section(
        "skills",
        skills,
        db,
        source="manual_edit",
        basis_updated_at=record.updated_at,
        user_id=user_id,
    )
