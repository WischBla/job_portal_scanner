"""Jobicy - remote job board with a geo parameter."""

from urllib.parse import urlencode

from .base import JobSourceAdapter, http_json, register
from .html_text import strip_html


@register
class JobicyAdapter(JobSourceAdapter):
    type_name = 'jobicy'
    label = 'Jobicy'
    supports_location_query = True
    limitations = ('Remote-only board. The geo=switzerland query returns very few postings and '
                   'jobGeo is often a broad region such as "Europe" or "Anywhere", so most '
                   'results are rejected locally for lack of Swiss evidence.')

    def fetch_jobs(self, profile, config, source_name):
        # Upstream optimisation only - the local filter still decides.
        urls = ['https://jobicy.com/api/v2/remote-jobs?' + urlencode({'count': 100, 'geo': 'switzerland'})]
        if (profile.get('country_mode') or 'strict').lower() != 'strict':
            for industry in ('engineering', 'management', 'project-management'):
                urls.append('https://jobicy.com/api/v2/remote-jobs?' +
                            urlencode({'count': 100, 'geo': 'europe', 'industry': industry}))
        jobs, seen = [], set()
        for url in urls:
            data = http_json(url)
            for item in (data.get('jobs') or []):
                external_id = str(item.get('id') or item.get('jobSlug') or item.get('url') or '')
                if not external_id or external_id in seen:
                    continue
                seen.add(external_id)
                jobs.append({
                    'source': source_name, 'source_type': self.type_name,
                    'external_id': external_id,
                    'company': str(item.get('companyName') or '').strip(),
                    'title': str(item.get('jobTitle') or '').strip(),
                    'location': str(item.get('jobGeo') or '').strip(),
                    'remote': True,
                    'job_url': str(item.get('url') or '').strip(),
                    'description': strip_html(item.get('jobDescription') or ''),
                    'excerpt': strip_html(item.get('jobExcerpt') or '')[:800],
                    'published_at': item.get('pubDate'),
                    'salary_min': item.get('salaryMin'), 'salary_max': item.get('salaryMax'),
                    'salary_currency': str(item.get('salaryCurrency') or '').strip(),
                    'salary_period': str(item.get('salaryPeriod') or '').strip(),
                })
        return jobs
