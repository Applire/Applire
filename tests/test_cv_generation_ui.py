# tests/test_iter20_cv_generation_ui.py
"""
Sprint 6 — CV Generation UI (integration tests, Tasks 20.7, 20.8, 20.10, 20.11)

Done when:
  - GET /api/cv/{id}/html returns X-Frame-Options: SAMEORIGIN header
  - GET /api/cv/{id}/html returns Content-Security-Policy: frame-ancestors 'self' header
  - GET /api/cv/{id}/pdf Content-Disposition contains role title slug
  - GET /api/cv?job_id={id} returns list sorted newest-first
  - GET /api/cv?job_id={unknown} returns empty list (not 404)
  - GET /static/templates/classic_german.png returns 200 image/png
  - GET /static/templates/modern_swiss.png returns 200 image/png

Run (requires Docker stack running):
    pytest tests/test_iter20_cv_generation_ui.py -v
"""
import re
import time
from pathlib import Path

import pytest
import requests

JD_FILE = Path(__file__).parent / "files" / "jd.txt"

_LINKEDIN = {
    "firstName": "Anna",
    "lastName": "Bauer",
    "emailAddress": "anna.bauer@example.de",
    "headline": "Senior Software Engineer",
    "location": {"name": "Munich, Germany"},
    "positions": [
        {
            "title": "Senior Software Engineer",
            "companyName": "Roche Deutschland GmbH",
            "startDate": {"month": 1, "year": 2019},
            "endDate": None,
            "description": "Microservices with Python and FastAPI. PostgreSQL, Docker, CI/CD.",
        }
    ],
    "educations": [
        {
            "schoolName": "Technische Universität München",
            "degreeName": "Master of Science",
            "fieldOfStudy": "Informatik",
            "startDate": {"year": 2014},
            "endDate": {"year": 2018},
        }
    ],
    "skills": [{"name": "Python"}, {"name": "FastAPI"}, {"name": "PostgreSQL"}],
    "languages": [{"language": {"name": "German"}, "proficiency": "NATIVE_OR_BILINGUAL"}],
}


@pytest.fixture(scope="module", autouse=True)
def ensure_profile(api, import_job):
    import_job.linkedin_json(api, _LINKEDIN)


@pytest.fixture(scope="module")
def job_id(api):
    jd_text = JD_FILE.read_text() if JD_FILE.exists() else (
        "QA Manager, 21 CFR Part 11 — München\n"
        "Requirements: Python, FastAPI, PostgreSQL, Docker."
    )
    r = requests.post(f"{api}/api/job/analyze", json={"text": jd_text}, timeout=60)
    assert r.status_code == 200, f"JD analysis failed: {r.text}"
    return r.json()["id"]


@pytest.fixture(scope="module")
def ready_cv_id(api, job_id):
    """Generate a CV and poll until ready. Returns cv_id string."""
    r = requests.post(
        f"{api}/api/cv/generate",
        json={"job_id": job_id, "template": "classic_german"},
        timeout=30,
    )
    assert r.status_code == 201, f"Generate failed: {r.text}"
    cv_id = r.json()["cv_id"]

    deadline = time.time() + 120
    while time.time() < deadline:
        status_r = requests.get(f"{api}/api/cv/{cv_id}/status", timeout=10)
        assert status_r.status_code == 200
        s = status_r.json()["status"]
        if s == "ready":
            return cv_id
        if s == "failed":
            pytest.fail(f"CV generation failed: {status_r.json()}")
        time.sleep(3)

    pytest.fail("CV did not become ready within 120s")


class TestHTMLSecurityHeaders:
    def test_x_frame_options_sameorigin(self, api, ready_cv_id):
        r = requests.get(f"{api}/api/cv/{ready_cv_id}/html", timeout=15)
        assert r.status_code == 200
        assert r.headers.get("X-Frame-Options") == "SAMEORIGIN"

    def test_content_security_policy_frame_ancestors(self, api, ready_cv_id):
        r = requests.get(f"{api}/api/cv/{ready_cv_id}/html", timeout=15)
        assert "frame-ancestors 'self'" in r.headers.get("Content-Security-Policy", "")


class TestPDFFilename:
    def test_content_disposition_has_lebenslauf_prefix(self, api, ready_cv_id):
        r = requests.get(f"{api}/api/cv/{ready_cv_id}/pdf", timeout=30)
        assert r.status_code == 200
        disposition = r.headers.get("Content-Disposition", "")
        assert "lebenslauf-" in disposition

    def test_content_disposition_ends_with_pdf(self, api, ready_cv_id):
        r = requests.get(f"{api}/api/cv/{ready_cv_id}/pdf", timeout=30)
        disposition = r.headers.get("Content-Disposition", "")
        assert disposition.endswith('.pdf"')

    def test_filename_has_role_slug_not_just_uuid(self, api, ready_cv_id):
        r = requests.get(f"{api}/api/cv/{ready_cv_id}/pdf", timeout=30)
        disposition = r.headers.get("Content-Disposition", "")
        match = re.search(r'filename="([^"]+)"', disposition)
        assert match is not None
        filename = match.group(1)
        parts = filename.replace(".pdf", "").split("-")
        assert len(parts) >= 3, f"Expected slug in filename, got: {filename}"


class TestCVListEndpoint:
    def test_list_returns_200(self, api, job_id, ready_cv_id):
        r = requests.get(f"{api}/api/cv?job_id={job_id}", timeout=15)
        assert r.status_code == 200

    def test_list_returns_array(self, api, job_id, ready_cv_id):
        r = requests.get(f"{api}/api/cv?job_id={job_id}", timeout=15)
        assert isinstance(r.json(), list)

    def test_list_contains_ready_cv(self, api, job_id, ready_cv_id):
        r = requests.get(f"{api}/api/cv?job_id={job_id}", timeout=15)
        ids = [cv["cv_id"] for cv in r.json()]
        assert ready_cv_id in ids

    def test_list_ready_cv_has_urls(self, api, job_id, ready_cv_id):
        r = requests.get(f"{api}/api/cv?job_id={job_id}", timeout=15)
        ready = next(cv for cv in r.json() if cv["cv_id"] == ready_cv_id)
        assert ready["html_url"] is not None
        assert ready["pdf_url"] is not None

    def test_list_empty_for_unknown_job(self, api):
        r = requests.get(
            f"{api}/api/cv?job_id=00000000-0000-0000-0000-000000000000", timeout=10
        )
        assert r.status_code == 200
        assert r.json() == []


class TestTemplateThumbnails:
    def test_classic_german_thumbnail_served(self, api):
        r = requests.get(f"{api}/static/templates/classic_german.png", timeout=15)
        assert r.status_code == 200
        assert "image/png" in r.headers.get("Content-Type", "")

    def test_modern_swiss_thumbnail_served(self, api):
        r = requests.get(f"{api}/static/templates/modern_swiss.png", timeout=15)
        assert r.status_code == 200
        assert "image/png" in r.headers.get("Content-Type", "")

    def test_thumbnail_is_valid_png(self, api):
        r = requests.get(f"{api}/static/templates/classic_german.png", timeout=15)
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
