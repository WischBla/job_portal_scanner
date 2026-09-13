"""Manual job-alert import: preview, deduplicate, store.

The parser (:mod:`jobscanner.linkedin_alert`) turns a saved alert mail into
``ParsedJob`` entries.  This module is what happens next:

    ParsedJob[]  ->  preview (nothing is written)  ->  the user picks  ->  import

Two rules shape everything here.

**An alert is a discovery channel, not a source of truth.**  If the job is
already in the database - normally because the company's own board delivered it
with a full description - the existing record stays authoritative: its source,
its application URL, its description and its score are untouched.  Only the
provenance is recorded, so the card can say that a LinkedIn alert mentioned it
too.  No second card is ever created.

**An alert entry is not a job description.**  A title, a company and a link are
not enough for a trustworthy Personal Fit Score, so a genuinely new entry is
stored, scored by the normal scorer and flagged ``NEEDS_DETAILS``.  Nothing is
invented; the user pastes the real description later and the job is re-scored
by exactly the same pipeline.

The hard filter is deliberately not applied to an import.  It exists to stop a
*source* from flooding the list with jobs nobody asked for; here the user has
looked at every entry in the preview and ticked it themselves.
"""

from . import linkedin_alert
from .db import connect, load_profile, now_iso, row_to_dict, utc_now_iso
from .locations import fold
from .normalizer import JobNormalizer, canonical_url
from .repository import JobRepository
from .schema_v2 import rescore_job
from .scoring import MatchScorer

#: What the card shows in "Source:" for a job that only an alert has seen.
LINKEDIN_SOURCE_NAME = 'LinkedIn alert'
#: Recorded in ``discovered_via``; the canonical ``source`` may well differ.
LINKEDIN_VIA = 'linkedin'

#: Preview verdicts.
NEW = 'NEW'
KNOWN = 'ALREADY KNOWN'

#: Why a preview entry was considered already known.  Shown in the dialog so a
#: match is never a silent decision.
MATCH_LABELS = {
    'external_id': 'Same job id at the original source',
    'url': 'Same application URL',
    'linkedin_id': 'Already imported from a LinkedIn alert',
    'identity': 'Same company, title and location',
}

_NORMALIZER = JobNormalizer()


class AlertImportError(ValueError):
    """Raised when an alert cannot be read at all."""


# --------------------------------------------------------------------------
# Parsing an upload
# --------------------------------------------------------------------------
def parse_alert(text='', filename='', data=None):
    """Read an alert from pasted text or from an uploaded .txt / .eml file."""
    if data is not None:
        entries = linkedin_alert.parse_upload(filename, data)
    else:
        entries = linkedin_alert.parse(text)
    return entries


def sanitize_entry(entry):
    """Re-derive every stored field from the one value that identifies a job.

    The import endpoint is handed the entries back from the preview, so the
    URL is rebuilt from the job id rather than trusted, and the free-text
    fields are trimmed.  Tracking parameters cannot survive this.
    """
    job_id = str((entry or {}).get('source_job_id') or '').strip()
    url = str((entry or {}).get('url') or '').strip()
    if not job_id:
        job_id = linkedin_alert.job_id_of(url)
    return linkedin_alert.ParsedJob({
        'source': LINKEDIN_VIA,
        'title': str((entry or {}).get('title') or '').strip()[:300],
        'company': str((entry or {}).get('company') or '').strip()[:200],
        'location': str((entry or {}).get('location') or '').strip()[:200],
        'source_job_id': job_id,
        'url': linkedin_alert.canonical_job_url(job_id or url),
        'snippet': str((entry or {}).get('snippet') or '').strip()[:200],
    })


# --------------------------------------------------------------------------
# Deduplication
# --------------------------------------------------------------------------
def _candidates(conn):
    return [row_to_dict(r) for r in conn.execute(
        'SELECT id, source, source_type, external_id, source_key, dedupe_key, company, title, '
        'location, raw_location, normalized_city, job_url, description, state, match_score, '
        'linkedin_job_id, discovered_via, needs_details FROM discovered_jobs').fetchall()]


