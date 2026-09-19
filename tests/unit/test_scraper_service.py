"""
Iteration 8 — JD URL Intake: scraper unit tests

Tests the tiered scraping service without any real HTTP calls.
All network I/O is mocked via unittest.mock.

Run:
    pytest tests/unit/test_iter8_scraper.py -v
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# HTML fixtures
# ---------------------------------------------------------------------------

_JOB_HTML_MAIN = """
<html><body>
<nav><a href="/">Home</a></nav>
<main>
  <h1>Senior Python Engineer</h1>
  <p>We are looking for a Senior Python Engineer with 5+ years of experience
  in FastAPI, PostgreSQL, and Docker. You will work on our core platform and
  collaborate with a small, talented team in Berlin. Strong knowledge of
  async Python, REST API design, and SQL is required. Experience with
  Kubernetes and CI/CD pipelines is a plus.</p>
</main>
<footer>Footer content — copyright 2025</footer>
</body></html>
"""

_JOB_HTML_ARTICLE = """
<html><body>
<article class="job-posting">
  <h1>Backend Developer</h1>
  <p>Join our team as a Backend Developer. You will build scalable microservices
  using Go and Python. Requirements: 3+ years of backend development experience,
  knowledge of gRPC, Docker, and PostgreSQL. Nice to have: Kafka, Redis,
  experience with DACH job market hiring processes.</p>
</article>
</body></html>
"""

_JOB_HTML_JOB_DESCRIPTION_DIV = """
<html><body>
<div id="jobDescriptionText">
  <h2>Data Engineer – Munich</h2>
  <p>We are seeking a Data Engineer to join our analytics team. You will design
  and maintain ETL pipelines, work with Apache Spark, Airflow, and BigQuery.
  Requirements: 4+ years of data engineering experience, SQL expertise,
  Python scripting, and familiarity with cloud data warehouses.</p>
