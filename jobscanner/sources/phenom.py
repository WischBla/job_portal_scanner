"""Phenom People career sites (Roche, ABB).

Phenom career sites are React shells whose data comes from a public,
unauthenticated JSON endpoint on the company's own careers domain:

    POST https://careers.<company>/widgets      (ddoKey = refineSearch)

No key, no cookie and no signature is involved - it is the request the public
careers page makes for every anonymous visitor.  The one thing that matters for
this scanner is that ``selected_fields.country`` filters server-side, so a
Switzerland scan costs a couple of requests instead of paging a 2'000-job
global board.
"""

import re

from .base import JobSourceAdapter, SourceError, http_json, register

PAGE_SIZE = 100
MAX_PAGES = 6
_SLUG = re.compile(r'[^A-Za-z0-9]+')


@register
class PhenomAdapter(JobSourceAdapter):
    type_name = 'phenom'
    label = 'Phenom career site'
    supports_location_query = True
    limitations = ('The public careers endpoint filters by country upstream, but only returns '
                   'the advert teaser plus parsed skills - not the full text - so descriptions '
                   'are shorter than on Greenhouse or Lever.')

    def fetch_jobs(self, profile, config, source_name):
        config = config or {}
        base = str(config.get('base_url') or '').strip().rstrip('/')
        if not base:
            raise SourceError('Phenom: base_url is missing.')
        company = str(config.get('company') or source_name).strip()
        locale_path = str(config.get('locale_path') or 'global/en').strip().strip('/')
        countries = config.get('countries') or ['Switzerland']

        jobs, seen = [], set()
        for page in range(MAX_PAGES):
            batch = self._page(base, countries, page * PAGE_SIZE, config)
            if not batch:
                break
            for item in batch:
                job = self._to_job(item, base, locale_path, company, source_name)
                if job and job['external_id'] not in seen:
                    seen.add(job['external_id'])
                    jobs.append(job)
            if len(batch) < PAGE_SIZE:
                break
        return jobs

    # -- upstream ----------------------------------------------------------
    def _page(self, base, countries, offset, config):
        payload = {
            'lang': str(config.get('lang') or 'en_global'),
            'deviceType': 'desktop',
            'country': str(config.get('site_country') or 'global'),
            'pageName': 'search-results',
            'ddoKey': 'refineSearch',
            'sortBy': '', 'subsearch': '',
            'from': offset, 'size': PAGE_SIZE,
            'jobs': True, 'counts': True,
            'all_fields': ['country', 'state', 'city', 'category', 'type'],
            'clearAll': False, 'jdsource': 'facets', 'isSliderEnable': False,
            'pageId': str(config.get('page_id') or 'page11'),
            'siteType': 'external', 'keywords': '', 'global': True,
            'selected_fields': {'country': list(countries)},
            'locationData': {},
        }
        data = http_json('{0}/widgets'.format(base), timeout=35, payload=payload)
        node = self._search_node(data)
        if node is None:
            raise SourceError('Phenom: no job data in the response from {0}.'.format(base))
        return node.get('jobs') or []

    @staticmethod
    def _search_node(data):
        """The payload nests the result under refineSearch or eagerLoadRefineSearch."""
        if not isinstance(data, dict):
            return None
        for key in ('refineSearch', 'eagerLoadRefineSearch'):
            block = data.get(key)
            if isinstance(block, dict) and isinstance(block.get('data'), dict):
                return block['data']
        if isinstance(data.get('jobs'), list):
            return data
        return None

    # -- normalisation -----------------------------------------------------
    def _to_job(self, item, base, locale_path, company, source_name):
        external_id = str(item.get('jobSeqNo') or item.get('jobId') or item.get('reqId') or '')
        title = str(item.get('title') or '').strip()
        if not external_id or not title:
            return None
        description = self._description(item)
        return {
            'source': source_name, 'source_type': self.type_name,
            'external_id': external_id,
            'company': company,
            'title': title,
            'location': self._location(item),
            'remote': None,
            'job_url': '{0}/{1}/job/{2}/{3}'.format(base, locale_path, external_id,
                                                    _SLUG.sub('-', title).strip('-')),
            'description': description,
            'excerpt': description[:800],
            'published_at': item.get('postedDate') or item.get('dateCreated') or '',
            'salary_min': None, 'salary_max': None,
            'salary_currency': '', 'salary_period': '',
        }

    @staticmethod
    def _location(item):
        locations = [str(x) for x in (item.get('multi_location') or []) if x]
        if locations:
            return ' | '.join(locations[:4])
        for key in ('cityStateCountry', 'location', 'address', 'cityState', 'city'):
            value = str(item.get(key) or '').strip()
            if value:
                return value
        return str(item.get('country') or '')

    @staticmethod
    def _description(item):
        """Teaser plus the parsed skill list - that is all the endpoint exposes."""
        parts = [str(item.get('descriptionTeaser') or '').strip()]
        for key in ('category', 'subCategory', 'type'):
            value = str(item.get(key) or '').strip()
            if value:
                parts.append(value)
        skills = [str(s) for s in (item.get('ml_skills') or []) if s]
        if skills:
            parts.append(', '.join(skills[:40]))
        return ' '.join(p for p in parts if p).strip()
