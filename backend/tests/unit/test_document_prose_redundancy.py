# Copyright (C) 2026 Tobias Rosenbaum
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

"""ADR-082 — redundancy reaches the delivered document, and the shared instrument misses it.

The fixtures below are the REAL captured defect from #659 (real-provider Marcus
run, 2026-09-02, `panel_review_case/operations_marcus_de` — a synthetic canonical
persona, so the content is committable verbatim). The real LLM was needed to
FIND this; it is not needed to guard it, which is the point of pinning it here.

Two separate defects are pinned, because ADR-082 places them at different layers:

* **L1 / SF-WRITE.27** — `services.cv._project_bullets` promoted a `ProjectEntry`'s
  ``description`` to a peer bullet beside the ``responsibilities`` it summarises.
  A description is a *summary of the same project*, so the overlap is manufactured
  deterministically, before any model or dedup pass. 4 of the 9 flagged pairs on the
  measured document, including the worst.
* **L2/L3 / SF-WRITE.26** — the delivered document states one achievement several
  times. `duplicate-bullets` could not see it: role-vs-project scope, exact `_norm`
  predicate. ADR-082 clause 5 widens it to every axis the document delivers, on a
  prose predicate; clauses 1–3 keep it a CHECK and never a repair.

The predicate is deliberately NOT `skills_near_dupe`: measured over this very
document it returns False on all 15 pairs (the two bullets #659 calls "nearly
identical" score Jaccard 0.594 against its 0.75 threshold). See
`test_the_skills_instrument_would_not_have_caught_this`, which pins that refutation
so nobody re-proposes the shortcut.
"""
from __future__ import annotations

import itertools

import pytest

from applire.services.ats_audit import (
    _audit_cv_text,
    _norm,
    bullets_prose_dupe,
    skills_near_dupe,
)
from applire.schemas.cv import TailoredCVData

# ── The captured defect (#659), verbatim from generated_cvs.tailored_data ──────

_DESCRIPTION = (
    "Einführung eines MES-Systems mit Maschinendatenerfassung an 14 Spritzgussmaschinen "
    "bei Weberit Kunststofftechnik – von der Auswahl über die Pilotlinie bis zum Rollout "
    "in beide Fertigungsbereiche. Teil der gemeinsam mit der Geschäftsführung erarbeiteten "
    "Industrie-4.0-Roadmap."
)
_RESP_ROLLOUT = (
    "Einführung eines MES-Systems mit Maschinendatenerfassung an 14 Spritzgussmaschinen "
    "als Projektleiter verantwortet – von der Auswahl über die Pilotlinie bis zum Rollout "
    "in beide Fertigungsbereiche."
)
_RESP_SHOPFLOOR = "Echtzeit-OEE-Transparenz direkt am Shopfloor-Board etabliert."
_RESP_ROADMAP = (
    "Projekt als Teil der mit der Geschäftsführung erarbeiteten Industrie-4.0-Roadmap umgesetzt."
)
_RESP_WIRTSCHAFTLICH = (
    "MES-Einführung bei Weberit 2023 wirtschaftlich mit der Geschäftsführung abgestimmt "
    "und im Rahmen der gemeinsam erarbeiteten Industrie-4.0-Roadmap vorbereitet."
)
_ACH_OEE = "OEE im Spritzguss innerhalb von 18 Monaten von 61 % auf 73 % gesteigert."

#: The role bullet the WRITER actually produced. All three generator rounds emitted
#: ``"projects": []`` and folded the project's evidence into this single line —
#: writer rule 9 was obeyed. The nested project was appended afterwards, by assembly.
_ROLE_BULLET_MES = (
    "MES-Einführung mit Maschinendatenerfassung an 14 Spritzgussmaschinen im Rahmen der "
    "gemeinsam mit der Geschäftsführung erarbeiteten Industrie-4.0-Roadmap vorbereitet und "
    "wirtschaftlich abgestimmt; als dokumentiertes Projektergebnis stieg die OEE im "
    "Spritzguss innerhalb von 18 Monaten von 61 % auf 73 %."
)
_ROLE_BULLET_LEAN = (
    "Shopfloor-Management und KVP-Routinen eingeführt und die Ausschussquote von 4,1 % auf "
    "2,3 % gesenkt; über SMED-Rüstworkshops und neue Feinplanung die Termintreue von 87 % "
    "auf 96 % verbessert."
)

