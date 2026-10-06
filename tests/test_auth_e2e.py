# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real-auth lane — two people on one instance, through nginx (ADR-091, ADR-092).

Runs ONLY in the real-auth lane (``APPLIRE_E2E_AUTH=1``) against
``docker-compose.ci-auth.yml``: production topology, login on, nginx on a non-80
port, mock LLM (0 provider calls). CI: job ``real-auth-lane`` in test.yml.

Local run (own project name and port, never the dev stack's)::

    export APPLIRE_CI_AUTH_PORT=18795
    C="docker compose -p strawberry-w4-5b-auth -f docker-compose.yml -f docker-compose.ci-auth.yml"
    $C up -d --build
    APPLIRE_E2E_AUTH=1 APPLIRE_API_BASE=http://localhost:18795 \\
      APPLIRE_E2E_COMPOSE_ARGS="-p strawberry-w4-5b-auth -f docker-compose.yml -f docker-compose.ci-auth.yml" \\
      pytest tests/test_auth_e2e.py tests/integration/test_auth_races.py -v
    $C down -v

Every assertion message names the person and the door (REST / REST+bearer /
MCP stdio) so a red line says who saw what through which door.

The journey: claim (raced, see test_auth_races.py) → the admin invites Ada and
Ben → both import a CV and analyse the SAME posting (one shared cache row) →
each sees only their own; foreign ids are 404 / ``not_found`` on every door;
token revoke; disable; self-delete with reference-counted posting erasure.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

_LANE_PATH = Path(__file__).resolve().parent / "e2e-auth" / "lane.py"
_spec = importlib.util.spec_from_file_location("applire_e2e_auth_lane", _LANE_PATH)
if "applire_e2e_auth_lane" in sys.modules:
    lane = sys.modules["applire_e2e_auth_lane"]
else:
    lane = importlib.util.module_from_spec(_spec)
    sys.modules["applire_e2e_auth_lane"] = lane
    _spec.loader.exec_module(lane)

if not lane.REAL_AUTH_LANE:
    pytest.skip(
        "real-auth lane only (APPLIRE_E2E_AUTH=1 against docker-compose.ci-auth.yml)",
        allow_module_level=True,
    )

MISSING = str(uuid.UUID(int=0xE2E))  # an id nobody owns


# ---------------------------------------------------------------------------
# The two-person world (module scope: built once, read by every test)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def admin():
    return lane.require_admin()


@pytest.fixture(scope="module")
def world(admin):
    rid = lane.run_id()
    ada = lane.new_user(admin, "Ada", rid)
    ben = lane.new_user(admin, "Ben", rid)
    w = {"rid": rid, "ada": ada, "ben": ben}
    for p in (ada, ben):
        w[f"{p.label}_import"] = lane.import_cv(p)
    shared = lane.posting_text(rid, "Shared Platform Engineer")
    w["Ada_job"] = lane.analyze(ada, shared)
    w["Ben_job"] = lane.analyze(ben, shared)
    # A posting only Ada ever analysed (Ben has no link to it).
    w["Ada_only_job"] = lane.analyze(ada, lane.posting_text(rid, "Ada-only Data Engineer"))
    for p in (ada, ben):
        apps = lane.applications(p)
        mine = [a for a in apps if str(a.get("job_analysis_id")) == w[f"{p.label}_job"]["id"]]
        assert len(mine) == 1, f"{p.label} REST GET /api/applications: {len(mine)} rows for the shared posting"
        w[f"{p.label}_app"] = mine[0]
        r = p.post("/api/flow", json={"job_id": w[f"{p.label}_job"]["id"]})
        assert r.status_code == 201, f"{p.label} REST POST /api/flow -> {r.status_code} {r.text[:200]}"
        w[f"{p.label}_flow"] = r.json()["flow_id"]
    return w


def _missing_body(p, path_template: str, method: str = "get", **kw) -> tuple[int, object]:
    r = getattr(p, method)(path_template.format(id=MISSING), **kw)
    return r.status_code, _body(r)


def _body(r):
    try:
        return r.json()
    except ValueError:
        return r.text


# ---------------------------------------------------------------------------
# Claim and invite
# ---------------------------------------------------------------------------


def test_the_claim_signed_in_an_admin_through_nginx(admin):
    c = lane.claim()
    assert admin.role == "admin", f"admin REST /api/auth/me role={admin.role!r} after the claim"
    state = lane.browser().get(f"{lane.API_BASE}/api/auth/state", timeout=30).json()
    assert state["setup_required"] is False, f"anonymous REST /api/auth/state still setup_required after the claim: {state}"
    assert state["harness"] is False, f"anonymous REST /api/auth/state harness on in the real-auth lane: {state}"
    if c.raced:
        assert c.statuses.count(204) == 1, f"{lane.RACERS} racers REST POST /api/setup: statuses {c.statuses}"


def test_invited_people_are_plain_users_and_admin_routes_refuse_them(world):
    for p in (world["ada"], world["ben"]):
        assert p.role == "user", f"{p.label} REST /api/auth/me role={p.role!r} (invited as user)"
        r = p.get("/api/admin/users")
        assert r.status_code == 403, f"{p.label} REST GET /api/admin/users -> {r.status_code} (want 403)"
        assert lane.error_code(r) == "forbidden", f"{p.label} REST GET /api/admin/users code {lane.error_code(r)!r}"


def test_anonymous_requests_are_401_unauthenticated():
    s = lane.browser()
    for path in ("/api/applications", "/api/profile", "/api/me/tokens", "/api/admin/users"):
        r = s.get(f"{lane.API_BASE}{path}", timeout=30)
        assert r.status_code == 401, f"anonymous REST GET {path} -> {r.status_code}"
        assert lane.error_code(r) == "unauthenticated", f"anonymous REST GET {path} code {lane.error_code(r)!r}"


# ---------------------------------------------------------------------------
# One shared posting, two private worlds
# ---------------------------------------------------------------------------


def test_the_same_posting_is_one_shared_cache_row(world):
    a, b = world["Ada_job"]["id"], world["Ben_job"]["id"]
    assert a == b, f"Ada and Ben REST POST /api/job/analyze of the same text gave two postings {a} / {b}"
    n = lane.psql(f"SELECT count(*) FROM job_analyses WHERE id = '{a}'")
    assert n == "1", f"database: {n} job_analyses rows for the shared posting {a}"


def test_each_person_has_their_own_application_for_the_shared_posting(world):
    a_app, b_app = world["Ada_app"]["id"], world["Ben_app"]["id"]
    assert a_app != b_app, f"Ada and Ben REST share one application row {a_app}"
    for p, mine, theirs in ((world["ada"], a_app, b_app), (world["ben"], b_app, a_app)):
        ids = {a["id"] for a in lane.applications(p)}
        assert mine in ids, f"{p.label} REST GET /api/applications misses their own {mine}"
        assert theirs not in ids, f"{p.label} REST GET /api/applications lists the other person's {theirs}"


def test_each_person_sees_only_their_own_profile_and_uploads(world):
    ada, ben = world["ada"], world["ben"]
    pa, pb = ada.get("/api/profile"), ben.get("/api/profile")
    assert pa.status_code == 200, f"Ada REST GET /api/profile -> {pa.status_code}"
    assert pb.status_code == 200, f"Ben REST GET /api/profile -> {pb.status_code}"
    assert pa.json()["id"] != pb.json()["id"], f"Ada and Ben REST GET /api/profile return one profile {pa.json()['id']}"
    ua = {u["id"] for u in ada.get("/api/profile/uploads").json()}
    ub = {u["id"] for u in ben.get("/api/profile/uploads").json()}
    assert ua and ub, f"Ada/Ben REST GET /api/profile/uploads empty after an import: {ua} / {ub}"
    assert not ua & ub, f"Ada and Ben REST GET /api/profile/uploads overlap: {ua & ub}"


def test_the_posting_labels_are_per_person(world):
    """Ben renames his application; Ada's view of the shared posting keeps hers (ADR-092 cl. 5f)."""
    ada, ben = world["ada"], world["ben"]
    job = world["Ada_job"]["id"]
    before = ada.get(f"/api/job/{job}").json()["company_name"]
    r = ben.patch(f"/api/applications/{world['Ben_app']['id']}", json={"company_name": f"Ben Co {world['rid']}"})
    assert r.status_code == 200, f"Ben REST PATCH own application -> {r.status_code} {r.text[:200]}"
    assert ben.get(f"/api/job/{job}").json()["company_name"] == f"Ben Co {world['rid']}", \
        "Ben REST GET /api/job: his own label did not apply"
    assert ada.get(f"/api/job/{job}").json()["company_name"] == before, \
        "Ada REST GET /api/job shows Ben's label on the shared posting"


# ---------------------------------------------------------------------------
# Foreign ids: 404 exactly like a missing id — REST, REST+bearer, MCP
# ---------------------------------------------------------------------------

_FOREIGN_GETS = [
    ("/api/applications/{id}", "Ada_app"),
    ("/api/flow/{id}/state", "Ada_flow"),
    ("/api/job/{id}", "Ada_only_job"),
    ("/api/profile/import-jobs/{id}", "Ada_import"),
]


def _id_of(world, key):
    v = world[key]
    if isinstance(v, dict):
        return str(v.get("id") or v.get("import_id"))
    return str(v)


@pytest.mark.parametrize("template,key", _FOREIGN_GETS)
def test_ben_gets_404_for_adas_ids_on_rest(world, template, key):
    ben = world["ben"]
    r = ben.get(template.format(id=_id_of(world, key)))
    missing_status, missing_body = _missing_body(ben, template)
    assert r.status_code == 404, f"Ben REST GET {template} with Ada's id -> {r.status_code} {r.text[:200]}"
    # The id the caller sent may be echoed (it is theirs already); masked out.
    assert (r.status_code, _mask(str(_body(r)))) == (missing_status, _mask(str(missing_body))), (
        f"Ben REST GET {template}: Ada's id answers {_body(r)!r}, a missing id answers {missing_body!r}"
    )
    own = world["ada"].get(template.format(id=_id_of(world, key)))
    assert own.status_code == 200, f"Ada REST GET {template} her own id -> {own.status_code}"


def test_ben_cannot_change_or_delete_adas_application_on_rest(world):
    ada, ben = world["ada"], world["ben"]
    app = world["Ada_app"]["id"]
    r = ben.patch(f"/api/applications/{app}", json={"notes": "Ben was here"})
    assert r.status_code == 404, f"Ben REST PATCH Ada's application -> {r.status_code}"
    r = ben.delete(f"/api/applications/{app}")
    assert r.status_code == 404, f"Ben REST DELETE Ada's application -> {r.status_code}"
    mine = ada.get(f"/api/applications/{app}")
    assert mine.status_code == 200, f"Ada REST GET her application after Ben's attempts -> {mine.status_code}"
    assert mine.json().get("notes") != "Ben was here", "Ada REST GET: Ben's PATCH reached her application"


def test_an_api_token_acts_for_its_owner_only(world):
    ada = world["ada"]
    tok = lane.make_token(ada, "api")
    s = lane.bearer(tok["token"])
    r = s.get(f"{lane.API_BASE}/api/applications", timeout=30)
    assert r.status_code == 200, f"Ada REST+api-bearer GET /api/applications -> {r.status_code}"
    ids = {a["id"] for a in r.json()["items"]}
    assert world["Ada_app"]["id"] in ids, "Ada REST+api-bearer misses her own application"
    assert world["Ben_app"]["id"] not in ids, "Ada REST+api-bearer lists Ben's application"
    r = s.get(f"{lane.API_BASE}/api/applications/{world['Ben_app']['id']}", timeout=30)
    assert r.status_code == 404, f"Ada REST+api-bearer GET Ben's application -> {r.status_code}"
    r = s.get(f"{lane.API_BASE}/api/me/tokens", timeout=30)
    assert r.status_code == 403, f"Ada REST+api-bearer GET /api/me/tokens (session-only) -> {r.status_code}"
    # A valid api bearer is CSRF-exempt: a script sends no Origin.
    r = s.patch(f"{lane.API_BASE}/api/applications/{world['Ada_app']['id']}", json={"notes": "via api token"}, timeout=30)
    assert r.status_code == 200, f"Ada REST+api-bearer PATCH own application (no Origin) -> {r.status_code} {r.text[:200]}"


def test_an_agent_token_is_refused_over_http(world):
    ada = world["ada"]
    tok = lane.make_token(ada, "agent")
    r = lane.bearer(tok["token"]).get(f"{lane.API_BASE}/api/applications", timeout=30)
    assert r.status_code == 401, f"Ada REST+agent-bearer GET /api/applications -> {r.status_code} (agent tokens are stdio-only)"


def test_mcp_agent_door_sees_only_its_owner(world):
    ada, ben = world["ada"], world["ben"]
    tok = lane.make_token(ada, "agent")["token"]
    run = lane.mcp(tok, [
        ("list_applications", {}),
        ("get_application", {"application_id": world["Ada_app"]["id"]}),
        ("get_application", {"application_id": world["Ben_app"]["id"]}),
        ("get_application", {"application_id": MISSING}),
        ("get_flow_state", {"flow_id": world["Ben_flow"]}),
        ("get_flow_state", {"flow_id": MISSING}),
        ("get_flow_state", {"flow_id": world["Ada_flow"]}),
    ])
    assert run.returncode == 0, f"Ada MCP stdio exited {run.returncode}: {run.stderr[-400:]}"
    kind, listed = lane.mcp_outcome(run.responses.get(1))
    assert kind == "ok", f"Ada MCP list_applications -> {kind} {listed!r}"
    items = listed.get("items", listed.get("result", listed)) if isinstance(listed, dict) else listed
    ids = {a["id"] for a in items}
    assert world["Ada_app"]["id"] in ids, f"Ada MCP list_applications misses her own: {ids}"
    assert world["Ben_app"]["id"] not in ids, "Ada MCP list_applications lists Ben's application"
    kind, own = lane.mcp_outcome(run.responses.get(2))
    assert kind == "ok", f"Ada MCP get_application(her own) -> {kind} {own!r}"
    foreign, missing = lane.mcp_outcome(run.responses.get(3)), lane.mcp_outcome(run.responses.get(4))
    assert foreign[0] != "ok", f"Ada MCP get_application(Ben's id) returned data: {foreign[1]!r}"
    assert "application not found" in str(foreign[1]), f"Ada MCP get_application(Ben's id) -> {foreign!r}"
    assert _not_found_shape(foreign) == _not_found_shape(missing), (
        f"Ada MCP get_application: Ben's id answers {foreign!r}, a missing id {missing!r}"
    )
    flow_foreign, flow_missing = lane.mcp_outcome(run.responses.get(5)), lane.mcp_outcome(run.responses.get(6))
    assert flow_foreign[0] != "ok", f"Ada MCP get_flow_state(Ben's flow) returned data: {flow_foreign[1]!r}"
    assert "flow not found" in str(flow_foreign[1]), f"Ada MCP get_flow_state(Ben's flow) -> {flow_foreign!r}"
    own_flow = lane.mcp_outcome(run.responses.get(7))
    assert own_flow[0] == "ok", f"Ada MCP get_flow_state(her own flow) -> {own_flow!r}"
    assert _not_found_shape(flow_foreign) == _not_found_shape(flow_missing), (
        f"Ada MCP get_flow_state: Ben's flow answers {flow_foreign!r}, a missing one {flow_missing!r}"
    )
    # And the mirror: Ben's agent cannot read Ada's application.
    btok = lane.make_token(ben, "agent")["token"]
    brun = lane.mcp(btok, [("get_application", {"application_id": world["Ada_app"]["id"]})])
    b = lane.mcp_outcome(brun.responses.get(1))
    assert b[0] != "ok", f"Ben MCP get_application(Ada's id) returned data: {b[1]!r}"
    assert "application not found" in str(b[1]), f"Ben MCP get_application(Ada's id) -> {b!r}"


def _not_found_shape(outcome):
    """Compare foreign vs missing with the id itself masked out."""
    kind, payload = outcome
    if kind == "error":
        code, msg = payload
        return kind, code, _mask(msg)
    return kind, _mask(str(payload))


def _mask(text: str) -> str:
    import re

    return re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<id>", text)


# ---------------------------------------------------------------------------
# Token revoke
# ---------------------------------------------------------------------------


def test_revoking_an_api_token_ends_it(world):
    ada = world["ada"]
    tok = lane.make_token(ada, "api", "revoke-me")
    s = lane.bearer(tok["token"])
    assert s.get(f"{lane.API_BASE}/api/applications", timeout=30).status_code == 200, \
        "Ada REST+api-bearer before revoke"
    r = world["ben"].delete(f"/api/me/tokens/{tok['id']}")
    assert r.status_code == 404, f"Ben REST DELETE Ada's token id -> {r.status_code} (foreign = missing)"
    assert s.get(f"{lane.API_BASE}/api/applications", timeout=30).status_code == 200, \
        "Ada REST+api-bearer stopped working after BEN's failed revoke"
    r = ada.delete(f"/api/me/tokens/{tok['id']}")
    assert r.status_code == 204, f"Ada REST DELETE /api/me/tokens/{{id}} -> {r.status_code}"
    r = s.get(f"{lane.API_BASE}/api/applications", timeout=30)
    assert r.status_code == 401, f"Ada REST+api-bearer after revoke -> {r.status_code}"
    listed = {t["id"] for t in ada.get("/api/me/tokens").json()["tokens"]}
    assert tok["id"] not in listed, "Ada REST GET /api/me/tokens still lists the revoked token"


def test_revoking_an_agent_token_mid_session_refuses_the_next_mcp_call(world):
    ada = world["ada"]
    tok = lane.make_token(ada, "agent", "mid-session")

    def revoke():
        r = ada.delete(f"/api/me/tokens/{tok['id']}")
        assert r.status_code == 204, f"Ada REST revoke agent token mid-session -> {r.status_code}"

    run = lane.mcp(tok["token"], [
        ("get_application", {"application_id": world["Ada_app"]["id"]}),
        ("get_application", {"application_id": world["Ada_app"]["id"]}),
    ], after={1: revoke})
    first, second = lane.mcp_outcome(run.responses.get(1)), lane.mcp_outcome(run.responses.get(2))
    assert first[0] == "ok", f"Ada MCP call before revoke -> {first!r}"
    assert second[0] != "ok", f"Ada MCP call AFTER revoke still served: {second[1]!r}"
    assert "no longer valid" in str(second[1]), f"Ada MCP call after revoke -> {second!r}"
    # A fresh process with the revoked token refuses to start (S-5).
    again = lane.mcp(tok["token"], [("list_applications", {})], timeout=60)
    assert again.returncode == 1, f"Ada MCP start with a revoked token exited {again.returncode}"
    assert "APPLIRE_AGENT_TOKEN" in again.stderr, f"Ada MCP start refusal text: {again.stderr[-300:]!r}"


def test_admin_revoke_tokens_ends_every_token_of_that_person(admin, world):
    ben = world["ben"]
    api = lane.make_token(ben, "api", "admin-revokes")
    s = lane.bearer(api["token"])
    assert s.get(f"{lane.API_BASE}/api/applications", timeout=30).status_code == 200, "Ben REST+api-bearer before"
    r = admin.post(f"/api/admin/users/{ben.id}/revoke-tokens")
    assert r.status_code == 200, f"admin REST POST revoke-tokens for Ben -> {r.status_code} {r.text[:200]}"
    assert r.json()["revoked"] >= 1, f"admin REST revoke-tokens for Ben revoked {r.json()}"
    assert s.get(f"{lane.API_BASE}/api/applications", timeout=30).status_code == 401, \
        "Ben REST+api-bearer still works after the admin revoked his tokens"
    assert ben.get("/api/applications").status_code == 200, \
        "Ben REST (cookie) lost his session — revoke-tokens must leave sessions alone"


# ---------------------------------------------------------------------------
# Disable
# ---------------------------------------------------------------------------


def test_disabling_a_person_ends_every_door_and_login_says_disabled_only_after_the_right_password(admin):
    rid = lane.run_id()
    cleo = lane.new_user(admin, "Cleo", rid)
    api = lane.bearer(lane.make_token(cleo, "api")["token"])
    agent = lane.make_token(cleo, "agent")["token"]
    assert cleo.get("/api/applications").status_code == 200, "Cleo REST before disable"

    r = admin.patch(f"/api/admin/users/{cleo.id}", json={"disabled": True})
    assert r.status_code == 200, f"admin REST PATCH disable Cleo -> {r.status_code} {r.text[:200]}"
    assert r.json()["status"] == "disabled", f"admin REST PATCH disable Cleo status {r.json()['status']!r}"

    r = cleo.get("/api/applications")
    assert r.status_code == 401, f"Cleo REST (old cookie) after disable -> {r.status_code}"
    assert lane.error_code(r) == "unauthenticated", f"Cleo REST (old cookie) code {lane.error_code(r)!r}"
    r = api.get(f"{lane.API_BASE}/api/applications", timeout=30)
    assert r.status_code == 401, f"Cleo REST+api-bearer after disable -> {r.status_code}"
    run = lane.mcp(agent, [("list_applications", {})], timeout=60)
    assert run.returncode == 1, f"Cleo MCP start after disable exited {run.returncode}"

    right = lane.login("Cleo", cleo.email, cleo.password)
    assert right.status_code == 403, f"Cleo REST login (right password, disabled) -> {right.status_code}"
    assert lane.error_code(right) == "account_disabled", f"Cleo REST login code {lane.error_code(right)!r}"
    wrong = lane.login("Cleo", cleo.email, "definitely not the password")
    unknown = lane.login("nobody", f"nobody-{rid}@ci-auth.example.org", "definitely not the password")
    assert wrong.status_code == 401, f"Cleo REST login (wrong password, disabled) -> {wrong.status_code}"
    assert (wrong.status_code, _body(wrong)) == (unknown.status_code, _body(unknown)), (
        f"REST login: disabled Cleo + wrong password {_body(wrong)!r} differs from an unknown email {_body(unknown)!r}"
    )

    r = admin.patch(f"/api/admin/users/{cleo.id}", json={"disabled": False})
    assert r.status_code == 200, f"admin REST PATCH re-enable Cleo -> {r.status_code}"
    again = lane.login("Cleo", cleo.email, cleo.password)
    assert again.status_code == 204, f"Cleo REST login after re-enable -> {again.status_code}"
    r = api.get(f"{lane.API_BASE}/api/applications", timeout=30)
    assert r.status_code == 401, f"Cleo REST+api-bearer after re-enable -> {r.status_code} (disable revoked it)"


# ---------------------------------------------------------------------------
# Self-delete and reference-counted erasure of shared postings
# ---------------------------------------------------------------------------


def _owned_rows(uid: str) -> dict[str, str]:
    out = {}
    for table in ("master_profiles", "applications", "uploads", "cv_import_jobs", "flow_sessions",
                  "personal_tokens", "auth_sessions"):
        out[table] = lane.psql(f"SELECT count(*) FROM {table} WHERE user_id = '{uid}'")
    return out


def _posting_exists(job_id: str) -> bool:
    return lane.psql(f"SELECT count(*) FROM job_analyses WHERE id = '{job_id}'") == "1"


def test_self_delete_erases_the_person_and_only_postings_nobody_else_holds(admin):
    rid = lane.run_id()
    dora = lane.new_user(admin, "Dora", rid)
    emil = lane.new_user(admin, "Emil", rid)
    for p in (dora, emil):
        lane.import_cv(p)
    shared_text = lane.posting_text(rid, "Shared SRE")
    shared = lane.analyze(dora, shared_text)["id"]
    assert lane.analyze(emil, shared_text)["id"] == shared, "Dora and Emil REST analyze: no shared posting"
    dora_only = lane.analyze(dora, lane.posting_text(rid, "Dora-only Analyst"))["id"]
    dora_agent = lane.make_token(dora, "agent")["token"]
    before = _owned_rows(dora.id)
    assert before["master_profiles"] == "1" and before["applications"] == "2", \
        f"Dora database rows before delete: {before}"

    r = dora.delete("/api/me/account", json={"password": "not dora's password"})
    assert r.status_code == 403, f"Dora REST DELETE /api/me/account wrong password -> {r.status_code}"
    assert lane.error_code(r) == "invalid_credentials", f"Dora REST self-delete code {lane.error_code(r)!r}"
    assert _posting_exists(dora_only), "database: a refused self-delete erased Dora's posting"

    r = dora.delete("/api/me/account", json={"password": dora.password})
    assert r.status_code == 204, f"Dora REST DELETE /api/me/account -> {r.status_code} {r.text[:200]}"

    r = dora.get("/api/auth/me")
    assert r.status_code == 401, f"Dora REST (old cookie) after self-delete -> {r.status_code}"
    r = lane.login("Dora", dora.email, dora.password)
    assert r.status_code == 401, f"Dora REST login after self-delete -> {r.status_code}"
    listed = {u["id"] for u in admin.get("/api/admin/users").json()["users"]}
    assert dora.id not in listed, "admin REST GET /api/admin/users still lists Dora after her self-delete"
    run = lane.mcp(dora_agent, [("list_applications", {})], timeout=60)
    assert run.returncode == 1, f"Dora MCP start after self-delete exited {run.returncode}"

    after = _owned_rows(dora.id)
    assert all(v == "0" for v in after.values()), f"database: Dora's owned rows survive her self-delete: {after}"
    assert not _posting_exists(dora_only), "database: the posting only Dora held survived her self-delete"
    assert _posting_exists(shared), "database: the posting Emil still holds was erased with Dora"
    r = emil.get(f"/api/job/{shared}")
    assert r.status_code == 200, f"Emil REST GET the shared posting after Dora left -> {r.status_code}"
    emil_apps = [a for a in lane.applications(emil) if str(a.get("job_analysis_id")) == shared]
    assert len(emil_apps) == 1, f"Emil REST GET /api/applications after Dora left: {len(emil_apps)} for the shared posting"

    r = emil.delete("/api/me/account", json={"password": emil.password})
    assert r.status_code == 204, f"Emil REST DELETE /api/me/account -> {r.status_code} {r.text[:200]}"
    assert not _posting_exists(shared), "database: the shared posting survived its last holder (Emil) leaving"


def test_the_last_admin_cannot_delete_or_demote_themselves(admin):
    users = admin.get("/api/admin/users").json()["users"]
    admins = [u for u in users if u["role"] == "admin" and u["status"] == "active"]
    if len(admins) != 1:
        pytest.fail(f"admin REST GET /api/admin/users: {len(admins)} active admins (the lane expects exactly one here)")
    r = admin.delete("/api/me/account", json={"password": admin.password})
    assert r.status_code == 409, f"admin REST DELETE /api/me/account as the last admin -> {r.status_code}"
    assert lane.error_code(r) == "last_admin", f"admin REST self-delete code {lane.error_code(r)!r}"
    r = admin.patch(f"/api/admin/users/{admin.id}", json={"role": "user"})
    assert r.status_code == 409, f"admin REST PATCH self role=user as the last admin -> {r.status_code}"
    assert lane.error_code(r) == "last_admin", f"admin REST self-demote code {lane.error_code(r)!r}"
    assert admin.refresh_me().role == "admin", "admin REST /api/auth/me: no longer admin after refused demote"
