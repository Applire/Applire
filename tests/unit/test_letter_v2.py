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


@pytest.fixture(autouse=True)
def _site_prompt_without_the_shared_context(monkeypatch):
    """These tests pin the call site's own prompt, budget and routing; the shared
    context (PROMPT_CONTEXT, default c) is applied and tested in prompts/preamble
    (backend/tests/unit/test_prompt_context_arms.py)."""
    monkeypatch.setenv("PROMPT_CONTEXT", "off")


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
    # v2 is the default since ruling W3-7 (2026-10-18); v1 is set explicitly.
    monkeypatch.setenv("LETTER_PIPELINE", "v1")


# ── 1. the switch ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    (None, "v2"), ("v1", "v1"), (" V1 ", "v1"), ("v2", "v2"), (" V2 ", "v2"), ("v3", "v2"), ("", "v2"),
])
def test_letter_pipeline_switch(monkeypatch, raw, expected):
    from applire.services.letter_v2 import letter_pipeline

    if raw is None:
        monkeypatch.delenv("LETTER_PIPELINE", raising=False)
    else:
        monkeypatch.setenv("LETTER_PIPELINE", raw)
    assert letter_pipeline() == expected


def test_letter_pipeline_defaults_to_v2_and_is_operator_surface():
    """Ruling W3-7: v2 is the default; LETTER_PIPELINE=v1 is the one-setting opt-out."""
    from applire.settings_registry import get

    entry = get("LETTER_PIPELINE")
    assert entry.default == "v2"
    assert entry.in_env_example is True


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
    # v1's rules are carried otherwise unchanged; v2 adds exactly TWO top-level rules,
    # the F1 closing rule (2026-10-18) and the listed-only rule (ruling V-1, build 2)
    assert v2.count("- GROUNDING CONTRACT") == 1
    assert SYSTEM_PROMPT.split("Rules:")[1].count("\n- ") + 2 == v2.split("Rules:")[1].count("\n- ")


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
    assert v1["body"]["paragraphs"][0] == "Dear Hiring Manager,"
    assert v1["body"]["paragraphs"][-1] != "Kind regards"


def test_v2_compose_sets_every_code_owned_field():
    out = _compose(_draft(_ENGLISH_BODY), "v2")
    assert out["header"]["address"] == "Stuttgart"
    assert out["signature"] == {"closing": "Kind regards", "name": "Katrin Hoffmann"}
    assert out["recipient"]["date"]


def test_v2_compose_date_line_carries_the_sender_place():
    """Hazelnut build 3 (784 date-line line, ADR-094 cl. L2 amended): DIN 5008 —
    "Stuttgart, 10. Juni 2026", the place read from the vault's own location (the
    same Ort, Datum helper the CV's closing line uses); v1 is unchanged."""
    from datetime import date
    from types import SimpleNamespace

    from applire.services.cover_letter import _compose_letter

    def compose(pipeline, pj, lang):
        return _compose_letter(
            copy.deepcopy(_draft(_GERMAN_BODY)), profile_json=pj, cv_data={},
            profile=SimpleNamespace(profile_json=pj), pre_gen={}, language=lang,
            today=date(2026, 6, 10), pipeline=pipeline,
        )["recipient"]["date"]

    assert compose("v2", _PROFILE_JSON, "de") == "Stuttgart, 10. Juni 2026"
    assert compose("v2", _PROFILE_JSON, "en") == "Stuttgart, 10 June 2026"
    no_place = copy.deepcopy(_PROFILE_JSON)
    no_place["personal_info"]["location"] = None
    assert compose("v2", no_place, "de") == "10. Juni 2026"  # never an invented place
    assert compose("v1", _PROFILE_JSON, "de") == "10. Juni 2026"


def test_v2_compose_signs_a_capitalised_vault_name_in_display_casing():
    """Hazelnut build 3 (784 polish (b)): a vault name transcribed in capitals from
    a CV header is signed in display casing — casing only; the header keeps the
    template's own typography (it is not touched here)."""
    pj = copy.deepcopy(_PROFILE_JSON)
    pj["personal_info"]["name"] = "DANIEL KOVAČ"
    from types import SimpleNamespace

    from applire.services.cover_letter import _compose_letter

    out = _compose_letter(
        copy.deepcopy(_draft(_ENGLISH_BODY)), profile_json=pj, cv_data={},
        profile=SimpleNamespace(profile_json=pj), pre_gen={}, language="en", pipeline="v2",
    )
    assert out["signature"]["name"] == "Daniel Kovač"
    assert out["header"]["name"] == "DANIEL KOVAČ"


@pytest.mark.parametrize("raw,shown", [
    ("STEFAN BRANDT", "Stefan Brandt"),
    ("ANNA-LENA VON DER HEIDE", "Anna-Lena von der Heide"),
    ("SEAN O'BRIEN", "Sean O'Brien"),
    ("JÖRG MÜLLER-LÜDENSCHEIDT", "Jörg Müller-Lüdenscheidt"),
    ("Emma Schneider", "Emma Schneider"),   # already the candidate's own casing
    ("McAllister", "McAllister"),
    ("", ""),
])
def test_display_name_changes_casing_only(raw, shown):
    from applire.utils.person_name import display_name

    out = display_name(raw)
    assert out == shown
    assert out.casefold() == raw.casefold()


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


