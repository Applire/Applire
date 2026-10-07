# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#688 (Strawberry build 2, WP-M) — the harness's A/B arm and call-count options.

#688 asks for a before/after matrix row per prompt change. A row can only be one
half of such a pair when it names the prompt AND the response schema it measured
(``schema_out`` carries every op docstring as a description, so an ops.py
docstring edit changes what the model reads), and a budget measured in provider
calls (ruling B2-3) can only be kept when the run counts its secondary calls —
the stance guard's adjudication call rides on the same provider.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "model_matrix.py"


def _load_harness():
    backend = str(REPO_ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    spec = importlib.util.spec_from_file_location("model_matrix_ab_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mm = _load_harness()


def _run_mock(tmp_path: Path, monkeypatch, *extra: str) -> dict:
    # configure_env writes os.environ; monkeypatch puts every key back.
    for key in ("LLM_PROVIDER", "LLM_DEBUG_LOG", "LLM_DEBUG_LOG_DIR"):
        monkeypatch.setenv(key, "")
    out = tmp_path / "arm.jsonl"
    argv = ["--provider", "mock", "--n", "1", "--shapes", "S6", "--concurrency", "1",
            "--out", str(out), *extra]
    assert mm.main(argv) == 0
    return json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))


def test_dump_prompt_writes_the_trees_own_prompt_and_schema(tmp_path, monkeypatch):
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT

    monkeypatch.setenv("LLM_PROVIDER", "")
    assert mm.main(["--dump-prompt", str(tmp_path / "a")]) == 0
    assert (tmp_path / "a" / "system_prompt.txt").read_text(encoding="utf-8") == RECONCILE_SYSTEM_PROMPT
    schema = json.loads((tmp_path / "a" / "schema.json").read_text(encoding="utf-8"))
    assert schema == mm.current_schema_param()


def test_a_row_names_the_prompt_and_schema_it_measured(tmp_path, monkeypatch):
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT

    summary = _run_mock(tmp_path, monkeypatch)
    identity = summary["meta"]["prompt"]
    assert identity["system_prompt_sha256"] == mm._sha(RECONCILE_SYSTEM_PROMPT)
    assert identity["system_prompt_chars"] == len(RECONCILE_SYSTEM_PROMPT)
    assert identity["system_prompt_override"] is None


def test_an_override_reaches_the_engine_and_is_restored_after(tmp_path, monkeypatch):
    from applire.services.profile.reconcile import engine

    before_prompt = engine.RECONCILE_SYSTEM_PROMPT
    before_schema_fn = engine._structured_output_schema
    variant = tmp_path / "b.txt"
    variant.write_text(before_prompt + "\nVARIANT-B MARKER\n", encoding="utf-8")
    schema_file = tmp_path / "b.json"
    schema_file.write_text(json.dumps({"name": "variant", "strict": False, "schema": {}}),
                           encoding="utf-8")

    seen: list[str] = []
    real_reconcile = engine.reconcile

    async def spy(*args, **kwargs):
        seen.append(engine.RECONCILE_SYSTEM_PROMPT)
        return await real_reconcile(*args, **kwargs)

    monkeypatch.setattr(engine, "reconcile", spy)
    summary = _run_mock(tmp_path, monkeypatch, "--system-prompt", str(variant),
                        "--schema", str(schema_file))

    # The engine read the variant during the run …
    assert seen and seen[0].endswith("VARIANT-B MARKER\n")
    identity = summary["meta"]["prompt"]
    assert identity["system_prompt_sha256"] == mm._sha(variant.read_text(encoding="utf-8"))
    assert identity["system_prompt_override"] == str(variant)
    assert identity["schema_override"] == str(schema_file)
    # … and nothing leaks into the next arm in the same process.
    assert engine.RECONCILE_SYSTEM_PROMPT == before_prompt
    assert engine._structured_output_schema is before_schema_fn


