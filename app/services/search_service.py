"""Web search service using SerpAPI."""

import httpx
from typing import Optional
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class SearchService:
    """Service for web search operations."""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = "https://serpapi.com/search"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def search(self, query: str, num_results: int = 5) -> list[dict]:
        """
        Perform a web search and return results.

        Args:
            query: Search query string
            num_results: Number of results to return

        Returns:
            List of search result dictionaries
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    self.base_url,
                    params={
                        "api_key": self.settings.serpapi_key,
                        "q": query,
                        "num": num_results,
                        "engine": "google",
                    },
                )
                response.raise_for_status()
                data = response.json()

            organic_results = data.get("organic_results", [])

            results = []
            for result in organic_results[:num_results]:
                results.append({
                    "title": result.get("title", ""),
                    "link": result.get("link", ""),
                    "snippet": result.get("snippet", ""),
                    "position": result.get("position", 0),
                })

            logger.info("search_completed", query=query, num_results=len(results))
            return results

        except Exception as e:
            logger.error("search_error", query=query, error=str(e))
            return []

    def format_results(self, results: list[dict]) -> str:
        """Format search results as readable text."""
        if not results:
            return "No search results found."

        formatted = []
        for i, result in enumerate(results, 1):
            formatted.append(
                f"{i}. {result['title']}\n"
                f"   {result['snippet']}\n"
                f"   Source: {result['link']}"
            )

        return "\n\n".join(formatted)
