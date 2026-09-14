"""Finding the job description a discovery channel could not carry.

A LinkedIn alert gives a title, a company, a location and a link.  That is
enough to *discover* a job and nowhere near enough to judge one, so this module
does the second half: it goes looking for the canonical description through
sources the scanner is already allowed to use, and when it cannot find one it
says so instead of inventing anything.

Four steps, cheapest and most trustworthy first:

1.  **The local database.**  The job is very often already here, delivered in
    full by the company's own board under a different URL.  Matching it is
    free, offline and exact.

2.  **The company's configured source.**  If the employer is on the watchlist
    with a source that has *proven* it answers (ACTIVE - a claimed endpoint is
    not enough), that adapter is asked for its board and the posting is matched
    by title and location.  One fetch per company per run, cached, so importing
    ten roles from one employer costs one request.

3.  **The public original posting.**  If the job already has a public
    ATS/careers URL, that page is fetched through the same safe mechanism the
    ``jsonld`` source uses and its schema.org ``JobPosting`` description is
    read.

4.  **Nothing.**  The job keeps its NEEDS_ENRICHMENT state, its card says "no
    canonical description found", and it stays exactly where it was.  A failed
    enrichment never damages a job.

Two things this module will not do, by construction rather than by intention:

*   **It never touches LinkedIn.**  ``_is_linkedin`` excludes those URLs from
    step 3, so the one place a LinkedIn link could have been fetched is closed.
    LinkedIn is a discovery channel here and nothing else.
*   **It never writes a description it did not receive from a source.**  There
    is no summarisation, no generation and no reuse of an alert's teaser line.

Whenever a description *is* found, the job is re-scored by the ordinary
pipeline - the same ``MatchScorer`` and the same two adjustments a scanned job
gets.  There is no enrichment-specific scoring.
"""

import json

from . import evidence as evidence_mod
from .db import connect, load_profile, now_iso, row_to_dict
from .locations import fold
from .normalizer import canonical_url
from .schema_v2 import rescore_job
from .sources import base as source_base
from .sources import registry
from .sources.structured_jobs import extract_job_postings, normalize_posting

#: Where a description came from.  Stored on the job, shown on the card: the
#: user should never have to wonder whether a description is the real posting.
FROM_LOCAL_DUPLICATE = 'local_duplicate'
FROM_COMPANY_SOURCE = 'company_source'
FROM_PUBLIC_POSTING = 'public_posting'
FROM_MANUAL = 'manual'
SOURCE_LABELS = {
    FROM_LOCAL_DUPLICATE: 'Reused from a job already in your database',
    FROM_COMPANY_SOURCE: 'Matched against the company\'s own job board',
    FROM_PUBLIC_POSTING: 'Read from the public original posting',
    FROM_MANUAL: 'Pasted by you',
}

#: Outcomes of one enrichment attempt.
ENRICHED = 'ENRICHED'
UNCHANGED = 'UNCHANGED'
NOT_FOUND = 'NOT_FOUND'

NOT_FOUND_MESSAGE = 'No canonical description found'

#: What a candidate has to clear before it counts as a find.  A board that
#: answers with a non-breaking space has not given us a job description, and
#: storing it would turn "we still know nothing" into "enrichment succeeded" -
#: exactly the kind of false success this release exists to remove.  It also
#: has to say more than whatever is already stored.
MIN_USEFUL_DESCRIPTION = evidence_mod.MIN_TEXT

#: A match has to be this certain before someone else's advert is attached to
#: this job.  Title and company must agree; the location may only disagree when
#: one of the two sides does not state one.
_MIN_TITLE_OVERLAP = 0.8


def _is_linkedin(url):
    return 'linkedin.com' in str(url or '').casefold()


def _title_key(value):
    return fold(value)


def _title_matches(wanted, candidate):
    """Same role, allowing for the decoration boards add to a title.

    "Head of SRE" and "Head of SRE (m/w/d)" are one job; "Head of SRE" and
    "Head of Security" are not.  Containment in either direction, with a length
    guard, is precise enough for that and simple enough to reason about.
    """
    a, b = _title_key(wanted), _title_key(candidate)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return shorter in longer and len(shorter) >= _MIN_TITLE_OVERLAP * len(longer)


