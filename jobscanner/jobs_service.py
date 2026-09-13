"""Assembling what the Jobs screen shows.

The screen is deliberately dumb: it renders exactly what this module returns.
Every card carries a score, a classification, at most five reasons, at most
three concerns and a compensation estimate - with or without AI.
"""

import json
from datetime import datetime, timezone

from . import compensation
from .ai import service as ai_service
from .db import connect, row_to_dict
from .settings import load_settings

MAX_REASONS = 5
MAX_CONCERNS = 3

EXCELLENT_FROM = 80
STRONG_FROM = 70
REVIEW_FROM = 60
WEAK_FROM = 50

#: Company priority breaks ties and nothing else.  A priority-A job that scored
#: 64 must never appear above a priority-B job that scored 82, so the score is
#: always the first term of the ordering and the posting date the last.
PRIORITY_ORDER = "CASE w.priority WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 ELSE 3 END"

#: Personal verdict on a job.  Recorded for later calibration only - nothing
#: in the scorer or the filters reads it, so a "NO" never silently changes how
#: the next scan behaves.
FEEDBACK_VALUES = ('', 'YES', 'MAYBE', 'NO')
FEEDBACK_REASONS = [
    'Too operational', 'Too junior', 'Too commercial', 'Too much consulting',
    'Wrong location', 'Insufficient technical responsibility',
    'Insufficient leadership scope', 'Compensation concern', 'Other',
]

#: States the Jobs screen understands.
OPEN_STATES = ('NEW', 'SEEN', 'SAVED', 'APPLIED')
#: Never shown as a current recommendation: ignored by the user, or retired by
#: the source because the posting is gone.
HIDDEN_STATES = ('IGNORED', 'EXPIRED')


def classify(score):
    """The band label.  Fixed thresholds, so "Excellent" always means 80+."""
    score = int(score or 0)
    if score >= EXCELLENT_FROM:
        return 'Excellent'
    if score >= STRONG_FROM:
        return 'Strong'
    if score >= REVIEW_FROM:
        return 'Review'
    return 'Weak' if score >= WEAK_FROM else 'Below threshold'


#: Band -> the sentence the Jobs screen shows next to the score.
BAND_LABELS = {
    'Excellent': 'Excellent / high priority',
    'Strong': 'Strong match',
    'Review': 'Worth reviewing',
    'Weak': 'Weak / edge match',
    'Below threshold': 'Below the display threshold',
}