def test_v2_checks_fn_language_and_format_block_figure_and_limit_advise():
    from applire.services.cover_letter import _v2_checks_fn

    ledger = [{"concept": "Observability", "surface_forms": [], "status": "claimable"}]
    fn = _v2_checks_fn(
        lambda d: d, profile_json=_PROFILE_JSON, keyword_ledger=ledger,
        denied_concepts=[], language="en",
    )
    body = list(_GERMAN_BODY[1:])  # German AND no salutation
    body.append("I do not have Observability experience, but I would welcome an interview with you.")
    found = {f.check: f for f in fn(_draft(body))}
    assert found["letter.language"].blocking
    assert found["letter.salutation"].blocking
    assert found["letter.limit"].blocking is False  # advisory: 9/12 false on the baseline


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


async def _seed(db, profile_json=None, pre_gen_inputs=None):
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
    profile = make_master_profile(profile_json=copy.deepcopy(profile_json or _PROFILE_JSON))
    db.add_all([user, job, profile])
    await db.flush()
    cl = GeneratedCoverLetter(
        job_analysis_id=job.id, profile_id=profile.id, template="classic_german",
        letter_data={}, pre_gen_inputs=dict(pre_gen_inputs or {}), status=CoverLetterStatus.pending.value,
        document_language="en",
    )
    db.add(cl)
    await db.commit()
    await db.refresh(cl)
    return job, profile, cl


def _pages_fn(pages, seq, record=None):
    """``seq == "fits_after_final_condense"``: 2 pages until the second (post-loop)
    condense call has been made, 1 page after it."""
    if seq == "fits_after_final_condense":
        def fn(_pdf):
            n = sum("=== CURRENT LETTER (JSON) ===" in p for p, _s in record["systems"])
            return ("text", 1 if n >= 2 else 2)
        return fn

    def fn(_pdf):
        return ("text", pages)

    return fn


async def _run(db, ids, *, payload, pages=1, terminal_script=None, review_verdict=None, pages_seq=None,
               settle_exhausted=False):
    """Drive the real render with the provider and review_and_refine faked; record
    every call's system prompt and every review_and_refine invocation's kwargs."""
    from applire.services.cover_letter import _render_cover_letter_background

    job, _profile, cl = ids
    record = {"systems": [], "loops": [], "audit": None}
    script = list(terminal_script or [])

    async def _aparse(prompt, system=None, **kw):
        record["systems"].append((prompt, system))
        from applire.prompts.review_cover_letter import TERMINAL_REVIEW_SYSTEM_PROMPT as _T
        from applire.prompts.review_cover_letter import V2_TERMINAL_REVIEW_SYSTEM_PROMPT as _T2
        if review_verdict is not None and system in (_T, _T2):
            return copy.deepcopy(review_verdict)
        return copy.deepcopy(payload)

    async def _fake_review(**kw):
        entry = dict(kw)
        entry["reviewer_prompt"] = kw["reviewer_prompt_fn"](kw["source"], kw["draft"])
        sig = kw.get("signal_issues_fn")
        entry["signal_issues"] = list(sig(kw["draft"])) if sig else []
        chk = kw.get("checks_fn")
        entry["checks"] = list(chk(kw["draft"])) if chk else []
        entry["corrector_prompt"] = kw["generator_prompt_fn"](kw["draft"], "fb", kw["source"])
        record["loops"].append(entry)
        if settle_exhausted and kw.get("chain_id") == "letter_terminal_review" and kw.get("on_settle"):
            from applire.services.review_issues import ReviewSettle

            kw["reviewer_prompt_fn"](kw["source"], kw["draft"])
            kw["on_settle"](ReviewSettle(path="exhausted", approved=False,
                                         blocking_issues=("an earlier round's finding",),
                                         minor_issues=(), rounds=3, settled=kw["draft"]))
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
              new=MagicMock(side_effect=_pages_fn(pages, pages_seq, record))),
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
    # the language fact reaches the loop through W1's checks_fn (the loop renders it
    # for the reviewer and opens a corrector round on it)
    assert any(f.check == "letter.language" and f.blocking for f in drafting["checks"])
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
    assert drafting.get("checks_fn") is None
    assert "DOCUMENT LANGUAGE:" not in drafting["corrector_prompt"]
    assert drafting["draft"]["header"]["address"] == "Musterstraße 123, 70563 Stuttgart"


