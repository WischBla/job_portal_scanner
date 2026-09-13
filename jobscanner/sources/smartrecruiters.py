"""SmartRecruiters public postings API - one company per configured source.

Endpoint (public, no key, documented by SmartRecruiters as the "Posting API"):

    GET https://api.smartrecruiters.com/v1/companies/{company}/postings

The list response carries identity, title and location but *not* the advert
text, so the adapter follows each posting to

    GET https://api.smartrecruiters.com/v1/companies/{company}/postings/{id}

for the job-ad sections.  That detail pass is what makes the local scorer able
to see "Kubernetes", "SRE" or "platform engineering" at all.
"""

from .base import JobSourceAdapter, SourceError, fetch_in_parallel, http_json, register
from .html_text import strip_html

API = 'https://api.smartrecruiters.com/v1/companies/{0}/postings'
PAGE_SIZE = 100
MAX_PAGES = 10
#: Detail lookups are one request per posting, so a very large board is capped.
MAX_DETAILS = 120

_SECTION_ORDER = ('jobDescription', 'qualifications', 'additionalInformation',
                  'companyDescription')


@register
class SmartRecruitersAdapter(JobSourceAdapter):
    type_name = 'smartrecruiters'
    label = 'SmartRecruiters'
    supports_location_query = False
    limitations = ('The public Posting API returns the whole board; there is no reliable '
                   'server-side country filter, so Switzerland is decided locally. The advert '
                   'text lives on a second endpoint, which is fetched per posting and capped '
                   'at {0} postings per scan.'.format(MAX_DETAILS))

    def fetch_jobs(self, profile, config, source_name):
        config = config or {}
        company_id = str(config.get('company_id') or config.get('site') or '').strip()
        if not company_id:
            raise SourceError('SmartRecruiters: company_id is missing.')
        display_name = str(config.get('company') or source_name).strip()

        postings = self._all_postings(company_id)
        wanted = postings[:MAX_DETAILS]
        details = dict(fetch_in_parallel(
            wanted, lambda item: (item['id'], self._detail(company_id, item['id']))))

        jobs = []
        for item in postings:
            job = self._to_job(item, details.get(item.get('id')), company_id,
                               display_name, source_name)
            if job:
                jobs.append(job)
        return jobs

    # -- upstream ----------------------------------------------------------
    def _all_postings(self, company_id):
        """Page through the board; stops as soon as a page comes back short."""
        out, seen = [], set()
        for page in range(MAX_PAGES):
            url = '{0}?limit={1}&offset={2}'.format(API.format(company_id), PAGE_SIZE,
                                                    page * PAGE_SIZE)
            data = http_json(url)
            if not isinstance(data, dict):
                raise SourceError('SmartRecruiters: unexpected response for "{0}".'.format(company_id))
            content = data.get('content') or []
            for item in content:
                posting_id = str(item.get('id') or '')
                if posting_id and posting_id not in seen:
                    seen.add(posting_id)
                    out.append(item)
            total = data.get('totalFound')
            if len(content) < PAGE_SIZE or (total is not None and len(out) >= int(total)):
                break
        return out

    @staticmethod
    def _detail(company_id, posting_id):
        return http_json('{0}/{1}'.format(API.format(company_id), posting_id))

    # -- normalisation -----------------------------------------------------
    def _to_job(self, item, detail, company_id, display_name, source_name):
        posting_id = str(item.get('id') or '')
        if not posting_id:
            return None
        detail = detail or {}
        company = item.get('company') or {}
        description = self._description(detail)
        url = (str(detail.get('postingUrl') or '').strip() or
               'https://jobs.smartrecruiters.com/{0}/{1}'.format(company_id, posting_id))
        return {
            'source': source_name, 'source_type': self.type_name,
            'external_id': posting_id,
            'company': display_name or str(company.get('name') or company_id),
            'title': str(item.get('name') or '').strip(),
            'location': self._location(item.get('location') or {}),
            'remote': self._remote(item.get('location') or {}),
            'job_url': url,
            'description': description,
            'excerpt': description[:800],
            'published_at': item.get('releasedDate') or '',
            'salary_min': None, 'salary_max': None,
            'salary_currency': '', 'salary_period': '',
        }

    @staticmethod
    def _description(detail):
        sections = ((detail.get('jobAd') or {}).get('sections') or {})
        parts = []
        for key in _SECTION_ORDER:
            text = strip_html((sections.get(key) or {}).get('text') or '')
            if text:
                parts.append(text)
        return ' '.join(parts).strip()

    @staticmethod
    def _location(location):
        """"Zurich, ZH, Switzerland" - country codes are expanded by the normalizer."""
        pieces = [str(location.get(key) or '').strip()
                  for key in ('city', 'region', 'country')]
        full = str(location.get('fullLocation') or '').strip()
        joined = ', '.join(p for p in pieces if p)
        return full or joined

    @staticmethod
    def _remote(location):
        if location.get('remote'):
            return True
        if location.get('hybrid'):
            return False
        return None
