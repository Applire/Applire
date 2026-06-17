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
Otherwise gate "none" and the merge proceeds unchanged.

Architecture boundary (ADR-041 amended): the system detects *difference* only —
identity is the user's call. Name divergence fires only when the normalised name
tokens are DISJOINT, so nicknames / reordering / middle names / maiden name /
transliteration are tolerated (a false "different person" re-adds the friction
Branch A removed).
"""
import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import (  # noqa: E402
    EducationEntry,
    MasterProfileData,
    PersonalInfo,
    Skill,
    WorkEntry,
)
from applire.services.profile.merge_gate import evaluate_merge_gate  # noqa: E402


def _cv(name: str = "Marcus Schmidt", *, with_content: bool = True) -> MasterProfileData:
    p = MasterProfileData(personal_info=PersonalInfo(name=name))
    if with_content:
        p.work_experience = [WorkEntry(company="Acme GmbH", role="Dev", start_date="2020-01")]
    return p


def test_matching_name_and_real_cv_does_not_gate():
    """The happy path: clean CV, matching name → no gate, no friction."""
    result = evaluate_merge_gate("Marcus Schmidt", _cv("Marcus Schmidt"))
    assert result.gate == "none"


def test_disjoint_name_triggers_divergence_gate():
    """A genuinely different person (no shared token) → name_divergence."""
    result = evaluate_merge_gate("Marcus Schmidt", _cv("Anna Bauer"))
    assert result.gate == "name_divergence"
    assert result.account_name == "Marcus Schmidt"
    assert result.cv_name == "Anna Bauer"


class TestNameVariantTolerance:
    """Legitimate name variants must NOT gate (any shared token → tolerated)."""

    def test_nickname_shared_surname(self):
        # Mike vs Michael — tolerated via shared "schmidt"
        assert evaluate_merge_gate("Michael Schmidt", _cv("Mike Schmidt")).gate == "none"

    def test_reordered_tokens(self):
        assert evaluate_merge_gate("Marcus Schmidt", _cv("Schmidt, Marcus")).gate == "none"

    def test_added_middle_name(self):
        assert evaluate_merge_gate("Marcus Schmidt", _cv("Marcus Aurelius Schmidt")).gate == "none"

    def test_maiden_to_married_shares_given_name(self):
        assert evaluate_merge_gate("Marcus Müller", _cv("Marcus Bauer")).gate == "none"

    def test_transliteration_shares_given_name(self):
        # "Müller" vs "Mueller" differs, but the shared given name keeps it tolerated
        assert evaluate_merge_gate("Hans Müller", _cv("Hans Mueller")).gate == "none"

    def test_exact_match_with_diacritics(self):
        assert evaluate_merge_gate("Müller", _cv("Müller")).gate == "none"


class TestNotACV:
    def test_near_empty_extraction_gates_not_a_cv(self):
        """No work / education / skills / certs extracted → not a CV."""
        garbage = _cv("Some Heading", with_content=False)
        assert evaluate_merge_gate("Marcus Schmidt", garbage).gate == "not_a_cv"

    def test_not_a_cv_takes_precedence_over_divergence(self):
        """A near-empty doc with a stray name is 'not a CV', not 'divergence'."""
        garbage = _cv("Acme Product Manual", with_content=False)
        assert evaluate_merge_gate("Marcus Schmidt", garbage).gate == "not_a_cv"

    def test_real_cv_with_only_education_is_not_gated(self):
        """A new-grad CV (education only) is a valid CV — no not_a_cv gate."""
        grad = MasterProfileData(personal_info=PersonalInfo(name="Marcus Schmidt"))
        grad.education = [EducationEntry(institution="TU München", degree="MSc")]
        assert evaluate_merge_gate("Marcus Schmidt", grad).gate == "none"


class TestEdges:
    def test_no_account_name_does_not_gate_divergence(self):
        """A brand-new user with no account name can't diverge — no gate."""
        assert evaluate_merge_gate("", _cv("Anna Bauer")).gate == "none"
        assert evaluate_merge_gate(None, _cv("Anna Bauer")).gate == "none"