def find_existing(entry, rows):
    """The stored job this alert entry is about, or ``(None, '')``.

    The order is the point.  A canonical identifier from the original source
    beats a URL, a URL beats the LinkedIn job id, and only when none of those
    is available does the coarse company+title+location identity decide.  The
    coarse step is safe here because an alert is by definition a *different*
    channel from the source that stored the job.
    """
    job_id = str(entry.get('source_job_id') or '')
    url = canonical_url(entry.get('url') or '')

    # 1. canonical external id at the original source, when the entry carries one
    external_id = str(entry.get('external_id') or '').strip()
    source_type = str(entry.get('source_type') or '').strip()
    if external_id and source_type:
        key = '{0}:{1}'.format(source_type, external_id)
        for row in rows:
            if row.get('source_key') == key:
                return row, 'external_id'

    # 2. canonical application URL
    if url:
        for row in rows:
            if row.get('dedupe_key') == url or canonical_url(row.get('job_url')) == url:
                return row, 'url'

    # 3. LinkedIn job id, however it was recorded
    if job_id:
        for row in rows:
            if str(row.get('linkedin_job_id') or '') == job_id:
                return row, 'linkedin_id'
            if (row.get('source_type') or '') == LINKEDIN_VIA and \
                    str(row.get('external_id') or '') == job_id:
                return row, 'linkedin_id'
            if linkedin_alert.job_id_of(row.get('job_url')) == job_id:
                return row, 'linkedin_id'

    # 4. normalized company + title + location
    row = _identity_match(entry, rows)
    return (row, 'identity') if row else (None, '')


def _identity_match(entry, rows):
    """Same employer, same role, same place - across two different channels.

    The place is only allowed to *disqualify* a match when both sides actually
    name one: an alert that says "Genf, Genf, Schweiz" and a board that says
    "Geneva, Switzerland" agree once both are normalised, but an entry with no
    location at all must not be blocked from matching by that absence.
    """
    company, title = fold(entry.get('company')), fold(entry.get('title'))
    if not company or not title:
        return None
    place = _place(entry.get('location'))
    for row in rows:
        if fold(row.get('company')) != company or fold(row.get('title')) != title:
            continue
        other = _place(row.get('normalized_city') or row.get('raw_location') or row.get('location'))
        if place and other and place != other:
            continue
        return row
    return None


def _place(value):
    """A location string reduced to the city the normalizer recognises."""
    text = str(value or '').strip()
    if not text:
        return ''
    verdict = _NORMALIZER.locations.normalize(text)
    return fold(verdict.get('normalized_city') or '') or fold(text)


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------
def preview(entries, conn=None):
    """What the import dialog shows.  Reads the database, writes nothing."""
    owns = conn is None
    conn = conn or connect()
    try:
        rows = _candidates(conn)
        out, seen = [], set()
        for raw in entries:
            entry = sanitize_entry(raw)
            if not entry['title'] or not entry['company']:
                continue
            if entry.key in seen:
                continue
            seen.add(entry.key)
            existing, reason = find_existing(entry, rows)
            item = dict(entry)
            item['status'] = KNOWN if existing else NEW
            item['match_reason'] = MATCH_LABELS.get(reason, '') if existing else ''
            item['existing_job_id'] = existing['id'] if existing else None
            item['existing_source'] = (existing.get('source') or '') if existing else ''
            item['needs_details'] = not (existing and (existing.get('description') or '').strip())
            out.append(item)
        return {
            'source': LINKEDIN_VIA,
            'found': len(out),
            'new': sum(1 for item in out if item['status'] == NEW),
            'known': sum(1 for item in out if item['status'] == KNOWN),
            'jobs': out,
        }
    finally:
        if owns:
            conn.close()


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------
def import_entries(entries, conn=None):
    """Store the entries the user ticked.

    Returns ``{'imported': n, 'linked': n, 'needs_details': n, 'job_ids': [...]}``.
    ``linked`` counts entries that turned out to be a job the database already
    had: those update provenance only.
    """
    owns = conn is None
    conn = conn or connect()
    try:
        profile = load_profile(conn)
        scorer = MatchScorer()
        repo = JobRepository(conn)
        rows = _candidates(conn)
        result = {'imported': 0, 'linked': 0, 'needs_details': 0, 'job_ids': [], 'skipped': 0}
        seen = set()

        for raw in entries:
            entry = sanitize_entry(raw)
            if not entry['title'] or not entry['company'] or entry.key in seen:
                result['skipped'] += 1
                continue
            seen.add(entry.key)
            existing, _reason = find_existing(entry, rows)
            if existing:
                _record_provenance(conn, existing['id'], existing, entry)
                result['linked'] += 1
                result['job_ids'].append(existing['id'])
                continue
            job_id = _insert(conn, repo, scorer, profile, entry)
            result['imported'] += 1
            result['needs_details'] += 1
            result['job_ids'].append(job_id)
            rows = _candidates(conn)      # so two identical entries collapse
        conn.commit()
        return result
    finally:
        if owns:
            conn.close()


