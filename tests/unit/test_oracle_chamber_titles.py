# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Build-3 adversarial A-2 (WP-FO, 2026-10-23): German chamber titles in prose.

"…führe den Titel Bilanzbuchhalter (IHK)" in a CV summary read ``unverifiable``:
E-2's title families need an ascription frame, and "den Titel" was none. The
widening: the frame reads "Titel"/"Berufsbezeichnung"; the chamber qualifier
"(IHK)"/"(HWK)" names a trade master title and the "Betriebswirt (IHK)" form;
"IHK-geprüfte Controllerin" and the "…kaufmann/-frau" trade titles are their own
families, backed only by their own name in the vault (a bare "Controllerin" job
title or another trade's Kaufmann backs nothing).
"""
import pytest

from applire.services.oracle.credentials import detect_credentials, fold, unbacked_credentials


@pytest.mark.parametrize("text, kind, family", [
    ("Erfahrene Finanzbuchhalterin; führe den Titel Bilanzbuchhalter (IHK).", "cv", "bilanzbuchhalter"),
    ("Seit 2019 trage ich die Berufsbezeichnung Steuerberaterin.", "letter", "steuerberater"),
    ("Als IHK-geprüfte Controllerin verantworte ich das Reporting.", "letter", "controller_ihk"),
    ("Controllerin (IHK) mit Schwerpunkt Konzernreporting", "cv", "controller_ihk"),
    ("Kfz-Mechatronikermeister (HWK) mit Werkstattpraxis", "cv", "meister_kammer"),
    ("Als Betriebswirt (IHK) leite ich das Team.", "letter", "betriebswirt_ihk"),
    ("Ich bin gelernte Industriekauffrau.", "letter", "kaufmann_ihk"),
    ("Ich habe den AEVO-Schein.", "letter", "ada_aevo"),
])
def test_a_chamber_title_is_detected(text, kind, family):
    assert family in {m.family for m in detect_credentials(text, kind=kind)}


@pytest.mark.parametrize("text, kind", [
    ("Als Controllerin verantworte ich das Reporting.", "letter"),        # a job title, no chamber
    ("Ich bereite mich auf die Prüfung zum Bilanzbuchhalter (IHK) vor.", "letter"),  # aspiration
    ("Ich unterstütze die Bilanzbuchhalterin (IHK) bei Abschlüssen.", "letter"),     # another person
    ("Sie suchen einen Bilanzbuchhalter (IHK).", "letter"),
    ("Wir suchen eine Industriekauffrau für unser Team.", "letter"),
    ("Mein Vater war Bäckermeister (HWK).", "letter"),
    ("Ich stimme mich eng mit unserer Controllerin (IHK) ab.", "letter"),  # a title family: frame needed
    ("Ich bin Kaufmann im Herzen.", "letter"),
    ("Ich arbeite eng mit dem Einkauf zusammen.", "letter"),
])
def test_no_ascription_no_mention(text, kind):
    assert detect_credentials(text, kind=kind) == []


@pytest.mark.parametrize("text, vault, unbacked", [
    ("Als IHK-geprüfte Controllerin verantworte ich das Reporting.", "Controllerin (IHK) seit 2020", []),
    ("Als IHK-geprüfte Controllerin verantworte ich das Reporting.", "Controllerin bei der Acme GmbH",
     ["IHK-geprüfte Controllerin"]),
    ("Ich bin gelernte Industriekauffrau.", "Ausbildung zum Industriekaufmann", []),
    ("Ich bin gelernte Industriekauffrau.", "Ausbildung zur Bürokauffrau", ["Industriekauffrau"]),
    ("Ich bin Industriekauffrau und Personalfachkauffrau.", "Ausbildung zur Industriekauffrau",
     ["Personalfachkauffrau"]),                                            # each trade title on its own
    ("Kfz-Mechatronikermeister (HWK) mit Werkstattpraxis", "Kfz-Mechatronikermeisterin", []),
    ("Kfz-Mechatronikermeister (HWK) mit Werkstattpraxis", "Industriemeister Metall",
     ["Kfz-Mechatronikermeister (HWK)"]),
    ("Erfahrene Finanzbuchhalterin; führe den Titel Bilanzbuchhalter (IHK).", "Finanzbuchhalterin",
     ["Bilanzbuchhalter"]),
])
def test_backing_reads_the_vault(text, vault, unbacked):
    kind = "cv" if not text.startswith(("Als", "Ich")) else "letter"
    assert [m.matched for m in unbacked_credentials(text, kind=kind, vault_folded=fold(vault))] == unbacked


def test_a_closed_master_title_is_one_mention():
    """"Industriemeister (IHK)" is the closed family's — not a second, open one."""
    assert [m.family for m in detect_credentials("Als Industriemeister (IHK) führe ich 30 Mitarbeitende.",
                                                 kind="letter")] == ["industriemeister"]