@pytest.mark.asyncio
async def test_seam_v2_terminal_mount_reads_findings_and_selects_nothing(db, v2):
    rec = await _run(db, await _seed(db), payload=_draft(_GERMAN_BODY[1:]))
    terminal = [l for l in rec["loops"] if l["chain_id"] == "letter_terminal_review"]
    assert terminal
    t = terminal[0]
    assert t["retain_if"] is None and t["prefer_if"] is None and t["load_bearing_fn"] is None
    assert any(f.check == "letter.salutation" for f in t["checks"])
    assert t.get("signal_issues_fn") is None


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
    """W2-1 B3 deleted the v1 floor (selection / revert / unreviewed re-condense);
    W2-4: at most ONE post-loop condense, language-named; RULING W3-3a: the round
    after it is REVIEW-ONLY — one reviewer call over the condensed letter, no
    corrector, and the reviewed letter is the one that ships."""
    from applire.models.cover_letter import GeneratedCoverLetter
    # v2 run: the post-condense review uses the v2 terminal reviewer (784:L31 variant)
    from applire.prompts.review_cover_letter import V2_TERMINAL_REVIEW_SYSTEM_PROMPT as TERMINAL_REVIEW_SYSTEM_PROMPT

    condensed = _draft(_ENGLISH_BODY)
    verdict = {"approved": False, "issues": [
        {"severity": "blocking", "issue": "The condense dropped the HGB closing claim."}]}
    ids = await _seed(db)
    rec = await _run(db, ids, payload=condensed, pages=2, review_verdict=verdict)
    condenses = [i for i, (p, _s) in enumerate(rec["systems"]) if "=== CURRENT LETTER (JSON) ===" in p]
    assert len(condenses) == 2  # pre-verdict (B4) + the one post-loop condense
    for i in condenses:
        p, s_ = rec["systems"][i]
        assert "Write every sentence in ENGLISH." in p
        assert "document language is ENGLISH" in s_.splitlines()[0]
    after = rec["systems"][condenses[-1] + 1:]
    # exactly ONE call after the post-loop condense, and it is the reviewer's
    assert [s_ for _p, s_ in after] == [TERMINAL_REVIEW_SYSTEM_PROMPT]
    assert "CODE CHECKS" in after[0][0]  # the page fact is shown to the reviewer
    terminal = [l for l in rec["loops"] if l["chain_id"] == "letter_terminal_review"]
    assert len(terminal) == 1  # no corrector loop after the condense
    outcome = rec["audit"]["terminal_review"]
    # W3-3c: the CODE fact (still over the page) fails the row; the reviewer's
    # finding is an advisory note, never a blocking issue
    assert outcome.path == "review_only" and outcome.status == "fail"
    assert all(t.startswith("[letter.") for t in outcome.blocking_issues)
    assert any(t.startswith("[letter.page_length]") for t in outcome.blocking_issues)
    assert any("reviewer's notes on the shortened letter" in n and "dropped the HGB closing claim" in n
               for n in outcome.notes)
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.letter_data["body"]["paragraphs"] == condensed["body"]["paragraphs"]


@pytest.mark.asyncio
async def test_seam_v2_post_condense_review_approves_a_clean_condensed_letter(db, v2):
    rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY), pages=1,
                     review_verdict={"approved": True, "issues": []})
    # pages=1: nothing over the norm, so no post-loop condense and no review-only call
    assert sum("=== CURRENT LETTER (JSON) ===" in p for p, _s in rec["systems"]) == 0


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


# ── RULING W2-3: recorded-name membership for employer / institution spans ──
_SHORT_NAME_BODY = [
    _ENGLISH_BODY[0],
    _ENGLISH_BODY[1],
    "At Schwarzwald I have owned the monthly and annual closing under HGB "
    "since 2019, including the coordination with the auditors.",
    _ENGLISH_BODY[3],
]


@pytest.mark.parametrize("sentence, expected", [
    ("At Schwarzwald Präzision GmbH I closed the books.", []),            # stored name
    ("At Schwarzwald Precision I closed the books.", []),                 # recorded alias
    ("At Schwarzwald Präzision I closed the books.", []),                 # W2-5: legal form dropped = member
    ("At Schwarzwald I closed the books.", ["Schwarzwald"]),              # distinctive token
    ("I studied at the University of Stuttgart.", []),                    # institution alias
    ("I worked at Arnold Antriebstechnik GmbH.", []),                     # not a recorded entity
])
def test_letter_name_findings_membership(sentence, expected):
    from applire.services.letter_v2 import letter_name_findings

    found = letter_name_findings({"body": {"paragraphs": [sentence]}}, _PROFILE_JSON)
    assert [f.span for f in found] == expected
    for f in found:
        assert "not a name the candidate recorded for Schwarzwald Präzision GmbH" in f.text()
        assert '"Schwarzwald Precision"' in f.text()


def test_letter_name_findings_w2_5_legal_form_is_stripped_before_membership():
    """RULING W2-5: "Brauner & Söhne Maschinenbau" for the recorded "… GmbH" is the
    same name; a genuinely different shortening ("Brauner") still blocks."""
    from applire.services.letter_v2 import letter_name_findings

    profile = {"work_experience": [{"company": "Brauner & Söhne Maschinenbau GmbH"}]}

    def spans(sentence):
        return [f.span for f in letter_name_findings({"body": {"paragraphs": [sentence]}}, profile)]

    assert spans("Bei Brauner & Söhne Maschinenbau erstellte ich Investitionsrechnungen.") == []
    assert spans("Bei Brauner erstellte ich Investitionsrechnungen.") == ["Brauner"]


def test_letter_name_findings_w2_5_strips_the_legal_form_of_an_alias_too():
    """A short form recorded WITH a legal form ("Schwarzwald Ltd") accepts the
    located token span without it ("Schwarzwald")."""
    from applire.services.letter_v2 import letter_name_findings

    profile = {"work_experience": [{"company": "Schwarzwald Präzision GmbH",
                                     "company_aliases": ["Schwarzwald Ltd"]}]}
    letter = {"body": {"paragraphs": ["At Schwarzwald I closed the books."]}}
    assert letter_name_findings(letter, profile) == []


def test_letter_name_findings_ignore_an_ordinary_word_matching_a_lowercase_token():
    """The Oracle's distinctive-token pass tolerates "kleine" for "kleine Gruppe (…)"
    (fail-open attribution); as a BLOCKING finding it must not fire on the adjective
    (measured: 3 of 28 v2 bench letters before this guard)."""
    from applire.services.letter_v2 import letter_name_findings

    profile = {"work_experience": [{"company": "kleine Gruppe (GmbH plus Vertriebsgesellschaft Schweiz)"}]}
    letter = {"body": {"paragraphs": ["Ich führte eine kleine Einheit mit vier Personen."]}}
    assert letter_name_findings(letter, profile) == []


