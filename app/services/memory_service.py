"""PostgreSQL chat memory service."""

from datetime import datetime
from typing import Optional
from sqlalchemy import create_engine, Column, String, Text, DateTime, Integer
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


class MemoryService:
    """Service for PostgreSQL-based chat memory."""

    def __init__(self):
        self.settings = get_settings()
        self.engine = create_engine(
            self.settings.postgres_url,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
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