</div>
</body></html>
"""

_TINY_HTML = "<html><body><p>Job found here.</p></body></html>"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_httpx_client(html: str):
    """Return a mock httpx.AsyncClient context manager that yields ``html``."""
    mock_response = MagicMock()
    mock_response.text = html
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _mock_playwright_cm(html: str):
    """Return a mock async_playwright() context manager that yields ``html`` via page.content()."""
    mock_page = AsyncMock()
    mock_page.content = AsyncMock(return_value=html)

    mock_browser = AsyncMock()
    mock_browser.new_page = AsyncMock(return_value=mock_page)

    mock_pw = MagicMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    return mock_cm


# ---------------------------------------------------------------------------
# ScraperError
# ---------------------------------------------------------------------------


def test_scraper_error_carries_url_and_reason():
    from applire.services.scraper import ScraperError

    err = ScraperError("https://example.com/job/1", "Could not extract text")
    assert err.url == "https://example.com/job/1"
    assert err.reason == "Could not extract text"
    assert err.code == "jd_fetch_failed"
    assert "Could not extract text" in str(err)


def test_scraper_error_code_can_be_overridden() -> None:
    from applire.services.scraper import ScraperError

    err = ScraperError("https://example.com/job/1", "some reason", code="custom_code")
    assert err.code == "custom_code"


# ---------------------------------------------------------------------------
# _validate_url
# ---------------------------------------------------------------------------


def test_validate_url_accepts_https():
    from applire.services.scraper import _validate_url

    _validate_url("https://www.stepstone.de/job/123")  # must not raise


def test_validate_url_accepts_http():
    from applire.services.scraper import _validate_url

    _validate_url("http://jobs.example.com/engineer")  # must not raise


def test_validate_url_rejects_file_scheme():
    from applire.services.scraper import _validate_url

    with pytest.raises(ValueError, match="http"):
        _validate_url("file:///etc/passwd")


def test_validate_url_rejects_ftp_scheme():
    from applire.services.scraper import _validate_url

    with pytest.raises(ValueError, match="http"):
        _validate_url("ftp://jobs.example.com/job.txt")


def test_validate_url_rejects_bare_string():
    from applire.services.scraper import _validate_url

    with pytest.raises(ValueError):
        _validate_url("not-a-url-at-all")


# ---------------------------------------------------------------------------
# _requires_js
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.stepstone.de/stellenangebote/senior-python-engineer.html",
        "https://www.stepstone.at/stellenangebote/backend-engineer.html",
        "https://www.stepstone.ch/stellenangebote/devops-engineer.html",
        "https://de.indeed.com/viewjob?jk=abc123",
        "https://at.indeed.com/viewjob?jk=def456",
        "https://ch.indeed.com/viewjob?jk=ghi789",
    ],
)
def test_requires_js_for_known_js_hosts(url):
    from applire.services.scraper import _requires_js

    assert _requires_js(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.example.com/engineer",
        "https://karriere.acme.de/jobs/python-developer",
        "https://generic-job-board.de/stellen/123",
        "https://www.linkedin.com/jobs/view/12345",
    ],
)
def test_requires_js_false_for_generic_hosts(url):
    """LinkedIn in this list is RECORDED behaviour, not an oversight.

    ADR-001 amended 2026-09-18 (founder ruling J-1): a guest posting page a board
    serves without a login is fetched by tier 1 like any other board; only a
    login-gated page falls back to manual paste. The record's original
    "no LinkedIn scraping" consequence is retired.
    """
    from applire.services.scraper import _requires_js

    assert _requires_js(url) is False


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


def test_extract_text_from_main_tag():
    from applire.services.scraper import _extract_text

    result = _extract_text(_JOB_HTML_MAIN)
    assert result is not None
    assert len(result) >= 200
    assert "Python Engineer" in result
    # nav and footer must be stripped
    assert "Home" not in result
    assert "Footer content" not in result


def test_extract_text_from_article_tag():
    from applire.services.scraper import _extract_text

    result = _extract_text(_JOB_HTML_ARTICLE)
    assert result is not None
    assert "Backend Developer" in result


def test_extract_text_from_job_description_div():
    from applire.services.scraper import _extract_text

    result = _extract_text(_JOB_HTML_JOB_DESCRIPTION_DIV)
    assert result is not None
    assert "Data Engineer" in result


def test_extract_text_returns_none_for_tiny_html():
    from applire.services.scraper import _extract_text

    result = _extract_text(_TINY_HTML)
    assert result is None


def test_extract_text_strips_script_content():
    from applire.services.scraper import _extract_text

    html = """
    <html><body><main>
      <script>var tracker = "secret-token-abc123";</script>
      <h1>Software Engineer</h1>
      <p>We need an experienced Software Engineer who knows Python, FastAPI,
      PostgreSQL, Docker, and Kubernetes. At least 5 years of professional
      experience in backend development is required. The role is based in
      Berlin with flexible hybrid work arrangements available.</p>
    </main></body></html>
    """
    result = _extract_text(html)
    assert result is not None
    assert "secret-token-abc123" not in result
    assert "Software Engineer" in result


# ---------------------------------------------------------------------------
# scrape_job_url — Tier 1 (httpx)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier1_happy_path():
    """Generic URL: Tier 1 (httpx) succeeds and returns extracted text."""
    from applire.services.scraper import scrape_job_url

    url = "https://jobs.example.com/senior-python-engineer"
    mock_client = _mock_httpx_client(_JOB_HTML_MAIN)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await scrape_job_url(url)

    assert "Python Engineer" in result
    assert len(result) >= 200


@pytest.mark.asyncio
async def test_tier1_insufficient_text_falls_back_to_tier2():
    """Tier 1 returns too little text → automatically falls back to Tier 2."""
    from applire.services.scraper import scrape_job_url

    url = "https://jobs.example.com/job/42"
    mock_client = _mock_httpx_client(_TINY_HTML)
    mock_pw = _mock_playwright_cm(_JOB_HTML_ARTICLE)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch("playwright.async_api.async_playwright", return_value=mock_pw),
    ):
        result = await scrape_job_url(url)

    assert "Backend Developer" in result


# ---------------------------------------------------------------------------
# scrape_job_url — Tier 2 (Playwright)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_js_host_skips_tier1_and_uses_tier2():
    """StepStone URL must skip Tier 1 entirely and go straight to Tier 2."""
    from applire.services.scraper import scrape_job_url

    url = "https://www.stepstone.de/stellenangebote/python-engineer-berlin.html"
    mock_pw = _mock_playwright_cm(_JOB_HTML_MAIN)

    with (
        patch("httpx.AsyncClient") as mock_httpx,
        patch("playwright.async_api.async_playwright", return_value=mock_pw),
    ):
        result = await scrape_job_url(url)

    mock_httpx.assert_not_called()
    assert "Python Engineer" in result


@pytest.mark.asyncio
async def test_indeed_dach_skips_tier1_and_uses_tier2():
    """Indeed DACH URL must skip Tier 1 and use Tier 2."""
    from applire.services.scraper import scrape_job_url

    url = "https://de.indeed.com/viewjob?jk=abc123def456"
    mock_pw = _mock_playwright_cm(_JOB_HTML_JOB_DESCRIPTION_DIV)

    with (
        patch("httpx.AsyncClient") as mock_httpx,
        patch("playwright.async_api.async_playwright", return_value=mock_pw),
    ):
        result = await scrape_job_url(url)

    mock_httpx.assert_not_called()
    assert "Data Engineer" in result


# ---------------------------------------------------------------------------
# scrape_job_url — Fallback (ScraperError)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_tiers_return_no_text_raises_scraper_error():
    """When both tiers extract no usable text, ScraperError is raised."""
    from applire.services.scraper import ScraperError, scrape_job_url

    url = "https://jobs.example.com/opaque-job"
    mock_client = _mock_httpx_client(_TINY_HTML)
    mock_pw = _mock_playwright_cm(_TINY_HTML)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch("playwright.async_api.async_playwright", return_value=mock_pw),
    ):
        with pytest.raises(ScraperError) as exc_info:
            await scrape_job_url(url)

    assert exc_info.value.url == url


@pytest.mark.asyncio
async def test_playwright_exception_raises_scraper_error():
    """A Playwright runtime error must be wrapped in ScraperError."""
    from applire.services.scraper import ScraperError, scrape_job_url

    url = "https://jobs.example.com/job/99"
    mock_client = _mock_httpx_client(_TINY_HTML)

    broken_cm = MagicMock()
    broken_cm.__aenter__ = AsyncMock(side_effect=RuntimeError("browser not found"))
    broken_cm.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("httpx.AsyncClient", return_value=mock_client),
        patch("playwright.async_api.async_playwright", return_value=broken_cm),
    ):
        with pytest.raises(ScraperError) as exc_info:
            await scrape_job_url(url)

    assert exc_info.value.url == url


# ---------------------------------------------------------------------------
# scrape_job_url — SSRF protection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scrape_rejects_file_scheme():
    from applire.services.scraper import scrape_job_url

    with pytest.raises(ValueError):
        await scrape_job_url("file:///etc/passwd")


@pytest.mark.asyncio
async def test_scrape_rejects_ftp_scheme():
    from applire.services.scraper import scrape_job_url

    with pytest.raises(ValueError):
        await scrape_job_url("ftp://jobs.example.com/job.txt")
