import datetime
import decimal
import json

from . import utils


class Encoder(json.JSONEncoder):
    """Class to handle JSON encoding for custom types."""

    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            # Note: Use str instead of float
            # to avoid dealing with presition
            return str(obj)
        elif isinstance(obj, datetime.datetime):
            return utils.date_to_str(obj)
        elif isinstance(obj, datetime.date):
            return obj.strftime('%Y-%m-%d')
        elif hasattr(obj, '__serialize__'):
            return obj.__serialize__()
        elif isinstance(obj, bytes):
            return obj.decode('utf8')

        return json.JSONEncoder.default(self, obj)


def deserialize(json_string):
    """Convert a JSON string to Python.

    :rtype: a Python type

    """

    return json.loads(json_string)


def serialize(python_type, encoding='utf8', prettify=False):
    """Serialize a Python object to JSON string.

    :returns: Bytes, or string when encoding is None.

    """

    if not prettify:
        value = json.dumps(python_type, separators=(',', ':'), cls=Encoder)
    else:
        value = json.dumps(python_type, indent=2, cls=Encoder)

    return value.encode(encoding) if encoding else value
