"""
Iteration 4 — Gap-Fill Interview
Done when: Start a session tied to a job with known gaps → receive a targeted question
           → answer it → see GET /api/profile updated with the new data
           → receive the next question → session completes.
"""
from pathlib import Path

import pytest
import requests

JD_FILE = Path(__file__).parent / "files" / "jd.txt"

# A deliberately thin profile so the gap analysis produces critical gaps
# that the interview session will address.
_SLIM_LINKEDIN = {
    "firstName": "Max",
    "lastName": "Mustermann",
    "emailAddress": "max@example.de",
    "headline": "Junior Software Developer",
    "location": {"name": "Berlin, Germany"},
    "positions": [
        {
            "title": "Junior Software Developer",
            "companyName": "Startup GmbH",
            "startDate": {"month": 6, "year": 2023},
            "endDate": None,
            "description": "Built internal tooling in Python.",
        }
    ],
    "educations": [
        {
            "schoolName": "FH Berlin",
            "degreeName": "Bachelor of Science",
            "fieldOfStudy": "Informatik",
            "startDate": {"year": 2019},
            "endDate": {"year": 2023},
        }
    ],
    "skills": [{"name": "Python"}, {"name": "Git"}],
    "languages": [
        {"language": {"name": "German"}, "proficiency": "NATIVE_OR_BILINGUAL"},
        {"language": {"name": "English"}, "proficiency": "ELEMENTARY"},
    ],
}

# A substantive answer expected to let the ResponseParser extract concrete data.
_RICH_ANSWER = (
    "I spent two years at Bayer AG as a Salesforce Administrator, "
    "from January 2021 to December 2022, managing the CRM for the pharma division. "
    "I have hands-on experience with Salesforce, Veeva Vault, and CRM data migration. "
    "I also led a cross-functional team of five people and managed stakeholder "
    "relationships at Director level across Germany and Switzerland."
)


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


def _import_profile(api: str, import_job) -> None:
    import_job.linkedin_json(api, _SLIM_LINKEDIN)


def _run_gap_analysis(api: str, job_id: str) -> dict:
    r = requests.post(f"{api}/api/job/{job_id}/gaps", timeout=60)
    assert r.status_code == 200, f"Gap analysis failed: {r.text}"
    return r.json()


def _create_session(api: str, job_id: str) -> requests.Response:
    return requests.post(
        f"{api}/api/session",
        json={"job_id": job_id},
        timeout=60,
    )


def _send_message(api: str, session_id: str, message: str) -> requests.Response:
    return requests.post(
        f"{api}/api/session/{session_id}/message",
        json={"message": message},
        timeout=60,
    )


def _get_profile(api: str) -> dict:
    r = requests.get(f"{api}/api/profile", timeout=10)
    assert r.status_code == 200, f"GET /api/profile failed: {r.text}"
    return r.json()


# ---------------------------------------------------------------------------
# Module-scoped setup: one LLM call chain per test run
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def job_id(api):
    return _post_analyze(api)["id"]


@pytest.fixture(scope="module")
def session_body(api, job_id, import_job):
    _import_profile(api, import_job)
    _run_gap_analysis(api, job_id)
    r = _create_session(api, job_id)
    assert r.status_code == 201, f"Session creation failed: {r.text}"
    return r.json()


@pytest.fixture(scope="module")
def first_message_body(api, session_body):
    """Send the first answer; returns the message response."""
    session_id = session_body["session_id"]
    r = _send_message(api, session_id, _RICH_ANSWER)
    assert r.status_code == 200, f"Send message failed: {r.text}"
    return r.json()


# ---------------------------------------------------------------------------
# Tests: POST /api/session
# ---------------------------------------------------------------------------


def test_create_session_returns_201(api, job_id, import_job):
    _import_profile(api, import_job)
    _run_gap_analysis(api, job_id)
    r = _create_session(api, job_id)
    assert r.status_code == 201, r.text


