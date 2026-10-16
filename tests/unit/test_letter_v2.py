# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Letter pipeline v2 (ADR-094 clause L, US369 / #779, US363 rulings W2-1/W2-2/W2-1b/W2-3).

The model writes, code checks. Pinned here:

* the ``LETTER_PIPELINE`` switch (default v1) and v1's byte-identity where v2 forks
  (writer system prompt, condense prompt, compose tail, letter ATS report);
* the v2 writer system prompt names the document language FIRST and no longer asks
  for the code-owned fields;
* the letter-language FACT (THE per-item predicate ``item_language_mismatch``) as a
  reviewer block, a corrector issue and the ``document-language`` ATS row;
* the code-owned sender header from the vault (the weak tier invented a street
  address in 5/32 baseline letters);
* the v1 rewrite steps as v2 FINDINGS (salutation, sign-off, closing paragraph,
  figure ownership, invented limit) — nothing deleted, nothing inserted;
* the recorded alternate name forms (#709/#716 letter half) as source material;
* one seam test per v2 call site on the real render path.

Fixtures are synthetic (``controlling_emma_de``-shaped, no real person).
"""
import copy
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


# ── fixtures ─────────────────────────────────────────────────────────────────

_GERMAN_BODY = [
    "Sehr geehrte Damen und Herren,",
    "mit großem Interesse bewerbe ich mich bei der Arnold Antriebstechnik GmbH, denn "
    "die Verbindung von Produktionscontrolling und Entscheidungsunterstützung reizt mich.",
    "Bei der Schwarzwald Präzision GmbH verantworte ich seit 2019 die Monats- und "
    "Jahresabschlüsse nach HGB und die Abstimmung mit den Wirtschaftsprüfern.",
    "Ich freue mich auf die Gelegenheit, meine Erfahrung in einem persönlichen Gespräch "
    "zu erläutern, und stehe Ihnen gern für ein Gespräch zur Verfügung.",
]

_ENGLISH_BODY = [
    "Dear Sir or Madam,",
    "I am applying for the Senior Controller role at Arnold Antriebstechnik GmbH because "
    "the link between production controlling and decision support is where I work best.",
    "At Schwarzwald Präzision GmbH I have owned the monthly and annual closing under HGB "
    "since 2019, including the coordination with the auditors.",
    "I would welcome the opportunity to discuss my experience with you in an interview "
    "and look forward to hearing from you.",
]

_PROFILE_JSON = {
    "personal_info": {
        "name": "Katrin Hoffmann",
        "email": "katrin.hoffmann@example.com",
        "phone": "+49 711 0000000",
        "address": None,
        "location": "Stuttgart",
    },
    "work_experience": [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "company": "Schwarzwald Präzision GmbH",
            "company_aliases": ["Schwarzwald Precision"],
            "role": "Financial Controller",
            "role_aliases": ["Finanzcontrollerin"],
            "start_date": "2019-01",
            "end_date": None,
            "achievements": ["Monats- und Jahresabschlüsse nach HGB seit 2019."],
        }
    ],
    "education": [
        {
            "institution": "Universität Stuttgart",
            "institution_aliases": ["University of Stuttgart"],
            "degree": "M.Sc. Betriebswirtschaftslehre",
        }
    ],
    "skills": [],
    "metadata": {"denied_concepts": []},
}


def _draft(body, **over):
    d = {
        "header": {
            "name": "Katrin Hofman",  # the model's misspelling
            "address": "Musterstraße 123, 70563 Stuttgart",  # invented (baseline, 5/32)
            "phone": "+49 711 9999999",
            "email": "k.hoffmann@example.org",
        },
        "recipient": {"name": None, "title": None, "company": "Arnold Antriebstechnik GmbH",
                      "address": None, "date": None},
        "body": {"paragraphs": list(body)},
        "signature": {"closing": "Mit freundlichen Grüßen", "name": "Katrin Hofman"},
    }
    d.update(over)
    return d


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setenv("LETTER_PIPELINE", "v2")


@pytest.fixture
def v1(monkeypatch):
    monkeypatch.delenv("LETTER_PIPELINE", raising=False)


# ── 1. the switch ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    (None, "v1"), ("v1", "v1"), ("v2", "v2"), (" V2 ", "v2"), ("v3", "v1"), ("", "v1"),
])
def test_letter_pipeline_switch(monkeypatch, raw, expected):
    from applire.services.letter_v2 import letter_pipeline

    if raw is None:
        monkeypatch.delenv("LETTER_PIPELINE", raising=False)
    else:
        monkeypatch.setenv("LETTER_PIPELINE", raw)
    assert letter_pipeline() == expected


def test_letter_pipeline_is_declared_but_withheld_from_env_example():
    from applire.settings_registry import get

    entry = get("LETTER_PIPELINE")
    assert entry.default == "v1"
    assert entry.in_env_example is False


# ── 2. the writer system prompt ──────────────────────────────────────────────


def test_v1_system_prompt_is_the_unchanged_constant():
    from applire.prompts.cover_letter import SYSTEM_PROMPT, build_system_prompt

    assert build_system_prompt("en") is SYSTEM_PROMPT
    assert build_system_prompt("de", pipeline="v1") is SYSTEM_PROMPT


@pytest.mark.parametrize("lang,name", [("en", "ENGLISH"), ("de", "GERMAN (Deutsch)")])
def test_v2_system_prompt_names_the_document_language_first(lang, name):
    from applire.prompts.cover_letter import build_system_prompt

    prompt = build_system_prompt(lang, pipeline="v2")
    first = prompt.splitlines()[0]
    assert f"document language is {name}" in first
    assert "German cover letter" not in prompt
    assert "Bewerbungsschreiben (German" not in prompt


def test_v2_schema_does_not_ask_for_code_owned_fields():
    from applire.prompts.cover_letter import build_system_prompt

    prompt = build_system_prompt("en", pipeline="v2")
    schema = prompt.split("Rules:")[0]
    assert '"header"' not in schema
    assert '"signature"' not in schema
    assert '"date"' not in schema
    assert '"paragraphs"' in schema and '"recipient"' in schema


def test_v2_titles_rule_allows_recorded_alternate_forms_only():
    from applire.prompts.cover_letter import SYSTEM_PROMPT, build_system_prompt

    v2 = build_system_prompt("en", pipeline="v2")
    assert "recorded alternate" in v2
    assert "never translate, shorten, generalise, upgrade or downgrade it yourself" in v2
    # v1's rules are carried otherwise unchanged
    assert v2.count("- GROUNDING CONTRACT") == 1
    assert SYSTEM_PROMPT.split("Rules:")[1].count("\n- ") == v2.split("Rules:")[1].count("\n- ")


def test_condense_prompt_names_the_language_on_v2_only():
    from applire.prompts.cover_letter import build_condense_prompt

    letter = _draft(_ENGLISH_BODY)
    v1 = build_condense_prompt(letter, 300, 2, letter_pages=1)
    assert "the same language, tone" in v1
    v2 = build_condense_prompt(letter, 300, 2, letter_pages=1, language="en")
    assert "Write every sentence in ENGLISH." in v2
    assert "the same language" not in v2


def test_user_prompt_carries_recorded_name_forms_only_when_given():
    from applire.prompts.cover_letter import build_cover_letter_prompt

    base = dict(cv_data={}, jd_text="Senior Controller", pre_gen_inputs={}, detected_language="en")
    assert "RECORDED NAME FORMS" not in build_cover_letter_prompt(**base)
    assert build_cover_letter_prompt(**base) == build_cover_letter_prompt(
        **base, recorded_name_forms_block=None
    )
    with_block = build_cover_letter_prompt(**base, recorded_name_forms_block="RECORDED NAME FORMS x")
    assert "RECORDED NAME FORMS x" in with_block


# ── 3. the letter-language fact ──────────────────────────────────────────────


def test_language_fact_flags_a_german_letter_for_an_english_posting():
    from applire.services.letter_v2 import letter_language_fact

    fact = letter_language_fact(_draft(_GERMAN_BODY), "en")
    assert fact.majority_foreign
    # the German Anrede (5 words) is itself a foreign sentence in an English letter
    assert len(fact.foreign) == fact.checked == 4


def test_language_fact_is_clean_on_the_right_language():
    from applire.services.letter_v2 import letter_language_fact

    assert letter_language_fact(_draft(_ENGLISH_BODY), "en").clean
    assert letter_language_fact(_draft(_GERMAN_BODY), "de").clean


def test_language_fact_names_one_stray_sentence_without_calling_the_letter_foreign():
    from applire.services.letter_v2 import letter_language_fact

    body = list(_ENGLISH_BODY)
    body[2] = body[2] + " Zudem verantworte ich die Abstimmung mit den Wirtschaftsprüfern und der Leitung."
    fact = letter_language_fact(_draft(body), "en")
    assert len(fact.foreign) == 1 and not fact.majority_foreign
    assert fact.foreign[0].paragraph_index == 2


@pytest.mark.parametrize("bad", [None, {}, {"body": None}, {"body": {"paragraphs": "x"}},
                                 {"body": {"paragraphs": [None, 3]}}])
def test_language_fact_tolerates_malformed_shapes(bad):
    from applire.services.letter_v2 import letter_language_fact

    fact = letter_language_fact(bad, "en")
    assert fact.clean and fact.checked == 0


def test_language_block_and_issue():
    from applire.services.letter_v2 import (
        letter_language_fact,
        letter_language_issues_fn,
        render_letter_language_block,
    )

    assert render_letter_language_block(letter_language_fact(_draft(_ENGLISH_BODY), "en")) == ""
    block = render_letter_language_block(letter_language_fact(_draft(_GERMAN_BODY), "en"))
    assert "LETTER LANGUAGE CHECK" in block and "approved=false" in block
    issues = letter_language_issues_fn("en")(_draft(_GERMAN_BODY))
    assert len(issues) == 1 and issues[0].is_blocking
    assert "whole letter is not written in ENGLISH" in issues[0].text
    assert letter_language_issues_fn("en")(_draft(_ENGLISH_BODY)) == []


def test_language_fact_is_the_shared_predicate():
    """ADR-066: no second detector. Mutating THE predicate must move the fact."""
    from applire.services import letter_v2

    with patch("applire.utils.language_detection.item_language_mismatch", return_value=False):
        assert letter_v2.letter_language_fact(_draft(_GERMAN_BODY), "en").clean


# ── 4. the ATS row (ruling W2-1b: red in the pre-download review, never a block)


def _letter_report(letter, language):
    from applire.services.ats_audit import _audit_letter_text

    text = " ".join(letter["body"]["paragraphs"]) + " " + letter["header"]["name"]
    return _audit_letter_text(text, letter, [], document_language=language)


def test_letter_report_has_no_language_row_without_a_language():
    from applire.services.ats_audit import _audit_letter_text

    letter = _draft(_GERMAN_BODY)
    report = _audit_letter_text(" ".join(letter["body"]["paragraphs"]), letter, [])
    assert "document-language" not in {c.id for c in report.checks}


def test_letter_report_language_row_fails_with_the_review_surface_key():
    report = _letter_report(_draft(_GERMAN_BODY), "en")
    row = next(c for c in report.checks if c.id == "document-language")
    assert row.status == "fail"
    assert row.details_key == "document-language-mixed"
    assert row.details_params["count"] == 4 and row.details_params["language"] == "en"


def test_letter_report_language_row_passes_on_a_clean_letter():
    report = _letter_report(_draft(_ENGLISH_BODY), "en")
    row = next(c for c in report.checks if c.id == "document-language")
    assert row.status == "pass"


# ── 5. code-owned fields ─────────────────────────────────────────────────────


def test_code_owned_header_comes_from_the_vault_never_the_model():
    from applire.services.letter_v2 import apply_code_owned_fields

    out = apply_code_owned_fields(_draft(_ENGLISH_BODY), profile_json=_PROFILE_JSON, cv_data={})
    assert out["header"] == {
        "name": "Katrin Hoffmann", "address": "Stuttgart",
        "phone": "+49 711 0000000", "email": "katrin.hoffmann@example.com",
    }
    assert out["signature"]["name"] == "Katrin Hoffmann"


def test_code_owned_header_drops_a_model_value_the_vault_does_not_record():
    from applire.services.letter_v2 import apply_code_owned_fields

    pj = copy.deepcopy(_PROFILE_JSON)
    pj["personal_info"]["phone"] = None
    out = apply_code_owned_fields(_draft(_ENGLISH_BODY), profile_json=pj, cv_data={})
    assert out["header"]["phone"] is None


def test_code_owned_header_falls_back_to_the_cv_contact():
    from applire.services.letter_v2 import vault_sender

    sender = vault_sender({}, {"contact": {"name": "A B", "location": "Bonn", "email": "a@b.c"}})
    assert sender == {"name": "A B", "address": "Bonn", "phone": None, "email": "a@b.c"}


# ── 6. compose: v1 rewrites, v2 only sets code-owned fields ──────────────────


def _compose(letter, pipeline):
    from types import SimpleNamespace

    from applire.services.cover_letter import _compose_letter

    return _compose_letter(
        copy.deepcopy(letter), profile_json=_PROFILE_JSON, cv_data={},
        profile=SimpleNamespace(profile_json=_PROFILE_JSON), pre_gen={},
        language="en", pipeline=pipeline,
    )


def test_v2_compose_changes_no_body_text():
    body = ["I am applying for the Senior Controller role at Arnold Antriebstechnik GmbH "
            "because the link between controlling and decision support is where I work best.",
            *_ENGLISH_BODY[2:], "Kind regards"]
    letter = _draft(body)
    v2 = _compose(letter, "v2")
    assert v2["body"]["paragraphs"] == body  # no Anrede injected, no sign-off dropped
    v1 = _compose(letter, "v1")
    assert v1["body"]["paragraphs"][0] == "Dear Sir or Madam,"
    assert v1["body"]["paragraphs"][-1] != "Kind regards"


def test_v2_compose_sets_every_code_owned_field():
    out = _compose(_draft(_ENGLISH_BODY), "v2")
    assert out["header"]["address"] == "Stuttgart"
    assert out["signature"] == {"closing": "Kind regards", "name": "Katrin Hoffmann"}
    assert out["recipient"]["date"]


def test_v1_compose_keeps_the_model_header():
    out = _compose(_draft(_ENGLISH_BODY), "v1")
    assert out["header"]["address"] == "Musterstraße 123, 70563 Stuttgart"


# ── 7. the v1 rewrites as v2 findings ────────────────────────────────────────


def _findings(letter, **kw):
    from applire.services.cover_letter import _letter_v2_findings

    return _letter_v2_findings(
        letter, profile_json=kw.get("profile_json", _PROFILE_JSON),
        keyword_ledger=kw.get("ledger"), denied_concepts=kw.get("denied"),
    )


def test_no_finding_on_a_clean_letter():
    assert _findings(_compose(_draft(_ENGLISH_BODY), "v2")) == []


def test_missing_salutation_is_a_finding():
    f = _findings(_draft(_ENGLISH_BODY[1:]))
    assert [x.family for x in f] == ["salutation"]
    assert "no salutation" in f[0].text


def test_inline_salutation_is_a_finding():
    body = ["Dear Sir or Madam, " + _ENGLISH_BODY[1][0].lower() + _ENGLISH_BODY[1][1:],
            *_ENGLISH_BODY[2:]]
    f = _findings(_draft(body))
    assert [x.family for x in f] == ["salutation"]
    assert '"Dear Sir or Madam,"' in f[0].text


def test_trailing_signoff_is_a_finding():
    letter = _compose(_draft([*_ENGLISH_BODY, "Kind regards", "Katrin Hoffmann"]), "v2")
    f = _findings(letter)
    assert [x.family for x in f] == ["signoff"]


def test_missing_closing_paragraph_is_a_finding():
    f = _findings(_draft([*_ENGLISH_BODY[:-1], "Notice period: three months."]))
    assert "closing" in [x.family for x in f]


def test_invented_limit_is_a_finding_not_a_cut():
    ledger = [{"concept": "Observability", "surface_forms": [], "status": "claimable"}]
    body = list(_ENGLISH_BODY)
    body[2] = "I do not have Observability experience, but I have owned the HGB closing since 2019."
    letter = _draft(body)
    f = _findings(letter, ledger=ledger, denied=[])
    assert [x.family for x in f] == ["limit"]
    assert letter["body"]["paragraphs"][2] == body[2]  # untouched


def test_figure_finding_detects_without_deleting():
    from applire.services.letter_figure_guard import guard_letter_figures, letter_figure_findings

    pj = copy.deepcopy(_PROFILE_JSON)
    pj["work_experience"].append({
        "id": "22222222-2222-4222-8222-222222222222",
        "company": "Brauner & Söhne Maschinenbau GmbH", "role": "Junior Controllerin",
        "start_date": "2016-01", "end_date": "2018-12",
        "achievements": ["Investitionsrechnungen für 37 Maschinenbeschaffungen erstellt."],
    })
    body = list(_ENGLISH_BODY)
    body[2] = ("At Schwarzwald Präzision GmbH I prepared investment appraisals for 37 machine "
               "purchases.")
    letter = _draft(body)
    found = letter_figure_findings(letter, pj)
    assert len(found) == 1 and found[0]["raw"] == "37"
    assert found[0]["foreign_owner_labels"] == ["Brauner & Söhne Maschinenbau GmbH"]
    # same detection as the v1 floor, which DELETES the sentence
    assert body[2] not in guard_letter_figures(copy.deepcopy(letter), pj)["body"]["paragraphs"]
    assert letter["body"]["paragraphs"][2] == body[2]
    assert [x.family for x in _findings(letter, profile_json=pj)] == ["figure"]


def test_v2_signal_fn_caps_each_family_and_includes_language():
    from applire.services.cover_letter import _v2_signal_issues_fn

    fn = _v2_signal_issues_fn(
        lambda d: d, profile_json=_PROFILE_JSON, keyword_ledger=None,
        denied_concepts=None, language="en",
    )
    issues = fn(_draft(_GERMAN_BODY[1:]))  # German AND no salutation
    texts = [i.text for i in issues]
    assert all(i.is_blocking for i in issues)
    assert any(t.startswith("Letter language") for t in texts)
    assert any("no salutation" in t for t in texts)


# ── 8. recorded name forms (#709/#716 letter half, W1-2 / W2-3) ──────────────


def test_recorded_name_forms_lists_only_names_with_aliases():
    from applire.services.letter_v2 import recorded_name_forms, render_recorded_name_forms_block

    forms = recorded_name_forms(_PROFILE_JSON)
    kinds = {(f.kind, f.stored) for f in forms}
    assert kinds == {
        ("company", "Schwarzwald Präzision GmbH"),
        ("role", "Financial Controller"),
        ("institution", "Universität Stuttgart"),
    }
    block = render_recorded_name_forms_block(forms, "en")
    assert '"Financial Controller" | "Finanzcontrollerin"' in block
    assert "written in ENGLISH" in block
    assert render_recorded_name_forms_block(recorded_name_forms({"work_experience": [
        {"company": "X", "role": "Y"}]}), "en") == ""


# ── 9. seams: the real render path, one test per v2 call site ────────────────


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

    user = User(id=uuid.uuid4(), email="letter-v2@test.com")
    job = JobAnalysis(
        id=uuid.uuid4(), raw_text_hash="letterv2", raw_text="Senior Controller at Arnold",
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


async def _run(db, ids, *, payload, pages=1, terminal_script=None):
    """Drive the real render with the provider and review_and_refine faked; record
    every call's system prompt and every review_and_refine invocation's kwargs."""
    from applire.services.cover_letter import _render_cover_letter_background

    job, _profile, cl = ids
    record = {"systems": [], "loops": [], "audit": None}
    script = list(terminal_script or [])

    async def _aparse(prompt, system=None, **kw):
        record["systems"].append((prompt, system))
        return copy.deepcopy(payload)

    async def _fake_review(**kw):
        entry = dict(kw)
        entry["reviewer_prompt"] = kw["reviewer_prompt_fn"](kw["source"], kw["draft"])
        sig = kw.get("signal_issues_fn")
        entry["signal_issues"] = list(sig(kw["draft"])) if sig else []
        entry["corrector_prompt"] = kw["generator_prompt_fn"](kw["draft"], "fb", kw["source"])
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
              new=MagicMock(side_effect=lambda pdf: ("text", pages))),
        patch("applire.services.cover_letter._update_ats_report_letter", side_effect=_audit),
    ):
        sl.return_value.__aenter__.return_value = db
        await _render_cover_letter_background(cl_id=cl.id, cv_id=None, job_id=job.id)
    return record


@pytest.mark.asyncio
async def test_seam_v2_writer_gets_the_language_stating_system_prompt(db, v2):
    rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY))
    writer_prompt, writer_system = rec["systems"][0]
    assert writer_system.splitlines()[0].startswith(
        "You are an expert DACH career coach writing a cover letter whose document language is ENGLISH"
    )
    assert "RECORDED NAME FORMS" in writer_prompt


@pytest.mark.asyncio
async def test_seam_v1_writer_gets_the_unchanged_system_prompt(db, v1):
    from applire.prompts.cover_letter import SYSTEM_PROMPT

    rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY))
    writer_prompt, writer_system = rec["systems"][0]
    assert writer_system is SYSTEM_PROMPT
    assert "RECORDED NAME FORMS" not in writer_prompt


@pytest.mark.asyncio
async def test_seam_v2_drafting_loop_reads_the_code_owned_draft_and_the_facts(db, v2):
    rec = await _run(db, await _seed(db), payload=_draft(_GERMAN_BODY))
    drafting = next(l for l in rec["loops"] if l["chain_id"] == "cover_letter")
    # code-owned fields assembled BEFORE the first review round
    assert drafting["draft"]["header"]["address"] == "Stuttgart"
    assert drafting["draft"]["signature"]["name"] == "Katrin Hoffmann"
    # the language fact reaches the reviewer and the corrector
    assert "LETTER LANGUAGE CHECK" in drafting["reviewer_prompt"]
    assert any(i.text.startswith("Letter language") for i in drafting["signal_issues"])
    assert "DOCUMENT LANGUAGE: ENGLISH" in drafting["corrector_prompt"]
    # no draft selected by code (W2-1 B5/B6)
    assert drafting["retain_if"] is None and drafting["load_bearing_fn"] is None
    assert "RECORDED NAME FORMS" in drafting["source"]


@pytest.mark.asyncio
async def test_seam_v1_drafting_loop_is_unchanged(db, v1):
    from applire.services.cover_letter_positioning import has_closing_paragraph

    rec = await _run(db, await _seed(db), payload=_draft(_GERMAN_BODY))
    drafting = next(l for l in rec["loops"] if l["chain_id"] == "cover_letter")
    assert drafting["retain_if"] is has_closing_paragraph
    assert drafting.get("signal_issues_fn") is None
    assert "LETTER LANGUAGE CHECK" not in drafting["reviewer_prompt"]
    assert "DOCUMENT LANGUAGE:" not in drafting["corrector_prompt"]
    assert drafting["draft"]["header"]["address"] == "Musterstraße 123, 70563 Stuttgart"


@pytest.mark.asyncio
async def test_seam_v2_terminal_mount_reads_findings_and_selects_nothing(db, v2):
    rec = await _run(db, await _seed(db), payload=_draft(_GERMAN_BODY[1:]))
    terminal = [l for l in rec["loops"] if l["chain_id"] == "letter_terminal_review"]
    assert terminal
    t = terminal[0]
    assert t["retain_if"] is None and t["prefer_if"] is None and t["load_bearing_fn"] is None
    assert "LETTER FORMAT CHECK" in t["reviewer_prompt"]
    assert any("no salutation" in i.text for i in t["signal_issues"])


@pytest.mark.asyncio
async def test_seam_v2_delivers_unrepaired_text_with_a_report_note(db, v2):
    from applire.models.cover_letter import GeneratedCoverLetter

    ids = await _seed(db)
    rec = await _run(db, ids, payload=_draft(_GERMAN_BODY[1:]))
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready"
    assert cl.letter_data["body"]["paragraphs"] == _GERMAN_BODY[1:]  # nothing injected
    assert cl.letter_data["header"]["address"] == "Stuttgart"
    outcome = rec["audit"]["terminal_review"]
    notes = " ".join(outcome.notes) if outcome is not None else ""
    assert "[salutation]" in notes


@pytest.mark.asyncio
async def test_seam_v2_has_no_final_length_floor(db, v2):
    rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY), pages=2)
    stages_with_condense = [p for p, _s in rec["systems"] if "=== CURRENT LETTER (JSON) ===" in p]
    # ONE condense (pre-verdict, kept — B4), never the floor's second or third
    assert len(stages_with_condense) == 1
    condense_prompt, condense_system = next(
        (p, s) for p, s in rec["systems"] if "=== CURRENT LETTER (JSON) ===" in p
    )
    assert "Write every sentence in ENGLISH." in condense_prompt
    assert "document language is ENGLISH" in condense_system.splitlines()[0]


@pytest.mark.asyncio
async def test_seam_v1_still_runs_the_final_length_floor(db, v1):
    from applire.prompts.cover_letter import SYSTEM_PROMPT

    rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY), pages=2)
    condenses = [(p, s) for p, s in rec["systems"] if "=== CURRENT LETTER (JSON) ===" in p]
    assert len(condenses) >= 2
    assert all(s is SYSTEM_PROMPT for _p, s in condenses)
    assert all("the same language" in p for p, _s in condenses)


def test_v2_compose_survives_a_malformed_recipient_from_the_corrector():
    """v2 bench 2026-10-16: a terminal corrector returned ``"recipient": "recipient"``
    and the date stamp raised ('str' object does not support item assignment)."""
    from types import SimpleNamespace

    from applire.services.cover_letter import _compose_letter

    letter = _draft(_ENGLISH_BODY)
    letter["recipient"] = "recipient"
    out = _compose_letter(
        letter, profile_json=_PROFILE_JSON, cv_data={},
        profile=SimpleNamespace(profile_json=_PROFILE_JSON),
        pre_gen={"recipient_company": "Arnold Antriebstechnik GmbH"},
        language="en", pipeline="v2",
    )
    assert out["recipient"]["company"] == "Arnold Antriebstechnik GmbH"
    assert out["recipient"]["date"]
    assert out["body"]["paragraphs"] == _ENGLISH_BODY
