# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ADR-052 amended 2026-10-23 (Hazelnut build 3 fix round, WP-FO): a spelled-out
percent ("Prozent", "percent", "per cent") is the same figure as the sign.

Delivery run 2026-10-22 (Marcus, letter 87cda23f): "verbesserte die Termintreue von
87 auf 96 Prozent" and "von 61 auf 73 Prozent" graded ``unbacked`` ("No vault
evidence for figure(s): 87, 96 …") against a vault that says "von 87 % auf 96 %" —
the extractor read the words as bare numbers, the vault's as percents. Two of the
letter's three red rows.
"""
import pytest

from applire.schemas.oracle import Claim
from applire.services.oracle.audit import verify_claim
from applire.services.oracle.matchers import extract_figures

_WEBERIT = "affbb347-00da-441e-8029-e3f90884d779"
_VAULT = {
    "personal_info": {"name": "Stefan Brandt"},
    "work_experience": [
        {
            "id": _WEBERIT,
            "company": "Weberit Kunststofftechnik GmbH",
            "title": "Produktionsleiter",
            "start_date": "2017-04",
            "achievements": [
                "Ausschussquote von 4,1 % auf 2,3 % gesenkt.",
                "Termintreue von 87 % auf 96 % verbessert durch neue Feinplanung und Rüstworkshops (SMED).",
                "OEE im Spritzguss innerhalb von 18 Monaten von 61 % auf 73 % gesteigert.",
            ],
        }
    ],
}


def _kv(text):
    return {(f.kind, f.value) for f in extract_figures(text)}


@pytest.mark.parametrize(
    "spelled, sign",
    [
        ("von 87 auf 96 Prozent", "von 87 % auf 96 %"),
        ("von 4,1 auf 2,3 Prozent", "von 4,1 % auf 2,3 %"),
        ("um 40 Prozent gesteigert", "um 40 % gesteigert"),
        ("grew revenue by 40 percent", "grew revenue by 40%"),
        ("a 35 per cent rise", "a 35 % rise"),
        ("61–73 Prozent", "61–73 %"),
    ],
)
def test_spelled_percent_is_the_sign(spelled, sign):
    assert _kv(spelled) == _kv(sign)
    assert all(k == "percent" for k, _ in _kv(spelled))


@pytest.mark.parametrize("text", ["um 15 Prozentpunkte gestiegen", "up 15 percentage points"])
def test_percentage_points_are_not_a_percent(text):
    assert ("percent", "15") not in _kv(text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sentence",
    [
        "Dort führte ich Shopfloor-Management und KVP-Routinen ein, verbesserte die Termintreue "
        "von 87 auf 96 Prozent und senkte die Ausschussquote von 4,1 auf 2,3 Prozent.",
        "Durch Echtzeit-Transparenz am Shopfloor-Board stieg die OEE im Spritzguss innerhalb "
        "von 18 Monaten von 61 auf 73 Prozent.",
    ],
)
async def test_marcus_letter_prozent_sentences_ground(sentence):
    """The two delivered sentences, verbatim, anchored as the letter extractor stamped them."""
    claim = Claim(text=sentence, location="body.paragraphs[2][1]", source_experience_id=_WEBERIT,
                  anchor_inherited=True)
    v = await verify_claim(claim, _VAULT, None, document_language="de")
    assert v.verdict == "grounded", (v.verdict, v.detail)


@pytest.mark.asyncio
async def test_a_spelled_percent_the_vault_lacks_is_still_red():
    claim = Claim(text="Die Termintreue stieg von 70 auf 99 Prozent.", location="body.paragraphs[2][1]",
                  source_experience_id=_WEBERIT)
    v = await verify_claim(claim, _VAULT, None, document_language="de")
    assert v.verdict == "unbacked" and v.checker == "numbers"
