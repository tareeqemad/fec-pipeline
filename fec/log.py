"""
Centralized logging for the FEC pipeline.

Usage:
    from fec.log import get_logger
    logger = get_logger(__name__)
    logger.info("Processing %d records", count)

Console output is clean (message only).
File output is detailed (timestamp + level + module).
"""
import logging
import os
import sys
from typing import Optional


_CONFIGURED = False


class CleanFormatter(logging.Formatter):
    """Console formatter: just the message, no clutter."""

    def format(self, record):
        return record.getMessage()


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Configure logging for the entire pipeline. Call once at startup."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = os.environ.get("LOG_LEVEL", level).upper()
    log_file = log_file or os.environ.get("LOG_FILE", "").strip() or None

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Console: clean output (message only)
    # Force UTF-8 on Windows (cp1256/cp1252 can't handle ✓→─ etc.)
    if sys.platform == "win32":
        import io
        console_stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    else:
        console_stream = sys.stdout
    console = logging.StreamHandler(console_stream)
    console.setFormatter(CleanFormatter())
    root.addHandler(console)

    # File: detailed output (timestamp + level + module)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-5s  %(name)-30s  %(message)s",
            datefmt="%H:%M:%S",
        ))
        root.addHandler(fh)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module. Auto-configures if not yet done."""
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)
