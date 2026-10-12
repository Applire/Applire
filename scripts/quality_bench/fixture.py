# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load and validate one quality-bench fixture directory.

The format is specified, field by field with the product reader of each field,
in ``tests/files/quality_bench/FIXTURE_FORMAT.md``. This module is the ONE
reader of that format — the seeding code (``seed.py``) and the scorers both go
through :func:`load_fixture`, so a scorer sees exactly the vault / job / ledger
the generator was handed, never a re-derived copy.

Pure JSON + filesystem: importing this module does not import any ``applire``
code and therefore does not instantiate ``applire.config.settings``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CELL_TYPES = ("same", "cross", "mixed", "upward")
LANGUAGES = ("de", "en")

#: ``job.json`` keys that map 1:1 onto ``job_analyses`` columns (models/job.py).
JOB_COLUMNS = (
    "role_title",
    "company_name",
    "required_skills",
    "nice_to_have_skills",
    "keywords",
    "seniority_level",
    "company_culture_signals",
    "language_requirement",
    "education_requirement",
    "jd_language",
    "scope_requirements",
    "leadership_emphasis",
)
# ``source_url`` is deliberately NOT a seeded column: with a URL on the posting,
# ``_render_cv_background`` runs company-colour detection (services/cv.py:4027,
# services/color_detection.py:296-331) — favicon and meta-tag fetches over the
# network plus an extra LLM colour-fallback call. A bench run must not do either.

#: ``gap.json`` keys that map 1:1 onto ``gap_analyses`` columns (models/gap.py).
GAP_COLUMNS = (
    "match_score",
    "critical_gaps",
    "minor_gaps",
    "strengths",
    "keyword_gaps",
    "category_a",
    "category_b",
    "category_c",
    "keyword_ledger",
    "gap_clusters",
    "requirement_breakdown",
)

#: ``fixture.json["letter_inputs"]`` keys — the user-supplied fields of
#: ``CoverLetterGenerateRequest`` (schemas/cover_letter.py:96).
LETTER_INPUT_KEYS = ("recipient_name", "recipient_company", "salary", "availability", "motivation", "tone")


class FixtureError(ValueError):
    """The fixture directory does not follow FIXTURE_FORMAT.md."""


@dataclass
class Fixture:
    """A loaded fixture. ``dir`` is the directory it was read from."""

    dir: Path
    case: str
    cell_type: str
    document_language: str
    cell_id: str
    vault: dict[str, Any]
    job: dict[str, Any]
    posting: str
    gap: dict[str, Any]
    ledger: list[dict[str, Any]]
    provenance: dict[str, Any]
    template: str = "classic_german"
    target_pages: int | None = None
    letter_inputs: dict[str, Any] = field(default_factory=dict)
    raw_meta: dict[str, Any] = field(default_factory=dict)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FixtureError(f"{path} is missing") from None
    except json.JSONDecodeError as exc:
        raise FixtureError(f"{path} is not valid JSON: {exc}") from None


def load_fixture(fixture_dir: str | Path) -> Fixture:
    """Read ``<fixture_dir>`` and validate it; raise :class:`FixtureError` on any deviation."""
    d = Path(fixture_dir).resolve()
    if not d.is_dir():
        raise FixtureError(f"fixture directory {d} does not exist")

    meta = _read_json(d / "fixture.json")
    if not isinstance(meta, dict):
        raise FixtureError("fixture.json must be a JSON object")
    for key in ("case", "cell_type", "document_language", "provenance"):
        if key not in meta:
            raise FixtureError(f"fixture.json lacks required key {key!r}")
    if meta["cell_type"] not in CELL_TYPES:
        raise FixtureError(f"fixture.json cell_type {meta['cell_type']!r} not in {CELL_TYPES}")
    if meta["document_language"] not in LANGUAGES:
        raise FixtureError(f"fixture.json document_language {meta['document_language']!r} not in {LANGUAGES}")
    prov = meta["provenance"]
    if not isinstance(prov, dict) or not all(k in prov for k in ("how", "model", "date")):
        raise FixtureError("fixture.json provenance must be an object with how, model, date")

    vault = _read_json(d / "vault.json")
    if not isinstance(vault, dict) or not isinstance(vault.get("work_experience"), list):
        raise FixtureError("vault.json must be a profile object with a work_experience list")

    job = _read_json(d / "job.json")
    if not isinstance(job, dict):
        raise FixtureError("job.json must be a JSON object")
    for key in ("role_title", "required_skills"):
        if key not in job:
            raise FixtureError(f"job.json lacks required key {key!r}")

    posting_path = d / "posting.md"
    if posting_path.exists():
        posting = posting_path.read_text(encoding="utf-8")
    elif isinstance(job.get("raw_text"), str) and job["raw_text"].strip():
        posting = job["raw_text"]
    else:
        raise FixtureError("posting.md is missing (and job.json carries no raw_text)")
    if not posting.strip():
        raise FixtureError("posting.md is empty")

    gap: dict[str, Any] = {}
    if (d / "gap.json").exists():
        gap = _read_json(d / "gap.json")
        if not isinstance(gap, dict):
            raise FixtureError("gap.json must be a JSON object")
    ledger: list[dict[str, Any]] = []
    if isinstance(gap.get("keyword_ledger"), list):
        ledger = gap["keyword_ledger"]
    elif (d / "ledger.json").exists():
        ledger = _read_json(d / "ledger.json")
        if not isinstance(ledger, list):
            raise FixtureError("ledger.json must be a JSON list of keyword-ledger rows")

    letter_inputs = meta.get("letter_inputs") or {}
    unknown = set(letter_inputs) - set(LETTER_INPUT_KEYS)
    if unknown:
        raise FixtureError(f"fixture.json letter_inputs has unknown keys {sorted(unknown)}")

    case = str(meta["case"])
    cell_id = str(meta.get("cell_id") or f"{case}.{d.name}")
    return Fixture(
        dir=d,
        case=case,
        cell_type=meta["cell_type"],
        document_language=meta["document_language"],
        cell_id=cell_id,
        vault=vault,
        job=job,
        posting=posting,
        gap=gap,
        ledger=ledger,
        provenance=prov,
        template=str(meta.get("template") or "classic_german"),
        target_pages=meta.get("target_pages"),
        letter_inputs=dict(letter_inputs),
        raw_meta=meta,
    )


def _main(argv: list[str]) -> int:
    """``python3 scripts/quality_bench/fixture.py <dir> [<dir> ...]`` — validate fixtures."""
    if not argv:
        print("usage: fixture.py <fixture-dir> [<fixture-dir> ...]", file=sys.stderr)
        return 2
    rc = 0
    for arg in argv:
        try:
            fx = load_fixture(arg)
        except FixtureError as exc:
            print(f"INVALID {arg}: {exc}")
            rc = 1
            continue
        print(
            f"ok {fx.cell_id}: {fx.cell_type}/{fx.document_language}, "
            f"{len(fx.vault.get('work_experience') or [])} work entries, "
            f"{len(fx.ledger)} ledger rows, {len(fx.posting)} posting chars"
        )
    return rc


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
