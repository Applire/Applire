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

"""#618 (education half) — a two-source import wrote the same qualification
twice under Education.

The reported shape (paraphrased — the real institution/degree strings live
only in ``test_618_reconcile_no_duplicate_after_set_field.py``, already
committed from the #618 certification-half PR; this file uses SYNTHETIC
equivalents throughout that preserve the same shape): one source names the
institution by its long legal form and the degree by an EN-ish job-title
phrase; the other names the institution by its short colloquial name and the
degree by the DE qualification name, and states the same date range at
coarser precision (year-only vs month+year). ``_apply_upsert_education``'s
natural key was plain ``classify_dupe`` on (institution, degree) alone —
neither the institution alias nor the date-precision difference folds, so the
pair created two rows.

``classify_education_dupe`` (``dedupe.py``) is the education-aware
counterpart to ``classify_certification_dupe``: institution is the anchor
(mechanical legal-form-noise fold + the pre-existing plain containment
signal), degree and dates are corroborators that can only ESCALATE a
same-institution candidate to AMBIGUOUS, never silently downgrade it to
DISTINCT. Degree text is folded only through a small, purely mechanical
German-preposition list — it does NOT attempt EN/DE occupational-title
translation, which stays a semantic judgement ADR-062 clause 1 reserves for
the model (see ``test_the_applier_natural_key_cannot_recognise_this_pair`` in
the sibling test file for the ground truth this mirrors).
"""
from __future__ import annotations

from applire.schemas.profile import EducationEntry, MasterProfileData
from applire.services.ats_audit import skill_tokens
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.dedupe import (
    _edu_date_relation,
    _edu_degree_relation,
    _edu_institution_relation,
    _AMBIG,
    _DISTINCT,
    _SAME,
    classify_dupe,
    classify_education_dupe,
)
from applire.services.profile.reconcile.import_witness import compute_import_not_applied
from applire.services.profile.reconcile.ops import SetField, UpsertEducation

SOURCE = "cv_upload (second source)"

# Synthetic equivalents of the #618 shape — a long-legal-form institution name
# folding to the same distinctive token as its colloquial short name, and an
# EN-ish job-title-shaped degree phrase alongside the DE qualification name.
_LONG_INSTITUTION = "Nordkontor Akademie GmbH"
_SHORT_INSTITUTION = "Nordkontor"
_DE_DEGREE = "Fachinformatiker Systemintegration"
_EN_DEGREE = "IT Systems Administrator"


def _existing_profile(*, degree: str = _DE_DEGREE) -> MasterProfileData:
    return MasterProfileData(
        education=[
            EducationEntry(
                institution=_LONG_INSTITUTION,
                degree=degree,
                start_date="09/2011",
                end_date="07/2014",
            )
        ]
    )


# ── 0. Prove the fixtures actually have the property their names claim ─────────


def test_fixture_degree_strings_really_are_token_disjoint():
    """Don't trust the "EN-ish vs DE" label — run the real tokeniser. If this
    ever starts sharing a token, the AMBIGUOUS test below is no longer testing
    what its docstring claims."""
    en = skill_tokens(_EN_DEGREE)
    de = skill_tokens(_DE_DEGREE)
    assert en, de  # both non-empty (a real signal, not two empty sets)
    assert en.isdisjoint(de), (en, de)


def test_fixture_institutions_share_a_token_only_after_stripping_noise():
    """The long/short institution pair must NOT already collapse under the
    plain (pre-#618) skill_tokens containment rule at >= 2 tokens — otherwise
    this fixture would already have matched under the OLD generic
    classify_dupe and would not exercise the new stripped signal at all."""
    long_tokens = skill_tokens(_LONG_INSTITUTION)
    short_tokens = skill_tokens(_SHORT_INSTITUTION)
    assert short_tokens == {"nordkontor"}
    assert long_tokens == {"nordkontor", "akademie", "gmbh"}
    # The old classify_dupe containment rule needs >= 2 tokens on the smaller
    # side to count as SAME — a single shared token does not qualify.
    assert short_tokens < long_tokens and len(short_tokens) == 1


# ── 1. classify_education_dupe — the three signals in isolation ────────────────


