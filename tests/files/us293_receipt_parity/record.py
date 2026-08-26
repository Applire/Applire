# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""US293 — the receipt-parity fixture: the pre-epic receipt form, recorded once.

E055 replaced the profile page's whole-section JSON textarea with structured
editors (US290–US292). The epic's 🔒 boundary says the editors change the
*payload shape* (lists that round-trip entry ids, merge-patches of the keys the
user actually changed) but NOT the write: every save is still the `FieldEdit`
intake on `commit_ops`, and every save still leaves the per-entry receipt the
2026-08-09 ADR-063 clause-8 ruling specified. US293 is the proof.

This module is shared by two programs that must never drift apart:

* **the recorder** (`python3 record.py`, run under the PRE-EPIC tree) — drives
  the REST door exactly as the textarea did (the whole section as JSON) for
  every (section × add/update/rename/remove) case and writes the receipt each
  edit left into `fixture.json`;
* **the pin** (`tests/unit/test_us293_receipt_parity.py`, run under today's
  tree) — drives the same doors with TODAY's editor payloads and asserts the
  receipt form is the recorded one.

Both load this file by path, so the seed vault, the case table, the two body
builders and the normaliser are ONE implementation. The recorder refuses to run
unless the imported `applire` package sits in a checkout of the pre-epic commit
(`PRE_EPIC_COMMIT` — main before US290), so the fixture cannot be re-recorded
from the wrong tree by accident.

Re-record only when the pre-epic form is re-derived on purpose:

    git worktree add --detach ../wt-pre-epic 1b410de9
    PYTHONPATH=../wt-pre-epic/backend python3 tests/files/us293_receipt_parity/record.py
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURE_PATH = HERE / "fixture.json"

#: main before US290 — the last commit whose profile page sent whole sections.
PRE_EPIC_COMMIT = "1b410de902f36f3a2550afe10216cf7e4d7be867"

#: The production files whose behaviour the fixture captures; their digests
#: are recorded so a reader can verify which code produced the receipts.
PROVENANCE_FILES = (
    "services/profile/reconcile/apply.py",
    "services/profile/field_edit.py",
    "services/profile/commit.py",
    "services/profile/__init__.py",
    "routers/profile.py",
    "schemas/profile.py",
)

# ── The seed vault ────────────────────────────────────────────────────────────
#
# Every list entry carries a fixed id, so the receipts are deterministic and an
# "update" is paired by id (ADR-077 clause 1) on both trees. Every section is
# populated with TWO entries: one to edit, one to remove.


def _fid(n: int) -> str:
    """A fixed, valid-looking uuid4 string for seed entry `n`."""
    return f"{n:08d}-0000-4000-8000-{n:012d}"


WORK_A, WORK_B = _fid(1), _fid(2)
EDU_A, EDU_B = _fid(3), _fid(4)
CERT_A, CERT_B = _fid(5), _fid(6)
SKILL_A, SKILL_B, SKILL_C = _fid(7), _fid(8), _fid(9)
LANG_A, LANG_B = _fid(10), _fid(11)
PUB_A, PUB_B = _fid(12), _fid(13)
VOL_A, VOL_B = _fid(14), _fid(15)
STORY_A, STORY_B = _fid(16), _fid(17)
PROJ_A, PROJ_B = _fid(18), _fid(19)

