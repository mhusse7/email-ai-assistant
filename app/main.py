"""Email AI Assistant - FastAPI Application."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from imapclient import IMAPClient

from app.config import get_settings
from app.models.email import EmailMessage, EmailConversation
from app.services.email_service import EmailService
from app.services.search_service import SearchService
from app.services.memory_service import MemoryService
from app.services.vector_service import VectorService
from app.services.notion_service import NotionService
from app.services.ai_service import AIService
from app.utils.logger import setup_logging, get_logger

# Initialize logging
setup_logging()
logger = get_logger(__name__)

# Global services
email_service: EmailService = None
search_service: SearchService = None
memory_service: MemoryService = None
vector_service: VectorService = None
ai_service: AIService = None
scheduler: AsyncIOScheduler = None

# Processing lock to prevent concurrent email processing
processing_lock = asyncio.Lock()


async def process_single_email(email_msg: EmailMessage) -> None:
    """Process a single email message."""
    response: Optional[str] = None

    try:
        logger.info(
            "processing_email",
            sender=email_msg.sender_email,
            subject=email_msg.subject,
        )

        # Generate AI response
        response = await ai_service.process_email(email_msg)

        # Try to send reply
        try:
            email_service.send_reply(
                to_email=email_msg.sender_email,
                subject=email_msg.subject,
                body=response,
                in_reply_to=email_msg.message_id,
                references=email_msg.references,
            )
        except Exception as send_error:
            # Queue for retry if send fails
            logger.error(
                "send_failed_queuing_retry",
                to_email=email_msg.sender_email,
                error=str(send_error),
            )
            memory_service.queue_failed_email(
                to_email=email_msg.sender_email,
                subject=email_msg.subject,
                body=response,
                error_message=str(send_error),
                in_reply_to=email_msg.message_id,
                references=email_msg.references,
            )
            # Still store to vector store since we generated a response

        # Store conversation in vector store
        conversation = EmailConversation(
            user_query=email_msg.body,
            assistant_response=response,
            sender_email=email_msg.sender_email,
            subject=email_msg.subject,
            message_id=email_msg.message_id,
        )
        vector_service.store_conversation(conversation)

        # Mark as processed in database
        msg_hash = EmailService.get_message_hash(email_msg.message_id)
        memory_service.mark_email_processed(
            message_hash=msg_hash,
            message_id=email_msg.message_id,
            sender_email=email_msg.sender_email,
        )

        logger.info(
            "email_processed_successfully",
            sender=email_msg.sender_email,
            message_id=email_msg.message_id,
        )

    except Exception as e:
        logger.error(
            "email_processing_failed",
            sender=email_msg.sender_email,
            error=str(e),
        )
        email_service.send_error_notification(
            error=str(e),
            context={
                "step": "email_processing_pipeline",
                "sender": email_msg.sender_email,
                "subject": email_msg.subject,
                "message_id": email_msg.message_id,
            },
        )


async def poll_emails() -> None:
    """Poll for new emails and process them."""
    async with processing_lock:
        try:
            logger.debug("polling_emails")

            # Fetch new emails (runs in thread pool since IMAP is sync, wrapped with hard timeout)
            loop = asyncio.get_event_loop()
            try:
                emails = await asyncio.wait_for(
                    loop.run_in_executor(None, email_service.fetch_new_emails),
                    timeout=120.0
                )
            except asyncio.TimeoutError:
                logger.error("imap_polling_timeout", error="IMAP sync execution exceeded 120s timeout")
                return

            if not emails:
                logger.debug("no_new_emails")
                return

            logger.info("found_new_emails", count=len(emails))

            # Process each email (with deduplication check)
            for email_msg in emails:
                # Check if already processed (persistent deduplication)
                msg_hash = EmailService.get_message_hash(email_msg.message_id)
                if memory_service.is_email_processed(msg_hash):
                    logger.debug(
                        "skipping_already_processed",
                        message_id=email_msg.message_id,
                    )
                    continue

                await process_single_email(email_msg)

        except Exception as e:
            logger.error("polling_error", error=str(e))


async def retry_failed_emails() -> None:
    """Retry sending failed emails."""
    try:
        failed_emails = memory_service.get_failed_emails(max_retries=5, limit=5)

        if not failed_emails:
            return

        logger.info("retrying_failed_emails", count=len(failed_emails))

        for failed in failed_emails:
            try:
                email_service.send_reply(
                    to_email=failed["to_email"],
                    subject=failed["subject"],
                    body=failed["body"],
                    in_reply_to=failed["in_reply_to"],
                    references=failed["references"],
                )
                memory_service.mark_email_retry_attempted(failed["id"], success=True)
                logger.info("retry_success", to_email=failed["to_email"])

            except Exception as e:
                memory_service.mark_email_retry_attempted(
                    failed["id"],
                    success=False,
                    error=str(e)
                )
                logger.warning(
                    "retry_failed",
                    to_email=failed["to_email"],
                    retry_count=failed["retry_count"] + 1,
                    error=str(e),
                )

    except Exception as e:
        logger.error("retry_job_error", error=str(e))


async def cleanup_vector_store() -> None:
    """Run weekly cleanup of old vector store points."""
    try:
        logger.info("running_vector_store_cleanup")
        if vector_service:
            # Delete points older than 90 days
            from datetime import datetime, timedelta
            cutoff_date = (datetime.utcnow() - timedelta(days=90)).isoformat()
            
            # This logic assumes the Qdrant service has an implemented cleanup function.
            # If not implemented, we would call a cleanup method here.
            # We'll rely on a placeholder implementation or add it to VectorService
            success = vector_service.cleanup_old_vectors(cutoff_date)
            logger.info("vector_store_cleanup_complete", success=success)
    except Exception as e:
        logger.error("vector_cleanup_error", error=str(e))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global email_service, search_service, memory_service, vector_service, ai_service, scheduler

    settings = get_settings()
    logger.info("starting_application")

    # Initialize services
    email_service = EmailService()
    search_service = SearchService()
    memory_service = MemoryService()
    vector_service = VectorService()
    notion_service = NotionService()  # New service
    ai_service = AIService(
        search_service=search_service,
        memory_service=memory_service,
        vector_service=vector_service,
        notion_service=notion_service,
    )

    # Setup scheduler for email polling
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_emails,
        trigger=IntervalTrigger(seconds=settings.polling_interval_seconds),
        id="email_polling",
        name="Poll for new emails",
        replace_existing=True,
    )

    # Add retry job for failed emails (every 5 minutes)
    scheduler.add_job(
        retry_failed_emails,
        trigger=IntervalTrigger(minutes=5),
        id="retry_failed_emails",
        name="Retry failed email sends",
        replace_existing=True,
    )

    # Add vector store cleanup job (weekly)
    scheduler.add_job(
        cleanup_vector_store,
        trigger=IntervalTrigger(days=7),
        id="vector_store_cleanup",
        name="Cleanup old vectors",
        replace_existing=True,
    )

    scheduler.start()

    logger.info(
        "scheduler_started",
        interval_seconds=settings.polling_interval_seconds,
    )

    yield

    # Shutdown
    logger.info("shutting_down")
    if scheduler:
        scheduler.shutdown(wait=False)


# Create FastAPI app
app = FastAPI(
    title="Email AI Assistant",
    description="AI-powered email assistant with RAG and web search",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Email AI Assistant",
        "status": "running",
        "version": "1.0.0",
    }


def check_imap_connection() -> bool:
    """Check IMAP server connectivity."""
    try:
        settings = get_settings()
        with IMAPClient(settings.imap_host, port=settings.imap_port, ssl=True, timeout=5) as client:
            client.login(settings.imap_user, settings.imap_password)
            client.logout()
        return True
    except Exception as e:
        logger.warning("imap_health_check_failed", error=str(e))
        return False


@app.get("/health")
async def health_check():
    """Health check endpoint for Coolify."""
    try:
        settings = get_settings()

        # Basic service checks
        checks = {
            "email_service": email_service is not None,
            "ai_service": ai_service is not None,
            "memory_service": memory_service is not None,
            "vector_service": vector_service is not None,
            "scheduler_running": scheduler is not None and scheduler.running,
        }

        all_healthy = all(checks.values())

        if not all_healthy:
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", "checks": checks},
            )

        return {
            "status": "healthy",
            "checks": checks,
            "timestamp": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "error": str(e)},
        )


@app.get("/health/deep")
async def deep_health_check():
    """Deep health check that verifies all external dependencies."""
    try:
        settings = get_settings()
        loop = asyncio.get_event_loop()

        # Check PostgreSQL
        postgres_ok = memory_service.check_connection() if memory_service else False

        # Check Qdrant
        qdrant_ok = vector_service.check_connection() if vector_service else False

        # Check IMAP (in thread pool since it's sync)
        imap_ok = await loop.run_in_executor(None, check_imap_connection)

        checks = {
            "postgres": postgres_ok,
            "qdrant": qdrant_ok,
            "imap": imap_ok,
            "scheduler_running": scheduler is not None and scheduler.running,
        }

        failed_emails = memory_service.get_failed_email_count() if memory_service else 0

        all_healthy = all(checks.values())

        response_data = {
            "status": "healthy" if all_healthy else "degraded",
            "checks": checks,
            "failed_email_queue": failed_emails,
            "timestamp": datetime.utcnow().isoformat(),
        }

        if not all_healthy:
            return JSONResponse(status_code=503, content=response_data)

        return response_data

    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "error": str(e)},
        )


@app.get("/stats")
async def get_stats():
    """Get application statistics."""
    try:
        vector_stats = vector_service.get_collection_stats() if vector_service else {}
        session_count = memory_service.get_session_count() if memory_service else 0
        failed_email_count = memory_service.get_failed_email_count() if memory_service else 0

        return {
            "vector_store": vector_stats,
            "memory_sessions": session_count,
            "failed_email_queue": failed_email_count,
            "scheduler_running": scheduler.running if scheduler else False,
            "timestamp": datetime.utcnow().isoformat(),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/poll")
async def trigger_poll(background_tasks: BackgroundTasks):
    """Manually trigger email polling."""
    background_tasks.add_task(poll_emails)
    return {"message": "Email polling triggered"}


@app.post("/test-email")
async def test_email(to_email: str, subject: str = "Test", body: str = "This is a test email."):
    """Send a test email."""
    try:
        success = email_service.send_reply(
            to_email=to_email,
            subject=subject,
            body=body,
        )
        return {"success": success, "to": to_email}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level=settings.log_level.lower(),
    )