def posting_age(published_at, first_seen):
    """Human posting age; falls back to when the scanner first saw the job."""
    stamp = _parse(published_at) or _parse(first_seen)
    if stamp is None:
        return ''
    delta = datetime.now(timezone.utc) - stamp
    days = delta.days
    if days <= 0:
        hours = max(1, int(delta.total_seconds() // 3600))
        return 'today' if hours >= 6 else '{0}h ago'.format(hours)
    if days == 1:
        return 'yesterday'
    if days < 14:
        return '{0} days ago'.format(days)
    if days < 60:
        return '{0} weeks ago'.format(days // 7)
    return '{0} months ago'.format(days // 30)


def _parse(value):
    text = str(value or '').strip()
    if not text:
        return None
    text = text.replace('Z', '+00:00')
    for parser in (datetime.fromisoformat,):
        try:
            stamp = parser(text)
            return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    for fmt in ('%a, %d %b %Y %H:%M:%S %z', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            stamp = datetime.strptime(text, fmt)
            return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _json_list(value):
    if isinstance(value, list):
        return value
    try:
        data = json.loads(value or '[]')
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def _sentence(text):
    text = str(text or '').strip()
    if not text:
        return ''
    return text[0].upper() + text[1:]


def to_card(row, conn, settings, person=None, with_ai=False, full=False):
    """One stored job -> the card the UI renders."""
    job = dict(row)
    score = int(job.get('match_score') or 0)
    reasons = [_sentence(r) for r in _json_list(job.get('match_reasons'))][:MAX_REASONS]
    concerns = [_sentence(c) for c in _json_list(job.get('match_concerns'))][:MAX_CONCERNS]

    estimate = None
    if settings.get('salary_show_estimates', True):
        estimate = compensation.get_or_create(job, settings=settings, conn=conn)

    card = {
        'id': job['id'],
        'title': job.get('title') or '',
        'company': job.get('company') or '',
        'location': job.get('normalized_city') or job.get('raw_location') or 'Switzerland',
        'country': job.get('normalized_country') or '',
        'work_model': job.get('work_model') or 'Unknown',
        'office_days': job.get('office_days'),
        'seniority': job.get('seniority') or '',
        'source': job.get('source') or '',
        'url': job.get('job_url') or '',
        'score': score,
        'classification': classify(score),
        'band': BAND_LABELS.get(classify(score), ''),
        'feedback': job.get('feedback') or '',
        'feedback_reason': job.get('feedback_reason') or '',
        'reasons': reasons,
        'concerns': concerns,
        'age': posting_age(job.get('published_at'), job.get('first_seen')),
        'published_at': job.get('published_at') or '',
        'first_seen': job.get('first_seen') or '',
        'state': job.get('state') or 'NEW',
        'is_new': bool(job.get('is_new')),
        'application_id': job.get('application_id'),
        'compensation': estimate,
    }
    if full:
        card['description'] = job.get('description') or job.get('excerpt') or ''
        card['breakdown'] = _json_list(job.get('match_breakdown'))
        card['location_reason'] = job.get('location_reason') or ''
        card['all_reasons'] = [_sentence(r) for r in _json_list(job.get('match_reasons'))]
        card['all_concerns'] = [_sentence(c) for c in _json_list(job.get('match_concerns'))]
    if with_ai:
        card['ai'] = ai_service.cached_analysis(job['id'], conn)
    return card


def list_cards(conn=None, state='', limit=200, include_ignored=False):
    owns = conn is None
    conn = conn or connect()
    try:
        settings = load_settings(conn)
        sql = ('SELECT j.* FROM discovered_jobs j '
               'LEFT JOIN company_watchlist w ON w.company_name = j.company COLLATE NOCASE')
        params = []
        if state:
            sql += ' WHERE j.state=?'
            params.append(state)
        elif not include_ignored:
            sql += " WHERE j.state NOT IN ('IGNORED', 'EXPIRED')"
        # Contract of the Jobs screen: score first, company priority only as a
        # tie-breaker, newest last.
        sql += (' ORDER BY j.match_score DESC, {0}, '
                "COALESCE(NULLIF(j.published_at, ''), j.first_seen) DESC LIMIT ?").format(
                    PRIORITY_ORDER)
        params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        return [to_card(row_to_dict(r), conn, settings) for r in rows]
    finally:
        if owns:
            conn.close()


def get_card(job_id, conn=None, with_ai=True):
    owns = conn is None
    conn = conn or connect()
    try:
        row = conn.execute('SELECT * FROM discovered_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return None
        return to_card(row_to_dict(row), conn, load_settings(conn), with_ai=with_ai, full=True)
    finally:
        if owns:
            conn.close()


def counts(conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        def one(sql, *params):
            return conn.execute(sql, params).fetchone()[0]
        return {
            'total': one("SELECT COUNT(*) FROM discovered_jobs "
                         "WHERE state NOT IN ('IGNORED', 'EXPIRED')"),
            'new': one("SELECT COUNT(*) FROM discovered_jobs WHERE state='NEW'"),
            'saved': one("SELECT COUNT(*) FROM discovered_jobs WHERE state='SAVED'"),
            'ignored': one("SELECT COUNT(*) FROM discovered_jobs WHERE state='IGNORED'"),
            'expired': one("SELECT COUNT(*) FROM discovered_jobs WHERE state='EXPIRED'"),
            'excellent': one('SELECT COUNT(*) FROM discovered_jobs WHERE match_score>=? '
                             "AND state NOT IN ('IGNORED', 'EXPIRED')", EXCELLENT_FROM),
            'strong': one('SELECT COUNT(*) FROM discovered_jobs WHERE match_score>=? '
                          "AND match_score<? AND state NOT IN ('IGNORED', 'EXPIRED')",
                          STRONG_FROM, EXCELLENT_FROM),
        }
    finally:
        if owns:
            conn.close()


def set_feedback(job_id, verdict, reason='', note='', conn=None):
    """Record a personal YES / MAYBE / NO verdict on one job.

    Stored and nothing more: no rule is rewritten, no weight is retrained and
    no job is removed.  The data is there for a later, explicit calibration
    step that the user asks for.
    """
    from .db import now_iso

    verdict = str(verdict or '').strip().upper()
    if verdict not in FEEDBACK_VALUES:
        raise ValueError('Feedback must be one of YES, MAYBE, NO (or empty to clear).')
    reason = str(reason or '').strip()
    if reason and reason not in FEEDBACK_REASONS:
        raise ValueError('Unknown feedback reason: {0}'.format(reason))
    if verdict != 'NO':
        reason = ''       # a reason only ever qualifies a "NO"

    owns = conn is None
    conn = conn or connect()
    try:
        row = conn.execute('SELECT id FROM discovered_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return None
        conn.execute('UPDATE discovered_jobs SET feedback=?, feedback_reason=?, '
                     'feedback_note=?, feedback_at=? WHERE id=?',
                     (verdict, reason, str(note or '')[:500],
                      now_iso() if verdict else '', job_id))
        conn.commit()
        return get_card(job_id, conn, with_ai=False)
    finally:
        if owns:
            conn.close()


def feedback_summary(conn=None):
    """Counts per verdict and per "NO" reason, for the Config screen."""
    owns = conn is None
    conn = conn or connect()
    try:
        verdicts = {v: 0 for v in ('YES', 'MAYBE', 'NO')}
        for row in conn.execute("SELECT feedback, COUNT(*) FROM discovered_jobs "
                                "WHERE feedback <> '' GROUP BY feedback").fetchall():
            verdicts[row[0]] = row[1]
        reasons = {row[0]: row[1] for row in conn.execute(
            "SELECT feedback_reason, COUNT(*) FROM discovered_jobs "
            "WHERE feedback='NO' AND feedback_reason <> '' GROUP BY feedback_reason").fetchall()}
        return {'verdicts': verdicts, 'reasons': reasons,
                'total': sum(verdicts.values())}
    finally:
        if owns:
            conn.close()
