"""Console messages and optional detailed file logs."""
import io
import logging
import os
import sys

_CONFIGURED = False


class CleanFormatter(logging.Formatter):
    """Message-only console formatting."""

    # Show only the message in the console.
    def format(self, record):
        return record.getMessage()


# Configure console and optional file logging once.
def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_file = os.environ.get("LOG_FILE", "").strip() or None

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # force UTF-8 on Windows: cp1256/cp1252 can't encode some log characters
    if sys.platform == "win32":
        console_stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    else:
        console_stream = sys.stdout
    console = logging.StreamHandler(console_stream)
    console.setFormatter(CleanFormatter())
    root.addHandler(console)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-5s  %(name)-30s  %(message)s",
            datefmt="%H:%M:%S",
        ))
        root.addHandler(fh)

    _CONFIGURED = True


# Set up logging when needed and return the module logger.
def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)
