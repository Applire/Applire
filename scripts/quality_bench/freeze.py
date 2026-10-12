#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 — freeze vaults produced on a real stack into bench fixtures.

Input: a JSON bundle ``{vault_key: {"profile_json": {...}, "postings": [{"application", "job", "gap"}]}}``
(as dumped from a stack's database — one entry per bench user) and the vault working folders
``<vaults_dir>/<vault_key>/case/posting_{original,cross,upward}.md`` that say which posting is which.

``vault_key`` is ``<case>.<native|mixed>``. Cells written (FIXTURE_FORMAT.md):

* native vault + original posting  → ``<case>/same_<lang>``
* native vault + cross posting      → ``<case>/cross_<lang>2<other>`` (document language = posting language)
* native vault + upward posting     → ``<case>/upward_<lang>``
* mixed vault + original posting    → ``<case>/mixed_<lang>``

A posting is matched to its file by its stored ``raw_text`` (whitespace-normalised prefix), never by order.

Usage::

    python3 scripts/quality_bench/freeze.py BUNDLE.json VAULTS_DIR OUT_DIR --model openai/gpt-5.6-luna --date 2026-10-12
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CASE_LANGUAGE = {
    "it_backend_daniel": "en",
    "operations_marcus_de": "de",
    "nursing_priya_relocator": "en",
    "controlling_emma_de": "de",
}
JOB_KEYS = (
    "role_title", "company_name", "required_skills", "nice_to_have_skills", "keywords",
    "seniority_level", "company_culture_signals", "language_requirement", "education_requirement",
    "jd_language", "scope_requirements", "leadership_emphasis",
)
GAP_SKIP = ("id", "user_id", "job_analysis_id", "created_at", "updated_at", "deleted_at")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()[:400]


def _which_posting(raw_text: str, case_dir: Path) -> str | None:
    want = _norm(raw_text)
    for kind in ("original", "cross", "upward"):
        f = case_dir / f"posting_{kind}.md"
        if f.exists() and _norm(f.read_text())[:200] == want[:200]:
            return kind
    return None


def freeze(bundle: dict, vaults_dir: Path, out_dir: Path, *, model: str, date: str) -> list[Path]:
    written: list[Path] = []
    for key, entry in sorted(bundle.items()):
        case, variant = key.split(".")
        lang = CASE_LANGUAGE[case]
        other = "de" if lang == "en" else "en"
        if not entry.get("profile_json"):
            raise SystemExit(f"{key}: no vault in the bundle")
        for p in entry["postings"]:
            job, gap = p.get("job"), p.get("gap")
            if not job:
                continue
            kind = _which_posting(job.get("raw_text") or "", vaults_dir / key / "case")
            if kind is None:
                raise SystemExit(f"{key}: posting {job.get('role_title')!r} matches no posting file")
            if variant == "mixed":
                if kind != "original":
                    continue
                cell, cell_type, doc_lang = f"mixed_{lang}", "mixed", lang
            elif kind == "original":
                cell, cell_type, doc_lang = f"same_{lang}", "same", lang
            elif kind == "cross":
                cell, cell_type, doc_lang = f"cross_{lang}2{other}", "cross", other
            else:
                cell, cell_type, doc_lang = f"upward_{lang}", "upward", lang
            if not gap:
                raise SystemExit(f"{key}/{cell}: no gap analysis — run analyze_gaps before freezing")
            d = out_dir / case / cell
            d.mkdir(parents=True, exist_ok=True)
            (d / "vault.json").write_text(json.dumps(entry["profile_json"], ensure_ascii=False, indent=2))
            (d / "job.json").write_text(json.dumps({k: job.get(k) for k in JOB_KEYS}, ensure_ascii=False, indent=2))
            (d / "posting.md").write_text(job.get("raw_text") or "")
            (d / "gap.json").write_text(json.dumps({k: v for k, v in gap.items() if k not in GAP_SKIP}, ensure_ascii=False, indent=2))
            (d / "fixture.json").write_text(json.dumps({
                "case": case, "cell_type": cell_type, "document_language": doc_lang,
                "provenance": {
                    "how": f"real pipeline on the dev stack: import of both sources + interview from the case dossier "
                           f"(vault {key}); job + gap analysis of posting_{kind}.md; frozen by freeze.py",
                    "model": model, "date": date,
                },
            }, ensure_ascii=False, indent=2))
            written.append(d)
    return written


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle"); ap.add_argument("vaults_dir"); ap.add_argument("out_dir")
    ap.add_argument("--model", required=True); ap.add_argument("--date", required=True)
    a = ap.parse_args(argv)
    bundle = json.loads(Path(a.bundle).read_text())
    for d in freeze(bundle, Path(a.vaults_dir), Path(a.out_dir), model=a.model, date=a.date):
        print(d)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
