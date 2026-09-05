# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#542 / ADR-076 clause 5 — the signal on a REAL delivered document.

Every other test for this signal builds its own two-line ledger. That proves the
predicate and nothing about the population: a coverage instrument is only ever as good
as the ledger it meets, and this codebase has a recorded incident of exactly that
(`skills_near_dupe` measured 0.594 against its own 0.75 threshold once it was pointed at
a population it was not calibrated for).

So this file points the signal at `tests/files/run_2026_08_15/` — a committed fixture of
a REAL delivered CV and the REAL 38-row Keyword Ledger of the run that produced it,
reconstructed through the production pipeline and cross-checked field-for-field against
that run's own gap-analysis snapshot (see the fixture's README). The CV is the delivered,
reviewer-exhausted draft: `cv_tailoring` attempt 5 of 5, `approved: false` on every round.

What it pins, and why each number matters:

* **4 of 16**, not 16 of 16. The rank filter and the ledger's real `fit_weight`s make the
  check selective on production data. Measured with every weight forced to 1.0 the same
  document reports 15 — so this test is also the calibration record for that difference.
* **All four are TAG-ONLY** — claimed in the skills list, evidenced in no bullet. That is
  the class ADR-076 clause 5 names and that no other coverage instrument can see.
* **`Deutsch` and `Englisch` really do carry `fit_weight >= REQUIRED_WEIGHT`** in a
  production ledger. The top-2 demand bound keeps them out of the corrector's feedback,
  and this test pins BOTH halves — the risk and the bound — so a future widening of the
  bound meets the language rows on the way.

Not a fixture-gated skip: the files are committed and CI collects this path.
"""
import json
import sys
from pathlib import Path

import pytest

_root = Path(__file__).parent.parent.parent
_backend = _root / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.cv_gap_hints import (  # noqa: E402
    UNDERCLAIM_ISSUE_LIMIT,
    narrative_corpus_view,
    underclaim_signal_issues,
    verified_narrative_underclaim,
)

_FIXTURE = _root / "tests" / "files" / "run_2026_08_15"


@pytest.fixture(scope="module")
def delivered():
    """The delivered CV and the run's own ledger, from the committed fixture."""
    ledger = json.loads((_FIXTURE / "post_interview_keyword_ledger.json").read_text("utf-8"))
    cv = json.loads((_FIXTURE / "tailored_cv_data.json").read_text("utf-8"))
    # Both files wrap their payload with a `_provenance` block; the document is under
    # `tailored_data` and the ledger under `keyword_ledger`. Asserted rather than
    # assumed — reading the ENVELOPE instead of the document makes the narrative corpus
    # empty, which makes every concept look tag-only and the check look broken.
    assert "keyword_ledger" in ledger and "tailored_data" in cv
    return cv["tailored_data"], ledger["keyword_ledger"]


def test_the_fixture_is_a_real_document_with_a_real_narrative(delivered):
    """Guard the envelope trap above: if this ever reads 0 bullets, every assertion
    below becomes vacuously satisfiable in the wrong direction."""
    from applire.services.keyword_ledger import _tailored_narrative_texts

    cv, ledger = delivered
    assert len(_tailored_narrative_texts(narrative_corpus_view(cv))) == 11
    assert len(ledger) == 38


def test_the_signal_is_selective_on_a_real_ledger(delivered):
    """4 of 16 eligible concepts — not 16 of 16. A check that fires on everything is as
    useless as one that fires on nothing, and only a real ledger can show which it is."""
    from applire.services.keyword_ledger import (
        REQUIRED_WEIGHT,
        is_positioning_only,
        is_scope_entry,
    )

    cv, ledger = delivered
    eligible = [
        e for e in ledger
        if e.get("claimable")
        and (e.get("fit_weight") or 0) >= REQUIRED_WEIGHT
        and not is_positioning_only(e)
        and not is_scope_entry(e)
    ]
    assert len(eligible) == 16
    assert len(verified_narrative_underclaim(cv, ledger)) == 4


def test_every_finding_on_the_delivered_document_is_the_tag_only_class(delivered):
    """The class ADR-076 clause 5 names — claimed in the skills list, evidenced in no
    bullet — and the class no other coverage instrument reports."""
    cv, ledger = delivered
    found = verified_narrative_underclaim(cv, ledger)
    assert {c.concept for c in found} == {
        "Führungserfahrung", "Arbeitssicherheit", "Deutsch", "Englisch"
    }
    assert all(c.tag_only for c in found), "all four are CLAIMED and unevidenced, not absent"


def test_the_demand_goes_to_the_postings_headline_requirements(delivered):
    """`fit_weight`-descending, bounded at two: what the corrector is actually told."""
    cv, ledger = delivered
    issues = underclaim_signal_issues(cv, ledger)
    assert len(issues) == UNDERCLAIM_ISSUE_LIMIT
    assert "Führungserfahrung" in issues[0].text
    assert "Arbeitssicherheit" in issues[1].text
    for issue in issues:
        assert "skills-list entry does NOT satisfy this" in issue.text
        assert "grounding outranks coverage" in issue.text.lower()


def test_the_language_rows_reach_the_REPORT_but_never_the_demand(delivered):
    """A production ledger really does weight `Deutsch`/`Englisch` at REQUIRED_WEIGHT, so
    a language row IS reported by `narrative-evidence` — where it is arguably poor advice
    — and is kept out of the corrector's feedback only by the top-2 bound. Both halves
    are pinned here so a future widening of the bound meets them on the way."""
    cv, ledger = delivered
    reported = {c.concept for c in verified_narrative_underclaim(cv, ledger)}
    demanded = " ".join(i.text for i in underclaim_signal_issues(cv, ledger))
    assert {"Deutsch", "Englisch"} <= reported
    assert "Deutsch" not in demanded and "Englisch" not in demanded


def test_the_positioning_only_transfer_row_is_never_demanded(delivered):
    """#552's own row. `Verpackungen` is `partial` + `claimable` and carries the
    transfer material (`adjacent_evidence: "Hygiene- und Dokumentationsdisziplin"`), so
    `is_positioning_only` is True and ADR-048's 2026-07-27 amendment excludes it:
    demanding the JD's own term of a candidate who does not hold it is a demand to
    over-claim. Clause 5 is correctly NOT the owner of this class — clause 9 is."""
    from applire.services.keyword_ledger import is_positioning_only

    cv, ledger = delivered
    row = next(e for e in ledger if e.get("concept") == "Verpackungen")
    assert is_positioning_only(row), "fixture no longer has the property this test is about"
    assert row.get("adjacent_evidence") == "Hygiene- und Dokumentationsdisziplin"
    assert "Verpackungen" not in {c.concept for c in verified_narrative_underclaim(cv, ledger)}
