# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#469 — the tenure CEILING: claimed years may not exceed derivable years.

#214 (PR #459) removed durations from figure extraction, and it was right to:
a duration is derived from date spans, never stored as a literal, so
digit-matching it produced category-error attributions (run 8: "14 Jahren
Expertise" attributed to a shift headcount). But the removal took the
Oracle's only reachable tenure check with it — an *inflated* tenure claim now
has no deterministic catcher at all, which is the wave-7 guardrail ("never
fix by making the Oracle more permissive") breached through the back door.

The restored floor uses the CORRECT predicate, the one #403 specifies:
**claimed years ≤ years derivable from the vault's own date spans**. That is
a FACT under ADR-062 clause 1 — date arithmetic over ``start_date``/
``end_date``, settled by the data structure, never by reading prose.

DIRECTION AND BOUNDARY (both load-bearing, see the docstrings in
``audit._tenure_ceiling_flag`` and ``vault.derive_tenure_ceiling_years``):

* Only the OVERCLAIM direction is decidable. A claim BELOW the derivable
  span is not a lie about the span.
* The ceiling is the TOTAL derivable career span, which is a valid upper
  bound for any domain subset ("X Jahre Erfahrung in <domain>"). Whether a
  domain-scoped claim inflates *within* that total is a JUDGEMENT (which
  roles count toward domain X) and stays with the critic.

DOCUMENTED NON-GOAL — run 17's "11-jährige Führungserfahrung". The issue
that filed this regression names run 17's one true red as the case to
restore. It is NOT restorable by this predicate, and the honest reason is
recorded in :func:`test_run17_understatement_is_a_documented_non_goal`.
"""
from datetime import date

import pytest

from applire.schemas.oracle import Claim
from applire.services.oracle import verify_claim
from applire.services.oracle.matchers import build_vault_index
from applire.services.oracle.matchers.figures import extract_tenure_claims
from applire.services.oracle.matchers.vault import derive_tenure_ceiling_years

_THIS_YEAR = date.today().year


def _profile_started(year: int, month: str = "01") -> dict:
    """A one-role vault whose single span runs from ``year`` to today."""
    return {
        "personal_info": {"name": "Stefan Brandt"},
        "work_experience": [
            {
                "id": "w1",
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "start_date": f"{year}-{month}",
                "is_current": True,
                "responsibilities": [
                    "Führung von 38 Mitarbeitenden im Dreischichtbetrieb.",
                ],
            }
        ],
    }


# ── the extractor: a tenure claim's NUMBER, digit and spelled ───────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("mit 14 Jahren Expertise in der Kunststofftechnik", [14.0]),
        ("meine 11-jährige Führungserfahrung", [11.0]),
        ("12+ years of experience", [12.0]),
        ("over 20 yrs of experience", [20.0]),
        # spelled forms — run 8's second claim shape, both languages
        ("sowie zehn Jahre ISO-9001-Audit-Praxis", [10.0]),
        ("twenty years of leadership", [20.0]),
        ("fünfundzwanzig Jahre in der Fertigung", [25.0]),
        # a non-numeric word before the unit is not a duration
        ("in den letzten Jahren", []),
        ("seit Jahren im Unternehmen", []),
        # months are not years (the #412 "in 18 Monaten" class)
        ("Steigerung in 18 Monaten", []),
        # a bare headcount is not a duration
        ("eine Schicht mit 14 Mitarbeitenden", []),
    ],
)
def test_extract_tenure_claims(text, expected):
    assert [t.years for t in extract_tenure_claims(text)] == expected


# ── the ceiling: date arithmetic over the vault's own spans ─────────────────


def test_ceiling_is_the_envelope_not_the_de_overlapped_union():
    """A career BREAK must not shrink the ceiling.

    The spans below cover ~4 + ~15 years of actual employment with a
    three-year gap between them; the de-overlapped union
    ``gap_inference._total_experience_years`` computes for seniority would
    therefore be ~19, while the honest thing a candidate says is "seit 2004
    in der Branche" (~22). This value is used to ACCUSE, so it resolves
    toward the larger number.
    """
    profile = {
        "work_experience": [
            {"id": "w1", "start_date": "2004-07", "end_date": "2008-07"},
            {"id": "w2", "start_date": "2011-08", "is_current": True},
        ]
    }
    years = derive_tenure_ceiling_years(profile)
    assert years is not None
    assert years == pytest.approx(_THIS_YEAR - 2004 + 0.5, abs=0.6)


def test_ceiling_is_none_when_no_span_is_dated():
    """Fail open: an undated vault disables the check entirely."""
    profile = {"work_experience": [{"id": "w1", "company": "Weberit"}]}
    assert derive_tenure_ceiling_years(profile) is None


def test_an_undated_entry_never_lowers_the_ceiling():
    profile = {
        "work_experience": [
            {"id": "w1", "start_date": "2004-07", "is_current": True},
            {"id": "w2", "company": "Undated GmbH"},
        ]
    }
    years = derive_tenure_ceiling_years(profile)
    assert years is not None and years > 20.0


def test_ceiling_reaches_the_vault_index():
    index = build_vault_index(_profile_started(2015))
    assert index.derivable_tenure_years == pytest.approx(_THIS_YEAR - 2015, abs=0.6)


# ── the verdict path: an overclaim lands unbacked ───────────────────────────


@pytest.mark.asyncio
async def test_tenure_overclaim_is_unbacked_and_names_the_derived_ceiling():
    """The honesty floor #214 removed, restored on the correct predicate."""
    verdict = await verify_claim(
        Claim(
            text="Mit 25 Jahren Erfahrung in der Kunststofftechnik führe ich Werke.",
            location="body.paragraphs[1][0]",
        ),
        _profile_started(2015),
    )
    assert verdict.verdict == "unbacked"
    assert verdict.checker == "numbers"
    assert verdict.detail is not None
    assert "25" in verdict.detail
    assert str(_THIS_YEAR - 2015) in verdict.detail


@pytest.mark.asyncio
async def test_spelled_tenure_overclaim_is_caught_too():
    """Run 8's second claim shape — "zehn Jahre …" — but inflated.

    The spelled path is the one #214's exemption reaches on BOTH sides; if
    the ceiling only understood digits, writing the number out in words
    would be a one-word bypass of the honesty floor.
    """
    verdict = await verify_claim(
        Claim(
            text="Ich bringe zwanzig Jahre Erfahrung in der Fertigung mit.",
            location="body.paragraphs[1][0]",
        ),
        _profile_started(2015),
    )
    assert verdict.verdict == "unbacked"
    assert verdict.checker == "numbers"


@pytest.mark.asyncio
async def test_a_claim_within_the_derivable_span_is_never_flagged():
    verdict = await verify_claim(
        Claim(
            text="Mit fünf Jahren Erfahrung in der Kunststofftechnik führe ich Teams.",
            location="body.paragraphs[1][0]",
        ),
        _profile_started(2015),
    )
    assert verdict.verdict != "unbacked"


# 2012-01 → 2025-08 is 13.58 derivable years: the exact shape the tolerance
# exists for (idiomatic round-up) and, at 16, the shape it must not excuse.
_ROUNDING_PROFILE = {
    "personal_info": {"name": "Stefan Brandt"},
    "work_experience": [
        {
            "id": "w1",
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "start_date": "2012-01",
            "end_date": "2025-08",
        }
    ],
}


@pytest.mark.asyncio
async def test_rounding_up_a_partial_year_is_honest_not_inflation():
    """13.6 derivable years and "14 Jahre" is how people speak, and a
    year-only end date can lose up to twelve months of the real span — the
    tolerance exists for exactly these two, and for nothing else."""
    verdict = await verify_claim(
        Claim(
            text="Mit 14 Jahren Erfahrung in der Kunststofftechnik führe ich Werke.",
            location="body.paragraphs[1][0]",
        ),
        _ROUNDING_PROFILE,
    )
    assert verdict.verdict != "unbacked"


@pytest.mark.asyncio
async def test_the_tolerance_does_not_excuse_a_real_overclaim():
    """Control for the test above — one year of slack, not open season."""
    verdict = await verify_claim(
        Claim(
            text="Mit 16 Jahren Erfahrung in der Kunststofftechnik führe ich Werke.",
            location="body.paragraphs[1][0]",
        ),
        _ROUNDING_PROFILE,
    )
    assert verdict.verdict == "unbacked"
    assert verdict.checker == "numbers"


@pytest.mark.asyncio
async def test_the_check_is_disabled_when_the_vault_has_no_dated_span():
    """Fail open — an undated vault must never manufacture an accusation."""
    profile = {
        "personal_info": {"name": "Stefan Brandt"},
        "work_experience": [
            {
                "id": "w1",
                "company": "Weberit Kunststofftechnik GmbH",
                "role": "Produktionsleiter",
                "responsibilities": ["Führung von 38 Mitarbeitenden."],
            }
        ],
    }
    verdict = await verify_claim(
        Claim(
            text="Mit 40 Jahren Erfahrung in der Kunststofftechnik führe ich Werke.",
            location="body.paragraphs[1][0]",
        ),
        profile,
    )
    assert verdict.verdict != "unbacked"


@pytest.mark.asyncio
async def test_domain_scoped_claim_above_the_total_span_is_still_caught():
    """"X Jahre Erfahrung in <domain>" bounds the domain AND the total.

    The total career span is a valid upper bound for every domain subset, so
    a domain-scoped claim ABOVE it is decidable. (Inflation *below* the
    total is not — see the boundary test below.)
    """
    verdict = await verify_claim(
        Claim(
            text="Ich verfüge über 30 Jahre Erfahrung im Mehrschichtbetrieb.",
            location="body.paragraphs[1][0]",
        ),
        _profile_started(2015),
    )
    assert verdict.verdict == "unbacked"


@pytest.mark.asyncio
async def test_domain_inflation_below_the_total_span_is_left_to_judgement():
    """The stated boundary, asserted so it cannot be silently widened.

    A candidate with a 22-year career claiming "20 Jahre Führungserfahrung"
    while only ever leading for 8 IS inflating — but deciding which roles
    count as leadership is a JUDGEMENT (ADR-062 clause 1), not date
    arithmetic. The deterministic ceiling must stay silent here rather than
    guess; the critic's ``numeric_inconsistency`` advisory owns it.
    """
    profile = {
        "work_experience": [
            {"id": "w1", "start_date": "2004-07", "end_date": "2018-07"},
            {"id": "w2", "start_date": "2018-08", "is_current": True},
        ]
    }
    verdict = await verify_claim(
        Claim(
            text="Ich verfüge über 20 Jahre Führungserfahrung in der Produktion.",
            location="body.paragraphs[1][0]",
        ),
        profile,
    )
    assert verdict.verdict != "unbacked"


@pytest.mark.asyncio
async def test_run17_understatement_is_a_documented_non_goal():
    """Run 17's "Meine 11-jährige Führungserfahrung" — NOT catchable here.

    #469 was filed because #214 silenced this red. Read precisely, run 17's
    letter claim is an UNDERSTATEMENT, not an inflation: the vault's dated
    spans run 07/2004 → today (~22 years) and the CV states "14 Jahren", so
    11 < 22 in the direction this check can decide. Both blind reviewers and
    the SF-CRITIC advisory flagged it as an INCONSISTENCY ("14 vs. 11 vs.
    ~15 Jahre"), which is a comparison between two DOCUMENTS, not between a
    document and the vault's date arithmetic.

    The pre-#214 catch was the broken mechanism getting lucky: "11" simply
    happened to appear as no vault figure. Rebuilding a predicate that fires
    on an understatement would be inventing a limit on the candidate — the
    ADR-061 clause-5 error in its deflation direction — so this test pins
    the honest scope instead: the deterministic floor owns INFLATION, the
    critic owns cross-document inconsistency (#403/#417).
    """
    profile = {
        "personal_info": {"name": "Stefan Brandt"},
        "work_experience": [
            {"id": "w1", "start_date": "2004-07", "end_date": "2011-07"},
            {"id": "w2", "start_date": "2011-08", "end_date": "2017-03"},
            {"id": "w3", "start_date": "2017-04", "is_current": True},
        ],
    }
    verdict = await verify_claim(
        Claim(
            text="Meine 11-jährige Führungserfahrung bringe ich vollständig ein.",
            location="body.paragraphs[1][0]",
        ),
        profile,
    )
    assert verdict.verdict != "unbacked"


# ── the #214 fix stays fixed: no digit-coincidence matching returns ─────────


@pytest.mark.asyncio
async def test_a_true_tenure_claim_never_attributes_to_a_headcount():
    """Run 8's category error must stay closed — the ceiling restores the
    detection #214 removed WITHOUT restoring the false attribution."""
    profile = {
        "personal_info": {"name": "Stefan Brandt"},
        "work_experience": [
            {
                "id": "w1",
                "company": "Rasselstein",
                "role": "Schichtleiter",
                "start_date": "2004-07",
                "end_date": "2011-07",
                "achievements": ["Führung einer Schicht mit 14 Mitarbeitenden"],
            },
            {
                "id": "w2",
                "company": "Weberit",
                "role": "Produktionsleiter",
                "start_date": "2011-08",
                "is_current": True,
                "achievements": ["Rollout auf 14 Spritzgussmaschinen"],
            },
        ],
    }
    verdict = await verify_claim(
        Claim(
            text=(
                "Als erfahrener Produktionsleiter mit 14 Jahren Expertise in "
                "der Kunststofftechnik und im Mehrschichtbetrieb bewerbe ich mich."
            ),
            location="body.paragraphs[1][0]",
        ),
        profile,
    )
    assert verdict.verdict not in ("unbacked", "misattributed")
