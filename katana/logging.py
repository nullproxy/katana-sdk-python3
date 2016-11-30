"""
Python 3 SDK for the KATANA(tm) Platform (http://katana.kusanagi.io)

Copyright (c) 2016-2017 KUSANAGI S.L. All rights reserved.

Distributed under the MIT license.

For the full copyright and license information, please view the LICENSE
file that was distributed with this source code.

"""

__license__ = "MIT"
__copyright__ = "Copyright (c) 2016-2017 KUSANAGI S.L. (http://kusanagi.io)"

import logging
import types
import sys

from datetime import datetime

from . import json


class KatanaFormatter(logging.Formatter):
    """Default KATANA logging formatter."""

    def formatTime(self, record, *args, **kwargs):
        return datetime.fromtimestamp(record.created).isoformat()[:-3]


def value_to_log_string(value, max_chars=100000):
    """Convert a value to a string.

    :param value: A value to log.
    :type value: object
    :param max_chars: Optional maximum number of characters to return.
    :type max_chars: int

    :rtype: str

    """

    if value is None:
        output = 'NULL'
    elif isinstance(value, bool):
        output = 'TRUE' if value else 'FALSE'
    elif isinstance(value, str):
        output = value
    elif isinstance(value, bytes):
        output = value.decode('utf8')
    elif isinstance(value, (dict, list, tuple)):
        output = json.serialize(value, prettify=True).decode('utf8')
    elif isinstance(value, types.FunctionType):
        if value.__name__ == '<lambda>':
            output = 'anonymous'
        else:
            output = '[function {}]'.format(value.__name__)
    else:
        output = repr(value)

    return output[:max_chars]


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

    # Setup katana api logger
    logger = logging.getLogger('katana.api')
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(logging.Formatter())  # No format is applied
        logger.addHandler(handler)
        logger.propagate = False

    # Setup other loggers
    logger = logging.getLogger('asyncio')
    logger.setLevel(logging.ERROR)
