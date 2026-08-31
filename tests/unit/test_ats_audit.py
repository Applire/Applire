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

import pytest

from applire.schemas.cv import TailoredCVData
from applire.services.ats_audit import _audit_cv_text, _audit_letter_text, _norm, surface_present


# ---------------------------------------------------------------------------
# #172 — the shared near-duplicate skill predicate. ONE instrument used by the
# reconciler (import merge), the render-side CV dedup, and the ATS audit.
# ---------------------------------------------------------------------------

# Real UAT pairs (2026-07-15 edge run) that rendered as separate skills but mean
# the same thing (or a strict refinement) — must be near-dupes.
_UAT_NEAR_DUPE_PAIRS = [
    ("Team Leadership", "Team Leadership and Mentorship"),
    ("Project Management", "Cross Functional Project Management"),
    ("GxP Compliance", "Regulatory Compliance and Validation Methodologies (GxP, CSV)"),
    ("Stakeholder Management", "Stakeholder Management & C-Level Consulting"),
]

# Bare single-token containment — one side is a SINGLE token strictly inside the
# other, larger token set. Under the strict predicate (#172, 2026-07-15 UAT) this
# is NOT an auto-merge: 'React' ⊂ 'React Native' are distinct skills, and merging
# would silently swallow one (persisted corruption) or rename Docker into a
# compound. The reconciler routes these to a user confirmation instead — never a
# silent merge — so `skills_near_dupe` must return False for them.
_SINGLE_TOKEN_CONTAINMENT_PAIRS = [
    ("React", "React Native"),
    ("AWS", "AWS Lambda"),
    ("Spring", "Spring Boot"),
    ("Vue", "Vue Router"),
    ("Excel", "Excel VBA"),
    ("Docker", "Docker & Kubernetes"),
    ("Docker", "Cloud Infrastructure & Deployment (Docker Compose)"),
]

# Pairs that share a token or look similar but are genuinely distinct skills —
# must NOT merge.
_MUST_NOT_MERGE_PAIRS = [
    ("Java", "JavaScript"),
    ("Python", "TypeScript"),
    ("Team Leadership", "Project Leadership"),
    ("React", "Vue"),
]


@pytest.mark.parametrize("a,b", _UAT_NEAR_DUPE_PAIRS)
def test_skills_near_dupe_true_for_uat_pairs(a, b):
    from applire.services.ats_audit import skills_near_dupe

    assert skills_near_dupe(a, b) is True
    assert skills_near_dupe(b, a) is True  # symmetric


@pytest.mark.parametrize("a,b", _MUST_NOT_MERGE_PAIRS)
def test_skills_near_dupe_false_for_distinct_pairs(a, b):
    from applire.services.ats_audit import skills_near_dupe

    assert skills_near_dupe(a, b) is False
    assert skills_near_dupe(b, a) is False


@pytest.mark.parametrize("a,b", _SINGLE_TOKEN_CONTAINMENT_PAIRS)
def test_skills_near_dupe_false_for_single_token_containment(a, b):
    """Bare single-token containment is NOT an auto-merge near-dupe (#172 strict):
    'React' ⊂ 'React Native' must stay distinct so the merge never silently drops
    a genuine skill."""
    from applire.services.ats_audit import skills_near_dupe

    assert skills_near_dupe(a, b) is False
    assert skills_near_dupe(b, a) is False


def test_skills_near_dupe_jaccard_boundary():
    """Non-containment high-overlap: 6 of 8 tokens shared → Jaccard 0.75 → dupe;
    dropping the overlap below the threshold → not a dupe."""
    from applire.services.ats_audit import skills_near_dupe

    a = "alpha beta gamma delta epsilon zeta eta"      # 7 tokens
    b = "alpha beta gamma delta epsilon zeta theta"    # 7 tokens, 6 shared → 6/8
    assert skills_near_dupe(a, b) is True
    c = "alpha beta gamma delta epsilon phi"           # 6 tokens
    d = "alpha beta gamma delta epsilon rho sigma"     # shares 5, 5/8 = 0.625
    assert skills_near_dupe(c, d) is False


# ---------------------------------------------------------------------------
# #308 (E049/US271, ADR-066/ADR-067) — shared-parenthetical-abbreviation shape.
#
# Ground truth (2026-07 captured LLM log): the vault holds
# 'MES (Manufacturing Execution System)'; a German CV writer correctly emitted
# 'Fertigungsleitsysteme (MES)' — the German translation, canonical-abbreviation
# preserved. Token-set Jaccard scores this pair at 1/5 = 0.2 (well below the 0.75
# threshold), so the #192 guarantee step re-added the vault's English spelling as
# a spurious "missing" skill, and the Oracle's grounding matcher graded the
# translated label 'unbacked' for the identical reason. Both symptoms share ONE
# cause (ADR-066: fix the predicate once) -- skills_near_dupe never recognised
# that two skill names carrying the SAME parenthetical abbreviation name the same
# skill, whatever language surrounds it.
# ---------------------------------------------------------------------------

_SHARED_ABBREVIATION_PAIRS = [
    ("MES (Manufacturing Execution System)", "Fertigungsleitsysteme (MES)"),
]

_SHARED_ABBREVIATION_NEGATIVE_PAIRS = [
    # 'Advanced' is a qualifier, not an abbreviation: single token but 8 chars
    # (over the 2-6 shape guard) and only 1 uppercase letter.
    ("Excel (Advanced)", "Word (Advanced)"),
    # Unrelated skills, no parens at all -- existing Jaccard/containment behaviour
    # must be untouched by the new disjunct.
    ("Team Leadership", "Team Building"),
    # One-sided: only one name carries a parenthetical. Governed by the existing
    # single-token-containment rule (skills_single_token_containment), NOT by the
    # new shared-abbreviation disjunct -- skills_near_dupe must stay False here.
    ("MES", "Fertigungsleitsysteme (MES)"),
]


@pytest.mark.parametrize("a,b", _SHARED_ABBREVIATION_PAIRS)
def test_skills_near_dupe_true_for_shared_parenthetical_abbreviation(a, b):
    from applire.services.ats_audit import skills_near_dupe

    assert skills_near_dupe(a, b) is True
    assert skills_near_dupe(b, a) is True  # symmetric


@pytest.mark.parametrize("a,b", _SHARED_ABBREVIATION_NEGATIVE_PAIRS)
def test_skills_near_dupe_false_without_shared_abbreviation_shape(a, b):
    from applire.services.ats_audit import skills_near_dupe

    assert skills_near_dupe(a, b) is False
    assert skills_near_dupe(b, a) is False


def test_skills_near_dupe_shared_abbreviation_is_case_insensitive():
    """The shape guard runs on the raw form (>= 2 uppercase letters each), but the
    final comparison of two already-qualifying abbreviations is case-insensitive:
    'GxP' (2 uppercase) and 'GXP' (3 uppercase) both pass the guard independently
    and must be recognised as the same abbreviation."""
    from applire.services.ats_audit import skills_near_dupe

    assert skills_near_dupe("Something (GxP)", "Other Thing (GXP)") is True


def test_skills_near_dupe_ambiguous_double_abbreviation_does_not_match():
    """Both the head and the parenthetical look abbreviation-shaped inside a
    single name -- there is no way to tell which one is canonical, so the
    predicate must not guess: no shared-abbreviation match for this shape."""
    from applire.services.ats_audit import skills_near_dupe

    assert skills_near_dupe("ABC (XYZ)", "Something Else (XYZ)") is False


def test_skill_tokens_folds_variants_and_strips_punct():
    from applire.services.ats_audit import skill_tokens

    assert skill_tokens("Code-Review") == skill_tokens("code reviews")
    # Conjunctions/ampersands are dropped; parenthesised tokens are unwrapped.
    assert "gxp" in skill_tokens("Methodologies (GxP, CSV)")
    assert "csv" in skill_tokens("Methodologies (GxP, CSV)")
    assert "&" not in skill_tokens("Docker & Kubernetes")


def test_skill_tokens_stems_only_purely_alpha_tokens():
    """The plural fold must skip tokens with internal punctuation, so 'node.js'
    stays intact instead of losing its trailing 's' ('node.j') (#172 minor)."""
    from applire.services.ats_audit import skill_tokens

    assert skill_tokens("node.js") == frozenset({"node.js"})
    assert "node.j" not in skill_tokens("node.js")
    # Purely-alphabetic plurals still fold as before.
    assert skill_tokens("reviews") == skill_tokens("review")


# ---------------------------------------------------------------------------
# #171a / #169 / #172 — three new deterministic CV checks: page-length,
# duplicate-bullets, skills-near-dupe.
# ---------------------------------------------------------------------------


def _check_by_id(report, cid):
    return next((c for c in report.checks if c.id == cid), None)


def test_page_length_check_absent_without_page_count():
    """Callers that don't supply a page count get no page-length check (back-compat
    with every text-only test)."""
    report = _audit_cv_text(_full_text(), _CV, keywords=[])
    assert _check_by_id(report, "page-length") is None


# E042/US238 (ADR-051 §5 + amendment §3): target-aware band. Default target = region
# standard (DACH = 2), so the #171a 2/3 behaviour is preserved when no target is given.


