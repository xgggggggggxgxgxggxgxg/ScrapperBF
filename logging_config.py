"""
Configuration centralisée des logs.

Niveau réglable par la variable d'environnement LOG_LEVEL (DEBUG, INFO, WARNING…),
INFO par défaut. Les modules récupèrent leur logger via logging.getLogger(
"scrapperbf.<module>"). setup_logging() est idempotent (ne configure qu'une fois).
"""

import logging
import os

_CONFIGURED = False


def setup_logging(level: str = None) -> logging.Logger:
    global _CONFIGURED
    lvl = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    numeric = getattr(logging, lvl, logging.INFO)
    if not _CONFIGURED:
        logging.basicConfig(
            level=numeric,
            format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
        _CONFIGURED = True
    else:
        logging.getLogger("scrapperbf").setLevel(numeric)
    return logging.getLogger("scrapperbf")


# Logger racine du projet (les sous-loggers en héritent).
log = logging.getLogger("scrapperbf")