def test_the_schema_override_honours_structured_output_off(tmp_path, monkeypatch):
    from applire.config import settings
    from applire.services.profile.reconcile import engine

    schema_file = tmp_path / "b.json"
    schema_file.write_text(json.dumps({"name": "variant"}), encoding="utf-8")
    monkeypatch.setattr(settings, "llm_structured_output", "off")
    identity, restore = mm.apply_prompt_overrides(None, str(schema_file))
    try:
        assert engine._structured_output_schema() is None
        assert identity["schema_sha256"] is None
    finally:
        restore()


def test_the_call_count_includes_every_logged_call(tmp_path, monkeypatch):
    from applire.config import settings

    before = (settings.llm_debug_log, settings.llm_debug_log_dir)
    log_dir = tmp_path / "llm"
    summary = _run_mock(tmp_path, monkeypatch, "--llm-log-dir", str(log_dir))
    logged = summary["meta"]["llm_log"]
    # One record per provider call, read back from the files the run wrote.
    written = sum(
        1 for path in log_dir.glob("*.jsonl") for line in path.read_text().splitlines() if line
    )
    assert logged["calls"] == written >= 1
    assert logged["by_stage"].get("reconcile", 0) >= 1
    # The process-wide switch is back as it was.
    assert (settings.llm_debug_log, settings.llm_debug_log_dir) == before


def test_a_reused_log_dir_is_refused(tmp_path, monkeypatch):
    log_dir = tmp_path / "llm"
    log_dir.mkdir()
    (log_dir / "2026-10-07.jsonl").write_text('{"stage": "reconcile"}\n', encoding="utf-8")
    for key in ("LLM_PROVIDER", "LLM_DEBUG_LOG", "LLM_DEBUG_LOG_DIR"):
        monkeypatch.setenv(key, "")
    import pytest

    with pytest.raises(SystemExit):
        mm.main(["--provider", "mock", "--n", "1", "--shapes", "S6",
                 "--llm-log-dir", str(log_dir)])


def test_llm_log_calls_counts_stages_and_errors(tmp_path):
    (tmp_path / "x.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [
            {"stage": "reconcile", "error": None},
            {"stage": "stance_adjudication", "error": None},
            {"stage": "reconcile", "error": "LLMTimeoutError: x"},
        ]) + "\n",
        encoding="utf-8",
    )
    assert mm.llm_log_calls(tmp_path) == {
        "calls": 3,
        "errors": 1,
        "by_stage": {"reconcile": 2, "stance_adjudication": 1},
    }


def test_table_prints_one_row_per_summary_with_per_shape_rates(tmp_path, capsys, monkeypatch):
    summary = {
        "meta": {"model": "vendor/model-x", "n": 10, "llm_log": {"calls": 37}},
        "per_shape": {
            "S6_incident_shape_all_present": {"zero_op_rate": 0.0, "malformed_op_rate": 0.2,
                                              "wrong_slot_rate": 0.0, "error_rate": 0.0},
            "S9_bullet_conflict_nova": {"zero_op_rate": 0.1, "malformed_op_rate": 0.0,
                                        "wrong_slot_rate": 0.0, "error_rate": 0.0},
        },
        "usage": {"calls": 30, "prompt_tokens": 1000, "completion_tokens": 200},
        "verdict": {"label": "sub-par"},
        "cost": {"usd_from_tokens": 0.0123},
    }
    path = tmp_path / "x.summary.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setenv("LLM_PROVIDER", "")
    assert mm.main(["--table", str(path)]) == 0
    out = capsys.readouterr().out
    assert "malformed S6/S9" in out
    row = [line for line in out.splitlines() if "model-x" in line][0]
    # per-shape, in shape order; the debug-log count wins over the usage-line count
    assert "| 0/10 % | 20/0 % |" in row  # lost turn, then malformed
    assert "| **sub-par** | 37 |" in row and "$0.0123" in row