def test_page_length_at_standard_target_pass_no_advisory():
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=2)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "pass" and c.details is None


def test_page_length_three_pages_pass_with_senior_advisory():
    # target defaults to standard (2); 3 pages is within the DACH max → senior advisory.
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=3)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "pass"
    assert c.details and "3 pages" in c.details and "senior" in c.details and "2" in c.details
    assert c.details_key == "page-length-senior"
    assert c.details_params == {"pages": 3, "region": "DACH", "standard": 2}


def test_page_length_over_max_fails():
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=6)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "fail"
    assert c.details and "6" in c.details and "2" in c.details and "condensed" not in c.details
    assert c.details_key == "page-length-exceeds"
    assert c.details_params == {"pages": 6, "region": "DACH", "standard": 2, "max": 3}


def test_page_length_chosen_target_above_standard_pass_with_deviation_advisory():
    # User chose target=3 (senior). 3 pages USES the allowance (3 > standard 2) and
    # meets it → pass WITH deviation advisory.
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=3, target=3)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "pass"
    assert c.details and "chosen target of 3" in c.details and "2" in c.details
    assert c.details_key == "page-length-target"
    assert c.details_params == {"pages": 3, "target": 3, "region": "DACH", "standard": 2}


def test_page_length_at_standard_with_substandard_target_plain_pass():
    # Adversarial find (2026-07-16): target=1 (below the standard — MCP-reachable only)
    # with a 2-page result must NOT claim "acceptable for senior profiles": 2 pages IS
    # the DACH standard. Within-norm documents never carry an advisory.
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=2, target=1)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "pass" and c.details is None
    assert c.details_key is None and c.details_params is None


def test_page_length_over_explicit_substandard_target_is_honest_miss():
    # #238 founder-acceptance F4: rewritten from the old
    # "still_senior_advisory" expectation, which CODIFIED the bug this fixes —
    # an EXPLICIT target (here target=1) that was missed must never be dressed
    # up as senior-profile advice, even though 3 pages is within the DACH max.
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=3, target=1)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "fail"
    assert c.details_key == "page-length-target-missed"


def test_page_length_within_norm_under_higher_chosen_target_no_advisory():
    # E042 follow-up: user chose target=3 but the document already fits the DACH
    # standard (2) — no deviation happened, so no advisory noise. Plain pass.
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=2, target=3)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "pass" and c.details is None
    assert c.details_key is None and c.details_params is None


def test_page_length_under_chosen_target_no_advisory_when_target_is_standard():
    # target explicitly the standard; one page is under it → plain pass, no advisory.
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=1, target=2)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "pass" and c.details is None


# #238 (founder-acceptance F4): the founder chose an explicit page target in the
# template picker ("2 pages — DACH standard") and got 3 pages back dressed as
# "acceptable for senior profiles" — the miss was reframed as advice. The old
# band checked condensation_exhausted ONLY once page_count > maximum, so at
# exactly maximum (3 <= 3) it was dead: a chosen-but-missed target inside the
# regional max fell straight into the unconditional senior-advisory pass.


def test_page_length_target_missed_within_max_is_honest_fail():
    # The exact founder scenario: explicit target=2 (== the DACH standard, i.e.
    # the "2 pages — DACH standard" picker option), condense loop exhausted its
    # budget, delivered 3 (== the DACH max). Must be an honest miss, not advice.
    report = _audit_cv_text(
        _full_text(), _CV, keywords=[], page_count=3, target=2, condensation_exhausted=True
    )
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "fail"
    assert c.details and "3 pages" in c.details and "2" in c.details
    assert "senior" not in c.details
    assert c.details_key == "page-length-target-missed"
    assert c.details_params == {
        "pages": 3, "target": 2, "region": "DACH", "standard": 2, "max": 3,
    }


def test_page_length_target_missed_within_max_honest_even_when_not_exhausted():
    # Defensive: the condense loop should always set condensation_exhausted when
    # it fails to meet the target, but the section-editor re-audit path can call
    # _audit_cv_text WITHOUT running the loop at all. An explicit target miss must
    # read as honest regardless of the flag's value.
    report = _audit_cv_text(
        _full_text(), _CV, keywords=[], page_count=3, target=2, condensation_exhausted=False
    )
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "fail"
    assert c.details_key == "page-length-target-missed"


def test_page_length_target_met_still_plain_pass():
    # Same explicit target (2) but the document actually meets it — a real pass,
    # not touched by the honest-miss branch.
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=2, target=2)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "pass" and c.details is None
    assert c.details_key is None and c.details_params is None


def test_page_length_fail_exhausted_wording():
    # condensation ran to exhaustion and still over max → honest "condensed" wording.
    report = _audit_cv_text(
        _full_text(), _CV, keywords=[], page_count=5, target=2, condensation_exhausted=True
    )
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "fail"
    assert c.details and "condensed to the maximum" in c.details and "5" in c.details
    assert c.details_key == "page-length-exhausted"
    assert c.details_params == {"pages": 5, "region": "DACH", "standard": 2, "max": 3}


_CV_DUP_BULLET = TailoredCVData.model_validate({
    "contact": {"name": "Anna Bauer"},
    "work_history": [
        {
            "company": "Acme GmbH", "role": "Engineer", "start_date": "2020",
            "bullets": ["Led the platform migration", "Mentored the team"],
            "projects": [
                {"name": "Atlas", "bullets": ["Led the platform migration", "Shipped v2"]},
            ],
        },
    ],
    "skills": [],
})


def test_duplicate_bullets_check_flags_role_vs_project_collision():
    report = _audit_cv_text("Anna Bauer", _CV_DUP_BULLET, keywords=[])
    c = _check_by_id(report, "duplicate-bullets")
    assert c is not None and c.status == "fail"
    assert "Led the platform migration" in (c.details or "")


def test_duplicate_bullets_check_passes_when_project_bullets_distinct():
    cv = _CV_DUP_BULLET.model_copy(deep=True)
    cv.work_history[0].projects[0].bullets = ["Shipped v2"]
    report = _audit_cv_text("Anna Bauer", cv, keywords=[])
    c = _check_by_id(report, "duplicate-bullets")
    assert c is not None and c.status == "pass"


def test_skills_near_dupe_check_flags_uat_pair():
    cv = _CV.model_copy(update={
        "skills": ["Team Leadership", "Team Leadership and Mentorship", "Python"]
    })
    report = _audit_cv_text(_full_text(), cv, keywords=[])
    c = _check_by_id(report, "skills-near-dupe")
    assert c is not None and c.status == "fail"
    assert "Team Leadership" in (c.details or "")


def test_skills_near_dupe_check_passes_on_clean_skills():
    report = _audit_cv_text(_full_text(), _CV, keywords=[])
    c = _check_by_id(report, "skills-near-dupe")
    assert c is not None and c.status == "pass"


def test_skills_near_dupe_check_passes_on_single_token_containment():
    """React + React Native are distinct skills; the audit must not flag a legit CV
    that legitimately lists both (#172 strict predicate)."""
    cv = _CV.model_copy(update={"skills": ["React", "React Native", "Python"]})
    report = _audit_cv_text(_full_text(), cv, keywords=[])
    c = _check_by_id(report, "skills-near-dupe")
    assert c is not None and c.status == "pass"


# ---------------------------------------------------------------------------
# #391 interim (PO-ruled 2026-08-15, ADR-076 amendment 4 point 6): the
# skills-weak-vault-tie advisory. Measurement-only — never a failure, never
# touches which skills ship. Positive shape is the #391 ground truth itself;
# negatives are the three legitimate-tie shapes the advisory must stay silent
# on (shared paren abbreviation, multi-token containment, exact match).
# ---------------------------------------------------------------------------


def test_skills_weak_vault_tie_flags_391_ground_truth_shape():
    """The exact #391 shape: a JD-echoing requirement string is vault-tied to a
    real profile skill only because they share the single token 'controlling'."""
    cv = _CV.model_copy(update={"skills": ["5 Jahre Controlling-Erfahrung"]})
    report = _audit_cv_text(
        _full_text() + "5 Jahre Controlling-Erfahrung\n",
        cv, keywords=[], vault_skill_forms=["Controlling"],
    )
    c = _check_by_id(report, "skills-weak-vault-tie")
    assert c is not None and c.status == "pass"
    assert "5 Jahre Controlling-Erfahrung" in (c.details or "")
    assert "Controlling" in (c.details or "")
    # The EN `details` fallback may carry English scaffold words...
    assert "shares only" in (c.details or "")
    assert c.details_key == "skills-weak-vault-tie"
    # ...but details_params must stay locale-neutral (a bare pair, no English
    # words) — the de/en templated sentences supply the ONLY prose, so a German
    # chip built from details_params never mixes in English (review finding).
    assert c.details_params == {
        "skills": "'5 Jahre Controlling-Erfahrung' ('Controlling')",
        "count": 1,
    }
    assert "shares only" not in c.details_params["skills"]
    # Advisory only — never a structure failure, never counted against passed/failed.
    assert report.failed == 0


