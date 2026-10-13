# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (scorers) — read a bench run directory and its frozen fixture.

The run-output contract (COMMON-BRIEF) is the only coupling to the harness
package::

    <run>/meta.json    cv.json    letter.json    reports.json    pages.json    llm.jsonl

``meta.json["fixture_dir"]`` names the frozen inputs (``tests/files/quality_bench/
FIXTURE_FORMAT.md``): ``vault.json`` (the profile JSON as the generator receives
it), ``job.json`` (the ``job_analyses`` columns) and the keyword ledger
(``gap.json["keyword_ledger"]``, else ``ledger.json``). The run directory's own
``ledger_effective.json`` (the ledger AFTER the pipeline's refresh against the
vault — what the generator actually saw) wins when present. The job and
ledger readers below are deliberately tolerant about nesting — the fixture
format is owned by the harness package — and look for the documented keys
(``required_skills``, ``nice_to_have_skills``, ``concept``/``status``/…)
wherever they sit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LANGUAGE_NAMES = {"de": "German", "en": "English"}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


@dataclass
class RunData:
    run_dir: Path
    meta: dict[str, Any]
    cv: dict | None
    letter: dict | None
    reports: dict[str, Any]
    pages: dict[str, Any]

    @property
    def run_id(self) -> str:
        m = self.meta
        return "/".join(str(m.get(k, "?")) for k in ("arm", "model_slug", "cell_id", "run_idx"))

    @property
    def language(self) -> str:
        return str(self.meta.get("document_language") or "de")

    @property
    def ok(self) -> bool:
        return self.meta.get("status") == "ok"


def load_run(run_dir: Path | str) -> RunData:
    run_dir = Path(run_dir)
    meta = read_json(run_dir / "meta.json", {}) or {}
    return RunData(
        run_dir=run_dir,
        meta=meta,
        cv=read_json(run_dir / "cv.json"),
        letter=read_json(run_dir / "letter.json"),
        reports=read_json(run_dir / "reports.json", {}) or {},
        pages=read_json(run_dir / "pages.json", {}) or {},
    )


def find_run_dirs(patterns: list[str]) -> list[Path]:
    """Expand dirs / globs into run directories (those holding a meta.json)."""
    import glob

    found: dict[str, Path] = {}
    for pat in patterns:
        matches = [Path(p) for p in glob.glob(pat)] or [Path(pat)]
        for p in matches:
            if p.is_file() and p.name == "meta.json":
                found[str(p.parent.resolve())] = p.parent
            elif p.is_dir():
                if (p / "meta.json").exists():
                    found[str(p.resolve())] = p
                else:
                    for m in sorted(p.rglob("meta.json")):
                        found[str(m.parent.resolve())] = m.parent
    return [found[k] for k in sorted(found)]


# --------------------------------------------------------------------------- #
# Fixture: vault, job (JD), ledger
# --------------------------------------------------------------------------- #


@dataclass
class Fixture:
    vault: dict
    job: dict
    ledger: list[dict]
    fixture_dir: Path | None = None
    warnings: list[str] = field(default_factory=list)


def load_fixture(meta: dict[str, Any], bases: list[Path] | Path | None = None) -> Fixture:
    """Load the frozen inputs named by ``meta["fixture_dir"]``.

    A relative ``fixture_dir`` is resolved against each of ``bases`` (and their
    parents) in turn — the harness records it repo-relative
    (``tests/files/quality_bench/<case>/<cell>``).
    """
    raw = meta.get("fixture_dir")
    warnings: list[str] = []
    fdir: Path | None = None
    if raw:
        cand = Path(raw)
        if cand.is_absolute():
            fdir = cand if cand.is_dir() else None
        else:
            roots = [bases] if isinstance(bases, Path) else list(bases or [])
            for base in roots:
                for root in (base, *base.parents):
                    if (root / cand).is_dir():
                        fdir = root / cand
                        break
                if fdir:
                    break
    if fdir is None:
        warnings.append(f"fixture_dir {raw!r} not found")
        return Fixture({}, {}, [], None, warnings)
    vault = read_json(fdir / "vault.json", None)
    if vault is None:
        warnings.append("vault.json missing")
    job = read_json(fdir / "job.json", None) or {}
    # FIXTURE_FORMAT.md: the ledger is gap.json["keyword_ledger"]; a bare ledger.json is the fallback.
    gap = read_json(fdir / "gap.json", None)
    ledger_raw = gap.get("keyword_ledger") if isinstance(gap, dict) else None
    if not isinstance(ledger_raw, list):
        ledger_raw = read_json(fdir / "ledger.json", None)
    if isinstance(ledger_raw, dict):
        ledger_raw = ledger_raw.get("rows") or ledger_raw.get("keyword_ledger") or ledger_raw.get("ledger") or []
    return Fixture(vault or {}, job, [r for r in (ledger_raw or []) if isinstance(r, dict)], fdir, warnings)


