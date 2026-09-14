"""Assembling what the Jobs screen shows.

The screen is deliberately dumb: it renders exactly what this module returns.
Every card carries a score, a classification, at most five reasons, at most
three concerns, a compensation estimate - and, since the evidence model, an
honest statement of how much the score is worth.

Two rules about visibility live here:

**Every active job is in the default list.**  There is no score threshold on
the Jobs view.  The Personal Fit Score decides the order and the band label;
it has never been allowed to decide whether a job exists, and a job the
scanner knows too little about is the last thing that should disappear.

**A filter is a view, not a lifecycle.**  ``FILTERS`` below narrows what is
shown; nothing in it changes a job's state, its persistence or its score.
"""

import json
from datetime import datetime, timezone

from . import compensation
from . import evidence as evidence_mod
from .ai import service as ai_service
from .db import connect, row_to_dict
#: Recommendation bands, read from the Personal Fit Score.  The single source
#: of truth is `scoring`; they are re-exported here because the Jobs screen and
#: the counts query have always imported them from this module.
from .scoring import EXCELLENT_FROM, REVIEW_FROM, STRONG_FROM, WEAK_FROM
from .settings import load_settings

MAX_REASONS = 5
MAX_CONCERNS = 3

#: Company priority breaks ties and nothing else.  A priority-A job that scored
#: 64 must never appear above a priority-B job that scored 82, so the score is
#: always the first term of the ordering and the posting date the last.
PRIORITY_ORDER = "CASE w.priority WHEN 'A' THEN 0 WHEN 'B' THEN 1 WHEN 'C' THEN 2 ELSE 3 END"

#: Personal verdict on a job.  Recorded for later calibration only - nothing
#: in the scorer or the filters reads it, so a "NO" never silently changes how
#: the next scan behaves.
FEEDBACK_VALUES = ('', 'YES', 'MAYBE', 'NO')

#: Why a job was a poor fit.  The vocabulary deliberately mirrors what the two
#: personal-fit adjustments measure, so a verdict can later be compared against
#: what the scorer believed - that is the whole point of recording it.
FEEDBACK_REASONS_NEGATIVE = [
    'Too stakeholder-heavy', 'Too political / external', 'Too consulting-heavy',
    'Too hands-on IC', 'Too software-development focused',
    'Too little technical ownership', 'Too little transformation scope',
    'Seniority too low', 'Compensation likely too low', 'Location/work model poor',
]
#: Why a job was a good fit.  Recording a YES without a reason loses exactly
#: the information that makes the calibration data useful.
FEEDBACK_REASONS_POSITIVE = [
    'Excellent technical ownership', 'Excellent SRE / platform fit',
    'Excellent transformation scope', 'Excellent technical program fit',
]
FEEDBACK_REASONS = FEEDBACK_REASONS_NEGATIVE + FEEDBACK_REASONS_POSITIVE + ['Other']

#: States the Jobs screen understands.
OPEN_STATES = ('NEW', 'SEEN', 'SAVED', 'APPLIED')
#: Never shown as a current recommendation: ignored by the user, or retired by
#: the source because the posting is gone.
HIDDEN_STATES = ('IGNORED', 'EXPIRED')

#: The Jobs screen's filters.  ``all`` is the default and it really does mean
#: all: every active Swiss-eligible job, whatever it scored and however little
#: is known about it.  Each entry is the SQL predicate for that view; none of
#: them is a lifecycle rule, and none of them can remove a job from the
#: database.
ALL = 'all'
FILTERS = {
    ALL: ("j.state NOT IN ('IGNORED','EXPIRED')", ()),
    'top': ("j.state NOT IN ('IGNORED','EXPIRED') AND j.match_score >= ?", (STRONG_FROM,)),
    'review': ("j.state NOT IN ('IGNORED','EXPIRED') AND j.match_score >= ? "
               'AND j.match_score < ?', (REVIEW_FROM, STRONG_FROM)),
    'edge': ("j.state NOT IN ('IGNORED','EXPIRED') AND j.match_score >= ? "
             'AND j.match_score < ?', (WEAK_FROM, REVIEW_FROM)),
    'low': ("j.state NOT IN ('IGNORED','EXPIRED') AND j.match_score < ?", (WEAK_FROM,)),
    'enrich': ("j.state NOT IN ('IGNORED','EXPIRED') AND j.enrichment_state = ?",
               (evidence_mod.NEEDS_ENRICHMENT,)),
    'saved': ("j.state = 'SAVED'", ()),
    'ignored': ("j.state = 'IGNORED'", ()),
}
FILTER_LABELS = [
    (ALL, 'All active'),
    ('top', 'Exceptional / Strong'),
    ('review', 'Worth reviewing'),
    ('edge', 'Edge'),
    ('low', 'Low priority'),
    ('enrich', 'Needs enrichment'),
    ('saved', 'Saved'),
    ('ignored', 'Ignored'),
]


def classify(score):
    """The band label for a Personal Fit Score.  85+ is always Exceptional."""
    score = int(score or 0)
    if score >= EXCELLENT_FROM:
        return 'Exceptional'
    if score >= STRONG_FROM:
        return 'Strong'
    if score >= REVIEW_FROM:
        return 'Review'
    return 'Edge' if score >= WEAK_FROM else 'Low priority'


