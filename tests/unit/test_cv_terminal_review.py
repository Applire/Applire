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

"""#538 (ADR-076 clause 3): the CV terminal review closes over the COMPOSED
document, and the subject-identity instrument proves "the reviewed subject IS
the delivered document".

What is under test here is the TOPOLOGY — the skeleton #538 builds:

* the terminal round's review subject is the composed document (vault joins,
  certifications, role facts present), never the bare prose draft;
* the corrector side of the terminal round only ever sees the PROSE shape
  (reordering, never rerouting — no vault field through a writer LLM);
* a terminal corrector change re-composes and RE-ENTERS review with the
  refreshed subject;
* the always-on ``REVIEW_SUBJECT_IDENTITY`` line (own vocabulary, hashes and
  counts only) matches on the clean path;
* an injected post-verdict mutation pass breaks the hash comparison AND
  triggers the clause-3 re-entry — the change is re-reviewed, not reverted
  (the #538 mutation test: delete the re-entry rule or the hash comparison in
  ``_render_cv_background`` and the tests below go red by name).

``review_and_refine`` itself is faked with a chain-dispatching stub — its loop
mechanics have their own tests; here it must only hand the reviewer_prompt_fn
a round, so the closure that builds the composed subject actually runs.
"""
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))


_WORK_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_CERT_NAME = "AWS Solutions Architect Professional"


def _profile_json() -> dict:
    return {
        "contact": {"first_name": "Anna", "last_name": "Bauer",
                    "email": "anna@example.com", "phone": None, "location": "Berlin",
                    "linkedin": None, "xing": None, "portfolio": None},
        "professional_summary": {"de": "Erfahrene Entwicklerin", "en": ""},
        "work_experience": [
            {
                "id": _WORK_ID,
                "company": "Acme GmbH",
                "role": "Software Engineer",
                "start_date": "2020-01",
                "end_date": None,
                "team_size": 7,
                "responsibilities": [f"Aufgabe {i}" for i in range(3)],
            }
        ],
        "education": [], "skills": [], "languages": [],
        "certifications": [
            {"name": _CERT_NAME, "issuing_organization": "AWS", "status": "confirmed"}
        ],
    }


def _writer_payload() -> dict:
    return {
        "summary": "Erfahrene Entwicklerin.",
        "work": [
            {"id": _WORK_ID, "bullets": ["Baute Backend-Services in Python."]},
        ],
        "skills": ["Python"],
    }


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    import applire.models.cover_letter  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _seed(db):
    from applire.models.job import JobAnalysis
    from applire.models.cv import GeneratedCV

    job_id, profile_id, cv_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=str(job_id), raw_text="job",
            role_title="Engineer", required_skills=[], nice_to_have_skills=[],
            keywords=["Python"], seniority_level="mid", company_culture_signals=[],
            language_requirement="de",
        ),
        make_master_profile(
            id=profile_id, profile_json=_profile_json(),
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        GeneratedCV(
            id=cv_id, job_analysis_id=job_id, profile_id=profile_id,
            tailored_data={}, template="classic_german", status="pending",
            target_pages=2,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        ),
    ])
    await db.commit()
    return job_id, profile_id, cv_id


def _fake_review(script, captured):
    """Chain-dispatching review_and_refine stub. Non-terminal chains settle the
    draft untouched. Terminal chains run the reviewer_prompt_fn (so the
    subject-building closure executes, exactly like the real loop's first
    reviewer call) and then apply the next scripted corrector action, if any."""
    async def fake(**kwargs):
        if kwargs.get("chain_id") != "cv_terminal_review":
            return kwargs["draft"]
        captured.append({
            "draft": kwargs["draft"],
            "prompt": kwargs["reviewer_prompt_fn"](kwargs["source"], kwargs["draft"]),
            "system": kwargs["reviewer_system"],
        })
        action = script.pop(0) if script else None
        return action(kwargs["draft"]) if action else kwargs["draft"]
    return fake


async def _run_pipeline(db, ids, *, script=None, captured=None, extra_patches=(),
                        review_retries=2):
    from applire.services.cv import _render_cv_background

    job_id, profile_id, cv_id = ids
    captured = captured if captured is not None else []
    provider = AsyncMock()
    provider.aparse_json.return_value = _writer_payload()
    extract = MagicMock(side_effect=lambda pdf: ("text", 2))

    patches = [
        patch("applire.services.cv.get_provider", return_value=provider),
        patch("applire.services.cv.review_and_refine",
              side_effect=_fake_review(script or [], captured)),
        patch("applire.services.cv.LLM_REVIEW_MAX_RETRIES", review_retries),
        patch("applire.services.cv.get_cv_html", new=AsyncMock(return_value="<html></html>")),
        patch("applire.services.cv._html_to_pdf", new=AsyncMock(return_value=b"pdf")),
        patch("applire.services.ats_audit.extract_text_and_pages", new=extract),
    ]
    with patch("applire.services.cv.AsyncSessionLocal") as sl:
        sl.return_value.__aenter__.return_value = db
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            for p in extra_patches:
                stack.enter_context(p)
            await _render_cv_background(cv_id, job_id, profile_id, "classic_german")
    return captured


def _identity_lines(caplog):
    return [r for r in caplog.records if "REVIEW_SUBJECT_IDENTITY" in r.getMessage()]


# --- the reviewed subject is the composed document --------------------------

