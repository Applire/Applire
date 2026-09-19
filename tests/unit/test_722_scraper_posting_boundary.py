# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
#722 — the stored JD is the posting, not the page.

A logged-out job-board guest page wraps the posting in a `<main>` it shares with
a topcard, a sign-in modal and a "similar jobs" rail carrying OTHER employers'
postings. The extractor used to take that `<main>`, so every deterministic
consumer of ``job_analyses.raw_text`` — the keyword ledger's
``jd_qualifying_phrase``, the letter's recipient extraction, the CV's JD excerpt
and ``raw_text_hash`` — read ten other companies' job ads as the employer's own
words.

Measured on the captured real page (2026-09-16, NOT in the repository: it carries
ten other companies' postings): 11,775 chars stored, 26 x "(m/w/d)", foreign
employers and salaries inside the text. After: 5,547 chars, 2 x "(m/w/d)", none.

The fixtures here are SYNTHETIC twins of that structure with invented employers.

Mutation kills (verified 2026-09-18, each by editing a scratchpad copy of
``services/scraper.py`` and re-running this file):
  * drop the two LinkedIn hints from ``_DESCRIPTION_HINTS``
        -> test_the_densest_named_container_wins_over_the_one_nested_in_it
    (the guest-page test survives that one: the structural arm underneath
     catches the same page, which is the layering working, not a hole)
  * return ``_description_blocks`` in document order instead of densest-first
        -> test_a_short_teaser_container_does_not_beat_the_full_posting
  * drop the ``_trim_wrapper`` call from ``_extract_text``
        -> test_an_unknown_board_posting_is_found_by_structure
  * set ``_MIN_BLOCK_SHARE = 0.0``
        -> test_a_posting_split_across_siblings_is_returned_whole
  * revert ``_CHROME_TAGS`` to script/style/nav/header/footer
        -> test_chrome_tags_never_reach_the_extracted_text
"""
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup

_FILES = Path(__file__).parent.parent / "files" / "scraper"

# Employers that exist only in the synthetic "similar jobs" rail.
_RAIL_EMPLOYERS = (
    "Silbermond Analytics",
    "Quellwasser Systeme KG",
    "Brückner Handel GmbH",
    "Feldmann Versicherung",
    "Nordstern Logistik AG",
    "Ahornblatt Pharma SE",
    "Kranichhof Beteiligungen",
    "Seegras Energie eG",
    "Turmfalke Software GmbH",
)


def _fixture(name: str) -> str:
    return (_FILES / name).read_text(encoding="utf-8")


def _main_text(html: str) -> str:
    """What the pre-#722 extractor returned: <main> after the old chrome strip."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    return soup.find("main").get_text(separator=" ", strip=True)


# ---------------------------------------------------------------------------
# The named-container arm — a board whose posting container we recognise
# ---------------------------------------------------------------------------


def test_the_guest_page_yields_the_posting_only():
    from applire.services.scraper import _extract_text

    html = _fixture("guest_board_posting.html")
    text = _extract_text(html)

    assert text is not None
    # The posting names the role twice; the page names it twelve times, because
    # the topcard and the nine rail entries each repeat "(m/w/d)".
    assert _main_text(html).count("(m/w/d)") == 12
    assert text.count("(m/w/d)") == 2
    # Not one foreign employer survives.
    assert [e for e in _RAIL_EMPLOYERS if e in text] == []
    # The sign-in modal is gone too.
    assert "Passwort" not in text
    assert "Nutzervereinbarung" not in text
    # The posting itself is intact.
    for passage in ("Wir sind Nordlicht Medien", "Deine Aufgaben", "Dein Profil", "Unser Angebot"):
        assert passage in text
    assert len(text) < len(_main_text(html))


def test_a_short_teaser_container_does_not_beat_the_full_posting():
    """Document order is not the right order.

    Boards put a short "Kurzfassung" above the posting and give it a
    description-ish class too. First-match wins would store the teaser and drop
    the posting; the densest match has to win.
    """
    from applire.services.scraper import _extract_text

    html = """
    <html><body><main>
      <div class="job-description job-description--teaser">
        <p>Kurzfassung: Wir suchen eine Leitung fuer unsere Datenplattform am
        Standort Hamburg, in Vollzeit und unbefristet, mit Fuehrungserfahrung und
        einem guten Gespuer fuer Zusammenarbeit zwischen Technik und Fachbereich.</p>
      </div>
      <div class="description__text">
        <p>Wir sind ein Familienunternehmen mit langer Geschichte und bauen unsere
        Zukunft aus Daten. Sie verantworten Aufbau und Betrieb unserer zentralen
        Datenplattform und fuehren ein Team von acht Personen fachlich und
        disziplinarisch. Sie entwickeln die Datenstrategie weiter, priorisieren die
        Roadmap mit den Fachbereichen und verantworten das Budget. Sie etablieren
        Standards fuer Datenqualitaet, Metadaten und Zugriffsrechte und verankern
        sie in den Teams. Mehrjaehrige Erfahrung in der Leitung technischer Teams
        setzen wir voraus, ebenso fundierte Kenntnisse moderner Datenarchitekturen
        und Streaming-Systeme sowie sehr gute Deutschkenntnisse.</p>
      </div>
    </main></body></html>
    """
    text = _extract_text(html)
    assert text is not None
    assert "Standards fuer Datenqualitaet" in text, "the full posting must win"
    assert "Kurzfassung" not in text


def test_the_densest_named_container_wins_over_the_one_nested_in_it():
    """LinkedIn nests `show-more-less-html__markup` inside `description__text`.

    Document order would return whichever the parser reached first; the outer
    node is the complete posting, so the DENSEST match has to win.
    """
    from applire.services.scraper import _extract_text

    html = """
    <html><body><main>
      <div class="description__text">
        <p>Vorbemerkung des Arbeitgebers zur ausgeschriebenen Position, die nur im
        aeusseren Container steht und im inneren Markup fehlt. Sie ist Teil der
        Stellenbeschreibung und darf nicht verloren gehen.</p>
        <div class="show-more-less-html__markup">
          <p>Der eigentliche Anzeigentext mit Aufgaben, Profil und Angebot, lang
          genug fuer die Akzeptanzschwelle von zweihundert Zeichen, damit beide
          Container als Kandidaten in Frage kommen und die Reihenfolge wirklich
          entschieden werden muss.</p>
        </div>
      </div>
    </main></body></html>
    """
    text = _extract_text(html)
    assert text is not None
    assert "Vorbemerkung des Arbeitgebers" in text
    assert "Der eigentliche Anzeigentext" in text


# ---------------------------------------------------------------------------
# The structural arm — a board we do not recognise
# ---------------------------------------------------------------------------


def test_an_unknown_board_posting_is_found_by_structure():
    """Same shape, house-style class names our hint list does not know."""
    from applire.services.scraper import _description_blocks, _extract_text

    html = _fixture("unnamed_board_posting.html")
    assert _description_blocks(BeautifulSoup(html, "lxml")) == [], (
        "the fixture must not be recognisable by name, or it proves the other arm"
    )

    text = _extract_text(html)
    assert text is not None
    assert text.count("(m/w/d)") == 2
    assert [e for e in _RAIL_EMPLOYERS if e in text] == []
    for passage in ("Wir sind Nordlicht Medien", "Deine Aufgaben", "Unser Angebot"):
        assert passage in text


def test_a_posting_split_across_siblings_is_returned_whole():
    """Negative control for the structural trim.

    Three sibling sections, none holding 40% of the wrapper: trimming here would
    ship a third of the posting. The wrapper must come back intact.
    """
    from applire.services.scraper import _extract_text

    html = _fixture("split_posting.html")
    text = _extract_text(html)

    assert text == _main_text(html)
    for passage in ("Wiesengrund Werke", "Audits", "Englischkenntnisse"):
        assert passage in text


def test_a_posting_split_across_two_siblings_is_returned_whole():
    """Adversarial pass, 2026-09-19: a TWO-way split, not the three-way case.

    Both halves individually clear `_MIN_BLOCK_SHARE` (roughly 50/50), so both
    are legitimate `_trim_wrapper` candidates. Picking "the densest" would ship
    the profile half and silently drop the tasks half (or vice versa) — the
    same corruption #722 fixed, one shape further. The wrapper must come back
    whole, exactly as the three-way split does.
    """
    from applire.services.scraper import _extract_text

    html = _fixture("two_way_split_posting.html")
    text = _extract_text(html)

    assert text == _main_text(html)
    for passage in ("Qualitätsabteilung", "IATF-16949-Audits"):
        assert passage in text


def test_two_disjoint_comparably_sized_named_blocks_both_survive():
    """Adversarial pass, 2026-09-19: two SEPARATE description-hint-named
    containers (not one nested inside the other, unlike the LinkedIn shape),
    each carrying about half the posting. "Densest wins" would silently drop
    whichever is shorter; both halves must survive."""
    from applire.services.scraper import _extract_text

    html = _fixture("two_way_named_split_posting.html")
    text = _extract_text(html)

    assert text is not None
    assert "Qualitätsabteilung" in text
    assert "Six Sigma" in text


def test_a_short_named_teaser_still_loses_to_the_full_named_posting():
    """Guard: the size-ratio check must not turn EVERY disjoint named pair
    ambiguous — a short teaser (clearly a minority of the full text, same
    shape as `test_a_short_teaser_container_does_not_beat_the_full_posting`)
    must still lose to the full posting, not trigger a fallback."""
    from applire.services.scraper import _extract_text

    html = """
    <html><body><main>
      <div class="job-description job-description--teaser">
        <p>Kurzfassung: Wir suchen eine Qualitaetsleitung fuer unseren
        Hauptstandort, in Vollzeit und unbefristet.</p>
      </div>
      <div class="job-description job-description--full">
        <p>Sie leiten die Qualitaetsabteilung und verantworten die Einhaltung
        unserer ISO-9001-Zertifizierung ueber alle Fertigungslinien hinweg.
        Sie planen interne und externe Audits, begleiten sie persoenlich und
        leiten aus den Ergebnissen Korrekturmassnahmen ab, deren Wirksamkeit
        Sie gemeinsam mit der Fertigungsleitung nachhalten. Sie bauen unser
        Reklamationsmanagement weiter aus und fuehren ein Team von sechs
        Qualitaetsingenieuren fachlich und disziplinarisch.</p>
      </div>
    </main></body></html>
    """
    text = _extract_text(html)
    assert text is not None
    assert "Qualitaetsabteilung" in text
    assert "Kurzfassung" not in text


def test_chrome_tags_never_reach_the_extracted_text():
    from applire.services.scraper import _extract_text

    html = """
    <html><body>
      <aside class="rail">Empfohlene Stelle bei Silbermond Analytics, Berlin.</aside>
      <dialog id="cookies">Wir verwenden Cookies. Alle akzeptieren?</dialog>
      <noscript>Bitte aktivieren Sie JavaScript.</noscript>
      <main>
        <form action="/login"><label>Passwort</label><input type="password"></form>
        <p>Fuer unseren Standort Hamburg suchen wir eine Leitung fuer die
        Datenplattform. Sie verantworten Aufbau und Betrieb, fuehren ein Team von
        acht Personen und entwickeln die Datenstrategie weiter. Mehrjaehrige
        Erfahrung in der Leitung technischer Teams setzen wir voraus.</p>
      </main>
    </body></html>
    """
    text = _extract_text(html)
    assert text is not None
    assert "Silbermond Analytics" not in text
    assert "Cookies" not in text
    assert "JavaScript" not in text
    assert "Passwort" not in text
    assert "Datenplattform" in text


# ---------------------------------------------------------------------------
# The repost hash (`job.py:_hash_text` over what the scraper returned)
# ---------------------------------------------------------------------------


def test_the_repost_hash_does_not_move_when_only_the_rail_changes():
    """`duplicate_of` compares sha256 of the stored text.

    The rail is re-ranked on every fetch, so while it was inside the stored text
    two fetches of the SAME posting could never hash equal — repost detection was
    dead on any page with one.
    """
    from applire.services.job import _hash_text
    from applire.services.scraper import _extract_text

    html = _fixture("guest_board_posting.html")

    soup = BeautifulSoup(html, "lxml")
    rail = soup.select_one("section.similar-jobs ul")
    items = rail.find_all("li")
    for item in reversed(items):          # a different rail order …
        rail.append(item.extract())
    items[0].decompose()                  # … and a different rail membership
    reordered = str(soup)
    assert reordered != html

    first = _extract_text(html)
    second = _extract_text(reordered)
    assert first == second
    assert _hash_text(first) == _hash_text(second)
    assert _hash_text(first) == hashlib.sha256(first.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Seam: ONE extractor serves BOTH doors
# ---------------------------------------------------------------------------


def _mock_httpx_client(html: str):
    response = MagicMock()
    response.text = html
    response.raise_for_status = MagicMock()
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "door_module",
    ["applire.routers.job", "applire.mcp.server"],
)
async def test_both_doors_fetch_through_the_fixed_extractor(door_module):
    """`POST /api/job/analyze` and the MCP tool `analyze_jd` import the same
    `scrape_job_url`. Drive each door's own symbol over the guest-page fixture
    and assert the posting — not the page — is what the door hands on."""
    import importlib

    from applire.services import scraper as scraper_module

    module = importlib.import_module(door_module)
    door_symbol = module.scrape_job_url
    assert door_symbol is scraper_module.scrape_job_url

    html = _fixture("guest_board_posting.html")
    with patch("httpx.AsyncClient", return_value=_mock_httpx_client(html)):
        text = await door_symbol("https://jobs.example.com/leiter-datenplattform")

    assert text.count("(m/w/d)") == 2
    assert [e for e in _RAIL_EMPLOYERS if e in text] == []
    assert "Wir sind Nordlicht Medien" in text


def test_tier2_waits_for_the_same_containers_tier1_extracts_from():
    """A JS board must not be read before the posting container exists."""
    import inspect

    from applire.services import scraper as scraper_module

    source = inspect.getsource(scraper_module._fetch_tier2)
    assert "_TIER2_WAIT_SELECTOR" in source
    for hint in ("description__text", "show-more-less-html__markup", "main"):
        assert hint in scraper_module._TIER2_WAIT_SELECTOR
