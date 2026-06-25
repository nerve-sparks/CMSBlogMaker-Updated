import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

MAX_URLS = 5
MAX_CHARS_PER_URL = 3000
REQUEST_TIMEOUT = 10

_SKIP_TAGS = ["script", "style", "nav", "footer", "header", "aside",
              "noscript", "form", "iframe", "advertisement"]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _fetch_url(url: str) -> str:
    """Fetch a URL and return its main text content. Returns empty string on any failure."""
    try:
        resp = requests.get(url.strip(), headers=_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        for tag in soup(_SKIP_TAGS):
            tag.decompose()

        # Try to get main content area first, fall back to body
        main = (
            soup.find("article")
            or soup.find("main")
            or soup.find(id="content")
            or soup.find(class_="content")
            or soup.find(class_="post-content")
            or soup.body
        )

        if not main:
            return ""

        text = " ".join(main.get_text(separator=" ").split())
        return text[:MAX_CHARS_PER_URL]

    except Exception as e:
        logger.warning(f"[REFERENCE] Could not fetch {url}: {e}")
        return ""


def fetch_reference_content(urls_str: str) -> str:
    """
    Takes a comma-separated string of URLs, fetches each one,
    extracts main text, and returns a formatted context block for the LLM.
    Silently skips any URL that fails.
    """
    if not urls_str or not urls_str.strip():
        return ""

    urls = [u.strip() for u in urls_str.split(",") if u.strip()][:MAX_URLS]
    if not urls:
        return ""

    sections = []
    for url in urls:
        content = _fetch_url(url)
        if content:
            sections.append(f'Source ({url}):\n"""\n{content}\n"""')
            logger.info(f"[REFERENCE] Fetched {len(content)} chars from {url}")
        else:
            logger.warning(f"[REFERENCE] Skipped {url} — could not fetch content")

    if not sections:
        return ""

    return "\n\n".join(sections)