def test_v2_checks_fn_carries_a_non_member_name_as_blocking():
    from applire.services.cover_letter import _v2_checks_fn

    fn = _v2_checks_fn(lambda d: d, profile_json=_PROFILE_JSON, keyword_ledger=None,
                       denied_concepts=None, language="en")
    found = [f for f in fn(_draft(_SHORT_NAME_BODY)) if f.check == "letter.names"]
    assert len(found) == 1 and found[0].blocking
    assert '"Schwarzwald" is not a name the candidate recorded' in found[0].text


@pytest.mark.asyncio
async def test_seam_v2_a_non_member_name_reaches_the_loop_and_ships_unrewritten(db, v2):
    from applire.models.cover_letter import GeneratedCoverLetter

    ids = await _seed(db)
    rec = await _run(db, ids, payload=_draft(_SHORT_NAME_BODY))
    drafting = rec["loops"][0]
    assert any(f.check == "letter.names" and f.blocking for f in drafting["checks"])
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.letter_data["body"]["paragraphs"] == _SHORT_NAME_BODY  # never rewritten
    outcome = rec["audit"]["terminal_review"]
    assert "[names]" in " ".join(outcome.notes if outcome is not None else [])


def test_letter_name_findings_decide_membership_by_w1s_norm_name():
    """Membership is W1's ``pick_recorded`` (NFKC, case, whitespace), not the locator's
    regex: an alias recorded with irregular spacing still makes the span a member."""
    from applire.services.letter_v2 import letter_name_findings

    profile = {"work_experience": [{"company": "Schwarzwald Präzision GmbH",
                                     "company_aliases": ["Schwarzwald  Präzision"]}]}
    letter = {"body": {"paragraphs": ["At Schwarzwald Präzision I closed the books."]}}
    assert letter_name_findings(letter, profile) == []


# ── adversarial probe B (2026-10-17): the v2 condense keeps facts with their
#    employer and keeps the coverage terms the letter already carries ──────────
def test_condense_prompt_v2_names_the_employer_rule_and_the_retained_terms():
    from applire.prompts.cover_letter import build_condense_prompt

    letter = {"body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "Text."]}}
    v1 = build_condense_prompt(letter, 250, 2, letter_pages=1)
    v2 = build_condense_prompt(letter, 250, 2, letter_pages=1, language="de",
                               retained_terms_block="COVERAGE ALREADY ACHIEVED x")
    rule = "Never move a fact to another employer's sentence"
    assert rule not in v1 and "COVERAGE ALREADY ACHIEVED" not in v1
    assert rule in v2
    assert v2.index("COVERAGE ALREADY ACHIEVED") < v2.index("=== CURRENT LETTER (JSON) ===")


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
async def test_seam_condenses_carry_the_retained_terms_on_v2_only(db, monkeypatch, pipeline):
    monkeypatch.setenv("LETTER_PIPELINE", pipeline)
    entry = {"concept": "HGB", "surface_forms": ["HGB"], "status": "claimable"}
    with patch("applire.services.keyword_ledger.claimable_present_entries",
               return_value=[entry]):
        rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY), pages=2)
    condenses = [p for p, _s in rec["systems"] if "=== CURRENT LETTER (JSON) ===" in p]
    assert condenses
    for p in condenses:
        has = "JOB-POSTING TERMS ALREADY IN THIS LETTER (code scan): HGB." in p
        assert "COVERAGE ALREADY ACHIEVED" not in p
        assert has is (pipeline == "v2")


# ── RULING W3-3: v2 sizes the page from this delivery's own render ──────────
def _norm():
    from types import SimpleNamespace
    return SimpleNamespace(letter_pages=1, letter_body_word_budget=300)


def _m(pages, words, overflow=None):
    from applire.services.cover_letter import MeasuredLetter
    return MeasuredLetter(page_count=pages, letter_pages=1, word_count=words,
                          word_budget=300, overflow_words=overflow)


def test_v2_page_target_prefers_this_renders_overflow_over_another_compositions_fit():
    from applire.services.cover_letter import _v2_page_target

    over = _m(2, 310, overflow=40)
    assert _v2_page_target(over, [_m(1, 295), over], _norm()) == 310 - 40 - 25  # 8 % of 310
    unmeasured = _m(2, 310, overflow=None)
    assert _v2_page_target(unmeasured, [_m(1, 262), unmeasured, _m(1, 248)], _norm()) == 262


def test_v2_page_target_sizes_from_the_overflow_with_a_margin():
    from applire.services.cover_letter import _v2_page_target

    over = _m(2, 289, overflow=12)          # the Luna shape: 289 words on 2 pages
    assert _v2_page_target(over, [over], _norm()) == 289 - 12 - 23  # 8 % of 289
    small = _m(2, 150, overflow=5)
    assert _v2_page_target(small, [small], _norm()) == 150 - 5 - 15  # margin floor
    big = _m(2, 420, overflow=30)
    assert _v2_page_target(big, [big], _norm()) == 300                # never above budget


def test_v2_page_target_falls_back_to_the_budget_without_a_measure():
    from applire.services.cover_letter import _v2_page_target

    over = _m(2, 310, overflow=None)
    assert _v2_page_target(over, [over], _norm()) == 300


@pytest.mark.asyncio
async def test_seam_v2_condense_target_comes_from_the_render_overflow(db, v2):
    """The writer's 2-page render put 30 words past page 1: the v2 condense is sized
    to the words that fit, not to the 300-word region budget."""
    body = list(_ENGLISH_BODY)
    words = sum(len(p.split()) for p in body)
    with patch("applire.services.ats_audit.extract_page_texts",
               return_value=["x " * 200, "y " * 30]):
        rec = await _run(db, await _seed(db), payload=_draft(body), pages=2)
    condenses = [p for p, _s in rec["systems"] if "=== CURRENT LETTER (JSON) ===" in p]
    expected = max(1, min(words - 30 - max(15, round(0.08 * words)), 300))
    assert len(condenses) == 2  # pre-verdict + the W2-4 post-loop condense
    assert all(f"AT MOST {expected} words" in p for p in condenses)


