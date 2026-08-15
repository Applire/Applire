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

"""#548 — the CV education section rendered "Industriemeister Metall, Metall":
the field of study doubled, once inside the degree title, once as the
separate field value. A blind HR reviewer flagged it unprompted (2026-08-14
edge model comparison, build ``c8cb4fa9``, both DE runs).

**Triage (applire-prompt-first).** Ground truth
(``tests/files/panel_review_case/operations_marcus_de/cv_stefan_brandt.md``,
line 36): the source states the qualification on ONE line —
``Industriemeister Metall (IHK), 2010`` — a German Meister title where the
specialisation is fused into the title. Querying the LOCAL dev Postgres
(``master_profiles``/``generated_cvs``, `docker exec applire-core-postgres-1`,
2026-08-15) for this exact persona shows the locally-extracted entry already
has ``field=""`` — the join with an empty field renders correctly today
against the default local provider. The doubling was reported only from the
two edge-run models (qwen3.7-max, gpt-5.6-luna); it is model-dependent, not a
property of one provider's failure. Both extraction prompts
(``prompts/cv_extraction.py``, ``prompts/cv_extraction_segmented.py``, plus
the text/LinkedIn-import ``prompts/profile_extraction.py``) described "field"
only as "field of study or specialisation" with no instruction that it must
exclude words already present in "degree" — a genuine prompt gap (category B).
That prompt rule was added (v5/v4/v4 respectively) but is UNVERIFIED — it
owes a real-provider run (ADR-062 clause 7) that this suite cannot supply.

**Because Applire is BYOI (ADR-054),** any self-hosted or cloud model can be
plugged in — including the two that already produced this defect — so a
prompt rule alone cannot be the only defence for a presentation-visible
duplication. ``education_title`` (``templates/filters.py``) is the mechanical
fail-safe: exact, verbatim TOKEN containment only (unicode NFKC + casefold),
never fuzzy matching or a synonym dictionary (ADR-076 clause 4 — paraphrase
and semantic equivalence are judgements, never settled by string comparison;
exact containment is the closed mechanical remainder). It replaces an
identical inline Jinja join that was duplicated across all 7 CV templates
(ADR-066 smell) with one shared filter.
"""
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.templates.filters import education_title  # noqa: E402


# ---------------------------------------------------------------------------
# 1 — the exact reported defect, pinned
# ---------------------------------------------------------------------------


def test_field_duplicated_inside_degree_is_shown_once():
    """The exact real-run shape (#548): degree already states "Metall" — the
    duplicate field value must not be appended a second time."""
    assert education_title("Industriemeister Metall", "Metall") == "Industriemeister Metall"


def test_field_duplicated_inside_degree_is_case_and_unicode_insensitive():
    # Casing and NFKC differences must not defeat the containment check —
    # the same real-run value in varied real-world casing/composition.
    assert education_title("Industriemeister Metall", "metall") == "Industriemeister Metall"
    assert education_title("industriemeister metall", "METALL") == "industriemeister metall"


def test_other_german_titles_with_fused_specialisation_render_once():
    """Not a one-off patch for this one string — the same class of German
    title (specialisation fused into the title) must be covered generally."""
    assert (
        education_title("Fachinformatiker Systemintegration", "Systemintegration")
        == "Fachinformatiker Systemintegration"
    )
    assert education_title("Technischer Fachwirt", "Fachwirt") == "Technischer Fachwirt"


# ---------------------------------------------------------------------------
# 2 — the other half of the contract: a legitimately distinct field survives
# ---------------------------------------------------------------------------


def test_legitimately_distinct_field_is_not_dropped():
    """A degree whose title is generic on its own MUST keep its field — this
    is the failure mode a naive "drop field whenever degree exists" fix would
    have introduced, and the reason ADR-076 clause 4 requires exact
    containment, not a blanket suppression."""
    assert education_title("Bachelor of Science", "Informatik") == "Bachelor of Science, Informatik"
    assert education_title("Master", "Wirtschaftsinformatik") == "Master, Wirtschaftsinformatik"
    assert education_title("Diplom", "Betriebswirtschaftslehre") == "Diplom, Betriebswirtschaftslehre"


def test_partial_token_overlap_is_not_treated_as_containment():
    """A field that merely SHARES a substring with degree (but is not a
    subset of its whole tokens) must still render — guards the exact-token
    boundary against over-matching on substrings."""
    # "Wirtschaft" is a substring of "Fachwirt" but not the same token.
    assert education_title("Fachwirt", "Wirtschaft") == "Fachwirt, Wirtschaft"


# ---------------------------------------------------------------------------
# 3 — edges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "degree,field,expected",
    [
        ("Diplom-Ingenieur", None, "Diplom-Ingenieur"),
        ("Diplom-Ingenieur", "", "Diplom-Ingenieur"),
        (None, "Metall", "Metall"),
        ("", "Metall", "Metall"),
        (None, None, ""),
        ("", "", ""),
    ],
)
def test_missing_sides_degrade_gracefully(degree, field, expected):
    assert education_title(degree, field) == expected


# ---------------------------------------------------------------------------
# 4 — fixture-property check: prove the pinned string actually has the
#     property its name claims (feedback_fixture_gated_tests_are_not_gates /
#     feedback_mutation_test_the_guard) — a token-containment predicate run
#     directly over the real reported string, independent of the filter.
# ---------------------------------------------------------------------------


def test_the_reported_fixture_really_is_a_containment_case():
    """Guards against a fixture that LOOKS like the bug but isn't: confirm
    "metall" really is a token of "industriemeister metall" by direct
    tokenisation, not by trusting the assertion above."""
    degree_tokens = set(__import__("re").findall(r"\w+", "Industriemeister Metall".casefold()))
    field_tokens = set(__import__("re").findall(r"\w+", "Metall".casefold()))
    assert field_tokens.issubset(degree_tokens), (
        "the fixture does not actually exhibit token containment — "
        "the pinned test above would be vacuous"
    )
    # And the "distinct field" counter-fixture really is NOT a containment case.
    bsc_tokens = set(__import__("re").findall(r"\w+", "Bachelor of Science".casefold()))
    informatik_tokens = set(__import__("re").findall(r"\w+", "Informatik".casefold()))
    assert not informatik_tokens.issubset(bsc_tokens), (
        "the 'distinct field' counter-fixture is accidentally a containment "
        "case — it would not catch a filter that over-suppresses"
    )
