"""Notion API integration service."""

from notion_client import AsyncClient
from typing import Optional
from datetime import datetime

from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class NotionService:
    """Service for interacting with the Notion API."""

    def __init__(self):
        self.settings = get_settings()
        self.client = None

        if self.settings.notion_api_key and self.settings.notion_database_id:
            try:
                self.client = AsyncClient(auth=self.settings.notion_api_key)
                logger.info("notion_service_initialized")
            except Exception as e:
                logger.error("notion_client_init_failed", error=str(e))
        else:
            logger.info("notion_service_disabled", reason="missing_credentials")

    def is_enabled(self) -> bool:
        """Check if Notion integration is configured and enabled."""
        return self.client is not None

    async def create_task(
        self,
        title: str,
        status: str = "Not started",
        priority: str = "Medium",
        source_email_subject: Optional[str] = None,
    ) -> bool:
        """
        Create a new task in the configured Notion database.

        Args:
            title: The name/title of the task
            status: Task status (e.g., 'To Do', 'In Progress')
            priority: Task priority (e.g., 'High', 'Medium', 'Low')
            source_email_subject: Optional subject of the email that triggered this

        Returns:
            True if successful, False otherwise
        """
        if not self.is_enabled():
            logger.warning("notion_task_creation_skipped", reason="notion_disabled")
            return False

        try:
            properties = {
                "Name": {
                    "title": [
                        {
                            "text": {"content": title}
                        }
                    ]
                },
                "Status": {
                    "status": {"name": status}
                },
                "Priority": {
                    "select": {"name": priority}
                }
            }

            # If sender included, we could shove it in a URL or Text column if it exists.
            # Using a text property named 'Source Email' as defined in the setup guide.
            if source_email_subject:
                properties["Source Email"] = {
                    "rich_text": [
                        {
                            "text": {"content": source_email_subject}
                        }
                    ]
                }

            await self.client.pages.create(
                parent={"database_id": self.settings.notion_database_id},
                properties=properties,
            )

            logger.info("notion_task_created", name=title, priority=priority)
            return True

        except Exception as e:
            logger.error("notion_task_creation_failed", error=str(e), title=title)
            return False