#: The six bullets exactly as DELIVERED — description first, then the four
#: responsibilities, then the achievement. This is `_project_bullets`' output.
DELIVERED_PROJECT_BULLETS = [
    _DESCRIPTION,
    _RESP_ROLLOUT,
    _RESP_SHOPFLOOR,
    _RESP_ROADMAP,
    _RESP_WIRTSCHAFTLICH,
    _ACH_OEE,
]


def _vault_project(**over) -> dict:
    """The vault `ProjectEntry` behind the delivered bullets, as captured."""
    entry = {
        "name": "Einführung eines MES-Systems mit Maschinendatenerfassung",
        "role": "Projektleiter",
        "start_date": "2023",
        "description": _DESCRIPTION,
        "responsibilities": [
            _RESP_ROLLOUT,
            _RESP_SHOPFLOOR,
            _RESP_ROADMAP,
            _RESP_WIRTSCHAFTLICH,
        ],
        "achievements": [_ACH_OEE],
        "technologies": ["MES", "Maschinendatenerfassung", "Industrie 4.0"],
    }
    entry.update(over)
    return entry


# ── 1. The refutation, pinned so the shortcut is not re-proposed ───────────────


def test_the_skills_instrument_would_not_have_caught_this():
    """ADR-082 Context 1–2 / clause 6.

    Both #659 and #424 proposed widening `skills_near_dupe` to bullets. Measured
    over the document that motivated it, the instrument fires on NOTHING — its
    0.75 Jaccard threshold is calibrated for 2–5-token skill NAMES, and over
    30-token sentences the non-shared prose dominates the union.

    If this ever goes red, `skills_near_dupe` has been re-calibrated and its five
    vault-merge call sites need re-checking BEFORE this test is updated: it is a
    canary on that predicate, not a statement about bullets.
    """
    fired = [
        (a[:40], b[:40])
        for a, b in itertools.combinations(DELIVERED_PROJECT_BULLETS, 2)
        if skills_near_dupe(a, b)
    ]
    assert fired == [], f"skills_near_dupe unexpectedly fired on prose: {fired}"


def test_the_shipped_exact_match_predicate_finds_nothing_either():
    """Why every one of the four implementations in arc42 §5.3.23's matrix was
    blind: the STRONGEST of them is exact-match, and none of these six bullets is
    a byte-repeat of another."""
    norms = [_norm(b) for b in DELIVERED_PROJECT_BULLETS]
    assert len(set(norms)) == len(norms)


# ── 2. The prose predicate: it must catch the real pairs ──────────────────────


@pytest.mark.parametrize(
    "a, b, why",
    [
        (_DESCRIPTION, _RESP_ROLLOUT,
         "the pair #659 calls 'nearly identical sentence-for-sentence'"),
        (_DESCRIPTION, _RESP_ROADMAP, "the Industrie-4.0-Roadmap clause, twice"),
        (_RESP_ROADMAP, _RESP_WIRTSCHAFTLICH, "the Roadmap fact restated a third time"),
        (_ROLE_BULLET_MES, _ACH_OEE,
         "the role bullet carries the OEE span verbatim — the axis duplicate-bullets "
         "ALREADY claimed, failed on predicate alone"),
        (_ROLE_BULLET_MES, _RESP_WIRTSCHAFTLICH,
         "role vs nested project, same MES/Roadmap facts"),
    ],
)
def test_prose_predicate_catches_the_captured_redundancy(a, b, why):
    assert bullets_prose_dupe(a, b), why


