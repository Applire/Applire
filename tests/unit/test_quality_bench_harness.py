# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 — hermetic tests for the quality-bench generation harness.

``scripts/quality_bench/run.py`` only ever produces its real numbers by hand, against a
paid provider. These tests pin the three things that would otherwise rot silently:

* the **fixture format** (``tests/files/quality_bench/FIXTURE_FORMAT.md``) and its loader,
* the **seed** — the rows the REAL pipelines read, built from a fixture,
* the **run-output contract** (meta/cv/letter/reports/pages/llm) for an ok run and for
  every failure shape (CV failed, letter failed, raised, timed out) — failed runs are
  recorded, never skipped.

No Chromium, no network, no real provider. The contract tests stub the two service doors;
``test_real_pipeline_on_mock_provider`` runs the REAL ``generate_cv`` / ``generate_cover_letter``
(mock provider, page rendering patched off) so a product-side signature or ownership change
that breaks the harness fails here instead of mid-measurement.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(REPO_ROOT / "scripts"), str(REPO_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from quality_bench import fixture as fixture_mod  # noqa: E402
from quality_bench import run as run_mod  # noqa: E402

SMOKE = REPO_ROOT / "tests" / "files" / "quality_bench" / "_smoke" / "same_de"

META_KEYS = {
    "arm", "model", "model_slug", "cell_id", "case", "cell_type", "document_language", "run_idx",
    "git_sha", "started", "finished", "status", "failed_stage", "error", "fixture_dir",
}


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def restore_process_state(monkeypatch):
    """Undo everything ``configure_environment`` / ``_repoint_database`` touch in-process."""
    from applire.config import settings

    for name in ("llm_provider", "llm_debug_log", "llm_debug_log_dir", "openrouter_model",
                 "openrouter_disable_thinking", "requesty_model", "requesty_disable_thinking"):
        monkeypatch.setattr(settings, name, getattr(settings, name))
    monkeypatch.setattr(os, "environ", os.environ.copy())
    import applire.db.session as db_session

    orig_factory, orig_engine = db_session.AsyncSessionLocal, db_session.engine
    yield
    # Modules first imported WHILE a run had the DB repointed bound that run's factory, so
    # restore by value over every loaded applire module, not from a setup-time snapshot.
    for name, mod in list(sys.modules.items()):
        if not name.startswith("applire.") or mod is None:
            continue
        if getattr(mod, "AsyncSessionLocal", orig_factory) is not orig_factory:
            mod.AsyncSessionLocal = orig_factory
        if ("AsyncSessionLocal" in vars(mod) or name == "applire.db.session") and hasattr(mod, "engine"):
            if mod.engine is not orig_engine:
                mod.engine = orig_engine


def _spec(tmp_path, *, timeout=60.0, pages=False, fx=None):
    fx = fx or fixture_mod.load_fixture(SMOKE)
    return run_mod.RunSpec(
        fixture=fx, arm="armX", provider="mock", model="mock", run_idx=3,
        out_root=tmp_path, timeout=timeout, measure_pages=pages,
    )


def _run(spec):
    return asyncio.run(run_mod.execute_run(spec))


def _copy_fixture(tmp_path, **meta_overrides) -> Path:
    dst = tmp_path / "fx" / "cell_a"
    shutil.copytree(SMOKE, dst)
    if meta_overrides:
        meta = json.loads((dst / "fixture.json").read_text(encoding="utf-8"))
        meta.update(meta_overrides)
        (dst / "fixture.json").write_text(json.dumps(meta), encoding="utf-8")
    return dst


# --------------------------------------------------------------------------- #
# fixture format
# --------------------------------------------------------------------------- #
def test_smoke_fixture_loads_and_is_internally_consistent():
    fx = fixture_mod.load_fixture(SMOKE)
    assert (fx.case, fx.cell_type, fx.document_language) == ("_smoke", "same", "de")
    assert fx.cell_id == "_smoke.same_de"
    assert fx.provenance["how"] and "date" in fx.provenance
    assert len(fx.vault["work_experience"]) == 3
    assert fx.ledger and all("concept" in r for r in fx.ledger)
    assert fx.ledger is fx.gap["keyword_ledger"]  # gap.json is the single ledger source
    assert "example.com" in fx.vault["personal_info"]["email"]
    # the posting is the text, not a stub
    assert "ISO 13485" in fx.posting


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda m: m.pop("case"), "case"),
        (lambda m: m.update(cell_type="sideways"), "cell_type"),
        (lambda m: m.update(document_language="fr"), "document_language"),
        (lambda m: m.update(provenance={"how": "x"}), "provenance"),
        (lambda m: m.update(letter_inputs={"bogus": 1}), "letter_inputs"),
    ],
)
def test_fixture_loader_rejects_a_malformed_fixture_json(tmp_path, mutate, message):
    d = _copy_fixture(tmp_path)
    meta = json.loads((d / "fixture.json").read_text(encoding="utf-8"))
    mutate(meta)
    (d / "fixture.json").write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(fixture_mod.FixtureError, match=message):
        fixture_mod.load_fixture(d)


