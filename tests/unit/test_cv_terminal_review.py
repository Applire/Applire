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


async def _seed(db, *, profile_json=None):
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
            id=profile_id, profile_json=profile_json or _profile_json(),
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


def _fake_review_settling(captured, *, settled_draft, blocking, path="exhausted"):
    """F-4 seam fake: unlike `_fake_review` this one drives the reviewer_prompt_fn AND
    the `on_settle` hook, with a `settled` draft that DIFFERS from the reviewed one —
    the shape `reviewer.py`'s `exhausted` return always has. It exists to exercise
    `cv.py`'s own wiring (`reviewed_cell` + `_record_settle`), not the loop's."""
    from applire.services.review_issues import ReviewSettle

    async def fake(**kwargs):
        if kwargs.get("chain_id") != "cv_terminal_review":
            return kwargs["draft"]
        reviewed = kwargs["draft"]
        kwargs["reviewer_prompt_fn"](kwargs["source"], reviewed)
        settled = settled_draft(reviewed)
        on_settle = kwargs.get("on_settle")
        if on_settle is not None:
            on_settle(ReviewSettle(
                path=path, approved=False, blocking_issues=tuple(blocking),
                minor_issues=(), rounds=1, settled=settled,
            ))
        captured.append({"reviewed": reviewed, "settled": settled})
        return settled

    return fake


async def _run_pipeline(db, ids, *, script=None, captured=None, extra_patches=(),
                        review_retries=2, payload=None, review_fake=None):
    from applire.services.cv import _render_cv_background

    job_id, profile_id, cv_id = ids
    captured = captured if captured is not None else []
    provider = AsyncMock()
    # EVERY provider JSON call returns the same payload — the language pass may
    # legitimately re-emit the prose through the provider, and a diverging
    # default would silently overwrite a test's tailored draft.
    provider.aparse_json.return_value = payload if payload is not None else _writer_payload()
    extract = MagicMock(side_effect=lambda pdf: ("text", 2))

    patches = [
        patch("applire.services.cv.get_provider", return_value=provider),
        patch("applire.services.cv.review_and_refine",
              side_effect=review_fake or _fake_review(script or [], captured)),
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


def _subject_slice(prompt: str) -> str:
    """The COMPOSED-CV section of the terminal reviewer prompt — deliberately
    excludes the CANDIDATE PROFILE block, which always carries the vault's own
    cert/role-fact strings and would make any whole-prompt assertion pass
    vacuously (caught by mutation C: subject swapped to the bare prose draft
    and a whole-prompt assertion stayed green)."""
    start = prompt.index("COMPOSED CV (the delivered document):")
    end = prompt.index("RENDER MEASURE")
    return prompt[start:end]


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
    subject = _subject_slice(prompt)
    assert _CERT_NAME in subject, "certifications must be IN the review subject"
    assert '"team_size": 7' in subject, "role facts must be IN the review subject"
    assert "Baute Backend-Services in Python." in subject, "prose bullets in the subject"
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
    reentered_subject = _subject_slice(captured[1]["prompt"])
    assert "Deutlich verbesserte Zusammenfassung." in reentered_subject
    assert _CERT_NAME in reentered_subject, \
        "re-entered subject is COMPOSED again — vault fields re-joined by code"

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, ids[2])
    assert record.tailored_data["summary"] == "Deutlich verbesserte Zusammenfassung."
    assert record.tailored_data["certifications"][0]["name"] == _CERT_NAME

    lines = _identity_lines(caplog)
    assert lines and "match=True" in lines[-1].getMessage()
    assert "terminal_rounds=2" in lines[-1].getMessage()