def test_create_session_response_structure(session_body):
    assert isinstance(session_body.get("session_id"), str)
    assert len(session_body["session_id"]) == 36, "session_id must be a UUID"
    assert isinstance(session_body.get("question"), str)
    assert isinstance(session_body.get("gaps_total"), int)
    assert isinstance(session_body.get("gaps_remaining"), int)


def test_create_session_question_is_non_empty(session_body):
    assert session_body["question"].strip(), "First question must not be empty"


def test_create_session_gaps_counts_are_valid(session_body):
    assert session_body["gaps_total"] >= 0
    assert session_body["gaps_remaining"] >= 0
    assert session_body["gaps_remaining"] <= session_body["gaps_total"]


def test_create_session_unknown_job_returns_404(api):
    r = _create_session(api, "00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Tests: POST /api/session/{id}/message
# ---------------------------------------------------------------------------


def test_first_message_returns_200(api, session_body):
    r = _send_message(api, session_body["session_id"], _RICH_ANSWER)
    assert r.status_code == 200, r.text


def test_first_message_response_structure(first_message_body):
    assert isinstance(first_message_body.get("complete"), bool)
    if first_message_body["complete"]:
        assert first_message_body.get("question") is None
        assert first_message_body.get("gaps_remaining") is None
    else:
        assert isinstance(first_message_body.get("question"), str)
        assert first_message_body["question"].strip()
        assert isinstance(first_message_body.get("gaps_remaining"), int)
        assert first_message_body["gaps_remaining"] >= 0


def test_session_progresses_or_completes(first_message_body, session_body):
    """After one answer, gaps_remaining must have decreased or session is complete."""
    if first_message_body["complete"]:
        return  # session finished in one turn — valid
    remaining_after = first_message_body["gaps_remaining"]
    remaining_before = session_body["gaps_remaining"]
    assert remaining_after < remaining_before, (
        f"gaps_remaining did not decrease: {remaining_before} → {remaining_after}"
    )


def test_profile_updated_after_answer(api, first_message_body):
    """GET /api/profile must reflect new skills extracted from _RICH_ANSWER."""
    profile = _get_profile(api)
    skills = [s["name"].lower() for s in profile["profile"].get("skills", [])]
    # At minimum, one of the skills explicitly named in _RICH_ANSWER should appear.
    # (ResponseParser may not catch all of them, but should catch at least one.)
    expected = {"salesforce", "veeva vault", "crm"}
    found = expected & set(skills)
    assert found, (
        f"None of the expected skills {expected} found in profile skills {skills!r}. "
        "ProfileUpdater may not have merged the parsed data."
    )


def test_session_reaches_completion(api, session_body):
    """Drive the session to completion by answering every remaining question."""
    session_id = session_body["session_id"]
    # Send up to 10 answers to ensure we eventually complete without an infinite loop.
    for _ in range(10):
        r = _send_message(api, session_id, _RICH_ANSWER)
        assert r.status_code in (200, 409), r.text
        body = r.json()
        if r.status_code == 409 or body.get("complete"):
            return  # session completed
    pytest.fail("Session did not complete after 10 messages")


def test_message_to_completed_session_returns_409(api, session_body):
    """Once complete, further messages must return 409 Conflict."""
    session_id = session_body["session_id"]
    # Drive to completion first (idempotent — already done by prior test if run in order)
    for _ in range(12):
        r = _send_message(api, session_id, _RICH_ANSWER)
        if r.status_code == 409 or (r.status_code == 200 and r.json().get("complete")):
            break
    # Now it must be complete — next message must 409
    r = _send_message(api, session_id, "one more")
    assert r.status_code == 409, f"Expected 409 after session complete, got {r.status_code}: {r.text}"


def test_empty_message_returns_422(api, session_body):
    r = _send_message(api, session_body["session_id"], "   ")
    assert r.status_code == 422


def test_message_unknown_session_returns_404(api):
    r = _send_message(api, "00000000-0000-0000-0000-000000000000", "hello")
    assert r.status_code == 404
