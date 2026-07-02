"""Sprint 4 — Happy Path Integration Test (Task 18.12)

End-to-end validation of the new user journey:
  - Upload CV → Analyze JD → Create flow → Advance through steps → Complete

Run with real LLM (requires INTEGRATION_LLM=1):
    INTEGRATION_LLM=1 pytest tests/integration/test_happy_path.py -v
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

JD_FILE = Path(__file__).parent.parent / "files" / "jd.txt"
CV_FILE = Path(__file__).parent.parent / "files" / "cv.pdf"


def test_happy_path_new_user(api):
    """Complete new user journey: CV upload → JD analysis → gap detection → complete."""
    # Step 1: Upload CV (use a sample file or fallback to this test file)
    if CV_FILE.exists():
        with open(CV_FILE, "rb") as f:
            r = requests.post(
                f"{api}/api/profile/upload",
                files={"file": ("cv.pdf", f, "application/pdf")},
                timeout=120,
            )
    else:
        # Fallback: create a minimal text file as CV
        r = requests.post(
            f"{api}/api/profile/upload",
            files={"file": ("cv.txt", b"John Doe, Software Engineer, Python developer", "text/plain")},
            timeout=120,
        )
    assert r.status_code == 200, f"CV upload failed: {r.text}"
    profile_id = r.json()["profile_id"]

    # Step 2: Analyze JD
    jd_text = JD_FILE.read_text() if JD_FILE.exists() else """
    Senior Software Engineer - Python
    
    We are looking for a Senior Software Engineer with strong Python skills.
    Requirements:
    - 5+ years Python experience
    - FastAPI or Django experience
    - PostgreSQL database skills
    - Docker containerization
    - CI/CD pipelines
    """
    r = requests.post(
        f"{api}/api/job/analyze",
        json={"text": jd_text},
        timeout=90,
    )
    assert r.status_code == 200, f"JD analysis failed: {r.text}"
    job_id = r.json()["id"]

    # Step 3: Create flow
    r = requests.post(
        f"{api}/api/flow",
        json={"job_id": job_id},
        timeout=30,
    )
    assert r.status_code == 201, f"Flow creation failed: {r.text}"
    flow_id = r.json()["flow_id"]
    user_type = r.json()["user_type"]
    assert user_type in ("new", "returning"), f"Unexpected user_type: {user_type}"

    # Step 4: Get initial state
    r = requests.get(f"{api}/api/flow/{flow_id}/state", timeout=30)
    assert r.status_code == 200
    state = r.json()
    assert state["current_step"] in ["jd_analysis", "cv_import", "gap_analysis"], \
        f"Unexpected step: {state['current_step']}"

    # Step 5: Perform gap analysis (async job + poll — sync POST /gaps removed, E037 N2)
    start = requests.post(f"{api}/api/job/{job_id}/gap-jobs", timeout=30)
    assert start.status_code == 202, f"Gap job start failed: {start.text}"
    gap_job_id = start.json()["gap_job_id"]
    gap_id = None
    deadline = time.time() + 180
    while time.time() < deadline:
        poll = requests.get(
            f"{api}/api/job/{job_id}/gap-jobs/{gap_job_id}", timeout=15
        )
        assert poll.status_code == 200, f"Gap job poll failed: {poll.text}"
        data = poll.json()
        if data["status"] == "ready":
            assert data["result"] is not None, f"Gap job ready without result: {data}"
            gap_id = data["result"]["id"]
            break
        if data["status"] == "failed":
            pytest.fail(f"Gap analysis failed: {data}")
        time.sleep(3)
    assert gap_id is not None, "Gap job did not become ready within 180s"

    # Step 6: Advance flow to gap_analysis step
    r = requests.post(
        f"{api}/api/flow/{flow_id}/advance",
        json={"step": "gap_analysis", "artifact_id": gap_id},
        timeout=30,
    )
    assert r.status_code == 200, f"Flow advance failed: {r.text}"

    # Step 7: Verify flow state after advance
    r = requests.get(f"{api}/api/flow/{flow_id}/state", timeout=30)
    assert r.status_code == 200
    state = r.json()
    assert "available_actions" in state
    assert state["gap_summary"] is not None, "Gap summary should be populated"

    # Verify match score is within valid range
    match_score = state["gap_summary"]["match_score"]
    assert 0.0 <= match_score <= 1.0, f"Invalid match score: {match_score}"

    # Step 8: Create interview session
    r = requests.post(
        f"{api}/api/session",
        json={"job_id": str(job_id)},
        timeout=60,
    )
    assert r.status_code == 201, f"Session creation failed: {r.text}"
    session_data = r.json()
    session_id = session_data["session_id"]
    assert session_data.get("first_question"), "Session must return a first question"

    # Step 9: Advance flow to interview, linking the session
    r = requests.post(
        f"{api}/api/flow/{flow_id}/advance",
        json={"step": "interview", "artifact_id": str(session_id)},
        timeout=30,
    )
    assert r.status_code == 200, f"Flow advance to interview failed: {r.text}"
    assert r.json()["current_step"] == "interview"

    # Step 10: Send one interview answer
    r = requests.post(
        f"{api}/api/session/{session_id}/message",
        json={"message": (
            "Ich habe über 8 Jahre Erfahrung in der professionellen Softwareentwicklung "
            "mit Python, davon 5 Jahre mit FastAPI in produktiven Microservice-Umgebungen."
        )},
        timeout=60,
    )
    assert r.status_code == 200, f"Session message failed: {r.text}"

    # Step 11: Advance flow to cv_generation (ends interview)
    r = requests.post(
        f"{api}/api/flow/{flow_id}/advance",
        json={"step": "cv_generation"},
        timeout=30,
    )
    assert r.status_code == 200, f"Flow advance to cv_generation failed: {r.text}"
    assert r.json()["current_step"] == "cv_generation"

    # Step 12: Trigger CV generation
    r = requests.post(
        f"{api}/api/cv/generate",
        json={"job_id": str(job_id)},
        timeout=60,
    )
    assert r.status_code == 201, f"CV generation failed: {r.text}"
    cv_id = r.json()["cv_id"]
    assert cv_id, "CV ID must be present"

    # Step 13: Poll CV status until ready (max 60 s)
    cv_ready = False
    for _ in range(60):
        r = requests.get(f"{api}/api/cv/{cv_id}/status", timeout=10)
        assert r.status_code == 200, f"CV status check failed: {r.text}"
        cv_data = r.json()
        if cv_data["status"] == "ready":
            cv_ready = True
            break
        assert cv_data["status"] != "failed", f"CV generation failed: {r.text}"
        time.sleep(1)
    assert cv_ready, "CV did not reach 'ready' within 60 s"

    # Step 14: Trigger cover letter generation
    r = requests.post(
        f"{api}/api/cover-letter/generate",
        json={"job_id": str(job_id), "salary": "95.000 € p.a."},
        timeout=60,
    )
    assert r.status_code == 201, f"Cover letter generation failed: {r.text}"
    cl_id = r.json()["cover_letter_id"]
    assert cl_id, "Cover letter ID must be present"

    # Step 15: Poll cover letter status until ready (max 60 s)
    cl_ready = False
    for _ in range(60):
        r = requests.get(f"{api}/api/cover-letter/{cl_id}/status", timeout=10)
        assert r.status_code == 200, f"Cover letter status check failed: {r.text}"
        status_data = r.json()
        if status_data["status"] == "ready":
            cl_ready = True
            assert status_data.get("letter_data"), "Ready cover letter must have letter_data"
            letter = status_data["letter_data"]
            for section in ("header", "recipient", "body", "signature"):
                assert section in letter, f"Missing cover letter section: {section}"
            break
        assert status_data["status"] != "failed", f"Cover letter generation failed: {r.text}"
        time.sleep(1)
    assert cl_ready, "Cover letter did not reach 'ready' within 60 s"

    # Step 16: Advance flow to complete
    r = requests.post(
        f"{api}/api/flow/{flow_id}/advance",
        json={"step": "complete", "artifact_id": str(cv_id)},
        timeout=30,
    )
    assert r.status_code == 200, f"Flow advance to complete failed: {r.text}"
    assert r.json()["current_step"] == "complete"