def test_skills_weak_vault_tie_silent_on_shared_paren_abbreviation():
    """A translation/synonym pair sharing its canonical abbreviation (#308) is a
    legitimate tie — the advisory must not fire."""
    cv = _CV.model_copy(update={"skills": ["Fertigungsleitsysteme (MES)"]})
    report = _audit_cv_text(
        _full_text(), cv, keywords=[],
        vault_skill_forms=["MES (Manufacturing Execution System)"],
    )
    assert _check_by_id(report, "skills-weak-vault-tie") is None


def test_skills_weak_vault_tie_silent_on_multi_token_containment():
    """A multi-token containment tie ('Team Leadership' ⊂ vault's 'Team Leadership
    and Mentorship') is a legitimate near-dupe tie — the advisory must not fire."""
    cv = _CV.model_copy(update={"skills": ["Team Leadership"]})
    report = _audit_cv_text(
        _full_text(), cv, keywords=[],
        vault_skill_forms=["Team Leadership and Mentorship"],
    )
    assert _check_by_id(report, "skills-weak-vault-tie") is None


def test_skills_weak_vault_tie_silent_on_exact_match():
    """An exact vault match is the strongest possible tie — the advisory must not
    fire even though the pair is also, trivially, single-token containment-free."""
    cv = _CV.model_copy(update={"skills": ["Python"]})
    report = _audit_cv_text(
        _full_text(), cv, keywords=[], vault_skill_forms=["Python"]
    )
    assert _check_by_id(report, "skills-weak-vault-tie") is None


def test_skills_weak_vault_tie_silent_when_a_stronger_tie_exists_elsewhere():
    """One real tie is enough: if the skill ALSO exactly matches a different vault
    form, the coincidental weak tie to another vault form must not surface."""
    cv = _CV.model_copy(update={"skills": ["5 Jahre Controlling-Erfahrung"]})
    report = _audit_cv_text(
        _full_text(), cv, keywords=[],
        vault_skill_forms=["Controlling", "5 Jahre Controlling-Erfahrung"],
    )
    assert _check_by_id(report, "skills-weak-vault-tie") is None


def test_skills_weak_vault_tie_silent_without_vault_skill_forms():
    """Back-compat: omitting `vault_skill_forms` (every pre-#391 caller) must
    reproduce prior behaviour exactly — the advisory never fires."""
    cv = _CV.model_copy(update={"skills": ["5 Jahre Controlling-Erfahrung"]})
    report = _audit_cv_text(_full_text(), cv, keywords=[])
    assert _check_by_id(report, "skills-weak-vault-tie") is None


def test_skills_weak_vault_tie_silent_on_german_compound_suffix():
    """The German-compound suffix shape ('Mitarbeiterführung' ⊃ 'Führung',
    #386/ADR-072) is a legitimate page-dupe tie — the advisory must not fire,
    even though neither side is a multi-token containment/Jaccard/paren-abbr
    match (it is single-token-vs-single-token, the shape guard #1 inside
    ``_weak_single_token_tie`` exists to route away from the near-dupe check)."""
    cv = _CV.model_copy(update={"skills": ["Mitarbeiterführung"]})
    report = _audit_cv_text(
        _full_text(), cv, keywords=[], vault_skill_forms=["Führung"]
    )
    assert _check_by_id(report, "skills-weak-vault-tie") is None


def test_skills_weak_vault_tie_silent_when_no_vault_tie_at_all():
    """A skill with NO vault tie of any kind (a genuine unbacked JD echo) is a
    different problem (`_drop_ungrounded_jd_echo_skills`'s job) — the advisory
    must not conflate 'no tie' with 'weak tie'."""
    cv = _CV.model_copy(update={"skills": ["Quantum Cryptography Research"]})
    report = _audit_cv_text(
        _full_text(), cv, keywords=[], vault_skill_forms=["Controlling"]
    )
    assert _check_by_id(report, "skills-weak-vault-tie") is None


def test_skills_weak_vault_tie_helper_matches_391_ground_truth():
    from applire.services.ats_audit import skills_weak_vault_tie

    result = skills_weak_vault_tie(
        ["5 Jahre Controlling-Erfahrung"], ["Controlling"]
    )
    assert result == [("5 Jahre Controlling-Erfahrung", "Controlling")]


def test_weak_single_token_tie_silent_on_pathological_shared_abbreviation():
    """The one non-structurally-excluded stronger-tie shape (see
    ``_weak_single_token_tie``'s docstring): a bare single-token name that is
    ITSELF a parenthetical still shares its abbreviation with the vault form —
    a genuine, if pathological, stronger tie that must still silence the
    advisory."""
    from applire.services.ats_audit import _weak_single_token_tie, skills_single_token_containment

    skill, vault_form = "(MES)", "MES (Manufacturing Execution System)"
    # Precondition: this pair really does single-token-contain (else the test
    # would trivially pass via the function's first guard, proving nothing).
    assert skills_single_token_containment(skill, vault_form) is True
    assert _weak_single_token_tie(skill, vault_form) is False


@pytest.mark.parametrize("a,b", _SINGLE_TOKEN_CONTAINMENT_PAIRS)
def test_compound_suffix_dupe_structurally_unreachable_given_single_token_containment(a, b):
    """Pins the docstring's unreachability claim on the module's own
    single-token-containment fixture pairs: whenever
    ``skills_single_token_containment`` is True, ``_compound_suffix_dupe``
    can never ALSO be True for the same pair (it requires both sides to be a
    single bare token; single-token containment's non-contained side always
    has >= 2 tokens by construction). If a future edit to either predicate
    breaks this invariant, this test — not just the near-silent advisory
    tests above — goes red."""
    from applire.services.ats_audit import _compound_suffix_dupe, skill_tokens, skills_single_token_containment

    assert skills_single_token_containment(a, b) is True
    assert _compound_suffix_dupe(skill_tokens(a), skill_tokens(b)) is False


# Slash forms on the CONTAINED side ("PP/MM"), on the CONTAINER side only
# ("SAP" vs "SAP PP/MM" — the contained side is plain, no slash), and a
# second contained-side-slash pair in a different domain (CI/CD) — the
# structural cases the predicate actually admits under established
# single-token containment. A slash on the contained side but NOT
# (in some form) on the container side is impossible to construct here: for
# containment to hold, the contained token must appear verbatim as an
# element of the container's own token set, so if it carries a slash the
# container's token set necessarily carries that same slash form too.
_SLASH_SINGLE_TOKEN_CONTAINMENT_PAIRS = [
    ("PP/MM", "SAP PP/MM"),
    ("SAP", "SAP PP/MM"),
    ("CI/CD", "Advanced CI/CD Pipelines"),
]


@pytest.mark.parametrize("a,b", _SLASH_SINGLE_TOKEN_CONTAINMENT_PAIRS)
def test_slash_compound_containment_structurally_unreachable_given_single_token_containment(a, b):
    """Pins the docstring's SECOND unreachability claim (the first is pinned by
    ``test_compound_suffix_dupe_structurally_unreachable_...`` above): the
    slash-compound containment over ``_page_token_set`` — ``skills_page_dupe``'s
    third disjunct — can never independently reveal a tie beyond what
    ``skills_single_token_containment`` already established for the same pair.

    Reconstructs the exact ``slash_only_containment`` expression an earlier,
    more defensive draft of ``_weak_single_token_tie`` computed before it was
    proved dead and simplified away (see that function's docstring): page-scope
    containment holding WITHOUT the plain ``skill_tokens``-level containment
    already holding. The underlying reason is monotonicity, not luck — if
    ``skill_tokens(a) <= skill_tokens(b)``, every slash-token in ``a`` is also a
    literal element of ``b``'s token set, so ``_page_token_set`` adds the exact
    same split parts to BOTH sides and the containment direction can never
    flip or add new information. Fixture pairs put a slash on the contained
    side, the container side only, or both, so ``_page_token_set`` genuinely
    differs from plain ``skill_tokens`` on at least one side of each pair (a
    no-slash pair would trivially pass without exercising the claim at all).
    If a future edit to ``_page_token_set``/the slash disjunct breaks the
    monotonicity property, this test goes red.
    """
    from applire.services.ats_audit import _page_token_set, skill_tokens, skills_single_token_containment

    ta, tb = skill_tokens(a), skill_tokens(b)
    pa, pb = _page_token_set(a), _page_token_set(b)
    # Preconditions: the claim is only interesting when (1) single-token
    # containment is actually established, and (2) the slash expansion
    # genuinely changes at least one side's token set — else this would
    # vacuously pass without exercising _page_token_set at all. Not both
    # sides always: the ("SAP", "SAP PP/MM") pair deliberately has a plain,
    # slash-free contained side (the "slash only on the container" case).
    assert skills_single_token_containment(a, b) is True
    assert pa != ta or pb != tb, "fixture must exercise the slash expansion on at least one side"
    slash_only_containment = bool(pa) and bool(pb) and (pa <= pb or pb <= pa) and not (ta <= tb or tb <= ta)
    assert slash_only_containment is False


