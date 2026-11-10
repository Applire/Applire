# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""790:11 / 790:40 (Hazelnut build 8) — a named ATTESTATION the vault never
mentions is not graded ``grounded`` by the claim's other figures.

#701 closed the hole for standard identifiers (ISO/DIN/EN/CFR). Attestations
— SOC 1/2/3, ISAE 3402/3000, SSAE 16/18, BSI C5 — are no identifiers under the
#374 pattern, so before this family "das SOC-2-Audit begleitet" rode on a true
"12 Mitarbeitende" (build-1 adversarial probe: SOC 2 and BSI C5 grounded when
fabricated). Each guard test asserts its BASELINE first (the pre-rule grading
through ``_verify_claim_unchecked`` is ``grounded``), so the test proves the
rule rather than a fixture that was never grounded.

Synthetic profile; no captured text.
"""
from __future__ import annotations

import pytest

from applire.services.oracle import verify_claim
from applire.services.oracle.audit import _verify_claim_unchecked
from applire.services.oracle.credentials import (
    ATTESTATION_FAMILIES,
    detect_credentials,
    entry_families,
    fold,
    is_backed,
)
from applire.services.oracle.extract import Claim

_W = "11111111-1111-1111-1111-111111111111"
_CV = "work_history[0].bullets[0]"
_LETTER = "body.paragraphs[0]"


def _profile(extra: str | None = None) -> dict:
    responsibilities = ["Team mit 12 Mitarbeitenden im Rechenzentrumsbetrieb gefuehrt."]
    if extra:
        responsibilities.append(extra)
    return {
        "personal_info": {"name": "Jonas Keller"},
        "professional_summary": {"de": "IT-Leiter mit Fokus auf Cloud-Betrieb."},
        "work_experience": [{
            "id": _W,
            "company": "Brennwerk Cloud GmbH",
            "role": "Leiter IT-Betrieb",
            "start_date": "2016-03",
            "responsibilities": responsibilities,
        }],
        "skills": [{"name": "ISO 27001"}],
    }


FABRICATED = [
    ("Team mit 12 Mitarbeitenden gefuehrt und das SOC-2-Audit begleitet.", "SOC-2", _CV),
    ("Team mit 12 Mitarbeitenden gefuehrt und das SOC2 Type II Audit begleitet.", "SOC2", _CV),
    ("Team mit 12 Mitarbeitenden gefuehrt, SOC 2, ISO 27001 betreut.", "SOC 2", _CV),
    ("Team mit 12 Mitarbeitenden gefuehrt und das BSI-C5-Testat erreicht.", "BSI-C5", _CV),
    ("Team mit 12 Mitarbeitenden gefuehrt und das C5-Testat erreicht.", "C5-Testat", _CV),
    ("Ich habe ein Team mit 12 Mitarbeitenden gefuehrt und das SOC-2-Audit begleitet.", "SOC-2", _LETTER),
]


@pytest.mark.parametrize("text,named,loc", FABRICATED, ids=[f[1] + "@" + f[2][:4] for f in FABRICATED])
@pytest.mark.asyncio
async def test_a_fabricated_attestation_no_longer_rides_on_a_true_figure(text, named, loc):
    base = await _verify_claim_unchecked(Claim(text=text, location=loc), _profile())
    assert base.verdict == "grounded", base  # the remainder of #701, reproduced
    v = await verify_claim(Claim(text=text, location=loc), _profile())
    assert v.verdict == "unbacked"
    assert v.detail_key == "identifierAbsent"
    assert v.detail_params == {"identifiers": named}
    assert "attestation" in v.detail


@pytest.mark.asyncio
async def test_soc1_is_not_backed_by_a_soc2_in_the_vault():
    text = "Team mit 12 Mitarbeitenden gefuehrt und das SOC-1-Audit begleitet."
    v = await verify_claim(Claim(text=text, location=_CV), _profile("SOC 2 Type II Audit begleitet."))
    assert v.verdict == "unbacked"


@pytest.mark.parametrize("claim,vault", [
    ("Team mit 12 Mitarbeitenden gefuehrt und das SOC-2-Audit begleitet.", "SOC-2-Audit begleitet."),
    ("Team mit 12 Mitarbeitenden gefuehrt und das SOC 2 Audit begleitet.", "SOC2 Type II Bericht verantwortet."),
    ("Team mit 12 Mitarbeitenden gefuehrt und das SOC 2 Audit begleitet.", "SOC II Bericht verantwortet."),
    ("Team mit 12 Mitarbeitenden gefuehrt und das BSI C5 Testat erreicht.", "Testierung nach C5:2020 begleitet."),
    ("Team mit 12 Mitarbeitenden gefuehrt und die ISAE 3402 Prüfung vorbereitet.", "ISAE-3402-Prüfung vorbereitet."),
], ids=["hyphen", "nospace", "roman", "c5-edition", "isae"])
@pytest.mark.asyncio
async def test_any_notation_of_the_same_report_in_the_vault_backs_it(claim, vault):
    v = await verify_claim(Claim(text=claim, location=_CV), _profile(vault))
    assert v.verdict == "grounded", v


@pytest.mark.parametrize("text,loc", [
    ("Team mit 12 Mitarbeitenden gefuehrt und ein SOC mit 2 Analysten aufgebaut.", _CV),
    ("Team mit 12 Mitarbeitenden gefuehrt, Dienstwagen Citroen C5 verwaltet.", _CV),
    ("Ihr Unternehmen ist nach SOC 2 attestiert und sucht 12 Mitarbeitende.", _LETTER),
], ids=["security-operations-centre", "bare-c5", "letter-about-the-employer"])
@pytest.mark.asyncio
async def test_what_is_not_an_attestation_claim_keeps_its_verdict(text, loc):
    base = await _verify_claim_unchecked(Claim(text=text, location=loc), _profile())
    v = await verify_claim(Claim(text=text, location=loc), _profile())
    assert v.verdict == base.verdict == "grounded"


def test_every_attestation_family_detects_and_folds_its_own_notations():
    cases = {
        "soc1": ["SOC 1", "SOC-1", "SOC1", "SOC I"],
        "soc2": ["SOC 2", "SOC-2", "SOC2", "SOC II", "SOC 2 Type II"],
        "soc3": ["SOC 3", "SOC III"],
        "isae3402": ["ISAE 3402", "ISAE-3402"],
        "isae3000": ["ISAE 3000"],
        "ssae": ["SSAE 18", "SSAE-16"],
        "bsi_c5": ["BSI C5", "BSI-C5", "C5-Testat", "C5:2020"],
    }
    assert set(cases) == set(ATTESTATION_FAMILIES)
    for family, forms in cases.items():
        for form in forms:
            found = [m for m in detect_credentials(f"Begleitete das {form} Audit.", kind="cv")]
            assert [m.family for m in found] == [family], (form, found)
            # the same notation in the vault backs it (lenient backing side)
            assert is_backed(found[0], fold(f"Projekt: {form} Bericht")), form
    # SOC 2.5 is a version number, not the report; a percentage is no report
    assert not detect_credentials("SOC 2.5 eingeführt.", kind="cv")


def test_an_attestation_entry_is_language_neutral():
    """A certification ENTRY naming an attestation keeps its family, so a name miss
    against an other-language vault stays red (entry rule, ADR-052 am. 10-23)."""
    assert "soc2" in entry_families("SOC 2 Type II Audit")


def test_the_claim_side_reads_the_upper_case_acronym_only():
    """The reports are written as upper-case acronyms; a lower-case "soc" is no
    report name (the backing side stays case-insensitive — lenient on purpose)."""
    assert not detect_credentials("Begleitete die soc 2 Runde.", kind="cv")
    assert detect_credentials("Begleitete die SOC 2 Runde.", kind="cv")
