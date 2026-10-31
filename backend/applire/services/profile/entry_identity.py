# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""U-9 (Hazelnut build 5, ADR-046 am. FS-6 extended) — candidate pairs for
"the same position under two names".

A candidate pair is a FACT (ADR-062 cl. 1): two entries of the same section
with an identical, STATED start month and an identical end month (or both
current). Whether they are the same position is a judgement — the model's at
import (``decisions.judge_identity``, verdicts persisted on
``metadata.entry_identity_verdicts``) and finally the candidate's (the hub's
duplicate-entry question). This module only enumerates and reads.
"""
from __future__ import annotations

from typing import Iterable

from applire.schemas.profile import MasterProfileData

#: Sections whose entries are positions (dates + an employer / organisation).
IDENTITY_SECTIONS: tuple[str, ...] = ("work_experience", "volunteer_activities")


def _month(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()[:7]


def _span_key(entry) -> tuple[str, str] | None:
    start = _month(getattr(entry, "start_date", None))
    if start is None:
        return None
    end = _month(getattr(entry, "end_date", None))
    if end is None:
        if not getattr(entry, "is_current", False):
            return None  # an undated end is not a stated fact
        end = "current"
    return start, end


def pair_key(section: str, a: str, b: str) -> tuple[str, str, str]:
    x, y = sorted((a, b))
    return section, x, y


def candidate_pairs(profile: MasterProfileData) -> list[tuple[str, str, str]]:
    """Every (section, id_a, id_b) with identical stated spans, ids sorted."""
    out: list[tuple[str, str, str]] = []
    for section in IDENTITY_SECTIONS:
        entries = [e for e in (getattr(profile, section, None) or []) if getattr(e, "id", None)]
        for i, a in enumerate(entries):
            ka = _span_key(a)
            if ka is None:
                continue
            for b in entries[i + 1:]:
                if _span_key(b) == ka:
                    out.append(pair_key(section, a.id, b.id))
    return out


def _keys(pairs: Iterable) -> set[tuple[str, str, str]]:
    return {pair_key(p.section, p.ids[0], p.ids[1]) for p in pairs if len(p.ids) == 2}


def open_pairs(profile: MasterProfileData) -> list[tuple[str, str, str]]:
    """Candidate pairs still to ASK: not kept separate by the candidate, not
    judged ``different`` by the model. An unjudged pair is asked."""
    meta = profile.metadata
    separate = _keys(meta.separate_entry_pairs) if meta else set()
    different = (
        _keys(v for v in meta.entry_identity_verdicts if v.verdict == "different") if meta else set()
    )
    return [k for k in candidate_pairs(profile) if k not in separate and k not in different]


def unjudged_pairs(profile: MasterProfileData) -> list[tuple[str, str, str]]:
    """Candidate pairs with no model verdict yet (what an import judges)."""
    meta = profile.metadata
    judged = _keys(meta.entry_identity_verdicts) if meta else set()
    separate = _keys(meta.separate_entry_pairs) if meta else set()
    return [k for k in candidate_pairs(profile) if k not in judged and k not in separate]
