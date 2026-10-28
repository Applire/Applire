# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ADV-7 (Hazelnut build 5 adversarial pass) — the merge verifier never lets a
merged sentence drop a German compound's qualifying prefix: a word is backed by
a source compound only as its MODIFIER (the word opens the compound), never as
its HEAD alone."""
import pytest

from applire.services.profile.merge_verifier import verify_merge


@pytest.mark.parametrize("sources,candidate", [
    (["Mitverantwortung für das Budget der Abteilung", "Budgetplanung mit SAP"],
     "Verantwortung für das Budget der Abteilung und Budgetplanung mit SAP"),
    (["Begleitung der SAP-Einführung", "Schulung der Key User"],
     "Leitung der SAP-Einführung und Schulung der Key User"),
    (["Durchführung von Lieferantenaudits", "Auswahl neuer Lieferanten"],
     "Führung von Lieferantenaudits und Auswahl neuer Lieferanten"),
])
def test_a_merge_cannot_drop_a_qualifying_prefix(sources, candidate):
    assert not verify_merge(candidate, sources), "the verifier passed an inflated merge"


def test_a_compound_modifier_still_counts():
    """Control: the modifier of a source compound is written in that source."""
    assert verify_merge(
        "Auswahl der Lieferanten und Lieferantenauswahl mit SAP Ariba",
        ["Lieferantenauswahl mit SAP Ariba", "Auswahl der Lieferanten"],
    )
    assert verify_merge(
        "Steuerung der Lieferanten im Einkauf",
        ["Lieferantensteuerung im Einkauf", "Steuerung der Lieferanten"],
    )
