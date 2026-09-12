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

#: States the Jobs screen understands.
OPEN_STATES = ('NEW', 'SEEN', 'SAVED', 'APPLIED')
#: Never shown as a current recommendation: ignored by the user, or retired by
#: the source because the posting is gone.
HIDDEN_STATES = ('IGNORED', 'EXPIRED')


def classify(score):
    score = int(score or 0)
    if score >= EXCELLENT_FROM:
        return 'Excellent'
    if score >= STRONG_FROM:
        return 'Strong'
    return 'Review'


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
        sql = 'SELECT * FROM discovered_jobs'
        params = []
        if state:
            sql += ' WHERE state=?'
            params.append(state)
        elif not include_ignored:
            sql += " WHERE state NOT IN ('IGNORED', 'EXPIRED')"
        # Contract of the Jobs screen: score first, newest second.
        sql += ' ORDER BY match_score DESC, COALESCE(NULLIF(published_at, \'\'), first_seen) DESC LIMIT ?'
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