def _place(value):
    from .alert_import import _place as place_of
    return place_of(value)


def _locations_agree(wanted, candidate):
    """Absence never disqualifies - only two different stated places do."""
    a, b = _place(wanted), _place(candidate)
    return not a or not b or a == b


# --------------------------------------------------------------------------
# The four steps
# --------------------------------------------------------------------------
def find_local_duplicate(conn, job):
    """A job already in the database that is the same posting, with a text.

    Matched on the canonical URL first, then on company + title + location.
    The row has to carry a real description - reusing an empty one would be a
    no-op dressed up as a success.
    """
    url = canonical_url(job.get('job_url'))
    linkedin_id = str(job.get('linkedin_job_id') or '')
    rows = conn.execute(
        'SELECT id, company, title, normalized_city, raw_location, job_url, dedupe_key, '
        'description, source, linkedin_job_id FROM discovered_jobs '
        'WHERE id<>? AND LENGTH(TRIM(description)) >= ?',
        (job['id'], MIN_USEFUL_DESCRIPTION)).fetchall()
    for row in rows:
        other = row_to_dict(row)
        if url and (other.get('dedupe_key') == url or canonical_url(other.get('job_url')) == url):
            return other
        if linkedin_id and str(other.get('linkedin_job_id') or '') == linkedin_id:
            return other
    for row in rows:
        other = row_to_dict(row)
        if fold(other.get('company')) != fold(job.get('company')):
            continue
        if not _title_matches(job.get('title'), other.get('title')):
            continue
        if not _locations_agree(job.get('normalized_city') or job.get('raw_location'),
                                other.get('normalized_city') or other.get('raw_location')):
            continue
        return other
    return None


def company_sources(conn, company):
    """The proven public sources configured for one employer.

    ACTIVE only.  A watchlist entry that merely *claims* a Greenhouse token has
    not shown that the token is right, and asking a wrong endpoint for a
    description is how the wrong advert gets attached to a job.
    """
    rows = conn.execute(
        'SELECT w.company_name, w.career_source_type, w.career_source_identifier, '
        's.name AS source_name, s.source_type, s.config_json '
        'FROM company_watchlist w JOIN job_sources s ON s.id = w.source_id '
        "WHERE w.enabled=1 AND s.enabled=1 AND w.source_status='ACTIVE' "
        'AND w.company_name=? COLLATE NOCASE', (str(company or ''),)).fetchall()
    return [row_to_dict(r) for r in rows]


