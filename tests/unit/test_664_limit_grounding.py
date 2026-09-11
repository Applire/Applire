# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#664 — the grounding catch for disclosed limits (ADR-075 amended 2026-09-11).

Every fixture below is verbatim from the captured 2026-09-05 delivery run
(`applire-core/backend/logs/llm/2026-09-05.jsonl`, records 666-686, the run
Bug #664 was filed from) or from the 2026-09-10 build-1 delivery run's persisted
`letter_data`. The two run's own numbers, reproduced by
`Documents/Runs/Nougat/build-2/l/runs/measure-664.txt`:

* 2026-09-05: 240 body sentences across 10 drafts, 38 of them state a limit,
  **6 ungrounded findings, 6/6 true positives** — `Wertschätzende Führung` x3 and
  `Qualitätsmanagement` x2 (both independently confirmed invented by the terminal
  reviewer's own check 1, records 679 and 682) plus one over-denial of
  `Fertigungsdigitalisierung`. The DELIVERED composition (record 681) is flagged;
  the corrector repair the final length floor DISCARDED (record 683) is clean.
* 2026-09-10 delivered letter: 0 findings, and its one limit sentence
  ("Mit IFS und BRC habe ich keine Erfahrung …") is correctly read as grounded.
"""
from applire.services.limit_grounding import (
    cut_ungrounded_limits,
    denial_segments,
    has_no_ungrounded_limit,
    split_sentences,
    ungrounded_limits,
)

# The 2026-09-05 run's six persisted denial LABELS, verbatim.
DENIED_2026_09_05 = [
    {"concept": "dauerhafte Führungsspanne von 120 Mitarbeitenden", "statement": "…"},
    {"concept": "weiteres Digitalisierungsprojekt in vergleichbarem Umfang", "statement": "…"},
    {
        "concept": "ein weiteres eigenständiges Digitalisierungs- oder "
        "IT-Integrationsprojekt in vergleichbarem Umfang",
        "statement": "…",
    },
    {"concept": "Direkte Erfahrung mit Verbundverpackungen", "statement": "…"},
    {"concept": "Eigenständige Vertriebserfahrung", "statement": "…"},
    {"concept": "eigenständige Investitionsplanung", "statement": "…"},
]

# The run's ledger rows this test needs, with the surface forms the captured
# VERIFIED COVERAGE CHECK block carries.
LEDGER_2026_09_05 = [
    {
        "concept": "Qualitätsmanagement",
        "surface_forms": ["Qualitätssicherung", "ISO 9001"],
        "claimable": True,
        "status": "partial",
    },
    {"concept": "Investitionsplanung", "surface_forms": [], "claimable": True, "status": "partial"},
    {"concept": "Verpackungsindustrie", "surface_forms": ["Verpackungsproduktion"],
     "claimable": True, "status": "partial"},
    {"concept": "SAP PP", "surface_forms": ["PP"], "claimable": True, "status": "direct"},
    {"concept": "SAP MM", "surface_forms": ["MM"], "claimable": True, "status": "direct"},
    {"concept": "SAP", "surface_forms": ["SAP ERP"], "claimable": True, "status": "direct"},
    {"concept": "Fertigung", "surface_forms": ["Fertigungsbereiche", "Serienfertigung"],
     "claimable": True, "status": "direct"},
    {"concept": "Fertigungsdigitalisierung", "surface_forms": ["Digitale Fertigung"],
     "claimable": True, "status": "partial"},
    {"concept": "Führungskompetenz", "surface_forms": ["Mitarbeiterführung", "Führung"],
     "claimable": True, "status": "direct"},
    {"concept": "Wertschätzende Führung", "surface_forms": ["Mitarbeiterführung"],
     "claimable": True, "status": "partial"},
]

# Record 681 — the composition LETTER_FINAL_FLOOR selected and shipped.
DELIVERED_2026_09_05 = {
    "body": {
        "paragraphs": [
            "Sehr geehrte Damen und Herren,",
            "Direkte Erfahrung mit Verbundverpackungen habe ich nicht; bei Weberit "
            "produziere ich seit 2021 Kunststoffverpackungen für Kosmetikkunden im "
            "Sauberraumbereich.",
            "Ein weiteres Digitalisierungs- oder IT-Integrationsprojekt habe ich nicht "
            "verantwortet; meine tägliche Produktionsdatenpraxis ist jedoch übertragbar. "
            "Eigenständige Investitionsplanung, Vertriebserfahrung und Qualitätsmanagement "
            "beanspruche ich nicht. Als Bereichsverantwortlicher begleitete ich "
            "ISO-9001-Audits.",
            "Gern erläutere ich Ihnen im persönlichen Gespräch, wie ich meine "
            "Produktionsverantwortung einbringen kann.",
        ]
    }
}


def _findings(letter):
    return ungrounded_limits(letter, LEDGER_2026_09_05, DENIED_2026_09_05)


def test_the_delivered_2026_09_05_letter_carries_exactly_one_ungrounded_limit():
    """The Bug's own delivery point: the persisted composition is flagged, and it is
    flagged on the concept the founder named, not on the two that ARE stated limits."""
    findings = _findings(DELIVERED_2026_09_05)
    assert [f.concept for f in findings] == ["Qualitätsmanagement"]
    assert findings[0].sentence == (
        "Eigenständige Investitionsplanung, Vertriebserfahrung und Qualitätsmanagement "
        "beanspruche ich nicht."
    )


def test_the_corrector_repair_the_final_floor_discarded_is_clean():
    """Record 683 removed the manufactured denial and kept the ISO-9001 affirmation.
    The control must prefer it — this is the fact the floor's selection now reads."""
    repaired = {
        "body": {
            "paragraphs": [
                "Sehr geehrte Damen und Herren,",
                "Direkte Erfahrung mit Verbundverpackungen habe ich nicht; bei Weberit "
                "produziere ich seit 2021 Kunststoffverpackungen für Kosmetikkunden.",
                "Als Bereichsverantwortlicher begleitete ich ISO-9001-Audits und "
                "Schnittstellen zur Qualitätssicherung. Eigenständige Investitionsplanung "
                "und Vertriebserfahrung beanspruche ich nicht.",
            ]
        }
    }
    assert _findings(repaired) == []
    assert has_no_ungrounded_limit(repaired, LEDGER_2026_09_05, DENIED_2026_09_05)
    assert not has_no_ungrounded_limit(
        DELIVERED_2026_09_05, LEDGER_2026_09_05, DENIED_2026_09_05
    )


def test_a_stated_limit_is_never_flagged_even_when_the_label_is_longer():
    """`Verbundverpackungen` is denied as "Direkte Erfahrung mit Verbundverpackungen".
    Grounding matches a ledger FORM inside the denial LABEL — the loose direction,
    deliberately: a missed catch ships what ships today, a false catch cuts an honest
    sentence."""
    letter = {
        "body": {
            "paragraphs": [
                "Direkte Erfahrung mit Verbundverpackungen habe ich nicht.",
                "Eine eigenständige Investitionsplanung habe ich nicht verantwortet.",
            ]
        }
    }
    ledger = LEDGER_2026_09_05 + [
        {"concept": "Verbundverpackungen", "surface_forms": [], "claimable": False,
         "status": "denied"}
    ]
    assert ungrounded_limits(letter, ledger, DENIED_2026_09_05) == []


def test_the_affirming_half_after_a_colon_is_not_read_as_denied():
    """Measured false positive from record 680: `SAP`, `SAP PP` and `SAP MM` were read
    out of the TRANSFER half of the sentence until the colon became a clause boundary."""
    letter = {
        "body": {
            "paragraphs": [
                "Eigenständige Investitionsplanung und Vertriebserfahrung bringe ich "
                "nicht mit: SAP PP/MM, Arbeitsplan-Stammdatenbereinigung sowie "
                "Kundenreklamationen übertragen sich auf die Aufgaben."
            ]
        }
    }
    assert ungrounded_limits(letter, LEDGER_2026_09_05, DENIED_2026_09_05) == []


def test_a_contrastive_sibling_clause_is_not_read_as_denied():
    """ADR-062 deleted a proximity matcher because German `nicht …, jedoch …` closes the
    negation at the comma and three of four contrastive transfer arguments were read as
    denials. The segment split is what keeps that from growing back."""
    letter = {
        "body": {
            "paragraphs": [
                "Ein weiteres Digitalisierungs- oder IT-Integrationsprojekt habe ich "
                "nicht verantwortet, meine Erfahrung mit Fertigungsdigitalisierung ist "
                "jedoch aus dem MES-Projekt belegt."
            ]
        }
    }
    assert ungrounded_limits(letter, LEDGER_2026_09_05, DENIED_2026_09_05) == []


def test_a_compound_does_not_flag_its_own_prefix_concept():
    """`surface_present` is a substring predicate (US212): `Fertigung` is "present" in
    `Fertigungsdigitalisierung`. Longest match wins, or every compound denial would flag
    its own stem as a second, invented finding (measured on record 673)."""
    letter = {
        "body": {"paragraphs": ["Fertigungsdigitalisierung selbst habe ich nicht verantwortet."]}
    }
    assert [f.concept for f in _findings(letter)] == ["Fertigungsdigitalisierung"]


def test_the_2026_09_10_delivered_letters_own_limit_sentence_is_grounded():
    """Second real delivered document, zero false positives (build-1 delivery run)."""
    letter = {
        "body": {
            "paragraphs": [
                "Mit IFS und BRC habe ich keine Erfahrung und produzierte nie direkt "
                "für Lebensmittelkunden."
            ]
        }
    }
    ledger = [
        {"concept": "IFS", "surface_forms": [], "claimable": False, "status": "denied"},
        {"concept": "BRC", "surface_forms": [], "claimable": False, "status": "denied"},
        {"concept": "Lebensmittelindustrie", "surface_forms": ["Lebensmittelkunden"],
         "claimable": False, "status": "denied"},
    ]
    assert ungrounded_limits(letter, ledger, []) == []


def test_the_cut_removes_the_sentence_and_nothing_else():
    """Founder ruling L-1 = A: a silent cut, whole sentences only, no replacement."""
    cut, findings = cut_ungrounded_limits(
        DELIVERED_2026_09_05, LEDGER_2026_09_05, DENIED_2026_09_05
    )
    assert [f.concept for f in findings] == ["Qualitätsmanagement"]
    paragraph = cut["body"]["paragraphs"][2]
    assert "Qualitätsmanagement" not in paragraph
    # the two sentences either side of the cut survive verbatim
    assert paragraph.startswith("Ein weiteres Digitalisierungs- oder IT-Integrationsprojekt")
    assert paragraph.endswith("Als Bereichsverantwortlicher begleitete ich ISO-9001-Audits.")
    # every other paragraph is untouched, and the original is not mutated
    assert cut["body"]["paragraphs"][1] == DELIVERED_2026_09_05["body"]["paragraphs"][1]
    assert "Qualitätsmanagement" in DELIVERED_2026_09_05["body"]["paragraphs"][2]


def test_the_cut_fails_open_when_it_would_empty_the_body():
    """A letter whose ONLY paragraph is the ungrounded limit keeps its letter and still
    reports the finding — a body with no paragraphs fails `LetterData` validation, and
    losing the document is never the safer direction."""
    letter = {"body": {"paragraphs": ["Qualitätsmanagement beanspruche ich nicht."]}}
    cut, findings = cut_ungrounded_limits(letter, LEDGER_2026_09_05, DENIED_2026_09_05)
    assert [f.concept for f in findings] == ["Qualitätsmanagement"]
    assert cut == letter


def test_split_sentences_is_lossless():
    paragraph = "Erstens A. Zweitens B; drittens C — und dann D! Ende?  "
    assert "".join(split_sentences(paragraph)) == paragraph


def test_a_sentence_with_no_denial_marker_yields_no_segments():
    assert denial_segments("Bei Weberit verantworte ich zwei Fertigungsbereiche.") == []


def test_malformed_input_never_raises_and_never_cuts():
    assert ungrounded_limits(None, LEDGER_2026_09_05, DENIED_2026_09_05) == []
    assert ungrounded_limits({}, LEDGER_2026_09_05, DENIED_2026_09_05) == []
    assert ungrounded_limits({"body": {"paragraphs": "nope"}}, LEDGER_2026_09_05, None) == []
    assert ungrounded_limits(DELIVERED_2026_09_05, None, None) == []
    assert ungrounded_limits(DELIVERED_2026_09_05, [{"bad": 1}, "x", None], None) == []
