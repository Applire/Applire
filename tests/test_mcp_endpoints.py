"""Sprint 7 — Iteration 21: Missing endpoint tests (Tasks 21.11, 21.12)

Done when:
  - GET /api/profile/exists — returns {exists, completeness_score}; false before
    profile created, true after; completeness_score reflects actual profile content
  - GET /api/applications?q= — full-text search over role_title, company_name,
    notes; case-insensitive; empty/omitted q returns all; no-match returns empty

Run (requires Docker):
    pytest tests/test_iter21_sprint7_endpoints.py -v
"""

import uuid
from pathlib import Path

import pytest
import requests

CV_FILE = Path(__file__).parent / "files" / "Profile.pdf"
JD_FILE = Path(__file__).parent / "files" / "jd.txt"


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def job_id(api):
    """Analyze the canonical JD once; return job_analysis_id."""
    text = JD_FILE.read_text()
    r = requests.post(f"{api}/api/job/analyze", json={"text": text}, timeout=60)
    assert r.status_code == 200, f"JD analysis failed: {r.text}"
    return r.json()["id"]


@pytest.fixture(scope="module")
def uploaded_profile(api, import_job):
    """Import the canonical CV through the async import job; return the ready result."""
    return import_job.result(api, "Profile.pdf", CV_FILE.read_bytes(), "application/pdf")


@pytest.fixture(scope="module")
def application(api, job_id, uploaded_profile):
    """Create an application and patch it with known notes; return its data dict."""
    r = requests.post(f"{api}/api/applications", json={"job_analysis_id": job_id})
    if r.status_code == 409:
        items = requests.get(f"{api}/api/applications").json()["items"]
        app = next((i for i in items if i["job_analysis_id"] == job_id), None)
        assert app is not None, "409 but application not found"
    else:
        assert r.status_code == 201, f"Unexpected {r.status_code}: {r.text}"
        app = r.json()

    # Patch in a known notes string so search-by-notes tests are deterministic.
    notes_text = "Contacted recruiter via xyzUniqueSearchToken"
    requests.patch(
        f"{api}/api/applications/{app['id']}",
        json={"notes": notes_text},
    )
    app["notes"] = notes_text
    return app


# ---------------------------------------------------------------------------
# GET /api/profile/exists
# ---------------------------------------------------------------------------


def test_profile_exists_response_has_correct_shape(api, uploaded_profile):
    """Response must contain exactly {exists: bool, completeness_score: float}."""
    r = requests.get(f"{api}/api/profile/exists")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"exists", "completeness_score"}
    assert isinstance(body["exists"], bool)
    assert isinstance(body["completeness_score"], float)


def test_profile_exists_returns_true_after_upload(api, uploaded_profile):
    """`exists` must be True once a profile has been uploaded."""
    r = requests.get(f"{api}/api/profile/exists")
    assert r.status_code == 200
    assert r.json()["exists"] is True


def test_profile_exists_completeness_score_is_positive(api, uploaded_profile):
    """completeness_score must be > 0 when a real CV has been imported."""
    r = requests.get(f"{api}/api/profile/exists")
    assert r.status_code == 200
    assert r.json()["completeness_score"] > 0.0


def test_profile_exists_completeness_score_is_bounded(api, uploaded_profile):
    """completeness_score must be in the range [0.0, 1.0]."""
    r = requests.get(f"{api}/api/profile/exists")
    assert r.status_code == 200
    score = r.json()["completeness_score"]
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# GET /api/applications?q= (search)
# ---------------------------------------------------------------------------


def test_search_no_q_returns_all_applications(api, application):
    """Omitting ?q returns the full pipeline (at least the seeded application)."""
    r = requests.get(f"{api}/api/applications")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    ids = [item["id"] for item in body["items"]]
    assert application["id"] in ids


def test_search_empty_q_returns_all_applications(api, application):
    """?q= (empty string) is equivalent to omitting q — returns all."""
    r_all = requests.get(f"{api}/api/applications")
    r_empty = requests.get(f"{api}/api/applications", params={"q": ""})
    assert r_empty.status_code == 200
    assert r_empty.json()["total"] == r_all.json()["total"]


def test_search_by_role_title_returns_match(api, application):
    """?q=<role_title_substring> must return the matching application."""
    role_title = application.get("role_title") or ""
    if not role_title:
        pytest.skip("Application has no role_title — JD extraction did not populate it")

    # Use a distinctive substring (first 6+ chars) to avoid accidental matches
    query = role_title[:8]
    r = requests.get(f"{api}/api/applications", params={"q": query})
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["items"]]
    assert application["id"] in ids, (
        f"Expected application {application['id']} in results for q={query!r}, "
        f"got: {ids}"
    )


def test_search_by_company_name_returns_match(api, application):
    """?q=<company_name_substring> must return the matching application."""
    company_name = application.get("company_name") or ""
    if not company_name:
        pytest.skip("Application has no company_name — JD extraction did not populate it")

    query = company_name[:8]
    r = requests.get(f"{api}/api/applications", params={"q": query})
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["items"]]
    assert application["id"] in ids, (
        f"Expected application {application['id']} in results for q={query!r}, "
        f"got: {ids}"
    )


def test_search_by_notes_returns_match(api, application):
    """?q=<notes_substring> must return the application with matching notes."""
    # "xyzUniqueSearchToken" was patched in by the fixture
    r = requests.get(f"{api}/api/applications", params={"q": "xyzUniqueSearchToken"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    ids = [item["id"] for item in body["items"]]
    assert application["id"] in ids


def test_search_no_match_returns_empty(api, application):
    """?q=<unmatched_token> must return total: 0 and items: []."""
    r = requests.get(
        f"{api}/api/applications",
        params={"q": "ZZZUNMATCHABLE_TOKEN_9a3f7b"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_search_is_case_insensitive(api, application):
    """Search must be case-insensitive (ILIKE)."""
    notes_upper = "XYZUNIQUESEARCHTOKEN"  # uppercase version of the patched notes
    r = requests.get(f"{api}/api/applications", params={"q": notes_upper})
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["items"]]
    assert application["id"] in ids, (
        f"Case-insensitive search failed: q={notes_upper!r} did not match application"
    )