@pytest.mark.asyncio
async def test_terminal_review_subject_is_composed_document(db, caplog):
    """#538 core claim: the terminal reviewer's subject carries the vault-joined
    compose-class fields — certifications, role facts — and the real render
    measure; pre-#538 the last reviewer saw none of them."""
    caplog.set_level(logging.INFO, logger="applire.services.cv")
    ids = await _seed(db)
    captured = await _run_pipeline(db, ids)

    assert len(captured) == 1, "exactly one terminal round on the clean path"
    prompt = captured[0]["prompt"]
    assert _CERT_NAME in prompt, "certifications must be IN the review subject"
    assert "team_size" in prompt, "role facts must be IN the review subject"
    assert "measured pages: 2, target: 2" in prompt, "real render measure attached"
    assert "SHAPE NOTE — TERMINAL ROUND" in captured[0]["system"]

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, ids[2])
    assert record.status == "ready"


@pytest.mark.asyncio
async def test_terminal_corrector_only_ever_sees_the_prose_shape(db):
    """Reordering, never rerouting: the draft the terminal loop (and thus its
    corrector) operates on is the PROSE shape — no vault-verbatim field is ever
    routed through a writer LLM (ADR-040/ADR-076 clause 2)."""
    ids = await _seed(db)
    captured = await _run_pipeline(db, ids)

    draft = captured[0]["draft"]
    assert set(draft) == {"summary", "work", "skills"}
    assert "certifications" not in draft and "contact" not in draft


# --- a terminal corrector change re-enters review ---------------------------

@pytest.mark.asyncio
async def test_terminal_corrector_change_recomposes_and_reenters(db, caplog):
    """Clause 3's re-entry rule, corrector direction: a changed draft is
    re-composed (vault joins re-applied by code) and re-reviewed."""
    caplog.set_level(logging.INFO, logger="applire.services.cv")
    ids = await _seed(db)

    def change_summary(draft):
        return {**draft, "summary": "Deutlich verbesserte Zusammenfassung."}

    captured = await _run_pipeline(db, ids, script=[change_summary])

    assert len(captured) == 2, "the changed draft must re-enter review"
    assert "Deutlich verbesserte Zusammenfassung." in captured[1]["prompt"]
    assert _CERT_NAME in captured[1]["prompt"], \
        "re-entered subject is COMPOSED again — vault fields re-joined by code"

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, ids[2])
    assert record.tailored_data["summary"] == "Deutlich verbesserte Zusammenfassung."
    assert record.tailored_data["certifications"][0]["name"] == _CERT_NAME

    lines = _identity_lines(caplog)
    assert lines and "match=True" in lines[-1].getMessage()
    assert "terminal_rounds=2" in lines[-1].getMessage()


# --- subject-identity instrument (evidence layer 1) -------------------------

@pytest.mark.asyncio
async def test_subject_identity_line_always_on_and_matching(db, caplog):
    """The always-on REVIEW_SUBJECT_IDENTITY line fires on EVERY delivery, its
    own vocabulary, match=True on the clean path."""
    caplog.set_level(logging.INFO, logger="applire.services.cv")
    ids = await _seed(db)
    await _run_pipeline(db, ids)

    lines = _identity_lines(caplog)
    assert len(lines) == 1
    msg = lines[0].getMessage()
    assert "match=True" in msg and "terminal_rounds=1" in msg and "reentered=0" in msg
    assert lines[0].levelno == logging.INFO


@pytest.mark.asyncio
async def test_post_verdict_mutation_breaks_hash_and_reenters(db, caplog):
    """#538 evidence layer 1, the MUTATION TEST: an artificial post-verdict
    mutation pass (injected into the audit window, after the terminal verdict)
    must (a) break the hash comparison — a WARNING match=False line — and
    (b) fire the clause-3 re-entry: the CHANGE is re-reviewed (the mutated
    content appears in a fresh terminal-review subject), never silently
    reverted, and the delivery re-audits to a matching final state.

    Deleting the re-entry rule or the hash comparison in
    ``_render_cv_background`` turns exactly this test red."""
    caplog.set_level(logging.INFO, logger="applire.services.cv")
    ids = await _seed(db)

    import applire.services.cv as cv_mod
    real_update = cv_mod._update_ats_report
    fired = {"n": 0}

    async def mutating_update(record, db_, **kw):
        await real_update(record, db_, **kw)
        if fired["n"] == 0:
            fired["n"] = 1
            data = dict(record.tailored_data)
            data["summary"] = data["summary"] + " INJECTED-POST-VERDICT"
            record.tailored_data = data

    captured = await _run_pipeline(
        db, ids,
        extra_patches=[patch("applire.services.cv._update_ats_report", new=mutating_update)],
    )

    lines = [ln.getMessage() for ln in _identity_lines(caplog)]
    warn_levels = [ln.levelno for ln in _identity_lines(caplog)]
    assert len(lines) == 2, "one mismatch line + one post-re-entry line"
    assert "match=False" in lines[0] and warn_levels[0] == logging.WARNING
    assert "match=True" in lines[1] and "reentered=1" in lines[1]

    assert len(captured) == 2, "the mutation must re-enter the terminal review"
    assert "INJECTED-POST-VERDICT" in captured[1]["prompt"], \
        "the re-entered review subject carries the CHANGE (reviewed, not reverted)"

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, ids[2])
    assert record.tailored_data["summary"].endswith("INJECTED-POST-VERDICT"), \
        "the reviewed change ships — re-entry reviews, it does not revert"
    assert record.status == "ready"


# --- review layer off → terminal round off, instrument still on -------------

@pytest.mark.asyncio
async def test_review_layer_disabled_skips_terminal_round_but_logs_identity(db, caplog):
    caplog.set_level(logging.INFO, logger="applire.services.cv")
    ids = await _seed(db)
    captured = await _run_pipeline(db, ids, review_retries=0)

    assert captured == [], "LLM_REVIEW_MAX_RETRIES=0 disables the terminal round too"
    lines = _identity_lines(caplog)
    assert len(lines) == 1
    assert "terminal_rounds=0" in lines[0].getMessage()
    assert "match=True" in lines[0].getMessage()
