"""PostgreSQL chat memory service."""

from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, Column, String, Text, DateTime, Integer, Boolean, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import QueuePool

from app.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

Base = declarative_base()


class ChatMessage(Base):
    """Chat message storage model."""

    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(255), index=True, nullable=False)
    role = Column(String(50), nullable=False)  # 'user' or 'assistant'
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class ProcessedEmail(Base):
    """Track processed email IDs to prevent duplicates."""

    __tablename__ = "processed_emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_hash = Column(String(64), unique=True, index=True, nullable=False)
    message_id = Column(String(512), nullable=False)
    sender_email = Column(String(255), nullable=False)
    processed_at = Column(DateTime, default=datetime.utcnow)


class FailedEmail(Base):
    """Queue for emails that failed to send."""

    __tablename__ = "failed_emails"

    id = Column(Integer, primary_key=True, autoincrement=True)
    to_email = Column(String(255), nullable=False)
    subject = Column(String(512), nullable=False)
    body = Column(Text, nullable=False)
    in_reply_to = Column(String(512), nullable=True)
    references = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_retry_at = Column(DateTime, nullable=True)
    resolved = Column(Boolean, default=False)


class MemoryService:
    """Service for PostgreSQL-based chat memory."""

    def __init__(self):
        self.settings = get_settings()
        self.engine = create_engine(
            self.settings.postgres_url,
            poolclass=QueuePool,
            pool_size=20,
            max_overflow=40,
            pool_timeout=30,
            pool_pre_ping=True,
        )
        self.Session = sessionmaker(bind=self.engine)

        # Create tables
        Base.metadata.create_all(self.engine)
        logger.info("memory_service_initialized")

    def get_conversation_history(
        self,
        session_id: str,
        limit: int = 10
    ) -> list[dict]:
        """
        Get conversation history for a session.

        Args:
            session_id: Session identifier (usually sender email)
            limit: Maximum number of message pairs to return

        Returns:
            List of message dictionaries with role and content
        """
        try:
            with self.Session() as session:
                messages = (
                    session.query(ChatMessage)
                    .filter(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(limit * 2)  # Get pairs
                    .all()
                )

                # Reverse to get chronological order
                messages = list(reversed(messages))

                return [
                    {"role": msg.role, "content": msg.content}
                    for msg in messages
                ]

        except Exception as e:
            logger.error("memory_get_error", session_id=session_id, error=str(e))
            return []

    def add_message(self, session_id: str, role: str, content: str) -> bool:
        """
        Add a message to the conversation history.

        Args:
            session_id: Session identifier
            role: 'user' or 'assistant'
            content: Message content

        Returns:
            True if successful
        """
        try:
            with self.Session() as session:
                message = ChatMessage(
                    session_id=session_id,
                    role=role,
                    content=content,
                )
                session.add(message)
                session.commit()

            logger.debug("message_added", session_id=session_id, role=role)
            return True

        except Exception as e:
            logger.error("memory_add_error", session_id=session_id, error=str(e))
            return False

    def add_conversation(self, session_id: str, user_message: str, assistant_message: str) -> bool:
        """Add both user and assistant messages."""
        try:
            with self.Session() as session:
                user_msg = ChatMessage(
                    session_id=session_id,
                    role="user",
                    content=user_message,
                )
                assistant_msg = ChatMessage(
                    session_id=session_id,
                    role="assistant",
                    content=assistant_message,
                )
                session.add(user_msg)
                session.add(assistant_msg)
                session.commit()

            return True

        except Exception as e:
            logger.error("conversation_add_error", session_id=session_id, error=str(e))
            return False

    def clear_session(self, session_id: str) -> bool:
        """Clear all messages for a session."""
        try:
            with self.Session() as session:
                session.query(ChatMessage).filter(
                    ChatMessage.session_id == session_id
                ).delete()
                session.commit()

            logger.info("session_cleared", session_id=session_id)
            return True

        except Exception as e:
            logger.error("session_clear_error", session_id=session_id, error=str(e))
            return False

    def get_session_count(self) -> int:
        """Get total number of unique sessions."""
        try:
            with self.Session() as session:
                count = session.query(ChatMessage.session_id).distinct().count()
                return count
        except Exception as e:
            logger.error("session_count_error", error=str(e))
            return 0

    # ==================== Deduplication Methods ====================

    def is_email_processed(self, message_hash: str) -> bool:
        """Check if an email has already been processed."""
        try:
            with self.Session() as session:
                exists = (
                    session.query(ProcessedEmail)
                    .filter(ProcessedEmail.message_hash == message_hash)
                    .first()
                )
                return exists is not None
        except Exception as e:
            logger.error("dedup_check_error", error=str(e))
            return False

    def mark_email_processed(
        self,
        message_hash: str,
        message_id: str,
        sender_email: str
    ) -> bool:
        """Mark an email as processed."""
        try:
            with self.Session() as session:
                record = ProcessedEmail(
                    message_hash=message_hash,
                    message_id=message_id,
                    sender_email=sender_email,
                )
                session.add(record)
                session.commit()
            logger.debug("email_marked_processed", message_hash=message_hash)
            return True
        except Exception as e:
            logger.error("dedup_mark_error", error=str(e))
            return False

    # ==================== Failed Email Queue Methods ====================

    def queue_failed_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        error_message: str,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
    ) -> bool:
        """Add a failed email to the retry queue."""
        try:
            with self.Session() as session:
                failed = FailedEmail(
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    in_reply_to=in_reply_to,
                    references=references,
                    error_message=error_message,
                )
                session.add(failed)
                session.commit()
            logger.info("email_queued_for_retry", to_email=to_email)
            return True
        except Exception as e:
            logger.error("queue_failed_email_error", error=str(e))
            return False

    def get_failed_emails(self, max_retries: int = 5, limit: int = 10) -> list[dict]:
        """Get failed emails that should be retried."""
        try:
            with self.Session() as session:
                failed = (
                    session.query(FailedEmail)
                    .filter(
                        FailedEmail.resolved == False,
                        FailedEmail.retry_count < max_retries
                    )
                    .order_by(
                        FailedEmail.retry_count.asc(),
                        FailedEmail.created_at.asc()
                    )
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        "id": f.id,
                        "to_email": f.to_email,
                        "subject": f.subject,
                        "body": f.body,
                        "in_reply_to": f.in_reply_to,
                        "references": f.references,
                        "retry_count": f.retry_count,
                    }
                    for f in failed
                ]
        except Exception as e:
            logger.error("get_failed_emails_error", error=str(e))
            return []

    def mark_email_retry_attempted(self, email_id: int, success: bool, error: Optional[str] = None) -> bool:
        """Update a failed email after retry attempt."""
        try:
            with self.Session() as session:
                failed = session.query(FailedEmail).filter(FailedEmail.id == email_id).first()
                if failed:
                    if success:
                        failed.resolved = True
                    else:
                        failed.retry_count += 1
                        failed.error_message = error
                    failed.last_retry_at = datetime.utcnow()
                    session.commit()
            return True
        except Exception as e:
            logger.error("mark_retry_error", error=str(e))
            return False

    def get_failed_email_count(self) -> int:
        """Get count of unresolved failed emails."""
        try:
            with self.Session() as session:
                return session.query(FailedEmail).filter(FailedEmail.resolved == False).count()
        except Exception as e:
            logger.error("failed_count_error", error=str(e))
            return 0

    # ==================== Health Check Methods ====================

    def check_connection(self) -> bool:
        """Check if database connection is healthy."""
        try:
            with self.Session() as session:
                session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error("db_health_check_error", error=str(e))
            return False