@pytest.mark.asyncio
async def test_seam_v1_condense_target_stays_the_region_budget(db, v1):
    with patch("applire.services.ats_audit.extract_page_texts",
               return_value=["x " * 200, "y " * 30]):
        rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY), pages=2)
    condenses = [p for p, _s in rec["systems"] if "=== CURRENT LETTER (JSON) ===" in p]
    assert condenses and "AT MOST 300 words" in condenses[0]


def test_v2_compose_flattens_object_paragraphs_from_the_corrector():
    """W3-3 re-measure: a corrector returned body paragraphs as objects and the
    v2 generation failed on ``.split``; the text is kept, the shape repaired."""
    from applire.services.cover_letter import _v2_coerce_paragraphs

    d = {"body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "Erster Absatz.",
                                 {"text": "Zweiter Absatz.", "note": "mit Zusatz."}, {}, ["Dritter", "Absatz."]]}}
    out = _v2_coerce_paragraphs(d)
    assert out["body"]["paragraphs"] == ["Sehr geehrte Damen und Herren,", "Erster Absatz.",
                                         "Zweiter Absatz. mit Zusatz.", "Dritter Absatz."]
    clean = {"body": {"paragraphs": ["a", "b"]}}
    assert _v2_coerce_paragraphs(clean) is clean


@pytest.mark.asyncio
async def test_seam_v2_survives_object_paragraphs(db, v2):
    from applire.models.cover_letter import GeneratedCoverLetter

    body = list(_ENGLISH_BODY[:-1]) + [{"text": _ENGLISH_BODY[-1]}]
    ids = await _seed(db)
    await _run(db, ids, payload=_draft(body))
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready"
    assert cl.letter_data["body"]["paragraphs"][-1] == _ENGLISH_BODY[-1]


def test_v2_writer_rule_keeps_a_target_a_target_and_v1_is_unchanged():
    from applire.prompts.cover_letter import SYSTEM_PROMPT, build_system_prompt

    v2 = build_system_prompt("de", pipeline="v2")
    assert "stays a target" in v2 and "never write it as achieved" in v2
    assert build_system_prompt("de") is SYSTEM_PROMPT and "stays a target" not in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_seam_v2_w3_3c_reviewer_findings_alone_never_fail_the_row(db, v2):
    """RULING W3-3c: the condensed letter fits (code facts clean) and the reviewer
    still rejects it — the row is an advisory PASS with the reviewer's notes, and
    the missing coverage terms come from the ledger scan."""
    verdict = {"approved": False, "issues": [
        {"severity": "blocking", "issue": "Figure '2019' is unanchored."}]}
    entry = {"concept": "SAP S/4HANA", "surface_forms": ["S/4HANA"], "status": "claimable"}
    # 2 pages through the terminal loop, 1 page once condensed
    with patch("applire.services.keyword_ledger.verified_missing_claimable", return_value=[entry]):
        rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY),
                         pages_seq="fits_after_final_condense", review_verdict=verdict,
                         settle_exhausted=True)
    outcome = rec["audit"]["terminal_review"]
    post = [n for n in outcome.notes if "reviewer's notes on the shortened letter" in n]
    assert post, outcome.notes
    assert outcome.status == "pass" and outcome.blocking_issues == ()  # an advisory PASS row
    assert "Figure '2019' is unanchored." in post[0]
    assert any("does not use: SAP S/4HANA." in n for n in outcome.notes)
    # the earlier terminal round's verdict read a draft that no longer ships
    assert "an earlier round's finding" not in " ".join(outcome.blocking_issues)
    assert outcome.path == "minor_only"


# ── W3-7 flip follow-up: a vault without a contact name ─────────────────────
def test_code_owned_fields_without_a_vault_name_validate_and_invent_nothing():
    """W3 (2026-10-18): a vault with no contact name composed ``header.name = None``;
    ``LetterData`` validation then failed in the .docx export / its ATS audit. The
    missing name is the schema's "no line" (""), and the model's typed name is not
    used for the signature either."""
    from applire.schemas.cover_letter import LetterData
    from applire.services.letter_v2 import apply_code_owned_fields

    profile = copy.deepcopy(_PROFILE_JSON)
    profile["personal_info"] = {"name": None, "email": "k@example.com", "phone": None,
                                "address": None, "location": None}
    draft = _draft(_ENGLISH_BODY)
    draft["signature"] = {"closing": "Kind regards", "name": "Max Mustermann"}  # model-typed
    out = apply_code_owned_fields(draft, profile_json=profile, cv_data={})
    assert out["header"]["name"] == "" and out["header"]["address"] == ""
    assert out["signature"]["name"] == ""
    LetterData.model_validate({k: out[k] for k in ("header", "recipient", "body", "signature")})


@pytest.mark.asyncio
async def test_seam_v2_letter_without_a_vault_name_renders_the_docx(db, v2):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services.cover_letter import _coerce_stored_letter_data
    from applire.services.office_export.letter_docx import render_letter_docx

    pj = copy.deepcopy(_PROFILE_JSON)
    pj["personal_info"] = {**pj["personal_info"], "name": None, "address": None, "location": None}
    ids = await _seed(db, profile_json=pj)
    await _run(db, ids, payload=_draft(_ENGLISH_BODY))
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    assert cl.status == "ready"
    letter = _coerce_stored_letter_data(cl.letter_data)  # raised before the fix
    assert letter.header.name == "" and "Hofman" not in letter.signature.name
    assert render_letter_docx(letter, lang="en", accent_color="#12233E")[:2] == b"PK"


