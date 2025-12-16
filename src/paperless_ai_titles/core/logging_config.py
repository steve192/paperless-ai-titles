from __future__ import annotations

import logging
from typing import Optional

from .config import Settings, get_settings

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(settings: Optional[Settings] = None) -> None:
    """Configure root logging once and allow runtime level tweaks."""
    settings = settings or get_settings()
    level_name = (settings.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        for handler in root.handlers:
            handler.setLevel(level)
        return
    logging.basicConfig(level=level, format=LOG_FORMAT)