def _insert(conn, repo, scorer, profile, entry):
    """A genuinely new job: normalise, score, store, flag as incomplete."""
    job = _NORMALIZER.normalize({
        'source': LINKEDIN_SOURCE_NAME,
        'source_type': LINKEDIN_VIA,
        'external_id': entry['source_job_id'],
        'company': entry['company'],
        'title': entry['title'],
        'location': entry['location'],
        'job_url': entry['url'],
        'description': '',
        # The alert's informational line is context, not a description; it is
        # shown, and it is never scored as if it described the role.
        'excerpt': entry['snippet'],
    })
    scored = scorer.score(job, profile)
    job_id, _is_new = repo.upsert(job, scored, utc_now_iso())
    conn.execute(
        'UPDATE discovered_jobs SET discovered_via=?, linkedin_job_id=?, linkedin_url=?, '
        'needs_details=1 WHERE id=?',
        (LINKEDIN_VIA, entry['source_job_id'], entry['url'], job_id))
    return job_id


def _record_provenance(conn, job_id, existing, entry):
    """The alert saw a job the database already had.

    Provenance only: the canonical source, the application URL, the
    description and the score of the existing record are left exactly as they
    are, which is what keeps an ATS job an ATS job.

    The one exception is a job a previous scan retired.  An alert advertising
    it is evidence that the posting is open again, and an EXPIRED job is
    hidden from the Jobs screen - so linking one would otherwise appear to do
    nothing at all.  It comes back as SEEN; a state the user chose by hand
    (SAVED / IGNORED / APPLIED) is never touched.
    """
    conn.execute(
        'UPDATE discovered_jobs SET discovered_via=CASE WHEN discovered_via=\'\' THEN ? '
        'ELSE discovered_via END, linkedin_job_id=?, linkedin_url=?, last_seen=? WHERE id=?',
        (LINKEDIN_VIA, entry['source_job_id'], entry['url'], utc_now_iso(), job_id))
    if (existing or {}).get('state') == 'EXPIRED':
        conn.execute("UPDATE discovered_jobs SET state='SEEN', state_changed_at=? WHERE id=?",
                     (now_iso(), job_id))


# --------------------------------------------------------------------------
# The description a LinkedIn entry could not carry
# --------------------------------------------------------------------------
def add_description(job_id, description, conn=None):
    """Attach the real job description and re-score with the normal pipeline.

    No LinkedIn-specific scoring exists: this is ``MatchScorer`` plus the two
    personal-fit adjustments, the same call the scan pipeline makes.
    """
    text = str(description or '').strip()
    if not text:
        raise AlertImportError('Paste the job description first.')
    owns = conn is None
    conn = conn or connect()
    try:
        row = conn.execute('SELECT * FROM discovered_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return None
        job = row_to_dict(row)
        conn.execute('UPDATE discovered_jobs SET description=?, excerpt=?, needs_details=0, '
                     'state_changed_at=? WHERE id=?',
                     (text, text[:800], now_iso(), job_id))
        job['description'] = text
        job['excerpt'] = text[:800]
        rescore_job(conn, job, load_profile(conn))
        conn.commit()
        from . import jobs_service
        return jobs_service.get_card(job_id, conn, with_ai=False)
    finally:
        if owns:
            conn.close()


#: Re-exported so callers need one import for the whole feature.
__all__ = ['AlertImportError', 'parse_alert', 'preview', 'import_entries',
           'add_description', 'find_existing', 'sanitize_entry', 'NEW', 'KNOWN',
           'LINKEDIN_VIA', 'LINKEDIN_SOURCE_NAME']
