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

"""#480's own probe sequence, as a regression fixture — BOTH halves.

The issue's reproduction, run on the #336 branch (`probe_compound_denial.py`):

```
denial: "hands-on Kubernetes operations"; JD concept: "Kubernetes"
before section-editor write:  ('denied', False)
after  unconfirmed skill:     ('direct', True)     ← half 1, closed by #501
after  edited bullet:         ('direct', True)     ← half 2, closed HERE
```

Half 2 is the vector step 1 could not reach: `_independently_affirmed`'s corpus
was the whole vault flattened *including* `work_experience[].responsibilities`,
so a sentence the candidate typed into the CV section editor functioned as the
independent affirmation that lifted their own recorded denial. The write-seam
re-floor cannot close it either — the release predicate is READ-side. ADR-059
amended 2026-08-09 (§7.5 option (a)) narrows the release corpus to attested
entity labels; that is what closes it.

The floored shape is `('gap', False)` rather than the probe's `('denied',
False)` because ADR-059's 2026-08-08 #486 amendment landed in between: a
containment-only match is **floored, never asserted** (`DENIAL_FLOOR_EVIDENCE`,
not `DENIED_EVIDENCE`) — the candidate never named bare "Kubernetes", so
writing testimony about it would be a fabrication. Non-claimable either way,
which is the property #480 is about.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from applire.models.profile import (
    MasterProfile,
    ProfileSnapshot,
    reset_unauthorized_profile_writes,
)
from applire.services.keyword_ledger import (
    DENIAL_FLOOR_EVIDENCE,
    build_keyword_ledger,
    reevaluate_gap_ledger_against_vault,
)
from applire.services.profile.commit import CommitProvenance, commit_ops
from applire.services.profile.reconcile.ops import ReplaceSection

DENIAL = "hands-on Kubernetes operations"
CONCEPT = "Kubernetes"

_BASE_VAULT = {
    "personal_info": {"full_name": "Daniel Kovač", "email": "daniel@example.invalid"},
    "work_experience": [
        {
            "id": "w1",
            "company": "Rheinwerk GmbH",
            "role": "Automation Engineer",
            "start_date": "2018-01",
            "end_date": "2023-12",
            "responsibilities": ["Owned the CI pipeline for two product teams."],
        }
    ],
    "skills": [{"name": "Ansible", "category": "technical", "status": "confirmed"}],
    "metadata": {
        "completeness_score": 0.0,
        "created_via": "cv_upload",
        "created_at": "2020-01-01T00:00:00Z",
        "last_updated": "2020-01-01T00:00:00Z",
        "denied_concepts": [
            {
                "concept": DENIAL,
                "statement": "I have never run Kubernetes hands-on in production.",
                "source": "interview",
                "date": "2026-08-01",
                "denial_level": "direct",
            }
        ],
    },
}


def _vault(*, skills=(), responsibilities=None) -> dict:
    """A copy of the base vault with the probe's mutations applied."""
    import copy

    out = copy.deepcopy(_BASE_VAULT)
    out["skills"].extend(skills)
    if responsibilities is not None:
        out["work_experience"][0]["responsibilities"] = list(responsibilities)
    return out


def _rebuild(profile_json: dict) -> tuple[str, bool]:
    """A full ledger rebuild — the classifier calls the concept `direct` and
    claimable, exactly as it did in the probe; only the deterministic floor
    stands between that and the delivered document."""
    ledger = build_keyword_ledger(
        [
            {
                "concept": CONCEPT,
                "status": "direct",
                "evidence": "Ran Kubernetes in production.",
                "surface_forms": [CONCEPT, "K8s"],
            }
        ],
        [CONCEPT],
        [],
        [],
        denied_concepts=(profile_json["metadata"]["denied_concepts"]),
        profile_json=profile_json,
    )
    row = next(e for e in ledger if e["concept"] == CONCEPT)
    return row["status"], bool(row["claimable"])


# ── The probe's starting point ───────────────────────────────────────────────


def test_before_any_write_the_narrow_denial_floors_the_broad_concept():
    assert _rebuild(_vault()) == ("gap", False)


# ── Half 1 — the unconfirmed skill (step 1's closure, at the narrowed corpus) ─


def test_an_unconfirmed_skill_does_not_release_the_denial():
    """Closed by #501 through `exclude_unconfirmed`; re-pinned here because the
    narrowed corpus reaches the same answer by construction — the shared
    `_UNCLAIMABLE_STATUSES` predicate subsumes step 1's wrap."""
    vault = _vault(
        skills=[{"name": "Kubernetes", "category": "technical", "status": "unconfirmed"}]
    )
    assert _rebuild(vault) == ("gap", False)


def test_a_denied_skill_does_not_release_the_denial_either():
    vault = _vault(
        skills=[{"name": "Kubernetes", "category": "technical", "status": "denied"}]
    )
    assert _rebuild(vault) == ("gap", False)


# ── Half 2 — the edited bullet. THE vector #480 stayed open on ───────────────


def test_a_bullet_naming_the_denied_concept_does_not_release_the_denial():
    vault = _vault(
        responsibilities=[
            "Owned the CI pipeline for two product teams.",
            "Ran the Kubernetes rollout for the platform group.",
        ]
    )
    assert _rebuild(vault) == ("gap", False), (
        "editor-typed prose is not an attested vault entity and may not lift "
        "the candidate's own recorded denial (ADR-059 amended 2026-08-09)"
    )


