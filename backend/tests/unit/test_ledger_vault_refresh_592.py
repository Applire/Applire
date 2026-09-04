# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#592 — the DO-NOT-CLAIM list vs the vault's own text (ADR-048 amended).

The delivered defect is a STALE LEDGER, not a classification error. Measured
over every captured gap-classification call (1608 calls / 7497 ``gap`` rows),
the classifier returns ``gap`` against a term the same prompt's profile carries
in 9 rows (0.12 %) — and all nine are true gaps (six candidate denials, three a
different sense of an English common noun). At the DELIVERY point the picture is
different: of 4119 forbidden-term instances in 932 captured writer prompts, 30
(0.73 %) are carried by the same prompt's own vault text.

The mechanism, pinned against the captured artefacts:
``backend/logs/llm/2026-08-24.jsonl`` record 18 (gap analysis, 16:09 UTC) shows
the Anna-Bauer vault with two work entries carrying ZERO responsibilities and
ZERO achievements — ``REST APIs`` / ``microservices`` / ``backend`` /
``Kubernetes`` were genuinely absent and the ``gap`` verdicts were correct.
``backend/logs/llm/2026-08-25.jsonl`` record 4 (CV writer, 17:11 UTC) shows the
same profile with 2+2 and 2+1 bullets carrying all four terms — and a
DO-NOT-CLAIM block still forbidding them.