#: Band -> the sentence the Jobs screen shows next to the score.  A low-priority
#: job is ranked last and labelled honestly; it is never deleted.
BAND_LABELS = {
    'Exceptional': 'Exceptional fit',
    'Strong': 'Strong fit',
    'Review': 'Worth reviewing',
    'Edge': 'Edge case',
    'Low priority': 'Low priority',
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
        # The base score is never hidden: the card always shows what the job
        # scored on relevance and what each adjustment did to it, so a move
        # down the list can be read off the card itself.
        'base_score': int(job.get('base_score') or score),
        'personal_fit': int(job.get('personal_fit_score') or score),
        'operating_style': {
            'classification': job.get('operating_style_class') or '',
            'adjustment': round(float(job.get('operating_style_adjustment') or 0.0), 1),
            'detail': job.get('operating_style_detail') or '',
        },
        'career_direction': {
            'classification': job.get('career_direction_class') or '',
            'adjustment': round(float(job.get('career_direction_adjustment') or 0.0), 1),
            'detail': job.get('career_direction_detail') or '',
        },
        # Where the job was *found*, which is not necessarily where it lives:
        # a LinkedIn alert can point at a job the company's own board already
        # delivered, and that job keeps its source and its application URL.
        'discovered_via': job.get('discovered_via') or '',
        'linkedin_url': job.get('linkedin_url') or '',
        # An imported alert entry has a title, a company and a link, and no
        # description.  The card says so instead of presenting a fit score
        # that was computed from four words.
        'needs_details': bool(job.get('needs_details')),
        # How much the score above is worth.  "78, confidence HIGH" and
        # "78, provisional" are different claims and the card makes the
        # difference visible rather than showing one number for both.
        'evidence': job.get('evidence_level') or evidence_mod.LOW,
        'evidence_detail': job.get('evidence_detail') or '',
        'enrichment_state': job.get('enrichment_state') or evidence_mod.NEEDS_ENRICHMENT,
        'confidence': job.get('fit_confidence') or evidence_mod.LOW,
        'provisional': bool(job.get('fit_provisional')),
        'high_potential': bool(job.get('high_potential')),
        'enrichment_source': job.get('enrichment_source') or '',
        'enrichment_detail': job.get('enrichment_detail') or '',
        'enrichment_at': job.get('enrichment_at') or '',
        'has_description': bool(str(job.get('description') or '').strip()),
        'lifecycle_reason': job.get('lifecycle_reason') or '',
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


def list_cards(conn=None, state='', limit=200, include_ignored=False, view=ALL):
    """The cards for one view of the Jobs screen.

    ``view`` narrows what is shown and nothing else; the default really is
    every active job.  ``state`` is the older, more specific selector and still
    wins when it is given, because the Ignored list is addressed that way.
    """
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
        elif include_ignored and view == ALL:
            pass                                  # everything, ignored included
        else:
            where, values = FILTERS.get(str(view or ALL), FILTERS[ALL])
            sql += ' WHERE ' + where
            params.extend(values)
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
            # One number per filter chip, so the Jobs screen can label each
            # view without fetching it.  Counts, like filters, are a view:
            # nothing here decides what is kept.
            'filters': {key: one('SELECT COUNT(*) FROM discovered_jobs j WHERE {0}'.format(
                FILTERS[key][0]), *FILTERS[key][1]) for key, _label in FILTER_LABELS},
            'needs_enrichment': one(
                'SELECT COUNT(*) FROM discovered_jobs WHERE enrichment_state=? '
                "AND state NOT IN ('IGNORED', 'EXPIRED')", evidence_mod.NEEDS_ENRICHMENT),
            'high_potential': one(
                'SELECT COUNT(*) FROM discovered_jobs WHERE high_potential=1 '
                "AND state NOT IN ('IGNORED', 'EXPIRED')"),
            'enriched': one(
                'SELECT COUNT(*) FROM discovered_jobs WHERE enrichment_state=? '
                "AND state NOT IN ('IGNORED', 'EXPIRED')", evidence_mod.ENRICHED),
            'partial': one(
                'SELECT COUNT(*) FROM discovered_jobs WHERE enrichment_state=? '
                "AND state NOT IN ('IGNORED', 'EXPIRED')", evidence_mod.PARTIAL),
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
    if not verdict:
        reason = ''       # clearing the verdict clears what qualified it

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
    """Counts per verdict and per reason, for the Config screen.

    Positive reasons count too: "why this was a yes" is exactly as useful for
    a later calibration as "why this was a no".
    """
    owns = conn is None
    conn = conn or connect()
    try:
        verdicts = {v: 0 for v in ('YES', 'MAYBE', 'NO')}
        for row in conn.execute("SELECT feedback, COUNT(*) FROM discovered_jobs "
                                "WHERE feedback <> '' GROUP BY feedback").fetchall():
            verdicts[row[0]] = row[1]
        reasons = {row[0]: row[1] for row in conn.execute(
            "SELECT feedback_reason, COUNT(*) FROM discovered_jobs "
            "WHERE feedback <> '' AND feedback_reason <> '' GROUP BY feedback_reason").fetchall()}
        return {'verdicts': verdicts, 'reasons': reasons,
                'total': sum(verdicts.values())}
    finally:
        if owns:
            conn.close()


def rescore(conn=None):
    """Re-run the current scorer over every stored job.

    The profile and the fit model change over time; stored jobs would keep the
    score they were given the day they were discovered.  This recomputes the
    base score and both adjustments in place.  It is deliberately additive:
    the job, its state, its feedback and its link to an application are never
    touched, so a rescore can never lose work the user has done.
    """
    from .profile import from_row as profile_from_row
    from .schema_v2 import rescore_existing_jobs

    owns = conn is None
    conn = conn or connect()
    try:
        row = conn.execute('SELECT * FROM search_profile ORDER BY id LIMIT 1').fetchone()
        if row is None:
            return 0
        count = rescore_existing_jobs(conn, profile_from_row(row_to_dict(row)))
        conn.commit()
        return count
    finally:
        if owns:
            conn.close()
