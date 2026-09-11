"""Lever postings API - one company's careers page per configured source."""

from .base import JobSourceAdapter, SourceError, http_json, register
from .html_text import strip_html


@register
class LeverAdapter(JobSourceAdapter):
    type_name = 'lever'
    label = 'Lever'
    supports_location_query = False
    limitations = ('Returns the whole posting list; filtering is local. Locations come from the '
                   'employer\'s own category field and are often city-only ("Zurich") or a '
                   'region ("EMEA").')

    def fetch_jobs(self, profile, config, source_name):
        site = str((config or {}).get('site') or '').strip()
        if not site:
            raise SourceError('Lever: site is missing.')
        region = str((config or {}).get('region') or 'global').strip().lower()
        base = ('https://api.eu.lever.co/v0/postings' if region == 'eu'
                else 'https://api.lever.co/v0/postings')
        data = http_json('{0}/{1}?mode=json'.format(base, site))
        jobs = []
        for item in (data or []):
            external_id = str(item.get('id') or item.get('hostedUrl') or '')
            if not external_id:
                continue
            categories = item.get('categories') or {}
            location = categories.get('location') if isinstance(categories, dict) else ''
            description = ' '.join([
                strip_html(item.get('descriptionPlain') or item.get('description') or ''),
                strip_html(item.get('additionalPlain') or item.get('additional') or ''),
            ]).strip()
            jobs.append({
                'source': source_name, 'source_type': self.type_name,
                'external_id': external_id,
                'company': str((config or {}).get('company') or source_name).strip(),
                'title': str(item.get('text') or '').strip(),
                'location': str(location or '').strip(),
                'remote': None,
                'job_url': str(item.get('hostedUrl') or item.get('applyUrl') or '').strip(),
                'description': description,
                'excerpt': description[:800],
                'published_at': item.get('createdAt'),
                'salary_min': None, 'salary_max': None,
                'salary_currency': '', 'salary_period': '',
            })
        return jobs
