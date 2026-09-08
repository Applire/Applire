#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US311 / #688 — the model-qualification matrix harness for the reconcile seam.

Replays ONE interview turn (the ADR-046 single-call reconciler plus the
deterministic applier) against a configured provider/model, N times per input
shape, and reports the failure rates that decide whether a model is safe to run
Applire's vault write path on.

**Opt-in.** This script is never a CI job: it needs an explicit provider key in
the environment and is invoked by hand. The only thing CI runs is the mock-provider
smoke test (``tests/unit/test_model_matrix_harness.py``) that keeps it from rotting.

Provenance: generalised from the 2026-09-06/07 summary-seed spike
(``Documents/Runs/Nougat/summary-seed-spike-2026-09-06/replay_multistation.py``),
whose eight input shapes and synthetic "Lena Fischer" vault are committed as
fixtures under ``tests/files/model_matrix/``. The spike measured
``z-ai/glm-5.3-flash`` at 11/20 zero-op turns and 12/20 malformed ops against
1/35 and 0/35 for ``openai/gpt-5.6-luna`` — those numbers are what this harness
turns into a repeatable measurement.

No database: the engine and ``apply_ops`` both run in memory.

Usage::

    # what the model would be asked to read — no provider call at all
    PYTHONPATH=backend python3 scripts/model_matrix.py --dry-run

    # one model, three shapes, n=10 (spends real provider credit)
    env $(grep -E '^(OPENROUTER_API_KEY|LLM_TIMEOUT)=' .env | xargs) \
      PYTHONPATH=backend python3 scripts/model_matrix.py \
      --provider openrouter --model deepseek/deepseek-v4-flash-0731 \
      --n 10 --shapes S6,S7,S8 --out runs/deepseek-v4-flash.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "files" / "model_matrix"

# `--model` is threaded to the provider through its own environment variable so
# that `get_provider()` (ADR-009) stays the single construction path — the harness
# never instantiates a provider SDK itself.
MODEL_ENV = {
    "mistral": "MISTRAL_MODEL",
    "openrouter": "OPENROUTER_MODEL",
    "requesty": "REQUESTY_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
    "openai": "OPENAI_MODEL",
    "ollama": "OLLAMA_MODEL",
    "mock": "",
}

# Proposed qualification thresholds (docs/llm-models.md "Which models work").
# A rate is measured PER SHAPE; the worst shape decides the model's verdict.
#
#   zero_op       — the turn produced no ops at all: the candidate's answer is
#                   silently gone. This is vault data loss, so the bar is the
#                   tightest one; the bench model's observed floor is 2.9 %.
#   malformed_op  — the model emitted an op the ADR-063 schema rejects. The
#                   engine drops it defensively, so this too is silent loss.
#   wrong_slot    — an op whose target entity is NOT the employer its own text
#                   names: a fact landed on the wrong station (document harm).
#   error         — the reconcile call raised (transport, parse, truncation).
THRESHOLDS = {"zero_op": 0.05, "malformed_op": 0.10, "wrong_slot": 0.10, "error": 0.10}
# A model is `qualified` when no threshold is crossed on any shape and it never
# lost a turn; `caveat` when it stayed under every threshold but did lose one;
# `sub-par` as soon as any threshold is crossed on any shape.

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
# Both provider log lines that carry token counts (openrouter.py, requesty.py).
_USAGE_RE = re.compile(
    r"LLM response \[(?P<method>\w+)\] model=(?P<model>\S+) latency=(?P<latency>[\d.]+)s "
    r"prompt_tokens=(?P<prompt>\S+) completion_tokens=(?P<completion>\S+)"
)

