# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adversarial pass, Hazelnut build 7 — 783:245 heal (stance markers + 0088).

The older-wording markers are matched on ANY interview denial statement, not only
on a clicked chip (the chip was never persisted). A candidate's own TYPED denial
that happens to contain one of the new evidence-silence phrases is offered for
withdrawal with a question that tells them "you picked a suggested answer that
only said your profile does not mention X yet" — which is false, and its first
option ("Ja, zurücknehmen") removes a real no. Synthetic data.
"""
from __future__ import annotations

import pytest

from applire.services.profile.reconcile.stance import offered_choice_denial_hits


def _profile(concept: str, statement: str) -> dict:
    return {
        "metadata": {
            "denied_concepts": [
                {"concept": concept, "statement": statement, "source": "interview", "date": "2026-11-01"}
            ],
            "enrichment_history": [],
        }
    }


@pytest.mark.parametrize(
    "concept,statement",
    [
        # typed, genuine no — then explains why nothing is documented
        ("SAP", "Mit SAP habe ich nie gearbeitet, entsprechend ist das bisher nicht dokumentiert."),
        # "concepts after the marker" arm runs to the sentence end, past the own no
        ("SAP", "Ich habe keine nachgewiesene Erfahrung mit SAP und habe nie mit SAP gearbeitet."),
    ],
)
def test_a_typed_genuine_denial_is_not_offered_as_a_chip_for_withdrawal(concept, statement):
    assert offered_choice_denial_hits(_profile(concept, statement)) == []


# ── A-6 extensions: negative arms, migration parity, real database ─────────────────────
import importlib.util
import json
import pathlib

import sqlalchemy as sa

from applire.services.profile.reconcile import stance

_TYPED = [
    ("SAP", "Mit SAP habe ich nie gearbeitet, entsprechend ist das bisher nicht dokumentiert."),
    ("SAP", "Ich habe keine nachgewiesene Erfahrung mit SAP und habe nie mit SAP gearbeitet."),
    ("SAP", "I have no experience with SAP, so my profile does not mention it yet."),
]
_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2] / "backend/alembic/versions/0088_older_wording_denial_heal.py"
)


def _mig():
    spec = importlib.util.spec_from_file_location("m0088_adv", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_third_typed_shape_english_is_not_offered_either():
    assert offered_choice_denial_hits(_profile(*_TYPED[2])) == []


@pytest.mark.parametrize(
    "concept,statement,expected",
    [
        # the chip's own words stay offered, including "keine Erfahrung" under the PROFILE as subject
        ("Qlik", "Ich habe Dashboards gebaut; mein Profil dokumentiert bisher jedoch keine Erfahrung mit Qlik oder Looker.", ["Qlik"]),
        ("Tableau", "Eine Anbindung an Tableau kann ich bisher nicht belegen, und mit Qlik habe ich nie gearbeitet.", ["Tableau"]),
        ("Boomi", "Ich habe LIMS betreut, aber zu Boomi steht in meinem Profil noch nichts.", ["Boomi"]),
        ("PSD2", "I built billing backends, but my documented experience does not cover PSD2.", ["PSD2"]),
    ],
)
def test_a_chip_wording_is_still_offered_negative_arm(concept, statement, expected):
    got = offered_choice_denial_hits(_profile(concept, statement))
    assert [c for h in got for c in h["denial_concepts"]] == expected


def test_a_typed_no_to_another_concept_does_not_shield_the_chip_concept():
    statement = "Eine Anbindung an Tableau kann ich bisher nicht belegen, und mit Qlik habe ich nie gearbeitet."
    assert offered_choice_denial_hits(_profile("Qlik", statement)) == []


@pytest.mark.parametrize("concept,statement", _TYPED)
def test_the_migrations_copy_agrees_on_the_typed_shapes(concept, statement):
    mod = _mig()
    assert mod.offered_choice_denial_hits(_profile(concept, statement)) == []
    chip = ("Boomi", "Ich habe LIMS betreut, aber zu Boomi steht in meinem Profil noch nichts.")
    assert mod.offered_choice_denial_hits(_profile(*chip)) == offered_choice_denial_hits(_profile(*chip)) != []


def test_the_migration_parks_nothing_for_the_typed_shapes_and_stays_idempotent():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod = _mig()
    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    table = sa.Table(
        "master_profiles", meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("profile_json", sa.JSON, nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    meta.create_all(engine)

    def vault(rows):
        p = _profile("x", "y")
        p["metadata"]["denied_concepts"] = [
            {"concept": c, "statement": s, "source": "interview", "date": "2026-11-01"} for c, s in rows
        ]
        p["metadata"]["pending_confirmations"] = []
        return p

    with engine.begin() as conn:
        conn.execute(table.insert(), [
            {"id": "typed", "profile_json": vault(_TYPED), "deleted_at": None},
            {"id": "chip", "profile_json": vault([(
                "Boomi", "Ich habe LIMS betreut, aber zu Boomi steht in meinem Profil noch nichts.")]), "deleted_at": None},
        ])

    def run():
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                mod.upgrade()
        with engine.connect() as conn:
            return {
                r.id: (json.loads(r.profile_json) if isinstance(r.profile_json, str) else r.profile_json)
                for r in conn.execute(sa.select(table))
            }

    for _ in range(2):  # idempotent
        got = run()
        assert got["typed"]["metadata"]["pending_confirmations"] == []
        assert len(got["chip"]["metadata"]["pending_confirmations"]) == 1
