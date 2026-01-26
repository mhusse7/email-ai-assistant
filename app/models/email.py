"""Email data models."""

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional


class EmailAttachment(BaseModel):
    """Represents an email attachment."""

    filename: str
    content_type: str  # e.g., "image/png", "image/jpeg"
    data: bytes  # Raw binary data

    class Config:
        # Allow arbitrary types for bytes
        arbitrary_types_allowed = True


class EmailMessage(BaseModel):
    """Represents a sanitized email message."""

    message_id: str
    sender_email: str
    subject: str
    body: str
    received_at: datetime = Field(default_factory=datetime.utcnow)
    in_reply_to: Optional[str] = None
    references: Optional[str] = None
    attachments: list[EmailAttachment] = Field(default_factory=list)

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}
        arbitrary_types_allowed = True


class EmailConversation(BaseModel):
    """Represents a conversation to store in vector DB."""

    user_query: str
    assistant_response: str
    sender_email: str
    subject: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    message_id: str

    @property
    def page_content(self) -> str:
        """Format for vector storage."""
        return f"User: {self.user_query}\n\nAssistant: {self.assistant_response}"

    @property
    def metadata(self) -> dict:
        """Metadata for vector storage."""
        return {
            "sender_email": self.sender_email,
            "subject": self.subject,
            "timestamp": self.timestamp.isoformat(),
            "message_id": self.message_id,
        }


class ProcessedEmail(BaseModel):
    """Result of processing an email."""

    original: EmailMessage
    response: str
    success: bool
    error: Optional[str] = None