def test_fixture_loader_reports_missing_files(tmp_path):
    d = _copy_fixture(tmp_path)
    (d / "vault.json").unlink()
    with pytest.raises(fixture_mod.FixtureError, match="vault.json"):
        fixture_mod.load_fixture(d)
    d2 = _copy_fixture(tmp_path / "again")
    (d2 / "posting.md").unlink()
    with pytest.raises(fixture_mod.FixtureError, match="posting"):
        fixture_mod.load_fixture(d2)


def test_ledger_json_is_used_only_when_gap_json_has_no_ledger(tmp_path):
    d = _copy_fixture(tmp_path)
    gap = json.loads((d / "gap.json").read_text(encoding="utf-8"))
    gap.pop("keyword_ledger")
    (d / "gap.json").write_text(json.dumps(gap), encoding="utf-8")
    (d / "ledger.json").write_text(json.dumps([{"concept": "X", "claimable": True}]), encoding="utf-8")
    assert [r["concept"] for r in fixture_mod.load_fixture(d).ledger] == ["X"]


# --------------------------------------------------------------------------- #
# environment / provider selection
# --------------------------------------------------------------------------- #
def test_model_slug_is_one_safe_path_component():
    assert run_mod.model_slug("openai/gpt-5.6-luna") == "openai-gpt-5.6-luna"
    assert run_mod.model_slug("mistralai/ministral-8b-2512") == "mistralai-ministral-8b-2512"
    assert "/" not in run_mod.model_slug("a/b/../c")
    assert run_mod.model_slug("///") == "unknown-model"


def test_mock_provider_scrubs_provider_keys_and_needs_no_key(monkeypatch, restore_process_state):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-should-be-removed")
    monkeypatch.setenv("MISTRAL_API_KEY", "also-removed")
    assert run_mod.configure_environment("mock", None) == "mock"
    assert os.environ["LLM_PROVIDER"] == "mock"
    assert "OPENROUTER_API_KEY" not in os.environ and "MISTRAL_API_KEY" not in os.environ
    from applire.config import settings

    assert settings.llm_provider == "mock" and settings.llm_debug_log is True


def test_openrouter_fails_loudly_without_a_key(monkeypatch, restore_process_state):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(run_mod.ConfigError, match="OPENROUTER_API_KEY"):
        run_mod.configure_environment("openrouter", "openai/gpt-5.6-luna")


def test_openrouter_fails_loudly_without_a_model(monkeypatch, restore_process_state):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(run_mod.ConfigError, match="--model or OPENROUTER_MODEL"):
        run_mod.configure_environment("openrouter", None)


def test_openrouter_refuses_a_conflicting_llm_provider(monkeypatch, restore_process_state):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "mistral")
    with pytest.raises(run_mod.ConfigError, match="conflicts"):
        run_mod.configure_environment("openrouter", "a/b")


def test_openrouter_sets_model_and_reasoning_off_by_default(monkeypatch, restore_process_state):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_DISABLE_THINKING", raising=False)
    assert run_mod.configure_environment("openrouter", "openai/gpt-5.6-luna") == "openai/gpt-5.6-luna"
    assert os.environ["OPENROUTER_MODEL"] == "openai/gpt-5.6-luna"
    assert os.environ["OPENROUTER_DISABLE_THINKING"] == "true"
    from applire.config import settings

    assert settings.openrouter_model == "openai/gpt-5.6-luna"
    assert settings.openrouter_disable_thinking is True
    # an explicit env value wins over the default
    monkeypatch.setenv("OPENROUTER_DISABLE_THINKING", "false")
    run_mod.configure_environment("openrouter", "openai/gpt-5.6-luna")
    assert settings.openrouter_disable_thinking is False