def _find_key(obj: Any, key: str) -> Any:
    """Depth-first search for the first dict value stored under ``key``."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


# --------------------------------------------------------------------------- #
# Vault view: "path: text" lines, paths addressing vault.json
# --------------------------------------------------------------------------- #

_UNUSABLE = {"unconfirmed", "denied"}


def _usable(entry: Any) -> bool:
    return not (isinstance(entry, dict) and str(entry.get("status") or "").lower() in _UNUSABLE)


def _clean(*parts: Any) -> str:
    return ", ".join(str(p) for p in parts if p not in (None, "", [], {}))


def vault_lines(vault: dict | None) -> list[tuple[str, str]]:
    """The vault as ``(path, text)`` lines the judge can cite.

    Paths index ``vault.json`` as given (indices are NOT renumbered after
    dropping an unconfirmed/denied entry), so a ``vault_ref`` resolves against
    the fixture. Unconfirmed and denied entries are left out — the generator
    never saw them either (``exclude_unconfirmed``), so they are not evidence.
    """
    v = vault or {}
    out: list[tuple[str, str]] = []

    def add(path: str, text: str) -> None:
        if text and text.strip():
            out.append((path, text.strip()))

    ps = v.get("professional_summary")
    if isinstance(ps, dict):
        for lang in ("de", "en"):
            add(f"professional_summary.{lang}", ps.get(lang) or "")
    elif isinstance(ps, str):
        add("professional_summary", ps)

    def experience(prefix: str, entry: dict, head: str) -> None:
        add(prefix, head)
        for field_name in ("responsibilities", "achievements"):
            for j, t in enumerate(entry.get(field_name) or []):
                if isinstance(t, str):
                    add(f"{prefix}.{field_name}[{j}]", t)
        if entry.get("technologies"):
            add(f"{prefix}.technologies", ", ".join(str(t) for t in entry["technologies"]))

    for i, w in enumerate(v.get("work_experience") or []):
        if isinstance(w, dict) and _usable(w):
            head = _clean(
                f"id={w.get('id')}" if w.get("id") else None,
                f"{w.get('role')} @ {w.get('company')}",
                f"{w.get('start_date')} - {w.get('end_date') or 'present'}",
                f"team_size={w.get('team_size')}" if w.get("team_size") is not None else None,
                f"budget_managed={w.get('budget_managed')}" if w.get("budget_managed") else None,
                f"industry_context={w.get('industry_context')}" if w.get("industry_context") else None,
            )
            experience(f"work_experience[{i}]", w, head)
    for i, e in enumerate(v.get("education") or []):
        if isinstance(e, dict) and _usable(e):
            add(f"education[{i}]", _clean(e.get("degree"), e.get("field"), e.get("institution"), e.get("grade"), e.get("thesis_title")))
    for i, c in enumerate(v.get("certifications") or []):
        if isinstance(c, dict) and _usable(c):
            add(f"certifications[{i}]", _clean(c.get("name"), c.get("issuing_organization"), c.get("date_obtained")))
    for i, s in enumerate(v.get("skills") or []):
        if isinstance(s, dict) and _usable(s):
            tail = _clean(s.get("category"), s.get("proficiency"), f"{s.get('years_experience')}y" if s.get("years_experience") else None)
            alias = f" aka {', '.join(s['aliases'])}" if s.get("aliases") else ""
            add(f"skills[{i}]", f"{s.get('name')} ({tail}){alias}")
    for i, lg in enumerate(v.get("languages") or []):
        if isinstance(lg, dict) and _usable(lg):
            add(f"languages[{i}]", _clean(lg.get("language"), lg.get("level")))
    for i, p in enumerate(v.get("projects") or []):
        if isinstance(p, dict) and _usable(p):
            experience(f"projects[{i}]", p, _clean(p.get("name"), p.get("description"), p.get("associated_experience")))
    for i, a in enumerate(v.get("volunteer_activities") or []):
        if isinstance(a, dict) and _usable(a):
            experience(f"volunteer_activities[{i}]", a, _clean(a.get("role"), a.get("organization"), a.get("description")))
    for i, st in enumerate(v.get("signature_stories") or []):
        if isinstance(st, dict):
            add(
                f"signature_stories[{i}]",
                " | ".join(f"{k}: {st[k]}" for k in ("title", "challenge", "mechanism", "outcome", "benchmark") if st.get(k)),
            )
    for i, pub in enumerate(v.get("publications") or []):
        if isinstance(pub, dict):
            add(f"publications[{i}]", _clean(pub.get("title"), pub.get("venue"), pub.get("published_date")))
    meta = v.get("metadata") if isinstance(v.get("metadata"), dict) else {}
    for i, d in enumerate(meta.get("denied_concepts") or []):
        if isinstance(d, dict):
            add(f"metadata.denied_concepts[{i}]", f"DENIED by the candidate: {d.get('concept')} — {d.get('statement')}")
    return out


def vault_text(vault: dict | None) -> str:
    return "\n".join(f"{p}: {t}" for p, t in vault_lines(vault))


def resolve_vault_ref(vault: dict | None, ref: str | None) -> str:
    """Text behind a judge-cited ``vault_ref`` (exact path, id, or path prefix)."""
    if not ref:
        return ""
    lines = vault_lines(vault)
    lookup = dict(lines)
    r = ref.strip()
    if r in lookup:
        return lookup[r]
    for path, text in lines:
        if f"id={r}" in text:
            return text
    m = re.match(r"^([a-z_]+\[\d+\](?:\.[a-z_]+(?:\[\d+\])?)?)", r)
    if m and m.group(1) in lookup:
        return lookup[m.group(1)]
    return r


# --------------------------------------------------------------------------- #
# JD view: required / preferred items with their ledger rows
# --------------------------------------------------------------------------- #


def jd_requirements(job: dict | None, ledger: list[dict] | None) -> dict[str, Any]:
    job = job or {}
    ledger = ledger or []
    required = [str(x) for x in (_find_key(job, "required_skills") or []) if x]
    preferred = [str(x) for x in (_find_key(job, "nice_to_have_skills") or []) if x]

    def key(s: str) -> str:
        return s.strip().casefold()

    by_concept: dict[str, dict] = {}
    for row in ledger:
        concept = str(row.get("concept") or "")
        by_concept[key(concept)] = row
        for sf in row.get("surface_forms") or []:
            by_concept.setdefault(key(str(sf)), row)
    for row in ledger:  # ledger rows the JD lists did not carry
        srcs = row.get("sources") or []
        concept = str(row.get("concept") or "")
        if "required" in srcs and key(concept) not in {key(r) for r in required}:
            required.append(concept)
        elif "nice_to_have" in srcs and key(concept) not in {key(p) for p in preferred + required}:
            preferred.append(concept)

    def with_row(item: str) -> dict[str, Any]:
        row = by_concept.get(key(item))
        entry: dict[str, Any] = {"item": item}
        if row:
            entry["ledger"] = {k: row.get(k) for k in ("status", "claimable", "evidence") if row.get(k) not in (None, "")}
        return entry

    return {
        "role_title": _find_key(job, "role_title") or job.get("title"),
        "seniority_level": _find_key(job, "seniority_level"),
        "required": [with_row(i) for i in required],
        "preferred": [with_row(i) for i in preferred],
        "scope_requirements": _find_key(job, "scope_requirements") or [],
    }


def jd_text(job: dict | None, ledger: list[dict] | None) -> str:
    req = jd_requirements(job, ledger)
    return json.dumps(req, ensure_ascii=False, indent=1)
