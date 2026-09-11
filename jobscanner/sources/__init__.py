"""Job source adapters.

Every adapter translates one portal into the flat raw-job structure defined in
``jobscanner.normalizer.RAW_FIELDS`` and nothing else.  Adapters may use a
portal's own location/country parameter as an *optimisation* (fewer pages to
download), but they never decide relevance - the local HardFilter does.
"""

from .base import JobSourceAdapter, SourceError, register, get_adapter, adapter_types
from . import arbeitnow, jobicy, remotive, greenhouse, lever, rss  # noqa: F401  (registration)

__all__ = ['JobSourceAdapter', 'SourceError', 'register', 'get_adapter', 'adapter_types']
