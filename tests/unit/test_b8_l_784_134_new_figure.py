# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""784:134 / 784:82 (Hazelnut build 8, WP-L; ADR-076 cl. 3 amended 2026-11-10) —
a figure no source states may not reach the letter through a step no verdict read.

Two layers:

* the FACT ``introduced_unsourced_figures`` on hand-built letters — the shapes the
  captured terminal segments produced (all Ministral: a latency "92 %", an LTIF
  written as a percentage, a "pro 100.000 Arbeitsstunden" base; synthetic personas,
  re-worded here) and the forms a source states a figure in that must not fire;
* one seam test per call site: the blocking v2 terminal check, the W3-3a
  post-condense review-only round (the row fails), the re-entry bound's
  unreviewed patch (v1 and v2, fail-closed to the reviewed draft), and the
  delivered letter's code note.
"""
import copy
import logging
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.support.profile_factory import make_master_profile

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.services.letter_figure_guard import introduced_unsourced_figures  # noqa: E402


def _l(*paragraphs):
    return {"body": {"paragraphs": list(paragraphs)}}


# ── the fact ─────────────────────────────────────────────────────────────────

_REF_DE = _l("Ich senkte die Unfallquote (LTIF) von 8,2 auf 3,1 und die Ausschussquote von 4,1 % auf 2,3 %.")
_SRC_DE = "Achievements: LTIF von 8,2 auf 3,1 gesenkt; Ausschussquote 4,1 % -> 2,3 %. ISO 9001 seit 9 Jahren."


@pytest.mark.parametrize("draft,expected", [
    # an LTIF the source states as a rate, written as a percentage
    (_l("Ich senkte die Unfallquote (LTIF) von 8,2 % auf 3,1 % und die Ausschussquote von 4,1 % auf 2,3 %."),
     {"8,2 %", "3,1 %"}),
    # a base the source never names
    (_l("Ich senkte die Unfallquote von 8,2 auf 3,1 pro 100.000 Arbeitsstunden."), {"100.000"}),
    # a result nobody recorded
    (_l("Ich senkte die Ausschussquote von 4,1 % auf 2,3 % und die Durchlaufzeit um 92 %."), {"92 %"}),
    # a duration is a figure too: the source says nine years, the step says ten
    (_l("Ich bringe zehn Jahre ISO-9001-Praxis mit."), {"zehn"}),
])
def test_an_introduced_unsourced_figure_is_found(draft, expected):
    found = introduced_unsourced_figures(_REF_DE, draft, [_SRC_DE])
    assert {f["raw"] for f in found} == expected
    assert all(f["sentence"] and f["paragraph_index"] == 0 for f in found)


@pytest.mark.parametrize("reference,draft,source", [
    # carried by the reference draft (the drafting loop reviewed it)
    (_l("Ich führte 38 Mitarbeitende."), _l("Ich führte als Leiter 38 Mitarbeitende."), ""),
    # the source states it in another form: a multiplier, a separator, a decimal comma
    (_l(""), _l("serving about 40,000 daily active customers"), "serving ~40k daily active customers"),
    (_l(""), _l("a budget of 1.2 million euros"), "Budget 1,2 Mio. EUR"),
    (_l(""), _l("about 200,000 invoices per month"), "200.000 Rechnungen pro Monat"),
    # a unit glued to the number in the source ("1.8s", "14x")
    (_l(""), _l("the p95 latency fell to 1.8 seconds"), "p95 latency: 1.8s"),
    (_l(""), _l("deploys ran 14 times faster"), "deploys 14x faster"),
    # a number may come from a percentage (not the other way round)
    (_l(""), _l("die Termintreue stieg um 9 Punkte"), "Termintreue +9 %"),
    # the source states the duration
    (_l(""), _l("neun Jahre ISO-9001-Praxis"), "ISO 9001: über 9 Jahre"),
    # not a figure: an identifier, a calendar year, a bare scale word
    (_l(""), _l("Ich bewertete IFRS 16 nach ISO 9001."), ""),
    (_l(""), _l("Seit 2019 verantworte ich den Abschluss."), ""),
    (_l(""), _l("ein Budget von 6 Millionen Euro"), "Budget: 6 Mio. EUR"),
])
def test_a_sourced_or_carried_figure_is_not(reference, draft, source):
    assert introduced_unsourced_figures(reference, draft, [source]) == []


def test_a_percent_needs_a_percent_in_the_source():
    """The LTIF shape: the bare rate 8,2 does not source "8,2 %"."""
    found = introduced_unsourced_figures(_l(""), _l("LTIF von 8,2 %"), ["LTIF 8,2"])
    assert [f["raw"] for f in found] == ["8,2 %"]


def test_one_entry_per_value_and_malformed_inputs_are_empty():
    found = introduced_unsourced_figures(_l(""), _l("um 37 % und erneut um 37 %."), [""])
    assert [f["raw"] for f in found] == ["37 %"]
    assert introduced_unsourced_figures(None, None, None) == []
    assert introduced_unsourced_figures({"body": "x"}, {"body": {"paragraphs": [None, 3]}}, [None]) == []


# ── seams: the real render path ──────────────────────────────────────────────

_BODY = [
    "Dear Sir or Madam,",
    "I am applying for the Senior Controller role at Arnold Antriebstechnik GmbH because "
    "the link between production controlling and decision support is where I work best.",
    "At Schwarzwald Präzision GmbH I have owned the monthly and annual closing under HGB "
    "since 2019, including the coordination with the auditors.",
    "I would welcome the opportunity to discuss my experience with you in an interview "
    "and look forward to hearing from you.",
]

_PROFILE_JSON = {
    "personal_info": {"name": "Katrin Hoffmann", "email": "katrin.hoffmann@example.com",
                      "location": "Stuttgart"},
    "work_experience": [{
        "id": "11111111-1111-4111-8111-111111111111",
        "company": "Schwarzwald Präzision GmbH",
        "role": "Financial Controller",
        "start_date": "2019-01",
        "end_date": None,
        "achievements": [
            "Monats- und Jahresabschlüsse nach HGB seit 2019.",
            "Verkürzte den Monatsabschluss von 8 auf 5 Arbeitstage.",
        ],
    }],
    "skills": [],
    "metadata": {"denied_concepts": []},
}

_MINTED = " This cut the closing cycle by 37%."
_SOURCED = " I shortened the monthly close from 8 to 5 working days."


def _draft(body):
    return {
        "header": {"name": "Katrin Hoffmann"},
        "recipient": {"name": None, "title": None, "company": "Arnold Antriebstechnik GmbH",
                      "address": None, "date": None},
        "body": {"paragraphs": list(body)},
        "signature": {"closing": "Mit freundlichen Grüßen", "name": "Katrin Hoffmann"},
    }


def _append(text):
    def fn(draft):
        d = copy.deepcopy(draft)
        d["body"]["paragraphs"][2] = d["body"]["paragraphs"][2] + text
        return d
    return fn


@pytest.fixture(autouse=True)
def _no_shared_context(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTEXT", "off")


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

    user = User(id=uuid.uuid4(), email="letter-new-figure@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash="b8l784134", raw_text="Senior Controller at Arnold",
        role_title="Senior Controller", company_name="Arnold Antriebstechnik GmbH",
        required_skills=[], nice_to_have_skills=[], keywords=[], seniority_level="senior",
        company_culture_signals=[], language_requirement="en", jd_language="en",
    )
    profile = make_master_profile(profile_json=copy.deepcopy(_PROFILE_JSON))
    db.add_all([user, job, profile])
    await db.flush()
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id, profile_id=profile.id, template="classic_german",
        letter_data={}, pre_gen_inputs={}, status=CoverLetterStatus.pending.value,
        document_language="en",
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    return job, profile, cl


async def _run(db, ids, *, terminal_script=(), condense_payload=None, pages_fn=None):
    from applire.services.cover_letter import _render_cover_letter_background

    job, _profile, cl = ids
    record = {"systems": [], "loops": [], "audit": None}
    script = list(terminal_script)
    writer = _draft(_BODY)

    async def _aparse(prompt, system=None, **kw):
        record["systems"].append((prompt, system))
        from applire.prompts.review_cover_letter import (
            TERMINAL_REVIEW_SYSTEM_PROMPT,
            V2_TERMINAL_REVIEW_SYSTEM_PROMPT,
        )
        if system in (TERMINAL_REVIEW_SYSTEM_PROMPT, V2_TERMINAL_REVIEW_SYSTEM_PROMPT):
            return {"approved": True, "issues": []}
        if condense_payload is not None and "=== CURRENT LETTER (JSON) ===" in prompt:
            return copy.deepcopy(condense_payload)
        return copy.deepcopy(writer)

    async def _fake_review(**kw):
        entry = dict(kw)
        chk = kw.get("checks_fn")
        entry["checks"] = list(chk(kw["draft"])) if chk else []
        kw["reviewer_prompt_fn"](kw["source"], kw["draft"])
        record["loops"].append(entry)
        if kw.get("chain_id") == "letter_terminal_review" and script:
            return script.pop(0)(kw["draft"])
        return kw["draft"]

    async def _audit(cl_, db_, **kw):
        record["audit"] = kw

    provider = AsyncMock()
    provider.aparse_json = AsyncMock(side_effect=_aparse)
    with (
        patch("applire.services.cover_letter.AsyncSessionLocal") as sl,
        patch("applire.services.cover_letter.get_provider", return_value=provider),
        patch("applire.services.cover_letter.review_and_refine", side_effect=_fake_review),
        patch("applire.services.cover_letter.LLM_REVIEW_MAX_RETRIES", 2),
        patch("applire.services.cover_letter_pdf.render_pdf", AsyncMock(return_value=b"%PDF")),
        patch("applire.services.ats_audit.extract_text_and_pages",
              new=MagicMock(side_effect=pages_fn or (lambda _pdf: ("text", 1)))),
        patch("applire.services.cover_letter._update_ats_report_letter", side_effect=_audit),
    ):
        sl.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)
    return record


def _terminal(record):
    return [entry for entry in record["loops"] if entry["chain_id"] == "letter_terminal_review"]


async def _delivered(db, ids):
    from applire.models.cover_letter import GeneratedCoverLetter

    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    return cl.letter_data["body"]["paragraphs"]


@pytest.mark.asyncio
async def test_seam_v2_terminal_round_carries_the_new_figure_as_blocking(db, monkeypatch):
    monkeypatch.setenv("LETTER_PIPELINE", "v2")
    rec = await _run(db, await _seed(db), terminal_script=[_append(_MINTED)])
    first, second = _terminal(rec)[:2]
    assert not [f for f in first["checks"] if f.check == "letter.new_figure"]
    found = [f for f in second["checks"] if f.check == "letter.new_figure"]
    assert len(found) == 1 and found[0].blocking and found[0].subjects == ("37%",)
    assert "no source states" in found[0].text


@pytest.mark.asyncio
async def test_seam_v2_delivered_letter_names_the_new_figure(db, monkeypatch):
    """The fake loop leaves the figure standing; the letter ships unrewritten
    (ADR-082/094 cl. 1) and the terminal-review check names it."""
    monkeypatch.setenv("LETTER_PIPELINE", "v2")
    ids = await _seed(db)
    rec = await _run(db, ids, terminal_script=[_append(_MINTED)])
    assert (await _delivered(db, ids))[2].endswith(_MINTED)
    notes = rec["audit"]["terminal_review"].notes
    code = [n for n in notes if n.kind == "code" and n.key == "letter.new_figure"]
    assert len(code) == 1 and code[0].subjects == ("37%",)


@pytest.mark.asyncio
async def test_seam_v2_a_sourced_figure_is_no_finding(db, monkeypatch):
    monkeypatch.setenv("LETTER_PIPELINE", "v2")
    rec = await _run(db, await _seed(db), terminal_script=[_append(_SOURCED)])
    assert not [f for e in _terminal(rec) for f in e["checks"] if f.check == "letter.new_figure"]
    assert not [n for n in rec["audit"]["terminal_review"].notes if n.key == "letter.new_figure"] \
        if rec["audit"]["terminal_review"] is not None else True


@pytest.mark.asyncio
async def test_seam_v2_post_condense_review_only_round_fails_on_a_new_figure(db, monkeypatch):
    """784:82: the condense mints a figure; RULING W3-3a reviews the condensed letter
    without a corrector, and W3-3c lets only code facts fail the row — this is one."""
    monkeypatch.setenv("LETTER_PIPELINE", "v2")
    condensed = _draft(_BODY)
    condensed["body"]["paragraphs"][2] += _MINTED
    record_pages = {"n": 0}

    def pages(_pdf):
        # over the page until the post-loop condense has run, then it fits
        record_pages["n"] += 1
        return ("text", 1 if record_pages["n"] > 2 else 2)

    ids = await _seed(db)
    rec = await _run(db, ids, condense_payload=condensed, pages_fn=pages)
    outcome = rec["audit"]["terminal_review"]
    assert outcome.path == "review_only" and outcome.status == "fail"
    assert any(t.startswith("[letter.new_figure]") for t in outcome.blocking_issues)
    assert (await _delivered(db, ids))[2].endswith(_MINTED)  # never rewritten


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
async def test_seam_unreviewed_patch_with_a_new_figure_ships_the_reviewed_draft(db, caplog, monkeypatch, pipeline):
    monkeypatch.setenv("LETTER_PIPELINE", pipeline)
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)
    edit = " I also introduced a rolling forecast."
    await _run(db, ids, terminal_script=[_append(edit), _append(_MINTED)])
    body = await _delivered(db, ids)
    assert body[2].endswith(edit), "the REVIEWED round-1 patch ships"
    assert "37%" not in " ".join(body)
    rejected = [r.getMessage() for r in caplog.records if "LETTER_TERMINAL_PATCH_REJECTED" in r.getMessage()]
    assert len(rejected) == 1 and "site=reentry_bound" in rejected[0] and "new_figure" in rejected[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
async def test_seam_unreviewed_patch_with_a_sourced_figure_still_ships(db, caplog, monkeypatch, pipeline):
    monkeypatch.setenv("LETTER_PIPELINE", pipeline)
    caplog.set_level(logging.INFO, logger="applire.services.cover_letter")
    ids = await _seed(db)
    await _run(db, ids, terminal_script=[_append(" I also introduced a rolling forecast."), _append(_SOURCED)])
    assert (await _delivered(db, ids))[2].endswith(_SOURCED)
    assert not [r for r in caplog.records if "LETTER_TERMINAL_PATCH_REJECTED" in r.getMessage()]
