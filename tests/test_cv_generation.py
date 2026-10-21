"""
Iteration 5 — CV Generation, Preview & Download
Done when: POST /api/cv/generate → GET /api/cv/{id}/html returns a rendered
           Lebenslauf → GET /api/cv/{id}/pdf returns a valid PDF binary.
"""
from pathlib import Path

import pytest
import requests

JD_FILE = Path(__file__).parent / "files" / "jd.txt"

_NULL_UUID = "00000000-0000-0000-0000-000000000000"

# A reasonably complete LinkedIn profile so the tailoring engine has material
# to work with.
_LINKEDIN = {
    "firstName": "Anna",
    "lastName": "Bauer",
    "emailAddress": "anna.bauer@example.de",
    "headline": "Senior Account Manager | Pharma & Life Sciences",
    "location": {"name": "Munich, Germany"},
    "positions": [
        {
            "title": "Senior Account Manager",
            "companyName": "Roche Deutschland GmbH",
            "startDate": {"month": 3, "year": 2020},
            "endDate": None,
            "description": (
                "Managed key accounts in the oncology division. "
                "Led cross-functional teams of 8 people. "
                "Exceeded revenue targets by 18% in FY2023. "
                "Deployed Veeva CRM for field force automation."
            ),
        },
        {
            "title": "Account Manager",
            "companyName": "Novartis AG",
            "startDate": {"month": 6, "year": 2017},
            "endDate": {"month": 2, "year": 2020},
            "description": (
                "Responsible for DACH territory sales in rare diseases. "
                "Managed relationships with KOLs and hospital pharmacies. "
                "Implemented Salesforce CRM for pipeline tracking."
            ),
        },
    ],
    "educations": [
        {
            "schoolName": "Ludwig-Maximilians-Universität München",
            "degreeName": "Master of Science",
            "fieldOfStudy": "Pharmazie",
            "startDate": {"year": 2012},
            "endDate": {"year": 2017},
        }
    ],
    "skills": [
        {"name": "Veeva CRM"},
        {"name": "Salesforce"},
        {"name": "Key Account Management"},
        {"name": "DACH Sales"},
        {"name": "Oncology"},
        {"name": "Stakeholder Management"},
    ],
    "languages": [
        {"language": {"name": "German"}, "proficiency": "NATIVE_OR_BILINGUAL"},
        {"language": {"name": "English"}, "proficiency": "FULL_PROFESSIONAL"},
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_analyze(api: str) -> dict:
    r = requests.post(
        f"{api}/api/job/analyze",
        json={"text": JD_FILE.read_text()},
        timeout=60,
    )
    assert r.status_code == 200, f"JD analyze failed: {r.text}"
    return r.json()


def _import_profile(api: str, import_job) -> dict:
    return import_job.linkedin_json(api, _LINKEDIN)


def _run_gap_analysis(api: str, job_id: str) -> dict:
    r = requests.post(f"{api}/api/job/{job_id}/gaps", timeout=60)
    assert r.status_code == 200, f"Gap analysis failed: {r.text}"
    return r.json()


def _post_generate(api: str, job_id: str) -> requests.Response:
    return requests.post(
        f"{api}/api/cv/generate",
        json={"job_id": job_id},
        timeout=120,
    )


def _get_html(api: str, cv_id: str) -> requests.Response:
    return requests.get(f"{api}/api/cv/{cv_id}/html", timeout=30)


def _get_pdf(api: str, cv_id: str) -> requests.Response:
    return requests.get(f"{api}/api/cv/{cv_id}/pdf", timeout=60)


# ---------------------------------------------------------------------------
# Module-scoped fixtures: one LLM call chain per test run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def job_id(api):
    return _post_analyze(api)["id"]


@pytest.fixture(scope="module")
def generate_body(api, job_id, import_job):
    _import_profile(api, import_job)
    _run_gap_analysis(api, job_id)
    r = _post_generate(api, job_id)
    assert r.status_code == 201, f"CV generation failed: {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def html_response(api, generate_body):
    cv_id = generate_body["cv_id"]
    r = _get_html(api, cv_id)
    assert r.status_code == 200, f"GET html failed: {r.text}"
    return r


@pytest.fixture(scope="module")
def pdf_response(api, generate_body):
    cv_id = generate_body["cv_id"]
    r = _get_pdf(api, cv_id)
    assert r.status_code == 200, f"GET pdf failed: {r.text}"
    return r


# ---------------------------------------------------------------------------
# Tests: POST /api/cv/generate  (5.6)
# ---------------------------------------------------------------------------


def test_generate_returns_201(api, job_id, import_job):
    _import_profile(api, import_job)
    _run_gap_analysis(api, job_id)
    r = _post_generate(api, job_id)
    assert r.status_code == 201, r.text


def test_generate_response_structure(generate_body):
    assert isinstance(generate_body.get("cv_id"), str)
    assert len(generate_body["cv_id"]) == 36, "cv_id must be a UUID"
    assert isinstance(generate_body.get("html_url"), str) and generate_body["html_url"]
    assert isinstance(generate_body.get("pdf_url"), str) and generate_body["pdf_url"]


def test_generate_urls_reference_cv_id(generate_body):
    cv_id = generate_body["cv_id"]
    assert cv_id in generate_body["html_url"], "html_url must contain cv_id"
    assert cv_id in generate_body["pdf_url"], "pdf_url must contain cv_id"


def test_generate_unknown_job_returns_404(api):
    r = _post_generate(api, _NULL_UUID)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/cv/{cv_id}/html  (5.4)
# ---------------------------------------------------------------------------


def test_html_returns_200(api, generate_body):
    r = _get_html(api, generate_body["cv_id"])
    assert r.status_code == 200, r.text


def test_html_content_type(html_response):
    assert "text/html" in html_response.headers.get("content-type", "")


def test_html_is_valid_document(html_response):
    body = html_response.text
    assert body.strip().lower().startswith("<!doctype html") or "<html" in body.lower()


def test_html_contains_candidate_name(api, html_response):
    """The rendered template must contain the candidate's name from the profile."""
    r = requests.get(f"{api}/api/profile", timeout=10)
    name = r.json()["profile"]["personal_info"]["name"]
    assert name, "Profile must have a non-empty name"
    # At least one name token must appear in the HTML
    assert any(part in html_response.text for part in name.split()), (
        f"Rendered HTML does not contain candidate name '{name}'"
    )


def test_html_contains_work_experience_section(html_response):
    """Classic German Lebenslauf must have a Berufserfahrung heading."""
    assert "Berufserfahrung" in html_response.text, (
        "HTML does not contain 'Berufserfahrung' section heading"
    )


def test_html_contains_skills_section(html_response):
    assert "Kenntnisse" in html_response.text, (
        "HTML does not contain 'Kenntnisse' section heading"
    )


def test_html_contains_languages_section(html_response):
    assert "Sprachkenntnisse" in html_response.text, (
        "HTML does not contain 'Sprachkenntnisse' section heading"
    )


def test_html_contains_at_least_one_skill(api, html_response):
    """The tailored skills list must be non-empty."""
    # Fetch all skill names from the current profile (post-accumulation)
    r = requests.get(f"{api}/api/profile", timeout=10)
    skills = r.json()["profile"]["skills"]
    skill_names = {s["name"] for s in skills if isinstance(s, dict) and s.get("name")}
    # Also check the expected pharma skills that the LLM should surface from work history
    expected_skills = {"Veeva", "Salesforce", "Account Management", "Oncology"} | skill_names
    found = any(skill in html_response.text for skill in expected_skills)
    assert found, (
        f"None of the expected skills found in rendered HTML"
    )


def test_html_unknown_cv_returns_404(api):
    r = _get_html(api, _NULL_UUID)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /api/cv/{cv_id}/pdf  (5.5)
# ---------------------------------------------------------------------------


def test_pdf_returns_200(api, generate_body):
    r = _get_pdf(api, generate_body["cv_id"])
    assert r.status_code == 200, r.text


def test_pdf_content_type(pdf_response):
    assert "application/pdf" in pdf_response.headers.get("content-type", "")


def test_pdf_has_content_disposition(pdf_response):
    disposition = pdf_response.headers.get("content-disposition", "")
    assert "attachment" in disposition
    assert ".pdf" in disposition


def test_pdf_starts_with_pdf_magic_bytes(pdf_response):
    """Valid PDFs start with %PDF."""
    assert pdf_response.content[:4] == b"%PDF", (
        f"Response does not start with %PDF: {pdf_response.content[:8]!r}"
    )


def test_pdf_is_non_empty(pdf_response):
    assert len(pdf_response.content) > 1024, (
        f"PDF is suspiciously small ({len(pdf_response.content)} bytes)"
    )


def test_pdf_unknown_cv_returns_404(api):
    r = _get_pdf(api, _NULL_UUID)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# End-to-end: same CV id is served consistently (idempotent reads)
# ---------------------------------------------------------------------------


def test_html_and_pdf_are_consistent(generate_body, html_response, pdf_response):
    """Both endpoints must serve the same CV (no errors, both non-empty)."""
    assert html_response.status_code == 200
    assert pdf_response.status_code == 200
    assert len(html_response.text) > 200
    assert len(pdf_response.content) > 1024