def test_prose_predicate_is_symmetric():
    for a, b in itertools.combinations(DELIVERED_PROJECT_BULLETS, 2):
        assert bullets_prose_dupe(a, b) == bullets_prose_dupe(b, a)


# ── 3. The over-merge test: what must SURVIVE ─────────────────────────────────
#
# ADR-082 clause 2: a repairer pays for a false positive with a silently deleted
# achievement. Even as a detector, a predicate that flags distinct evidence trains
# the candidate to ignore it. These are the negative controls.


def test_the_one_genuinely_distinct_project_bullet_is_never_flagged():
    """`_RESP_SHOPFLOOR` states a fact none of its five siblings state. It is the
    bullet a repair pass would have to preserve, and the one this predicate must
    never touch."""
    for other in DELIVERED_PROJECT_BULLETS:
        if other == _RESP_SHOPFLOOR:
            continue
        assert not bullets_prose_dupe(_RESP_SHOPFLOOR, other), other[:60]


def test_two_distinct_achievements_sharing_domain_vocabulary_are_not_dupes():
    """Both are Lean/production bullets full of shared German operations nouns and
    percentage figures; they state entirely different achievements."""
    assert not bullets_prose_dupe(_ROLE_BULLET_LEAN, _ROLE_BULLET_MES)


@pytest.mark.parametrize(
    "a, b",
    [
        ("SAP PP für Disposition eingesetzt.", "SAP MM für Bestellanforderungen genutzt."),
        ("Team von 14 Mitarbeitenden geführt.", "Team von 38 Mitarbeitenden geführt."),
        ("ISO 9001 verantwortet.", "ISO 45001 vorbereitet."),
    ],
)
def test_short_bullets_sharing_a_stem_are_not_dupes(a, b):
    """Short bullets share a large token FRACTION by construction, which is exactly
    where an unguarded containment ratio over-merges. Two roles, two standards and
    two team sizes are distinct facts, not restatements."""
    assert not bullets_prose_dupe(a, b)


# ── 4. The delivered document: the widened check must FAIL on it ─────────────


def _cv_with(project_bullets: list[str], role_bullets: list[str]) -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "Stefan Brandt"},
        "summary": "Produktionsleiter.",
        "work_history": [{
            "company": "Weberit Kunststofftechnik GmbH",
            "role": "Produktionsleiter",
            "start_date": "2017-04",
            "bullets": role_bullets,
            "projects": [{
                "name": "Einführung eines MES-Systems mit Maschinendatenerfassung",
                "bullets": project_bullets,
            }],
        }],
        "skills": [],
    })


def _audit(cv: TailoredCVData):
    """Audit through the real seam. The extracted-text argument is built FROM the
    document so the unrelated `content-N` presence checks are satisfied and cannot
    mask what we assert on."""
    text = "\n".join(
        [cv.contact.name or "", cv.summary or ""]
        + [b for w in cv.work_history for b in (w.bullets or [])]
        + [b for w in cv.work_history for p in (w.projects or []) for b in (p.bullets or [])]
        + [b for p in (cv.projects or []) for b in (p.bullets or [])]
    )
    return _audit_cv_text(text, cv, keywords=[])


def _check(report, check_id: str):
    return next((c for c in report.checks if c.id == check_id), None)


def test_duplicate_bullets_FAILS_on_the_delivered_659_document():
    """The delivery-point assertion. This document shipped with
    `duplicate-bullets: pass`; ADR-082 clause 5 is what makes it fail."""
    cv = _cv_with(DELIVERED_PROJECT_BULLETS, [_ROLE_BULLET_MES, _ROLE_BULLET_LEAN])
    check = _check(_audit(cv), "duplicate-bullets")
    assert check is not None, "the check must be emitted whenever there is a nested project"
    assert check.status == "fail", check.details
    # It must name the offending text, or the finding is not actionable.
    assert "MES" in check.details or "Industrie" in check.details, check.details


