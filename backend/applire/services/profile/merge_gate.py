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


def names_clearly_differ(account_name: str | None, cv_name: str | None) -> bool:
    """True only when both names are present and share NO normalised token — a
    likely third-person CV (FMEA JF-M-2.4). Conservative: a shared surname,
    reordering, accents/transliteration, nicknames or a maiden-name change all
    suppress it, so a false 'different person' never re-adds friction (ADR-037).
    Canonical home for the check US154/155 first surfaced as a warning.
    """
    a, b = _name_tokens(account_name), _name_tokens(cv_name)
    if not a or not b:
        return False
    return a.isdisjoint(b)


def looks_like_cv(data: MasterProfileData) -> bool:
    """Heuristic (FMEA JF-M-2.3): a real CV yields work history, education, or a
    name together with skills. A JD / cover letter / slide deck extracts to
    ~nothing of these."""
    return bool(
        data.work_experience
        or data.education
        or (data.personal_info.name.strip() and data.skills)
    )


def evaluate_merge_gate(
    account_name: str | None, extracted: MasterProfileData
) -> GateResult:
    """Return the pre-merge gate verdict for an incoming CV extraction."""
    cv_name = extracted.personal_info.name or None

    # not-a-CV takes precedence: a near-empty doc has no reliable name to compare.
    if not looks_like_cv(extracted):
        return GateResult(gate="not_a_cv", account_name=account_name, cv_name=cv_name)

    if names_clearly_differ(account_name, cv_name):
        return GateResult(
            gate="name_divergence", account_name=account_name, cv_name=cv_name
        )

    return GateResult(gate="none", account_name=account_name, cv_name=cv_name)
