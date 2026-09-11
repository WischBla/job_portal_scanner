"""Generic RSS / Atom job feed adapter."""

import re
import xml.etree.ElementTree as ET

from .base import JobSourceAdapter, SourceError, http_bytes, register
from .html_text import strip_html

ATOM = '{http://www.w3.org/2005/Atom}'


@register
class RssAdapter(JobSourceAdapter):
    type_name = 'rss'
    label = 'RSS / Atom'
    supports_location_query = False
    limitations = ('Feeds rarely carry a structured location field. The adapter therefore tries '
                   'to read the location out of the title ("… (Zurich)") and otherwise leaves it '
                   'empty, which means the local filter has to rely on description evidence.')

    def fetch_jobs(self, profile, config, source_name):
        url = str((config or {}).get('url') or '').strip()
        if not url:
            raise SourceError('RSS: feed URL is missing.')
        try:
            root = ET.fromstring(http_bytes(url))
        except ET.ParseError as exc:
            raise SourceError('RSS: feed is not valid XML ({0}).'.format(exc)) from exc

        company = str((config or {}).get('company') or source_name)
        jobs = []
        items = root.findall('.//item')
        if items:
            for item in items:
                title = (item.findtext('title') or '').strip()
                link = (item.findtext('link') or '').strip()
                description = strip_html(item.findtext('description') or '')
                jobs.append(self._job(source_name, company,
                                      (item.findtext('guid') or link or title).strip(),
                                      title, link, description,
                                      item.findtext('pubDate') or ''))
        else:
            for entry in root.findall('.//{0}entry'.format(ATOM)):
                title = (entry.findtext('{0}title'.format(ATOM)) or '').strip()
                link_el = entry.find('{0}link'.format(ATOM))
                link = (link_el.attrib.get('href') or '').strip() if link_el is not None else ''
                description = strip_html(entry.findtext('{0}summary'.format(ATOM)) or
                                         entry.findtext('{0}content'.format(ATOM)) or '')
                jobs.append(self._job(source_name, company,
                                      (entry.findtext('{0}id'.format(ATOM)) or link or title).strip(),
                                      title, link, description,
                                      entry.findtext('{0}updated'.format(ATOM)) or
                                      entry.findtext('{0}published'.format(ATOM)) or ''))
        return jobs

    def _job(self, source_name, company, external_id, title, link, description, published):
        return {
            'source': source_name, 'source_type': self.type_name,
            'external_id': external_id,
            'company': company,
            'title': title,
            'location': self._location_from_title(title),
            'remote': None,
            'job_url': link,
            'description': description,
            'excerpt': description[:800],
            'published_at': published,
            'salary_min': None, 'salary_max': None,
            'salary_currency': '', 'salary_period': '',
        }

    @staticmethod
    def _location_from_title(title):
        """Feeds often encode the location as '(Zurich)' or ' - Zurich' in the title."""
        match = re.search(r'\(([^()]{2,40})\)\s*$', title or '')
        if match:
            return match.group(1).strip()
        match = re.search(r'\s[-–|]\s([A-Za-zÄÖÜäöüéèàç .\'-]{2,40})$', title or '')
        return match.group(1).strip() if match else ''