def test_requesty_fails_loudly_without_its_own_key(monkeypatch, restore_process_state):
    # B2-6: an OpenRouter key must not satisfy a Requesty run (and vice versa).
    monkeypatch.delenv("REQUESTY_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(run_mod.ConfigError, match="REQUESTY_API_KEY"):
        run_mod.configure_environment("requesty", "openai/gpt-5.6-luna")


def test_requesty_fails_loudly_without_a_model(monkeypatch, restore_process_state):
    monkeypatch.setenv("REQUESTY_API_KEY", "k")
    monkeypatch.delenv("REQUESTY_MODEL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(run_mod.ConfigError, match="--model or REQUESTY_MODEL"):
        run_mod.configure_environment("requesty", None)


def test_requesty_accepts_the_stack_env_shape(monkeypatch, restore_process_state):
    """The COMMON-BRIEF §7 export carries LLM_PROVIDER=requesty + REQUESTY_MODEL: no conflict,
    the model comes from the env, reasoning stays as exported, settings are mirrored."""
    monkeypatch.setenv("REQUESTY_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "requesty")
    monkeypatch.setenv("REQUESTY_MODEL", "openai/gpt-5.6-luna")
    monkeypatch.setenv("REQUESTY_DISABLE_THINKING", "true")
    assert run_mod.configure_environment("requesty", None) == "openai/gpt-5.6-luna"
    from applire.config import settings

    assert os.environ["LLM_PROVIDER"] == "requesty"
    assert settings.llm_provider == "requesty"
    assert settings.requesty_model == "openai/gpt-5.6-luna"
    assert settings.requesty_disable_thinking is True
    # an explicit --model wins over the env model; an explicit thinking value wins over the default
    monkeypatch.setenv("REQUESTY_DISABLE_THINKING", "false")
    assert run_mod.configure_environment("requesty", "a/b") == "a/b"
    assert os.environ["REQUESTY_MODEL"] == "a/b" and settings.requesty_model == "a/b"
    assert settings.requesty_disable_thinking is False


def test_requesty_refuses_a_conflicting_llm_provider(monkeypatch, restore_process_state):
    monkeypatch.setenv("REQUESTY_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    with pytest.raises(run_mod.ConfigError, match="conflicts"):
        run_mod.configure_environment("requesty", "a/b")


def test_cli_offers_requesty_and_threads_the_model_to_the_child():
    args = run_mod._parse_args(["--fixture", "x", "--arm", "a", "--out", "o", "--provider", "requesty"])
    assert args.provider == "requesty"
    assert "requesty" in run_mod.PROVIDERS and "requesty_model" in run_mod._SETTINGS_SNAPSHOT
    assert "requesty_disable_thinking" in run_mod._SETTINGS_SNAPSHOT


def test_plan_refuses_to_mix_into_an_existing_run_dir(tmp_path):
    args = run_mod._parse_args(
        ["--fixture", str(SMOKE), "--arm", "a", "--out", str(tmp_path), "--runs", "2"]
    )
    specs, skipped = run_mod._plan(args, "mock")
    assert [s.run_idx for s in specs] == [0, 1] and not skipped
    specs[0].run_dir.mkdir(parents=True)
    with pytest.raises(run_mod.ConfigError, match="already exists"):
        run_mod._plan(args, "mock")
    (specs[0].run_dir / "meta.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    args.resume = True
    specs2, skipped2 = run_mod._plan(args, "mock")
    assert [s.run_idx for s in specs2] == [1] and skipped2 == [specs[0].run_dir]


# --------------------------------------------------------------------------- #
# seed
# --------------------------------------------------------------------------- #
def test_seed_builds_the_rows_the_pipelines_read(tmp_path, restore_process_state):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire import ownership
    from applire.db.session import Base
    from applire.models.application import Application
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.models.job import JobAnalysis
    from applire.models.profile import MasterProfile
    from applire.services.application import get_application_for_job
    from applire.services.owner_resolution import job_for_user
    from applire.utils.language_detection import resolve_document_language
    from quality_bench.seed import seed_fixture

    # job.json says the posting is German; the fixture pins English -> the pin must win.
    d = _copy_fixture(tmp_path, document_language="en")
    fx = fixture_mod.load_fixture(d)

    async def go():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 's.sqlite3'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        ids = await seed_fixture(factory, fx)
        out = {}
        with ownership.owner_context(ids.user_id):
            async with factory() as db:
                job = await job_for_user(db, ids.job_id, ids.user_id)  # reachable via the application link
                app = await get_application_for_job(ids.job_id, ids.user_id, db)
                out["lang"] = resolve_document_language(app, job)
                out["jd_lang"] = job.jd_language
                out["source_url"] = job.source_url
                out["raw_text"] = job.raw_text
                out["profile"] = (await db.execute(select(MasterProfile))).scalar_one().profile_json
                gap = (await db.execute(select(GapAnalysis))).scalar_one()
                out["ledger"] = gap.keyword_ledger
                out["keyword_gaps"] = gap.keyword_gaps
                flow = (await db.execute(select(FlowSession))).scalar_one()
                out["flow"] = (flow.job_id, flow.user_id, flow.generated_cv_id)
                out["app"] = (app.company_name, app.role_title)
        await engine.dispose()
        return ids, out

    ids, out = asyncio.run(go())
    assert out["lang"] == "en" and out["jd_lang"] == "de"
    assert out["source_url"] is None  # never seeded: would trigger network colour detection
    assert out["raw_text"] == fx.posting
    assert out["profile"] == fx.vault
    assert out["ledger"] == fx.ledger and out["keyword_gaps"] == fx.gap["keyword_gaps"]
    assert out["flow"] == (ids.job_id, ids.user_id, None)
    assert out["app"] == ("Brenner Medizintechnik GmbH", "Leiter Qualitätsmanagement")


# --------------------------------------------------------------------------- #
# run-output contract (service doors stubbed)
# --------------------------------------------------------------------------- #
CV_DATA = {"contact": {"name": "Mara Hellwig"}, "summary": "Zusammenfassung.", "work_history": []}
LETTER_DATA = {"header": {"name": "Mara Hellwig"}, "body": {"paragraphs": ["Sehr geehrte Damen und Herren,"]}}
TERMINAL = {"id": "terminal-review", "status": "pass", "details": "approved"}
LOG_LINE = {"ts": "2026-10-12T00:00:00+00:00", "stage": "cv_tailoring", "review_role": None,
            "review_attempt": None, "model": "mock", "system": "s", "prompt": "p", "response": {"a": 1}}


@pytest.fixture
def stub_doors(monkeypatch, restore_process_state):
    """Replace the two service doors (and the provider factory) with row-writing stubs.

    ``plan`` selects the behaviour per stage: "ok" | "failed" | "raise" | "hang".
    """
    from applire.config import settings
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.services import cover_letter as letter_service
    from applire.services import cv as cv_service

    plan = {"cv": "ok", "letter": "ok"}

    def write_llm_log():
        log_dir = Path(settings.llm_debug_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "2026-10-12.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(LOG_LINE) + "\n")

    async def fake_generate_cv(job_id, db, provider, background_tasks=None, template="classic_german",
                               base_url="", target_pages=None, *, user_id=None):
        write_llm_log()
        if plan["cv"] == "raise":
            raise LookupError("posting vanished")
        if plan["cv"] == "hang":
            await asyncio.sleep(30)
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        profile = (await db.execute(select(MasterProfile))).scalar_one()
        ok = plan["cv"] == "ok"
        row = GeneratedCV(
            job_analysis_id=job_id, profile_id=profile.id, user_id=user_id,
            tailored_data=CV_DATA if ok else {}, template=template,
            status="ready" if ok else "failed", target_pages=2, document_language="de",
            error_message=None if ok else "boom in the writer",
            ats_report={"checks": [TERMINAL]} if ok else None,
            truthfulness_report={"verdict": "x"} if ok else None,
        )
        db.add(row)
        await db.commit()
        return type("R", (), {"cv_id": row.id})()

    async def fake_generate_cover_letter(request, db, provider, background_tasks=None, base_url="",
                                         *, user_id=None):
        write_llm_log()
        if plan["letter"] == "raise":
            raise RuntimeError("letter door exploded")
        from sqlalchemy import select
        from applire.models.profile import MasterProfile

        profile = (await db.execute(select(MasterProfile))).scalar_one()
        ok = plan["letter"] == "ok"
        row = GeneratedCoverLetter(
            job_analysis_id=request.job_id, profile_id=profile.id, user_id=user_id,
            letter_data=LETTER_DATA if ok else {}, pre_gen_inputs={"tone": request.tone},
            status="ready" if ok else "failed", document_language="de",
            error_message=None if ok else "letter writer failed",
            ats_report={"checks": [TERMINAL]} if ok else None,
        )
        db.add(row)
        await db.commit()
        return type("R", (), {"cover_letter_id": row.id})()

    async def fake_measure_pages(cv_id, cl_id, user_id, state):
        return {"cv": 2, "letter": 1}

    monkeypatch.setattr(cv_service, "generate_cv", fake_generate_cv)
    monkeypatch.setattr(letter_service, "generate_cover_letter", fake_generate_cover_letter)
    monkeypatch.setattr(run_mod, "measure_pages", fake_measure_pages)
    monkeypatch.setattr("applire.providers.llm.get_provider", lambda: object())
    run_mod.configure_environment("mock", None)
    return plan


def test_ok_run_writes_the_full_contract(tmp_path, stub_doors):
    meta = _run(_spec(tmp_path, pages=True))
    rd = tmp_path / "armX" / "mock" / "_smoke.same_de" / "3"
    assert meta["status"] == "ok"
    assert {"meta.json", "cv.json", "letter.json", "reports.json", "pages.json", "llm.jsonl"} <= {
        p.name for p in rd.iterdir()
    }
    on_disk = json.loads((rd / "meta.json").read_text(encoding="utf-8"))
    assert META_KEYS <= set(on_disk)
    assert (on_disk["arm"], on_disk["model"], on_disk["model_slug"], on_disk["cell_id"], on_disk["case"],
            on_disk["cell_type"], on_disk["document_language"], on_disk["run_idx"]) == (
        "armX", "mock", "mock", "_smoke.same_de", "_smoke", "same", "de", 3)
    assert on_disk["status"] == "ok" and on_disk["failed_stage"] is None and on_disk["error"] is None
    assert on_disk["fixture_dir"] == str(SMOKE.resolve())
    assert on_disk["git_sha"] is None or len(on_disk["git_sha"]) == 40
    assert on_disk["started"] <= on_disk["finished"]
    assert on_disk["settings"]["llm_provider"] == "mock"

    assert json.loads((rd / "cv.json").read_text(encoding="utf-8")) == CV_DATA
    assert json.loads((rd / "letter.json").read_text(encoding="utf-8")) == LETTER_DATA
    assert json.loads((rd / "pages.json").read_text(encoding="utf-8")) == {"cv": 2, "letter": 1}
    reports = json.loads((rd / "reports.json").read_text(encoding="utf-8"))
    assert set(reports) == {"cv", "letter"}
    for doc in ("cv", "letter"):
        assert {"ats_report", "truthfulness_report", "critic_report", "terminal_review"} <= set(reports[doc])
        assert reports[doc]["terminal_review"] == TERMINAL
    assert reports["cv"]["truthfulness_report"] == {"verdict": "x"}
    ledger = json.loads((rd / "ledger_effective.json").read_text(encoding="utf-8"))
    assert ledger and ledger[0]["concept"]


def test_llm_log_is_isolated_into_the_run_dir_verbatim(tmp_path, stub_doors):
    _run(_spec(tmp_path))
    rd = tmp_path / "armX" / "mock" / "_smoke.same_de" / "3"
    lines = [json.loads(ln) for ln in (rd / "llm.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines == [LOG_LINE, LOG_LINE]  # one write per stub door, records untouched
    assert not (rd / "_llmlog").exists() and not (rd / "_work").exists()
    assert json.loads((rd / "meta.json").read_text(encoding="utf-8"))["llm_records"] == 2


def test_no_pages_flag_yields_null_page_counts(tmp_path, stub_doors):
    _run(_spec(tmp_path, pages=False))
    rd = tmp_path / "armX" / "mock" / "_smoke.same_de" / "3"
    assert json.loads((rd / "pages.json").read_text(encoding="utf-8")) == {"cv": None, "letter": None}


def test_failed_cv_is_recorded_not_skipped(tmp_path, stub_doors):
    stub_doors["cv"] = "failed"
    meta = _run(_spec(tmp_path))
    rd = tmp_path / "armX" / "mock" / "_smoke.same_de" / "3"
    on_disk = json.loads((rd / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == on_disk["status"] == "failed"
    assert on_disk["failed_stage"] == "cv" and "boom in the writer" in on_disk["error"]
    assert not (rd / "cv.json").exists() and not (rd / "letter.json").exists()
    assert json.loads((rd / "reports.json").read_text(encoding="utf-8"))["cv"]["status"] == "failed"
    assert (rd / "llm.jsonl").exists()  # what the model said before failing is still evidence


def test_failed_letter_keeps_the_cv_and_names_the_stage(tmp_path, stub_doors):
    stub_doors["letter"] = "failed"
    _run(_spec(tmp_path))
    rd = tmp_path / "armX" / "mock" / "_smoke.same_de" / "3"
    on_disk = json.loads((rd / "meta.json").read_text(encoding="utf-8"))
    assert (on_disk["status"], on_disk["failed_stage"]) == ("failed", "letter")
    assert "letter writer failed" in on_disk["error"]
    assert (rd / "cv.json").exists() and not (rd / "letter.json").exists()


@pytest.mark.parametrize("stage,exc", [("cv", "LookupError"), ("letter", "RuntimeError")])
def test_a_raising_door_is_recorded_with_its_traceback(tmp_path, stub_doors, stage, exc):
    stub_doors[stage] = "raise"
    meta = _run(_spec(tmp_path))
    rd = tmp_path / "armX" / "mock" / "_smoke.same_de" / "3"
    assert meta["status"] == "failed" and meta["failed_stage"] == stage
    assert meta["error"].startswith(exc)
    assert exc in (rd / "error.txt").read_text(encoding="utf-8")


def test_a_hung_stage_times_out_and_is_recorded(tmp_path, stub_doors):
    stub_doors["cv"] = "hang"
    # The budget is the whole run's (seed included), so it must outlast a real seed on a
    # loaded box: at 0.5 s the seed itself timed out under a parallel gate (failed_stage
    # "seed", B2 D1). 5 s is ~10x the loaded seed; the hang stub sleeps 30 s.
    meta = _run(_spec(tmp_path, timeout=5.0))
    assert meta["status"] == "failed" and meta["failed_stage"] == "cv"
    assert "timeout" in meta["error"]
    rd = tmp_path / "armX" / "mock" / "_smoke.same_de" / "3"
    assert json.loads((rd / "meta.json").read_text(encoding="utf-8"))["status"] == "failed"
    assert not (rd / "_work").exists()


def test_meta_is_written_pessimistically_before_the_run(tmp_path, stub_doors, monkeypatch):
    """A hard kill (SIGKILL / OOM) must leave a failed record, not an empty dir."""
    seen = {}
    real_pipeline = run_mod._pipeline

    async def spy(spec, run_dir, meta, state):
        seen["meta"] = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
        return await real_pipeline(spec, run_dir, meta, state)

    monkeypatch.setattr(run_mod, "_pipeline", spy)
    _run(_spec(tmp_path))
    assert seen["meta"]["status"] == "failed" and seen["meta"]["failed_stage"] == "seed"
    assert "did not complete" in seen["meta"]["error"]


def test_finalize_llm_log_is_idempotent_and_orders_by_file(tmp_path):
    (tmp_path / "_llmlog").mkdir()
    (tmp_path / "_llmlog" / "2026-10-11.jsonl").write_text('{"n":1}\n{"n":2}\n', encoding="utf-8")
    (tmp_path / "_llmlog" / "2026-10-12.jsonl").write_text('{"n":3}\n', encoding="utf-8")
    assert run_mod.finalize_llm_log(tmp_path) == 3
    assert run_mod.finalize_llm_log(tmp_path) == 3  # second call: dir gone, file kept
    assert (tmp_path / "llm.jsonl").read_text(encoding="utf-8") == '{"n":1}\n{"n":2}\n{"n":3}\n'


# --------------------------------------------------------------------------- #
# the REAL pipelines on the mock provider (no browser, no network)
# --------------------------------------------------------------------------- #
def test_real_pipeline_on_mock_provider(tmp_path, monkeypatch, restore_process_state):
    """Seed -> real ``generate_cv`` -> real ``generate_cover_letter`` -> run dir.

    The PDF render (Chromium) is patched to raise: the render/measure steps are fail-open
    (cv.py:4526-4532 ``_measure_and_condense`` caller; cover_letter.py ``_persist_and_measure``),
    so the documents are still delivered, unmeasured, and the harness records ``null`` pages.
    """

    async def no_browser(*_a, **_k):
        raise RuntimeError("no browser in the hermetic unit tier")

    monkeypatch.setattr("applire.services.cv._html_to_pdf", no_browser)
    monkeypatch.setattr("applire.services.cover_letter_pdf.render_pdf", no_browser)
    run_mod.configure_environment("mock", None)

    meta = _run(_spec(tmp_path, pages=True))
    rd = tmp_path / "armX" / "mock" / "_smoke.same_de" / "3"
    assert meta["status"] == "ok", (meta["failed_stage"], meta["error"])

    cv = json.loads((rd / "cv.json").read_text(encoding="utf-8"))
    letter = json.loads((rd / "letter.json").read_text(encoding="utf-8"))
    assert cv.get("work_history") and letter.get("body")
    # the vault join composed the fixture's own role into the delivered CV
    assert any(w["company"] == "Nordwerk Medizintechnik GmbH" for w in cv["work_history"])

    reports = json.loads((rd / "reports.json").read_text(encoding="utf-8"))
    assert reports["cv"]["document_language"] == "de" == reports["letter"]["document_language"]
    # Without a browser the pipelines are fail-open for DELIVERY but not for the audit: the ATS
    # report (which carries the terminal-review check) is left NULL (cv.py:5803,
    # cover_letter.py:3547). A real bench run therefore needs Chromium to get ats_report /
    # terminal_review; the harness records the NULLs as they are.
    assert reports["cv"]["ats_report"] is None and reports["cv"]["terminal_review"] is None
    assert reports["cv"]["truthfulness_report"] is not None or reports["cv"]["critic_report"] is not None
    assert json.loads((rd / "pages.json").read_text(encoding="utf-8")) == {"cv": None, "letter": None}
    assert "cv" in json.loads((rd / "meta.json").read_text(encoding="utf-8")).get("pages_error", {})

    records = [json.loads(ln) for ln in (rd / "llm.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert records, "the mock run must leave an LLM debug log"
    assert {"ts", "stage", "review_role", "review_attempt", "model", "system", "prompt", "response"} <= set(records[0])
    assert {"cv_tailoring", "cover_letter"} <= {r["stage"] for r in records}
    assert not (rd / "_work").exists() and not (rd / "_llmlog").exists()
    assert uuid.UUID(meta["ids"]["cv_id"]) and uuid.UUID(meta["ids"]["cover_letter_id"])
    # usage is captured per call in memory (the product's SQLite sink can lock and
    # disable itself mid-run); one row per provider call the debug log recorded
    assert meta["usage"]["source"] == "captured" and meta["usage"]["calls"] == len(records)
    from applire.providers.llm import usage as usage_mod

    assert usage_mod._sink is usage_mod._persist  # the product sink is put back


def test_settings_snapshot_records_every_pipeline_switch(monkeypatch):
    """US368: a v1-vs-v2 arm is unreadable without the switches it ran under."""
    monkeypatch.setenv("CV_PIPELINE", "v2")
    monkeypatch.setenv("LETTER_PIPELINE", "v1")
    snap = run_mod.settings_snapshot()
    assert snap["CV_PIPELINE"] == "v2" and snap["LETTER_PIPELINE"] == "v1"