@pytest.mark.asyncio
async def test_reentry_bound_exhaustion_ships_recomposed_and_flags_structured(db, caplog):
    """The bounded clause-3 exception (pre-propagation adversarial finding 1):
    when the terminal corrector changes the draft in EVERY allowed round, the
    final change ships re-composed but never re-reviewed. That state must be
    (a) delivered (never a gate), (b) re-composed from the final prose with the
    vault joins re-applied, and (c) flagged STRUCTURALLY on the always-on
    identity line (`reentry_exhausted=True`, WARNING) — an unstructured
    warning alone is bookkeeping, not testimony."""
    caplog.set_level(logging.INFO, logger="applire.services.cv")
    ids = await _seed(db)

    def change1(draft):
        return {**draft, "summary": "Erste Terminal-Korrektur."}

    def change2(draft):
        return {**draft, "summary": "Zweite Terminal-Korrektur."}

    captured = await _run_pipeline(db, ids, script=[change1, change2])

    assert len(captured) == 2, "bound=1 allows exactly two terminal invocations"

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, ids[2])
    assert record.status == "ready", "never a delivery gate"
    assert record.tailored_data["summary"] == "Zweite Terminal-Korrektur.", \
        "the final (unreviewed) change ships re-composed"
    assert record.tailored_data["certifications"][0]["name"] == _CERT_NAME, \
        "vault joins re-applied on the final recomposition"

    lines = _identity_lines(caplog)
    assert len(lines) == 1
    assert "reentry_exhausted=True" in lines[0].getMessage()
    assert lines[0].levelno == logging.WARNING, \
        "an unreviewed final change is a WARNING even when the hash matches"


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
    assert "INJECTED-POST-VERDICT" in _subject_slice(captured[1]["prompt"]), \
        "the re-entered review subject carries the CHANGE (reviewed, not reverted)"

    from applire.models.cv import GeneratedCV
    record = await db.get(GeneratedCV, ids[2])
    assert record.tailored_data["summary"].endswith("INJECTED-POST-VERDICT"), \
        "the reviewed change ships — re-entry reviews, it does not revert"
    assert record.status == "ready"


# --- run-B class (2026-08-14): the pass-authored pair is IN the subject -----

