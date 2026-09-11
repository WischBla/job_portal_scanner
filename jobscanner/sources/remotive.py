"""Remotive - worldwide remote board with a free-text search parameter."""

from urllib.parse import urlencode

from .base import JobSourceAdapter, http_json, register
from .html_text import strip_html


@register
class RemotiveAdapter(JobSourceAdapter):
    type_name = 'remotive'
    label = 'Remotive'
    supports_location_query = False
    limitations = ('Worldwide remote board. "candidate_required_location" is usually a region '
                   'such as "Europe" or "Worldwide"; those are rejected locally unless the '
                   'description explicitly names Switzerland. Only a search term can be sent '
                   'upstream, not a country.')

    def fetch_jobs(self, profile, config, source_name):
        urls = ['https://remotive.com/api/remote-jobs?' + urlencode({'limit': 400})]
        # Upstream optimisation: ask for Switzerland explicitly as well.
        urls.append('https://remotive.com/api/remote-jobs?' +
                    urlencode({'search': 'switzerland', 'limit': 100}))
        jobs, seen = [], set()
        for url in urls:
            data = http_json(url)
            for item in (data.get('jobs') or []):
                external_id = str(item.get('id') or item.get('url') or '')
                if not external_id or external_id in seen:
                    continue
                seen.add(external_id)
                description = strip_html(item.get('description') or '')
                jobs.append({
                    'source': source_name, 'source_type': self.type_name,
                    'external_id': external_id,
                    'company': str(item.get('company_name') or '').strip(),
                    'title': str(item.get('title') or '').strip(),
                    'location': str(item.get('candidate_required_location') or '').strip(),
                    'remote': True,
                    'job_url': str(item.get('url') or '').strip(),
                    'description': description,
                    'excerpt': description[:800],
                    'published_at': item.get('publication_date'),
                    'salary_min': None, 'salary_max': None,
                    'salary_currency': '', 'salary_period': '',
                })
        return jobs
