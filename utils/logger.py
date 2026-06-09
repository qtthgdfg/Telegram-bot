"""
Centralised logger — all engines import from here.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from config import LOG_LEVEL, LOG_FILE


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    # Rotating file (10 MB × 5 backups)
    try:
        fh = RotatingFileHandler(LOG_FILE, maxBytes=10_485_760, backupCount=5)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass

    return logger