class Session(object):
    """One enrichment run: an import, or one click of ``Enrich``.

    Exists for the cache.  Importing twelve roles from one employer must ask
    that employer's board once, not twelve times - which is also the difference
    between being a well-behaved client of a public API and being a burst of
    traffic.
    """

    def __init__(self, conn, fetcher=None, page_fetcher=None):
        self.conn = conn
        self._fetch = fetcher if fetcher is not None else _fetch_company_source
        self._page = page_fetcher if page_fetcher is not None else _fetch_public_posting
        self._boards = {}
        self._profile = None
        self.last_error = ''

    @property
    def profile(self):
        if self._profile is None:
            self._profile = load_profile(self.conn)
        return self._profile

    def board(self, source):
        """This employer's postings, fetched at most once per session."""
        key = source.get('source_name') or source.get('source_type')
        if key not in self._boards:
            try:
                self._boards[key] = self._fetch(source) or []
            except Exception as exc:            # noqa: BLE001 - a source is allowed to fail
                self._boards[key] = []
                self.last_error = str(exc)[:300]
        return self._boards[key]

    # -- the attempt ------------------------------------------------------
    def enrich(self, job_id):
        """Try to give one job its canonical description.

        Returns ``{'status', 'source', 'detail', 'job_id'}``.  On failure the
        stored job is not modified in any way: an enrichment that finds nothing
        must be indistinguishable from one that never ran.
        """
        row = self.conn.execute('SELECT * FROM discovered_jobs WHERE id=?',
                                (job_id,)).fetchone()
        if row is None:
            return {'status': NOT_FOUND, 'source': '', 'detail': 'Job not found.',
                    'job_id': job_id}
        job = row_to_dict(row)
        if evidence_mod.assess(job)['level'] != evidence_mod.LOW:
            return {'status': UNCHANGED, 'source': job.get('enrichment_source') or '',
                    'detail': 'Already has a usable description.', 'job_id': job_id}

        for step in (self._from_database, self._from_company_source, self._from_public_posting):
            found = step(job)
            if found:
                description, origin, detail, extra = found
                return self._attach(job, description, origin, detail, extra)

        self._record_attempt(job_id, NOT_FOUND_MESSAGE)
        return {'status': NOT_FOUND, 'source': '', 'detail': NOT_FOUND_MESSAGE,
                'job_id': job_id}

    # -- step 1 -----------------------------------------------------------
    def _from_database(self, job):
        other = find_local_duplicate(self.conn, job)
        if not other or len(str(other['description'] or '').strip()) < MIN_USEFUL_DESCRIPTION:
            return None
        return (other['description'], FROM_LOCAL_DUPLICATE,
                'Reused the description already stored for the same posting from {0}.'.format(
                    other.get('source') or 'another source'),
                {})

    # -- step 2 -----------------------------------------------------------
    def _from_company_source(self, job):
        for source in company_sources(self.conn, job.get('company')):
            for posting in self.board(source):
                if not _title_matches(job.get('title'), posting.get('title')):
                    continue
                if not _locations_agree(job.get('normalized_city') or job.get('raw_location'),
                                        posting.get('location')):
                    continue
                description = str(posting.get('description') or '').strip()
                if len(description) < MIN_USEFUL_DESCRIPTION:
                    continue
                return (description, FROM_COMPANY_SOURCE,
                        'Matched "{0}" on {1}\'s own {2} board.'.format(
                            posting.get('title'), source.get('company_name'),
                            registry.source_label(source.get('source_type'))),
                        {'job_url': posting.get('job_url') or ''})
        return None

    # -- step 3 -----------------------------------------------------------
    def _from_public_posting(self, job):
        url = str(job.get('job_url') or '').strip()
        if not url.startswith('http') or _is_linkedin(url):
            return None
        try:
            description = self._page(url, job.get('company'))
        except Exception:                        # noqa: BLE001 - a dead page is not a crash
            return None
        if len(str(description or '').strip()) < MIN_USEFUL_DESCRIPTION:
            return None
        return (description, FROM_PUBLIC_POSTING,
                'Read the schema.org job posting from the original page.', {})

    # -- writing ----------------------------------------------------------
    def _attach(self, job, description, origin, detail, extra):
        """Store a description that came from a source, then re-score normally."""
        text = str(description or '').strip()
        stored = str(job.get('description') or '').strip()
        if len(text) < MIN_USEFUL_DESCRIPTION or len(text) <= len(stored):
            self._record_attempt(job['id'], NOT_FOUND_MESSAGE)
            return {'status': NOT_FOUND, 'source': '', 'detail': NOT_FOUND_MESSAGE,
                    'job_id': job['id']}

        url = str((extra or {}).get('job_url') or '').strip()
        self.conn.execute(
            'UPDATE discovered_jobs SET description=?, excerpt=?, enrichment_source=?, '
            'enrichment_detail=?, enrichment_at=? WHERE id=?',
            (text, text[:800], origin, detail[:400], now_iso(), job['id']))
        job['description'] = text
        job['excerpt'] = text[:800]
        # A canonical application URL is worth adopting - but only when it does
        # not collide with another stored job, because ``dedupe_key`` is how
        # the next scan recognises this row.
        if url and not _is_linkedin(url) and self._url_is_free(url, job['id']):
            self.conn.execute('UPDATE discovered_jobs SET job_url=?, dedupe_key=? WHERE id=?',
                              (url, canonical_url(url), job['id']))
            job['job_url'] = url
        rescore_job(self.conn, job, self.profile)
        report = evidence_mod.assess(job)
        self.conn.execute('UPDATE discovered_jobs SET needs_details=? WHERE id=?',
                          (1 if report['state'] == evidence_mod.NEEDS_ENRICHMENT else 0,
                           job['id']))
        return {'status': ENRICHED, 'source': origin, 'detail': detail,
                'job_id': job['id'], 'evidence': report['level']}

    def _url_is_free(self, url, job_id):
        key = canonical_url(url)
        row = self.conn.execute(
            'SELECT 1 FROM discovered_jobs WHERE dedupe_key=? AND id<>? LIMIT 1',
            (key, job_id)).fetchone()
        return row is None

    def _record_attempt(self, job_id, message):
        """A failed attempt is recorded on the job and changes nothing else.

        ``enrichment_source`` is cleared rather than left behind: a job with no
        description must not claim one came from anywhere.
        """
        self.conn.execute(
            "UPDATE discovered_jobs SET enrichment_detail=?, enrichment_at=?, "
            "enrichment_source=CASE WHEN TRIM(description)='' THEN '' "
            'ELSE enrichment_source END WHERE id=?',
            (message[:400], now_iso(), job_id))


