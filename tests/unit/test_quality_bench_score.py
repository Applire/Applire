# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (package 2) — hermetic tests for the bench scorers, judge client,
attribution, report and calibration.

Nothing here touches the network, a real provider or a browser: the judge is
``FakeJudge`` (canned JSON per class) or ``JudgeClient`` over an
``httpx.MockTransport``. The scorer package lives under ``scripts/`` (not an
importable product package), so it is loaded by path, like the other script
harness tests.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
QB_DIR = REPO_ROOT / "scripts" / "quality_bench"


def _load(name: str):
    """Import scripts/quality_bench/<name>.py as a top-level module (path-loaded)."""
    for p in (str(REPO_ROOT / "backend"), str(QB_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(f"qb_ut_{name}", QB_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


for _p in (str(REPO_ROOT / "backend"), str(QB_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from score import attribution, drafts as drafts_mod, inputs, items, judge as judge_mod, prompts  # noqa: E402
from score.duplicates import candidate_pairs, score_duplicates  # noqa: E402
from score.language import score_language  # noqa: E402
from score.score_run import score_run  # noqa: E402
from score.underselling import find_removals, score_underselling  # noqa: E402
from score.upselling import oracle_agreement, score_upselling  # noqa: E402

report_mod = _load("report")
calibrate_mod = _load("calibrate")


# --------------------------------------------------------------------------- #
# Synthetic, realistic documents (German CV with planted defects)
# --------------------------------------------------------------------------- #

W1 = "w-0001"
W2 = "w-0002"

B_KEEP = "Führung von 38 Mitarbeitenden im Dreischichtbetrieb in Spritzguss und Montage."
B_CAPPED = "Senkung der Ausschussquote von 4,1 % auf 2,3 % durch SMED und Shopfloor-Management."
B_ENGLISH = "Introduced the lean shopfloor routine with our team and for all production lines."
B_UPSOLD = "Verantwortung für ein Werk mit 400 Mitarbeitenden und 80 Mio. EUR Umsatz."
B_W2 = "Einführung von KVP-Routinen in der Endmontage."


def tailored_data() -> dict:
    return {
        "contact": {"name": "Marcus Weber", "email": "marcus.weber@example.com", "location": "Koblenz"},
        "summary": "Produktionsleiter mit 14 Jahren Erfahrung in der diskreten Fertigung. Schwerpunkte: Lean Management und KVP.",
        "work_history": [
            {"id": W1, "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter",
             "start_date": "2016-03", "end_date": None, "industry_context": "Plastics injection moulding",
             "budget_managed": "2 Mio. EUR", "team_size": 38,
             "bullets": [B_KEEP, B_ENGLISH, B_UPSOLD],
             "projects": [{"name": "SMED-Rollout", "bullets": ["Rüstzeiten um 35 % reduziert."]}]},
            {"id": W2, "company": "Rheinmetall Montage AG", "role": "Schichtleiter", "start_date": "2010-01", "end_date": "2016-02",
             "bullets": [B_W2], "projects": []},
        ],
        "skills": ["Lean Management", "SAP PP", "CSV", "Computer System Validation", "Stakeholder Management", "Process Optimisation"],
        "education": [{"institution": "HWK Koblenz", "degree": "Industriemeister Metall", "field": "Metall", "start_date": "2008", "end_date": "2010"}],
        "languages": [{"language": "Deutsch", "level": "Muttersprache"}, {"language": "Englisch", "level": "fluent"}],
        "certifications": [{"name": "Six Sigma Green Belt", "issuing_organization": "TÜV", "date_obtained": "2018-05-01", "expiry_date": ""}],
        "projects": [],
        "show_photo": True,
    }


LETTER_PARAS = [
    "Sehr geehrte Damen und Herren,",
    "mit 14 Jahren Erfahrung in der Fertigung bewerbe ich mich als Leiter Operations. Ich bringe z. B. SMED-Kompetenz mit.",
    "Bei Weberit führte ich 38 Mitarbeitende. I reduced the scrap rate by 40 percent.",
    "Ich freue mich auf das Gespräch.",
]


def letter_data() -> dict:
    return {
        "header": {"name": "Marcus Weber", "address": "Koblenz", "email": "marcus.weber@example.com", "phone": "+49 261 0000000"},
        "recipient": {"name": None, "title": None, "company": "Rheinwerk Verpackungen GmbH", "address": None, "date": "12. Oktober 2026"},
        "body": {"paragraphs": LETTER_PARAS},
        "signature": {"closing": "Mit freundlichen Grüßen", "name": "Marcus Weber"},
    }


def vault() -> dict:
    return {
        "personal_info": {"name": "Marcus Weber"},
        "work_experience": [
            {"id": W1, "company": "Weberit Kunststofftechnik GmbH", "role": "Produktionsleiter", "start_date": "2016-03",
             "team_size": 38, "budget_managed": "2 Mio. EUR", "industry_context": "Plastics injection moulding",
             "responsibilities": [B_KEEP], "achievements": [B_CAPPED, "Rüstzeiten um 35 % reduziert."]},
            {"id": W2, "company": "Rheinmetall Montage AG", "role": "Schichtleiter", "start_date": "2010-01", "end_date": "2016-02",
             "responsibilities": [B_W2], "achievements": []},
        ],
        "skills": [{"id": "s1", "name": "Lean Management", "status": "confirmed"},
                   {"id": "s2", "name": "Kubernetes", "status": "unconfirmed"}],
        "education": [{"institution": "HWK Koblenz", "degree": "Industriemeister Metall"}],
    }


JOB = {"role_title": "Leiter Operations", "analysis": {"required_skills": ["Lean Management", "Shopfloor-Management"],
                                                       "nice_to_have_skills": ["SAP PP"]}}
LEDGER = [{"concept": "Shopfloor-Management", "status": "direct", "claimable": True, "sources": ["required"],
           "evidence": "work_experience[0].achievements[0]"}]


def rec(ts: int, stage: str, role, attempt, response, ok=True) -> dict:
    return {"ts": f"2026-10-12T08:00:{ts:02d}+00:00", "stage": stage, "review_role": role, "review_attempt": attempt,
            "model": "x/y", "system": "s", "prompt": "p", "response": response, "ok": ok}


def prose(w1_bullets, skills, w2_bullets=(B_W2,), summary="Produktionsleiter mit 14 Jahren Erfahrung in der diskreten Fertigung."):
    return {"summary": summary,
            "work": [{"id": W1, "bullets": list(w1_bullets), "projects": []}, {"id": W2, "bullets": list(w2_bullets), "projects": []}],
            "skills": list(skills)}


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")


def make_llm_log(path: Path) -> None:
    """Writer -> reviewer -> corrector#1 -> language pass -> terminal corrector#1; letter writer + condense."""
    writer = prose([B_KEEP, B_CAPPED, B_ENGLISH, B_UPSOLD], ["Lean Management", "SAP PP", "CSV", "Machine Learning"])
    corr1 = prose([B_KEEP, B_CAPPED, B_ENGLISH, B_UPSOLD], ["Lean Management", "SAP PP", "CSV", "Computer System Validation", "Machine Learning"])
    lang = prose([B_KEEP, B_CAPPED, B_ENGLISH, B_UPSOLD], ["Lean Management", "SAP PP", "CSV", "Computer System Validation", "Machine Learning"])
    term = prose([B_KEEP, B_CAPPED, B_ENGLISH, B_UPSOLD], ["Lean Management", "SAP PP", "CSV", "Computer System Validation", "Machine Learning", "Process Optimisation"])
    L = letter_data()
    l_writer = {**L, "body": {"paragraphs": LETTER_PARAS[:3] + ["Ich verantworte bei Weberit seit 2016 die gesamte Fertigung und senkte die Ausfallzeiten um 90 Prozent."] + LETTER_PARAS[3:]}}
    l_condense = {**L, "body": {"paragraphs": LETTER_PARAS[:3] + ["Ich verantworte bei Weberit seit 2016 die gesamte Fertigung und senkte die Ausfallzeiten um 90 Prozent."] + LETTER_PARAS[3:]}}
    write_jsonl(path, [
        rec(1, "cv_tailoring", None, None, writer),
        rec(2, "cv_tailoring", "reviewer", 1, {"approved": False, "issues": [{"severity": "blocking", "issue": "x"}], "feedback": ""}),
        rec(3, "cv_tailoring", "generator", 1, corr1),
        rec(4, "cv_language", "generator", 1, lang),
        rec(5, "cv_language", None, None, {"findings": []}),            # mislabelled outcome-critic record: NOT a draft
        rec(6, "cv_terminal_review", "generator", 1, term),
        rec(7, "cover_letter", None, None, l_writer),
        rec(8, "cover_letter", "reviewer", 1, {"approved": True, "issues": [], "feedback": ""}),
        rec(9, "letter_condense_pre_verdict", None, None, l_condense),
    ])


def make_run(root: Path, *, arm="baseline", model="luna", cell="same_marcus", idx=0, cell_type="same",
             status="ok", fixture: Path | None = None, with_log=True, cv=True, letter=True, lang="de",
             truth_cv=None) -> Path:
    rd = root / arm / model / cell / str(idx)
    rd.mkdir(parents=True)
    meta = {"arm": arm, "model": f"x/{model}", "model_slug": model, "cell_id": cell, "case": "operations_marcus_de",
            "cell_type": cell_type, "document_language": lang, "run_idx": idx, "status": status,
            "failed_stage": None if status == "ok" else "cv_tailoring", "error": None if status == "ok" else "invalid JSON",
            "fixture_dir": str(fixture) if fixture else None}
    (rd / "meta.json").write_text(json.dumps(meta))
    if status == "ok":
        if cv:
            (rd / "cv.json").write_text(json.dumps(tailored_data()))
        if letter:
            (rd / "letter.json").write_text(json.dumps(letter_data()))
        reports = {"cv": {"truthfulness_report": truth_cv}, "letter": {}} if truth_cv else {}
        (rd / "reports.json").write_text(json.dumps(reports))
        (rd / "pages.json").write_text(json.dumps({"cv": 2, "letter": 1}))
        if with_log:
            make_llm_log(rd / "llm.jsonl")
    return rd


@pytest.fixture()
def fixture_dir(tmp_path) -> Path:
    fd = tmp_path / "fixture"
    fd.mkdir()
    (fd / "vault.json").write_text(json.dumps(vault()))
    (fd / "job.json").write_text(json.dumps(JOB))
    (fd / "ledger.json").write_text(json.dumps(LEDGER))
    return fd


# --------------------------------------------------------------------------- #
# Items
# --------------------------------------------------------------------------- #


def test_flatten_cv_stable_locs_and_kinds():
    got = {i.loc: i for i in items.flatten_cv(tailored_data())}
    assert got["cv.summary[0]"].kind == "summary"
    assert got["cv.summary[1]"].text.startswith("Schwerpunkte")
    assert got["cv.work[0].bullets[1]"].text == B_ENGLISH
    assert got["cv.work[0].role"].kind == "role"
    assert got["cv.work[0].industry_context"].text == "Plastics injection moulding"
    assert got["cv.work[0].budget_managed"].kind == "fact"
    assert got["cv.work[0].projects[0].bullets[0]"].kind == "bullet"
    assert got["cv.work[1].bullets[0]"].text == B_W2
    assert got["cv.skills[3]"].text == "Computer System Validation"
    assert got["cv.education[0].degree"].text == "Industriemeister Metall"
    assert got["cv.languages[1].level"].text == "fluent"
    assert got["cv.certifications[0].name"].kind == "certification"
    # names, dates, team_size, contact are not text items
    assert not any("contact" in l or "start_date" in l or "team_size" in l for l in got)
    assert all(i.doc == "cv" for i in got.values())


def test_flatten_cv_draft_by_id_shares_delivered_locs():
    cv = tailored_data()
    draft = prose(["a", "b"], ["x"], w2_bullets=("c",))
    draft["work"] = list(reversed(draft["work"]))  # draft order differs from delivered
    got = {i.loc: i.text for i in items.flatten_cv(draft, index_map=items.index_by_id(cv))}
    assert got["cv.work[0].bullets[1]"] == "b"
    assert got["cv.work[1].bullets[0]"] == "c"


def test_flatten_letter_sentences_and_paragraphs():
    got = {i.loc: i for i in items.flatten_letter(letter_data())}
    assert got["letter.header.address"].text == "Koblenz"
    assert got["letter.signature.closing"].text == "Mit freundlichen Grüßen"
    assert got["letter.recipient.date"].kind == "header"
    # "z. B." must not split paragraph 1; paragraph 2 has two sentences
    assert got["letter.body[1][0]"].text.endswith("Leiter Operations.")
    assert got["letter.body[1][1]"].text == "Ich bringe z. B. SMED-Kompetenz mit."
    assert got["letter.body[2][1]"].text.startswith("I reduced")
    assert got["letter.body[2][1]"].parent == "letter.body[2]"
    paras = {i.loc: i for i in items.flatten_letter(letter_data(), sentences=False)}
    assert paras["letter.body[3]"].text == "Ich freue mich auf das Gespräch."
    assert not any("][" in l for l in paras)


@pytest.mark.parametrize("text,expected", [
    ("Das ist gut. Das auch.", ["Das ist gut.", "Das auch."]),
    ("Seit dem 3. Mai 2020 leite ich das Werk. Davor war ich Schichtleiter.", ["Seit dem 3. Mai 2020 leite ich das Werk.", "Davor war ich Schichtleiter."]),
    ("Ca. 38 Mitarbeitende, d. h. drei Schichten. Ergebnis: 2,3 % Ausschuss.", ["Ca. 38 Mitarbeitende, d. h. drei Schichten.", "Ergebnis: 2,3 % Ausschuss."]),
    ("I led the team (e.g. QA). It worked! Really?", ["I led the team (e.g. QA).", "It worked!", "Really?"]),
    ("", []),
])
def test_split_sentences(text, expected):
    assert items.split_sentences(text) == expected


def test_text_index_short_and_long_matching():
    idx = items.TextIndex(["SAP PP", "Führung von 38 Mitarbeitenden im Dreischichtbetrieb in Spritzguss."])
    assert idx.contains("sap  pp")
    assert not idx.contains("SAP")  # a short chip is never a substring hit
    assert idx.contains("Führung von 38 Mitarbeitenden im Dreischichtbetrieb in Spritzguss und Montage.", 0.85)
    assert not idx.contains("Einführung von KVP-Routinen in der Endmontage.")
    assert items.TextIndex(["Ich nutze SAP PP täglich im Werk"]).contains_word_sequence("SAP PP")


# --------------------------------------------------------------------------- #
# Drafts
# --------------------------------------------------------------------------- #


def test_extract_drafts_orders_by_ts_and_skips_reviewers_and_mislabelled(tmp_path):
    make_llm_log(tmp_path / "llm.jsonl")
    d = drafts_mod.extract_drafts(drafts_mod.load_records(tmp_path / "llm.jsonl"), items.index_by_id(tailored_data()))
    assert [x.label for x in d["cv"]] == [
        "cv_tailoring/writer", "cv_tailoring/corrector#1", "cv_language/corrector#1", "cv_terminal_review/corrector#1"]
    assert [x.label for x in d["letter"]] == ["cover_letter/writer", "letter_condense_pre_verdict"]
    assert d["cv"][0].stage_detail()["review_role"] is None
    assert d["cv"][1].stage_detail()["review_attempt"] == 1


def test_extract_drafts_letter_writer_with_empty_stage(tmp_path):
    """Real logs: the letter writer's first call carries stage=None (captured 2026-09-19/26)."""
    log = tmp_path / "llm.jsonl"
    write_jsonl(log, [rec(1, None, None, None, letter_data()), rec(2, "cover_letter", "generator", 1, letter_data())])
    d = drafts_mod.extract_drafts(drafts_mod.load_records(log))["letter"]
    assert [x.label for x in d] == ["cover_letter/writer", "cover_letter/corrector#1"]


def test_extract_drafts_smoke_run_label_leaks(tmp_path):
    """The package-1 smoke log: correctors logged with review_role=null, the letter writer under cv_audit."""
    log = tmp_path / "llm.jsonl"
    corrector = {**rec(3, "cv_tailoring", None, None, prose([B_KEEP], ["Lean"])), "system": "You are a tailored CV corrector. You receive (1)"}
    writer = {**rec(1, "cv_tailoring", None, None, prose([B_KEEP], ["Lean"])), "system": "You are an expert DACH career consultant."}
    letter = {**rec(5, "cv_audit", None, None, letter_data()), "system": "You are an expert DACH career coach writing"}
    critic = rec(6, "outcome_critic", None, None, {"findings": []})
    write_jsonl(log, [writer, corrector, letter, critic])
    d = drafts_mod.extract_drafts(drafts_mod.load_records(log))
    assert [x.label for x in d["cv"]] == ["cv_tailoring/writer", "cv_tailoring/corrector#1"]
    assert [x.label for x in d["letter"]] == ["cover_letter/writer"]


def test_extract_drafts_segmented_partials_accumulate(tmp_path):
    log = tmp_path / "llm.jsonl"
    write_jsonl(log, [
        rec(1, "cv_tailoring", None, None, {"summary": "Eine Zusammenfassung."}),
        rec(2, "cv_tailoring", None, None, {"bullets": ["Erster Punkt im Werk."], "projects": []}),
        rec(3, "cv_tailoring", None, None, {"skills": ["Lean"]}),
    ])
    d = drafts_mod.extract_drafts(drafts_mod.load_records(log))["cv"]
    assert len(d) == 3 and all(x.partial for x in d)
    assert {"Eine Zusammenfassung.", "Erster Punkt im Werk.", "Lean"} <= set(d[-1].texts)


# --------------------------------------------------------------------------- #
# U-del
# --------------------------------------------------------------------------- #


def test_u_del_lists_what_the_last_draft_had_and_the_document_lost(tmp_path):
    make_llm_log(tmp_path / "llm.jsonl")
    d = drafts_mod.extract_drafts(drafts_mod.load_records(tmp_path / "llm.jsonl"), items.index_by_id(tailored_data()))
    delivered = tailored_data()
    delivered["work_history"][0]["bullets"] = [B_KEEP, B_ENGLISH, B_UPSOLD]  # B_CAPPED removed by a cap
    delivered["skills"] = [s for s in delivered["skills"]]  # "Machine Learning" never delivered
    removals = find_removals("cv", d["cv"], items.flatten_cv(delivered))
    by_text = {r["text"]: r for r in removals}
    assert B_CAPPED in by_text and by_text[B_CAPPED]["kind"] == "removed"
    assert by_text[B_CAPPED]["draft_loc"] == "cv.work[0].bullets[1]"
    assert "Machine Learning" in by_text
    assert B_KEEP not in by_text and "SAP PP" not in by_text


def test_u_del_altered_and_chip_conveyed_in_prose_is_not_a_removal(tmp_path):
    drafts = {"cv": [drafts_mod.Draft(0, "t", "cv", "cv_terminal_review", "generator", 1, "x", False, [
        items.Item("cv.work[0].bullets[0]", "Senkung der Ausschussquote von 4,1 % auf 2,3 % durch SMED und Shopfloor-Management im Werk.", "cv", "bullet"),
        items.Item("cv.skills[0]", "SMED", "cv", "skill"),
        items.Item("cv.skills[1]", "Kanban", "cv", "skill"),
    ])]}
    delivered = [
        items.Item("cv.work[0].bullets[0]", "Senkung der Ausschussquote von 4,1 % auf 2,3 % durch SMED.", "cv", "bullet"),
    ]
    got = {r["text"][:20]: r for r in find_removals("cv", drafts["cv"], delivered)}
    assert got["Senkung der Ausschus"]["kind"] == "altered"
    assert got["Senkung der Ausschus"]["counterpart"].startswith("Senkung")
    assert "SMED" not in got            # still conveyed inside the delivered bullet
    assert "Kanban" in got


def test_u_del_letter_sentence_removed_and_llm_stage_drop(tmp_path):
    make_llm_log(tmp_path / "llm.jsonl")
    d = drafts_mod.extract_drafts(drafts_mod.load_records(tmp_path / "llm.jsonl"))
    removals = find_removals("letter", d["letter"], items.flatten_letter(letter_data()))
    assert [r["text"] for r in removals] == [
        "Ich verantworte bei Weberit seit 2016 die gesamte Fertigung und senkte die Ausfallzeiten um 90 Prozent."]
    assert removals[0]["draft_loc"].startswith("letter.body[")
    # an earlier draft's content that a later model draft lost (and the document does not have)
    cv_drafts = [
        drafts_mod.Draft(0, "1", "cv", "cv_tailoring", None, None, "w", False, [items.Item("cv.work[0].bullets[0]", "Aufbau eines Qualitätsregelkreises mit Kunden.", "cv", "bullet")]),
        drafts_mod.Draft(1, "2", "cv", "cv_terminal_review", "generator", 1, "t", False, [items.Item("cv.work[0].bullets[0]", "Völlig anderer Text über Schichtplanung.", "cv", "bullet")]),
    ]
    delivered = [items.Item("cv.work[0].bullets[0]", "Völlig anderer Text über Schichtplanung.", "cv", "bullet")]
    assert find_removals("cv", cv_drafts, delivered) == []
    dropped = find_removals("cv", cv_drafts, delivered, include_llm_stage=True)
    assert dropped[0]["kind"] == "dropped_by_llm" and dropped[0]["removed_by_draft_idx"] == 1


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #


@pytest.fixture()
def drafts_of_log(tmp_path):
    make_llm_log(tmp_path / "llm.jsonl")
    return drafts_mod.extract_drafts(drafts_mod.load_records(tmp_path / "llm.jsonl"), items.index_by_id(tailored_data()))


def test_attribution_names_the_first_draft_in_ts_order(drafts_of_log):
    issues = [
        {"class": "L", "doc": "cv", "loc": "cv.work[0].bullets[1]", "text": B_ENGLISH, "evidence": {}, "subclass": "en-in-de", "stage": None},
        # "Computer System Validation" first appears in corrector#1 (writer only had "CSV")
        {"class": "D", "doc": "cv", "loc": "cv.skills[3]", "text": "Computer System Validation",
         "evidence": {"anchor_text": "CSV"}, "subclass": "skill~skill", "stage": None},
        {"class": "S", "doc": "cv", "loc": "cv.work[0].bullets[2]", "text": B_UPSOLD, "evidence": {"claim": "400 Mitarbeitenden"}, "subclass": "inflated", "stage": None},
        # vault-verbatim join item: in no draft -> compose
        {"class": "L", "doc": "cv", "loc": "cv.work[0].industry_context", "text": "Plastics injection moulding", "evidence": {}, "subclass": "en-in-de", "stage": None},
    ]
    attribution.attribute_issues(issues, drafts_of_log)
    assert issues[0]["stage"] == "cv_tailoring/writer"
    assert issues[0]["stage_detail"]["review_role"] is None and issues[0]["stage_detail"]["draft_idx"] == 0
    assert issues[1]["stage"] == "cv_tailoring/corrector#1"
    assert issues[1]["stage_detail"]["review_attempt"] == 1
    assert issues[2]["stage"] == "cv_tailoring/writer"
    assert issues[3]["stage"] == "compose"


def test_attribution_u_del_compose_and_llm_step(drafts_of_log):
    capped = {"class": "U", "doc": "cv", "loc": "x", "text": "Machine Learning", "evidence": {}, "subclass": "del", "stage": None}
    # present in every draft through the last one -> removed by the deterministic tail
    attribution.attribute_issue(capped, drafts_of_log)
    assert capped["stage"] == "compose"
    # "Process Optimisation" exists only in the terminal draft -> the first draft after the last one
    # containing a text that was lost mid-way: use a text lost after the writer
    lost = {"class": "U", "doc": "cv", "loc": "x", "text": "Lean Management", "evidence": {}, "subclass": "del", "stage": None}
    attribution.attribute_issue(lost, drafts_of_log)
    assert lost["stage"] == "compose"
    drafts = {"cv": drafts_of_log["cv"][:1] + [
        drafts_mod.Draft(1, "2026-10-12T08:00:30+00:00", "cv", "cv_terminal_review", "generator", 2, "cv_terminal_review/corrector#2", False,
                         [items.Item("cv.skills[0]", "Etwas ganz anderes", "cv", "skill")])]}
    gone = {"class": "U", "doc": "cv", "loc": "x", "text": B_CAPPED, "evidence": {}, "subclass": "del", "stage": None}
    attribution.attribute_issue(gone, drafts)
    assert gone["stage"] == "cv_terminal_review/corrector#2"


def test_attribution_omit_never_drafted_and_unattributed(drafts_of_log):
    omit = {"class": "U", "doc": "both", "loc": "work_experience[0].achievements[9]", "text": "Aufbau eines Lieferantenaudits mit TÜV", "evidence": {}, "subclass": "omit", "stage": None}
    attribution.attribute_issue(omit, drafts_of_log)
    assert omit["stage"] == "never_drafted"
    none = {"class": "L", "doc": "letter", "loc": "x", "text": "whatever text here for testing", "evidence": {}, "subclass": "en-in-de", "stage": None}
    attribution.attribute_issue(none, {"cv": [], "letter": []})
    assert none["stage"] == "unattributed"


# --------------------------------------------------------------------------- #
# Class scorers
# --------------------------------------------------------------------------- #


def test_language_scorer_validates_locs_and_records_cross_check():
    judge = judge_mod.FakeJudge({"L": {
        "issues": [{"loc": "cv.work[0].bullets[1]", "text": "x", "detected_language": "en"},
                   {"loc": "cv.skills[9]", "text": "invented", "detected_language": "en"}],
        "exempt": [{"loc": "cv.work[0].role", "text": "Produktionsleiter", "reason": "job title"}]}})
    out = score_language(judge, "cv", items.flatten_cv(tailored_data()), "de")
    assert [i["loc"] for i in out["issues"]] == ["cv.work[0].bullets[1]"]
    assert out["issues"][0]["text"] == B_ENGLISH            # the delivered text, not the judge's quote
    assert out["issues"][0]["subclass"] == "en-in-de"
    assert out["issues"][0]["class"] == "L" and out["issues"][0]["stage"] is None
    assert out["dropped_unknown_loc"] == 1
    # O-2 field exemption: the role is exempt by location and never reaches the judge
    roles = [e for e in out["exempt"] if e["loc"] == "cv.work[0].role"]
    assert roles and roles[0]["reason"] == "field exempt (O-2)"
    cc = out["cross_check"]
    assert cc is not None and "cv.work[0].bullets[1]" in cc["both"]   # the product predicate agrees on a 9-word English bullet
    system, user = judge.calls[0][1], judge.calls[0][2]
    assert "German" in system and "DOCUMENT LANGUAGE" in user
    assert '"loc": "cv.skills[3]"' in user
    assert '"loc": "cv.work[0].role"' not in user


def test_duplicate_group_of_k_counts_k_minus_1_and_merges_overlap():
    its = [i for i in items.flatten_cv(tailored_data()) if i.kind in ("skill", "bullet", "summary")]
    judge = judge_mod.FakeJudge({"D": {"groups": [
        {"locs": ["cv.skills[2]", "cv.skills[3]"], "texts": ["CSV", "Computer System Validation"], "why": "acronym"},
        {"locs": ["cv.skills[3]", "cv.skills[5]", "cv.skills[99]"], "texts": [], "why": "synonym"}]}})
    out = score_duplicates(judge, "cv", its, "de")
    assert len(out["groups"]) == 1 and out["groups"][0]["locs"] == ["cv.skills[2]", "cv.skills[3]", "cv.skills[5]"]
    assert [i["loc"] for i in out["issues"]] == ["cv.skills[3]", "cv.skills[5]"]       # k = 3 -> 2 issues
    assert out["issues"][0]["subclass"] == "skill~skill"
    assert out["issues"][0]["evidence"]["anchor_loc"] == "cv.skills[2]"
    assert out["issues"][0]["deterministic_candidate"] is True   # acronym pair also found by code
    assert out["dropped_unknown_loc"] == 1


def test_candidate_pairs_find_acronym_containment_and_overlap():
    its = [items.Item(f"l{n}", t, "cv", "skill") for n, t in enumerate(
        ["CSV", "Computer System Validation", "Lean Management", "Lean", "ISO 9001", "ISO 45001"])]
    pairs = {(a, b): r for a, b, r in candidate_pairs(its)}
    assert pairs[("l0", "l1")] == "acronym"
    assert ("l2", "l3") in pairs                      # containment
    assert ("l4", "l5") not in pairs or pairs[("l4", "l5")] != "acronym"


def test_upselling_scorer_and_oracle_agreement_table():
    its = items.flatten_cv(tailored_data())
    judge = judge_mod.FakeJudge({"S": {"issues": [
        {"loc": "cv.work[0].bullets[2]", "claim": "400 Mitarbeitenden", "kind": "inflated", "vault_ref_or_null": "work_experience[0]", "why": "38"},
        {"loc": "cv.work[0].bullets[0]", "claim": "x", "kind": "weird", "vault_ref_or_null": None, "why": ""}]}})
    out = score_upselling(judge, "cv", its, "de", vault())
    assert [i["loc"] for i in out["issues"]] == ["cv.work[0].bullets[2]"]
    assert out["issues"][0]["subclass"] == "inflated" and out["dropped_bad_kind"] == 1
    assert "Kubernetes" not in judge.calls[0][2]          # unconfirmed vault skills are not evidence
    assert "work_experience[0].achievements[0]:" in judge.calls[0][2]
    report = {"claims": [
        {"claim": {"text": B_UPSOLD, "location": "work_history[0].bullets[2]"}, "verdict": {"verdict": "grounded"}},
        {"claim": {"text": B_KEEP, "location": "work_history[0].bullets[0]"}, "verdict": {"verdict": "unbacked"}},
        {"claim": {"text": "Etwas ohne Entsprechung im Dokument", "location": "x"}, "verdict": {"verdict": "unbacked"}}]}
    ag = oracle_agreement("cv", its, report, out["issues"])
    assert ag["counts"] == {"both_flag": 0, "oracle_only": 1, "judge_only": 1, "neither": 0}
    assert ag["oracle_claims_unmatched"] == 1


def test_underselling_scorer_judges_removals_omissions_understatements():
    its_cv, its_l = items.flatten_cv(tailored_data()), items.flatten_letter(letter_data())
    removals = [{"doc": "cv", "draft_loc": "cv.work[0].bullets[1]", "text": B_CAPPED, "kind": "removed", "counterpart": None, "item_kind": "bullet"},
                {"doc": "cv", "draft_loc": "cv.skills[7]", "text": "Machine Learning", "kind": "removed", "counterpart": None, "item_kind": "skill"},
                {"doc": "cv", "draft_loc": "cv.skills[8]", "text": "Lean", "kind": "removed", "counterpart": None, "item_kind": "skill"}]
    judge = judge_mod.FakeJudge({"U": lambda s, u: {
        "removed": [{"id": "R1", "grounded": True, "relevant": True, "why": "measured scrap result"},
                    {"id": "R2", "grounded": False, "relevant": True, "why": "not in vault"},
                    {"id": "R3", "grounded": True, "relevant": True, "conveyed_elsewhere": True,
                     "conveyed_loc": "cv.skills[0]", "why": "Lean Management still listed"},
                    {"id": "R77", "grounded": True, "relevant": True, "why": "invented id"}],
        "omitted": [{"vault_ref": "work_experience[0].achievements[1]", "jd_item": "Lean Management", "why": "SMED result missing"},
                    {"vault_ref": "work_experience[0].achievements[0]", "jd_item": "Kubernetes", "why": "not a required item"},
                    {"vault_ref": "work_experience[9].achievements[0]", "jd_item": "x", "why": "invented path"}],
        "understated": [{"loc": "cv.work[0].bullets[0]", "vault_ref": "work_experience[0]", "why": "weaker"},
                        {"loc": "cv.nowhere", "vault_ref": None, "why": ""}]}})
    out = score_underselling(judge, language="de", vault=vault(), job=JOB, ledger=LEDGER,
                             cv_items=its_cv, letter_items=its_l, removals=removals)
    kinds = {(i["subclass"], i["doc"], i["loc"]) for i in out["issues"]}
    assert kinds == {("del", "cv", "cv.work[0].bullets[1]"), ("omit", "both", "work_experience[0].achievements[1]"),
                     ("under", "cv", "cv.work[0].bullets[0]")}
    assert out["dropped_unknown_id"] == 1 and out["dropped_unknown_vault_ref"] == 1 and out["dropped_unknown_loc"] == 1
    assert out["removals_conveyed_elsewhere"] == 1        # P-4: a cut label whose competence is still on the page
    assert out["dropped_jd_item_not_required"] == 1       # P-4: an omission counts only against a required item
    omit = next(i for i in out["issues"] if i["subclass"] == "omit")
    assert omit["text"] == "Rüstzeiten um 35 % reduziert."          # vault_ref resolved to the vault text
    user = judge.calls[0][2]
    assert '"id": "R1"' in user and "Shopfloor-Management" in user and "evidence" in user   # ledger row rendered beside the JD item


def test_prompts_carry_the_spec_wording_verbatim():
    for cls, needle in (("L", "NOT a defect (report under `exempt` instead, not `issues`)"),
                        ("U", "Only grounded AND relevant AND not conveyed elsewhere removals are defects."),
                        ("D", "The same fact in the CV and in the letter is NOT a defect."),
                        ("S", "Statements about the hiring company are out of scope.")):
        sp = prompts.system_prompt(cls, "de")
        assert sp.startswith(prompts.PREAMBLE) and needle in sp
        assert "{lang}" not in sp
    assert "German" in prompts.system_prompt("L", "de") and "English" in prompts.system_prompt("L", "en")
    assert "A missed defect costs less than an invented one" in prompts.PREAMBLE


# --------------------------------------------------------------------------- #
# Judge client (no real endpoint: httpx.MockTransport)
# --------------------------------------------------------------------------- #


def _client(handler, tmp_path, model="m/x"):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return judge_mod.JudgeClient(model=model, api_key="k", cache_dir=tmp_path / "cache", http=http, sleep=lambda s: None)


def _ok(content: str, usage=None):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5}})


def test_judge_requires_env_and_fails_loudly(monkeypatch):
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(judge_mod.JudgeError, match="JUDGE_MODEL"):
        judge_mod.JudgeClient()
    monkeypatch.setenv("JUDGE_MODEL", "some/model")
    with pytest.raises(judge_mod.JudgeError, match="OPENROUTER_API_KEY"):
        judge_mod.JudgeClient()


def test_judge_cache_key_covers_model_system_and_user(tmp_path):
    calls = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        calls.append(body)
        return _ok('{"issues": []}')

    c = _client(handler, tmp_path)
    assert c.complete_json("sys", "usr", cls="S") == {"issues": []}
    assert c.complete_json("sys", "usr", cls="S") == {"issues": []}
    assert len(calls) == 1 and c.stats.cache_hits == 1            # second call is free
    assert calls[0]["temperature"] == 0 and calls[0]["model"] == "m/x"
    c.complete_json("sys", "usr2", cls="S")
    c.complete_json("sys2", "usr", cls="S")
    assert len(calls) == 3
    c2 = _client(handler, tmp_path, model="m/other")
    c2.complete_json("sys", "usr", cls="S")
    assert len(calls) == 4                                         # a different model never hits the cache
    assert judge_mod.cache_key("m", "a", "b") != judge_mod.cache_key("m", "ab", "")
    # a fresh client over the same directory still hits
    _client(handler, tmp_path).complete_json("sys", "usr", cls="S")
    assert len(calls) == 4


def test_judge_retries_malformed_json_once_and_never_caches_it(tmp_path):
    replies = iter(["I think there are no issues.", '```json\n{"groups": []}\n```'])
    calls = []

    def handler(req):
        calls.append(json.loads(req.content))
        return _ok(next(replies))

    c = _client(handler, tmp_path)
    assert c.complete_json("s", "u", cls="D") == {"groups": []}
    assert len(calls) == 2 and calls[1]["messages"][-1]["role"] == "user"
    assert c.stats.retries == 1

    def always_bad(req):
        return _ok("nope")

    bad = _client(always_bad, tmp_path / "other")
    with pytest.raises(judge_mod.JudgeError, match="malformed JSON twice"):
        bad.complete_json("s", "u", cls="D")
    assert not list((tmp_path / "other" / "cache").rglob("*.json")) if (tmp_path / "other" / "cache").exists() else True


def test_judge_http_error_is_loud_and_transient_is_retried(tmp_path):
    seq = iter([httpx.Response(429, text="slow down"), _ok('{"a": 1}')])
    c = _client(lambda req: next(seq), tmp_path)
    assert c.complete_json("s", "u") == {"a": 1}
    c2 = _client(lambda req: httpx.Response(401, text="bad key"), tmp_path / "x")
    with pytest.raises(judge_mod.JudgeError, match="401"):
        c2.complete_json("s", "u")


def test_fake_judge_is_canned_per_class_and_records_calls():
    fj = judge_mod.FakeJudge({"D": {"groups": [{"locs": ["a", "b"]}]}})
    assert fj.complete_json("s", "u", cls="D") == {"groups": [{"locs": ["a", "b"]}]}
    assert fj.complete_json("s", "u", cls="L") == {"issues": [], "exempt": []}
    assert [c[0] for c in fj.calls] == ["D", "L"]


# --------------------------------------------------------------------------- #
# score_run end to end (FakeJudge)
# --------------------------------------------------------------------------- #


def canned_judge() -> judge_mod.FakeJudge:
    return judge_mod.FakeJudge({
        "L": lambda s, u: {"issues": [{"loc": "cv.work[0].bullets[1]", "text": "x", "detected_language": "en"}]
                           if "DOCUMENT: cv" in u else
                           [{"loc": "letter.body[2][1]", "text": "x", "detected_language": "en"}],
                           "exempt": []},
        "D": lambda s, u: {"groups": [{"locs": ["cv.skills[2]", "cv.skills[3]"], "texts": [], "why": "acronym"}] if "cv.skills[2]" in u else []},
        "S": lambda s, u: {"issues": [{"loc": "cv.work[0].bullets[2]", "claim": "400", "kind": "inflated", "vault_ref_or_null": None, "why": "38"}]
                           if "cv.work[0].bullets[2]" in u else []},
        "U": lambda s, u: {"removed": [{"id": "R1", "grounded": True, "relevant": True, "why": "true"}],
                           "omitted": [{"vault_ref": "work_experience[0].achievements[1]", "jd_item": "Lean Management", "why": "x"}],
                           "understated": []},
    })


def test_score_run_end_to_end(tmp_path, fixture_dir):
    truth = {"claims": [{"claim": {"text": B_UPSOLD, "location": "work_history[0].bullets[2]"}, "verdict": {"verdict": "grounded"}}]}
    rd = make_run(tmp_path / "out", fixture=fixture_dir, truth_cv=truth)
    res = score_run(rd, canned_judge(), fixture_bases=[tmp_path])
    assert res["status"] == "scored" and res["docs"] == ["cv", "letter"]
    by = {(i["class"], i["doc"], i["subclass"]): i for i in res["issues"]}
    assert ("L", "cv", "en-in-de") in by and ("L", "letter", "en-in-de") in by
    assert by[("L", "cv", "en-in-de")]["stage"] == "cv_tailoring/writer"
    assert by[("D", "cv", "skill~skill")]["stage"] == "cv_tailoring/corrector#1"
    assert by[("S", "cv", "inflated")]["stage"] == "cv_tailoring/writer"
    assert by[("U", "both", "omit")]["stage"] == "never_drafted"
    assert res["counts"]["D"]["cv"] == 1 and res["counts"]["L"]["letter"] == 1
    # U-del: the letter sentence the condense draft had and the delivered letter lost
    assert any("Ausfallzeiten um 90 Prozent" in r["text"] for r in res["details"]["U"]["removals"])
    assert res["oracle_agreement"]["cv"]["counts"]["judge_only"] == 1      # Oracle said grounded, judge said inflated
    assert (rd / "score.json").exists()
    assert json.loads((rd / "score.json").read_text())["run_id"] == "baseline/luna/same_marcus/0"
    assert res["instrument"]["calls"] >= 6 and res["instrument"]["judge_model"] == "fake-judge"


def test_score_run_does_not_score_a_failed_run_and_warns_on_missing_fixture(tmp_path, fixture_dir):
    failed = make_run(tmp_path / "out", status="failed", idx=1)
    fj = canned_judge()
    assert score_run(failed, fj)["status"] == "not_scored_failed_run" and fj.calls == []
    no_fx = make_run(tmp_path / "out", idx=2, fixture=None)
    res = score_run(no_fx, canned_judge(), fixture_bases=[tmp_path])
    assert res["classes_skipped"] == ["S", "U"] and any("fixture_dir" in w for w in res["warnings"])
    assert res["counts"]["S"] == {"cv": 0, "letter": 0, "both": 0}


# --------------------------------------------------------------------------- #
# Report: bootstrap, failed runs, arm diff
# --------------------------------------------------------------------------- #


def _write_score(rd: Path, issues: list[dict], docs=("cv", "letter")) -> None:
    meta = json.loads((rd / "meta.json").read_text())
    run_id = "/".join(str(meta[k]) for k in ("arm", "model_slug", "cell_id", "run_idx"))
    (rd / "score.json").write_text(json.dumps({"status": "scored", "run_id": run_id, "docs": list(docs), "issues": issues, "exempt": [], "counts": {}}))


def _issue(cls, doc="cv", stage="cv_tailoring/writer"):
    return {"class": cls, "doc": doc, "loc": "l", "text": "t", "evidence": {}, "subclass": "s", "stage": stage}


def test_bootstrap_is_deterministic_and_brackets_the_point():
    nums, dens = [3.0, 0.0, 5.0, 1.0, 2.0, 4.0], [2.0] * 6
    ix = report_mod._indices(6, "k")
    a, b = report_mod.ratio_ci(nums, dens, ix), report_mod.ratio_ci(nums, dens, report_mod._indices(6, "k"))
    assert a == b                                        # fixed seed -> identical numbers
    point, lo, hi = a
    assert point == pytest.approx(100 * 15 / 12) and lo <= point <= hi
    assert report_mod._indices(6, "k") != report_mod._indices(6, "other")
    assert len(ix) == report_mod.RESAMPLES == 1000
    assert report_mod.ratio_ci([0.0], [0.0], report_mod._indices(1, "z")) == (None, None, None)


def test_report_counts_failed_runs_separately_and_never_as_zero(tmp_path):
    out = tmp_path / "out"
    for i in range(4):
        rd = make_run(out, idx=i, with_log=False)
        _write_score(rd, [_issue("L"), _issue("L", "letter", "compose")] if i < 2 else [_issue("D")])
    make_run(out, idx=4, status="failed", with_log=False)
    make_run(out, idx=5, with_log=False)              # ok but never scored
    rep = report_mod.build_report([str(out)])
    assert rep["runs"] == {"scored": 4, "failed": 1, "unscored": 1}
    assert rep["failed_runs"][0]["failed_stage"] == "cv_tailoring" and rep["failed_runs"][0]["error"] == "invalid JSON"
    assert rep["unscored_runs"][0]["reason"] == "no score.json"
    overall = rep["arms"]["baseline"]["groups"]["overall"]
    assert overall["runs"] == 4 and overall["all"]["documents"] == 8      # failed run's 2 docs are not in the denominator
    assert overall["all"]["L"]["issues"] == 4 and overall["all"]["L"]["rate"] == pytest.approx(50.0)
    assert overall["all"]["L"]["pct_docs"] == pytest.approx(50.0)         # cv+letter of 2 runs of 8 documents
    assert overall["cv"]["L"]["rate"] == pytest.approx(50.0) and overall["letter"]["documents"] == 4
    assert overall["all"]["D"]["issues"] == 2
    assert rep["arms"]["baseline"]["stage_histogram"]["L"] == {"cv_tailoring/writer": 2, "compose": 2}
    md = report_mod.render_markdown(rep)
    assert "Failed runs" in md and "invalid JSON" in md and "50.0 [" in md


def test_report_arm_diff_and_untrusted_class(tmp_path):
    out = tmp_path / "out"
    for i in range(6):
        _write_score(make_run(out, arm="baseline", idx=i, with_log=False), [_issue("L")] * 3)
        _write_score(make_run(out, arm="armA", idx=i, with_log=False), [_issue("L")] * (1 if i < 5 else 2))
    (tmp_path / "cal.json").write_text(json.dumps({"classes": {"S": {"trusted": False}, "L": {"trusted": True}}}))
    rep = report_mod.build_report([str(out)], calibration=str(tmp_path / "cal.json"))
    row = next(r for r in rep["arm_diffs"] if r["scope"] == "overall")
    assert (row["arm_a"], row["arm_b"]) == ("baseline", "armA")
    assert row["L"]["diff"] < 0 and row["L"]["hi"] < 0 and row["L"]["significant"] is True
    assert row["D"]["diff"] == 0 and row["D"]["significant"] is False
    assert rep["untrusted_classes"] == ["S"]
    md = report_mod.render_markdown(rep)
    assert "instrument not trusted" in md and "Arm diff" in md
    # the whole report is reproducible byte for byte
    assert report_mod.build_report([str(out)], calibration=str(tmp_path / "cal.json")) == rep


def test_report_main_writes_md_and_json(tmp_path):
    out = tmp_path / "out"
    _write_score(make_run(out, with_log=False), [_issue("U", "both")])
    assert report_mod.main([str(out), "--out", str(tmp_path / "rep")]) == 0
    assert (tmp_path / "rep" / "report.md").exists()
    data = json.loads((tmp_path / "rep" / "report.json").read_text())
    assert data["arms"]["baseline"]["groups"]["overall"]["all"]["U"]["issues"] == 1
    assert data["arms"]["baseline"]["groups"]["overall"]["all"]["U"]["docs_with_issue"] == 0   # run-level "both" is in no document


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #


def test_calibration_precision_recall_and_untrusted_flag(tmp_path):
    out = tmp_path / "out"
    r0 = make_run(out, idx=0, with_log=False)
    r1 = make_run(out, idx=1, with_log=False)
    _write_score(r0, [  # L: 3 judge findings, 2 correct; S: 1 judge finding, wrong
        {**_issue("L"), "loc": "cv.skills[0]", "text": "Machine Learning"},
        {**_issue("L"), "loc": "cv.skills[1]", "text": "Process Optimisation"},
        {**_issue("L"), "loc": "cv.skills[2]", "text": "Quality Gate Review"},
        {**_issue("S"), "loc": "cv.work[0].bullets[0]", "text": "Leitung eines Werks"},
        {**_issue("D"), "loc": "cv.skills[3]", "text": "Computer System Validation", "evidence": {"group_locs": ["cv.skills[2]", "cv.skills[3]"]}},
    ])
    _write_score(r1, [{**_issue("L", "letter"), "loc": "letter.body[2][1]", "text": "I reduced scrap"}])
    labels = tmp_path / "labels.jsonl"
    labels.write_text("\n".join(json.dumps(x) for x in [
        {"kind": "doc", "run": "baseline/luna/same_marcus/0", "doc": "cv"},
        {"kind": "doc", "run": "baseline/luna/same_marcus/0", "doc": "letter"},
        {"kind": "doc", "run": "baseline/luna/same_marcus/1", "doc": "cv"},       # fully labelled, zero defects
        {"kind": "issue", "run": "baseline/luna/same_marcus/0", "doc": "cv", "class": "L", "loc": "cv.skills[0]", "text": "Machine Learning"},
        {"kind": "issue", "run": "baseline/luna/same_marcus/0", "doc": "cv", "class": "L", "loc": "cv.skills[1]"},
        {"kind": "issue", "run": "baseline/luna/same_marcus/0", "doc": "cv", "class": "L", "loc": "cv.work[1].bullets[0]", "text": "An English bullet nobody flagged"},
        {"kind": "issue", "run": "baseline/luna/same_marcus/0", "doc": "cv", "class": "D", "loc": "cv.skills[2]", "text": "CSV"},
        {"kind": "issue", "run": "baseline/luna/same_marcus/1", "doc": "letter", "class": "L", "loc": "letter.body[2][1]"},   # letter of run 1 NOT labelled
    ]))
    res = calibrate_mod.calibrate(labels, [str(out)])
    L, S, D = res["classes"]["L"], res["classes"]["S"], res["classes"]["D"]
    assert (L["tp"], L["fp"], L["fn"]) == (2, 1, 1) and L["precision"] == pytest.approx(2 / 3) and L["recall"] == pytest.approx(2 / 3)
    assert L["trusted"] is False and L["status"] == "instrument not trusted"       # 0.67 < 0.8
    assert S["precision"] == 0.0 and S["trusted"] is False and S["recall"] is None
    assert D["tp"] == 1 and D["trusted"] is True                                   # matched through the group's member locs
    assert res["classes"]["U"]["precision"] is None and res["classes"]["U"]["trusted"] is None
    assert res["documents_labelled"] == 3
    assert res["ignored_labels_outside_labelled_docs"] == 1                        # the unlabelled letter's label does not count
    assert L["false_positives"][0]["loc"] == "cv.skills[2]" and L["misses"][0]["loc"] == "cv.work[1].bullets[0]"
    # the file feeds report.py
    cal = tmp_path / "calibration.json"
    assert calibrate_mod.main([str(labels), str(out), "--out", str(cal)]) == 0
    assert report_mod.build_report([str(out)], calibration=str(cal))["untrusted_classes"] == ["L", "S"]


def test_ledger_source_order_effective_then_gap_then_ledger(tmp_path, fixture_dir):
    (fixture_dir / "gap.json").write_text(json.dumps({"keyword_ledger": [{"concept": "FromGap", "sources": ["required"], "status": "direct"}]}))
    assert inputs.load_fixture({"fixture_dir": str(fixture_dir)}).ledger[0]["concept"] == "FromGap"
    (fixture_dir / "gap.json").unlink()
    assert inputs.load_fixture({"fixture_dir": str(fixture_dir)}).ledger[0]["concept"] == "Shopfloor-Management"
    rd = make_run(tmp_path / "out", fixture=fixture_dir)
    (rd / "ledger_effective.json").write_text(json.dumps([{"concept": "EffectiveOnly", "sources": ["required"], "status": "gap", "claimable": False}]))
    fj = canned_judge()
    score_run(rd, fj, write=False)
    u_user = next(c[2] for c in fj.calls if c[0] == "U")
    assert "EffectiveOnly" in u_user and "Shopfloor-Management" in u_user  # job.json lists it; the ledger row now says gap


def test_render_view_scores_the_page_not_the_row():
    """The pilot (2026-10-12) counted "Deutsch · Muttersprache" in an English CV as
    language mix; the page renders it "German · Native" (``localize_languages``).
    The scorer must flatten the render view."""
    from score.score_run import render_view

    cv = {
        "contact": {"name": "Stefan Brandt", "email": "stefan.brandt@example.com"},
        "languages": [{"language": "Deutsch", "level": "Muttersprache"}],
    }
    page = render_view(cv, "en")
    assert page["languages"] == [{"language": "German", "level": "Native"}]
    assert cv["languages"][0]["language"] == "Deutsch"  # the row is not mutated


def test_duplicate_class_is_the_founders_definition():
    from score.score_run import D_STRUCTURES

    assert D_STRUCTURES == {"skill~skill", "bullet~bullet"}
