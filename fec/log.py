"""Logging: plain console, detailed log file."""
import io
import logging
import os
import sys

_CONFIGURED = False


class CleanFormatter(logging.Formatter):
    """Console formatter: just the message, no clutter."""

    # return only the log message
    def format(self, record):
        return record.getMessage()


# configure pipeline logging once at startup
def setup_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_file = os.environ.get("LOG_FILE", "").strip() or None

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Windows cp1252 cannot encode some characters
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


# get a module logger, configuring if needed
def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)


# log label and count when count nonzero
def log_count(logger: logging.Logger, label: str, count: int) -> None:
    if count:
        logger.info("  %-38s %s", label, f"{count:,}")
