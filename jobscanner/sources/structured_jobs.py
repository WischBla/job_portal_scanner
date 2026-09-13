"""schema.org JobPosting extraction from public, server-rendered career pages.

Used for companies that publish real structured data but run no Greenhouse,
Lever or SmartRecruiters board.  The contract is deliberately narrow:

* the page must be public - no login, no cookie wall, no CAPTCHA;
* the postings must already be in the HTML the server sends;
* the data must be schema.org ``JobPosting`` in a ``<script
  type="application/ld+json">`` block (``@graph`` and arrays are handled).

If a page needs a browser to render its jobs, it is *not* supported here - the
company stays MANUAL instead, because a scraper that silently rots is worse
than an honest "open the careers page" link.

Config:
    url          the listing page (or a job page)
    company      display name
    link_pattern optional regex; matching links are followed and parsed too
    max_pages    cap on followed links (default 40)
"""

import json
import re
from urllib.parse import urljoin

from .base import JobSourceAdapter, SourceError, fetch_in_parallel, http_text, register
from .html_text import strip_html

_LD_JSON = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                      re.S | re.I)
_HREF = re.compile(r'href=["\']([^"\'#]+)["\']', re.I)
DEFAULT_MAX_PAGES = 40


def extract_job_postings(html):
    """Every JobPosting object in a page's JSON-LD blocks."""
    postings = []
    for block in _LD_JSON.findall(html or ''):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        _collect(data, postings)
    return postings


def _collect(node, out, depth=0):
    if depth > 6:
        return
    if isinstance(node, list):
        for item in node:
            _collect(item, out, depth + 1)
        return
    if not isinstance(node, dict):
        return
    types = node.get('@type')
    types = types if isinstance(types, list) else [types]
    if any(str(t).lower() == 'jobposting' for t in types if t):
        out.append(node)
        return
    for key in ('@graph', 'itemListElement', 'mainEntity', 'item'):
        if key in node:
            _collect(node[key], out, depth + 1)


def normalize_posting(posting, page_url='', fallback_company=''):
    """One schema.org JobPosting -> the flat raw-job structure."""
    title = str(posting.get('title') or posting.get('name') or '').strip()
    if not title:
        return None
    url = str(posting.get('url') or posting.get('sameAs') or page_url or '').strip()
    description = strip_html(posting.get('description') or '')
    salary = _salary(posting.get('baseSalary'))
    return {
        'source': '', 'source_type': 'jsonld',
        'external_id': _identifier(posting) or url or title,
        'company': _organisation(posting.get('hiringOrganization')) or fallback_company,
        'title': title,
        'location': _location(posting),
        'remote': _remote(posting),
        'job_url': url,
        'description': description,
        'excerpt': description[:800],
        'published_at': str(posting.get('datePosted') or '').strip(),
        'salary_min': salary[0], 'salary_max': salary[1],
        'salary_currency': salary[2], 'salary_period': salary[3],
        'employment_type': _employment_type(posting.get('employmentType')),
    }


def _identifier(posting):
    value = posting.get('identifier')
    if isinstance(value, dict):
        return str(value.get('value') or value.get('name') or '').strip()
    return str(value or '').strip()


def _organisation(value):
    if isinstance(value, dict):
        return str(value.get('name') or '').strip()
    return str(value or '').strip()


def _employment_type(value):
    if isinstance(value, list):
        return ', '.join(str(v) for v in value if v)
    return str(value or '').strip()


def _location(posting):
    places = posting.get('jobLocation')
    places = places if isinstance(places, list) else [places]
    parts = []
    for place in places:
        if not isinstance(place, dict):
            if place:
                parts.append(str(place))
            continue
        address = place.get('address')
        if isinstance(address, str):
            parts.append(address)
            continue
        if not isinstance(address, dict):
            name = str(place.get('name') or '').strip()
            if name:
                parts.append(name)
            continue
        pieces = [str(address.get(key) or '').strip() for key in
                  ('addressLocality', 'addressRegion', 'addressCountry')]
        country = address.get('addressCountry')
        if isinstance(country, dict):
            pieces[2] = str(country.get('name') or '').strip()
        joined = ', '.join(p for p in pieces if p)
        if joined:
            parts.append(joined)
    if not parts:
        applicant = posting.get('applicantLocationRequirements')
        applicant = applicant if isinstance(applicant, list) else [applicant]
        for item in applicant:
            if isinstance(item, dict) and item.get('name'):
                parts.append(str(item['name']))
    return ' | '.join(dict.fromkeys(p for p in parts if p))


def _remote(posting):
    value = str(posting.get('jobLocationType') or '').strip().lower()
    if value == 'telecommute':
        return True
    return None


def _salary(value):
    if not isinstance(value, dict):
        return (None, None, '', '')
    currency = str(value.get('currency') or value.get('salaryCurrency') or '').strip().upper()
    amount = value.get('value')
    if isinstance(amount, dict):
        low = _number(amount.get('minValue') if amount.get('minValue') is not None
                      else amount.get('value'))
        high = _number(amount.get('maxValue'))
        period = str(amount.get('unitText') or '').strip()
        return (low, high if high is not None else low, currency, period)
    return (_number(amount), _number(amount), currency, '')


def _number(value):
    try:
        return float(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


@register
class StructuredJobsAdapter(JobSourceAdapter):
    type_name = 'jsonld'
    label = 'schema.org JobPosting'
    supports_location_query = False
    limitations = ('Only works when a public career page ships its postings as JSON-LD in the '
                   'server response. Pages that render their jobs in the browser are not '
                   'supported and the company stays MANUAL instead of getting a brittle scraper.')

    def fetch_jobs(self, profile, config, source_name):
        config = config or {}
        url = str(config.get('url') or '').strip()
        if not url:
            raise SourceError('schema.org: url is missing.')
        company = str(config.get('company') or source_name).strip()
        html = http_text(url, timeout=30)

        postings = [(url, posting) for posting in extract_job_postings(html)]
        pattern = str(config.get('link_pattern') or '').strip()
        if pattern:
            for page_url, page_html in self._follow(html, url, pattern, config):
                postings.extend((page_url, posting) for posting in extract_job_postings(page_html))

        jobs, seen = [], set()
        for page_url, posting in postings:
            job = normalize_posting(posting, page_url=page_url, fallback_company=company)
            if job is None or not job['job_url']:
                continue
            job.pop('employment_type', None)
            job['source'] = source_name
            job['source_type'] = self.type_name
            job['company'] = job['company'] or company
            if job['external_id'] in seen:
                continue
            seen.add(job['external_id'])
            jobs.append(job)
        if not jobs:
            raise SourceError('schema.org: no JobPosting data found at {0}.'.format(url))
        return jobs

    @staticmethod
    def _follow(html, base_url, pattern, config):
        try:
            matcher = re.compile(pattern)
        except re.error as exc:
            raise SourceError('schema.org: link_pattern is not a valid regex ({0}).'.format(exc))
        links, seen = [], set()
        for href in _HREF.findall(html):
            target = urljoin(base_url, href)
            if target not in seen and matcher.search(target):
                seen.add(target)
                links.append(target)
        limit = int(config.get('max_pages') or DEFAULT_MAX_PAGES)
        return fetch_in_parallel(links[:limit],
                                 lambda link: (link, http_text(link, timeout=30)))