def test_date_range_containment_is_the_brief_own_example():
    """'2002-2005' contains '09/2002-01/2005' — the literal example the work
    order gives."""
    assert _edu_date_relation("2002", "2005", "09/2002", "01/2005") == _SAME


def test_date_ranges_with_no_overlap_at_all_are_distinct():
    assert _edu_date_relation("09/1998", "07/2000", "2015", "2017") == _DISTINCT


def test_missing_dates_on_one_side_are_no_evidence():
    assert _edu_date_relation(None, None, "2011", "2014") is None


def test_institution_alias_folds_after_stripping_legal_form_noise():
    assert _edu_institution_relation(_LONG_INSTITUTION, _SHORT_INSTITUTION) == _SAME


def test_bare_institution_containment_without_noise_is_ambiguous_not_same():
    """A single generic token surviving containment (no legal-form noise
    involved at all) stays AMBIGUOUS — mirrors the #172 skills rule this
    module already follows elsewhere ('Ford' vs 'Ford Foundation')."""
    assert _edu_institution_relation("Nordkontor Regional Campus", _SHORT_INSTITUTION) == _AMBIG


def test_degree_cross_language_pair_is_distinct_not_folded():
    """The instrument does NOT translate — an EN-ish job title and its DE
    qualification-name counterpart tokenise to disjoint sets."""
    assert _edu_degree_relation(_EN_DEGREE, _DE_DEGREE) == _DISTINCT


def test_degree_same_language_cosmetic_variant_is_same():
    assert _edu_degree_relation(_DE_DEGREE, _DE_DEGREE.upper()) == _SAME


# ── 2. classify_education_dupe — the combined verdict ───────────────────────────


def test_institution_alias_plus_date_containment_plus_same_degree_matches():
    existing = _existing_profile().education[0]
    verdict = classify_education_dupe(
        institution=_SHORT_INSTITUTION, degree=_DE_DEGREE,
        start_date="2011", end_date="2014",
        existing=[existing],
        institution_getter=lambda e: e.institution, degree_getter=lambda e: e.degree,
        start_date_getter=lambda e: e.start_date, end_date_getter=lambda e: e.end_date,
    )
    assert verdict.match is existing
    assert verdict.ambiguous == []


def test_institution_alias_plus_date_containment_plus_cross_language_degree_is_ambiguous():
    """The #618 shape itself: institution + dates say SAME, degree text does
    not fold — never a silent merge, never a silent duplicate."""
    existing = _existing_profile(degree=_EN_DEGREE).education[0]
    verdict = classify_education_dupe(
        institution=_SHORT_INSTITUTION, degree=_DE_DEGREE,
        start_date="2011", end_date="2014",
        existing=[existing],
        institution_getter=lambda e: e.institution, degree_getter=lambda e: e.degree,
        start_date_getter=lambda e: e.start_date, end_date_getter=lambda e: e.end_date,
    )
    assert verdict.match is None
    assert verdict.ambiguous == [existing]


def test_distinct_institution_is_not_even_a_candidate():
    existing = EducationEntry(
        institution="Bergland Technical College", degree=_DE_DEGREE,
        start_date="09/2011", end_date="07/2014",
    )
    verdict = classify_education_dupe(
        institution=_LONG_INSTITUTION, degree=_DE_DEGREE,
        start_date="2011", end_date="2014",
        existing=[existing],
        institution_getter=lambda e: e.institution, degree_getter=lambda e: e.degree,
        start_date_getter=lambda e: e.start_date, end_date_getter=lambda e: e.end_date,
    )
    assert verdict.match is None
    assert verdict.ambiguous == []  # ruled out entirely, not even parked


def test_same_institution_and_degree_but_disjoint_dates_escalates_not_vetoes():
    """Two clearly separate stints (same institution, same nominal degree
    name, decades apart) must ask rather than silently assume either
    'same entry' or 'definitely two entries'."""
    existing = _existing_profile().education[0]  # 09/2011-07/2014
    verdict = classify_education_dupe(
        institution=_SHORT_INSTITUTION, degree=_DE_DEGREE,
        start_date="2015", end_date="2017",
        existing=[existing],
        institution_getter=lambda e: e.institution, degree_getter=lambda e: e.degree,
        start_date_getter=lambda e: e.start_date, end_date_getter=lambda e: e.end_date,
    )
    assert verdict.match is None
    assert verdict.ambiguous == [existing]


