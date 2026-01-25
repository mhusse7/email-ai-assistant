# Email AI Assistant

AI-powered email assistant with RAG, web search, and conversation memory. Replaces n8n workflow with a native Python application deployable on Coolify.

## Features

- **IMAP Polling**: Monitors your iCloud email for new messages
- **Gemini AI**: Uses Google's Gemini model for intelligent responses
- **RAG Memory**: Stores conversations in Qdrant vector store for context
- **Chat Memory**: PostgreSQL-based conversation history per sender
- **Web Search**: SerpAPI integration for real-time information
- **Email Threading**: Proper reply threading with In-Reply-To headers
- **Sender Whitelist**: Only responds to approved email addresses
- **Error Notifications**: Email alerts when processing fails

## Quick Start

### Local Development

1. Copy environment file:
```bash
cp .env.example .env
```

2. Edit `.env` with your credentials

3. Start with Docker Compose:
```bash
docker-compose up -d
```

4. Check health:
```bash
curl http://localhost:8000/health
```

## Coolify Deployment

### Option 1: Standalone App (Recommended)

If you already have PostgreSQL and Qdrant running on Coolify:

1. Create a new **Docker** project in Coolify
2. Point to your Git repository
3. Set environment variables in Coolify:

| Variable | Description |
|----------|-------------|
| `IMAP_HOST` | imap.mail.me.com |
| `IMAP_PORT` | 993 |
| `IMAP_USER` | hussem5@icloud.com |
| `IMAP_PASSWORD` | Your app-specific password |
| `SMTP_HOST` | smtp.mail.me.com |
| `SMTP_PORT` | 587 |
| `SMTP_USER` | hussem5@icloud.com |
| `SMTP_PASSWORD` | Your app-specific password |
| `ALLOWED_SENDERS` | email1@example.com,email2@example.com |
| `GEMINI_API_KEY` | Your Gemini API key |
| `SERPAPI_KEY` | Your SerpAPI key |
| `POSTGRES_HOST` | Your Coolify Postgres hostname |
| `POSTGRES_PORT` | 5432 |
| `POSTGRES_DB` | email_assistant |
| `POSTGRES_USER` | postgres |
| `POSTGRES_PASSWORD` | Your Postgres password |
| `QDRANT_HOST` | Your Coolify Qdrant hostname |
| `QDRANT_PORT` | 6333 |
| `QDRANT_COLLECTION` | email_conversations |
| `POLLING_INTERVAL_SECONDS` | 30 |
| `ERROR_NOTIFICATION_EMAIL` | Your email for error alerts |

4. Deploy

### Option 2: Full Stack

Use `docker-compose.coolify.yml` to deploy with its own PostgreSQL and Qdrant.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | App info |
| `/health` | GET | Health check |
| `/stats` | GET | Statistics |
| `/poll` | POST | Trigger email check |
| `/test-email` | POST | Send test email |

## Architecture

```
┌─────────────────┐     ┌──────────────┐
│  iCloud Email   │────▶│ IMAP Polling │
└─────────────────┘     └──────┬───────┘
                               │
                               ▼
                     ┌─────────────────┐
                     │  Email Service  │
                     │  (Sanitize)     │
                     └────────┬────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │    AI Agent      │
                    │   (Gemini)       │
                    └────────┬─────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ Qdrant RAG    │  │ PostgreSQL      │  │ SerpAPI         │
│ (Past Emails) │  │ (Chat Memory)   │  │ (Web Search)    │
└───────────────┘  └─────────────────┘  └─────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │  SMTP Reply     │
                    └─────────────────┘
```

## iCloud App-Specific Password

1. Go to https://appleid.apple.com
2. Sign in and go to Security
3. Under App-Specific Passwords, click "Generate Password"
4. Use this password for IMAP_PASSWORD and SMTP_PASSWORD

## Improvements Over n8n Version

1. **Deduplication**: Prevents processing the same email twice
2. **Rate Limiting**: Built-in retry logic with exponential backoff
3. **Email Threading**: Proper In-Reply-To and References headers
4. **Health Checks**: Coolify-compatible health endpoint
5. **Structured Logging**: JSON logs for easier debugging
6. **Connection Pooling**: Efficient database connections
7. **Error Recovery**: Graceful error handling with notifications
8. **Statistics API**: Monitor conversations and vector store
