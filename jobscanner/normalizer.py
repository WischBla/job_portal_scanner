"""JobNormalizer - raw adapter payload -> NormalizedJob.

Adapters are only allowed to produce the flat RAW_FIELDS structure.  Every
interpretation (location, work model, seniority, dedupe identity) happens here
so that portal quirks cannot leak into filtering or the UI.
"""

import hashlib
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from .locations import LocationNormalizer, fold

RAW_FIELDS = ('source', 'source_type', 'external_id', 'company', 'title', 'location',
              'remote', 'job_url', 'description', 'excerpt', 'published_at',
              'salary_min', 'salary_max', 'salary_currency', 'salary_period')

SENIORITY_RULES = [
    ('Senior Director', [r'\bsenior director\b', r'\bsr\.? director\b']),
    ('VP', [r'\bvice president\b', r'\bvp\b', r'\bsvp\b', r'\bevp\b', r'\bchief\b', r'\bcto\b', r'\bcio\b']),
    ('Head of', [r'\bhead of\b', r'^head\b', r'\bleiter(in)?\b', r'\bbereichsleit']),
    ('Director', [r'\bdirector\b', r'\bdirektor(in)?\b']),
    ('Principal', [r'\bprincipal\b', r'\bdistinguished\b']),
    ('Global Lead', [r'\bglobal lead\b', r'\bglobal head\b']),
    ('Senior Lead', [r'\bsenior lead\b', r'\bstaff\b', r'\bsenior principal\b']),
    ('Senior Manager', [r'\bsenior\b[\w\s,./-]*\bmanager\b', r'\bsr\.? manager\b',
                        r'\bsenior engineering manager\b', r'\bgroup manager\b']),
    ('Lead', [r'\blead\b', r'\bleadership\b', r'\bteam lead\b', r'\btech lead\b']),
    ('Manager', [r'\bmanager\b', r'\bmanagerin\b']),
]

_TRACKING_PARAMS = re.compile(r'^(utm_|gh_|lever-|ref$|source$|src$)', re.IGNORECASE)


def canonical_url(url):
    """Strip tracking noise so the same posting from two feeds dedupes."""
    text = str(url or '').strip()
    if not text:
        return ''
    try:
        parts = urlsplit(text)
    except ValueError:
        return text.casefold()
    query = '&'.join(
        piece for piece in parts.query.split('&')
        if piece and not _TRACKING_PARAMS.match(piece.split('=')[0])
    )
    host = parts.netloc.casefold()
    if host.startswith('www.'):
        host = host[4:]
    path = parts.path.rstrip('/')
    return urlunsplit((parts.scheme.casefold(), host, path, query, '')).casefold()


def normalize_published(value):
    if value in (None, ''):
        return ''
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).replace(
                microsecond=0).isoformat().replace('+00:00', 'Z')
        except (OverflowError, OSError, ValueError):
            return str(value)
    return str(value).strip()


def detect_seniority(title):
    text = fold(title)
    for label, patterns in SENIORITY_RULES:
        for pattern in patterns:
            if re.search(pattern, text):
                return label
    return 'Other'


class JobNormalizer:
    def __init__(self, location_normalizer=None):
        self.locations = location_normalizer or LocationNormalizer()

    def normalize(self, raw):
        title = str(raw.get('title') or '').strip()
        company = str(raw.get('company') or '').strip()
        raw_location = str(raw.get('location') or '').strip()
        description = str(raw.get('description') or '').strip()
        excerpt = str(raw.get('excerpt') or '').strip() or description[:800]
        url = str(raw.get('job_url') or '').strip()
        source = str(raw.get('source') or '').strip()
        source_type = str(raw.get('source_type') or '').strip()
        external_id = str(raw.get('external_id') or '').strip()

        verdict = self.locations.normalize(
            raw_location, title=title, description=description, remote_hint=raw.get('remote'))

        source_key = '{0}:{1}'.format(source_type or source, external_id or canonical_url(url) or
                                      _hash(company, title, raw_location))
        dedupe_key = canonical_url(url) or _hash(company, title, verdict['normalized_city'] or raw_location)

        return {
            'source': source,
            'source_type': source_type,
            'external_id': external_id,
            'source_key': source_key,
            'dedupe_key': dedupe_key,
            'company': company,
            'title': title,
            'location': raw_location,
            'raw_location': raw_location,
            'job_url': url,
            'description': description,
            'excerpt': excerpt,
            'published_at': normalize_published(raw.get('published_at')),
            'salary_min': _as_float(raw.get('salary_min')),
            'salary_max': _as_float(raw.get('salary_max')),
            'salary_currency': str(raw.get('salary_currency') or '').strip().upper(),
            'salary_period': str(raw.get('salary_period') or '').strip(),
            'seniority': detect_seniority(title),
            'normalized_country': verdict['normalized_country'] or '',
            'normalized_city': verdict['normalized_city'] or '',
            'normalized_region': verdict['normalized_region'] or '',
            'is_remote': bool(verdict['is_remote']),
            'is_hybrid': bool(verdict['is_hybrid']),
            'is_onsite': bool(verdict['is_onsite']),
            'work_model': verdict['work_model'],
            'office_days': verdict['office_days'],
            'switzerland_eligible': bool(verdict['switzerland_eligible']),
            'location_confidence': verdict['location_confidence'],
            'location_reason': verdict['reason'],
            'location_evidence': verdict['evidence'],
        }


def _as_float(value):
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hash(*parts):
    blob = '|'.join(fold(p) for p in parts)
    return hashlib.sha1(blob.encode('utf-8')).hexdigest()[:20]