``reevaluate_gap_ledger_against_vault`` exists for exactly this (#274/#284/#273:
"a requirement's status must reflect whether the VAULT answers it, not whether
one particular interview turn happened to write something") and was called at
ONE seam only — ``services/session.py`` at interview-session start.
"""
from __future__ import annotations

import pytest

from applire.services.keyword_ledger import (
    DENIED_EVIDENCE,
    refresh_ledger_against_vault,
    split_ledger_for_prompt,
)


def _vault(*, bullets: list[str], denied: list[dict] | None = None) -> dict:
    return {
        "work_experience": [
            {
                "id": "w1",
                "company": "TechVision GmbH",
                "position": "Senior Software Engineer",
                "responsibilities": list(bullets),
                "achievements": [],
            }
        ],
        "skills": [],
        "metadata": {"denied_concepts": denied or []},
    }


def _gap_row(concept: str, forms: list[str] | None = None) -> dict:
    return {
        "concept": concept,
        "surface_forms": forms or [concept],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    }


def test_refresh_heals_a_gap_row_the_vault_now_answers():
    """The #592 shape: the ledger was built before the bullet existed."""
    ledger = [_gap_row("REST APIs")]
    vault = _vault(
        bullets=["Built and maintained REST APIs in FastAPI serving 2 million-plus daily requests."]
    )

    refreshed, changed = refresh_ledger_against_vault(ledger, vault, seam="test")

    assert changed is True
    row = refreshed[0]
    assert row["status"] == "direct"
    assert row["claimable"] is True
    assert "REST APIs" in row["evidence"]
    _claimable, forbidden = split_ledger_for_prompt(refreshed)
    assert "REST APIs" not in forbidden


def test_refresh_leaves_a_genuine_gap_alone():
    """A term the vault still does not carry stays forbidden."""
    ledger = [_gap_row("GraphQL")]
    vault = _vault(bullets=["Built and maintained REST APIs in FastAPI."])

    refreshed, changed = refresh_ledger_against_vault(ledger, vault, seam="test")

    assert changed is False
    assert refreshed[0]["status"] == "gap"
    _claimable, forbidden = split_ledger_for_prompt(refreshed)
    assert forbidden == ["GraphQL"]


def test_refresh_never_lifts_a_denied_concept_the_vault_prose_also_mentions():
    """The ADR-059 floor outranks vault presence at this seam too.

    Ten of the thirty measured delivery-point contradictions are terms that are
    ALSO on the denial rail (``Produktion`` in the Marcus case, ``RAG`` in the
    Priya case). A refresh that lifted those would overrule the candidate's own
    testimony — the exact failure the 2026-08-28 side branch was refuted for.
    """
    ledger = [_gap_row("Produktion")]
    vault = _vault(
        bullets=[
            "Vorbereitung der ISO-45001-Zertifizierung für die Produktion begleitet.",
        ],
        denied=[
            {
                "concept": "Produktion",
                "denial_level": "direct",
                "statement": "Ich habe keine direkte Produktionsverantwortung getragen.",
            }
        ],
    )

    refreshed, changed = refresh_ledger_against_vault(ledger, vault, seam="test")

    assert changed is False
    assert refreshed[0]["status"] == "gap"
    assert refreshed[0]["claimable"] is False


def test_refresh_never_touches_a_row_already_written_as_denied():
    ledger = [
        {
            **_gap_row("Kubernetes"),
            "status": "denied",
            "evidence": DENIED_EVIDENCE,
            "denial_level": "direct",
        }
    ]
    vault = _vault(bullets=["Right-sized Kubernetes node pools across three clusters."])

    refreshed, changed = refresh_ledger_against_vault(ledger, vault, seam="test")

    assert changed is False
    assert refreshed[0]["status"] == "denied"
    assert refreshed[0]["evidence"] == DENIED_EVIDENCE


def test_refresh_does_not_demote_a_claimable_row_at_a_read_seam():
    """The stated carve-out: ADR-061/#318's affirmative invariant runs at every
    ledger PERSIST seam, and this is a READ. A claimable row whose vault backing
    has since disappeared is NOT demoted here — that direction stays on the
    write paths, so a generation can never silently ship less than the persisted
    analysis authorised. Pinned in both directions so the carve-out cannot be
    widened by accident."""
    ledger = [
        {
            "concept": "Kubernetes",
            "surface_forms": ["Kubernetes"],
            "sources": ["required"],
            "fit_weight": 1.0,
            "status": "direct",
            "evidence": "ran production Kubernetes",
            "claimable": True,
        }
    ]
    vault = _vault(bullets=["Built and maintained REST APIs in FastAPI."])

    refreshed, changed = refresh_ledger_against_vault(ledger, vault, seam="test")

    assert refreshed[0]["status"] == "direct"
    assert refreshed[0]["claimable"] is True
    assert changed is False


def test_refresh_is_a_no_op_on_an_already_current_ledger():
    """Idempotence: the second refresh over the same vault reports no change."""
    ledger = [_gap_row("REST APIs")]
    vault = _vault(bullets=["Built and maintained REST APIs in FastAPI."])

    once, changed_1 = refresh_ledger_against_vault(ledger, vault, seam="test")
    twice, changed_2 = refresh_ledger_against_vault(once, vault, seam="test")

    assert changed_1 is True
    assert changed_2 is False
    assert twice == once


def test_refresh_tolerates_an_absent_vault_and_an_empty_ledger():
    assert refresh_ledger_against_vault([], {"work_experience": []}, seam="t") == ([], False)
    ledger = [_gap_row("REST APIs")]
    assert refresh_ledger_against_vault(ledger, None, seam="t") == (ledger, False)


def test_refresh_does_not_mutate_its_input():
    ledger = [_gap_row("REST APIs")]
    snapshot = [dict(row) for row in ledger]
    vault = _vault(bullets=["Built and maintained REST APIs in FastAPI."])

    refresh_ledger_against_vault(ledger, vault, seam="test")

    assert ledger == snapshot


@pytest.mark.parametrize(
    "concept,should_heal",
    [
        ("REST APIs", True),
        ("microservices", True),
        ("backend", True),
        ("GraphQL", False),
        ("Redis", False),
    ],
)
def test_refresh_reproduces_the_592_capture(concept: str, should_heal: bool):
    """The four concepts of the captured 2026-08-25 Anna-Bauer DO-NOT-CLAIM
    block, against the vault as the CV writer received it that day."""
    vault = {
        "work_experience": [
            {
                "id": "w0",
                "company": "TechVision GmbH",
                "position": "Senior Software Engineer",
                "responsibilities": [
                    "Architected and led the migration of a monolithic Django application "
                    "into 12 independently deployable microservices, coordinating a team of "
                    "6 engineers across 3 time zones over an 18-month rollout that touched "
                    "every customer-facing endpoint in production."
                ],
                "achievements": [
                    "Reduced infrastructure costs by 42% (380k dollars per year) by "
                    "right-sizing Kubernetes node pools."
                ],
            },
            {
                "id": "w1",
                "company": "StartupX AG",
                "position": "Backend Engineer",
                "responsibilities": [
                    "Built and maintained REST APIs in FastAPI serving 2 million-plus "
                    "daily requests."
                ],
                "achievements": [
                    "Delivered the company's first automated test suite, raising backend "
                    "coverage from 12% to 81%."
                ],
            },
        ],
        "skills": [],
        "metadata": {"denied_concepts": []},
    }
    ledger = [_gap_row(concept)]

    refreshed, _changed = refresh_ledger_against_vault(ledger, vault, seam="test")
    _claimable, forbidden = split_ledger_for_prompt(refreshed)

    assert (concept not in forbidden) is should_heal