def test_audit_cv_threads_page_count_from_pdf():
    """audit_cv must read the real PDF page count and run the page-length check."""
    from io import BytesIO
    from pypdf import PdfReader, PdfWriter
    from applire.services.ats_audit import audit_cv

    def _blank_pdf(n: int) -> bytes:
        writer = PdfWriter()
        for _ in range(n):
            writer.add_blank_page(width=595, height=842)  # A4 points
        buf = BytesIO()
        writer.write(buf)
        return buf.getvalue()

    report = audit_cv(_blank_pdf(5), _CV, keywords=[])
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "fail" and "5" in (c.details or "")

    report_ok = audit_cv(_blank_pdf(2), _CV, keywords=[])
    assert _check_by_id(report_ok, "page-length").status == "pass"

_CV = TailoredCVData.model_validate({
    "contact": {"name": "Anna Bauer", "email": "anna@example.com", "phone": "+49 151 1234567", "location": "Berlin"},
    "summary": "Backend engineer with cloud focus.",
    "work_history": [
        {"company": "Cloudwerk GmbH", "role": "Senior Backend Engineer", "start_date": "2021-04", "end_date": None,
         "bullets": ["Built FastAPI services", "Led Kubernetes migration"]},
        {"company": "DataHaus AG", "role": "Software Engineer", "start_date": "2017-09", "end_date": "2021-03",
         "bullets": ["Maintained ETL pipelines"]},
    ],
    "skills": ["Python", "FastAPI", "Kubernetes"],
    "education": [{"institution": "TU Berlin", "degree": "M.Sc.", "field": "Informatik", "start_date": "2014-10", "end_date": "2017-08"}],
    "languages": [{"language": "Deutsch", "level": "C2"}],
})

def _full_text() -> str:
    """Extracted text of a document that faithfully carries ALL of `_CV`.

    The summary and the three bullets are load-bearing, not decoration: until
    the ADR-039 amendment (2026-08-31, #634) the audit read no free text on the
    CV side, so this fixture omitted them and still called itself "faithful" —
    it was faithful only to what the instrument happened to examine. Removing
    any line below makes `content-*` fail, which is the point.
    """
    return (
        "Anna Bauer anna@example.com +49 151 1234567 Berlin\n"
        "Backend engineer with cloud focus.\n"
        "Senior Backend Engineer Cloudwerk GmbH 04/2021 - heute\n"
        "Built FastAPI services\n"
        "Led Kubernetes migration\n"
        "Software Engineer DataHaus AG 09/2017 - 03/2021\n"
        "Maintained ETL pipelines\n"
        "Python FastAPI Kubernetes\n"
        "TU Berlin M.Sc. Informatik 2014 - 2017\n"
    )

def test_all_checks_pass_on_faithful_text():
    report = _audit_cv_text(_full_text(), _CV, keywords=["Python", "GraphQL"])
    assert report.failed == 0
    assert report.document == "cv"
    assert report.keywords.present == ["Python"]
    assert report.keywords.missing == ["GraphQL"]

def test_missing_contact_and_entry_fail():
    text = "Senior Backend Engineer Cloudwerk GmbH 2021"
    report = _audit_cv_text(text, _CV, keywords=[])
    failed_ids = {c.id for c in report.checks if c.status == "fail"}
    assert "contact-name" in failed_ids
    assert "work-1" in failed_ids          # DataHaus entry absent

def test_reading_order_fails_when_entries_swapped():
    text = (
        "Anna Bauer anna@example.com +49 151 1234567\n"
        "Software Engineer DataHaus AG 2017 2021\n"
        "Senior Backend Engineer Cloudwerk GmbH 2021\n"
        "Python FastAPI Kubernetes TU Berlin M.Sc. Informatik 2014 2017\n"
    )
    report = _audit_cv_text(text, _CV, keywords=[])
    assert any(c.id == "reading-order" and c.status == "fail" for c in report.checks)


def test_reading_order_failure_detail_states_comparison_without_guessing_cause():
    """#118 — the fail detail must say WHAT was compared (CV data order vs
    extracted-text order), not presume a cause like column interleaving: UAT
    hit this failure from a data-ordering bug, not a layout problem."""
    text = (
        "Anna Bauer anna@example.com +49 151 1234567\n"
        "Software Engineer DataHaus AG 2017 2021\n"
        "Senior Backend Engineer Cloudwerk GmbH 2021\n"
        "Python FastAPI Kubernetes TU Berlin M.Sc. Informatik 2014 2017\n"
    )
    report = _audit_cv_text(text, _CV, keywords=[])
    detail = next(c.details for c in report.checks if c.id == "reading-order")
    assert "column interleaving" not in detail
    assert "extracted text" in detail and "CV data" in detail

def test_year_only_date_matching():
    report = _audit_cv_text(_full_text().replace("04/2021", "April 2021"), _CV, keywords=[])
    assert report.failed == 0

def test_checks_skipped_for_absent_data():
    cv = _CV.model_copy(update={"contact": _CV.contact.model_copy(update={"email": None, "phone": None})})
    text = "Anna Bauer Senior Backend Engineer Cloudwerk GmbH 2021 Software Engineer DataHaus AG 2017 Python FastAPI Kubernetes TU Berlin M.Sc. Informatik 2014"
    report = _audit_cv_text(text, cv, keywords=[])
    ids = {c.id for c in report.checks}
    assert "contact-email" not in ids and "contact-phone" not in ids

def test_letter_audit():
    letter = {
        "header": {"name": "Anna Bauer", "email": "anna@example.com", "phone": None, "address": "Berlin"},
        "recipient": {"company": "Cloudwerk GmbH", "name": "Herr Schmidt", "title": None, "address": None, "date": "11. Juni 2026"},
        "body": {"paragraphs": ["Sehr geehrter Herr Schmidt,", "ich bewerbe mich…", "Mit freundlichen Grüßen"]},
        "signature": {"name": "Anna Bauer"},
    }
    text = "Anna Bauer anna@example.com Berlin Cloudwerk GmbH Herr Schmidt Sehr geehrter Herr Schmidt, ich bewerbe mich… Mit freundlichen Grüßen"
    report = _audit_letter_text(text, letter, keywords=["Cloud"])
    assert report.document == "cover_letter"
    assert report.failed == 0


# ---------------------------------------------------------------------------
# #399 — pypdf inserts a spurious space inside a kerned character pair (e.g.
# adjacent identical glyphs "ff"/"11"), splitting a token that IS present in
# the rendered PDF's header. Ground truth (charter run 12, controlling_emma_de,
# 2026-08-01, classic_german/lebenslauf_letter template, reproduced via the
# real Playwright round-trip): pypdf's extract_text() returned
# "...katrin.hof fmann@example.com" for a header that unambiguously contains
# "katrin.hoffmann@example.com" — poppler's pdftotext extracts the same PDF
# intact. The issue's own hypothesis (header block not reaching the text
# layer, or an obfuscation/encoding step) does NOT hold: the header text does
# reach the text layer, mangled by a pypdf extraction artifact, not dropped or
# encoded away.
# ---------------------------------------------------------------------------

def test_contact_email_survives_pypdf_kerning_space_artifact():
    """The exact shape #399 reproduced: the header DOES contain the email, but
    pypdf's extracted text has a spurious space inside a kerned run ("hof
    fmann"). Must still PASS — this is a text-extraction artifact, not an
    absent address."""
    letter = {
        "header": {
            "name": "Katrin Hoffmann",
            "email": "katrin.hoffmann@example.com",
            "phone": "+49 711 0000000",
            "address": "Musterstraße 1, 70173 Stuttgart",
        },
        "recipient": {"company": "Beispiel AG", "name": "Herr Arnold", "title": None,
                       "address": None, "date": "1. August 2026"},
        "body": {"paragraphs": ["Absatz eins.", "Absatz zwei."]},
        "signature": {"name": "Katrin Hoffmann"},
    }
    # Verbatim shape of the pypdf-mangled text this run produced (#399 repro):
    # the phantom space lands inside "hoffmann", between the two adjacent "f"s.
    text = (
        "KATRIN HOFFMANN\nHerr Arnold\nBeispiel AG\n1. August 2026\nAbsatz eins.\n"
        "Absatz zwei.\nKatrin Hoffmann\nMusterstraße 1, 70173 Stuttgart · "
        "+49 71 1 0000000 · katrin.hof fmann@example.com"
    )
    report = _audit_letter_text(text, letter, keywords=[])
    email_check = _check_by_id(report, "contact-email")
    assert email_check is not None and email_check.status == "pass", (
        f"contact-email false negative on a pypdf kerning-space artifact: {email_check}"
    )


# ---------------------------------------------------------------------------
# E042/US240 (ADR-051 §6): cover-letter page-length DETECTION check — same check
# id ("page-length") as the CV band, but no target/condense (deferred this
# flavour): 1 page passes, 2+ fails naming the region's letter norm.
# ---------------------------------------------------------------------------

_LETTER = {
    "header": {"name": "Anna Bauer", "email": "anna@example.com", "phone": None, "address": "Berlin"},
    "recipient": {"company": "Cloudwerk GmbH", "name": "Herr Schmidt", "title": None, "address": None, "date": None},
    "body": {"paragraphs": ["Sehr geehrter Herr Schmidt,"]},
    "signature": {"name": "Anna Bauer"},
}


def test_letter_page_length_check_absent_without_page_count():
    """Callers that don't supply a page count get no page-length check (mirrors
    the CV-side back-compat behaviour)."""
    report = _audit_letter_text("Anna Bauer", _LETTER, keywords=[])
    assert _check_by_id(report, "page-length") is None


