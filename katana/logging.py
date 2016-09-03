import logging
import sys

from datetime import datetime


class KatanaFormatter(logging.Formatter):
    """Default KATANA logging formatter."""

    def formatTime(self, record, *args, **kwargs):
        return datetime.fromtimestamp(record.created).isoformat()[:-3]


def setup_katana_logging(level=logging.INFO):
    """Initialize logging defaults for KATANA.

    :param level: Logging level. Default: INFO.

    """

    format = "%(asctime)sZ [%(levelname)s] [SDK] %(message)s"

    # Setup root logger
    root = logging.root
    if not root.handlers:
        logging.basicConfig(level=level, stream=sys.stdout)
        root.setLevel(level)
        root.handlers[0].setFormatter(KatanaFormatter(format))

    # Setup katana logger
    logger = logging.getLogger('katana')
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(KatanaFormatter(format))
        logger.addHandler(handler)
        logger.propagate = False

    # Setup other loggers
    logger = logging.getLogger('asyncio')
    logger.setLevel(logging.ERROR)
