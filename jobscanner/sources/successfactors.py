"""SAP SuccessFactors career sites (Swiss Re, SIX, Zurich Insurance, Adnovum...).

These sites have no JSON API and no RSS, but the public search page is fully
server rendered and uses one vendor-standard template, so this is a *vendor*
adapter rather than a per-company scraper: the same code drives every
SuccessFactors career site.

    {base}/search/?q=&locationsearch=Switzerland&startrow=0

Each result row is a ``<tr class="data-row">`` with a ``jobTitle-link`` anchor,
a ``jobLocation`` span and a ``jobDate`` span.  The advert text is read from the
detail page's schema.org microdata (``itemprop="description"``), which is the
same contract ``structured_jobs`` relies on.

Nothing here bypasses anything: no login, no cookie, no CAPTCHA, no hidden
endpoint - it is the page a visitor gets.
"""

import re
from urllib.parse import urljoin

from .base import JobSourceAdapter, SourceError, fetch_in_parallel, http_text, register
from .html_text import strip_html

PAGE_SIZE = 25
MAX_PAGES = 12
MAX_DETAILS = 120

_ROW = re.compile(r'<tr[^>]+class="[^"]*data-row[^"]*"[^>]*>(.*?)</tr>', re.S | re.I)
_LINK = re.compile(r'<a[^>]+href="([^"]*/job/[^"]+)"[^>]*class="[^"]*jobTitle-link[^"]*"[^>]*>(.*?)</a>'
                   r'|<a[^>]+class="[^"]*jobTitle-link[^"]*"[^>]*href="([^"]*/job/[^"]+)"[^>]*>(.*?)</a>',
                   re.S | re.I)
_LOCATION = re.compile(r'<span[^>]+class="[^"]*jobLocation[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
_DATE = re.compile(r'<span[^>]+class="[^"]*jobDate[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
_JOB_ID = re.compile(r'/job/[^/]*?/(\d+)/?$')
_DESCRIPTION = re.compile(r'itemprop="description"[^>]*>(.*?)(?:</div>\s*</div>\s*</div>|</body>)',
                          re.S | re.I)


@register
class SuccessFactorsAdapter(JobSourceAdapter):
    type_name = 'successfactors'
    label = 'SuccessFactors career site'
    supports_location_query = True
    limitations = ('Server-rendered vendor template, so there is no JSON API: the public search '
                   'page is paged {0} rows at a time and the advert text comes from each job '
                   "page's schema.org microdata. ``locationsearch`` is passed upstream, but the "
                   'local filter still decides.'.format(PAGE_SIZE))

    def fetch_jobs(self, profile, config, source_name):
        config = config or {}
        base = str(config.get('base_url') or '').strip().rstrip('/')
        if not base:
            raise SourceError('SuccessFactors: base_url is missing.')
        company = str(config.get('company') or source_name).strip()
        # Upstream optimisation only - the HardFilter still has the last word.
        location = str(config.get('location_query') or 'Switzerland').strip()

        rows = self._all_rows(base, location)
        wanted = rows[:MAX_DETAILS]
        descriptions = dict(fetch_in_parallel(
            wanted, lambda row: (row['url'], self._description(row['url']))))

        jobs = []
        for row in rows:
            description = descriptions.get(row['url']) or ''
            jobs.append({
                'source': source_name, 'source_type': self.type_name,
                'external_id': row['external_id'],
                'company': company,
                'title': row['title'],
                'location': row['location'],
                'remote': None,
                'job_url': row['url'],
                'description': description,
                'excerpt': description[:800] or row['title'],
                'published_at': row['published_at'],
                'salary_min': None, 'salary_max': None,
                'salary_currency': '', 'salary_period': '',
            })
        return jobs

    # -- upstream ----------------------------------------------------------
    def _all_rows(self, base, location):
        out, seen = [], set()
        for page in range(MAX_PAGES):
            url = '{0}/search/?q=&locationsearch={1}&startrow={2}'.format(
                base, location.replace(' ', '%20'), page * PAGE_SIZE)
            html = http_text(url, timeout=30)
            rows = self._parse_rows(html, base)
            fresh = [row for row in rows if row['url'] not in seen]
            for row in fresh:
                seen.add(row['url'])
                out.append(row)
            if not fresh or len(rows) < PAGE_SIZE:
                break
        return out

    def _parse_rows(self, html, base):
        rows = []
        for chunk in _ROW.findall(html or ''):
            match = _LINK.search(chunk)
            if not match:
                continue
            href = match.group(1) or match.group(3) or ''
            title = strip_html(match.group(2) or match.group(4) or '')
            if not href or not title:
                continue
            url = urljoin(base + '/', href)
            locations = [strip_html(loc) for loc in _LOCATION.findall(chunk)]
            dates = [strip_html(date) for date in _DATE.findall(chunk)]
            id_match = _JOB_ID.search(url.split('?')[0])
            rows.append({
                'external_id': id_match.group(1) if id_match else url,
                'title': title,
                'location': next((loc for loc in locations if loc), ''),
                'published_at': next((d for d in dates if d), ''),
                'url': url,
            })
        return rows

    @staticmethod
    def _description(url):
        match = _DESCRIPTION.search(http_text(url, timeout=30))
        return strip_html(match.group(1)) if match else ''
