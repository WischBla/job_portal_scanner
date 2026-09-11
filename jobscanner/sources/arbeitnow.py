"""Arbeitnow - mostly German-market board, no server-side country filter."""

from urllib.parse import urlencode

from .base import JobSourceAdapter, http_json, register
from .html_text import strip_html


@register
class ArbeitnowAdapter(JobSourceAdapter):
    type_name = 'arbeitnow'
    label = 'Arbeitnow'
    supports_location_query = False
    limitations = ('The public job-board API offers no country or location parameter and is '
                   'dominated by German postings. Everything is filtered locally, so a scan '
                   'downloads many pages to surface a handful of Swiss roles.')

    MAX_PAGES = 5

    def fetch_jobs(self, profile, config, source_name):
        jobs, seen = [], set()
        for page in range(1, self.MAX_PAGES + 1):
            data = http_json('https://www.arbeitnow.com/api/job-board-api?' + urlencode({'page': page}))
            page_jobs = data.get('data') or []
            if not page_jobs:
                break
            for item in page_jobs:
                external_id = str(item.get('slug') or item.get('url') or '')
                if not external_id or external_id in seen:
                    continue
                seen.add(external_id)
                jobs.append({
                    'source': source_name, 'source_type': self.type_name,
                    'external_id': external_id,
                    'company': str(item.get('company_name') or '').strip(),
                    'title': str(item.get('title') or '').strip(),
                    'location': str(item.get('location') or '').strip(),
                    'remote': bool(item.get('remote')),
                    'job_url': str(item.get('url') or '').strip(),
                    'description': strip_html(item.get('description') or ''),
                    'excerpt': '',
                    'published_at': item.get('created_at'),
                    'salary_min': None, 'salary_max': None,
                    'salary_currency': '', 'salary_period': '',
                })
        return jobs
