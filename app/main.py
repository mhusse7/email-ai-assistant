"""Email AI Assistant - FastAPI Application."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.models.email import EmailMessage, EmailConversation
from app.services.email_service import EmailService
from app.services.search_service import SearchService
from app.services.memory_service import MemoryService
from app.services.vector_service import VectorService
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
    try:
        logger.info(
            "processing_email",
            sender=email_msg.sender_email,
            subject=email_msg.subject,
        )

        # Generate AI response
        response = await ai_service.process_email(email_msg)

        # Send reply
        email_service.send_reply(
            to_email=email_msg.sender_email,
            subject=email_msg.subject,
            body=response,
            in_reply_to=email_msg.message_id,
            references=email_msg.references,
        )

        # Store conversation in vector store
        conversation = EmailConversation(
            user_query=email_msg.body,
            assistant_response=response,
            sender_email=email_msg.sender_email,
            subject=email_msg.subject,
            message_id=email_msg.message_id,
        )
        vector_service.store_conversation(conversation)

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

            # Fetch new emails (runs in thread pool since IMAP is sync)
            loop = asyncio.get_event_loop()
            emails = await loop.run_in_executor(None, email_service.fetch_new_emails)

            if not emails:
                logger.debug("no_new_emails")
                return

            logger.info("found_new_emails", count=len(emails))

            # Process each email
            for email_msg in emails:
                await process_single_email(email_msg)

        except Exception as e:
            logger.error("polling_error", error=str(e))


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
    ai_service = AIService(
        search_service=search_service,
        memory_service=memory_service,
        vector_service=vector_service,
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


@app.get("/health")
async def health_check():
    """Health check endpoint for Coolify."""
    try:
        settings = get_settings()

        # Check services
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


@app.get("/stats")
async def get_stats():
    """Get application statistics."""
    try:
        vector_stats = vector_service.get_collection_stats() if vector_service else {}
        session_count = memory_service.get_session_count() if memory_service else 0

        return {
            "vector_store": vector_stats,
            "memory_sessions": session_count,
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