def test_letter_page_length_one_page_passes_no_details():
    report = _audit_letter_text("Anna Bauer", _LETTER, keywords=[], page_count=1)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "pass" and c.details is None


def test_letter_page_length_two_pages_fails_naming_the_norm():
    report = _audit_letter_text("Anna Bauer", _LETTER, keywords=[], page_count=2)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "fail"
    assert c.details and "2 pages" in c.details and "DACH" in c.details and "1 page" in c.details
    assert c.details_key == "page-length-letter"
    assert c.details_params == {"pages": 2, "region": "DACH", "letterPages": 1}


def test_audit_cover_letter_threads_page_count_from_pdf():
    """audit_cover_letter must read the real PDF page count (via
    extract_text_and_pages) and run the page-length check."""
    from io import BytesIO
    from pypdf import PdfWriter
    from applire.services.ats_audit import audit_cover_letter

    def _blank_pdf(n: int) -> bytes:
        writer = PdfWriter()
        for _ in range(n):
            writer.add_blank_page(width=595, height=842)  # A4 points
        buf = BytesIO()
        writer.write(buf)
        return buf.getvalue()

    report = audit_cover_letter(_blank_pdf(2), _LETTER, keywords=[])
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "fail" and "2" in (c.details or "")

    report_ok = audit_cover_letter(_blank_pdf(1), _LETTER, keywords=[])
    assert _check_by_id(report_ok, "page-length").status == "pass"


# ---------------------------------------------------------------------------
# Fix 1: empty-field guards
# ---------------------------------------------------------------------------

def test_empty_fields_do_not_false_pass():
    """Fix 1: empty company/role should not silently pass; real role not in text must FAIL."""
    from applire.schemas.cv import TailoredCVData

    # Work entry with empty company but real role "Engineer" that is NOT in the text
    cv = TailoredCVData.model_validate({
        "contact": {"name": "Test User", "email": None, "phone": None, "location": None},
        "summary": "",
        "work_history": [
            {"company": "", "role": "Engineer", "start_date": None, "end_date": None, "bullets": []},
        ],
        "skills": [],
        "education": [],
        "languages": [],
    })
    # Text does NOT contain "Engineer"
    text = "Test User some unrelated text"
    report = _audit_cv_text(text, cv, keywords=[])
    failed_ids = {c.id for c in report.checks if c.status == "fail"}
    assert "work-0" in failed_ids, "work entry with real role not in text must FAIL, not silently pass"

    # Work entry with BOTH company="" and role="" → no check emitted at all
    cv_both_empty = TailoredCVData.model_validate({
        "contact": {"name": "Test User", "email": None, "phone": None, "location": None},
        "summary": "",
        "work_history": [
            {"company": "", "role": "", "start_date": None, "end_date": None, "bullets": []},
        ],
        "skills": [],
        "education": [],
        "languages": [],
    })
    report2 = _audit_cv_text("Test User", cv_both_empty, keywords=[])
    ids = {c.id for c in report2.checks}
    assert "work-0" not in ids, "work entry with both company and role empty must emit no check"


def test_empty_keyword_not_counted_present():
    """Fix 1: empty string keyword must not appear in present or missing."""
    report = _audit_cv_text(_full_text(), _CV, keywords=[""])
    assert report.keywords.present == [], "empty keyword must not appear as present"
    assert report.keywords.missing == [], "empty keyword must not appear as missing"


# ---------------------------------------------------------------------------
# Fix 2: Unicode robustness
# ---------------------------------------------------------------------------

def test_unicode_extraction_variants():
    """Fix 2: decomposed umlauts, soft hyphens, and ligatures must all match."""
    import unicodedata
    from applire.schemas.cv import TailoredCVData

    # Decomposed "Müller": M + u + combining diaeresis + ller
    decomposed_mueller = "Müller"
    assert decomposed_mueller != "Müller"  # confirm they differ at the string level

    cv_mueller = TailoredCVData.model_validate({
        "contact": {"name": "Jörg Müller", "email": None, "phone": None, "location": None},
        "summary": "",
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    })
    # Text contains decomposed form of the name
    text_decomposed = f"Jörg Müller"
    report = _audit_cv_text(text_decomposed, cv_mueller, keywords=[])
    assert all(c.status == "pass" for c in report.checks), \
        f"decomposed umlaut in text should still match; checks: {report.checks}"

    # Soft hyphen in text: "Pro­fil" (U+00AD between o and f)
    cv_profil = TailoredCVData.model_validate({
        "contact": {"name": "Profil Expert", "email": None, "phone": None, "location": None},
        "summary": "",
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    })
    text_softhyphen = "Pro­fil Expert"
    report2 = _audit_cv_text(text_softhyphen, cv_profil, keywords=[])
    assert all(c.status == "pass" for c in report2.checks), \
        f"soft hyphen in text should be stripped before matching; checks: {report2.checks}"

    # Ligature fi (U+FB01): "Proﬁ" (P-r-o-U+FB01) in text, needle is "Profi"
    cv_profi = TailoredCVData.model_validate({
        "contact": {"name": "Profi Engineer", "email": None, "phone": None, "location": None},
        "summary": "",
        "work_history": [],
        "skills": [],
        "education": [],
        "languages": [],
    })
    text_ligature = "Proﬁ Engineer"  # U+FB01 = ﬁ ligature → NFKC → "fi"
    report3 = _audit_cv_text(text_ligature, cv_profi, keywords=[])
    assert all(c.status == "pass" for c in report3.checks), \
        f"fi-ligature in text should expand to 'fi' before matching; checks: {report3.checks}"


# ---------------------------------------------------------------------------
# Fix 3: keyword de-duplication
# ---------------------------------------------------------------------------

def test_duplicate_keywords_deduplicated():
    """Fix 3: duplicate keywords (case-insensitive) should appear only once in present."""
    report = _audit_cv_text(_full_text(), _CV, keywords=["Python", "python", "Python"])
    assert report.keywords.present == ["Python"], \
        f"duplicates must be collapsed to one entry; got {report.keywords.present}"


# ---------------------------------------------------------------------------
# E037 US203: missing keywords split into claimable vs honest-gap
# ---------------------------------------------------------------------------

# A ledger where "Python" is held (claimable), "GraphQL" is an honest gap.
_LEDGER = [
    {"concept": "Python", "surface_forms": ["Python"], "claimable": True,
     "status": "direct", "sources": ["required"], "fit_weight": 1.0, "evidence": "5y"},
    {"concept": "GraphQL", "surface_forms": ["GraphQL"], "claimable": False,
     "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": ""},
]


def test_missing_keywords_split_into_claimable_and_honest_gap():
    """US203: a missing keyword the candidate HAS per the ledger (claimable) is a
    surfacing miss; a missing keyword they don't have is an honest gap. Present
    keywords never appear in either bucket."""
    # Text contains neither Python nor GraphQL → both missing, but bucketed differently.
    text = "Anna Bauer some unrelated prose with no job keywords"
    report = _audit_cv_text(text, _CV, keywords=["Python", "GraphQL"], ledger=_LEDGER)
    assert set(report.keywords.missing) == {"Python", "GraphQL"}        # back-compat list intact
    assert report.keywords.missing_claimable == ["Python"]              # held but absent → fixable
    assert report.keywords.missing_honest_gap == ["GraphQL"]            # not in profile → honest


def test_present_keyword_not_in_either_missing_bucket():
    report = _audit_cv_text(_full_text(), _CV, keywords=["Python", "GraphQL"], ledger=_LEDGER)
    assert report.keywords.present == ["Python"]
    assert "Python" not in report.keywords.missing_claimable
    assert report.keywords.missing_honest_gap == ["GraphQL"]


def test_denied_gap_keyword_aliased_by_claimable_entry_is_honest_gap():
    """F4 (blind PQ 2026-07-02, trust-critical): the gap LLM echoed 'Azure' as a
    surface form of the claimable compound requirement 'Cloud environment
    qualification (AWS, Azure)' while classifying 'Azure' itself as an honest gap
    in the SAME ledger. The audit then bucketed the missing keyword 'Azure' as
    missing_claimable → the panel read 'Supported by your profile' although the
    user had denied Azure experience. Through the REAL builder, 'Azure' must land
    in missing_honest_gap."""
    from applire.services.keyword_ledger import build_keyword_ledger

    ledger = build_keyword_ledger(
        classifications=[
            {
                "concept": "Cloud environment qualification (AWS, Azure)",
                "status": "partial",
                "surface_forms": ["Cloud environment qualification", "AWS", "Azure"],
                "evidence": "Qualified first GxP cloud environment (AWS). Azure not explicitly mentioned.",
            },
            {"concept": "Azure", "status": "gap", "surface_forms": ["Azure"], "evidence": ""},
        ],
        required_skills=["Cloud environment qualification (AWS, Azure)"],
        nice_to_have_skills=[],
        keywords=["AWS", "Azure"],
    )
    # CV text truthfully surfaces AWS but never claims Azure.
    text = "Anna Bauer qualified the company's first GxP cloud environment on AWS"
    report = _audit_cv_text(text, _CV, keywords=["AWS", "Azure"], ledger=ledger)
    assert "Azure" in report.keywords.missing
    assert "Azure" not in report.keywords.missing_claimable, (
        "a concept the ledger itself classifies 'gap' must never be presented as "
        "'supported by your profile'"
    )
    assert "Azure" in report.keywords.missing_honest_gap