def test_the_vault_re_evaluation_thread_refuses_the_same_bullet():
    """The strip thread (`reevaluate_gap_ledger_against_vault`) is a second,
    independent path into the release predicate — the lockstep clause exists
    because a fix applied at one path and not another is the recurring defect."""
    vault = _vault(
        responsibilities=["Ran the Kubernetes rollout for the platform group."]
    )
    row = {
        "concept": CONCEPT,
        "surface_forms": [CONCEPT],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    }
    out, _changed = reevaluate_gap_ledger_against_vault([row], vault)
    assert (out[0]["status"], bool(out[0]["claimable"])) == ("gap", False)


@pytest.mark.asyncio
async def test_the_bullet_written_through_the_section_edit_path_does_not_release(
    db_session, seeded
):
    """End to end through the write path the probe used: a CV section edit
    (`ReplaceSection`, PR 3) commits the bullet, and the ledger is then rebuilt
    from the PERSISTED vault. The write is accepted — the system never edits or
    refuses the candidate's own sentence — and it simply does not release."""
    edited = list(seeded.profile_json["work_experience"])
    edited[0] = {
        **edited[0],
        "responsibilities": ["Ran the Kubernetes rollout for the platform group."],
    }
    await commit_ops(
        db_session,
        [ReplaceSection(section="work_experience", value=edited)],
        CommitProvenance(source="manual_edit", intake="field_edit", actor="candidate"),
    )
    await db_session.commit()

    persisted = seeded.profile_json
    assert "Kubernetes" in persisted["work_experience"][0]["responsibilities"][0], (
        "the candidate's own sentence is never edited or removed by the system"
    )
    assert _rebuild(persisted) == ("gap", False)


# ── The positive control — #207's over-blocking class stays closed ───────────


def test_a_genuinely_attested_skill_still_releases_the_narrow_denial():
    """The corpus narrows; the PREDICATE's semantics do not. A claimable,
    confirmed vault skill whose name independently affirms the broad concept
    releases it exactly as it always did — re-opening #207 (a narrow denial
    tarring a term the vault genuinely attests) would fail toward claiming
    less, but it would still be wrong."""
    vault = _vault(
        skills=[{"name": "Kubernetes", "category": "technical", "status": "confirmed"}]
    )
    assert _rebuild(vault) == ("direct", True)


def test_a_confirmed_certification_label_also_releases():
    vault = _vault()
    vault["certifications"] = [
        {"name": "Certified Kubernetes Administrator", "status": "confirmed"}
    ]
    assert _rebuild(vault) == ("direct", True)


def test_a_role_title_naming_the_concept_still_releases():
    """`work_experience[].role`/`company` are the corpus's weakest members —
    `WorkEntry` has no status field — and they stay in per the PO ruling:
    dropping them would over-floor genuine role-title affirmations."""
    vault = _vault()
    vault["work_experience"][0]["role"] = "Kubernetes Platform Engineer"
    assert _rebuild(vault) == ("direct", True)


def test_the_floored_row_is_floored_and_never_asserted_as_testimony():
    """The never-upgrade half is unchanged and stays read-side: bare
    "Kubernetes" is refused, but no denial is WRITTEN about a term the
    candidate never named (#486)."""
    ledger = build_keyword_ledger(
        [{"concept": CONCEPT, "status": "direct", "evidence": "x"}],
        [CONCEPT],
        [],
        [],
        denied_concepts=_BASE_VAULT["metadata"]["denied_concepts"],
        profile_json=_vault(),
    )
    row = next(e for e in ledger if e["concept"] == CONCEPT)
    assert row["evidence"] == DENIAL_FLOOR_EVIDENCE


# ── The coverage consumers at the two SPLIT sites keep the wide corpus ───────


def test_the_strip_threads_coverage_half_still_reads_the_whole_vault():
    """`reevaluate_gap_ledger_against_vault` builds ONE corpus that fed two
    predicates — the denial floor (release) and `surface_present` (coverage).
    Only the release half narrows: an un-denied concept named in a bullet must
    still heal its gap row, which is the whole point of that function."""
    vault = _vault(
        responsibilities=["Owned the Terraform estate for the platform group."]
    )
    row = {
        "concept": "Terraform",
        "surface_forms": ["Terraform"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "gap",
        "evidence": "",
        "claimable": False,
    }
    out, changed = reevaluate_gap_ledger_against_vault([row], vault)
    assert changed is True
    assert out[0]["claimable"] is True
    assert "Terraform" in out[0]["evidence"]


def test_the_heal_seams_evidence_index_still_reads_the_whole_vault():
    """`assert_claimable_backed` builds one filtered vault that fed the denial
    corpus AND `build_vault_index` (clause 5's affirmative floor). Only the
    denial corpus narrows: a claimable row grounded by a bullet must survive."""
    from applire.services.keyword_ledger import assert_claimable_backed

    vault = _vault(
        responsibilities=["Owned the Terraform estate for the platform group."]
    )
    row = {
        "concept": "Terraform",
        "surface_forms": ["Terraform"],
        "sources": ["required"],
        "fit_weight": 1.0,
        "status": "direct",
        "evidence": "Owned the Terraform estate for the platform group.",
        "claimable": True,
    }
    healed, violations = assert_claimable_backed([row], vault, seam="test")
    assert violations == []
    assert healed[0]["claimable"] is True


# ── Harness ──────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_session():
    from applire.db.session import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c, tables=[MasterProfile.__table__, ProfileSnapshot.__table__]
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db_session):
    from applire.models.profile import authorized_profile_write

    with authorized_profile_write():
        record = MasterProfile(profile_json=_vault())
    db_session.add(record)
    await db_session.commit()
    reset_unauthorized_profile_writes()
    return record
