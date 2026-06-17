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

"""
US167 (E033 / ADR-041 amended) — pre-merge integrity gate.

Two deterministic (no-LLM) checks run BEFORE the additive merge commits:
  (a) not-a-CV / near-empty extraction  → gate "not_a_cv"
  (b) account-vs-CV name divergence     → gate "name_divergence"

The system detects *difference* only — identity is the user's call. Name
divergence fires only when the normalised name tokens are DISJOINT, so
nicknames / reordering / middle names / maiden name / transliteration are
tolerated; a false "different person" would re-add the friction ADR-037 removed.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from applire.schemas.profile import MasterProfileData


@dataclass
class GateResult:
    gate: str  # "none" | "not_a_cv" | "name_divergence"
    account_name: str | None = None
    cv_name: str | None = None


def _name_tokens(name: str | None) -> set[str]:
    """Lowercased, diacritic-stripped, punctuation-free name tokens."""
    if not name:
        return set()
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in ascii_name)
    return {t for t in cleaned.casefold().split() if t}


def _is_near_empty(extracted: MasterProfileData) -> bool:
    """A real CV has at least one professional section. None → not a CV."""
    return not (
        extracted.work_experience
        or extracted.education
        or extracted.skills
        or extracted.certifications
    )


def evaluate_merge_gate(
    account_name: str | None, extracted: MasterProfileData
) -> GateResult:
    """Return the pre-merge gate verdict for an incoming CV extraction."""
    cv_name = extracted.personal_info.name or None

    # not-a-CV takes precedence: a near-empty doc has no reliable name to compare.
    if _is_near_empty(extracted):
        return GateResult(gate="not_a_cv", account_name=account_name, cv_name=cv_name)

    account_tokens = _name_tokens(account_name)
    cv_tokens = _name_tokens(cv_name)
    if account_tokens and cv_tokens and account_tokens.isdisjoint(cv_tokens):
        return GateResult(
            gate="name_divergence", account_name=account_name, cv_name=cv_name
        )

    return GateResult(gate="none", account_name=account_name, cv_name=cv_name)
