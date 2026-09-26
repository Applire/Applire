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
      — and, since ADR-041 amended 2026-09-26 (#674 line (b)), a NAMELESS
      extraction whose employers share none with a populated vault's history,
      held under the same gate value with ``cv_name=None``.

The system detects *difference* only — identity is the user's call. Name
divergence fires only when the normalised name tokens are DISJOINT, so
nicknames / reordering / middle names / maiden name / transliteration are
tolerated; a false "different person" would re-add the friction ADR-037 removed.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from applire.schemas.profile import MasterProfileData
# The ATS employer-clause detector's generic-first-word list (ruling R-4) is
# reused, not forked: the same words are unsafe as an employer anchor there and
# as identity evidence here (adversarial pass 2026-09-26, V-1 residual).
from applire.services.ats_audit import _GENERIC_FIRST_WORDS
from applire.services.profile.merge import _company_tokens, company_names_match


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


def _employers(data: MasterProfileData | None) -> list[str]:
    """Non-blank employer names of a profile's work history."""
    if data is None:
        return []
    return [w.company.strip() for w in data.work_experience if (w.company or "").strip()]


#: Labels that fill the employer field for self-employment. They name a way of
#: working, not an employer, so two strangers who both freelanced "share" one.
_SELF_EMPLOYMENT_LABELS = frozenset({
    "freelance", "freelancer", "freelancing", "self-employed", "self employed",
    "selfemployed", "selbstständig", "selbständig", "selbststandig", "selbstaendig",
    "freiberuflich", "freiberufler", "freiberuflerin", "freischaffend",
    "independent", "independent contractor", "contractor", "sole trader",
})


def _is_self_employment_label(company: str) -> bool:
    """True when the employer name is a self-employment label, optionally with
    a parenthetical or a dash-suffixed field ("Freiberuflich (IT-Beratung)",
    "Freelance – UX"). Matches the leading one or two words only."""
    head = re.split(r"[(\[|:/,–—]|\s-\s", company, maxsplit=1)[0]
    words = [w.strip(".,;") for w in head.casefold().split()]
    if not words:
        return False
    return words[0] in _SELF_EMPLOYMENT_LABELS or " ".join(words[:2]) in _SELF_EMPLOYMENT_LABELS


def _same_employer_for_identity(a: str, b: str) -> bool:
    """:func:`merge.company_names_match`, narrowed for an IDENTITY decision
    between two unverified sources (adversarial pass 2026-09-26, V-1 residual).

    ``company_names_match`` stays as it is — it serves the merge, where both
    names already belong to the same user. Here a match counts only when
    neither side is a self-employment label and the words the two names share
    are not ALL generic company words ("Deutsche" ⊆ "Deutsche Bahn AG" is not a
    shared employer). An exact match with no significant token (``"SAP"`` ==
    ``"SAP"``) still counts.
    """
    if _is_self_employment_label(a) or _is_self_employment_label(b):
        return False
    if not company_names_match(a, b):
        return False
    shared = _company_tokens(a) & _company_tokens(b)
    return not shared or not shared <= _GENERIC_FIRST_WORDS


def nameless_history_diverges(
    extracted: MasterProfileData, vault: MasterProfileData | None
) -> bool:
    """True when the extraction carries NO name and none of its employers is one
    the vault already holds (ADR-041 amended 2026-09-26, #674 line (b)).

    ``names_clearly_differ`` needs both names, so a nameless extraction of a
    different person's CV merged unheld against a full, divergent history. The
    name being unreadable, the next-strongest identity evidence the vault holds
    is the work history: ONE employer in common keeps the plain merge, because a
    false "different person" re-adds the friction ADR-037 removed. "In common"
    is the merge's identity rule (:func:`merge.company_names_match`, ADR-066)
    minus self-employment labels and generic-word-only overlaps
    (:func:`_same_employer_for_identity`). Nothing to compare — an employer-less
    extraction, or a vault with no employers — also keeps the plain merge.
    """
    if (extracted.personal_info.name or "").strip():
        return False
    incoming = _employers(extracted)
    held = _employers(vault)
    if not incoming or not held:
        return False
    return not any(_same_employer_for_identity(a, b) for a in incoming for b in held)


def evaluate_merge_gate(
    account_name: str | None,
    extracted: MasterProfileData,
    vault: MasterProfileData | None = None,
) -> GateResult:
    """Return the pre-merge gate verdict for an incoming CV extraction.

    *vault* is the profile the extraction would merge into (``None`` on a first
    import); it is read only for the nameless-extraction branch.
    """
    cv_name = extracted.personal_info.name or None

    # not-a-CV takes precedence: a near-empty doc has no reliable name to compare.
    if not looks_like_cv(extracted):
        return GateResult(gate="not_a_cv", account_name=account_name, cv_name=cv_name)

    if names_clearly_differ(account_name, cv_name):
        return GateResult(
            gate="name_divergence", account_name=account_name, cv_name=cv_name
        )

    # Same hold, same gate value (ruling V-1): a new value would park the upload
    # silently in every client that only knows the two existing ones.
    if nameless_history_diverges(extracted, vault):
        return GateResult(gate="name_divergence", account_name=account_name, cv_name=None)

    return GateResult(gate="none", account_name=account_name, cv_name=cv_name)
