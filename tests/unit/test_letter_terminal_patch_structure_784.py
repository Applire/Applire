# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""784:71 (E-12 walk, founder 2026-10-22) — the structural check on the terminal
corrector patch that ships UNREVIEWED: signature block intact, salutation alone,
body and closing separate; a patch that breaks one ships the last REVIEWED draft
instead (fail-closed).

Two layers:

* the fact (``terminal_patch_structure_defects``) on hand-built compositions,
  including the real shape from the 2026-08-15 luna log (the corrector prepended
  a body paragraph's content to the closing and appended "Mit freundlichen
  Grüßen" as its own paragraph);
* one seam test per call site that can ship an unreviewed patch — the re-entry
  bound in the terminal loop (v1 and v2) and v1's final-floor review round.
"""
import logging
from unittest.mock import patch

import pytest

from applire.services.cover_letter import terminal_patch_structure_defects
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

_SALUTATION = "Sehr geehrte Damen und Herren,"
_OPENING = (
    "die Position als Leiter Operations bei der Rheinwerk Verpackungen GmbH spricht mich an, "
    "weil Sie Verpackungen für Lebensmittelkunden fertigen."
)
_BODY = (
    "Bei Weberit führe ich als Produktionsleiter zwei Fertigungsbereiche mit 38 Mitarbeitenden "
    "im Dreischichtbetrieb. Durch SMED-Workshops steigerte ich die Termintreue von 87 % auf 96 %."
)
_LANGUAGE = (
    "Deutsch ist meine Muttersprache; außerdem begleite ich Werksbesuche des französischen "
    "Mutterkonzerns auf Englisch und präsentiere Quartalszahlen auf Englisch."
)
_CLOSING = (
    "Gern erläutere ich Ihnen im persönlichen Gespräch, wie ich Rheinwerk unterstützen kann. "
    "Über Ihre Einladung freue ich mich; mein Eintrittstermin kann besprochen werden."
)


def _letter(paragraphs, *, closing="Mit freundlichen Grüßen", name="Max Prober"):
    return {
        "recipient": {"company": "Rheinwerk"},
        "body": {"paragraphs": list(paragraphs)},
        "signature": {"closing": closing, "name": name},
    }


_REVIEWED = _letter([_SALUTATION, _OPENING, _BODY, _LANGUAGE, _CLOSING])


# --- the fact -----------------------------------------------------------------


def test_an_unchanged_structure_has_no_defect():
    edited = _letter([
        _SALUTATION, _OPENING, _BODY + " Zudem führte ich KVP-Routinen ein.", _LANGUAGE,
        _CLOSING + " Ich freue mich auf Ihre Rückmeldung.",
    ])
    assert terminal_patch_structure_defects(_REVIEWED, edited) == []


def test_the_logged_luna_merge_shape_is_two_defects():
    """2026-08-15.jsonl, cover_letter_condense attempt 2 (luna): the language
    paragraph's content went INTO the closing paragraph and the sign-off became the
    last body paragraph."""
    merged = _letter([
        _SALUTATION, _OPENING, _BODY, _LANGUAGE + " " + _CLOSING, "Mit freundlichen Grüßen",
    ])
    assert terminal_patch_structure_defects(_REVIEWED, merged) == ["signature", "closing_merged"]


def test_body_content_prepended_to_the_closing_is_a_merge():
    merged = _letter([
        _SALUTATION, _OPENING, _BODY,
        "Bei Weberit verantwortete ich außerdem ein MES-Projekt. " + _CLOSING,
    ])
    assert "closing_merged" in terminal_patch_structure_defects(_REVIEWED, merged)


def test_a_rewritten_closing_is_not_claimed_as_a_merge():
    """Fail-open: the corrector rewrote the closing — no sentence of the patch is
    the reviewed opener edited, so no merge can be proven."""
    rewritten = _letter([
        _SALUTATION, _OPENING, _BODY, _LANGUAGE,
        "Ich würde mich sehr freuen, Sie in einem Vorstellungsgespräch kennenzulernen, "
        "und stehe ab dem nächsten Quartal zur Verfügung.",
    ])
    assert terminal_patch_structure_defects(_REVIEWED, rewritten) == []


def test_a_body_collapsed_below_three_paragraphs_is_a_merge():
    collapsed = _letter([_SALUTATION, _OPENING + " " + _BODY + " " + _CLOSING])
    assert "closing_merged" in terminal_patch_structure_defects(_REVIEWED, collapsed)


def test_a_signoff_closing_a_prose_paragraph_is_a_signature_defect():
    signed = _letter([
        _SALUTATION, _OPENING, _BODY, _LANGUAGE, _CLOSING + " Mit freundlichen Grüßen Max Prober",
    ])
    assert "signature" in terminal_patch_structure_defects(_REVIEWED, signed)


def test_a_changed_signature_name_is_a_signature_defect():
    renamed = _letter([_SALUTATION, _OPENING, _BODY, _LANGUAGE, _CLOSING], name="M. Prober")
    assert terminal_patch_structure_defects(_REVIEWED, renamed) == ["signature"]


def test_a_salutation_run_into_the_opener_is_a_defect():
    inline = _letter([_SALUTATION + " " + _OPENING, _BODY, _LANGUAGE, _CLOSING])
    assert "salutation" in terminal_patch_structure_defects(_REVIEWED, inline)


def test_a_lost_closing_is_a_defect():
    stub = _letter([_SALUTATION, _OPENING, _BODY, _LANGUAGE, "Kündigungsfrist besprechbar."])
    assert "closing_lost" in terminal_patch_structure_defects(_REVIEWED, stub)


def test_a_defect_the_reviewed_draft_already_had_is_not_the_patchs():
    already = _letter([_SALUTATION, _OPENING, _BODY, _LANGUAGE + " " + _CLOSING])
    again = _letter([_SALUTATION, _OPENING, _BODY + " Zudem KVP.", _LANGUAGE + " " + _CLOSING])
    # The reviewed closing opener IS "Deutsch ist …" now — kept at index 0.
    assert terminal_patch_structure_defects(already, again) == []



# ── harness (copied from tests/unit/test_letter_terminal_review.py — test modules
#    are not importable across files here) ──

def _profile_json() -> dict:
    return {
        "personal_info": {"name": "Max Prober", "email": "max@example.com"},
        "work_experience": [
            {
                "id": "w-alpha",
                "company": "Alpha Systems GmbH",
                "role": "Principal Platform Engineer",
                "responsibilities": [
                    "Built an internal LLM-assisted document classification "
                    "service, targeting a 60% reduction in manual processing "
                    "time."
                ],
                "achievements": [
                    "Documents pre-classified by the service passed the very "
                    "first review round in most cases, confirming the 60% "
                    "reduction target is conservative."
                ],
            },
        ],
        "skills": [],
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
    from applire.models.cover_letter import CoverLetterStatus, GeneratedCoverLetter
    from applire.models.job import JobAnalysis
    from applire.models.user import User

    user = User(id=uuid.uuid4(), email="letter-terminal@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(),
        raw_text_hash="letterpatch784",
        raw_text="Platform Engineer at Vector Analytics",
        role_title="Platform Engineer",
        company_name="Vector Analytics",
        required_skills=[],
        nice_to_have_skills=[],
        keywords=[],
        seniority_level="senior",
        company_culture_signals=[],
        language_requirement="en",
        jd_language="en",
    )
    profile = make_master_profile(profile_json=_profile_json())
    db.add_all([user, job, profile])
    await db.flush()
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id,
        profile_id=profile.id,
        template="classic_german",
        letter_data={},
        pre_gen_inputs={},
        status=CoverLetterStatus.pending.value,
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    return job, profile, cl


def _fake_review(script, captured):
    """Chain-dispatching review_and_refine stub. Non-terminal chains settle the
    draft untouched. Terminal chains run the reviewer_prompt_fn (so the
    subject-building closure executes, exactly like the real loop's first
    reviewer call) and then apply the next scripted corrector action, if any."""
    async def fake(**kwargs):
        if kwargs.get("chain_id") != "letter_terminal_review":
            return kwargs["draft"]
        captured.append({
            "draft": kwargs["draft"],
            "prompt": kwargs["reviewer_prompt_fn"](kwargs["source"], kwargs["draft"]),
            "system": kwargs["reviewer_system"],
            "retain_if": kwargs.get("retain_if"),
            "prefer_if": kwargs.get("prefer_if"),
            "load_bearing_fn": kwargs.get("load_bearing_fn"),
        })
        action = script.pop(0) if script else None
        return action(kwargs["draft"]) if action else kwargs["draft"]
    return fake


async def _run_pipeline(db, ids, *, script=None, captured=None, extra_patches=(),
                        review_retries=2, payload=None, pages=None):
    """Drive _render_cover_letter_background end to end.

    ``pages``: queue of page counts the measure yields, one per render
    (defaults to 1 forever — no overflow). The condense generation is routed
    by its own prompt marker, so provider call order never breaks a test.
    """
    from applire.services.cover_letter import _render_cover_letter_background

    job, profile, cl = ids
    captured = captured if captured is not None else []
    pages_queue = list(pages or [])
    calls = {"writer": 0, "condense": 0}

    async def _aparse(prompt, system=None, **kw):
        if "=== CURRENT LETTER (JSON) ===" in prompt:
            calls["condense"] += 1
            return _writer_payload_de()
        calls["writer"] += 1
        return payload if payload is not None else _writer_payload()

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)
    extract = MagicMock(
        side_effect=lambda pdf: ("text", pages_queue.pop(0) if pages_queue else 1)
    )

    patches = [
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch("applire.services.cover_letter.review_and_refine",
              side_effect=_fake_review(script or [], captured)),
        patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", review_retries),
        patch("applire.services.cover_letter_pdf.render_pdf",
              AsyncMock(return_value=b"%PDF-fake")),
        patch("applire.services.ats_audit.extract_text_and_pages", new=extract),
    ]
    # The audit tail is measurement-only by contract and heavyweight — stub it
    # unless a test injects its own (mutating) stand-in via extra_patches.
    if not any("update_ats_report_letter" in str(getattr(p, "attribute", "") or p)
               for p in extra_patches):
        patches.append(
            patch("applire.services.cover_letter._update_ats_report_letter",
                  new=AsyncMock())
        )
    with patch("applire.services.cover_letter.AsyncSessionLocal") as sl:
        sl.return_value.__aenter__.return_value = db
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            for p in extra_patches:
                stack.enter_context(p)
            await _render_cover_letter_background(
                cl_id=cl.id, cv_id=None, job_id=job.id
            )
    return captured, calls


def _identity_lines(caplog):
    return [
        r for r in caplog.records
        if "REVIEW_SUBJECT_IDENTITY" in r.getMessage() and "cl_id=" in r.getMessage()
    ]



# --- seam 1: the re-entry bound of the terminal loop (v1 and v2) -------------


def _writer_payload_de() -> dict:
    return {
        "header": {"name": "Max Prober"},
        "recipient": {"name": None, "company": None, "date": None},
        "body": {"paragraphs": [_SALUTATION, _OPENING, _BODY, _LANGUAGE, _CLOSING]},
        "signature": {"closing": None, "name": None},
    }


def _edit_body(draft):
    body = dict(draft["body"])
    paragraphs = list(body["paragraphs"])
    paragraphs[2] = paragraphs[2] + " Zudem führte ich KVP-Routinen ein."
    body["paragraphs"] = paragraphs
    return {**draft, "body": body}


def _merge_into_closing(draft):
    body = dict(draft["body"])
    p = list(body["paragraphs"])
    body["paragraphs"] = p[:-2] + [p[-2] + " " + p[-1]]
    return {**draft, "body": body}


def _append_closing_sentence(draft):
    body = dict(draft["body"])
    p = list(body["paragraphs"])
    body["paragraphs"] = p[:-1] + [p[-1] + " Ich freue mich auf Ihre Rückmeldung."]
    return {**draft, "body": body}


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
async def test_an_unreviewed_merging_patch_ships_the_reviewed_draft(db, caplog, monkeypatch, pipeline):
    monkeypatch.setenv("LETTER_PIPELINE", pipeline)
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)
    captured, _ = await _run_pipeline(
        db, ids, script=[_edit_body, _merge_into_closing], payload=_writer_payload_de()
    )
    assert len(captured) == 2

    from applire.models.cover_letter import GeneratedCoverLetter

    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    paragraphs = cl.letter_data["body"]["paragraphs"]
    assert paragraphs[-1].startswith("Gern erläutere ich"), "the reviewed closing ships alone"
    assert any("KVP-Routinen" in p for p in paragraphs), "the REVIEWED round-1 patch ships"
    rejected = [r for r in caplog.records if "LETTER_TERMINAL_PATCH_REJECTED" in r.getMessage()]
    assert len(rejected) == 1 and "site=reentry_bound" in rejected[0].getMessage()
    assert "closing_merged" in rejected[0].getMessage()
    lines = _identity_lines(caplog)
    assert lines and "match=True" in lines[-1].getMessage()
    assert "reentry_exhausted=False" in lines[-1].getMessage(), \
        "the delivered letter WAS reviewed — not an unreviewed ship"


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
async def test_an_unreviewed_well_formed_patch_still_ships(db, caplog, monkeypatch, pipeline):
    """The check is not a gate on every unreviewed patch — a structurally sound
    one ships as before (ship-and-report, ADR-076 clause 3)."""
    monkeypatch.setenv("LETTER_PIPELINE", pipeline)
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)
    await _run_pipeline(
        db, ids, script=[_edit_body, _append_closing_sentence], payload=_writer_payload_de()
    )
    from applire.models.cover_letter import GeneratedCoverLetter

    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.letter_data["body"]["paragraphs"][-1].endswith("Ich freue mich auf Ihre Rückmeldung.")
    assert not [r for r in caplog.records if "LETTER_TERMINAL_PATCH_REJECTED" in r.getMessage()]
    assert "reentry_exhausted=True" in _identity_lines(caplog)[-1].getMessage()


@pytest.mark.asyncio
async def test_a_merging_patch_in_a_reviewed_round_is_left_to_the_next_review(db, caplog, monkeypatch):
    """Scope: only the patch that would ship UNREVIEWED is checked. A first-round
    patch re-enters review (the reviewer reads it) and is not rejected here."""
    monkeypatch.setenv("LETTER_PIPELINE", "v2")
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)
    captured, _ = await _run_pipeline(
        db, ids, script=[_merge_into_closing], payload=_writer_payload_de()
    )
    assert len(captured) == 2, "the round-1 patch re-entered review"
    assert not [r for r in caplog.records if "LETTER_TERMINAL_PATCH_REJECTED" in r.getMessage()]
