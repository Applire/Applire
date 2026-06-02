"""
Iteration 3 — Gap Analysis
Done when: POST /api/job/{job_id}/gaps returns a gap report with a realistic
           match score and specific, actionable gaps.
"""
import json
from pathlib import Path

import pytest
import requests

JD_FILE = Path(__file__).parent / "files" / "jd.txt"

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


def _import_profile(api: str) -> dict:
    linkedin_json = {
        "firstName": "Max",
        "lastName": "Mustermann",
        "emailAddress": "max@example.de",
        "headline": "Senior Software Engineer",
        "location": {"name": "Munich, Germany"},
        "positions": [
            {
                "title": "Senior Software Engineer",
                "companyName": "Acme GmbH",
                "startDate": {"month": 1, "year": 2021},
                "endDate": None,
                "description": "Python microservices, FastAPI, PostgreSQL, Docker.",
            }
        ],
        "educations": [
            {
                "schoolName": "TU Munich",
                "degreeName": "Bachelor of Science",
                "fieldOfStudy": "Informatik",
                "startDate": {"year": 2014},
                "endDate": {"year": 2018},
            }
        ],
        "skills": [
            {"name": "Python"},
            {"name": "FastAPI"},
            {"name": "PostgreSQL"},
            {"name": "Docker"},
            {"name": "Git"},
        ],
        "languages": [
            {"language": {"name": "German"}, "proficiency": "NATIVE_OR_BILINGUAL"},
            {"language": {"name": "English"}, "proficiency": "PROFESSIONAL_WORKING"},
        ],
    }
    r = requests.post(
        f"{api}/api/profile/import",
        data={"linkedin_json": json.dumps(linkedin_json)},
        timeout=90,
    )
    assert r.status_code == 200, f"Profile import failed: {r.text}"
    return r.json()


def _post_gaps(api: str, job_id: str) -> requests.Response:
    return requests.post(f"{api}/api/job/{job_id}/gaps", timeout=60)


# ---------------------------------------------------------------------------
# Module-scoped setup: one LLM call per test run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def job_id(api):
    return _post_analyze(api)["id"]


@pytest.fixture(scope="module")
def gap_body(api, job_id):
    _import_profile(api)
    r = _post_gaps(api, job_id)
    assert r.status_code == 200, f"Gap analysis failed: {r.text}"
    return r.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_gaps_returns_200(api, job_id):
    _import_profile(api)
    r = _post_gaps(api, job_id)
    assert r.status_code == 200, r.text


def test_gaps_response_structure(gap_body):
    assert isinstance(gap_body.get("id"), str) and len(gap_body["id"]) == 36
    assert isinstance(gap_body.get("job_analysis_id"), str) and len(gap_body["job_analysis_id"]) == 36
    assert isinstance(gap_body.get("profile_id"), str) and len(gap_body["profile_id"]) == 36
    # ADR-035: match_score is computed deterministically in Python; None only when JD
    # has zero requirements (never in practice here).
    score = gap_body.get("match_score")
    assert score is None or isinstance(score, float), (
        f"match_score must be float or None, got {type(score)}"
    )
    assert isinstance(gap_body.get("critical_gaps"), list)
    assert isinstance(gap_body.get("minor_gaps"), list)
    assert isinstance(gap_body.get("strengths"), list)
    assert isinstance(gap_body.get("keyword_gaps"), list)
    assert isinstance(gap_body.get("category_a"), list)
    assert isinstance(gap_body.get("category_b"), list)
    assert isinstance(gap_body.get("category_c"), list)
    # ADR-035: requirement_breakdown is always present in new responses.
    assert isinstance(gap_body.get("requirement_breakdown"), list), (
        "requirement_breakdown must be a list"
    )
    assert isinstance(gap_body.get("created_at"), str)


def test_gaps_match_score_in_range(gap_body):
    score = gap_body["match_score"]
    if score is not None:
        assert 0.0 <= score <= 1.0, f"match_score {score} out of range [0.0, 1.0]"


def test_gaps_lists_contain_strings(gap_body):
    for field in ("critical_gaps", "minor_gaps", "strengths", "keyword_gaps"):
        items = gap_body[field]
        assert all(isinstance(item, str) and item for item in items), (
            f"{field} must contain non-empty strings"
        )


def test_gaps_has_actionable_output(gap_body):
    """At least some gaps or strengths must be present — the report must not be empty."""
    total_items = (
        len(gap_body["critical_gaps"])
        + len(gap_body["minor_gaps"])
        + len(gap_body["strengths"])
    )
    assert total_items > 0, "Gap report has no gaps or strengths — LLM returned empty lists"


def test_gaps_job_id_matches(gap_body, job_id):
    assert gap_body["job_analysis_id"] == job_id


def test_gaps_unknown_job_returns_404(api):
    r = _post_gaps(api, "00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
