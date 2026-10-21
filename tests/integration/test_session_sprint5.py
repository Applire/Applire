"""Integration tests for Sprint 5 (Iteration 19) backend additions.

Covers:
- 19.9  target_gap parameter on SessionCreateRequest (micro-session)
- 19.11 /api/job/{job_id}/gaps/refresh endpoint
- 19.10 ConflictSummary in SessionMessageResponse

Run with:
    INTEGRATION_LLM=1 pytest tests/integration/test_session_sprint5.py -v
"""
import os
import time
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.skipif(
    not os.getenv("INTEGRATION_LLM"),
    reason="Real-LLM test — set INTEGRATION_LLM=1 to run",
)

_CV_FILE = Path(__file__).parent.parent / "files" / "Profile.pdf"

SAMPLE_JD = (
    "Senior Python Engineer at FinTech GmbH. "
    "Requirements: Python, FastAPI, PostgreSQL, Docker, REST APIs, "
    "CI/CD experience, 5+ years backend development."
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def ensure_profile(api: str, import_job) -> None:
    """Upload a CV so Gap-Click (targeted) session tests can find a master profile.

    In CI each run starts with an empty database. Targeted sessions fail with
    "No profile found — upload a CV first" if no profile exists.
    """
    import_job.result(api, "Profile.pdf", _CV_FILE.read_bytes(), "application/pdf")


@pytest.fixture(scope="module")
def job_id(api: str) -> str:
    """Analyse a JD once for all tests in this module."""
    res = requests.post(
        f"{api}/api/job/analyze",
        json={"text": SAMPLE_JD},
        timeout=30,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


# ---------------------------------------------------------------------------
# 19.9 — target_gap creates a 1-question micro-session
# ---------------------------------------------------------------------------

def test_targeted_session_with_target_gap(api: str, job_id: str) -> None:
    """mode=targeted + target_gap creates a micro-session with estimated_questions=1."""
    res = requests.post(
        f"{api}/api/session",
        json={
            "job_id": job_id,
            "mode": "targeted",
            "target_gap": "Python 5+ years",
        },
        timeout=30,
    )
    assert res.status_code == 201, res.text
    data = res.json()

    assert data["mode"] == "targeted", f"expected mode=targeted, got {data['mode']}"
    assert data["estimated_questions"] == 1, (
        f"micro-session must have estimated_questions=1, got {data['estimated_questions']}"
    )
    assert data.get("session_id"), "session_id must be present"
    # First question must be present
    first_q = data.get("question") or data.get("first_question")
    assert first_q, "micro-session must return a question"


def test_targeted_session_completes_after_one_answer(api: str, job_id: str) -> None:
    """Answering the single micro-session question returns complete=True."""
    # Create micro-session
    session_res = requests.post(
        f"{api}/api/session",
        json={
            "job_id": job_id,
            "mode": "targeted",
            "target_gap": "Docker containerisation",
        },
        timeout=30,
    )
    assert session_res.status_code == 201, session_res.text
    session_id = session_res.json()["session_id"]

    # Send one answer
    msg_res = requests.post(
        f"{api}/api/session/{session_id}/message",
        json={"message": "Yes, I have 3 years of Docker experience in production environments."},
        timeout=30,
    )
    assert msg_res.status_code == 200, msg_res.text
    msg_data = msg_res.json()

    assert msg_data["complete"] is True, (
        "micro-session must complete after a single answer"
    )


def test_targeted_session_without_target_gap_falls_back_to_full(api: str, job_id: str) -> None:
    """mode=targeted without target_gap falls back gracefully (estimated_questions > 1 or still valid)."""
    res = requests.post(
        f"{api}/api/session",
        json={"job_id": job_id, "mode": "targeted"},
        timeout=30,
    )
    # Must not error — backend should handle missing target_gap
    assert res.status_code in (200, 201), res.text


# ---------------------------------------------------------------------------
# 19.11 — /api/job/{job_id}/gaps/refresh endpoint
# ---------------------------------------------------------------------------

def test_gaps_refresh_schema_and_new_id(api: str, job_id: str) -> None:
    """/api/job/{job_id}/gaps/refresh returns valid schema and a new analysis id."""
    time.sleep(15)  # Longer pause — previous tests make several LLM calls, cumulative rate limit needs more headroom

    # GET currently cached gap analysis (if any) — no LLM call
    cached_res = requests.get(f"{api}/api/job/{job_id}/gaps", timeout=10)
    cached_id = cached_res.json().get("id") if cached_res.status_code == 200 else None

    # Single LLM call to /refresh
    res = requests.post(f"{api}/api/job/{job_id}/gaps/refresh", timeout=30)
    assert res.status_code == 200, res.text
    data = res.json()

    # Verify schema
    assert "match_score" in data, "match_score must be in response"
    assert isinstance(data["match_score"], (int, float)), "match_score must be numeric"
    assert 0.0 <= data["match_score"] <= 1.0, "match_score must be in [0, 1]"
    for key in ("category_a", "category_b", "category_c"):
        assert key in data, f"{key} must be in response"
        assert isinstance(data[key], list), f"{key} must be a list"

    # Verify a new analysis was created (not the cached one)
    if cached_id is not None:
        assert data.get("id") != cached_id, (
            "refresh must create a new analysis, not return the cached one"
        )


def test_gaps_refresh_unknown_job_returns_404(api: str) -> None:
    """Refreshing gaps for a non-existent job returns 404."""
    res = requests.post(
        f"{api}/api/job/00000000-0000-0000-0000-000000000000/gaps/refresh",
        timeout=10,
    )
    assert res.status_code == 404, f"Expected 404, got {res.status_code}"


# ---------------------------------------------------------------------------
# 19.10 — pending_conflicts field in SessionMessageResponse
# ---------------------------------------------------------------------------

def test_message_response_has_pending_conflicts_field(api: str, job_id: str) -> None:
    """SessionMessageResponse always includes the pending_conflicts field (null or list)."""
    # Create a normal (guided) session
    session_res = requests.post(
        f"{api}/api/session",
        json={"job_id": job_id, "mode": "guided"},
        timeout=30,
    )
    assert session_res.status_code == 201, session_res.text
    session_id = session_res.json()["session_id"]

    # Send a benign answer
    msg_res = requests.post(
        f"{api}/api/session/{session_id}/message",
        json={"message": "I have 6 years of Python experience with FastAPI and microservices."},
        timeout=30,
    )
    assert msg_res.status_code == 200, msg_res.text
    data = msg_res.json()

    # pending_conflicts must be present — either null/None or a list
    assert "pending_conflicts" in data, (
        "pending_conflicts field must always be present in SessionMessageResponse"
    )
    value = data["pending_conflicts"]
    assert value is None or isinstance(value, list), (
        f"pending_conflicts must be null or a list, got {type(value)}"
    )


def test_conflict_summary_schema_when_present(api: str, job_id: str) -> None:
    """When pending_conflicts is a non-empty list, each entry has the expected ConflictSummary schema."""
    # Create a session and send an answer that could potentially trigger a conflict.
    # We can't guarantee a conflict is triggered deterministically, so we verify the
    # schema IF conflicts are returned, and skip otherwise.
    session_res = requests.post(
        f"{api}/api/session",
        json={"job_id": job_id, "mode": "guided"},
        timeout=30,
    )
    assert session_res.status_code == 201, session_res.text
    session_id = session_res.json()["session_id"]

    # Send an answer that contradicts a hypothetical existing profile entry
    msg_res = requests.post(
        f"{api}/api/session/{session_id}/message",
        json={"message": "I worked at Acme Corp as Software Engineer from 2018 to 2020."},
        timeout=30,
    )
    assert msg_res.status_code == 200, msg_res.text
    data = msg_res.json()

    conflicts = data.get("pending_conflicts")
    if not conflicts:
        pytest.skip("No conflict triggered in this run — schema check skipped")

    for conflict in conflicts:
        assert "conflict_id" in conflict, "ConflictSummary must have conflict_id"
        assert "field" in conflict, "ConflictSummary must have field"
        assert "old_value" in conflict, "ConflictSummary must have old_value"
        assert "new_value" in conflict, "ConflictSummary must have new_value"
        assert isinstance(conflict["conflict_id"], str)
        assert isinstance(conflict["field"], str)