@pytest.mark.asyncio
async def test_pass_authored_measured_pair_is_in_the_review_subject(db):
    """#538 evidence layer 2, pinned as a synthetic regression: run B delivered
    a bullet paired with a paraphrase of itself ("… — measured: …"), authored
    by ``_prefer_measured_outcomes`` AFTER the last reviewer — no reviewer ever
    saw it. Under the reordered topology the same deterministic pass runs
    inside ``_compose_document``, so its output lies IN the terminal review
    subject. Visibility is the criterion; whether the model flags it is
    judgement (ADR-076 clause 3 / #540's first-migration rationale)."""
    from applire.models.job import JobAnalysis
    from applire.models.cv import GeneratedCV

    # The exact pair from tests/unit/test_cv_outcome_preference.py's live-shape
    # fixture — token overlap over find_paired_outcome's conservative floor is
    # proven there; this test pins the TOPOLOGY (pair in the review subject).
    target_bullet = (
        "Built an internal LLM-assisted document classification service in "
        "Python (FastAPI, PostgreSQL, Docker), targeting a 60% reduction in "
        "manual processing time."
    )
    outcome_bullet = (
        "Documents pre-classified by the service passed the very first review "
        "round in most cases, confirming the 60% reduction target is "
        "conservative."
    )
    profile = _profile_json()
    profile["work_experience"][0]["responsibilities"] = [target_bullet]
    profile["work_experience"][0]["achievements"] = [outcome_bullet]
    profile["work_experience"][0]["is_current"] = True

    job_id, profile_id, cv_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db.add_all([
        JobAnalysis(
            id=job_id, raw_text_hash=str(job_id), raw_text="job",
            role_title="Engineer", required_skills=[], nice_to_have_skills=[],
            keywords=["Python"], seniority_level="mid", company_culture_signals=[],
            language_requirement="en", jd_language="en",
        ),
        make_master_profile(
            id=profile_id, profile_json=profile,
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

    payload = _writer_payload()
    payload["work"][0]["bullets"] = [target_bullet]

    captured = await _run_pipeline(db, (job_id, profile_id, cv_id), payload=payload)

    subject = _subject_slice(captured[0]["prompt"])
    assert "— measured:" in subject, \
        "the pass-authored pair must lie IN the terminal review subject"
    record = await db.get(GeneratedCV, cv_id)
    joined = " ".join(record.tailored_data["work_history"][0]["bullets"])
    assert "— measured:" in joined, "delivered == reviewed subject (same pair)"


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


# --- F-4: the CV chain supplies the draft the last verdict was rendered over -


@pytest.mark.asyncio
async def test_the_cv_chain_reports_a_post_verdict_correction_as_unverified(db):
    """F-4 (#672 line 123) — the SEAM test for `cv.py`'s wiring.

    `review_and_refine` hands `reviewer_prompt_fn` the draft it is about to judge and
    keeps no record of it, so only this chain can tell the report "the corrector revised
    the document after this verdict". Revert `reviewed_draft=reviewed_cell["draft"]` in
    `_record_settle` (or the `reviewed_cell["draft"] = draft` line in `_reviewer_prompt`)
    and this test goes red by name while every other test in this file stays green.
    """
    from applire.models.cv import GeneratedCV

    ids = await _seed(db)
    captured = []
    payload = _writer_payload()

    # A DISTINCT correction per round: an idempotent one would make round 2's settled
    # draft equal the draft round 2 reviewed, which is the genuine `fail` shape and
    # would make this test pass for the wrong reason.
    rounds = {"n": 0}

    def _corrected(draft):
        rounds["n"] += 1
        out = dict(draft)
        out["summary"] = f"Correction {rounds['n']} the reviewer never read."
        return out

    await _run_pipeline(
        db, ids, captured=captured, payload=payload,
        review_fake=_fake_review_settling(
            captured, settled_draft=_corrected,
            blocking=('The summary claims "Erfahrene Entwicklerin" without support.',),
        ),
    )
    assert captured and captured[0]["reviewed"] != captured[0]["settled"]

    record = await db.get(GeneratedCV, ids[2])
    check = {c["id"]: c for c in record.ats_report["checks"]}["terminal-review"]
    assert check["status"] == "not_applicable", check
    assert "UNVERIFIED" in check["details"]
    assert "Erfahrene Entwicklerin" in check["details"]
    assert "Open findings" not in check["details"]
    # The MEASURED half. `exhausted` reports as unverified from the settle-path table
    # alone, so only a sentence that requires `CorrectionFacts` can prove this chain
    # actually supplied the reviewed draft — mutation M2 (dropping
    # `reviewed_draft=reviewed_cell["draft"]`) survives without this assertion.
    assert (
        "could decide" in check["details"]
        or "cannot be decided deterministically" in check["details"]
    ), check["details"]


# --- F-5: the SIGNATURE STORY FIGURES block reaches the terminal reviewer ----


def _profile_json_with_story() -> dict:
    """The founder-UAT SHAPE, synthetic throughout: one curated story on the first work
    entry whose measured outcome carries a percent figure."""
    profile = _profile_json()
    profile["signature_stories"] = [
        {
            "id": "11111111-2222-3333-4444-555555555555",
            "title": "LIMS rollout across three sites",
            "challenge": "Three sites planned three parallel validation strategies.",
            "mechanism": "One shared validation strategy, reviewed once per site.",
            "outcome": "Validation effort fell by roughly 80 % and the first site went live.",
            "experience_refs": [_WORK_ID],
        }
    ]
    return profile


@pytest.mark.asyncio
async def test_the_terminal_reviewer_is_told_a_story_figure_is_missing(db):
    """F-5 (#672 line 124) — the SEAM test for `cv.py`'s wrapper stack.

    Revert the `story_figures_reviewer_prompt_fn` wrapper in `_terminal_review` and this
    test goes red by name; every other test in this file stays green.
    """
    ids = await _seed(db, profile_json=_profile_json_with_story())
    captured = await _run_pipeline(db, ids, captured=[])
    assert captured, "the terminal reviewer must have been asked at least once"
    prompt = captured[0]["prompt"]
    assert "SIGNATURE STORY FIGURES" in prompt
    assert "LIMS rollout across three sites" in prompt
    assert "MISSING" in prompt
    # The block names the entry the story belongs to, so the corrector knows where.
    assert "Acme GmbH" in prompt
    # And the check that reads it is on the terminal door.
    assert "11. SIGNATURE STORY FIGURES" in captured[0]["system"]


@pytest.mark.asyncio
async def test_a_story_whose_figure_is_on_the_page_is_reported_present_not_demanded(db):
    """The asserted baseline: the block is a COMPLETE statement, so a story already
    carried is listed under PRESENT and never demanded
    (`feedback_prohibition_is_not_an_answer`)."""
    ids = await _seed(db, profile_json=_profile_json_with_story())
    payload = _writer_payload()
    payload["summary"] = "Validation effort fell by 80% after one shared strategy."
    captured = await _run_pipeline(db, ids, captured=[], payload=payload)
    prompt = captured[0]["prompt"]
    assert "SIGNATURE STORY FIGURES" in prompt
    assert "PRESENT" in prompt
    assert "MISSING — blocking" not in prompt


@pytest.mark.asyncio
async def test_a_profile_without_stories_leaves_the_reviewer_prompt_untouched(db):
    ids = await _seed(db)
    captured = await _run_pipeline(db, ids, captured=[])
    assert "SIGNATURE STORY FIGURES" not in captured[0]["prompt"]


# --- F-9: the SKILLS-LIST SHAPE block reaches the terminal reviewer ----------


@pytest.mark.asyncio
async def test_the_terminal_reviewer_is_told_which_skills_were_lifted_from_prose(db):
    """F-9 (#672 line 126) — the SEAM test for `cv.py`'s wrapper stack.

    Revert the `skill_shape_reviewer_prompt_fn` wrapper in `_terminal_review` and this
    test goes red by name; every other test in this file stays green.
    """
    ids = await _seed(db)
    payload = _writer_payload()
    payload["skills"] = ["System Owner", "vendor selection"]
    payload["summary"] = "Acted as System Owner and ran vendor selection for three sites."
    captured = await _run_pipeline(db, ids, captured=[], payload=payload)
    prompt = captured[0]["prompt"]
    assert "SKILLS-LIST SHAPE" in prompt
    assert '"System Owner"' in prompt
    assert "a fact, not a verdict" in prompt
    assert "12. SKILLS-LIST SHAPE" in captured[0]["system"]


@pytest.mark.asyncio
async def test_a_skills_list_of_attested_vault_forms_adds_no_shape_block(db):
    """The asserted baseline for the block above."""
    profile = _profile_json()
    profile["skills"] = [{"name": "Python", "status": "confirmed"}]
    ids = await _seed(db, profile_json=profile)
    payload = _writer_payload()
    payload["skills"] = ["Python"]
    payload["summary"] = "Built services in Python."
    captured = await _run_pipeline(db, ids, captured=[], payload=payload)
    assert "SKILLS-LIST SHAPE" not in captured[0]["prompt"]


@pytest.mark.asyncio
async def test_the_chain_threads_the_keyword_ledger_into_the_skills_shape_scan(db):
    """F-9 seam, the ledger half. The SKILLS-LIST SHAPE fact can only see a chip that
    `_restore_narrative_named_skills` placed via a SIBLING surface form of its ledger row
    if the chain hands it that ledger. Drop the `keyword_ledger` argument in
    `_terminal_review` and this test goes red by name."""
    import applire.services.skill_shape as skill_shape_mod

    seen: list = []
    real = skill_shape_mod.skill_shape_reviewer_prompt_fn

    # No default on `keyword_ledger`: a chain that passes only two positionals raises
    # TypeError here, which is what makes this a kill rather than a green no-op — the
    # chain's own ledger is `[]` on this fixture (no GapAnalysis row), so asserting its
    # VALUE could never distinguish "threaded" from "not threaded".
    def spy(base_fn, profile_json, keyword_ledger, **kw):
        seen.append(keyword_ledger)
        return real(base_fn, profile_json, keyword_ledger, **kw)

    ids = await _seed(db)
    await _run_pipeline(
        db, ids, captured=[],
        extra_patches=[patch.object(skill_shape_mod, "skill_shape_reviewer_prompt_fn", spy)],
    )
    assert seen, (
        "the skills-shape wrapper must be built by the terminal chain WITH the ledger as "
        "its third positional argument"
    )
    assert seen[0] == [], seen  # this fixture has no GapAnalysis row


@pytest.mark.asyncio
async def test_the_shape_and_story_wrappers_are_handed_the_composed_document(db):
    """The contract `_reviewer_prompt` has with this whole wrapper chain: it passes the
    COMPOSED document, exactly as it does to `coverage_reviewer_prompt_fn` and to
    `pinned_facts_reviewer_prompt_fn` (`composed=True`). A wrapper that re-composes its
    argument composes a `TailoredCVData` dump as if it were a prose draft — measured
    2026-09-20: with a `structured_document_fn` in place the SKILLS-LIST SHAPE block
    reported nothing on 6 of 6 real-provider runs whose delivered document the same scan
    flags three entries on, and no seam assertion about the block's PRESENCE could see it.
    """
    import applire.services.skill_shape as skill_shape_mod

    seen: list = []
    real = skill_shape_mod.skill_shape_reviewer_prompt_fn

    def spy(base_fn, profile_json, keyword_ledger, **kw):
        assert "structured_document_fn" not in kw or kw["structured_document_fn"] is None, (
            "the argument is already composed — re-composing it is the 2026-09-20 defect"
        )
        inner = real(base_fn, profile_json, keyword_ledger, **kw)

        def wrapped(source, draft):
            seen.append(draft)
            return inner(source, draft)

        return wrapped

    ids = await _seed(db)
    await _run_pipeline(
        db, ids, captured=[],
        extra_patches=[patch.object(skill_shape_mod, "skill_shape_reviewer_prompt_fn", spy)],
    )
    assert seen, "the wrapper must be called at least once"
    doc = seen[0]
    # Composed shape, not the writer's prose shape …
    assert "work_history" in doc and "work" not in doc, doc.keys()
    # … and carrying a vault-joined field only `_compose_document` adds.
    assert any(c.get("name") == _CERT_NAME for c in (doc.get("certifications") or [])), doc
