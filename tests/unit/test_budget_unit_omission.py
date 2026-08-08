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

"""#382 — PO decision 2026-08-08, **Option A**: a budget value that carries no
unit is OMITTED from every delivered document, and the omission is addressed to
the user rather than swallowed.

Three properties, pinned here at the level each one lives at:

1. **The predicate is one implementation** (``utils.budget_unit``, ADR-066).
   "Does this budget wording carry a unit" is a FACT (ADR-062 clause 1) settled
   by the currency vocabulary ``role_facts`` already used — never re-derived per
   call site, and never *guessed*: no unit is ever invented for a bare figure.
2. **The document says nothing** rather than an ambiguous magnitude. The
   template filter (``budget_display``) is the fail-safe: it also covers a
   tailored CV persisted BEFORE this change, which is re-rendered straight from
   stored ``tailored_data`` without passing through ``_apply_role_facts``.
3. **The omission is visible.** The vault keeps the value (it is real
   testimony); the Health hub raises an issue for it, and completeness treats a
   unit-less budget as not-yet-answered so the one-question fix is offered where
   the data lives.

Cross-reference: ``test_cv_budget_display_all_templates.py`` pins the same
policy end-to-end through the real ``get_cv_html`` render on every template.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import MasterProfileData  # noqa: E402
from applire.services.profile import completeness as C  # noqa: E402
from applire.services.profile.health import assess_health  # noqa: E402
from applire.templates.filters import budget_display  # noqa: E402
from applire.utils.budget_unit import budget_needs_unit, budget_unit  # noqa: E402


# ---------------------------------------------------------------------------
# 1 — the predicate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,expected",
    [
        ("ca. 6 Mio. EUR", "EUR"),
        ("6 Mio. €", "€"),
        ("€200k", "€"),
        ("USD 2M", "USD"),
        ("2 Mio. CHF", "CHF"),
        ("£450,000", "£"),
        # The shape #382 was filed for: the reconciler's own int -> str coercion.
        ("6000000", None),
        ("6.000.000", None),
        ("~6m", None),
        ("mid six figures", None),
        ("", None),
        (None, None),
    ],
)
def test_budget_unit_reads_only_what_the_wording_states(value, expected):
    """The unit is READ from the candidate's wording, never inferred from the
    magnitude. "6000000" has no unit — six million of what is not knowable."""
    assert budget_unit(value) == expected


@pytest.mark.parametrize(
    "value,expected",
    [
        ("6000000", True),
        ("6.000.000", True),
        ("~6m", True),
        ("mid six figures", True),
        ("ca. 6 Mio. EUR", False),
        ("€200k", False),
        # Nothing stored is a COMPLETENESS gap, not a unit gap — the two are
        # different questions and must not be conflated in the user's view.
        ("", False),
        ("   ", False),
        (None, False),
    ],
)
def test_budget_needs_unit_is_only_about_a_value_that_exists(value, expected):
    assert budget_needs_unit(value) is expected


# ---------------------------------------------------------------------------
# 2 — the delivered document
# ---------------------------------------------------------------------------

def test_a_unit_less_budget_renders_as_nothing():
    """Option A at the template layer: the furniture line gets an empty string,
    so the template's own truthiness guard drops the whole labelled item."""
    assert budget_display("6000000", "de") == ""
    assert budget_display("6000000", "en") == ""


def test_a_pre_grouped_but_unit_less_budget_renders_as_nothing():
    """Grouping was the PREVIOUS fix (2026-07-30 finding 1). It is superseded:
    "6.000.000" is exactly as ambiguous as "6000000"."""
    assert budget_display("6.000.000", "de") == ""


def test_a_unit_bearing_budget_still_renders_verbatim():
    """The candidate's own wording is untouched — Option A omits, it never
    rewrites."""
    assert budget_display("ca. 6 Mio. EUR", "de") == "ca. 6 Mio. EUR"
    assert budget_display("€200k", "en") == "€200k"


def test_no_currency_is_ever_invented_to_rescue_a_bare_figure():
    """The rejected alternative, pinned so it cannot come back as a "helpful"
    default: a bare figure must not acquire € (or any other unit) on the way to
    the page."""
    for lang in ("de", "en"):
        rendered = budget_display("6000000", lang)
        assert "€" not in rendered and "EUR" not in rendered
        assert "6" not in rendered


# ---------------------------------------------------------------------------
# 3 — the omission is never silent
# ---------------------------------------------------------------------------

def _profile_with_unit_less_budget() -> MasterProfileData:
    return MasterProfileData.model_validate({
        "work_experience": [
            {
                "id": "w1",
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "start_date": "2016-01",
                "is_current": True,
                "expected_fields": ["team_size", "budget_managed"],
                "achievements": ["Rüstzeiten um 22 % gesenkt."],
                "team_size": 38,
                "budget_managed": "6000000",
            }
        ]
    })


def _profile_with_unit_bearing_budget() -> MasterProfileData:
    profile = _profile_with_unit_less_budget()
    profile.work_experience[0].budget_managed = "ca. 6 Mio. EUR"
    return profile


def test_health_raises_an_issue_for_a_unit_less_budget():
    health = assess_health(_profile_with_unit_less_budget())
    unit_issues = [i for i in health.issues if i.thread == "unit"]
    assert len(unit_issues) == 1
    issue = unit_issues[0]
    # Actionable, never blocking — the same call ADR-041 amended made for the
    # equivalent conflict class.
    assert issue.profile_mismatch_severity == "review"
    assert issue.field_ref == "work_experience.budget_managed"
    # The entry label is the join key the profile page uses to put the
    # affordance next to the affected field (same label field_gaps emits).
    assert issue.source_record_ref == "Produktionsleiter @ Weberit Kunststofftechnik GmbH"
    assert "6000000" in issue.summary


def test_health_issue_id_is_stable_across_reads():
    """``HealthIssue.id`` is contracted "stable, deterministic". It must not be
    keyed off ``WorkEntry.id``, whose default factory mints a fresh UUID for any
    legacy entry persisted before that field existed."""
    first = assess_health(_profile_with_unit_less_budget())
    second = assess_health(_profile_with_unit_less_budget())
    assert [i.id for i in first.issues] == [i.id for i in second.issues]


def test_health_stays_quiet_for_a_unit_bearing_budget():
    health = assess_health(_profile_with_unit_bearing_budget())
    assert [i for i in health.issues if i.thread == "unit"] == []


def test_a_unit_less_budget_is_not_a_present_field():
    """The value is in the vault, but the question it was meant to answer is
    still open — so the enrichment interview asks it (its prompt already says
    "size / currency") and the profile page can offer that fix."""
    entry = {"role": "Produktionsleiter", "company": "Weberit", "budget_managed": "6000000"}
    assert C.field_present(entry, "budget_managed") is False
    assert C.field_present({**entry, "budget_managed": "ca. 6 Mio. EUR"}, "budget_managed") is True


def test_the_unit_gap_reaches_the_completeness_field_gaps():
    profile = _profile_with_unit_less_budget()
    gaps = C.field_gaps(profile.model_dump())
    assert "budget_managed: Produktionsleiter @ Weberit Kunststofftechnik GmbH" in gaps


def test_the_vault_still_holds_the_value():
    """Option A omits from documents; it never deletes testimony. The figure
    re-enters the line the moment a unit is confirmed."""
    profile = _profile_with_unit_less_budget()
    assess_health(profile)
    assert profile.work_experience[0].budget_managed == "6000000"