def test_post_interview_upgrade_reclassifies_gap_as_claimable():
    """#188: after an interview confirms 'CI/CD', its ledger entry is upgraded to
    claimable. The ATS audit must then treat a prose-only 'CI/CD' as a SURFACING
    miss (missing_claimable — "you support this, surface it"), NOT an honest gap,
    and a literally-present 'CI/CD' must not be flagged present_unsupported."""
    from applire.services.keyword_ledger import upgrade_ledger_for_concepts

    ledger = [
        {"concept": "CI/CD", "surface_forms": ["CI/CD"], "sources": ["required"],
         "fit_weight": 1.0, "status": "gap", "evidence": "", "claimable": False},
    ]
    upgraded, changed = upgrade_ledger_for_concepts(
        ledger, ["CI/CD"], "Built CI/CD pipelines end-to-end with GitHub Actions."
    )
    assert changed is True

    # (a) prose-only mention (no literal 'CI/CD') → missing, and now CLAIMABLE.
    prose_text = "Anna Bauer automated the build and deployment pipeline"
    report = _audit_cv_text(prose_text, _CV, keywords=["CI/CD"], ledger=upgraded)
    assert report.keywords.missing == ["CI/CD"]
    assert report.keywords.missing_claimable == ["CI/CD"]
    assert report.keywords.missing_honest_gap == []

    # (b) literal present → counted present, and NOT present_unsupported (it is now
    # a supported claim per the upgraded ledger).
    present_text = "Anna Bauer owns the CI/CD pipelines end to end"
    report2 = _audit_cv_text(present_text, _CV, keywords=["CI/CD"], ledger=upgraded)
    assert report2.keywords.present == ["CI/CD"]
    assert report2.keywords.present_unsupported == []


def test_pre_upgrade_gap_is_honest_gap_not_claimable():
    """Guard the contrast: BEFORE the upgrade the same prose-only 'CI/CD' is an
    honest gap (not something to surface) — the upgrade is what flips it."""
    ledger = [
        {"concept": "CI/CD", "surface_forms": ["CI/CD"], "sources": ["required"],
         "fit_weight": 1.0, "status": "gap", "evidence": "", "claimable": False},
    ]
    report = _audit_cv_text(
        "Anna Bauer automated the build and deployment pipeline",
        _CV, keywords=["CI/CD"], ledger=ledger,
    )
    assert report.keywords.missing == ["CI/CD"]
    assert report.keywords.missing_claimable == []
    assert report.keywords.missing_honest_gap == ["CI/CD"]


def test_missing_keyword_unknown_to_ledger_is_honest_gap():
    """A missing keyword with no claimable ledger entry defaults to honest-gap —
    never silently claimable (mirrors the ledger's gap-default rule)."""
    report = _audit_cv_text("Anna Bauer", _CV, keywords=["Rust"], ledger=_LEDGER)
    assert report.keywords.missing == ["Rust"]
    assert report.keywords.missing_claimable == []
    assert report.keywords.missing_honest_gap == ["Rust"]


def test_no_ledger_all_missing_default_to_honest_gap():
    """Legacy pre-E037 path: no ledger → claimable bucket empty, all missing are honest-gap
    (back-compat — the panel still has something to show)."""
    report = _audit_cv_text("Anna Bauer", _CV, keywords=["Python", "GraphQL"])
    assert set(report.keywords.missing) == {"Python", "GraphQL"}
    assert report.keywords.missing_claimable == []
    assert set(report.keywords.missing_honest_gap) == {"Python", "GraphQL"}


def test_letter_missing_keywords_split_by_ledger():
    letter = {
        "header": {"name": "Anna Bauer", "email": None, "phone": None, "address": "Berlin"},
        "recipient": {"company": None, "name": None, "title": None, "address": None, "date": None},
        "body": {"paragraphs": ["Sehr geehrte Damen und Herren,"]},
        "signature": {"name": "Anna Bauer"},
    }
    text = "Anna Bauer Berlin Sehr geehrte Damen und Herren,"
    report = _audit_letter_text(text, letter, keywords=["Python", "GraphQL"], ledger=_LEDGER)
    assert report.keywords.missing_claimable == ["Python"]
    assert report.keywords.missing_honest_gap == ["GraphQL"]


def test_empty_letter_paragraph_skipped():
    letter = {
        "header": {"name": "Anna Bauer", "email": None, "phone": None, "address": "Berlin"},
        "recipient": {"company": None, "name": None, "title": None, "address": None, "date": None},
        "body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "", "   "]},
        "signature": {"name": "Anna Bauer"},
    }
    text = "Anna Bauer Berlin Sehr geehrte Damen und Herren,"
    report = _audit_letter_text(text, letter, keywords=[])
    ids = {c.id for c in report.checks}
    assert "body-0" in ids
    assert "body-1" not in ids and "body-2" not in ids
    assert report.failed == 0


# ---------------------------------------------------------------------------
# US212 (#122, ADR-048 amended 2026-07-04): unified presence predicate —
# surface-form union + morphological fold. Regression fixtures lifted from the
# Chocolate UAT CV that surfaced the bug.
# ---------------------------------------------------------------------------

_LEDGER_122 = [
    {"concept": "code review practices", "surface_forms": ["Code reviews"], "claimable": True,
     "status": "direct", "sources": ["keyword"], "fit_weight": 0.0,
     "evidence": "enforced code review standards at NordPharm"},
    {"concept": "education technology", "surface_forms": ["EdTech"], "claimable": True,
     "status": "partial", "sources": ["keyword"], "fit_weight": 0.0,
     "evidence": "educational games development at Provadis"},
    {"concept": "container orchestration", "surface_forms": ["container orchestration", "Kubernetes", "K8s"],
     "claimable": True, "status": "direct", "sources": ["required"], "fit_weight": 1.0,
     "evidence": "led Kubernetes migration"},
    {"concept": "SaaS", "surface_forms": ["SaaS"], "claimable": False,
     "status": "gap", "sources": ["keyword"], "fit_weight": 0.0, "evidence": ""},
]


def test_plural_keyword_matches_singular_in_text():
    """#122 'Code reviews': the literal plural is absent but 'code review standards'
    is in the text — the morphological fold must count the keyword present."""
    text = "Anna Bauer enforcing code review standards across teams"
    report = _audit_cv_text(text, _CV, keywords=["Code reviews"], ledger=_LEDGER_122)
    assert report.keywords.present == ["Code reviews"]
    assert report.keywords.missing_claimable == []


def test_singular_keyword_matches_plural_in_text():
    text = "Anna Bauer ran weekly code reviews for the platform team"
    report = _audit_cv_text(text, _CV, keywords=["Code review"], ledger=_LEDGER_122)
    assert report.keywords.present == ["Code review"]


def test_surface_form_alias_counts_keyword_present():
    """Presence = union over the owning ledger entry's surface forms, not just the
    keyword literal (panel previously literal-only; gap hints already union)."""
    text = "Anna Bauer led the Kubernetes migration"
    report = _audit_cv_text(text, _CV, keywords=["container orchestration"], ledger=_LEDGER_122)
    assert report.keywords.present == ["container orchestration"]


def test_hyphen_variant_matches():
    text = "Anna Bauer wrote the Code-Review guidelines"
    report = _audit_cv_text(text, _CV, keywords=["code review"], ledger=None)
    assert report.keywords.present == ["code review"]


def test_short_token_not_plural_folded():
    """Guard: K8s / SaaS style tokens must NOT be stripped to a degenerate stem
    ('k8', 'saa') that substring-matches unrelated text."""
    report_k8 = _audit_cv_text("Anna Bauer manages a k8 fleet", _CV, keywords=["K8s"], ledger=None)
    assert report_k8.keywords.missing == ["K8s"]
    report_saa = _audit_cv_text("Anna Bauer worked in Saarland", _CV, keywords=["SaaS"], ledger=_LEDGER_122)
    assert report_saa.keywords.missing == ["SaaS"]
    assert report_saa.keywords.missing_honest_gap == ["SaaS"]


def test_symbol_keywords_unaffected_by_fold():
    text = "Anna Bauer builds C# services with CI/CD pipelines"
    report = _audit_cv_text(text, _CV, keywords=["C#", "CI/CD"], ledger=None)
    assert set(report.keywords.present) == {"C#", "CI/CD"}


def test_edtech_true_miss_stays_missing_claimable():
    """#122 'EdTech': evidence-adjacent prose does NOT satisfy the literal check —
    the keyword stays missing and, per the ledger, claimable."""
    text = "Anna Bauer developed educational games using Flash"
    report = _audit_cv_text(text, _CV, keywords=["EdTech"], ledger=_LEDGER_122)
    assert report.keywords.missing == ["EdTech"]
    assert report.keywords.missing_claimable == ["EdTech"]


