"""The honest non-source.

A company with no public, machine-readable endpoint keeps its place on the
watchlist, but the application must never suggest that it is being scanned.
This adapter exists so "manual" is a first-class kind in the registry rather
than a special case sprinkled through the code: it owns no ``job_sources`` row,
is never scheduled by the pipeline, and fetching it is a programming error.
"""

from .base import JobSourceAdapter, SourceError, register


@register
class ManualSourceAdapter(JobSourceAdapter):
    type_name = 'manual'
    label = 'Manual'
    supports_location_query = False
    limitations = ('No public, machine-readable job endpoint could be verified for this '
                   'company, so nothing is fetched. The watchlist entry only offers a link to '
                   'the careers page.')

    def fetch_jobs(self, profile, config, source_name):
        raise SourceError('{0} has no automated source; open the careers page instead.'
                          .format((config or {}).get('company') or source_name))

    def verify(self, config):
        return 0, 'Manual entry - nothing is fetched.'
