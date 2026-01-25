"""Basic health check tests."""

import pytest
from fastapi.testclient import TestClient


def test_root_endpoint():
    """Test root endpoint returns expected data."""
    # Note: Full testing requires running services
    # This is a placeholder for integration tests
    pass


def test_email_sanitization():
    """Test email body sanitization."""
    from app.services.email_service import EmailService

    service = EmailService.__new__(EmailService)  # Create without __init__
    service.processed_ids = set()

    # Test signature removal
    body = "Hello world\n\n--\nJohn Doe\nCEO"
    cleaned = service._clean_body(body)
    assert "John Doe" not in cleaned
    assert "Hello world" in cleaned


def test_allowed_senders_parsing():
    """Test whitelist parsing."""
    from app.config import Settings

    # Mock settings with whitelist
    import os
    os.environ["IMAP_USER"] = "test@test.com"
    os.environ["IMAP_PASSWORD"] = "test"
    os.environ["SMTP_USER"] = "test@test.com"
    os.environ["SMTP_PASSWORD"] = "test"
    os.environ["GEMINI_API_KEY"] = "test"
    os.environ["SERPAPI_KEY"] = "test"
    os.environ["POSTGRES_PASSWORD"] = "test"
    os.environ["ALLOWED_SENDERS"] = "a@b.com, c@d.com, E@F.com"

    settings = Settings()
    allowed = settings.allowed_senders_list

    assert len(allowed) == 3
    assert "a@b.com" in allowed
    assert "c@d.com" in allowed
    assert "e@f.com" in allowed  # Should be lowercase
