"""Shared application logging and audit events."""

import logging
import os


LOG_LEVEL = os.getenv("BIFP2P_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("bifp2p")


def audit_event(event, **fields):
    """Log operational state changes without secrets, invoices, or message bodies."""
    parts = [f"{key}={value}" for key, value in fields.items() if value is not None]
    logger.info("AUDIT event=%s%s", event, (" " + " ".join(parts)) if parts else "")