# ── F1 (delivery run 2026-10-18): no keyword-list closing on v2 ─────────────
def test_v2_condense_places_retained_terms_in_their_evidence_sentence_never_a_list():
    from applire.prompts.cover_letter import build_condense_prompt

    letter = {"body": {"paragraphs": ["Sehr geehrte Damen und Herren,", "Text."]}}
    from applire.prompts.cover_letter import render_condense_retained_terms

    block = render_condense_retained_terms(["MES", "OEE", ""])
    assert block.startswith("JOB-POSTING TERMS ALREADY IN THIS LETTER (code scan): MES, OEE.")
    assert "inside a sentence that carries its evidence" in block
    assert "Never gather terms into a list" in block and "never move them into the closing" in block
    assert "drop the term" in block
    assert "MUST" not in block  # F1: never the corrector's must-keep wording
    assert render_condense_retained_terms([]) == ""
    v2 = build_condense_prompt(letter, 250, 2, letter_pages=1, language="de", retained_terms_block=block)
    assert block in v2 and v2.index(block) < v2.index("=== CURRENT LETTER (JSON) ===")
    v1 = build_condense_prompt(letter, 250, 2, letter_pages=1)
    assert "JOB-POSTING TERMS ALREADY IN THIS LETTER" not in v1


def test_v2_writer_rules_state_the_closing_is_a_call_to_action_never_a_list():
    from applire.prompts.cover_letter import SYSTEM_PROMPT, V2_CLOSING_RULE, build_system_prompt

    assert "never a list of skills, tools or job-posting terms" in V2_CLOSING_RULE
    assert V2_CLOSING_RULE in build_system_prompt("de", pipeline="v2")
    assert V2_CLOSING_RULE not in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_seam_v2_corrector_carries_the_closing_rule_v1_does_not(db, v1):
    from applire.prompts.cover_letter import V2_CLOSING_RULE

    rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY))
    assert all(V2_CLOSING_RULE not in l["corrector_prompt"] for l in rec["loops"])


@pytest.mark.asyncio
async def test_seam_v2_corrector_carries_the_closing_rule(db, v2):
    from applire.prompts.cover_letter import V2_CLOSING_RULE

    rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY))
    assert rec["loops"] and all(f"CLOSING: {V2_CLOSING_RULE}" in l["corrector_prompt"] for l in rec["loops"])


# ── RULING DEL-3 (ADR-075 amended 2026-10-18): the input filter, both pipelines ──

_DEL3_OWED = "Mit IFS habe ich keine Erfahrung, zehn Jahre ISO-9001-Auditpraxis bringe ich mit."
_DEL3_OTHER = "Eine konkrete Investitionssumme kann ich nicht nennen."


async def _seed_with_denials(db):
    from datetime import datetime, timezone

    from applire.models.gap import GapAnalysis

    pj = copy.deepcopy(_PROFILE_JSON)
    pj["metadata"] = {"denied_concepts": [
        {"concept": "IFS", "statement": _DEL3_OWED, "source": "interview", "date": "2026-10-01"},
        {"concept": "Investitionssumme", "statement": _DEL3_OTHER, "source": "interview",
         "date": "2026-10-01"},
    ]}
    ids = await _seed(db, profile_json=pj)
    job, profile, _cl = ids
    db.add(GapAnalysis(
        job_analysis_id=job.id, profile_id=profile.id, match_score=0.5, critical_gaps=[],
        minor_gaps=[], strengths=[], keyword_gaps=[], category_a=[], category_b=[],
        category_c=[], keyword_ledger=[], gap_clusters=[], requirement_breakdown=[],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ))
    await db.commit()
    return ids


