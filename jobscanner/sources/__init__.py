"""Job source adapters.

Every adapter translates one portal into the flat raw-job structure defined in
``jobscanner.normalizer.RAW_FIELDS`` and nothing else.  Adapters may use a
portal's own location/country parameter as an *optimisation* (fewer pages to
download), but they never decide relevance - the local HardFilter does.

Two families live here:

``arbeitnow`` / ``jobicy`` / ``remotive``
    Broad aggregators.  Cheap, but very little of what they carry is a senior
    Swiss technology-leadership role.

``greenhouse`` / ``lever`` / ``smartrecruiters`` / ``successfactors`` /
``phenom`` / ``amazon_jobs`` / ``jsonld`` / ``rss``
    Company sources: one employer per configured source, driven by the company
    watchlist.  These are the high-signal ones.

``registry`` maps a watchlist entry onto the right adapter and config.
"""

from .base import (BROWSER_USER_AGENT, JobSourceAdapter, SourceError, adapter_types,
                   fetch_in_parallel, get_adapter, http_json, http_text, register)
from . import (amazon_jobs, arbeitnow, greenhouse, jobicy, lever, manual,  # noqa: F401
               phenom, remotive, rss, smartrecruiters, structured_jobs, successfactors)
from .registry import (ACTIVE, ERROR, MANUAL, SOURCE_KINDS, SOURCE_STATUSES, UNAVAILABLE,
                       WATCHLIST_SOURCE_TYPES, get_kind, is_automated, kind_catalogue,
                       resolve, source_label, verify)

__all__ = [
    'JobSourceAdapter', 'SourceError', 'register', 'get_adapter', 'adapter_types',
    'http_json', 'http_text', 'fetch_in_parallel', 'BROWSER_USER_AGENT',
    'SOURCE_KINDS', 'WATCHLIST_SOURCE_TYPES', 'SOURCE_STATUSES',
    'ACTIVE', 'MANUAL', 'UNAVAILABLE', 'ERROR',
    'get_kind', 'is_automated', 'kind_catalogue', 'resolve', 'source_label', 'verify',
]