def test_honest_gap_surface_form_present_flags_unsupported():
    """Fourth quadrant (#117) with union matching: an honest-gap term present in the
    document via any of its surface forms is a truthfulness warning."""
    ledger = [{"concept": "SaaS", "surface_forms": ["SaaS", "software as a service"],
               "claimable": False, "status": "gap", "sources": ["keyword"],
               "fit_weight": 0.0, "evidence": ""}]
    text = "Anna Bauer sells software as a service to enterprises"
    report = _audit_cv_text(text, _CV, keywords=["SaaS"], ledger=ledger)
    assert report.keywords.present == ["SaaS"]
    assert report.keywords.present_unsupported == ["SaaS"]


def test_gap_stance_not_widened_by_foreign_entry():
    """F4 invariant holds under union matching: a keyword owned by an honest-gap
    entry must not be counted present via a DIFFERENT claimable entry's forms.
    Built through the REAL builder (gap-stance enforcement strips 'Azure' from
    the claimable entry) — presence for 'Azure' may only consider the honest-gap
    entry's own forms, even though the claimable form IS in the text."""
    from applire.services.keyword_ledger import build_keyword_ledger

    ledger = build_keyword_ledger(
        classifications=[
            {"concept": "Cloud environment qualification (AWS, Azure)", "status": "partial",
             "surface_forms": ["Cloud environment qualification", "Azure"],
             "evidence": "Qualified GxP cloud environment."},
            {"concept": "Azure", "status": "gap", "surface_forms": ["Azure"], "evidence": ""},
        ],
        required_skills=["Cloud environment qualification (AWS, Azure)"],
        nice_to_have_skills=[],
        keywords=["Azure"],
    )
    text = "Anna Bauer performed cloud environment qualification work"
    report = _audit_cv_text(text, _CV, keywords=["Azure"], ledger=ledger)
    assert report.keywords.missing == ["Azure"]
    assert report.keywords.missing_honest_gap == ["Azure"]


# ---------------------------------------------------------------------------
# #249 run-4 — ONE shared presence predicate: the ATS panel's
# present_unsupported and the Truthfulness Oracle's "grounded" (deterministic
# literal tie, services/oracle/matchers/grounding.py:ground_skill_claim) must
# never contradict each other for the same skill string. Oracle's
# ground_skill_claim checks `surface_present` against the vault's own literal
# text — THE same instrument used here. Defense-in-depth over the #249
# denial-narrowing fix (services/keyword_ledger.py): even a stale/mis-
# classified ledger row can no longer make the ATS panel say "remove it or
# add evidence" about a skill the vault literally attests.
# ---------------------------------------------------------------------------

def test_present_unsupported_never_contradicts_literal_vault_grounding():
    """Reproduces the run-4 fixture shape: a ledger row wrongly marks 'RAG'
    unclaimable/gap (as the pre-fix denial-containment bug did), but the
    vault's own literal text — the SAME text Oracle's ground_skill_claim
    checks — carries 'Retrieval-Augmented Generation (RAG)'. present_unsupported
    must honour that literal tie over the ledger's classification."""
    ledger = [
        {"concept": "RAG", "surface_forms": ["RAG"], "claimable": False,
         "status": "gap", "sources": ["required"], "fit_weight": 1.0,
         "evidence": "Candidate explicitly stated a limit here (interview)."},
    ]
    text = "Anna Bauer built a production RAG system end to end"
    vault_text_norm = _norm(
        "work_experience[0].technologies: Python, LangChain, "
        "Retrieval-Augmented Generation (RAG)"
    )
    report = _audit_cv_text(
        text, _CV, keywords=["RAG"], ledger=ledger, vault_text_norm=vault_text_norm,
    )
    assert report.keywords.present == ["RAG"]
    assert "RAG" not in report.keywords.present_unsupported
    # The invariant made explicit: whatever the ledger says, a keyword that
    # clears THE shared presence predicate against the vault's own literal
    # text can never be flagged present_unsupported.
    assert surface_present("RAG", vault_text_norm)


def test_present_unsupported_without_vault_text_is_unchanged_back_compat():
    """Legacy callers that never pass vault_text_norm keep today's exact
    behaviour — the guard is additive, never a silent regression."""
    ledger = [
        {"concept": "RAG", "surface_forms": ["RAG"], "claimable": False,
         "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": ""},
    ]
    text = "Anna Bauer built a production RAG system end to end"
    report = _audit_cv_text(text, _CV, keywords=["RAG"], ledger=ledger)
    assert report.keywords.present_unsupported == ["RAG"]


def test_present_unsupported_still_fires_when_no_literal_vault_tie_exists():
    """Contrast: without any literal vault backing, a genuinely unsupported
    present keyword still gets flagged — the guard narrows, never disables,
    the fourth-quadrant truthfulness warning."""
    ledger = [
        {"concept": "RAG", "surface_forms": ["RAG"], "claimable": False,
         "status": "gap", "sources": ["required"], "fit_weight": 1.0, "evidence": ""},
    ]
    text = "Anna Bauer built a production RAG system end to end"
    vault_text_norm = _norm("Python FastAPI Kubernetes")
    report = _audit_cv_text(
        text, _CV, keywords=["RAG"], ledger=ledger, vault_text_norm=vault_text_norm,
    )
    assert report.keywords.present_unsupported == ["RAG"]


# ---------------------------------------------------------------------------
# Friction finding (#234-adjacent) — verb-form false negatives in surface_present.
# Live-reproduced: "Mentoring" and "Performance optimization" were reported
# missing_claimable although the CV verbatim said "Mentored 2 junior engineers"
# and "...improving query performance by 40%". Bounded fix: a conservative
# same-stem verb-form fold (-ing/-ed/-es/-s, stem length >= 4, both directions)
# added to surface_present's token-level fallback ONLY. Paraphrase-level
# matching ("performance optimization" vs "improving ... performance") stays
# explicitly out of scope.
# ---------------------------------------------------------------------------

def test_surface_present_folds_ing_to_ed_verb_form():
    """'Mentoring' (the ledger/keyword form) must be found in text that only
    says 'Mentored' — the live-reproduced false negative."""
    text_norm = _norm("Mentored 2 junior engineers on system design")
    assert surface_present("Mentoring", text_norm) is True


def test_surface_present_folds_ed_to_ing_verb_form_reverse_direction():
    """The fold must work in the other direction too: keyword 'Mentored' found
    in text that only says 'Mentoring'."""
    text_norm = _norm("Responsible for mentoring junior engineers")
    assert surface_present("Mentored", text_norm) is True


def test_surface_present_paraphrase_reorder_stays_out_of_scope():
    """'test automation' (keyword) vs 'automated tests' (document) is a
    multi-token paraphrase (word-order AND part-of-speech shift on both
    words) — the SAME category as 'performance optimization' vs 'improving
    query performance', which the fix explicitly does not attempt. Pinned:
    this must NOT match."""
    text_norm = _norm("Built a suite of automated tests for the pipeline")
    assert surface_present("test automation", text_norm) is False


def test_surface_present_stems_under_four_chars_never_fold():
    """'AI' must never fold — far too short to safely stem, and folding it
    would substring-match unrelated words. (Text deliberately avoids any
    'ai' substring so this isolates the NEW fold from the pre-existing
    plain-substring check.)"""
    text_norm = _norm("The team runs quarterly audits")
    assert surface_present("AI", text_norm) is False


def test_surface_present_unrelated_words_do_not_false_positive():
    """'mentor' and 'mention' must NOT match: 'mention' strips no suffix (its
    -ing/-ed/-es/-s pass doesn't apply) and stays 'mention'; 'mentor' likewise
    stays 'mentor' -- different stems, no fold."""
    text_norm = _norm("Anna Bauer did not mention this responsibility")
    assert surface_present("mentor", text_norm) is False


def test_surface_present_trailing_s_fold_unchanged():
    """Pre-existing US212 trailing-s plural fold must be untouched by the new
    verb-form fallback."""
    text_norm = _norm("Anna Bauer ran weekly code reviews for the platform team")
    assert surface_present("Code review", text_norm) is True


def test_missing_claimable_no_longer_false_negatives_on_mentored():
    """Report-level repro of the live finding: 'Mentoring' must land in
    ``present``, not ``missing_claimable``, when the CV verbatim says
    'Mentored'."""
    text = "Anna Bauer mentored 2 junior engineers and improved query performance by 40 percent"
    report = _audit_cv_text(text, _CV, keywords=["Mentoring"], ledger=_LEDGER_122)
    assert report.keywords.present == ["Mentoring"]
    assert report.keywords.missing_claimable == []


def test_dedupe_predicates_stay_strict_after_verb_fold():
    """Guard against ripple: skill_tokens/skills_near_dupe/_fold_variants (the
    ADR-046 dedupe instruments) must NOT gain the verb-form fold -- they stay
    exactly as strict as before. 'Mentor' and 'Mentoring' are genuinely
    different skill labels and must still NOT auto-merge."""
    from applire.services.ats_audit import _fold_variants, skills_near_dupe

    assert skills_near_dupe("Mentor", "Mentoring") is False
    # _fold_variants (phrase-level substring fold) must still be the plain
    # trailing-s-only fold, UNCHANGED by the new verb-form fallback: no -ing/
    # -ed entries added, only the pre-existing guarded singular/plural pair.
    assert _fold_variants("mentoring") == ["mentoring", "mentorings"]
    assert _fold_variants("reviews") == ["reviews", "review"]


