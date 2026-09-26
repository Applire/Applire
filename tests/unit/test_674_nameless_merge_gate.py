# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#674 line (b) — a NAMELESS extraction must not merge a stranger's history
silently (ADR-041 amended 2026-09-26; founder ruling V-1, option A).

`names_clearly_differ` needs BOTH names, so an extraction that attributes no
name at all — footer-only contact block, an OCR-lost header — sailed through as
`gate='none'` against a populated, divergent vault. Since #367 that predicate
runs on all three CV-ingestion doors.

The rule, and why it is this rule:
  * no extracted name  AND  the vault has >= 1 employer  AND  the extraction has
    >= 1 employer  AND  NO employer in common (under `merge.company_names_match`,
    the merge's own identity rule — ADR-066)  →  the EXISTING `name_divergence`
    hold, `cv_name=None` (no new gate value: a client that only knows the two
    existing values would park the upload silently);
  * one employer in common → plain merge (ADR-037: a false "different person"
    re-adds friction);
  * nothing to compare (employer-less extraction, employer-less vault, first
    import) → plain merge.

Mutation contract (each named test must go red):
  * delete the `nameless_history_diverges` branch in `evaluate_merge_gate`
      → test_674_repro_*, test_nameless_foreign_history_is_held_on_every_door
  * drop the `if (extracted.personal_info.name ...).strip(): return False` guard
      → test_a_named_extraction_is_left_to_the_name_check
  * invert the overlap (`any(...)` instead of `not any(...)`)
      → test_674_repro_*, test_one_shared_employer_keeps_the_plain_merge
  * drop the `if not incoming or not held` guard
      → test_nothing_to_compare_keeps_the_plain_merge (both params)
  * stop passing the vault from `ingest_cv`
      → test_nameless_foreign_history_is_held_on_every_door (all three doors)
"""
import pytest
from sqlalchemy import select

from applire.schemas.profile import MasterProfileData
from applire.services.profile.merge_gate import (
    evaluate_merge_gate,
    nameless_history_diverges,
)
from .test_367_one_ingest_all_doors import (  # noqa: F401 — fixtures
    DOORS,
    _profile_count,
    sqlite_session,
    storage,
)


def _profile(name: str, companies: list[str], *, skills: bool = True) -> MasterProfileData:
    data: dict = {
        "personal_info": {"name": name},
        "work_experience": [
            {"company": c, "role": "Engineer", "start_date": f"20{10 + i}-01"}
            for i, c in enumerate(companies)
        ],
    }
    if skills:
        data["skills"] = [{"name": "Python", "category": "technical"}]
    return MasterProfileData.model_validate(data)


MARCUS_VAULT = _profile("Marcus Schmidt", ["Rasselstein Umformtechnik GmbH", "Kunststoffwerk Nord AG"])


# ── the predicate ────────────────────────────────────────────────────────────


def test_674_repro_nameless_full_foreign_history_is_held():
    """The collector line's own repro: `evaluate_merge_gate("Marcus Schmidt",
    <name="" + full history>)` returned `none`. It must hold."""
    foreign = _profile("", ["Bosch Rexroth AG", "Siemens Healthineers"])

    result = evaluate_merge_gate("Marcus Schmidt", foreign, MARCUS_VAULT)

    assert result.gate == "name_divergence"
    assert result.cv_name is None
    assert result.account_name == "Marcus Schmidt"


def test_674_repro_whitespace_name_counts_as_nameless():
    foreign = _profile("   ", ["Bosch Rexroth AG"])
    assert evaluate_merge_gate("Marcus Schmidt", foreign, MARCUS_VAULT).gate == "name_divergence"


def test_one_shared_employer_keeps_the_plain_merge():
    """The same person's CV without a readable name merges: one employer in
    common, under the merge's own subset rule ('Rasselstein' ⊆ the full name)."""
    own = _profile("", ["Rasselstein", "Neue Firma GmbH"])

    assert nameless_history_diverges(own, MARCUS_VAULT) is False
    assert evaluate_merge_gate("Marcus Schmidt", own, MARCUS_VAULT).gate == "none"


def test_a_named_extraction_is_left_to_the_name_check():
    """A name that shares a token with the account is the name check's call —
    the history branch must not second-guess it (no new friction for a CV whose
    name matches but whose employers are all new, e.g. a career changer's
    fresh CV)."""
    same_person_new_jobs = _profile("Marcus Schmidt", ["Bosch Rexroth AG"])

    assert nameless_history_diverges(same_person_new_jobs, MARCUS_VAULT) is False
    assert evaluate_merge_gate(
        "Marcus Schmidt", same_person_new_jobs, MARCUS_VAULT
    ).gate == "none"


@pytest.mark.parametrize(
    "extracted,vault",
    [
        pytest.param(
            MasterProfileData.model_validate({
                "personal_info": {"name": ""},
                "education": [{"institution": "TU Berlin", "degree": "B.Sc."}],
            }),
            MARCUS_VAULT,
            id="extraction_has_no_employer",
        ),
        pytest.param(
            _profile("", ["Bosch Rexroth AG"]),
            _profile("Marcus Schmidt", []),
            id="vault_has_no_employer",
        ),
    ],
)
def test_nothing_to_compare_keeps_the_plain_merge(extracted, vault):
    assert nameless_history_diverges(extracted, vault) is False
    assert evaluate_merge_gate("Marcus Schmidt", extracted, vault).gate == "none"


def test_first_import_has_no_vault_and_merges():
    assert evaluate_merge_gate(None, _profile("", ["Bosch Rexroth AG"]), None).gate == "none"


def test_not_a_cv_still_takes_precedence():
    empty = MasterProfileData.model_validate({"personal_info": {"name": ""}})
    assert evaluate_merge_gate("Marcus Schmidt", empty, MARCUS_VAULT).gate == "not_a_cv"


def test_the_two_name_divergence_is_unchanged():
    anna = _profile("Anna Bauer", ["Rasselstein Umformtechnik GmbH"])
    result = evaluate_merge_gate("Marcus Schmidt", anna, MARCUS_VAULT)
    assert result.gate == "name_divergence"
    assert result.cv_name == "Anna Bauer"


# ── the seam: one per door, through the real ingest ──────────────────────────


def _nameless_foreign_extraction() -> dict:
    return {
        "personal_info": {"name": ""},
        "work_experience": [
            {"company": "Bosch Rexroth AG", "role": "Engineer", "start_date": "2019-01"},
            {"company": "Siemens Healthineers", "role": "Lead", "start_date": "2021-01"},
        ],
        "skills": [{"name": "Python", "category": "technical"}],
    }


async def _seed_marcus(door_session, storage_):
    from .test_367_one_ingest_all_doors import _door_browser

    seed = {
        "personal_info": {"name": "Marcus Schmidt"},
        "work_experience": [
            {"company": "Rasselstein Umformtechnik GmbH", "role": "Meister", "start_date": "2011-08"}
        ],
        "skills": [{"name": "SAP PP", "category": "technical"}],
    }
    result = await _door_browser(door_session, storage_, seed)
    assert result.status != "GATED"


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.asyncio
async def test_nameless_foreign_history_is_held_on_every_door(door, sqlite_session, storage):
    """Browser upload, MCP `import_cv`, LinkedIn import — the nameless foreign
    CV is parked, the vault stays byte-identical, and the parked row carries the
    gate the resolve endpoint / `held_merges` / Health hub already read."""
    from applire.models.profile import MasterProfile
    from applire.models.uploads import UploadRecord

    await _seed_marcus(sqlite_session, storage)
    before = (await sqlite_session.execute(select(MasterProfile))).scalars().first().profile_json

    result = await door(sqlite_session, storage, _nameless_foreign_extraction())

    assert result.status == "GATED", f"{door.__name__} merged a nameless foreign CV"
    assert result.gate == "name_divergence"
    assert result.cv_name is None
    assert result.account_name == "Marcus Schmidt"
    after = (await sqlite_session.execute(select(MasterProfile))).scalars().first().profile_json
    assert after == before, "a held merge must leave the vault byte-identical"
    assert await _profile_count(sqlite_session) == 1

    rec = (
        await sqlite_session.execute(select(UploadRecord).where(UploadRecord.id == result.staged_id))
    ).scalar_one()
    assert rec.gate_status == "name_divergence"
    assert rec.staged_extraction["personal_info"]["name"] == ""


@pytest.mark.parametrize("door", DOORS)
@pytest.mark.asyncio
async def test_nameless_own_history_still_merges_on_every_door(door, sqlite_session, storage):
    """No friction added for the candidate's own CV without a readable name."""
    await _seed_marcus(sqlite_session, storage)
    own = _nameless_foreign_extraction()
    own["work_experience"].append(
        {"company": "Rasselstein Umformtechnik GmbH", "role": "Meister", "start_date": "2011-08"}
    )

    result = await door(sqlite_session, storage, own)

    assert getattr(result, "status", None) != "GATED"
    assert getattr(result, "gate", "none") == "none"
