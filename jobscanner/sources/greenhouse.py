"""Greenhouse job boards - one company's careers page per configured source."""

from .base import JobSourceAdapter, SourceError, http_json, register
from .html_text import strip_html


@register
class GreenhouseAdapter(JobSourceAdapter):
    type_name = 'greenhouse'
    label = 'Greenhouse'
    supports_location_query = False
    limitations = ('Board API returns the full board; there is no server-side location filter. '
                   'Location strings are free text set by the employer ("Zurich, Switzerland", '
                   '"Remote - EMEA"), so quality depends on the company.')

    def fetch_jobs(self, profile, config, source_name):
        token = str((config or {}).get('board_token') or '').strip()
        if not token:
            raise SourceError('Greenhouse: board_token is missing.')
        data = http_json('https://boards-api.greenhouse.io/v1/boards/{0}/jobs?content=true'.format(token))
        jobs = []
        for item in (data.get('jobs') or []):
            external_id = str(item.get('id') or item.get('absolute_url') or '')
            if not external_id:
                continue
            location = item.get('location') or {}
            content = strip_html(item.get('content') or '')
            jobs.append({
                'source': source_name, 'source_type': self.type_name,
                'external_id': external_id,
                'company': str((config or {}).get('company') or source_name).strip(),
                'title': str(item.get('title') or '').strip(),
                'location': str(location.get('name') or '').strip() if isinstance(location, dict) else str(location),
                'remote': None,
                'job_url': str(item.get('absolute_url') or '').strip(),
                'description': content,
                'excerpt': content[:800],
                'published_at': item.get('updated_at'),
                'salary_min': None, 'salary_max': None,
                'salary_currency': '', 'salary_period': '',
            })
        return jobs
