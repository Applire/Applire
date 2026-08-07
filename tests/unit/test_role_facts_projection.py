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

"""#328 (PO decision 2026-08-07, option 4) + #382 — the quantified role facts
are DERIVED PROJECTIONS of the candidate's own bullet text, never the primary
store of the figure.

Option 4 has two halves. Part A (already shipped, guarded by
``test_role_facts_prompt_parity.py``) is the prompt-side rule: the extractor
and the reconciler must keep the figure IN the candidate's own
responsibility/achievement wording. This file is the other half: the typed
``team_size`` / ``budget_managed`` / ``industry_context`` fields are
**reconciled against those bullets at every write**, so that

* the typed value can be traced back to the bullet that states it
  (``provenance == "derived"``, with the bullet as the projection's quote), and
* a typed value that no bullet corroborates is kept but MARKED
  (``provenance == "uncorroborated"``) rather than silently presented as if the
  candidate's own prose backed it, and
* #382: a typed value that lost its unit on the way in ("6000000") regains it
  from the candidate's own wording ("6 Mio. €"), because the bullet — not the
  typed field — is where the figure actually lives.

ADR-062 clause 1 classification: everything asserted here is a FACT. "Does this
text state this figure" is settled by the tokens; the shared oracle extractor
``matchers.figures.extract_figures`` is the one implementation that settles it
(ADR-066), and no second figure parser is introduced.

ADR-070 boundary (the masquerade guard): a derived projection is NOT an
attestation. ``bar.attested`` stays exclusively the model-cited, fail-closed
verified channel of ``verify_attested_evidence`` — a projection computed by
code may never enter it, and the scope confrontation block must not hand the
judge a code-derived quote that reads like one.
"""

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.profile import MasterProfileData, WorkEntry  # noqa: E402
from applire.services.profile.reconcile.apply import apply_ops  # noqa: E402
from applire.services.profile.reconcile.ops import SetField  # noqa: E402
from applire.services.scope_requirements import (  # noqa: E402
    build_scope_ledger_entries,
    build_scope_prompt_block,
    collect_candidate_values,
)

SOURCE = "interview"

# The charter-run-#9 ground truth, verbatim (issue #328 / #382).
BULLET_EUR = (
    "Budgetverantwortung ca. 6 Mio. EUR (Personal, Instandhaltung, "
    "Material-Gemeinkosten)."
)
BULLET_EURO_SIGN = "Budgetverantwortung von ca. 6 Mio. € pro Jahr"
BULLET_TEAM = "Führung einer Schicht mit 38 Mitarbeitenden"


def _weberit(**overrides) -> WorkEntry:
    fields = {
        "company": "Weberit Kunststofftechnik GmbH",
        "role": "Leiter Produktion",
        "responsibilities": ["Shopfloor-Management", BULLET_EURO_SIGN],
        "achievements": [],
    }
    fields.update(overrides)
    return WorkEntry(**fields)


def _applied(entry: WorkEntry, **sets) -> WorkEntry:
    """Run the entry through the ONE write path (ADR-063), optionally filling
    typed fields by ``set_field`` — the op shape #382's triage pinned as the
    origin of the bare-digit budget."""
    ops = [SetField(target=entry.id, field=f, value=v) for f, v in sets.items()]
    profile = MasterProfileData(work_experience=[entry])
    return apply_ops(profile, ops, SOURCE).profile.work_experience[0]


# ── #382 — the unit survives, because the bullet keeps it ──────────────────


def test_bare_digit_budget_regains_its_currency_from_the_candidates_own_bullet():
    """#382, the reported shape exactly: the reconciler's ``str(int)`` coercion
    stores ``"6000000"``; the candidate's own bullet two lines below the
    furniture says "ca. 6 Mio. €". The projection adopts the bullet's wording,
    so the document furniture can never render an ambiguous magnitude.

    MUTATION CHECK: drop the unit anywhere on this path — canonicalise to a
    bare number, prefer the stored string over the bullet's, or stop matching
    "6000000" against the bullet's "6 Mio." magnitude — and this test fails.
    """
    entry = _applied(_weberit(), budget_managed=6000000)
    assert entry.budget_managed == "6 Mio. €", (
        "the bare digit string kept its ambiguity — six million what?"
    )
    projection = entry.role_fact_projections["budget_managed"]
    assert projection.unit == "€"
    assert projection.provenance == "derived"
    assert projection.quote == BULLET_EURO_SIGN


def test_budget_projection_keeps_a_written_out_currency_code():
    """The same figure written "6 Mio. EUR" — the unit is a code, not a symbol,
    and must survive identically."""
    entry = _applied(
        _weberit(responsibilities=["Shopfloor-Management", BULLET_EUR]),
        budget_managed=6000000,
    )
    assert entry.budget_managed == "6 Mio. EUR"
    assert entry.role_fact_projections["budget_managed"].unit == "EUR"


