"""Web page content extraction service."""

import asyncio
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Maximum content length to return (characters) to avoid blowing up the AI context window
MAX_CONTENT_LENGTH = 15000


class WebReaderService:
    """Service for fetching and extracting readable text from web pages."""

    async def read_url(self, url: str, timeout: int = 15) -> str:
        """
        Fetch a URL and extract the main readable text content.

        Args:
            url: The URL to fetch
            timeout: Request timeout in seconds

        Returns:
            Extracted text content, or an error message
        """
        try:
            logger.info("fetching_url", url=url[:120])

            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; EmailAIAssistant/1.0)",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            }

            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                verify=True,
            ) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()

            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return f"The URL returned non-HTML content ({content_type}). Cannot extract text."

            text = self._extract_text(response.text)

            if not text or len(text.strip()) < 50:
                return "The page was fetched but contained very little readable text content."

            # Truncate if too long
            if len(text) > MAX_CONTENT_LENGTH:
                text = text[:MAX_CONTENT_LENGTH] + "\n\n[Content truncated — page was very long]"

            logger.info("url_fetched_successfully", url=url[:120], chars=len(text))
            return text

        except httpx.TimeoutException:
            logger.warning("url_fetch_timeout", url=url[:120])
            return f"Timed out trying to fetch the URL after {timeout} seconds."
        except httpx.HTTPStatusError as e:
            logger.warning("url_fetch_http_error", url=url[:120], status=e.response.status_code)
            return f"The URL returned HTTP error {e.response.status_code}."
        except Exception as e:
            logger.error("url_fetch_error", url=url[:120], error=str(e))
            return f"Error fetching URL: {str(e)}"

    def _extract_text(self, html: str) -> str:
        """
        Extract readable text from HTML, removing navigation, ads, scripts, etc.

        Args:
            html: Raw HTML string

        Returns:
            Clean text content
        """
        soup = BeautifulSoup(html, "lxml")

        # Remove non-content elements
        for tag in soup.find_all([
            "script", "style", "nav", "footer", "header",
            "aside", "form", "iframe", "noscript", "svg",
            "button", "input", "select", "textarea",
        ]):
            tag.decompose()

        # Try to find the main content area first
        main_content = (
            soup.find("article")
            or soup.find("main")
            or soup.find("div", {"role": "main"})
            or soup.find("div", class_=lambda c: c and ("content" in c or "article" in c or "post" in c))
        )

        target = main_content if main_content else soup.body if soup.body else soup

        # Extract text with paragraph separation
        lines = []
        for element in target.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "td"]):
            text = element.get_text(separator=" ", strip=True)
            if text and len(text) > 15:  # Skip very short fragments (nav links, etc)
                # Add heading markers
                if element.name and element.name.startswith("h"):
                    text = f"\n## {text}\n"
                elif element.name == "li":
                    text = f"- {text}"
                lines.append(text)

        return "\n".join(lines)