SEED_PROFILE: dict[str, Any] = {
    "personal_info": {
        "name": "Anna Bauer",
        "email": "anna@example.invalid",
        "phone": "+49 30 000000",
        "location": None,
        "date_of_birth": "1990-04-12",
        "linkedin_url": "https://www.linkedin.com/in/anna-bauer-example",
    },
    "professional_summary": {"de": "Deutsche Zusammenfassung.", "en": None},
    "work_experience": [
        {
            "id": WORK_A,
            "company": "Acme GmbH",
            "role": "Engineer",
            "location": "Berlin",
            "start_date": "2020-01",
            "is_current": True,
            "responsibilities": ["Ran the build", "Owned the release train"],
            "technologies": ["Python", "Kafka"],
        },
        {
            "id": WORK_B,
            "company": "Beta AG",
            "role": "Analyst",
            "start_date": "2016-01",
            "end_date": "2019-12",
            "is_current": False,
            "responsibilities": ["Reported on pipeline health"],
        },
    ],
    "education": [
        {
            "id": EDU_A,
            "institution": "TU Berlin",
            "degree": "M.Sc.",
            "field": "Computer Science",
            "start_date": "2010-10",
            "end_date": "2013-03",
        },
        {
            "id": EDU_B,
            "institution": "Universität Hamburg",
            "degree": "B.Sc.",
            "field": "Mathematics",
            "start_date": "2007-10",
            "end_date": "2010-09",
        },
    ],
    "certifications": [
        {
            "id": CERT_A,
            "name": "AWS Solutions Architect – Associate",
            "issuing_organization": "Amazon Web Services",
            "date_obtained": "2022-05-01",
            "status": "confirmed",
        },
        {
            "id": CERT_B,
            "name": "Professional Scrum Master I",
            "issuing_organization": "Scrum.org",
            "date_obtained": "2019-02-15",
            "status": "confirmed",
        },
    ],
    "skills": [
        {"id": SKILL_A, "name": "Python", "category": "technical", "proficiency": "advanced", "status": "confirmed"},
        {"id": SKILL_B, "name": "Kafka", "category": "technical", "proficiency": "intermediate", "status": "confirmed"},
        {"id": SKILL_C, "name": "SQL", "category": "technical", "proficiency": "intermediate", "status": "unconfirmed"},
    ],
    "languages": [
        {"id": LANG_A, "language": "German", "level": "C2", "status": "confirmed"},
        {"id": LANG_B, "language": "English", "level": "C1", "status": "confirmed"},
    ],
    "publications": [
        {
            "id": PUB_A,
            "title": "Kafka in Practice",
            "type": "publication",
            "venue": "JavaSPEKTRUM",
            "published_date": "2021-06-01",
        },
        {"id": PUB_B, "title": "Observability for Batch Jobs", "type": "publication", "venue": "Blog"},
    ],
    "volunteer_activities": [
        {
            "id": VOL_A,
            "organization": "CoderDojo Berlin",
            "role": "Mentor",
            "start_date": "2018-01",
            "is_current": True,
        },
        {"id": VOL_B, "organization": "Tafel Berlin", "role": "Volunteer", "start_date": "2015-03", "end_date": "2016-03", "is_current": False},
    ],
    "signature_stories": [
        {
            "id": STORY_A,
            "title": "The migration nobody noticed",
            "challenge": "A monolith with a 2h deploy window",
            "mechanism": "Strangler pattern behind a feature flag",
            "outcome": "Deploy time down to 6 minutes",
        },
        {
            "id": STORY_B,
            "title": "Pipeline health made visible",
            "challenge": "Silent batch failures",
            "mechanism": "Per-job SLO dashboards",
            "outcome": "Mean time to detect from 2 days to 20 minutes",
        },
    ],
    "projects": [
        {
            "id": PROJ_A,
            "name": "Ledger",
            "role": "Lead",
            "description": "Double-entry ledger service",
            "start_date": "2022-01",
            "is_current": True,
            "technologies": ["Python", "PostgreSQL"],
        },
        {"id": PROJ_B, "name": "Build Cache", "role": "Contributor", "start_date": "2021-05", "end_date": "2021-09", "is_current": False},
    ],
    "metadata": {},
}

# ── Today's editor drafts ─────────────────────────────────────────────────────
#
# What each profile-page editor sends for a NEW entry, transcribed from
# `frontend/lib/profile-entries.ts` (`makeEmpty*`): every form field, unset ones
# as `null` / `[]`, and NO `id` (`WorkExperienceEditor` strips it — the vault
# mints one). The pre-epic textarea user typed only the fields they had.
# `signature_stories` has no profile-page editor (ADR-055: stories arrive
# through the interview and the agent door), so its "editor" body is the
# entry as typed.
EDITOR_DRAFTS: dict[str, dict[str, Any]] = {
    "work_experience": {
        "company": "", "role": "", "location": None, "start_date": None, "end_date": None,
        "is_current": None, "responsibilities": [], "achievements": [], "technologies": [],
        "role_aliases": [], "industry_context": None, "team_size": None, "budget_managed": None,
        "expected_fields": None, "role_fact_projections": {},
    },
    "education": {
        "institution": "", "degree": "", "field": "", "start_date": None, "end_date": None,
        "grade": None, "thesis_title": None, "relevant_coursework": [],
    },
    "skills": {
        "name": "", "category": "technical", "proficiency": "intermediate",
        "years_experience": None, "last_used": None, "status": "confirmed",
    },
    "languages": {"language": "", "level": None, "status": "confirmed"},
    "certifications": {
        "name": "", "issuing_organization": None, "date_obtained": None, "expiry_date": None,
        "credential_id": None, "credential_url": None, "status": "confirmed",
    },
    "projects": {
        "name": "", "description": None, "url": None, "associated_experience": None, "role": "",
        "location": None, "start_date": None, "end_date": None, "is_current": None,
        "responsibilities": [], "achievements": [], "technologies": [], "expected_fields": None,
    },
    "publications": {
        "title": "", "type": "publication", "co_authors": [], "venue": None,
        "published_date": None, "doi": None, "url": None, "patent_number": None,
    },
    "volunteer_activities": {
        "organization": "", "role": "", "description": None, "cause": None, "location": None,
        "start_date": None, "end_date": None, "is_current": None,
        "responsibilities": [], "achievements": [], "technologies": [], "expected_fields": None,
    },
    "signature_stories": {},
}

