"""Email service for IMAP polling and SMTP sending."""

import email
import re
import smtplib
import hashlib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import parseaddr
from typing import Optional
from imapclient import IMAPClient
from tenacity import retry, stop_after_attempt, wait_exponential
import html2text

from app.config import get_settings
from app.models.email import EmailMessage, EmailAttachment
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Supported image MIME types
SUPPORTED_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/gif",
    "image/webp",
    "image/heic",
    "image/heif",
}

# Max image size (10MB)
MAX_IMAGE_SIZE = 10 * 1024 * 1024


class EmailService:
    """Service for email operations."""

    def __init__(self):
        self.settings = get_settings()
        self.h2t = html2text.HTML2Text()
        self.h2t.ignore_links = False
        self.h2t.ignore_images = True

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def fetch_new_emails(self) -> list[EmailMessage]:
        """Fetch unread emails from IMAP server."""
        emails = []

        try:
            with IMAPClient(self.settings.imap_host, port=self.settings.imap_port, ssl=True, timeout=30) as client:
                client.login(self.settings.imap_user, self.settings.imap_password)
                client.select_folder("INBOX")

                # Search for unseen messages
                message_ids = client.search(["UNSEEN"])
                logger.info("found_emails", count=len(message_ids))

                if not message_ids:
                    return emails

                # Fetch messages - use BODY.PEEK[] to avoid marking as read prematurely
                messages = client.fetch(message_ids, ["BODY.PEEK[]", "FLAGS"])

                for msg_id, data in messages.items():
                    try:
                        # Handle different key formats from IMAPClient
                        raw_email = (
                            data.get(b"BODY[]") or
                            data.get(b"RFC822") or
                            data.get("BODY[]") or
                            data.get("RFC822")
                        )
                        if raw_email is None:
                            # Try to find any key that contains the email body
                            for key in data.keys():
                                key_str = str(key)
                                if b"BODY" in key if isinstance(key, bytes) else "BODY" in key:
                                    raw_email = data[key]
                                    break
                        if raw_email is None:
                            logger.warning("no_email_body", msg_id=msg_id, keys=[str(k) for k in data.keys()])
                            continue
                        parsed = email.message_from_bytes(raw_email)

                        email_msg = self._parse_email(parsed, msg_id)
                        if email_msg and self._should_process(email_msg):
                            emails.append(email_msg)
                            # Mark as seen
                            client.add_flags([msg_id], [b"\\Seen"])

                    except Exception as e:
                        logger.error("email_parse_error", msg_id=msg_id, error=str(e))
                        continue

        except Exception as e:
            logger.error("imap_connection_error", error=str(e))
            raise

        return emails

    def _parse_email(self, msg: email.message.Message, uid: int) -> Optional[EmailMessage]:
        """Parse an email message into our model."""
        try:
            # Get sender
            from_header = msg.get("From", "")
            _, sender_email = parseaddr(from_header)
            sender_email = sender_email.lower().strip()

            # Get subject
            subject = msg.get("Subject", "No Subject")
            if isinstance(subject, bytes):
                subject = subject.decode("utf-8", errors="ignore")

            # Get message ID
            message_id = msg.get("Message-ID", f"uid-{uid}-{datetime.utcnow().timestamp()}")

            # Get threading headers
            in_reply_to = msg.get("In-Reply-To")
            references = msg.get("References")

            # Get body
            body = self._extract_body(msg)

            # Extract image attachments
            attachments = self._extract_image_attachments(msg)

            # Allow emails with just images (no text body required if images present)
            if (not body or len(body.strip()) < 2) and not attachments:
                logger.debug("empty_email_body", sender=sender_email)
                return None

            # Clean the body (or set default if only images)
            if body:
                body = self._clean_body(body)
            else:
                body = "[Image attachment(s) - please analyze]"

            return EmailMessage(
                message_id=message_id,
                sender_email=sender_email,
                subject=subject,
                body=body,
                in_reply_to=in_reply_to,
                references=references,
                attachments=attachments,
            )

        except Exception as e:
            logger.error("email_parse_error", error=str(e))
            return None

    def _extract_image_attachments(self, msg: email.message.Message) -> list[EmailAttachment]:
        """Extract image attachments from email."""
        attachments = []

        if not msg.is_multipart():
            return attachments

        for part in msg.walk():
            content_type = part.get_content_type()

            # Check if it's a supported image type
            if content_type not in SUPPORTED_IMAGE_TYPES:
                continue

            # Get filename
            filename = part.get_filename()
            if not filename:
                # Generate a filename if none provided
                ext = content_type.split("/")[-1]
                filename = f"image.{ext}"

            # Get the image data
            payload = part.get_payload(decode=True)
            if not payload:
                continue

            # Check size limit
            if len(payload) > MAX_IMAGE_SIZE:
                logger.warning(
                    "image_too_large",
                    filename=filename,
                    size=len(payload),
                    max_size=MAX_IMAGE_SIZE,
                )
                continue

            attachments.append(
                EmailAttachment(
                    filename=filename,
                    content_type=content_type,
                    data=payload,
                )
            )
            logger.info("image_attachment_extracted", filename=filename, size=len(payload))

        return attachments

    def _extract_body(self, msg: email.message.Message) -> str:
        """Extract text body from email message."""
        body = ""

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition", ""))

                if "attachment" in content_disposition:
                    continue

                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body = payload.decode("utf-8", errors="ignore")
                        break
                elif content_type == "text/html" and not body:
                    payload = part.get_payload(decode=True)
                    if payload:
                        html_content = payload.decode("utf-8", errors="ignore")
                        body = self.h2t.handle(html_content)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                content_type = msg.get_content_type()
                if content_type == "text/html":
                    body = self.h2t.handle(payload.decode("utf-8", errors="ignore"))
                else:
                    body = payload.decode("utf-8", errors="ignore")

        return body

    def _clean_body(self, body: str) -> str:
        """Clean and sanitize email body."""
        # Remove signatures
        body = re.sub(r"--\s*\n[\s\S]*$", "", body)
        body = re.sub(r"Sent from my \w+", "", body, flags=re.IGNORECASE)

        # Remove quoted replies
        body = re.sub(r"On .* wrote:[\s\S]*", "", body, flags=re.IGNORECASE)
        body = re.sub(r">.*\n", "", body)

        # Normalize whitespace
        body = re.sub(r"\s+", " ", body).strip()

        # Truncate if needed
        if len(body) > 30000:
            body = body[:30000] + "... [truncated]"

        return body

    def _should_process(self, email_msg: EmailMessage) -> bool:
        """Check if email should be processed (basic checks only)."""
        # Skip own emails (prevent loops)
        if self.settings.imap_user.lower() in email_msg.sender_email:
            logger.debug("skipping_own_email", sender=email_msg.sender_email)
            return False

        # Check whitelist
        allowed = self.settings.allowed_senders_list
        if allowed and email_msg.sender_email not in allowed:
            logger.info("sender_not_whitelisted", sender=email_msg.sender_email)
            return False

        return True

    @staticmethod
    def get_message_hash(message_id: str) -> str:
        """Generate a hash for deduplication."""
        return hashlib.md5(message_id.encode()).hexdigest()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def send_reply(
        self,
        to_email: str,
        subject: str,
        body: str,
        in_reply_to: Optional[str] = None,
        references: Optional[str] = None,
    ) -> bool:
        """Send an email reply via SMTP."""
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self.settings.smtp_user
            msg["To"] = to_email
            msg["Subject"] = f"Re: {subject}" if not subject.startswith("Re:") else subject

            # Threading headers for proper email thread grouping
            if in_reply_to:
                msg["In-Reply-To"] = in_reply_to
            if references:
                msg["References"] = f"{references} {in_reply_to}" if in_reply_to else references
            elif in_reply_to:
                msg["References"] = in_reply_to

            # Format body as HTML
            html_body = self._format_html_response(body)
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port) as server:
                server.starttls()
                server.login(self.settings.smtp_user, self.settings.smtp_password)
                server.send_message(msg)

            logger.info("email_sent", to=to_email, subject=subject)
            return True

        except Exception as e:
            logger.error("smtp_send_error", to=to_email, error=str(e))
            raise

    def _format_html_response(self, body: str) -> str:
        """Format response as HTML email."""
        # Convert markdown-like formatting
        html_body = body
        html_body = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", html_body)
        html_body = re.sub(r"\*(.*?)\*", r"<em>\1</em>", html_body)
        html_body = html_body.replace("\n", "<br>")

        return f"""
        <div style="font-family: Arial, sans-serif; line-height: 1.6; max-width: 600px;">
            {html_body}
            <hr style="border: none; border-top: 1px solid #ccc; margin: 20px 0;">
            <p style="color: #888; font-size: 12px;">AI Assistant</p>
        </div>
        """

    def send_error_notification(self, error: str, context: dict) -> None:
        """Send error notification email."""
        if not self.settings.error_notification_email:
            return

        try:
            subject = "Email AI Assistant Error"
            body = f"""
Error in Email AI Assistant:

{error}

Context:
{context}

Time: {datetime.utcnow().isoformat()}
            """

            msg = MIMEText(body)
            msg["From"] = self.settings.smtp_user
            msg["To"] = self.settings.error_notification_email
            msg["Subject"] = subject

            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port) as server:
                server.starttls()
                server.login(self.settings.smtp_user, self.settings.smtp_password)
                server.send_message(msg)

        except Exception as e:
            logger.error("error_notification_failed", error=str(e))
