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

"""#552 fixture-integrity pin — the reconstructed post-interview Keyword
Ledger, ``tests/files/run_2026_08_15/`` (persona ``operations_marcus_de`` /
Stefan Brandt, synthetic).

The original DB rows this evidence run produced (2026-08-15 evening,
``operations_marcus_de``) were lost to a fresh-DB-reset ahead of the #538
evidence run (2026-08-16). This fixture is a log reconstruction: JSONL line
147's gap-classifier response + embedded JOB ANALYSIS/CANDIDATE PROFILE, plus
``profile.metadata.denied_concepts`` rebuilt from every reconciler-response
``denials`` list up to and including line 146, run through the REAL
production pipeline (``applire.services.gap.ledger_input_from_classification``
-> ``applire.services.keyword_ledger.build_keyword_ledger`` ->
``applire.services.scope_requirements.build_scope_prompt_block`` /
``build_scope_ledger_entries`` -> ``applire.services.keyword_ledger.
assert_claimable_backed`` -> ``applire.services.match_score.
compute_match_score_from_ledger``). Cross-checked byte-for-field against the
surviving API snapshot ``gap_analysis_post_interview.json`` — zero
divergence (all 38 rows, every field, and ``match_score``). Full derivation
in ``tests/files/run_2026_08_15/README.md``.

**The #552 class-proof finding pinned here is a REFUTAL, not a confirmation**
(per the reconstruction task's own instruction: report an absent property
rather than force it). None of the 6 denied rows (IFS, BRC,
Verpackungsindustrie, Lebensmittelindustrie, Konsumgüter, Lebensmittel)
carries ``adjacent_evidence`` — production code (``_denied_row`` /
``_floored_row``, ADR-048 amended 2026-08-13 #526) strips it by design on
every denied/floored row, and the raw classifier output never attached it to
these six concepts in any of the interview's 4 refresh calls either. The
Sauberraum/hygiene transfer material this run's testimony actually produced
(skill ``"Hygiene- und Dokumentationsdisziplin"``) surfaces instead as
regular claimable, narrative-backed material on a DIFFERENT, non-denied
ledger row (``"Verpackungen"``) — i.e. it does NOT live only as
denial-adjacent evidence for this run. If #552's blind-spot hypothesis holds
elsewhere, this fixture is not its positive evidence; it pins the negative
result so nobody re-derives it from scratch or misremembers it as positive.
"""
import json
from pathlib import Path

import pytest

_FIXTURE_DIR = Path(__file__).parent.parent / "files" / "run_2026_08_15"
_LEDGER_FILE = _FIXTURE_DIR / "post_interview_keyword_ledger.json"
_CV_FILE = _FIXTURE_DIR / "tailored_cv_data.json"
_LETTER_FILE = _FIXTURE_DIR / "cover_letter_data.json"

_DENIED_FAMILY = {
    "IFS",
    "BRC",
    "Verpackungsindustrie",
    "Lebensmittelindustrie",
    "Konsumgüter",
    "Lebensmittel",
}

pytestmark = pytest.mark.skipif(
    not _LEDGER_FILE.exists() or not _CV_FILE.exists() or not _LETTER_FILE.exists(),
    reason="#552 run_2026_08_15 fixtures not present in this checkout",
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_denied_family_present_with_denial_level_direct():
    """All 6 denied concepts from the run's testimony are present, `status:
    "denied"`, `denial_level: "direct"` — the durable floor #231/ADR-064 puts
    on a declared denial, regardless of what the classifier's own adjacency
    inference said on any given refresh."""
    ledger = _load(_LEDGER_FILE)["keyword_ledger"]
    by_concept = {row["concept"]: row for row in ledger}

    missing = _DENIED_FAMILY - by_concept.keys()
    assert not missing, f"denied family concepts missing from the ledger: {missing}"

    for concept in _DENIED_FAMILY:
        row = by_concept[concept]
        assert row["status"] == "denied", f"{concept}: expected status=denied, got {row['status']!r}"
        assert row["denial_level"] == "direct", (
            f"{concept}: expected denial_level=direct, got {row['denial_level']!r}"
        )
        assert row["claimable"] is False, f"{concept}: a denied row must never be claimable"


def test_denied_family_never_carries_adjacent_evidence():
    """The #552 class-proof property, PINNED AS IT ACTUALLY HELD (a refutal):
    none of the 6 denied rows carries `adjacent_evidence` — production code
    strips it unconditionally on the denial write (ADR-048 amended
    2026-08-13, #526: "a denial is the candidate's own position on the term
    itself, so there is no substitute to promote"). If a future change makes
    any of these rows carry a non-null `adjacent_evidence`, that is a
    regression of the #526 amendment, not a fix of #552 — flag it, don't
    silence this assertion."""
    ledger = _load(_LEDGER_FILE)["keyword_ledger"]
    by_concept = {row["concept"]: row for row in ledger}

    for concept in _DENIED_FAMILY:
        value = by_concept[concept].get("adjacent_evidence")
        assert value is None, (
            f"{concept}: denied row unexpectedly carries adjacent_evidence "
            f"{value!r} — this would overturn the #552 refutal this fixture "
            "pins; re-verify before treating it as confirmation of the "
            "original hypothesis"
        )


def test_transfer_material_is_regular_claimable_vault_material_not_denial_only():
    """The other half of the #552 class-proof: the Sauberraum/hygiene
    transfer material this run's testimony produced is NOT confined to
    denial-adjacent evidence. It surfaces as a regular `claimable`,
    `narrative_backed` ledger row under a different, non-denied concept
    ("Verpackungen"), and the same skill string appears directly in the
    tailored CV's skills list and the delivered letter body — i.e. any critic
    reading only claimable/narrative-backed rows WOULD see this material for
    this run."""
    ledger = _load(_LEDGER_FILE)["keyword_ledger"]
    by_concept = {row["concept"]: row for row in ledger}

    transfer_row = by_concept["Verpackungen"]
    assert transfer_row["status"] == "partial"
    assert transfer_row["claimable"] is True
    assert transfer_row["narrative_backed"] is True
    assert transfer_row["adjacent_evidence"] == "Hygiene- und Dokumentationsdisziplin"

    cv_data = _load(_CV_FILE)["tailored_data"]
    assert "Hygiene- und Dokumentationsdisziplin" in cv_data.get("skills", []), (
        "the transfer skill must also reach the delivered CV's skills list, "
        "not just the ledger row"
    )

    letter_data = _load(_LETTER_FILE)["letter_data"]
    letter_text = " ".join(letter_data["body"]["paragraphs"])
    assert "Hygiene- und Dokumentationsdisziplin" in letter_text, (
        "the transfer skill must also reach the delivered letter body"
    )
    assert "Sauberraum" in letter_text


def test_ledger_matches_api_snapshot_row_count_and_score():
    """Cross-check pin: the reconstruction's row count and match_score are
    exactly what the surviving API snapshot recorded — the strongest signal
    available that the reconstructed inputs (job analysis, profile,
    denied_concepts) and the real production code path are faithful to what
    actually ran on 2026-08-15."""
    fixture = _load(_LEDGER_FILE)
    assert len(fixture["keyword_ledger"]) == 38
    assert fixture["match_score"] == pytest.approx(0.7450980392156863)