def test_duplicate_bullets_catches_redundancy_WITHIN_one_bullet_list():
    """#659's stated scope: two near-duplicate bullets under one project, with NO
    role bullets at all — so the pre-ADR-082 role-vs-project comparison has nothing
    to compare against and would emit `pass`."""
    cv = _cv_with([_DESCRIPTION, _RESP_ROLLOUT, _RESP_SHOPFLOOR], role_bullets=[])
    check = _check(_audit(cv), "duplicate-bullets")
    assert check is not None and check.status == "fail", check


def test_duplicate_bullets_PASSES_on_a_document_with_no_redundancy():
    """The negative control for the check itself: a green report must still be
    reachable, or the check is a permanent red that everyone learns to ignore."""
    cv = _cv_with(
        [_RESP_SHOPFLOOR, "Rüstzeiten an der Pilotlinie um 35 % reduziert."],
        [_ROLE_BULLET_LEAN],
    )
    check = _check(_audit(cv), "duplicate-bullets")
    assert check is not None and check.status == "pass", check.details


def test_the_audit_never_mutates_the_document(monkeypatch):
    """ADR-076 clause 3 + ADR-082 clause 5: detection only. The audit may not become
    the repair pass ADR-058 clause 4 forbids."""
    cv = _cv_with(DELIVERED_PROJECT_BULLETS, [_ROLE_BULLET_MES])
    before = cv.model_dump(mode="json")
    _audit(cv)
    assert cv.model_dump(mode="json") == before


# ── 5. L1 — the projection that MANUFACTURES the redundancy (SF-WRITE.27) ─────


def test_project_bullets_does_not_promote_description_beside_its_own_detail():
    """ADR-082 clause 7. A `description` is a prose SUMMARY of the project; emitting
    it as a peer of the responsibilities it summarises manufactures the redundancy
    before any model or dedup pass is involved."""
    from applire.services.cv import _project_bullets

    bullets = _project_bullets(_vault_project())

    assert _DESCRIPTION not in bullets, (
        "the description must not ride alongside the detail it summarises"
    )
    # Every real fact still reaches the page — this is a projection fix, not a cut.
    for kept in (_RESP_ROLLOUT, _RESP_SHOPFLOOR, _RESP_ROADMAP, _RESP_WIRTSCHAFTLICH, _ACH_OEE):
        assert kept in bullets


def test_project_bullets_keeps_the_description_when_it_is_the_only_content():
    """#312's case survives: a project with nothing but a description still carries
    a line, because a heading over nothing is the defect #312 closed."""
    from applire.services.cv import _project_bullets

    only_desc = _vault_project(responsibilities=[], achievements=[])
    assert _project_bullets(only_desc) == [_DESCRIPTION]


def test_the_projection_fix_removes_the_worst_pair_but_not_the_whole_defect():
    """Honest scoping, pinned. Clause 7 removes the description-borne pairs — the
    largest single share — and the vault's OWN accumulation (two bullets both
    stating the Industrie-4.0-Roadmap fact) survives it, which is why clause 5's
    detector is still owed and why L2 is recorded as open."""
    from applire.services.cv import _project_bullets

    bullets = _project_bullets(_vault_project())
    surviving = [
        (a, b) for a, b in itertools.combinations(bullets, 2) if bullets_prose_dupe(a, b)
    ]
    assert surviving, "the vault-level accumulation must still be detectable"
    assert any(
        _RESP_ROADMAP in pair and _RESP_WIRTSCHAFTLICH in pair for pair in surviving
    ), surviving


# ── 6. #424 — one project entity rendered in BOTH containers ─────────────────