# Per-task sink so a concurrent run still attributes its own token usage.
# ContextVars propagate into the asyncio task that copies the context, and the
# provider's `logger.info` runs inside that same task.
_usage_sink: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "model_matrix_usage_sink", default=None
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
class Fixtures:
    """The committed shapes file plus the profile dumps it references."""

    def __init__(self, root: Path) -> None:
        self.root = root
        with (root / "shapes.json").open(encoding="utf-8") as fh:
            doc = json.load(fh)
        self.version: int = doc["version"]
        self.stations: dict[str, dict[str, Any]] = doc["stations"]
        self.shapes: dict[str, dict[str, Any]] = doc["shapes"]
        self._profiles: dict[str, dict[str, Any]] = {}
        for name in {s["profile"] for s in self.shapes.values()}:
            with (root / "profiles" / f"{name}.json").open(encoding="utf-8") as fh:
                self._profiles[name] = json.load(fh)

    def names(self) -> list[str]:
        return list(self.shapes)

    def profile_data(self, shape: str) -> dict[str, Any]:
        return self._profiles[self.shapes[shape]["profile"]]

    def new_info(self, shape: str) -> dict[str, str]:
        """The interview turn as the interview bridge hands it to the reconciler.

        Key order is the spike's (``gap``, ``question``, ``answer``) — it is what
        the model literally reads, so it is part of the fixture, not cosmetics.
        """
        cfg = self.shapes[shape]
        return {"gap": cfg["gap"], "question": cfg["question"], "answer": cfg["answer"]}

    def lang(self, shape: str) -> str:
        return self.shapes[shape]["lang"]

    def expected_stations(self, shape: str) -> list[str]:
        return list(self.shapes[shape]["expected_stations"])

    def station_of_id(self, entity_id: str | None) -> str | None:
        if not entity_id:
            return None
        for key, meta in self.stations.items():
            if meta["id"] == entity_id:
                return key
        return None

    def stations_in_text(self, blob: Any) -> set[str]:
        text = json.dumps(blob, ensure_ascii=False, default=str).lower()
        return {
            key
            for key, meta in self.stations.items()
            if any(word.lower() in text for word in meta["keywords"])
        }


def load_profile(data: dict[str, Any]) -> Any:
    """Validate a fixture dump back into a ``MasterProfileData``."""
    from applire.schemas.profile import MasterProfileData

    return MasterProfileData.model_validate(data)


def render_prompts(fixtures: Fixtures, shape: str) -> tuple[str, str]:
    """Return (system prompt, user prompt) exactly as the engine would send them."""
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT, build_reconcile_prompt

    profile = load_profile(fixtures.profile_data(shape))
    user = build_reconcile_prompt(profile, fixtures.new_info(shape), "interview")
    return RECONCILE_SYSTEM_PROMPT, user


def canonical_prompt(text: str) -> str:
    """Replace every UUID with a positional placeholder.

    The vault fixture pins the entity ids the spike minted randomly on every run
    (education entries and skills carry generated UUIDs). Canonicalising them is
    what makes "byte-identical to the spike" a checkable claim rather than a
    comparison against something that was never stable in the first place.
    """
    seen: dict[str, str] = {}

    def _sub(match: re.Match[str]) -> str:
        return seen.setdefault(match.group(0), f"<uuid-{len(seen)}>")

    return _UUID_RE.sub(_sub, text)


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #
class _UsageHandler(logging.Handler):
    """Read the provider's own usage fields off its INFO response line.

    ``providers/llm/`` is WP-O1's file territory this flavour, so the harness does
    not add a usage seam of its own: it reads the counts the OpenRouter/Requesty
    providers already log from ``response.usage``.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — instrumentation must never break a run
            return
        match = _USAGE_RE.search(message)
        if not match:
            return
        sink = _usage_sink.get()
        if sink is None:
            return
        sink.append(
            {
                "method": match.group("method"),
                "model": match.group("model"),
                "latency_s": _num(match.group("latency")),
                "prompt_tokens": _num(match.group("prompt")),
                "completion_tokens": _num(match.group("completion")),
            }
        )


def _num(raw: str) -> int | float | None:
    if raw in ("?", "None"):
        return None
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return None


def dump_op(op: Any) -> dict[str, Any]:
    try:
        return op.model_dump(mode="json")
    except Exception:  # noqa: BLE001 — a plain dict already is the dump
        return dict(op) if isinstance(op, dict) else {"op": str(op)}


def classify(
    fixtures: Fixtures,
    shape: str,
    ops: list[dict[str, Any]],
    rejected_ops: list[str],
    applied: Any,
) -> dict[str, Any]:
    """Turn one turn's ops into the per-run metric row."""
    expected = fixtures.expected_stations(shape)
    per_station: dict[str, list[str]] = {key: [] for key in fixtures.stations}
    unattributed: list[str] = []
    wrong_slot: list[dict[str, Any]] = []

    for op in ops:
        kind = op.get("op")
        target_station = fixtures.station_of_id(op.get("target"))
        # The op's own words, minus the handles — a target id must not vote on
        # which station the TEXT names, or nothing could ever be wrong-slot.
        body = {k: v for k, v in op.items() if k not in ("target", "ref", "parent", "evidence")}
        text_stations = fixtures.stations_in_text(body)
        if target_station:
            per_station[target_station].append(kind)
            if text_stations and target_station not in text_stations:
                wrong_slot.append(
                    {
                        "op": kind,
                        "target": op.get("target"),
                        "target_station": target_station,
                        "text_stations": sorted(text_stations),
                    }
                )
        elif text_stations:
            for key in text_stations:
                per_station[key].append(kind)
        else:
            unattributed.append(kind)

    skills = [
        {
            "name": op.get("name"),
            "category": op.get("category"),
            "proficiency": op.get("proficiency"),
            "status": op.get("status"),
            "evidence": len(op.get("evidence") or []),
            # #684 adds `years_experience` to UpsertSkill; read defensively so the
            # harness measures it the day the field lands and reports 0 before then.
            "years_experience": op.get("years_experience"),
        }
        for op in ops
        if op.get("op") == "upsert_skill"
    ]

    covered = [key for key in expected if per_station.get(key)]
    metrics: dict[str, Any] = {
        "n_ops": len(ops),
        "kinds": sorted({str(op.get("op")) for op in ops}),
        "zero_op": len(ops) == 0,
        "malformed_ops": len(rejected_ops),
        "rejected_kinds": sorted(set(rejected_ops)),
        "per_station": per_station,
        "unattributed": unattributed,
        "stations_expected": expected,
        "stations_covered": covered,
        "station_coverage": round(len(covered) / len(expected), 3) if expected else None,
        "wrong_slot": wrong_slot,
        "set_summary": sum(1 for op in ops if op.get("op") == "set_summary"),
        "disputes": sum(1 for op in ops if op.get("op") == "flag_conflict"),
        "confirmations": sum(1 for op in ops if op.get("op") == "request_confirmation"),
        "skills": skills,
        "skill_evidence_total": sum(s["evidence"] for s in skills),
        "skills_with_years_experience": sum(
            1 for s in skills if s.get("years_experience") not in (None, "")
        ),
    }
    if applied is not None:
        result = applied.model_dump(mode="json")
        metrics["apply"] = {
            "changes": len(result.get("changes") or []),
            "conflicts": [
                (c.get("section"), c.get("field")) for c in (result.get("conflicts") or [])
            ],
            "pending_confirmations": len(result.get("pending_confirmations") or []),
        }
        # An applier-parked conflict is a dispute the model raised implicitly.
        metrics["disputes"] += len(result.get("conflicts") or [])
    return metrics