_DEL3_LEDGER = [
    {"concept": "IFS", "status": "denied", "claimable": False, "fit_weight": 0.5,
     "surface_forms": ["IFS"], "sources": ["nice_to_have"], "evidence": ""},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("pipeline", ["v1", "v2"])
async def test_seam_del3_only_the_postings_denial_reaches_the_writer_obligation(
        db, monkeypatch, pipeline):
    monkeypatch.setenv("LETTER_PIPELINE", pipeline)

    async def _ledger(*_a, **_k):
        return copy.deepcopy(_DEL3_LEDGER)

    with patch("applire.services.keyword_ledger.refresh_persist_and_rescore", side_effect=_ledger):
        rec = await _run(db, await _seed_with_denials(db), payload=_draft(_GERMAN_BODY))
    writer_prompt = rec["systems"][0][0]
    req = writer_prompt.index("=== REQUIRED: STATED LIMITS THIS POSTING ASKS ABOUT")
    con = writer_prompt.index("=== STATED LIMITS — CONSTRAINT ONLY")
    req_block = writer_prompt[req:writer_prompt.find("\n\n===", req)]
    con_block = writer_prompt[con:writer_prompt.find("\n\n===", con)]
    # the owed denial is an obligation, and only there
    assert _DEL3_OWED in req_block and _DEL3_OWED not in con_block
    # the other denial still reaches the writer (never claimed) — as a constraint only
    assert _DEL3_OTHER in con_block and _DEL3_OTHER not in req_block
    # the loop's source owes only the posting's denial too (the vault itself, which the
    # source also carries, still holds both statements)
    drafting = next(l for l in rec["loops"] if l["chain_id"] == "cover_letter")
    k = drafting["source"].index('"stated_limits": {')
    entry = drafting["source"][k:drafting["source"].index('"instruction"', k)]
    assert '"required": true' in entry or _DEL3_OWED in entry
    assert _DEL3_OWED in entry and _DEL3_OTHER not in entry


@pytest.mark.asyncio
async def test_seam_del3_every_denial_owed_still_reaches_the_loop_source(db, v2):
    """When every denial is this posting's, the constraint-only copy is empty — the
    loop's ``stated_limits`` entry must not hang on that empty block."""
    ledger = copy.deepcopy(_DEL3_LEDGER) + [
        {"concept": "Investitionssumme", "status": "denied", "claimable": False,
         "fit_weight": 1.0, "surface_forms": ["Investitionssumme"], "sources": ["required"],
         "evidence": ""},
    ]

    async def _ledger(*_a, **_k):
        return copy.deepcopy(ledger)

    with patch("applire.services.keyword_ledger.refresh_persist_and_rescore", side_effect=_ledger):
        rec = await _run(db, await _seed_with_denials(db), payload=_draft(_GERMAN_BODY))
    writer_prompt = rec["systems"][0][0]
    assert "STATED LIMITS — CONSTRAINT ONLY" not in writer_prompt
    drafting = next(l for l in rec["loops"] if l["chain_id"] == "cover_letter")
    k = drafting["source"].index('"stated_limits": {')
    entry = drafting["source"][k:drafting["source"].index('"instruction"', k)]
    assert _DEL3_OWED in entry and _DEL3_OTHER in entry


# ── Hazelnut build 2 — 786:L136: a standard identifier the vault never states ──

_ISO_PROFILE_JSON = copy.deepcopy(_PROFILE_JSON)
_ISO_PROFILE_JSON["work_experience"][0]["achievements"].append(
    "Interne Kontrollen nach ISO 27001 aufgebaut."
)


def _iso_body(sentence: str) -> list[str]:
    return [_ENGLISH_BODY[0], _ENGLISH_BODY[1], _ENGLISH_BODY[2] + " " + sentence, _ENGLISH_BODY[3]]


def test_identifier_typo_is_a_letter_finding():
    letter = _compose(_draft(_iso_body("I built the internal controls under ISO 2701.")), "v2")
    found = [f for f in _findings(letter, profile_json=_ISO_PROFILE_JSON) if f.family == "identifier"]
    assert len(found) == 1
    assert "“ISO 2701”" in found[0].text and "“ISO 27001”" in found[0].text


def test_identifier_as_recorded_is_no_letter_finding():
    letter = _compose(_draft(_iso_body("I built the internal controls under ISO 27001.")), "v2")
    assert not [f for f in _findings(letter, profile_json=_ISO_PROFILE_JSON) if f.family == "identifier"]


def test_v2_checks_fn_carries_an_identifier_finding_as_blocking():
    from applire.services.cover_letter import _v2_checks_fn

    fn = _v2_checks_fn(lambda d: d, profile_json=_ISO_PROFILE_JSON, keyword_ledger=None,
                       denied_concepts=None, language="en")
    found = [f for f in fn(_draft(_iso_body("I built the controls under ISO 2701."))) if f.check == "letter.identifier"]
    assert len(found) == 1 and found[0].blocking


# ── Hazelnut build 2 — 784:L31: availability the candidate never gave ─────────

_NOTICE_BODY = [
    _ENGLISH_BODY[0], _ENGLISH_BODY[1], _ENGLISH_BODY[2],
    "I would welcome the opportunity to discuss my experience with you in an interview. "
    "My notice period can be discussed.",
]


@pytest.mark.parametrize("sentence, hit", [
    # the v2 gate corpus shapes (5c2f056b), all with no availability input
    ("My notice period can be discussed.", True),
    ("Meine Kündigungsfrist ermöglicht einen reibungslosen Übergang.", True),
    ("Mein Eintrittstermin kann flexibel vereinbart werden.", True),
    ("I am available to start immediately and would welcome a conversation.", True),
    ("Available immediately, I am confident my skills would add value.", True),
    ("Mein aktueller Arbeitgeber ermöglicht eine sofortige Verfügbarkeit.", True),
    ("Mein Umzug ist geplant, und ich bin ab sofort für eine Einstellung verfügbar.", True),
    # not a statement about when the candidate could start
    ("I defined SLOs with a 99.9% availability target for the tracking API.", False),
    ("Für ein persönliches Gespräch stehe ich Ihnen gerne zur Verfügung.", False),
    ("Ich bin ab sofort für ein Gespräch verfügbar.", False),
    ("I am available for an interview at your convenience.", False),
    ("Die Anlage erreichte eine Verfügbarkeit von 98 %.", False),
])
def test_availability_sentences_vocabulary(sentence, hit):
    from applire.services.letter_v2 import availability_sentences

    letter = {"body": {"paragraphs": [_ENGLISH_BODY[0], sentence]}}
    assert bool(availability_sentences(letter)) is hit


def test_availability_without_input_is_a_finding_and_with_input_none():
    letter = _compose(_draft(_NOTICE_BODY), "v2")
    from applire.services.cover_letter import _letter_v2_findings

    without = _letter_v2_findings(letter, profile_json=_PROFILE_JSON, keyword_ledger=None,
                                  denied_concepts=None, availability_given=False)
    found = [f for f in without if f.family == "availability"]
    assert len(found) == 1 and '"My notice period can be discussed."' in found[0].text
    given = _letter_v2_findings(letter, profile_json=_PROFILE_JSON, keyword_ledger=None,
                                denied_concepts=None, availability_given=True)
    assert not [f for f in given if f.family == "availability"]


def test_v2_checks_fn_carries_availability_as_blocking():
    from applire.services.cover_letter import _v2_checks_fn

    fn = _v2_checks_fn(lambda d: d, profile_json=_PROFILE_JSON, keyword_ledger=None,
                       denied_concepts=None, language="en", availability_given=False)
    found = [f for f in fn(_draft(_NOTICE_BODY)) if f.check == "letter.availability"]
    assert len(found) == 1 and found[0].blocking


@pytest.mark.asyncio
@pytest.mark.parametrize("pre_gen, expected", [({}, True), ({"availability": "ab 01.03.2027"}, False)])
async def test_seam_v2_availability_finding_follows_the_typed_input(db, v2, pre_gen, expected):
    """Seam (render → drafting loop): no typed availability and no vault testimony →
    the check fires on the round's draft; a typed availability silences it."""
    rec = await _run(db, await _seed(db, pre_gen_inputs=pre_gen), payload=_draft(_NOTICE_BODY))
    drafting = rec["loops"][0]
    assert any(f.check == "letter.availability" for f in drafting["checks"]) is expected


@pytest.mark.asyncio
async def test_seam_v2_loops_get_the_v2_reviewer_and_corrector_prompts(db, v2):
    from applire.prompts import review_cover_letter as r

    rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY))
    systems = {(e["chain_id"], e["reviewer_system"], e["generator_system"]) for e in rec["loops"]}
    assert ("cover_letter", r.V2_REVIEW_SYSTEM_PROMPT, r.V2_COVER_LETTER_REFINEMENT_PROMPT) in systems
    assert all(rs is not r.REVIEW_SYSTEM_PROMPT and rs is not r.TERMINAL_REVIEW_SYSTEM_PROMPT
               for _c, rs, _g in systems)