# ── 3. _apply_upsert_education — the #618 batch shape, end to end ──────────────


def test_two_source_batch_produces_one_entry_and_asks_instead_of_duplicating():
    """The actual #618 defect, reproduced with synthetic strings: a second
    source's set_field against the vault entry, PLUS a target-less
    upsert_education restating the SAME qualification under alternate wording.
    Before this fix: two silent rows. After: one entry, one question."""
    profile = _existing_profile()
    existing_id = profile.education[0].id

    ops = [
        SetField(target=existing_id, field="grade", value="1,7"),
        UpsertEducation(
            institution=_SHORT_INSTITUTION,
            degree=_EN_DEGREE,
            start_date="2011",
            end_date="2014",
        ),
    ]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.education) == 1
    assert result.profile.education[0].institution == _LONG_INSTITUTION
    assert result.profile.education[0].grade == "1,7"
    assert len(result.pending_confirmations) == 1
    pc = result.pending_confirmations[0]
    assert pc.context["section"] == "education"
    assert _DE_DEGREE in pc.context["existing"][0]


def test_two_source_batch_with_matching_degree_wording_merges_silently_no_duplicate():
    """The cleaner case: same degree wording, only the institution alias and
    date precision differ. A real MATCH — merges, no question needed."""
    profile = _existing_profile()  # long institution, DE degree, fine dates
    ops = [
        UpsertEducation(
            institution=_SHORT_INSTITUTION,  # alias
            degree=_DE_DEGREE,  # SAME wording as the vault entry
            start_date="2011",  # coarser precision
            end_date="2014",
            grade="1,7",  # genuinely new fact from the second source
        )
    ]
    result = apply_ops(profile, ops, SOURCE)

    assert len(result.profile.education) == 1
    assert result.profile.education[0].institution == _LONG_INSTITUTION  # unchanged
    assert result.profile.education[0].grade == "1,7"  # filled
    assert result.pending_confirmations == []


# ── 4. import_witness — the applier's OWN instrument, not a weaker one ─────────
# Mirrors the N1 cert bug this same session's #618 precedent named: a witness
# still on the generic classify_dupe would report a pair the REAL applier just
# matched as a loss. Constructed so arm (a) (exact natural key) and arm (c)
# (the op's OWN declared institution/degree, verbatim) both MISS — the op that
# actually carried this fact into the vault used the CANONICAL (long-form)
# wording, per prompt rule 5's "restate the entity with its own existing text"
# guidance, while `incoming` (freshly extracted from the second source) still
# holds the alias wording. Only arm (b) — classify_education_dupe — can
# rescue it.


def test_import_witness_recognises_the_same_alias_pair_the_applier_matched():
    incoming = MasterProfileData(
        education=[
            EducationEntry(institution=_SHORT_INSTITUTION, degree=_DE_DEGREE,
                            start_date="2011", end_date="2014")
        ]
    )
    canonical_op = UpsertEducation(
        institution=_LONG_INSTITUTION, degree=_DE_DEGREE,
        start_date="09/2011", end_date="07/2014",
    )
    merged = apply_ops(MasterProfileData(), [canonical_op], SOURCE).profile
    assert len(merged.education) == 1  # sanity: the op landed as one entry

    # Ground truth: the OLD generic classify_dupe genuinely cannot see this
    # pair either (single-token institution containment is AMBIGUOUS, not
    # SAME, so classify_dupe's all-evidenced-SAME MATCH requirement fails) —
    # proving arm (b) would have missed it before #618's education half.
    old_verdict = classify_dupe(
        {"institution": _SHORT_INSTITUTION, "degree": _DE_DEGREE},
        merged.education,
        {"institution": lambda e: e.institution, "degree": lambda e: e.degree},
    )
    assert old_verdict.match is None

    items = compute_import_not_applied(incoming, merged, ops=[canonical_op])
    assert items == [], (
        "import_witness reported a pair the real applier just merged as "
        f"lost: {items}"
    )


