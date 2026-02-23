"""Qdrant vector store service for RAG."""

from datetime import datetime
from typing import Optional
import google.generativeai as genai
from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.models import Distance, VectorParams, PointStruct
import uuid

from app.config import get_settings
from app.models.email import EmailConversation
from app.utils.logger import get_logger

logger = get_logger(__name__)

EMBEDDING_DIMENSION = 768  # Gemini embedding dimension


class VectorService:
    """Service for Qdrant vector store operations."""

    def __init__(self):
        self.settings = get_settings()

        # Initialize Gemini for embeddings
        genai.configure(api_key=self.settings.gemini_api_key)

        # Initialize Qdrant client with API key authentication (HTTP, not HTTPS)
        self.client = QdrantClient(
            host=self.settings.qdrant_host,
            port=self.settings.qdrant_port,
            api_key=self.settings.qdrant_api_key if self.settings.qdrant_api_key else None,
            https=False,  # Use HTTP since Qdrant is not configured with SSL
        )

        # Ensure collection exists
        self._ensure_collection()
        logger.info("vector_service_initialized", collection=self.settings.qdrant_collection)

    def _ensure_collection(self) -> None:
        """Ensure the collection exists, create if not."""
        try:
            collections = self.client.get_collections().collections
            collection_names = [c.name for c in collections]

            if self.settings.qdrant_collection not in collection_names:
                self.client.create_collection(
                    collection_name=self.settings.qdrant_collection,
                    vectors_config=VectorParams(
                        size=EMBEDDING_DIMENSION,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info("collection_created", name=self.settings.qdrant_collection)

        except Exception as e:
            logger.error("collection_creation_error", error=str(e))
            raise

    def _get_embedding(self, text: str) -> list[float]:
        """Generate embedding for text using Gemini."""
        try:
            result = genai.embed_content(
                model="models/embedding-001",
                content=text,
                task_type="retrieval_document",
            )
            return result["embedding"]
        except Exception as e:
            logger.error("embedding_error", error=str(e))
            raise

    def _get_query_embedding(self, text: str) -> list[float]:
        """Generate embedding for query using Gemini."""
        try:
            result = genai.embed_content(
                model="models/embedding-001",
                content=text,
                task_type="retrieval_query",
            )
            return result["embedding"]
        except Exception as e:
            logger.error("query_embedding_error", error=str(e))
            raise

    def store_conversation(self, conversation: EmailConversation) -> bool:
        """
        Store a conversation in the vector store.

        Args:
            conversation: EmailConversation object to store

        Returns:
            True if successful
        """
        try:
            # Generate embedding
            embedding = self._get_embedding(conversation.page_content)

            # Create point
            point = PointStruct(
                id=str(uuid.uuid4()),
                vector=embedding,
                payload={
                    "page_content": conversation.page_content,
                    **conversation.metadata,
                },
            )

            # Upsert to Qdrant
            self.client.upsert(
                collection_name=self.settings.qdrant_collection,
                points=[point],
            )

            logger.info(
                "conversation_stored",
                sender=conversation.sender_email,
                message_id=conversation.message_id,
            )
            return True

        except Exception as e:
            logger.error("store_conversation_error", error=str(e))
            return False

    def search_similar(
        self,
        query: str,
        limit: int = 5,
        sender_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        Search for similar conversations.

        Args:
            query: Search query
            limit: Maximum results to return
            sender_filter: Optional filter by sender email

        Returns:
            List of similar conversations with scores
        """
        try:
            # Generate query embedding
            query_embedding = self._get_query_embedding(query)

            # Build filter if specified
            search_filter = None
            if sender_filter:
                search_filter = models.Filter(
                    must=[
                        models.FieldCondition(
                            key="sender_email",
                            match=models.MatchValue(value=sender_filter),
                        )
                    ]
                )

            # Search
            results = self.client.search(
                collection_name=self.settings.qdrant_collection,
                query_vector=query_embedding,
                limit=limit,
                query_filter=search_filter,
            )

            # Format results
            formatted = []
            for result in results:
                formatted.append({
                    "content": result.payload.get("page_content", ""),
                    "sender_email": result.payload.get("sender_email", ""),
                    "subject": result.payload.get("subject", ""),
                    "timestamp": result.payload.get("timestamp", ""),
                    "score": result.score,
                })

            logger.debug("search_completed", query=query[:50], results=len(formatted))
            return formatted

        except Exception as e:
            logger.error("search_error", query=query[:50], error=str(e))
            return []

    def format_search_results(self, results: list[dict]) -> str:
        """Format search results as readable text for the AI."""
        if not results:
            return "No relevant past conversations found."

        formatted = ["Relevant past conversations:"]
        for i, result in enumerate(results, 1):
            formatted.append(
                f"\n--- Conversation {i} (Relevance: {result['score']:.2f}) ---\n"
                f"Subject: {result['subject']}\n"
                f"Date: {result['timestamp']}\n"
                f"{result['content']}"
            )

        return "\n".join(formatted)

    def get_collection_stats(self) -> dict:
        """Get collection statistics."""
        try:
            info = self.client.get_collection(self.settings.qdrant_collection)
            return {
                "vectors_count": info.vectors_count,
                "points_count": info.points_count,
                "status": info.status.value,
            }
        except Exception as e:
            logger.error("stats_error", error=str(e))
            return {}

    def cleanup_old_vectors(self, cutoff_date_iso: str) -> bool:
        """
        Delete vectors older than the specified cutoff date.
        
        Args:
            cutoff_date_iso: ISO formatted date string to delete before
            
        Returns:
            True if successful
        """
        try:
            # First, check if collection is empty
            stats = self.get_collection_stats()
            if stats.get("points_count", 0) == 0:
                logger.info("vector_cleanup_skipped_empty")
                return True
                
            # Create a filter for dates less than cutoff
            date_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="timestamp",
                        range=models.DatetimeRange(
                            lt=cutoff_date_iso
                        ),
                    )
                ]
            )

            # In Qdrant, deleting by filter
            operation_info = self.client.delete(
                collection_name=self.settings.qdrant_collection,
                points_selector=models.FilterSelector(
                    filter=date_filter
                )
            )
            
            logger.info(
                "vectors_cleaned_up", 
                cutoff=cutoff_date_iso, 
                status=operation_info.status.value
            )
            return True

        except Exception as e:
            logger.error("vector_cleanup_error", error=str(e))
            return False

    def check_connection(self) -> bool:
        """Check if Qdrant connection is healthy."""
        try:
            collections = self.client.get_collections()
            return True
        except Exception as e:
            logger.error("qdrant_health_check_error", error=str(e))
            return False
