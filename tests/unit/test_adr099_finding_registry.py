# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""T-registry (ADR-099 cl. 2, UAT-2 fix round, WP-B): every kind a document-side
producer can emit is registered in ``services.finding_registry.REGISTRY``.

The producers are enumerated from their SOURCE (AST), not from a hand-kept list:
a new ``ATSCheck(id=…)``, ``ClaimVerdict(detail_key=…)``, ``CheckFinding(check=…)``,
``LetterFinding(family=…)``, terminal note key, critic kind or keyword bucket
without a registry entry fails this test — so a kind can never render raw or
vanish (ADR-099 cl. 2/12)."""
from __future__ import annotations

import ast
import pathlib

import pytest

from applire.services import finding_registry as fr

SERVICES = pathlib.Path(fr.__file__).resolve().parent


def _const(node, consts: dict[str, str]):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    if isinstance(node, ast.JoinedStr):  # f"work-{i}" → "work-0" (family strips it)
        out = ""
        for part in node.values:
            if isinstance(part, ast.Constant):
                out += str(part.value)
            else:
                out += "0"
        return out
    return None


def _module_consts(tree) -> dict[str, str]:
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def _calls(paths, name: str):
    """(kwargs-as-constants, positional constants) of every call to ``name``."""
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = _module_consts(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            if fname != name:
                continue
            kw = {k.arg: _const(k.value, consts) for k in node.keywords if k.arg}
            pos = [_const(a, consts) for a in node.args]
            yield path.name, node.lineno, kw, pos


def _ats_checks():
    paths = [SERVICES / "ats_audit.py", SERVICES / "terminal_review_outcome.py"]
    rows = []
    for fname, line, kw, _pos in _calls(paths, "ATSCheck"):
        if kw.get("id"):
            rows.append((fname, line, kw["id"], kw.get("status"), kw.get("details_key")))
    for fname, line, _kw, pos in _calls(paths, "_check"):
        if len(pos) >= 2 and pos[1]:
            rows.append((fname, line, pos[1], None, None))
    return rows


def test_the_ats_enumeration_is_not_empty():
    ids = {fr.check_family(r[2]) for r in _ats_checks()}
    # a broken scanner must not pass vacuously
    assert {"contact-name", "work", "page-length", "terminal-review", "document-language"} <= ids, ids


@pytest.mark.parametrize("row", _ats_checks(), ids=lambda r: f"{r[0]}:{r[1]}:{r[2]}")
def test_every_ats_check_is_registered(row):
    _f, _l, cid, status, details_key = row
    check = {"id": cid, "status": status, "details_key": details_key}
    hit = fr.resolve(fr.check_kinds(check))
    assert hit is not None, f"unregistered ATS check {cid!r} (status {status}, key {details_key})"
    if status == "not_applicable":
        # ADR-099 cl. 2 / ADR-081 cl. 9: a producer that did not judge is unknown or
        # report-only, never an action on the document and never "fixed".
        assert hit[1].cls in ("U", "S"), (cid, hit)


def _oracle_verdicts():
    paths = sorted((SERVICES / "oracle").rglob("*.py"))
    rows = []
    for fname, line, kw, _pos in _calls(paths, "ClaimVerdict"):
        if kw.get("detail_key") or kw.get("verdict"):
            rows.append((fname, line, kw.get("verdict"), kw.get("detail_key")))
    return rows


def test_the_oracle_enumeration_is_not_empty():
    keys = {r[3] for r in _oracle_verdicts()}
    assert {"misattributed", "restatementBorrowed", "inflatedAspirational"} <= keys, keys


@pytest.mark.parametrize("row", _oracle_verdicts(), ids=lambda r: f"{r[0]}:{r[1]}:{r[3]}")
def test_every_oracle_detail_key_is_registered(row):
    _f, _l, verdict, detail_key = row
    if detail_key:
        assert f"oracle:{detail_key}" in fr.REGISTRY or verdict in ("grounded", "not_applicable"), detail_key
    for v in ([verdict] if verdict else ["grounded", "inflated", "misattributed", "unbacked", "unverifiable", "not_applicable"]):
        hit = fr.resolve(fr.verdict_kinds({"verdict": v, "detail_key": detail_key}))
        assert hit is not None, (v, detail_key)
        if v in ("inflated", "misattributed", "unbacked"):
            # group 1 is never silent (ADR-099 cl. 6)
            assert fr.rendered_class(hit[1])[0] == "A", (v, detail_key, hit)


def _service_files():
    return sorted(p for p in SERVICES.glob("*.py"))


def _terminal_codes():
    codes = set()
    for _f, _l, kw, _pos in _calls(_service_files(), "CheckFinding"):
        c = kw.get("check")
        # an f-string family ("letter.{f.family}") is enumerated through LetterFinding
        if c and "." in c and not c.endswith(".0"):
            codes.add(c)
    for _f, _l, kw, pos in _calls(_service_files(), "LetterFinding"):
        fam = kw.get("family") or (pos[0] if pos else None)
        if fam:
            codes.add(f"letter.{fam}")
    return sorted(codes)


def test_the_terminal_code_enumeration_is_not_empty():
    codes = set(_terminal_codes())
    assert {"cv.role_budget", "letter.language", "letter.salutation", "cv.claim_missing"} <= codes, codes


@pytest.mark.parametrize("code", _terminal_codes())
def test_every_terminal_review_code_is_registered(code):
    assert f"terminal:{code}" in fr.REGISTRY, code


def _terminal_note_keys():
    keys = set()
    for _f, _l, kw, _pos in _calls(_service_files(), "TerminalNote"):
        if kw.get("key") and kw.get("kind") in ("note", "finding"):
            keys.add((kw["kind"], kw["key"]))
    return sorted(keys)


@pytest.mark.parametrize("kind_key", _terminal_note_keys(), ids=lambda k: f"{k[0]}:{k[1]}")
def test_every_terminal_note_key_is_registered(kind_key):
    kind, key = kind_key
    hit = fr.resolve(fr.note_kinds({"kind": kind, "key": key}))
    assert hit is not None and hit[0] != "terminal:finding" or kind == "finding", kind_key


def test_note_keys_that_are_variables_are_registered():
    # cover_letter.py assigns note_key = "letter.limit_cut" / "letter.limit_kept"
    src = (SERVICES / "cover_letter.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "note_key" for t in node.targets):
            assert isinstance(node.value, ast.Constant)
            assert f"terminal:note:{node.value.value}" in fr.REGISTRY, node.value.value


def test_every_critic_kind_is_registered():
    from applire.services.outcome_critic import _PASS_KINDS

    kinds = set().union(*_PASS_KINDS.values())
    assert kinds
    for k in kinds:
        assert f"critic:{k}" in fr.REGISTRY, k


def test_every_keyword_bucket_is_registered():
    from applire.schemas.ats import ATSKeywordCoverage

    lists = [n for n, f in ATSKeywordCoverage.model_fields.items()
             if not n.endswith("_matches") and n != "delivery"]
    for n in lists:
        assert f"keyword:{n}" in fr.REGISTRY, n
    for b in fr.KEYWORD_BUCKETS:
        assert b in ATSKeywordCoverage.model_fields, b


def test_registry_vocabulary_is_closed():
    for kind, entry in fr.REGISTRY.items():
        assert entry.resolver is None or entry.resolver in fr.RESOLVERS, (kind, entry)
        if entry.cls == "F":
            assert entry.fallback in ("A", "U", "S"), kind   # cl. 4(d)/(e)
        else:
            assert entry.fallback is None, kind
        if fr.rendered_class(entry)[0] in ("A", "U"):
            assert entry.resolver, f"class A/U kind {kind} has no action"  # cl. 2


def test_a_row_is_never_stamped_f_and_a_pass_is_silent():
    report = {
        "checks": [
            {"id": "document-language", "status": "fail", "details_key": "document-language-mixed"},
            {"id": "duplicate-bullets", "status": "fail", "details_key": "duplicate-bullets"},
            {"id": "work-3", "status": "pass"},
            {"id": "terminal-review", "status": "not_applicable", "details_key": "terminal-review-not-run",
             "notes": [{"kind": "code", "key": "cv.page_length"}]},
        ],
        "keywords": {},
    }
    assert fr.annotate_report(report) == []
    got = [(c["delivery_class"], c["resolver"]) for c in report["checks"]]
    assert got == [("A", "translate"), ("S", None), ("S", None), ("U", "regenerate")]
    assert report["checks"][3]["notes"][0]["delivery_class"] == "S"
    assert report["keywords"]["delivery"]["present_unsupported"] == {"delivery_class": "A", "resolver": "ask_true"}


def test_the_audit_seam_stamps_every_report():
    """``ats_audit._finish`` is the one seam every audit door reaches."""
    from applire.schemas.ats import ATSCheck, ATSKeywordCoverage
    from applire.services.ats_audit import _finish

    rep = _finish("cv", [ATSCheck(id="page-length", status="fail", details="3 pages",
                                  details_key="page-length-target-missed")], ATSKeywordCoverage())
    assert rep.checks[0].delivery_class == "A" and rep.checks[0].resolver == "page_length"
    assert rep.keywords.delivery["missing_claimable"].delivery_class == "S"


def test_the_oracle_seam_stamps_every_verdict():
    from applire.schemas.oracle import Claim, ClaimResult, ClaimVerdict, TruthfulnessReport

    rep = TruthfulnessReport.from_results("cv", [
        ClaimResult(claim=Claim(text="x", location="summary[0]"),
                    verdict=ClaimVerdict(verdict="misattributed", checker="attribution", detail_key="misattributed")),
        ClaimResult(claim=Claim(text="y", location="summary[1]"),
                    verdict=ClaimVerdict(verdict="grounded", checker="grounding", detail_key="groundedUnion")),
    ])
    out = fr.stamp_truthfulness_model(rep)
    assert (out.claims[0].verdict.delivery_class, out.claims[0].verdict.resolver) == ("A", "ask_station")
    assert out.claims[1].verdict.delivery_class == "S"


def _catalog_keys(lang):
    import json

    root = SERVICES.parents[2] / "frontend" / "messages" / f"{lang}.json"
    return set(json.loads(root.read_text(encoding="utf-8"))["ats"]["checkDetails"])


def _emitted_details_keys():
    keys = set()
    for _f, _l, kw, _pos in _calls([SERVICES / "ats_audit.py", SERVICES / "terminal_review_outcome.py"], "ATSCheck"):
        if kw.get("details_key"):
            keys.add(kw["details_key"])
    for _f, _l, _kw, pos in _calls([SERVICES / "ats_audit.py"], "_key_failed"):
        if len(pos) >= 2 and pos[1]:
            keys.add(pos[1])
    return sorted(keys)


@pytest.mark.parametrize("key", _emitted_details_keys())
def test_every_emitted_details_key_is_in_both_catalogs(key):
    """ADR-099 cl. 12: no server prose — every keyed detail renders from de/en."""
    # a composite key the surface assembles from catalog parts (ReviewSurface
    # narrativeEvidenceDetail: tag-only / absent / absent-also / more)
    composite = {"narrative-evidence-underclaim": "narrative-evidence-tag-only"}
    key = composite.get(key, key)
    assert key in _catalog_keys("de") and key in _catalog_keys("en"), key


def test_an_unkeyed_action_row_is_silent_until_keyed(caplog):
    caplog.set_level("INFO", logger="applire.services.finding_registry")
    report = {"checks": [{"id": "document-language", "status": "fail", "details": "server sentence"}]}
    fr.annotate_report(report)
    assert report["checks"][0]["delivery_class"] == "S"
    assert any("FINDING_UNKEYED" in r.message for r in caplog.records)
    report = {"checks": [{"id": "document-language", "status": "fail", "details_key": "document-language-mixed"}]}
    fr.annotate_report(report)
    assert report["checks"][0]["delivery_class"] == "A"


def test_the_duplicate_rows_are_keyed():
    from applire.schemas.ats import ATSCheck
    from applire.services.ats_audit import _check, _key_failed

    checks: list = []
    _check(checks, "duplicate-bullets", False, "x")
    _key_failed(checks, "duplicate-bullets", {"pairs": "a ~ b", "count": 1})
    assert checks[0].details_key == "duplicate-bullets" and checks[0].details_params["pairs"] == "a ~ b"
    _check(checks, "duplicate-bullets", True, "x")
    _key_failed(checks, "duplicate-bullets", {"pairs": ""})
    assert checks[1].details_key is None


def test_the_letter_keep_decision_decides_the_page_row():
    """ADR-099 cl. 7: "{n} Seiten behalten" on the letter → the row is decided
    (page-length-kept, class S, still in the report as the pin driver)."""
    from applire.services.ats_audit import LETTER_PAGE_KEEP_KEY, decide_kept_letter_page_length

    report = {"checks": [{"id": "page-length", "status": "fail", "details_key": "page-length-letter",
                          "details_params": {"pages": 2, "region": "DACH", "letterPages": 1}}]}
    assert decide_kept_letter_page_length(report, None) is report
    state = {"decisions": [{"finding_key": LETTER_PAGE_KEEP_KEY, "action": "kept"}]}
    out = decide_kept_letter_page_length(report, state)
    row = out["checks"][0]
    assert (row["status"], row["details_key"], row["delivery_class"]) == ("pass", "page-length-kept", "S")
    assert row["details_params"] == {"pages": 2, "target": 1}
    fresh = {"checks": [dict(report["checks"][0])]}
    fr.annotate_report(fresh)
    assert (fresh["checks"][0]["delivery_class"], fresh["checks"][0]["resolver"]) == ("A", "page_length")


def test_d_ag1_only_the_agent_origin_silences_the_terminal_not_run_row():
    """RULING D-AG1, both sides: an Applire-written document whose final review did
    not run keeps the counted U "regenerate" (origin pipeline, loop degraded OR no
    loop state); only origin == "agent" makes the row S. A re-audit (origin None)
    carries the flag from the report it replaces."""
    row = {"id": "terminal-review", "status": "not_applicable", "details_key": "terminal-review-not-run"}
    for degradation in (None, [{"chain": "cv_terminal_review", "stage": "review"}]):
        out = fr.apply_delivery({"checks": [dict(row)]}, review_degradation=degradation, origin="pipeline")
        assert (out["checks"][0]["delivery_class"], out["checks"][0]["resolver"]) == ("U", "regenerate")
        assert out["delivery"]["authored_by_agent"] is False
    out = fr.apply_delivery({"checks": [dict(row)]}, review_degradation=None, origin="agent")
    assert (out["checks"][0]["delivery_class"], out["checks"][0]["resolver"]) == ("S", None)
    again = fr.apply_delivery({"checks": [dict(row)]}, review_degradation=None, previous=out)
    assert again["delivery"]["authored_by_agent"] is True and again["checks"][0]["delivery_class"] == "S"
    # the agent flag silences ONLY the terminal-review row
    other = fr.apply_delivery({"checks": [{"id": "document-language", "status": "fail",
                                           "details_key": "document-language-mixed"}]},
                              review_degradation=None, origin="agent")
    assert other["checks"][0]["delivery_class"] == "A"