def test_a_stored_value_that_already_states_its_unit_is_left_verbatim():
    """The candidate's own qualifier ("ca.") is worth more than uniformity: a
    typed value that already carries a currency is corroborated, not rewritten.
    """
    entry = _applied(
        _weberit(budget_managed="ca. 6 Mio. EUR", responsibilities=[BULLET_EUR])
    )
    assert entry.budget_managed == "ca. 6 Mio. EUR"
    assert entry.role_fact_projections["budget_managed"].provenance == "derived"


def test_no_currency_is_invented_when_the_bullet_states_a_different_figure():
    """Fail-closed: corroboration is a figure match, not a topic match. A
    bullet about a 2-million budget may not lend its "€" to a stored 6 000 000.
    """
    entry = _applied(
        _weberit(responsibilities=["Budgetverantwortung von 2 Mio. € pro Jahr"]),
        budget_managed=6000000,
    )
    assert entry.budget_managed == "6000000"
    assert entry.role_fact_projections["budget_managed"].unit is None
    assert entry.role_fact_projections["budget_managed"].provenance == "uncorroborated"


# ── provenance: derived vs. uncorroborated ────────────────────────────────


def test_a_typed_value_no_bullet_states_is_kept_but_marked_uncorroborated():
    """Fail-OPEN on the value (an interview answer is real testimony even when
    no bullet repeats it — dropping it would break completeness, the scope
    floor and the furniture line) and fail-CLOSED on the provenance."""
    entry = _applied(
        _weberit(responsibilities=["Shopfloor-Management"]),
        budget_managed="6 Mio. €",
    )
    assert entry.budget_managed == "6 Mio. €"
    projection = entry.role_fact_projections["budget_managed"]
    assert projection.provenance == "uncorroborated"
    assert projection.quote is None


def test_team_size_is_derived_from_the_bullet_that_states_the_headcount():
    entry = _applied(
        _weberit(responsibilities=["Shopfloor-Management", BULLET_TEAM]),
        team_size=38,
    )
    assert entry.team_size == 38
    projection = entry.role_fact_projections["team_size"]
    assert projection.provenance == "derived"
    assert projection.quote == BULLET_TEAM
    assert projection.value == "38"


def test_team_size_no_bullet_states_is_marked_uncorroborated():
    entry = _applied(_weberit(responsibilities=[BULLET_TEAM]), team_size=90)
    assert entry.team_size == 90
    assert entry.role_fact_projections["team_size"].provenance == "uncorroborated"


def test_industry_context_stated_in_the_entrys_own_prose_is_derived():
    entry = _applied(_weberit(industry_context="Kunststofftechnik"))
    projection = entry.role_fact_projections["industry_context"]
    assert projection.provenance == "derived"
    assert "Kunststofftechnik" in (projection.quote or "")


def test_industry_context_absent_from_the_entrys_own_prose_is_uncorroborated():
    entry = _applied(_weberit(company="Weberit GmbH", industry_context="Luftfahrt"))
    assert entry.industry_context == "Luftfahrt"
    assert entry.role_fact_projections["industry_context"].provenance == "uncorroborated"


def test_an_absent_typed_field_gets_no_projection():
    entry = _applied(_weberit())
    assert "team_size" not in entry.role_fact_projections
    assert "budget_managed" not in entry.role_fact_projections


def test_the_add_role_door_projects_too():
    """``apply_add_role`` is the one work-entry write that does not go through
    ``apply_ops`` (it constructs a ``WorkEntry`` directly, ``role_add.py``).
    A door that skips the projection would leave entries whose provenance map
    is silently absent rather than honestly ``uncorroborated`` — the ADR-066
    "landed on 1 of N" shape this repository keeps re-learning."""
    from applire.schemas.profile_roles import AddRoleRequest
    from applire.services.profile.role_add import apply_add_role

    profile = MasterProfileData()
    result = apply_add_role(
        profile,
        AddRoleRequest(
            company="Weberit Kunststofftechnik GmbH",
            title="Leiter Produktion",
            start_date="2019-03",
            industry="Kunststofftechnik",
            source="manual",
        ),
    )
    entry = result.profile.work_experience[0]
    assert entry.role_fact_projections["industry_context"].provenance == "derived"


# ── #382 end to end: the furniture line is no longer ambiguous ────────────


def test_the_furniture_line_renders_the_unit_the_bullet_states():
    """#382's user-visible symptom, from the write path to the rendered value:
    ``GET /api/cv/{id}/html`` shipped "Budget: 6.000.000" — six million what? —
    because ``budget_display`` correctly refuses to invent a currency on a
    furniture line a recruiter reads as authoritative structured data. With the
    figure's unit recovered from the candidate's own bullet upstream, the
    filter's existing already-worded pass-through does the rest, with no
    renderer change."""
    from applire.templates.filters import budget_display

    entry = _applied(_weberit(), budget_managed=6000000)
    assert budget_display(entry.budget_managed, "de") == "6 Mio. €"


