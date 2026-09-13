"""amazon.jobs - Amazon's own public careers search endpoint (covers AWS).

    GET https://www.amazon.jobs/en/search.json?loc_query=Switzerland&country=CHE

This is the JSON the public careers site itself requests; it takes no key and
no cookie.  It is the only one of the hyperscalers with a stable public feed,
which is why Google/Microsoft/Meta stay MANUAL while AWS does not.
"""

from urllib.parse import urlencode

from .base import JobSourceAdapter, http_json, register
from .html_text import strip_html

SEARCH = 'https://www.amazon.jobs/en/search.json'
PAGE_SIZE = 100
MAX_PAGES = 5


@register
class AmazonJobsAdapter(JobSourceAdapter):
    type_name = 'amazon_jobs'
    label = 'Amazon Jobs (public)'
    supports_location_query = True
    limitations = ('Covers every Amazon entity including AWS. The country filter is applied '
                   'upstream, so a scan is cheap, but the legal-entity name ("AWS EMEA SARL, '
                   'Switzerland Branch") is what shows up as the employer.')

    def fetch_jobs(self, profile, config, source_name):
        config = config or {}
        company = str(config.get('company') or source_name).strip()
        # Upstream optimisation only - the local HardFilter still decides.
        country = str(config.get('country_code') or 'CHE').strip()
        location = str(config.get('location_query') or 'Switzerland').strip()

        jobs, seen = [], set()
        for page in range(MAX_PAGES):
            query = urlencode({'loc_query': location, 'country': country, 'sort': 'recent',
                               'result_limit': PAGE_SIZE, 'offset': page * PAGE_SIZE})
            data = http_json('{0}?{1}'.format(SEARCH, query), timeout=30)
            batch = (data or {}).get('jobs') or []
            if not batch:
                break
            for item in batch:
                job = self._to_job(item, company, source_name)
                if job and job['external_id'] not in seen:
                    seen.add(job['external_id'])
                    jobs.append(job)
            if len(batch) < PAGE_SIZE:
                break
        return jobs

    def _to_job(self, item, company, source_name):
        external_id = str(item.get('id_icims') or item.get('id') or '')
        title = str(item.get('title') or '').strip()
        if not external_id or not title:
            return None
        description = ' '.join(strip_html(item.get(key) or '') for key in
                               ('description', 'basic_qualifications',
                                'preferred_qualifications')).strip()
        path = str(item.get('job_path') or '').strip()
        return {
            'source': source_name, 'source_type': self.type_name,
            'external_id': external_id,
            # The employing entity is the honest answer ("AWS EMEA SARL"), with
            # the watchlist name kept as a fallback.
            'company': str(item.get('company_name') or '').strip() or company,
            'title': title,
            'location': str(item.get('normalized_location') or item.get('location') or '').strip(),
            'remote': None,
            'job_url': 'https://www.amazon.jobs{0}'.format(path) if path.startswith('/') else path,
            'description': description,
            'excerpt': strip_html(item.get('description_short') or '')[:800] or description[:800],
            'published_at': item.get('posted_date') or item.get('updated_time') or '',
            'salary_min': None, 'salary_max': None,
            'salary_currency': '', 'salary_period': '',
        }