# --------------------------------------------------------------------------
# The two real fetchers (injectable, so the tests never touch the network)
# --------------------------------------------------------------------------
def _fetch_company_source(source):
    """Ask one configured company source for its current board."""
    try:
        config = json.loads(source.get('config_json') or '{}')
    except (TypeError, ValueError):
        config = {}
    adapter = source_base.get_adapter(source.get('source_type'))
    source_base.start_probe(source.get('source_name') or '')
    return adapter.fetch_jobs({}, config, source.get('source_name') or '') or []


def _fetch_public_posting(url, company=''):
    """The description of a public posting page, from its structured data.

    Structured data only.  Scraping the visible text of an arbitrary careers
    page produces navigation menus and cookie banners, and storing that as a
    job description would be worse than storing nothing - it would be evidence
    that is not evidence.
    """
    html = source_base.http_text(url, timeout=20)
    for posting in extract_job_postings(html):
        normalized = normalize_posting(posting, page_url=url, fallback_company=company)
        if normalized and str(normalized.get('description') or '').strip():
            return normalized['description'].strip()
    return ''


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------
def enrich_job(job_id, conn=None, session=None):
    """One job, one attempt.  What the ``Enrich`` button calls."""
    owns = conn is None
    conn = conn or connect()
    try:
        result = (session or Session(conn)).enrich(job_id)
        conn.commit()
        return result
    finally:
        if owns:
            conn.close()


def enrich_jobs(job_ids, conn, session=None):
    """One attempt for each of several jobs, sharing one session.

    This is the automatic pass after an import: one import, one enrichment
    attempt per job, no background daemon and no repeated crawling.  A failure
    on one job never stops the others.
    """
    session = session or Session(conn)
    summary = {'attempted': 0, 'enriched': 0, 'unchanged': 0, 'not_found': 0, 'results': []}
    for job_id in job_ids or []:
        summary['attempted'] += 1
        try:
            result = session.enrich(job_id)
        except Exception as exc:                 # noqa: BLE001 - never lose the rest
            result = {'status': NOT_FOUND, 'source': '', 'job_id': job_id,
                      'detail': str(exc)[:200]}
        summary['results'].append(result)
        if result['status'] == ENRICHED:
            summary['enriched'] += 1
        elif result['status'] == UNCHANGED:
            summary['unchanged'] += 1
        else:
            summary['not_found'] += 1
    conn.commit()
    return summary


__all__ = ['Session', 'enrich_job', 'enrich_jobs', 'find_local_duplicate', 'company_sources',
           'ENRICHED', 'UNCHANGED', 'NOT_FOUND', 'NOT_FOUND_MESSAGE', 'SOURCE_LABELS',
           'FROM_LOCAL_DUPLICATE', 'FROM_COMPANY_SOURCE', 'FROM_PUBLIC_POSTING', 'FROM_MANUAL']