def _cv_project_in_both_containers() -> TailoredCVData:
    """#424's captured shape: the vault held two `ProjectEntry` records for one
    real project — one with `associated_experience` set (renders nested), one with
    it null (renders in the standalone Projekte section) — so assembly renders it
    twice, "leicht abweichend formuliert" as the blind hiring-manager put it.

    `_nest_projects` deduplicates by normalised name WITHIN each container and
    never across them, so this survives assembly. Both wordings therefore reach
    the delivered document, in two different bullet lists.
    """
    nested = (
        "Verantwortung für das Teilprojekt Abstimmung zwischen den Gesellschaften "
        "bei der Einführung von LucaNet für die Konzernkonsolidierung."
    )
    standalone = (
        "Einführung von LucaNet für die Konzernkonsolidierung der Gesellschaften "
        "verantwortet, inklusive Abstimmung zwischen GmbH und Vertriebsgesellschaft Schweiz."
    )
    return TailoredCVData.model_validate({
        "contact": {"name": "Katrin Hoffmann"},
        "summary": "Financial Controller.",
        "work_history": [{
            "company": "Schwarzwald Präzision GmbH",
            "role": "Financial Controller",
            "start_date": "2018-01",
            "bullets": ["Monatsabschlüsse für zwei Gesellschaften verantwortet."],
            "projects": [{
                "name": "Einführung LucaNet für Konsolidierung",
                "bullets": [nested],
            }],
        }],
        "projects": [{
            "name": "Einführung LucaNet für Konsolidierung (GmbH + Vertriebsgesellschaft Schweiz)",
            "bullets": [standalone],
        }],
        "skills": [],
    })


def test_duplicate_bullets_sees_a_project_rendered_nested_AND_standalone():
    """#424. The two wordings sit in two DIFFERENT bullet containers, so no
    per-axis scan compares them — only enumerating the whole delivered set does.
    This is a DETECTION assertion: ADR-082 clause 8 leaves the double-write itself
    to the reconciler's `ONE CONTAINER` rule, whose compliance is unmeasured, so
    the audit is what makes the residue visible on every delivered document.
    """
    check = _check(_audit(_cv_project_in_both_containers()), "duplicate-bullets")
    assert check is not None and check.status == "fail", check
    assert "LucaNet" in check.details, check.details
    # The finding must say WHERE, or the candidate cannot act on it.
    assert "Projekte >" in check.details, check.details


# ── 7. The cost property (ADR-082 clause 5's check is O(pairs)) ──────────────


def test_prose_tokens_is_memoized_so_tokenisation_is_linear_not_quadratic():
    """`bullets_prose_dupe` is called once per PAIR of delivered bullets, so a
    tokenizer that re-runs inside it is O(n^2) in the number of bullets when O(n)
    is enough. Measured on a 216-bullet document before memoization: 1,993 ms per
    audit; after: 778 ms.

    This pins the mechanism rather than a wall-clock number — a timing assertion
    would be flaky on a loaded CI runner, and the property that matters is that a
    repeat call does not re-tokenise.
    """
    from applire.services.ats_audit import _prose_tokens

    _prose_tokens.cache_clear()
    first = _prose_tokens(_RESP_ROLLOUT)
    hits_before = _prose_tokens.cache_info().hits
    second = _prose_tokens(_RESP_ROLLOUT)

    assert second is first, "a repeated tokenisation must come from the cache"
    assert _prose_tokens.cache_info().hits == hits_before + 1
    assert isinstance(first, tuple), "cached value must be immutable"


def test_the_empty_intersection_shortcut_cannot_change_a_verdict():
    """The short-circuit skips the quadratic scan when two bullets share no token
    at all. That is exact, not a heuristic — a contiguous shared run needs at
    least one shared token — so it may only skip work, never flip an answer."""
    from applire.services.ats_audit import _longest_shared_run, _prose_tokens

    disjoint = ("Rüstzeiten an der Pilotlinie um 35 % reduziert.",
                "Konzernkonsolidierung von Excel auf LucaNet umgestellt.")
    ta, tb = _prose_tokens(disjoint[0]), _prose_tokens(disjoint[1])
    assert not (set(ta) & set(tb)), "fixture must actually be token-disjoint"
    assert _longest_shared_run(ta, tb) == 0
    assert not bullets_prose_dupe(*disjoint)