# ── The case table ────────────────────────────────────────────────────────────
#
# One row per (section × op). List sections get four ops: `add` (an id-less
# new entry), `update` (a non-label field of an existing entry), `rename` (a
# LABEL field — the receipt is paired by id and named by the new label) and
# `remove`. Object sections get three: `add` (a key that was empty), `update`
# and `remove` (an explicit null clears the key — #178 merge-patch semantics).


def _list_cases(section: str, target: str, remove: str, *, add: dict, update: dict, rename: dict) -> list[dict]:
    return [
        {"id": f"{section}.add", "section": section, "kind": "list", "op": "add", "entry": add},
        {"id": f"{section}.update", "section": section, "kind": "list", "op": "update", "target": target, "set": update},
        {"id": f"{section}.rename", "section": section, "kind": "list", "op": "rename", "target": target, "set": rename},
        {"id": f"{section}.remove", "section": section, "kind": "list", "op": "remove", "target": remove},
    ]


def _object_case(section: str, op: str, patch: dict) -> dict:
    return {"id": f"{section}.{op}", "section": section, "kind": "object", "op": op, "set": patch}


CASES: list[dict[str, Any]] = [
    *_list_cases(
        "work_experience", WORK_A, WORK_B,
        add={"company": "Gamma SE", "role": "Platform Lead", "start_date": "2021-03", "is_current": True},
        update={"end_date": "2022-06", "is_current": False},
        rename={"role": "Senior Engineer"},
    ),
    *_list_cases(
        "education", EDU_A, EDU_B,
        add={"institution": "RWTH Aachen", "degree": "Dr.-Ing.", "field": "Computer Science", "start_date": "2013-10"},
        update={"grade": "1.3"},
        rename={"degree": "M.Sc. Informatik"},
    ),
    *_list_cases(
        "certifications", CERT_A, CERT_B,
        add={"name": "Certified Kubernetes Administrator", "issuing_organization": "CNCF", "status": "confirmed"},
        update={"expiry_date": "2027-05-01"},
        rename={"name": "AWS Solutions Architect – Professional"},
    ),
    *_list_cases(
        "skills", SKILL_A, SKILL_B,
        add={"name": "Terraform", "category": "technical", "proficiency": "intermediate", "status": "confirmed"},
        update={"proficiency": "expert"},
        rename={"name": "Python 3"},
    ),
    *_list_cases(
        "languages", LANG_A, LANG_B,
        add={"language": "French", "level": "B1", "status": "confirmed"},
        update={"level": "C1"},
        rename={"language": "Deutsch"},
    ),
    *_list_cases(
        "publications", PUB_A, PUB_B,
        add={"title": "Streaming at Scale", "type": "publication", "venue": "QCon London"},
        update={"published_date": "2024-11-05"},
        rename={"title": "Kafka in Practice, 2nd ed."},
    ),
    *_list_cases(
        "volunteer_activities", VOL_A, VOL_B,
        add={"organization": "Hackerspace Berlin", "role": "Mentor"},
        update={"cause": "Education"},
        rename={"role": "Board member"},
    ),
    *_list_cases(
        "signature_stories", STORY_A, STORY_B,
        add={
            "title": "Zero-downtime schema change",
            "challenge": "A 400 GB table nobody dared to alter",
            "mechanism": "Online migration in shadow columns",
            "outcome": "Zero minutes of downtime across 3 releases",
        },
        update={"benchmark": "industry median: 4 h maintenance window"},
        rename={"title": "The migration nobody noticed (2)"},
    ),
    *_list_cases(
        "projects", PROJ_A, PROJ_B,
        add={"name": "Cost Explorer", "role": "Lead"},
        update={"description": "Double-entry ledger service for the platform"},
        rename={"name": "Ledger v2"},
    ),
    _object_case("personal_info", "add", {"location": "Berlin"}),
    _object_case("personal_info", "update", {"phone": "+49 30 111111"}),
    _object_case("personal_info", "remove", {"phone": None}),
    _object_case("professional_summary", "add", {"en": "English summary."}),
    _object_case("professional_summary", "update", {"de": "Neue deutsche Zusammenfassung."}),
    _object_case("professional_summary", "remove", {"de": None}),
]

