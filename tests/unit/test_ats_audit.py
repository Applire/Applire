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
from applire.services.ats_audit import _audit_cv_text, _audit_letter_text


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


def test_page_length_two_pages_pass_no_advisory():
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=2)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "pass" and c.details is None


def test_page_length_three_pages_pass_with_advisory():
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=3)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "pass"
    assert c.details and "3 pages" in c.details and "2" in c.details


def test_page_length_over_three_pages_fails():
    report = _audit_cv_text(_full_text(), _CV, keywords=[], page_count=6)
    c = _check_by_id(report, "page-length")
    assert c is not None and c.status == "fail"
    assert c.details and "6" in c.details and "2" in c.details


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
    return (
        "Anna Bauer anna@example.com +49 151 1234567 Berlin\n"
        "Senior Backend Engineer Cloudwerk GmbH 04/2021 - heute\n"
        "Software Engineer DataHaus AG 09/2017 - 03/2021\n"
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
     "evidence": "enforced code review standards at BioNTech"},
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
