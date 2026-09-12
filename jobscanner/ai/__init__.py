"""Optional AI analysis.

The application is fully usable with AI switched off: deterministic matching
and templates then produce every field the UI shows.  When a provider is
configured, its structured answer is stored once per job so reopening the Jobs
page never triggers a new request.
"""

from .base import AIProvider, AIError, AIResult
from .service import analyse_job, cached_analysis, clear_analysis, get_provider

__all__ = ['AIProvider', 'AIError', 'AIResult', 'analyse_job', 'cached_analysis',
           'clear_analysis', 'get_provider']