# ---------------------------------------------------------------------------
# Adversarial pass 2026-09-01 — the OPPOSITE failure direction
# ---------------------------------------------------------------------------
# The classifier's own docstring states the policy: once the institution says
# SAME, degree and dates "can only ESCALATE the verdict to AMBIGUOUS — never
# silently downgrade it to DISTINCT", because a silent WRONG MERGE is no better
# than the silent second row #618 removes. That policy was not honoured in the
# one case where NEITHER corroborator carries any evidence at all: institution
# alone then produced a MATCH against whichever entry happened to come first.
#
# Measured before the fix: an UpsertEducation naming only an institution (blank
# degree, no dates) — the shape a thin second source such as a LinkedIn
# education row produces — matched the FIRST entry at that institution and
# _fill_empties wrote its field/grade onto that row. Two qualifications became
# one, and import_witness (which shares this classifier by design) reported the
# discarded one as "matched, not lost".

_UNI = "Universität Hamburg"


def _uni_pair() -> list[EducationEntry]:
    return [
        EducationEntry(institution=_UNI, degree="B.Sc. Wirtschaftsinformatik",
                       start_date="2015-10", end_date="2019-09"),
        EducationEntry(institution=_UNI, degree="M.Sc. Wirtschaftsinformatik",
                       start_date="2019-10", end_date="2021-09"),
    ]


_GETTERS = dict(
    institution_getter=lambda e: e.institution,
    degree_getter=lambda e: e.degree,
    start_date_getter=lambda e: e.start_date,
    end_date_getter=lambda e: e.end_date,
)


def test_institution_alone_never_matches_it_asks():
    """No corroboration on EITHER side is not identity — it is a question."""
    verdict = classify_education_dupe(
        institution=_UNI, degree="", start_date=None, end_date=None,
        existing=_uni_pair(), **_GETTERS,
    )
    assert verdict.match is None, (
        "a blank degree with no dates matched on the institution string alone — "
        f"silently merging into {verdict.match.degree!r}"
    )
    assert len(verdict.ambiguous) == 2, "both same-institution entries must be parked"


def test_institution_alias_alone_never_matches_it_asks():
    """The #618 noise-stripped alias fold must not become a matcher by itself.

    This pair shares no raw token, so the pre-#618 generic classify_dupe could
    not see it at all — the alias fold is what makes it reachable, which is why
    the zero-evidence gate has to hold here specifically.
    """
    verdict = classify_education_dupe(
        institution="Provadis Hochschule", degree="", start_date=None, end_date=None,
        existing=[EducationEntry(institution="Provadis Partner für Bildung GmbH",
                                 degree="Fachinformatiker Anwendungsentwicklung",
                                 start_date="2002-09", end_date="2005-01")],
        **_GETTERS,
    )
    assert verdict.match is None
    assert len(verdict.ambiguous) == 1


def test_one_corroborator_is_enough_to_match():
    """The gate asks for evidence, not for BOTH kinds — dates alone still match."""
    verdict = classify_education_dupe(
        institution=_UNI, degree="", start_date="2015-10", end_date="2019-09",
        existing=_uni_pair()[:1], **_GETTERS,
    )
    assert verdict.match is not None
    assert verdict.match.degree == "B.Sc. Wirtschaftsinformatik"


def test_the_two_source_alias_pair_still_merges():
    """Regression pin: the zero-evidence gate must not undo #618's own fix."""
    verdict = classify_education_dupe(
        institution=_SHORT_INSTITUTION, degree=_DE_DEGREE,
        start_date="2011", end_date="2014",
        existing=_existing_profile().education, **_GETTERS,
    )
    assert verdict.match is not None, "#618's own reported pair no longer merges"
    # The EN/DE degree pair stays a QUESTION by design (ADR-062 clause 1 — no
    # occupational-title translation in the deterministic layer), so the pin
    # uses the aliased-institution + same-degree-wording pair, which is the
    # one #618's fix actually turned from two rows into one.


def test_a_thin_second_source_row_does_not_contaminate_an_existing_degree():
    """End to end through the applier: ask, never write onto the wrong row."""
    profile = MasterProfileData(education=_uni_pair())
    op = UpsertEducation(institution=_UNI, degree="",
                         field="Wirtschaftsinformatik (Master, Data Science)", grade="1,3")
    result = apply_ops(profile, [op], SOURCE)
    bachelor = result.profile.education[0]
    assert bachelor.grade is None, (
        f"the incoming grade was written onto {bachelor.degree!r}"
    )
    assert result.pending_confirmations, "an unresolvable identity must reach the user as a question"
