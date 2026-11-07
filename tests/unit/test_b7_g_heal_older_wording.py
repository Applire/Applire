# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""783:245 (heal) — the OLDER evidence-silence chip wording (Hazelnut build 7, G).

Build 6's A-1 heal found persisted interview denials minted from a clicked
suggested-answer chip that said the PROFILE is silent ("zu X steht in meinem Profil
noch nichts"). The founder ruled on 2026-11-05 to extend it to the older family of
the same class — "kann ich bisher nicht belegen", "keine ausdrücklich belegte
Erfahrung mit X", "mein Profil dokumentiert dazu noch keine X" — which the
interview prompt also produced (114 of the 376 scanned chips in the build-6 audit).
Those words say the EVIDENCE is missing, not that the candidate lacks the item.

Pinned here: each wording is found (DE + EN, concepts before and after the marker);
the clause ends where the wording ends; a typed own "no" beside it, a later typed
denial, a first-person "ich habe keine Erfahrung mit X" and an unrelated sentence
are NOT offered; the 0088 migration's copy agrees with the app and parks only what
0086 did not already ask.

Synthetic data only (invented company and terms).
"""

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from applire.services.profile.reconcile import stance

_REPO = Path(__file__).resolve().parents[2]
_MIG_DIR = _REPO / "backend" / "alembic" / "versions"
_MIGRATION = _MIG_DIR / "0088_older_wording_denial_heal.py"
_MIGRATION_0086 = _MIG_DIR / "0086_offered_choice_denial_heal.py"

# (statement, the concepts the clause names)
OLD_WORDINGS = [
    # "kann ich (bisher) nicht belegen" — concepts BEFORE the marker
    (
        "Ich habe bei der Marlow Pharma die Schichtplanung eingeführt; eine direkte Steuerung der "
        "Chargenfreigabe kann ich bisher nicht belegen.",
        ["Chargenfreigabe"],
    ),
    (
        "Ich habe Kennzahlen aufbereitet; eine systematische Messung der Rüstzeit kann ich daraus "
        "jedoch nicht belegen.",
        ["Rüstzeit"],
    ),
    # "kann (aber) keine … belegen"
    (
        "Ich habe Abweichungen analysiert, kann aber keine direkte Erfahrung mit Make-or-buy-"
        "Entscheidungen belegen.",
        ["Make-or-buy-Entscheidungen"],
    ),
    # "keine ausdrücklich belegte / ausgewiesene Erfahrung mit …" — concepts AFTER the marker
    (
        "Ich kenne standardisierte Abläufe, aber keine ausdrücklich belegte Erfahrung mit GMP-Audits "
        "oder Reinraumqualifizierung.",
        ["GMP-Audits", "Reinraumqualifizierung"],
    ),
    (
        "Ich habe Qualitätsprozesse begleitet, jedoch keine ausdrücklich ausgewiesene Erfahrung mit "
        "TISAX oder VDA 6.3.",
        ["TISAX", "VDA 6.3"],
    ),
    # "ist noch nicht (konkret) belegt / habe ich bisher nicht beschrieben"
    (
        "Ich habe Budgets verantwortet, aber meine direkte Erfahrung mit Lagerbewertung ist noch "
        "nicht konkret belegt.",
        ["Lagerbewertung"],
    ),
    (
        "Ich habe SAP eingeführt; eine direkte Erfahrung mit Bedarfsprognose oder Demand Planning "
        "habe ich bisher nicht beschrieben.",
        ["Bedarfsprognose", "Demand Planning"],
    ),
    # "mein Profil dokumentiert …"
    (
        "Ich habe Dashboards gebaut; mein Profil dokumentiert dazu noch keine Details zu Tableau oder Qlik.",
        ["Tableau", "Qlik"],
    ),
    ("Ich habe Dashboards gebaut; mein Profil dokumentiert noch nichts zu Tableau.", ["Tableau"]),
    ("Ich habe Dashboards gebaut; mein Profil dokumentiert Tableau noch nicht ausdrücklich.", ["Tableau"]),
    ("Ich habe Dashboards gebaut; zu Tableau dokumentiert mein Profil noch nichts.", ["Tableau"]),
    (
        "Ich habe Dashboards gebaut; mein Profil dokumentiert dazu noch nicht, ob Tableau oder Qlik "
        "eingesetzt wurden.",
        ["Tableau", "Qlik"],
    ),
    ("Ich habe Dashboards gebaut, aber zu Tableau steht noch nichts darin.", ["Tableau"]),
    (
        "Ich habe Dashboards gebaut; zu Tableau und Qlik steht in meinem Profil noch kein konkretes "
        "Beispiel.",
        ["Tableau", "Qlik"],
    ),
    (
        "Ich habe Dashboards gebaut; mein Profil dokumentiert Tableau, Qlik und Looker noch nicht.",
        ["Tableau", "Qlik", "Looker"],
    ),
    ("Ich habe Dashboards gebaut, während mein Profil keine konkrete Tableau-Nutzung dokumentiert.", ["Tableau"]),
    # EN
    (
        "I operated production services, but my documented experience does not cover Nagios, "
        "Zabbix, or SLAs directly.",
        ["Nagios", "Zabbix", "SLAs"],
    ),
    (
        "I built REST APIs, but I have not yet documented the full RabbitMQ, retry-queue, or "
        "dead-letter details.",
        ["RabbitMQ"],
    ),
    ("I built billing backends, but it does not yet document MiFID requirements.", ["MiFID"]),
]


def _row(concept, statement, *, source="interview", date="2026-11-02"):
    return {
        "concept": concept, "statement": statement, "source": source, "date": date,
        "denial_level": "direct", "probe_asked": False,
    }


def _vault(rows, *, pending=None, answered=None):
    return {
        "personal_info": {"full_name": "Lena Marquardt", "email": "lena@example.invalid"},
        "work_experience": [],
        "skills": [],
        "metadata": {
            "completeness_score": 0.0, "created_via": "cv_upload",
            "created_at": "2020-01-01T00:00:00Z", "last_updated": "2020-01-01T00:00:00Z",
            "denied_concepts": copy.deepcopy(rows),
            "pending_confirmations": copy.deepcopy(pending or []),
            "denial_scope_answered": list(answered or []),
        },
    }


def _offered(statement, concepts, **kw):
    hits = stance.offered_choice_denial_hits(_vault([_row(c, statement, **kw) for c in concepts]))
    return [c for h in hits for c in h["denial_concepts"]]


@pytest.mark.parametrize(("statement", "concepts"), OLD_WORDINGS)
def test_the_older_wording_is_a_profile_silence_clause_and_offered(statement, concepts):
    assert stance.profile_silence_clauses(statement), statement
    assert _offered(statement, concepts) == concepts


@pytest.mark.parametrize(
    "statement",
    [
        # a genuine first-person denial is the candidate's own no
        "Ich habe keine Erfahrung mit Tableau.",
        "Mit Tableau habe ich nie gearbeitet.",
        "Ich habe bisher keine Führungserfahrung gesammelt, kann aber viel liefern.",
        "I have not used Tableau.",
        # "belegen" in an unrelated sense
        "Ich kann den Nutzen jederzeit belegen.",
        "Mein Profil dokumentiert Tableau ausführlich.",
    ],
)
def test_a_statement_that_genuinely_denies_the_item_is_not_offered(statement):
    assert stance.profile_silence_clauses(statement) == [], statement
    assert _offered(statement, ["Tableau"]) == []


def test_a_clause_that_ends_at_the_marker_does_not_swallow_a_typed_continuation():
    """"…Tableau kann ich bisher nicht belegen, und mit Qlik habe ich nie gearbeitet." — Qlik is
    the candidate's own no (adversarial H-3 shape)."""
    statement = (
        "Ich habe Dashboards gebaut; eine Anbindung an Tableau kann ich bisher nicht belegen, und mit "
        "Qlik habe ich nie gearbeitet."
    )
    assert _offered(statement, ["Tableau", "Qlik"]) == ["Tableau"]
    clause = stance.profile_silence_clauses(statement)[0]
    assert clause.endswith("nicht belegen") and "qlik" not in clause


def test_a_sentence_after_a_concepts_after_marker_is_not_part_of_the_clause():
    statement = (
        "Ich habe Dashboards gebaut; mein Profil dokumentiert dazu noch keine Details zu Tableau. "
        "Mit Qlik habe ich nie gearbeitet."
    )
    assert _offered(statement, ["Tableau", "Qlik"]) == ["Tableau"]


@pytest.mark.parametrize(("statement", "concepts"), OLD_WORDINGS[:5])
def test_a_concept_also_typed_beside_the_older_wording_is_the_candidates_own_no(statement, concepts):
    typed = statement + f" Und mit {concepts[0]} habe ich wirklich nie gearbeitet."
    assert concepts[0] not in _offered(typed, concepts)


def test_a_later_typed_denial_withdraws_the_offer_of_that_concept():
    statement, concepts = OLD_WORDINGS[3]
    vault = _vault([_row(c, statement) for c in concepts])
    vault["metadata"]["enrichment_history"] = [{"changes": [
        {"section": "metadata", "field": "denied_concepts", "action": "updated", "new_value": concepts[0]},
    ]}]
    (hit,) = stance.offered_choice_denial_hits(vault)
    assert hit["denial_concepts"] == concepts[1:]


def test_only_interview_source_denials_of_the_older_wording_are_offered():
    statement, concepts = OLD_WORDINGS[0]
    assert _offered(statement, concepts, source="agent_interview") == []


# ---------------------------------------------------------------------------
# The 0088 data migration
# ---------------------------------------------------------------------------


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _migration():
    return _load(_MIGRATION, "m0088")


def test_the_migrations_copy_agrees_with_the_app_on_every_older_wording():
    mod = _migration()
    statements = [s for s, _ in OLD_WORDINGS] + [
        "Ich habe keine Erfahrung mit Tableau.",
        "Ich habe Dashboards gebaut; eine Anbindung an Tableau kann ich bisher nicht belegen, und mit "
        "Qlik habe ich nie gearbeitet.",
        "Zu Boomi steht in meinem Profil noch nichts.",
        "my profile doesn't cover PSD2 yet.",
    ]
    concepts = sorted({c for _, cs in OLD_WORDINGS for c in cs} | {"Qlik", "Boomi", "PSD2"})
    rows = [_row(c, s, date=f"2026-11-0{i % 9 + 1}") for i, s in enumerate(statements) for c in concepts]
    vault = _vault(rows)
    for s in statements:
        assert mod.profile_silence_clauses(s) == stance.profile_silence_clauses(s), s
    app_hits, mig_hits = stance.offered_choice_denial_hits(vault), mod.offered_choice_denial_hits(vault)
    assert app_hits == mig_hits and len(app_hits) >= len(OLD_WORDINGS)
    for hit in app_hits:
        app = stance.offered_choice_denial_confirmation(hit).model_dump(mode="json")
        mig = mod.offered_choice_denial_confirmation(hit)
        app.pop("confirmation_id"), mig.pop("confirmation_id")
        assert mig == app


def test_the_migration_chains_onto_0087():
    mod = _migration()
    assert (mod.revision, mod.down_revision) == ("0088", "0087")


def test_the_migration_parks_only_what_0086_did_not_ask_on_a_real_database():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    mod, m0086 = _migration(), _load(_MIGRATION_0086, "m0086")
    old_statement, old_concepts = OLD_WORDINGS[3]
    new_statement = "Ich habe LIMS betreut, aber zu Boomi und Kafka steht in meinem Profil noch nichts."
    rows = [_row(c, old_statement) for c in old_concepts] + [_row(c, new_statement) for c in ("Boomi", "Kafka")]
    typed = [_row("Tableau", "Ich habe Dashboards gebaut. Mit Tableau habe ich nie gearbeitet.")]

    engine = sa.create_engine("sqlite://")
    meta = sa.MetaData()
    table = sa.Table(
        "master_profiles", meta,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("profile_json", sa.JSON, nullable=False),
        sa.Column("deleted_at", sa.DateTime, nullable=True),
    )
    meta.create_all(engine)
    old_key = "offered_choice|" + stance.offered_choice_denial_hits(_vault(rows))[0]["statement_key"]
    assert old_key.startswith("offered_choice|")
    from datetime import datetime

    def run(step, mod_):
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                step()
        with engine.connect() as conn:
            return {
                r.id: (json.loads(r.profile_json) if isinstance(r.profile_json, str) else r.profile_json)
                for r in conn.execute(sa.select(table))
            }

    with engine.begin() as conn:
        conn.execute(table.insert(), [
            {"id": "mixed", "profile_json": _vault(rows), "deleted_at": None},
            {"id": "typed", "profile_json": _vault(typed), "deleted_at": None},
            {"id": "answered", "profile_json": _vault(rows, answered=[old_key]), "deleted_at": None},
            {"id": "deleted", "profile_json": _vault(rows), "deleted_at": datetime(2026, 11, 1)},
        ])

    def items(vault):
        return [
            c for c in vault["metadata"]["pending_confirmations"]
            if c.get("source") == "denial_scope" and c["context"].get("conflict_kind") == "offered_choice"
        ]

    # 0086 first: it asks the NEW wording only; 0088 then adds the OLD wording, and only that
    after86 = run(m0086.upgrade, m0086)
    assert [i["context"]["denial_concepts"] for i in items(after86["mixed"])] == [["Boomi", "Kafka"]]
    after88 = run(mod.upgrade, mod)
    got = sorted(i["context"]["denial_concepts"] for i in items(after88["mixed"]))
    assert got == sorted([["Boomi", "Kafka"], old_concepts])
    assert after88["mixed"]["metadata"]["denied_concepts"] == _vault(rows)["metadata"]["denied_concepts"], (
        "nothing is withdrawn at upgrade"
    )
    assert items(after88["typed"]) == [], "a typed denial is never offered"
    assert items(after88["deleted"]) == []
    assert [i["context"]["denial_concepts"] for i in items(after88["answered"])] == [["Boomi", "Kafka"]], (
        "a statement already answered (denial_scope_answered) is never asked again"
    )

    again = run(mod.upgrade, mod)
    assert len(items(again["mixed"])) == 2, "idempotent"

    from applire.schemas.profile import MasterProfileData

    MasterProfileData.model_validate(again["mixed"])

    # downgrade is a no-op: 0086's questions (same family) must survive
    down = run(mod.downgrade, mod)
    assert len(items(down["mixed"])) == 2