async def run_one(
    provider: Any,
    fixtures: Fixtures,
    shape: str,
    index: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """One reconcile turn, measured. Never raises."""
    from applire.services.profile.reconcile.apply import apply_ops
    from applire.services.profile.reconcile.engine import reconcile

    async with semaphore:
        usage: list[dict[str, Any]] = []
        _usage_sink.set(usage)
        started = time.time()
        record: dict[str, Any] = {"shape": shape, "run": index}
        try:
            profile = load_profile(fixtures.profile_data(shape))
            result = await reconcile(
                profile,
                fixtures.new_info(shape),
                "interview",
                provider,
                fixtures.lang(shape),
            )
            ops = [dump_op(op) for op in result.ops]
            record["ops"] = ops
            record["rejected_ops"] = list(result.rejected_ops or [])
            record["denials"] = list(result.denials or [])
            record["ambiguities"] = len(result.ambiguities or [])
            applied = None
            try:
                applied = apply_ops(profile, result.ops, "interview")
            except Exception as exc:  # noqa: BLE001 — an applier crash IS a finding
                record["apply_error"] = f"{type(exc).__name__}: {exc}"
            record["metrics"] = classify(
                fixtures, shape, ops, record["rejected_ops"], applied
            )
        except Exception as exc:  # noqa: BLE001 — a failed turn is data, not a crash
            record["error"] = f"{type(exc).__name__}: {exc}"
        record["elapsed_s"] = round(time.time() - started, 2)
        record["usage"] = {
            "calls": len(usage),
            "prompt_tokens": sum(u["prompt_tokens"] or 0 for u in usage),
            "completion_tokens": sum(u["completion_tokens"] or 0 for u in usage),
            "detail": usage,
        }
        return record


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
def summarise(records: list[dict[str, Any]], shapes: list[str]) -> dict[str, Any]:
    per_shape: dict[str, Any] = {}
    for shape in shapes:
        rows = [r for r in records if r["shape"] == shape]
        if not rows:
            continue
        total = len(rows)
        errors = [r for r in rows if r.get("error")]
        ok = [r for r in rows if not r.get("error")]

        def rate(predicate: Any) -> float:
            return round(sum(1 for r in ok if predicate(r["metrics"])) / total, 3) if total else 0.0

        coverages = [
            r["metrics"]["station_coverage"]
            for r in ok
            if r["metrics"].get("station_coverage") is not None
        ]
        latencies = sorted(r["elapsed_s"] for r in rows)
        per_shape[shape] = {
            "n": total,
            "error_rate": round(len(errors) / total, 3),
            "zero_op_rate": rate(lambda m: m["zero_op"]),
            "malformed_op_rate": rate(lambda m: m["malformed_ops"] > 0),
            "wrong_slot_rate": rate(lambda m: bool(m["wrong_slot"])),
            "dispute_rate": rate(lambda m: m["disputes"] > 0),
            "set_summary_rate": rate(lambda m: m["set_summary"] > 0),
            "apply_error_rate": round(
                sum(1 for r in ok if r.get("apply_error")) / total, 3
            ),
            "station_coverage_mean": round(sum(coverages) / len(coverages), 3)
            if coverages
            else None,
            "skill_evidence_mean": round(
                sum(r["metrics"]["skill_evidence_total"] for r in ok) / len(ok), 2
            )
            if ok
            else None,
            "years_experience_rate": rate(lambda m: m["skills_with_years_experience"] > 0),
            "latency_p50_s": latencies[len(latencies) // 2] if latencies else None,
        }

    usage = {
        "calls": sum(r.get("usage", {}).get("calls", 0) for r in records),
        "prompt_tokens": sum(r.get("usage", {}).get("prompt_tokens", 0) for r in records),
        "completion_tokens": sum(
            r.get("usage", {}).get("completion_tokens", 0) for r in records
        ),
    }
    return {"per_shape": per_shape, "usage": usage, "verdict": verdict(per_shape)}


def verdict(per_shape: dict[str, Any]) -> dict[str, Any]:
    """`qualified` / `caveat` / `sub-par`, with the crossings that decided it."""
    crossings: list[str] = []
    lost_a_turn = False
    for shape, rates in per_shape.items():
        for metric, limit in THRESHOLDS.items():
            observed = rates.get(f"{metric}_rate", 0.0)
            if observed > limit:
                crossings.append(f"{shape}: {metric}_rate {observed:.0%} > {limit:.0%}")
            elif observed > 0:
                lost_a_turn = True
    if crossings:
        label = "sub-par"
    elif lost_a_turn:
        label = "caveat"
    else:
        label = "qualified"
    return {"label": label, "crossings": crossings, "thresholds": dict(THRESHOLDS)}


def print_summary(summary: dict[str, Any], header: str) -> None:
    cols = [
        ("zero_op_rate", "zero-op"),
        ("malformed_op_rate", "malformed"),
        ("wrong_slot_rate", "wrong-slot"),
        ("dispute_rate", "dispute"),
        ("set_summary_rate", "set_summary"),
        ("error_rate", "error"),
    ]
    print()
    print(header)
    print(
        f"{'shape':<34}{'n':>4}  "
        + "".join(f"{label:>12}" for _, label in cols)
        + f"{'coverage':>10}{'p50 s':>8}"
    )
    for shape, rates in summary["per_shape"].items():
        coverage = rates["station_coverage_mean"]
        print(
            f"{shape:<34}{rates['n']:>4}  "
            + "".join(f"{rates[key]:>11.0%} " for key, _ in cols)
            + (f"{coverage:>10.2f}" if coverage is not None else f"{'-':>10}")
            + f"{rates['latency_p50_s'] or 0:>8.1f}"
        )
    usage = summary["usage"]
    print(
        f"\nprovider calls: {usage['calls']}  "
        f"prompt tokens: {usage['prompt_tokens']}  "
        f"completion tokens: {usage['completion_tokens']}"
    )
    if summary.get("cost"):
        cost = summary["cost"]
        for key, value in cost.items():
            print(f"cost[{key}]: {value}")
    result = summary["verdict"]
    print(f"\nVERDICT: {result['label'].upper()}")
    for crossing in result["crossings"]:
        print(f"  crossed  {crossing}")
    if not result["crossings"]:
        print(
            "  no threshold crossed "
            f"(zero-op ≤ {THRESHOLDS['zero_op']:.0%}, malformed ≤ {THRESHOLDS['malformed_op']:.0%}, "
            f"wrong-slot ≤ {THRESHOLDS['wrong_slot']:.0%}, error ≤ {THRESHOLDS['error']:.0%})"
        )


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #
def token_cost(usage: dict[str, Any], price_in: float | None, price_out: float | None) -> dict[str, Any] | None:
    if price_in is None and price_out is None:
        return None
    dollars = (usage["prompt_tokens"] / 1_000_000) * (price_in or 0.0) + (
        usage["completion_tokens"] / 1_000_000
    ) * (price_out or 0.0)
    return {"usd_from_tokens": round(dollars, 6), "price_in": price_in, "price_out": price_out}


def openrouter_credits() -> float | None:
    """Total credit spent so far, for a before/after delta. None when unavailable."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        import httpx

        response = httpx.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {key}"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json().get("data") or {}
        value = data.get("total_usage")
        return float(value) if value is not None else None
    except Exception:  # noqa: BLE001 — a cost probe must never fail a measurement
        return None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def configure_env(provider: str, model: str | None, timeout: int | None) -> None:
    """Point the ADR-009 factory at the requested provider BEFORE applire imports.

    ``applire.config.Settings`` is read once at import time, so the environment
    has to be right before anything under ``applire.`` is first imported.
    """
    os.environ["LLM_PROVIDER"] = provider
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    if model:
        env_var = MODEL_ENV.get(provider)
        if not env_var:
            raise SystemExit(f"--model is not applicable to provider '{provider}'")
        os.environ[env_var] = model
    if timeout:
        os.environ["LLM_TIMEOUT"] = str(timeout)
    backend = str(REPO_ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)


def install_usage_handler() -> None:
    """Attach the usage reader once — a second handler would double every count."""
    logger = logging.getLogger("applire.providers.llm")
    if not any(isinstance(h, _UsageHandler) for h in logger.handlers):
        logger.addHandler(_UsageHandler())
    logger.setLevel(logging.INFO)
    logger.propagate = False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="model_matrix",
        description="Measure one LLM on Applire's reconcile seam (US311 / #688).",
    )
    parser.add_argument("--provider", default="mock", help="LLM_PROVIDER value (default: mock)")
    parser.add_argument("--model", default=None, help="model id for that provider")
    parser.add_argument("--n", type=int, default=10, help="runs per shape (default: 10)")
    parser.add_argument(
        "--shapes",
        default="S6,S7,S8",
        help="comma-separated shape names or prefixes; 'all' for every shape",
    )
    parser.add_argument("--out", default=None, help="JSONL file for the per-run records")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=None, help="LLM_TIMEOUT seconds")
    parser.add_argument("--price-in", type=float, default=None, help="$ per 1M input tokens")
    parser.add_argument("--price-out", type=float, default=None, help="$ per 1M output tokens")
    parser.add_argument(
        "--no-credits-probe",
        action="store_true",
        help="skip the OpenRouter credit before/after read",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print prompt sizes per shape and exit — no provider call",
    )
    parser.add_argument(
        "--score",
        default=None,
        metavar="JSONL",
        help=(
            "re-score an existing record file instead of running a model — this "
            "harness's own --out, or the 2026-09-06 spike's runs*.jsonl"
        ),
    )
    parser.add_argument("--fixtures", default=str(FIXTURE_DIR))
    return parser.parse_args(argv)


def resolve_shapes(fixtures: Fixtures, spec: str) -> list[str]:
    if spec.strip().lower() == "all":
        return fixtures.names()
    chosen: list[str] = []
    for token in (part.strip() for part in spec.split(",") if part.strip()):
        matches = [name for name in fixtures.names() if name == token or name.startswith(token)]
        if not matches:
            raise SystemExit(f"unknown shape '{token}'; known: {', '.join(fixtures.names())}")
        for name in matches:
            if name not in chosen:
                chosen.append(name)
    return chosen


def do_dry_run(fixtures: Fixtures, shapes: list[str]) -> int:
    print(f"fixtures v{fixtures.version} — {fixtures.root}")
    print(f"{'shape':<34}{'profile':<20}{'system':>9}{'user':>9}{'total':>9}{'~tokens':>9}")
    for shape in shapes:
        system, user = render_prompts(fixtures, shape)
        total = len(system) + len(user)
        print(
            f"{shape:<34}{fixtures.shapes[shape]['profile']:<20}"
            f"{len(system):>9}{len(user):>9}{total:>9}{total // 4:>9}"
        )
    return 0


def score_file(fixtures: Fixtures, path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Re-score records that already exist — no provider, no credit.

    Accepts this harness's own ``--out`` file and, deliberately, the raw record
    format of the spike this harness generalises: those runs are the baseline the
    published table's first two rows rest on, and re-deriving them here is what
    proves the classifier agrees with the numbers already reported in #688.
    """
    records: list[dict[str, Any]] = []
    shapes: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            shape = raw.get("shape")
            if shape not in fixtures.shapes:
                raise SystemExit(f"{path}: unknown shape {shape!r}")
            if shape not in shapes:
                shapes.append(shape)
            record = {
                "shape": shape,
                "run": raw.get("run"),
                "elapsed_s": raw.get("elapsed_s") or 0.0,
                "usage": raw.get("usage")
                or {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0},
            }
            if raw.get("error"):
                record["error"] = raw["error"]
            else:
                record["metrics"] = raw.get("metrics") or classify(
                    fixtures,
                    shape,
                    raw.get("ops") or [],
                    raw.get("rejected_ops") or [],
                    None,
                )
                if raw.get("apply_error"):
                    record["apply_error"] = raw["apply_error"]
            records.append(record)
    return records, sorted(shapes)


async def run_matrix(args: argparse.Namespace, fixtures: Fixtures, shapes: list[str]) -> dict[str, Any]:
    from applire.providers.llm import get_provider

    provider = get_provider()
    print(f"provider: {type(provider).__name__} model={args.model or '<env default>'}", flush=True)
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    tasks = [
        run_one(provider, fixtures, shape, index, semaphore)
        for shape in shapes
        for index in range(1, args.n + 1)
    ]
    handle = open(args.out, "w", encoding="utf-8") if args.out else None
    records: list[dict[str, Any]] = []
    try:
        for coro in asyncio.as_completed(tasks):
            record = await coro
            records.append(record)
            if handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                handle.flush()
            metrics = record.get("metrics") or {}
            print(
                f"[{record['shape']} #{record['run']}] {record['elapsed_s']}s "
                f"ops={metrics.get('n_ops')} kinds={metrics.get('kinds')} "
                f"malformed={metrics.get('malformed_ops')} "
                f"covered={metrics.get('stations_covered')} "
                f"wrong_slot={len(metrics.get('wrong_slot') or [])} "
                f"set_summary={metrics.get('set_summary')} "
                f"err={record.get('error') or record.get('apply_error') or '-'}",
                flush=True,
            )
    finally:
        if handle:
            handle.close()
    return {"records": records}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_env(args.provider, args.model, args.timeout)
    fixtures = Fixtures(Path(args.fixtures))
    shapes = resolve_shapes(fixtures, args.shapes)

    if args.dry_run:
        return do_dry_run(fixtures, shapes)

    if args.score:
        records, scored_shapes = score_file(fixtures, Path(args.score))
        summary = summarise(records, scored_shapes)
        summary["meta"] = {"scored_from": args.score, "turns": len(records)}
        print_summary(summary, f"MODEL MATRIX (re-scored) — {args.score}")
        return 0

    install_usage_handler()
    credits_before = (
        None
        if args.no_credits_probe or args.provider != "openrouter"
        else openrouter_credits()
    )
    started = time.time()
    outcome = asyncio.run(run_matrix(args, fixtures, shapes))
    records = outcome["records"]
    records.sort(key=lambda r: (r["shape"], r["run"]))
    summary = summarise(records, shapes)
    summary["meta"] = {
        "provider": args.provider,
        "model": args.model,
        "n": args.n,
        "shapes": shapes,
        "fixtures_version": fixtures.version,
        "wall_s": round(time.time() - started, 1),
    }
    cost = token_cost(summary["usage"], args.price_in, args.price_out) or {}
    credits_after = (
        None
        if args.no_credits_probe or args.provider != "openrouter"
        else openrouter_credits()
    )
    if credits_before is not None and credits_after is not None:
        cost["usd_from_openrouter_credits"] = round(credits_after - credits_before, 6)
    if cost:
        summary["cost"] = cost

    print_summary(summary, f"MODEL MATRIX — {args.provider}/{args.model or 'default'} n={args.n}")
    if args.out:
        summary_path = Path(args.out).with_suffix(".summary.json")
        with summary_path.open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"\nrecords: {args.out}\nsummary: {summary_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