CASE_BY_ID: dict[str, dict[str, Any]] = {c["id"]: c for c in CASES}
LIST_OPS = ("add", "update", "rename", "remove")
OBJECT_OPS = ("add", "update", "remove")


# ── Bodies: what each era's UI actually sent ──────────────────────────────────


def loaded_section(seed: dict, section: str) -> Any:
    """The section as `GET /api/profile` returns it — the state both eras'
    UIs edited: the schema's JSON dump, ids and defaults included."""
    from applire.schemas.profile import MasterProfileData

    return MasterProfileData.model_validate(copy.deepcopy(seed)).model_dump(mode="json")[section]


def _apply_list_op(case: dict, entries: list, new_entry: dict) -> list:
    op = case["op"]
    if op == "add":
        return [*entries, new_entry]
    if op in ("update", "rename"):
        return [{**e, **case["set"]} if e.get("id") == case["target"] else e for e in entries]
    if op == "remove":
        return [e for e in entries if e.get("id") != case["target"]]
    raise ValueError(op)


def pre_epic_body(case: dict, loaded: Any) -> Any:
    """The textarea's PATCH body: the WHOLE section as loaded, edited in place.

    Object sections went out as the complete object (every key, `photo_url`
    included); a new list entry was typed with the fields the user had.
    """
    if case["kind"] == "object":
        return {**loaded, **case["set"]}
    return _apply_list_op(case, copy.deepcopy(loaded), dict(case["entry"]) if case["op"] == "add" else {})


def editor_body(case: dict, loaded: Any) -> Any:
    """Today's editor payload (`frontend/lib/sectionSave.ts`).

    * `saveProfileObjectSection`: a MERGE-PATCH carrying only the keys the
      user changed;
    * `saveProfileSection`: the COMPLETE next list — existing entries as
      loaded (ids kept), the edited entry replaced in place, a removed entry
      filtered out, a new entry appended as the editor's full draft WITHOUT an
      id (`makeEmpty*` + the typed fields).
    """
    if case["kind"] == "object":
        return dict(case["set"])
    draft = {**EDITOR_DRAFTS.get(case["section"], {}), **case["entry"]} if case["op"] == "add" else {}
    return _apply_list_op(case, copy.deepcopy(loaded), draft)


# ── The receipt form ──────────────────────────────────────────────────────────

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def mask(value: Any) -> Any:
    """Replace every uuid-shaped string (minted ids differ per run) by a marker."""
    if isinstance(value, str) and _UUID.match(value):
        return "<uuid>"
    if isinstance(value, dict):
        return {k: mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v) for v in value]
    return value


def _describe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(value), "value": mask(value)}
    if isinstance(value, list):
        return {"type": "list", "value": mask(value)}
    return {"type": type(value).__name__, "value": value}


def change_shape(change: dict) -> dict:
    """One `FieldChange` as the parity pin compares it: every receipt field
    verbatim, values with their key sets and uuids masked, plus whether an
    update kept the entry's identity (ADR-077 clause 1)."""
    old, new = change.get("old_value"), change.get("new_value")
    shape = {
        "section": change["section"],
        "field": change["field"],
        "action": change["action"],
        "rationale": change.get("rationale"),
        "rationale_key": change.get("rationale_key"),
        "old_value": _describe(old),
        "new_value": _describe(new),
    }
    if isinstance(old, dict) and isinstance(new, dict):
        shape["same_id"] = old.get("id") is not None and old.get("id") == new.get("id")
    return shape


def receipt_shape(record: dict) -> dict:
    """One `EnrichmentRecord` as the parity pin compares it (timestamp and
    record id excluded — they are minted per write)."""
    return {
        "source": record["source"],
        "source_session_id": record.get("source_session_id"),
        "confidence": record.get("confidence"),
        "reconciliation": record.get("reconciliation"),
        "changes": [change_shape(c) for c in record["changes"]],
    }


def skeleton(shape: dict) -> dict:
    """The section-generic form of a receipt: what every LIST section's edit
    has in common once the section name, the entry label and the entry's own
    keys are abstracted away. `projects` had no pre-epic door (422), so its
    receipts are pinned against this form of its siblings."""
    out = {k: shape[k] for k in ("source", "source_session_id", "confidence", "reconciliation")}
    changes = []
    for c in shape["changes"]:
        rationale = c["rationale"] or ""
        rationale = rationale.replace(c["field"], "<label>").replace(c["section"], "<section>")
        changes.append(
            {
                "action": c["action"],
                "rationale_key": c["rationale_key"],
                "rationale": rationale,
                "old_value": None if c["old_value"] is None else c["old_value"]["type"],
                "new_value": None if c["new_value"] is None else c["new_value"]["type"],
                "same_id": c.get("same_id"),
            }
        )
    out["changes"] = changes
    return out


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seed_sha256() -> str:
    return _sha256(json.dumps(SEED_PROFILE, sort_keys=True).encode())