# ── the projection cannot drift from the bullets ──────────────────────────


def test_a_stale_projection_is_recomputed_and_cannot_survive_its_bullet():
    """The projection is recomputed on every write, so it is never a second,
    ageing store of the same fact. An entry carrying a projection whose quote
    is no longer in any bullet must come back out of ``apply_ops`` corrected,
    even when the op under application touches something else entirely."""
    entry = _weberit(
        budget_managed="6 Mio. €",
        responsibilities=["Shopfloor-Management"],  # the budget bullet is gone
        role_fact_projections={
            "budget_managed": {
                "value": "6 Mio. €",
                "unit": "€",
                "quote": BULLET_EURO_SIGN,
                "provenance": "derived",
            }
        },
    )
    got = _applied(entry, location="Wuppertal")
    assert got.role_fact_projections["budget_managed"].provenance == "uncorroborated"
    assert got.role_fact_projections["budget_managed"].quote is None


# ── ADR-070 boundary: derived is not attested ─────────────────────────────


def _scope_profile() -> dict:
    entry = _applied(_weberit(), budget_managed=6000000)
    return MasterProfileData(work_experience=[entry]).model_dump(mode="json")


_BUDGET_REQ = {
    "kind": "budget",
    "value": 5000000,
    "comparator": "min",
    "quote": "Budgetverantwortung ab 5 Mio. EUR",
    "level": "required",
}


def test_scope_candidate_values_carry_the_unit_and_the_derived_provenance():
    """#382's "any future consumer inherits the ambiguity": the scope
    confrontation is that consumer. It must see "6 Mio. €", not "6000000"."""
    values = collect_candidate_values(_scope_profile(), "budget")
    assert values and values[0]["value"] == "6 Mio. €"
    assert values[0]["provenance"] == "derived"
    assert values[0]["unit"] == "€"


def test_a_derived_projection_is_never_reported_as_an_attestation():
    """ADR-070 clause 1 — ``bar.attested`` is the model-cited, fail-closed
    verified channel and nothing else. A projection code computed from the
    vault's own bullets is evidence of a different kind, and must not be able
    to reach the judge or the writers wearing the attestation's clothes.

    MUTATION CHECK: wire the projection's quote into ``bar["attested"]`` (the
    shapes are identical — ``{entry, quote, unit}``) and this test fails.
    """
    profile = _scope_profile()
    block = build_scope_prompt_block([_BUDGET_REQ], profile, "de")
    entries = build_scope_ledger_entries(
        block,
        [{"concept": block[0]["concept"], "status": "direct",
          "cited_entry": block[0]["candidate_values"][0]["entry"],
          "reason": "the vault states the budget"}],
        profile,
    )
    bar = entries[0]["bar"]
    assert bar["attested"] is None, "a derived projection masqueraded as an attestation"
    assert bar["candidate_values"][0]["provenance"] == "derived"


def test_the_scope_confrontation_gives_a_derived_value_no_quote_channel():
    """The structural half of the same boundary: an attested quote and a
    derived projection are distinguished by which key carries prose, so a
    candidate value must never carry one. Otherwise the distinction survives
    only as a label the next prompt edit can blur."""
    values = collect_candidate_values(_scope_profile(), "budget")
    assert "quote" not in values[0]


def test_the_gap_prompt_tells_the_judge_what_provenance_means():
    """Prompt-first: the confrontation block gained a key, so the prompt that
    reads it gains a rule. Left unexplained, "provenance": "derived" is an
    invitation for the judge to treat a code-derived corroboration as the
    attestation ADR-070 makes it cite and verify separately — the same
    masquerade the fact layer forbids structurally, arriving through the
    prompt instead."""
    from applire.prompts.gap_analysis import SYSTEM_PROMPT

    assert "provenance" in SYSTEM_PROMPT
    assert "uncorroborated" in SYSTEM_PROMPT


def test_a_derived_typed_value_still_satisfies_the_ADR_069_direct_floor():
    """The projection changes the typed value's PROVENANCE marking, not its
    standing: ADR-069 clause 2 makes ``direct`` conditional on a cited typed
    vault value, and a bullet-corroborated projection is one. Pinned so the
    masquerade guard above cannot be over-applied into a silent downgrade of
    every scope row."""
    profile = _scope_profile()
    block = build_scope_prompt_block([_BUDGET_REQ], profile, "de")
    entries = build_scope_ledger_entries(
        block,
        [{"concept": block[0]["concept"], "status": "direct",
          "cited_entry": block[0]["candidate_values"][0]["entry"],
          "reason": "the vault states the budget"}],
        profile,
    )
    assert entries[0]["status"] == "direct"
