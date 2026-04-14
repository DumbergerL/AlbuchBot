"""
Send email digest with improved logging and error handling.

Usage:
  python agent/send_email_digest.py \
    --subject output/email_subject.txt \
    --body output/email_body.txt \
    --attachments output/news.json
"""

from __future__ import annotations

import argparse
import logging
import os
import smtplib
import socket
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from pathlib import Path
from typing import Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("albuchbot.send_email")


class EmailConfig:
    """Email configuration from environment variables."""

    def __init__(self):
        self.server = os.getenv("MAIL_SERVER", "").strip()
        self.port_str = os.getenv("MAIL_PORT", "587").strip()
        self.user = os.getenv("MAIL_USER", "").strip()
        self.password = os.getenv("MAIL_PASS", "").strip()
        self.to = os.getenv("MAIL_TO", "").strip()
        self.from_addr = os.getenv("MAIL_FROM", "").strip() or self.user

    def validate(self) -> tuple[bool, str]:
        """Validate configuration and return (is_valid, error_message)."""
        if not self.server:
            return False, "MAIL_SERVER is not set or empty"
        if not self.port_str.isdigit():
            return False, f"MAIL_PORT is not a valid number: {self.port_str}"
        if not self.user:
            return False, "MAIL_USER is not set or empty"
        if not self.password:
            return False, "MAIL_PASS is not set or empty"
        if not self.to:
            return False, "MAIL_TO is not set or empty"
        if not self.from_addr:
            return False, "MAIL_FROM and MAIL_USER are both empty"
        return True, ""

    def check_dns(self) -> tuple[bool, str]:
        """Check if server hostname can be resolved."""
        logger.info(f"Resolving hostname: {self.server}")
        try:
            socket.gethostbyname(self.server)
            logger.info(f"✓ Hostname resolved: {self.server}")
            return True, ""
        except socket.gaierror as e:
            error_msg = f"DNS resolution failed for '{self.server}': {e}"
            logger.error(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected error resolving '{self.server}': {e}"
            logger.error(error_msg)
            return False, error_msg

    @property
    def port(self) -> int:
        """Get port as integer."""
        return int(self.port_str)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send email digest with Python SMTP")
    parser.add_argument("--subject", required=True, help="Path to subject text file")
    parser.add_argument("--body", required=True, help="Path to body text file")
    parser.add_argument(
        "--attachments",
        nargs="+",
        default=[],
        help="Paths to attachment files",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="SMTP timeout in seconds",
    )
    return parser.parse_args()


def read_file(path: Path) -> str:
    """Read file content with UTF-8 encoding."""
    try:
        content = path.read_text(encoding="utf-8")
        logger.info(f"Read file: {path} ({len(content)} chars)")
        return content
    except FileNotFoundError:
        logger.error(f"File not found: {path}")
        raise
    except Exception as e:
        logger.error(f"Error reading {path}: {e}")
        raise


def send_email(
    config: EmailConfig,
    subject: str,
    body: str,
    attachment_paths: list[str],
    timeout: int,
) -> None:
    """Send email via SMTP."""
    logger.info(f"Connecting to {config.server}:{config.port}...")

    try:
        # Create SMTP connection
        server = smtplib.SMTP(config.server, config.port, timeout=timeout)
        server.starttls()
        logger.info("Connected, starting TLS...")

        # Login
        logger.info(f"Authenticating as {config.user}...")
        server.login(config.user, config.password)
        logger.info("Authentication successful")

        # Build message
        msg = MIMEMultipart()
        msg["From"] = config.from_addr
        msg["To"] = config.to
        msg["Subject"] = subject.strip()

        # Add body
        msg.attach(MIMEText(body, "plain", "utf-8"))
        logger.info("Email body attached")

        # Add attachments
        for attachment_path in attachment_paths:
            path = Path(attachment_path)
            if not path.exists():
                logger.warning(f"Attachment not found, skipping: {path}")
                continue

            try:
                with open(path, "rb") as attachment:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(attachment.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f"attachment; filename= {path.name}")
                    msg.attach(part)
                logger.info(f"Attachment added: {path.name}")
            except Exception as e:
                logger.warning(f"Error attaching {path}: {e}")
                continue

        # Send
        logger.info(f"Sending email to {config.to}...")
        server.send_message(msg)
        logger.info("Email sent successfully")

        # Close
        server.quit()
        logger.info("SMTP connection closed")

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"Authentication failed: {e} (check MAIL_USER and MAIL_PASS)")
        raise
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
        raise
    except socket.gaierror as e:
        logger.error(f"DNS/Network error: {e} (check MAIL_SERVER hostname)")
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise


def main() -> None:
    args = parse_args()

    # Load config
    config = EmailConfig()
    logger.info("=== AlbuchBot Email Sender ===")
    logger.info(f"Configuration loaded:")
    logger.info(f"  MAIL_SERVER={config.server}")
    logger.info(f"  MAIL_PORT={config.port}")
    logger.info(f"  MAIL_USER={config.user}")
    logger.info(f"  MAIL_TO={config.to}")
    logger.info(f"  MAIL_FROM={config.from_addr}")

    # Validate
    is_valid, error_msg = config.validate()
    if not is_valid:
        logger.error(f"Configuration validation failed: {error_msg}")
        raise RuntimeError(error_msg)

    logger.info("✓ Configuration validated")

    # Check DNS
    dns_ok, dns_error = config.check_dns()
    if not dns_ok:
        raise RuntimeError(dns_error)

    # Read subject and body
    subject = read_file(Path(args.subject))
    body = read_file(Path(args.body))

    logger.info(f"Subject: {subject.strip()[:60]}...")
    logger.info(f"Body length: {len(body)} chars")

    # Send
    try:
        send_email(config, subject, body, args.attachments, args.timeout)
        logger.info("✓ Email sent successfully")
    except Exception as e:
        logger.error(f"✗ Failed to send email: {e}")
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        exit(1)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send email digest with Python SMTP")
    parser.add_argument("--subject", required=True, help="Path to subject text file")
    parser.add_argument("--body", required=True, help="Path to body text file")
    parser.add_argument(
        "--attachments",
        nargs="+",
        default=[],
        help="Paths to attachment files",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="SMTP timeout in seconds",
    )
    return parser.parse_args()


def read_file(path: Path) -> str:
    """Read file content with UTF-8 encoding."""
    try:
        content = path.read_text(encoding="utf-8")
        logger.info(f"Read file: {path} ({len(content)} chars)")
        return content
    except FileNotFoundError:
        logger.error(f"File not found: {path}")
        raise
    except Exception as e:
        logger.error(f"Error reading {path}: {e}")
        raise


def send_email(
    config: EmailConfig,
    subject: str,
    body: str,
    attachment_paths: list[str],
    timeout: int,
) -> None:
    """Send email via SMTP."""
    logger.info(f"Connecting to {config.server}:{config.port}...")

    try:
        # Create SMTP connection
        server = smtplib.SMTP(config.server, config.port, timeout=timeout)
        server.starttls()
        logger.info("Connected, starting TLS...")

        # Login
        logger.info(f"Authenticating as {config.user}...")
        server.login(config.user, config.password)
        logger.info("Authentication successful")

        # Build message
        msg = MIMEMultipart()
        msg["From"] = config.from_addr
        msg["To"] = config.to
        msg["Subject"] = subject.strip()

        # Add body
        msg.attach(MIMEText(body, "plain", "utf-8"))
        logger.info("Email body attached")

        # Add attachments
        for attachment_path in attachment_paths:
            path = Path(attachment_path)
            if not path.exists():
                logger.warning(f"Attachment not found, skipping: {path}")
                continue

            try:
                with open(path, "rb") as attachment:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(attachment.read())
                    encoders.encode_base64(part)
                    part.add_header("Content-Disposition", f"attachment; filename= {path.name}")
                    msg.attach(part)
                logger.info(f"Attachment added: {path.name}")
            except Exception as e:
                logger.warning(f"Error attaching {path}: {e}")
                continue

        # Send
        logger.info(f"Sending email to {config.to}...")
        server.send_message(msg)
        logger.info("Email sent successfully")

        # Close
        server.quit()
        logger.info("SMTP connection closed")

    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"Authentication failed: {e}")
        raise
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise


def main() -> None:
    args = parse_args()

    # Load config
    config = EmailConfig()
    logger.info(f"Email config: server={config.server}, port={config.port}, user={config.user}")

    # Validate
    is_valid, error_msg = config.validate()
    if not is_valid:
        logger.error(f"Configuration error: {error_msg}")
        raise RuntimeError(error_msg)

    logger.info("Configuration validated")

    # Read subject and body
    subject = read_file(Path(args.subject))
    body = read_file(Path(args.body))

    logger.info(f"Subject: {subject.strip()[:60]}...")
    logger.info(f"Body length: {len(body)} chars")

    # Send
    try:
        send_email(config, subject, body, args.attachments, args.timeout)
        logger.info("✓ Email sent successfully")
    except Exception as e:
        logger.error(f"✗ Failed to send email: {e}")
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical(f"Fatal error: {e}")
        exit(1)
