# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D-5 (Hazelnut build 5 founder replay): one credential, two languages.

A second-source import added the English title of a certificate the profile
already held under its German title. The lexical instrument
(``dedupe.classify_certification_dupe``) folds only a few named DE/EN word pairs.
"Experte für …" against "Expert for … System …" is a translation, not a
spelling, so the instrument called the pair DISTINCT. The certificate then
stood in the profile twice.

Whether two titles name the same credential is a judgement (ADR-062). This
module makes it in ONE model call at the end of an import: the certificates
this import ADDED against the ones the profile held before. A match is applied
the way the reconciler's own ``match_existing`` would have been:

- the added entry is removed;
- its empty-field facts fill the held entry;
- a ``MatchReceipt`` (basis ``model``) goes on the import's ``matched``, so the
  summary shows "recognised as already present" and "Nicht dasselbe" can add
  the entry back;
- a ``MatchExisting`` op is returned for the caller's emitted ops, so the
  import witness binds the document's entry and does not report it as not
  applied.

A failed call changes nothing; both entries stay (the safe direction: nothing
is lost, the candidate sees both).
"""
from __future__ import annotations

import logging
from typing import Any

from applire.schemas.profile import MasterProfileData, MatchReceipt
from applire.services.profile.reconcile.apply import ApplyResult, _fill_empties, _merged, _norm
from applire.services.profile.reconcile.ops import MatchExisting

logger = logging.getLogger(__name__)

_FIELDS = ("name", "issuing_organization", "date_obtained", "expiry_date", "credential_id")


def _view(cert: Any) -> dict[str, Any]:
    return {f: getattr(cert, f) for f in _FIELDS if getattr(cert, f, None)}


async def bind_translated_certificates(
    existing: MasterProfileData, applied: ApplyResult, provider
) -> list[MatchExisting]:
    """Fold each certificate this import added into the held certificate the
    model judges to be the same credential. Mutates ``applied`` (profile,
    changes, matched); returns the bindings as ``match_existing`` ops."""
    from applire.prompts.profile_decisions import CERT_SAME_SYSTEM
    from applire.services.profile.decisions import judge_already_present

    held_ids = {c.id for c in existing.certifications if c.id}
    certs = applied.profile.certifications
    held = [c for c in certs if c.id in held_ids]
    added = [c for c in certs if c.id and c.id not in held_ids]
    if not held or not added:
        return []
    try:
        found = await judge_already_present(
            provider,
            [{"item": f"n{n}", "section": "certifications", **_view(c)} for n, c in enumerate(added)],
            {"certifications": [{"id": c.id, **_view(c)} for c in held]},
            system=CERT_SAME_SYSTEM,
            stage="profile_certificate_same",
        )
    except Exception:  # noqa: BLE001 — never fail the import
        logger.warning("certificate identity check skipped", exc_info=True)
        return []
    by_id = {c.id: c for c in held}
    bindings: list[MatchExisting] = []
    for n, cert in enumerate(added):
        target = by_id.get((found or {}).get(f"n{n}"))
        if target is None:
            continue
        certs.remove(cert)
        wanted = _norm(cert.name)
        for i, change in enumerate(applied.changes):
            if (
                change.section == "certifications" and change.action == "added"
                and isinstance(change.new_value, str) and _norm(change.new_value) == wanted
            ):
                del applied.changes[i]
                break
        if _fill_empties(target, {f: getattr(cert, f) for f in _FIELDS[1:]} | {"credential_url": cert.credential_url}):
            applied.changes.append(_merged("certifications", "name", None, target.name, entity=target))
        applied.matched.append(MatchReceipt(
            section="certifications", entity_id=str(target.id), incoming=cert.name,
            existing=target.name, basis="model",
        ))
        bindings.append(MatchExisting(target=str(target.id), incoming=cert.name, existing=target.name))
    return bindings