@pytest.mark.asyncio
async def test_seam_v1_loops_keep_the_v1_prompts(db, v1):
    from applire.prompts import review_cover_letter as r

    rec = await _run(db, await _seed(db), payload=_draft(_ENGLISH_BODY))
    for e in rec["loops"]:
        assert e["reviewer_system"] in (r.REVIEW_SYSTEM_PROMPT, r.TERMINAL_REVIEW_SYSTEM_PROMPT)
        assert e["generator_system"] is r.COVER_LETTER_REFINEMENT_PROMPT


def test_v2_letter_prompts_drop_the_availability_demand_and_v1_keeps_it():
    from applire.prompts import cover_letter as w
    from applire.prompts import review_cover_letter as r

    assert "availability folded in" in r.REVIEW_SYSTEM_PROMPT            # v1 byte-identical
    assert "availability folded in" in r.TERMINAL_REVIEW_SYSTEM_PROMPT
    for p in (r.V2_REVIEW_SYSTEM_PROMPT, r.V2_TERMINAL_REVIEW_SYSTEM_PROMPT):
        assert "availability folded in" not in p and "never ask for one" in p
    assert "NO AVAILABILITY WITHOUT INPUT" in r.V2_COVER_LETTER_REFINEMENT_PROMPT
    assert "NO AVAILABILITY WITHOUT INPUT" not in r.COVER_LETTER_REFINEMENT_PROMPT
    v2 = w.build_system_prompt("de", pipeline="v2")
    assert "AVAILABILITY ONLY FROM INPUT" in v2 and "Include Eintrittstermin in body only" not in v2
    assert "Include Eintrittstermin in body only if availability is provided." in w.SYSTEM_PROMPT


def test_letter_default_colour_is_the_cv_default():
    """L-2 = A (Hazelnut build 3): with no colour profile the letter renders in the
    CV's default colours, so both documents of one application read as one set."""
    from applire.services.color_detection import _default_context
    from applire.services.cover_letter import _default_color_context

    cv = _default_context()
    assert _default_color_context() == {
        "primary": cv.primary, "primary_tint": cv.primary_tint,
        "surface": cv.surface, "surface_text": cv.surface_text,
    }


@pytest.mark.parametrize("recorded, place", [
    ("Frankfurt am Main, Germany", "Frankfurt am Main"),
    ("München, Deutschland", "München"),
    ("Maximilianstraße 12, 80539 München", "München"),
    ("8001 Zürich, Schweiz", "Zürich"),
    ("Koblenz", "Koblenz"),
    ("Germany", "Germany"),
    ("  ", None),
    (None, None),
])
def test_date_line_place_names_the_place_only(recorded, place):
    """Bench 2026-10-22 (WP-L after-run): Daniel's German letter was dated
    "Frankfurt am Main, Germany, 22. Oktober 2026" — DIN 5008 names the place."""
    from applire.services.letter_v2 import date_line_place

    assert date_line_place(recorded) == place


def test_letter_and_cv_closing_name_the_same_place():
    from datetime import date

    from applire.services.cover_letter import _inject_letter_date
    from applire.services.cv import format_place_date_for_cv
    from applire.services.letter_v2 import sender_location

    profile = {"personal_info": {"location": "Frankfurt am Main, Germany"}}
    letter = _inject_letter_date({}, "de", date(2026, 10, 22), location=sender_location(profile, None))
    assert letter["recipient"]["date"] == "Frankfurt am Main, 22. Oktober 2026"
    assert format_place_date_for_cv("Frankfurt am Main, Germany", "de").startswith("Frankfurt am Main, ")
