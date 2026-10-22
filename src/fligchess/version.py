"""Compatibility shim: the version now lives in ``fligchess.const``.

Old imports (``from fligchess import version``; ``from fligchess.version
import APP_VERSION``) keep working because every name is re-exported here.
New code should import from ``fligchess.const`` directly.
"""

from fligchess.const import (APP_VERSION, TAG_PREFIX, release_tag,
                             parse_version, is_newer)

__all__ = ["APP_VERSION", "TAG_PREFIX", "release_tag", "parse_version",
           "is_newer"]