# ---------------------------------------------------------------------------
# E048/US266 (#249 option b): the frontend third-state join (Oracle "unbacked"
# skill claim vs Keyword Ledger adjacency-claimable concept) needs the FULL
# claimable concept list on the ATS report -- not just missing_claimable
# (which only covers concepts missing from the document text). A concept the
# generator DID surface (present in the document) needs to reach the frontend
# too, since that is exactly the case that produced the #249 contradiction.
# ---------------------------------------------------------------------------

def test_claimable_concepts_exposes_full_claimable_list_regardless_of_presence():
    """`claimable_concepts` carries every claimable ledger entry's surface forms
    (concept name included), whether the term is present or missing in the
    document -- unlike `missing_claimable`, which only covers the absent
    subset. Honest-gap (non-claimable) concepts never appear."""
    # Present case: "Python" literally appears in the CV text.
    report_present = _audit_cv_text(_full_text(), _CV, keywords=["Python", "GraphQL"], ledger=_LEDGER)
    assert "Python" in report_present.keywords.claimable_concepts
    assert "GraphQL" not in report_present.keywords.claimable_concepts

    # Missing case: neither term appears in the text -- claimable_concepts is
    # unaffected by presence (only missing_claimable changes).
    text = "Anna Bauer some unrelated prose with no job keywords"
    report_missing = _audit_cv_text(text, _CV, keywords=["Python", "GraphQL"], ledger=_LEDGER)
    assert "Python" in report_missing.keywords.claimable_concepts
    assert "GraphQL" not in report_missing.keywords.claimable_concepts


def test_claimable_concepts_includes_adjacency_surface_forms():
    """A ledger entry whose CONCEPT differs from its literal surface form
    (e.g. an adjacency-classified 'Strategic Planning' concept the LLM only
    supported via a related 'Digital Strategy' profile skill) must expose
    BOTH the concept name and its surface forms, so the frontend can join
    against whichever text the document actually renders."""
    from applire.services.keyword_ledger import build_keyword_ledger

    ledger = build_keyword_ledger(
        classifications=[
            {
                "concept": "Strategic Planning",
                "status": "partial",
                "surface_forms": ["Strategic Planning"],
                "evidence": "Adjacent to profile skill 'Digital Strategy'.",
            },
        ],
        required_skills=["Strategic Planning"],
        nice_to_have_skills=[],
        keywords=["Strategic Planning"],
    )
    report = _audit_cv_text(
        "Anna Bauer led Strategic Planning initiatives across three markets",
        _CV,
        keywords=["Strategic Planning"],
        ledger=ledger,
    )
    assert "Strategic Planning" in report.keywords.claimable_concepts


# ---------------------------------------------------------------------------
# #244 — the '-ship' derivational-noun fold, extending the verb-form fallback.
# Live-reproduced: the CV skill "Mentoring" flagged unbacked against a vault
# that only carries "...Mentorship" (the skill "Team Leadership and
# Mentorship" plus a responsibility saying "...guidance and mentorship...").
# Neither form is a plural of the other and they are single, differing
# tokens -- skill_tokens/skills_near_dupe correctly refuse them (containment
# needs >= 2 tokens, Jaccard is 0) -- so the fold belongs here, in
# surface_present's existing guarded token-level fallback, exactly like the
# -ing/-ed pair already does for "Mentored"/"Mentoring".
# ---------------------------------------------------------------------------

def test_surface_present_folds_ship_noun_to_ing_verb_form():
    """'Mentoring' (CV skill) must be found in text that only says
    '...mentorship...' — the #244 live false negative."""
    text_norm = _norm("Team Leadership and Mentorship")
    assert surface_present("Mentoring", text_norm) is True


def test_surface_present_folds_ship_noun_reverse_direction():
    """The fold must work the other way too: 'Mentorship' (keyword) found in
    text that only says 'mentoring'."""
    text_norm = _norm("Responsible for mentoring junior engineers")
    assert surface_present("Mentorship", text_norm) is True


def test_verb_stem_ship_fold_respects_min_stem_length():
    """'Airship' would strip to 'air' (3 chars) -- below the guard floor, so
    the suffix must NOT be stripped at all, exactly like the existing 'AI'
    guard. (Tested directly on ``_verb_stem``: any text containing a longer
    word trivially contains its own stem as a literal substring, so this
    guard is unobservable through ``surface_present`` alone.)"""
    from applire.services.ats_audit import _verb_stem

    assert _verb_stem("airship") == "airship"
    assert _verb_stem("mentorship") == "mentor"


def test_surface_present_ship_fold_unrelated_words_do_not_false_positive():
    """'Relationship' stems to 'relation' -- must not falsely match an
    unrelated word like 'relative'."""
    text_norm = _norm("Anna Bauer is a close relative of the hiring manager")
    assert surface_present("Relationship", text_norm) is False


def test_dedupe_predicates_stay_strict_after_ship_fold():
    """Guard against ripple, mirroring the -ing/-ed guard above: the ADR-046
    dedupe instruments must NOT gain the -ship fold either. 'Mentor' and
    'Mentorship' are genuinely different skill labels and must still NOT
    auto-merge; the guarded plural-only phrase fold is unaffected."""
    from applire.services.ats_audit import _fold_variants, skills_near_dupe

    assert skills_near_dupe("Mentor", "Mentorship") is False
    assert _fold_variants("mentorship") == ["mentorship", "mentorships"]


# ---------------------------------------------------------------------------
# #260 — pre-generation keyword-liability check, report-surface parity.
# ---------------------------------------------------------------------------


def test_keyword_liability_concepts_on_report_mirrors_the_ledger_predicate():
    """A required, claimable, narrative-less concept surfaces on the ATS
    report exactly like `claimable_concepts` does (agent + report-surface
    parity, #260)."""
    from applire.services.keyword_ledger import build_keyword_ledger

    ledger = build_keyword_ledger(
        classifications=[
            {"concept": "RAG", "status": "direct", "surface_forms": ["RAG"],
             "evidence": "listed under Skills"},
        ],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=["RAG"],
        profile_json={"skills": [{"name": "RAG"}]},
    )
    report = _audit_cv_text(_full_text(), _CV, keywords=["RAG"], ledger=ledger)
    assert report.keywords.keyword_liability_concepts == ["RAG"]


def test_keyword_liability_concepts_empty_when_narrative_backed():
    from applire.services.keyword_ledger import build_keyword_ledger

    ledger = build_keyword_ledger(
        classifications=[
            {"concept": "RAG", "status": "direct", "surface_forms": ["RAG"],
             "evidence": "listed under Skills"},
        ],
        required_skills=["RAG"],
        nice_to_have_skills=[],
        keywords=["RAG"],
        profile_json={
            "skills": [{"name": "RAG"}],
            "signature_stories": [
                {"title": "x", "challenge": "x", "mechanism": "Built a RAG pipeline.", "outcome": "x"},
            ],
        },
    )
    report = _audit_cv_text(_full_text(), _CV, keywords=["RAG"], ledger=ledger)
    assert report.keywords.keyword_liability_concepts == []


def test_keyword_liability_concepts_absent_when_no_ledger():
    report = _audit_cv_text(_full_text(), _CV, keywords=["Python"])
    assert report.keywords.keyword_liability_concepts == []


def test_249_related_and_260_liability_are_orthogonal_for_one_concept():
    """Hard constraint: #260 must not contradict #249's third 'related
    evidence' state. #249's `claimable_concepts` (literal-vault-presence
    axis, feeds the frontend's 'related' display state when the Oracle
    verdicts a claim unbacked) and #260's `keyword_liability_concepts`
    (narrative-depth axis) are DIFFERENT axes over the SAME ledger entry —
    a concept can legitimately appear in both lists at once. This is the
    coherent combined vocabulary: 'claimable, and the ledger vouches for it'
    (#249) is not the same fact as 'claimable, but nobody told its story yet'
    (#260); a reader of both panels sees two complementary signals about the
    same concept, never a contradiction."""
    from applire.services.keyword_ledger import build_keyword_ledger

    # Adjacency-only claimable concept (no literal surface form in the CV
    # text at all — the #249 shape) that ALSO has no narrative anywhere
    # (the #260 shape): both signals fire together, honestly, for one concept.
    ledger = build_keyword_ledger(
        classifications=[
            {
                "concept": "Strategic Planning",
                "status": "partial",
                "surface_forms": ["Strategic Planning"],
                "evidence": "Adjacent to profile skill 'Digital Strategy'.",
            },
        ],
        required_skills=["Strategic Planning"],
        nice_to_have_skills=[],
        keywords=["Strategic Planning"],
        profile_json={"skills": [{"name": "Digital Strategy"}]},
    )
    report = _audit_cv_text(
        "Anna Bauer some unrelated prose with no job keywords",
        _CV,
        keywords=["Strategic Planning"],
        ledger=ledger,
    )
    assert "Strategic Planning" in report.keywords.claimable_concepts
    assert "Strategic Planning" in report.keywords.keyword_liability_concepts
    # Neither list contradicts the other -- both are true statements about
    # the same underlying ledger entry, read off two different fields.