def cases_sha256() -> str:
    return _sha256(json.dumps(CASES, sort_keys=True).encode())


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# ── The recorder (pre-epic tree only) ─────────────────────────────────────────


def _applire_root() -> Path:
    import applire

    # `applire` is a namespace package (no `__init__.py`), so `__file__` is None.
    return Path(next(iter(applire.__path__))).resolve()


def _checkout_head(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def _provenance(root: Path) -> dict:
    return {
        "commit": _checkout_head(root),
        "applire_root": str(root),
        "sha256": {rel: _sha256((root / rel).read_bytes()) for rel in PROVENANCE_FILES},
    }


async def _fresh_db(path: Path):
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.uploads  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.db.session import Base

    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(factory) -> uuid.UUID:
    from applire.models.profile import MasterProfile, authorized_profile_write

    async with factory() as session:
        with authorized_profile_write():
            record = MasterProfile(profile_json=copy.deepcopy(SEED_PROFILE))
        session.add(record)
        await session.commit()
        return record.id


async def _read_back(engine, profile_id: uuid.UUID) -> dict:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from applire.models.profile import MasterProfile

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        row = (await session.execute(select(MasterProfile).where(MasterProfile.id == profile_id))).scalar_one()
        return dict(row.profile_json)


def fake_request(body: Any):
    """`patch_section` uses exactly one thing of the Request: `await .json()`."""
    from unittest.mock import AsyncMock

    return types.SimpleNamespace(json=AsyncMock(return_value=body))


async def _drive_rest(factory, section: str, body: Any) -> None:
    from applire.routers.profile import patch_section

    async with factory() as session:
        await patch_section(section, fake_request(body), session, None, None)


async def _record_one(case: dict, workdir: Path) -> dict:
    from fastapi import HTTPException

    engine, factory = await _fresh_db(workdir / f"{case['id']}.sqlite")
    try:
        profile_id = await _seed(factory)
        body = pre_epic_body(case, loaded_section(SEED_PROFILE, case["section"]))
        entry: dict[str, Any] = {
            "section": case["section"],
            "kind": case["kind"],
            "op": case["op"],
            "pre_epic_body": body,
            "status": 200,
        }
        try:
            await _drive_rest(factory, case["section"], body)
        except HTTPException as exc:
            entry["status"] = exc.status_code
            entry["refusal"] = str(exc.detail)
            return entry
        stored = await _read_back(engine, profile_id)
        record = stored["metadata"]["enrichment_history"][-1]
        entry["receipt"] = mask({k: v for k, v in record.items() if k not in ("id", "timestamp")})
        entry["shape"] = receipt_shape(record)
        return entry
    finally:
        await engine.dispose()


async def record() -> dict:
    root = _applire_root()
    head = _checkout_head(root)
    if head != PRE_EPIC_COMMIT:
        raise SystemExit(
            f"refusing to record: the imported applire package ({root}) is checked out at {head}, "
            f"not the pre-epic commit {PRE_EPIC_COMMIT}. Point PYTHONPATH at a worktree of that commit."
        )
    with tempfile.TemporaryDirectory() as tmp:
        cases = {c["id"]: await _record_one(c, Path(tmp)) for c in CASES}
    return {
        "story": "US293 — structured edits keep the write-seam contract (E055)",
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recorded_from": _provenance(root),
        "seed_sha256": seed_sha256(),
        "cases_sha256": cases_sha256(),
        "cases": cases,
    }


def main() -> None:
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("LLM_PROVIDER", "mock")
    fixture = asyncio.run(record())
    FIXTURE_PATH.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    accepted = sum(1 for c in fixture["cases"].values() if c["status"] == 200)
    refused = {cid: c["status"] for cid, c in fixture["cases"].items() if c["status"] != 200}
    print(f"recorded {len(fixture['cases'])} cases from {fixture['recorded_from']['commit'][:8]} → {FIXTURE_PATH}")
    print(f"  accepted: {accepted}   refused: {refused}")
    for cid, c in fixture["cases"].items():
        if c["status"] == 200:
            print(f"  {cid:32s} {len(c['shape']['changes'])} change(s): " + ", ".join(f"{x['action']}:{x['field']}" for x in c["shape"]["changes"]))


if __name__ == "__main__":
    main()
