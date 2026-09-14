"""JobRepository - persistence, stable deduplication and job states."""

import json

from . import fit
from .db import load_profile, now_iso, row_to_dict, utc_now_iso
from .filters import GROUP_LABELS, rejection_group
from .scoring import EXCELLENT_FROM, STRONG_FROM

JOB_STATES = ('NEW', 'SEEN', 'SAVED', 'IGNORED', 'APPLIED', 'EXPIRED')

#: Why a job left the active list.  EXPIRED is one state with two honest
#: causes, and conflating them is what made "expired" mean "we did not see it
#: this time".
GONE_FROM_SOURCE = 'source_confirmed_gone'
FILTERED_OUT = 'no_longer_matches_filters'

#: How many *successful, authoritative* scans of a healthy source have to miss
#: a job before it is retired.  One miss is not evidence: boards paginate,
#: rate-limit, drop a posting for an hour and put it back.  Two consecutive
#: clean reconciliations are a confirmation.
EXPIRE_AFTER_MISSES = 2

#: Result orderings offered in the UI.  'score' is the default: best match
#: first, newest first within the same score.
SORT_ORDERS = {
    'score': 'match_score DESC, published_at DESC, first_seen DESC',
    'newest': 'published_at DESC, first_seen DESC, match_score DESC',
    'company': 'company COLLATE NOCASE ASC, match_score DESC',
    'location': ('normalized_city COLLATE NOCASE ASC, normalized_country COLLATE NOCASE ASC, '
                 'match_score DESC'),
}
ACTIVE_STATES = ('NEW', 'SEEN', 'SAVED')
#: States the user set by hand - a rescan must never reset them.
STICKY_STATES = ('SAVED', 'IGNORED', 'APPLIED')

_JOB_COLUMNS = [
    'source', 'source_type', 'external_id', 'source_key', 'dedupe_key', 'company', 'title',
    'location', 'raw_location', 'normalized_country', 'normalized_city', 'normalized_region',
    'is_remote', 'is_hybrid', 'switzerland_eligible', 'location_confidence', 'location_reason',
    'work_model', 'office_days', 'seniority', 'remote', 'job_url', 'description', 'excerpt',
    'published_at', 'salary_min', 'salary_max', 'salary_currency', 'salary_period',
    'match_score', 'match_label', 'match_reasons', 'matched_terms', 'match_breakdown',
    'match_concerns', 'needs_details',
] + list(fit.SCORE_COLUMNS)
_JSON_COLUMNS = ('match_reasons', 'matched_terms', 'match_breakdown', 'match_concerns')


def parse_job_row(row):
    data = row_to_dict(row)
    if data is None:
        return None
    for key in _JSON_COLUMNS:
        try:
            data[key] = json.loads(data.get(key) or '[]')
        except (TypeError, ValueError):
            data[key] = []
    for key in ('is_remote', 'is_hybrid', 'switzerland_eligible', 'is_new'):
        data[key] = bool(data.get(key))
    data['remote'] = None if data.get('remote') is None else bool(data.get('remote'))
    data.pop('review_state', None)  # legacy column, superseded by `state`
    return data


class JobRepository:
    def __init__(self, conn):
        self.conn = conn

    # -- reads -------------------------------------------------------------
    def get(self, job_id):
        return parse_job_row(self.conn.execute(
            'SELECT * FROM discovered_jobs WHERE id=?', (job_id,)).fetchone())

    def find_by_source_key(self, source_key):
        return self.conn.execute(
            'SELECT id, state, application_id, first_seen FROM discovered_jobs WHERE source_key=?',
            (source_key,)).fetchone()

    def find_by_dedupe_key(self, dedupe_key):
        if not dedupe_key:
            return None
        return self.conn.execute(
            'SELECT id, state, application_id, first_seen, source_key FROM discovered_jobs '
            'WHERE dedupe_key=? LIMIT 1', (dedupe_key,)).fetchone()

    def list_jobs(self, state='', search='', min_score=0, new_only=False, limit=300,
                  sort='score'):
        clauses, params = [], []
        if state:
            clauses.append('state = ?')
            params.append(state)
        else:
            clauses.append("state NOT IN ('IGNORED','EXPIRED')")
        if new_only:
            clauses.append('is_new = 1')
        if min_score:
            clauses.append('match_score >= ?')
            params.append(int(min_score))
        if search:
            wildcard = '%{0}%'.format(search)
            clauses.append('(company LIKE ? OR title LIKE ? OR location LIKE ? OR description LIKE ?)')
            params.extend([wildcard] * 4)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        order = SORT_ORDERS.get(str(sort or 'score').lower(), SORT_ORDERS['score'])
        rows = self.conn.execute(
            'SELECT * FROM discovered_jobs{0} ORDER BY '
            "CASE state WHEN 'NEW' THEN 1 WHEN 'SEEN' THEN 2 WHEN 'SAVED' THEN 3 "
            "WHEN 'APPLIED' THEN 4 ELSE 5 END, {1} "
            'LIMIT ?'.format(where, order), params + [int(limit)]).fetchall()
        return [parse_job_row(r) for r in rows]

    def counts(self, minimum_score=0):
        def one(sql, *params):
            return self.conn.execute(sql, params).fetchone()[0]
        return {
            'new': one("SELECT COUNT(*) FROM discovered_jobs WHERE is_new=1 AND state='NEW'"),
            'strong': one("SELECT COUNT(*) FROM discovered_jobs WHERE match_score>=? "
                          "AND state IN ('NEW','SEEN','SAVED')", STRONG_FROM),
            'excellent': one("SELECT COUNT(*) FROM discovered_jobs WHERE match_score>=? "
                             "AND state IN ('NEW','SEEN','SAVED')", EXCELLENT_FROM),
            'relevant': one("SELECT COUNT(*) FROM discovered_jobs WHERE match_score>=? "
                            "AND state IN ('NEW','SEEN','SAVED')", int(minimum_score or 0)),
            'new_strong': one("SELECT COUNT(*) FROM discovered_jobs WHERE is_new=1 "
                              "AND match_score>=? AND state IN ('NEW','SEEN','SAVED')", STRONG_FROM),
            'saved': one("SELECT COUNT(*) FROM discovered_jobs WHERE state='SAVED'"),
            'applied': one("SELECT COUNT(*) FROM discovered_jobs WHERE state='APPLIED'"),
            'ignored': one("SELECT COUNT(*) FROM discovered_jobs WHERE state='IGNORED'"),
            'total': one("SELECT COUNT(*) FROM discovered_jobs WHERE state NOT IN ('IGNORED','EXPIRED')"),
        }

    # -- writes ------------------------------------------------------------
    def mark_all_seen(self):
        """Called at the start of a scan so `is_new` always means "this run"."""
        self.conn.execute('UPDATE discovered_jobs SET is_new=0')
        self.conn.execute("UPDATE discovered_jobs SET state='SEEN' WHERE state='NEW'")

    def upsert(self, job, scored, seen_at=None):
        """Insert or refresh one scored job. Returns (row_id, is_new).

        Identity: ``source_type:external_id`` first (stable across renames of a
        source), then the canonical URL / company+title fallback so the same
        posting coming from two portals is stored once.
        """
        seen_at = seen_at or utc_now_iso()
        existing = self.find_by_source_key(job['source_key']) or self.find_by_dedupe_key(job['dedupe_key'])

        values = {column: job.get(column) for column in _JOB_COLUMNS}
        values.update({
            'is_remote': 1 if job.get('is_remote') else 0,
            'is_hybrid': 1 if job.get('is_hybrid') else 0,
            'switzerland_eligible': 1 if job.get('switzerland_eligible') else 0,
            'remote': 1 if job.get('is_remote') else 0,
            'match_score': scored['score'],
            'match_label': scored['label'],
            'match_reasons': json.dumps(scored['reasons'], ensure_ascii=False),
            'matched_terms': json.dumps(scored['terms'], ensure_ascii=False),
            'match_breakdown': json.dumps(scored['breakdown'], ensure_ascii=False),
            'match_concerns': json.dumps(scored['concerns'], ensure_ascii=False),
        })
        values.update(fit.score_columns(scored, job))
        # ``needs_details`` predates the evidence model and is still what the
        # card and the import flow read.  It is now derived rather than set by
        # hand, so the two can never disagree.
        values['needs_details'] = 1 if values['enrichment_state'] == 'NEEDS_ENRICHMENT' else 0

        if existing:
            # A refresh must never make a job *poorer*.  The same posting can
            # arrive from an aggregator as a stub after it arrived from the
            # company's own board in full - and an enriched or hand-pasted
            # description is work that a rescan has no business undoing.  When
            # the incoming text is shorter than what is stored, the stored one
            # is kept and the job is re-scored on it.
            columns = list(_JOB_COLUMNS)
            if not self._is_richer(values.get('description'), existing['id']):
                columns = [c for c in columns if c not in ('description', 'excerpt')]
                values = self._rescored_on_stored_text(job, values, existing['id'])
            assignments = ','.join('{0}=?'.format(c) for c in columns)
            self.conn.execute(
                'UPDATE discovered_jobs SET {0}, last_seen=? WHERE id=?'.format(assignments),
                [values[c] for c in columns] + [seen_at, existing['id']])
            # A state the user chose by hand survives every rescan.
            if existing['state'] not in STICKY_STATES:
                self.conn.execute("UPDATE discovered_jobs SET state='SEEN' WHERE id=? AND state<>'SEEN'",
                                  (existing['id'],))
            return (existing['id'], False)

        columns = _JOB_COLUMNS + ['state', 'state_changed_at', 'is_new', 'first_seen', 'last_seen']
        params = [values[c] for c in _JOB_COLUMNS] + ['NEW', now_iso(), 1, seen_at, seen_at]
        cursor = self.conn.execute(
            'INSERT INTO discovered_jobs ({0}) VALUES ({1})'.format(
                ','.join(columns), ','.join('?' for _ in columns)), params)
        return (cursor.lastrowid, True)

    def _is_richer(self, incoming, job_id):
        """Does the incoming text say more than what is already stored?"""
        row = self.conn.execute('SELECT description FROM discovered_jobs WHERE id=?',
                                (job_id,)).fetchone()
        stored = str((row[0] if row else '') or '').strip()
        return len(str(incoming or '').strip()) >= len(stored)

    def _rescored_on_stored_text(self, job, values, job_id):
        """Re-score the incoming job against the description that is kept.

        Without this the row would hold one job's description and another
        job's score, which is the sort of quiet inconsistency that makes a
        ranking impossible to explain.
        """
        from .scoring import MatchScorer

        row = self.conn.execute(
            'SELECT description, excerpt, enrichment_source FROM discovered_jobs WHERE id=?',
            (job_id,)).fetchone()
        if row is None:
            return values
        merged = dict(job)
        merged['description'] = row[0]
        merged['excerpt'] = row[1]
        scored = MatchScorer().score(merged, load_profile(self.conn))
        values = dict(values)
        values.update({
            'match_score': scored['score'],
            'match_label': scored['label'],
            'match_reasons': json.dumps(scored['reasons'], ensure_ascii=False),
            'matched_terms': json.dumps(scored['terms'], ensure_ascii=False),
            'match_breakdown': json.dumps(scored['breakdown'], ensure_ascii=False),
            'match_concerns': json.dumps(scored['concerns'], ensure_ascii=False),
        })
        values.update(fit.score_columns(scored, merged))
        values['needs_details'] = 1 if values['enrichment_state'] == 'NEEDS_ENRICHMENT' else 0
        return values

    def set_state(self, job_id, state, application_id=None):
        if state not in JOB_STATES:
            raise ValueError('Unknown job state: {0}'.format(state))
        if application_id is None:
            cursor = self.conn.execute(
                'UPDATE discovered_jobs SET state=?, state_changed_at=?, is_new=0 WHERE id=?',
                (state, now_iso(), job_id))
        else:
            cursor = self.conn.execute(
                'UPDATE discovered_jobs SET state=?, state_changed_at=?, is_new=0, application_id=? '
                'WHERE id=?', (state, now_iso(), application_id, job_id))
        return cursor.rowcount > 0

    def retire_filtered(self, source_keys, reason=FILTERED_OUT):
        """Retire jobs this scan *saw* and deliberately rejected.

        This is the confirmed case and it needs no waiting period: the source
        delivered the posting, the hard filter looked at it and said no.  That
        is what makes tightening a filter take effect immediately, and it is a
        different fact from "the source did not mention it", which is handled
        by :meth:`expire_missing`.

        A state the user chose by hand is never overwritten, and a job is
        never retired for its *score* - only a filter decision reaches here.
        """
        keys = [k for k in (source_keys or []) if k]
        if not keys:
            return 0
        placeholders = ','.join('?' for _ in keys)
        cursor = self.conn.execute(
            "UPDATE discovered_jobs SET state='EXPIRED', state_changed_at=?, is_new=0, "
            'lifecycle_reason=? WHERE source_key IN ({0}) AND state NOT IN {1}'.format(
                placeholders, str(STICKY_STATES)),
            [now_iso(), reason] + keys)
        return cursor.rowcount

    def expire_missing(self, source_names, seen_ids, reason=GONE_FROM_SOURCE):
        """Retire jobs a healthy source has now failed to return twice.

        ``source_names`` must contain *only* sources whose fetch was a
        successful, authoritative reconciliation.  A source that was rate
        limited, timed out, answered 500, failed to parse or returned a
        suspiciously empty list is not in that list and therefore cannot
        retire anything: an absent answer is not evidence that a job is gone.

        Even a healthy source only counts as one witness.  Boards paginate,
        drop a posting for an hour and put it back, so a job has to be missing
        from ``EXPIRE_AFTER_MISSES`` consecutive clean scans before it is
        retired.  Seeing it again resets the counter.

        A state the user chose by hand (SAVED / IGNORED / APPLIED) is never
        overwritten, and no score is ever consulted.
        """
        if not source_names:
            return 0
        source_placeholders = ','.join('?' for _ in source_names)
        params = list(source_names)
        id_clause = ''
        if seen_ids:
            id_clause = ' AND id NOT IN ({0})'.format(','.join('?' for _ in seen_ids))
            params.extend(seen_ids)
        scope = 'source IN ({0}){1}'.format(source_placeholders, id_clause)

        # Anything this scan did return is present again: forget the misses.
        if seen_ids:
            self.conn.execute(
                'UPDATE discovered_jobs SET missing_scans=0 WHERE id IN ({0}) '
                'AND missing_scans<>0'.format(','.join('?' for _ in seen_ids)), seen_ids)
        self.conn.execute(
            'UPDATE discovered_jobs SET missing_scans=missing_scans+1 '
            "WHERE {0} AND state NOT IN {1} AND state<>'EXPIRED'".format(
                scope, str(STICKY_STATES)), params)
        cursor = self.conn.execute(
            "UPDATE discovered_jobs SET state='EXPIRED', state_changed_at=?, is_new=0, "
            'lifecycle_reason=? WHERE {0} AND state NOT IN {1} AND missing_scans>=?'.format(
                scope, str(STICKY_STATES)),
            [now_iso(), reason] + params + [EXPIRE_AFTER_MISSES])
        return cursor.rowcount

    def release_orphaned_applications(self):
        """A job whose application was deleted goes back into the pool.

        The foreign key clears ``application_id`` on delete, but the job would
        otherwise stay stuck in APPLIED and never reappear.
        """
        self.conn.execute(
            "UPDATE discovered_jobs SET state='SEEN', state_changed_at=? "
            "WHERE state='APPLIED' AND application_id IS NULL", (now_iso(),))

    def link_existing_applications(self):
        """A posting already tracked in the application list is not a new find."""
        self.conn.execute('''
            UPDATE discovered_jobs
               SET state='APPLIED', is_new=0,
                   application_id=(SELECT a.id FROM applications a
                                    WHERE a.job_url <> '' AND a.job_url = discovered_jobs.job_url
                                    LIMIT 1)
             WHERE job_url <> ''
               AND EXISTS (SELECT 1 FROM applications a
                            WHERE a.job_url <> '' AND a.job_url = discovered_jobs.job_url)
        ''')

    # -- rejection log -----------------------------------------------------
    def clear_rejections(self, keep_run_id=None):
        if keep_run_id is None:
            self.conn.execute('DELETE FROM rejected_jobs')
        else:
            self.conn.execute('DELETE FROM rejected_jobs WHERE run_id IS NOT ? AND run_id <> ?',
                              (keep_run_id, keep_run_id))

    def record_rejection(self, run_id, job, rejection, score=None):
        self.conn.execute('''
            INSERT INTO rejected_jobs (run_id,source,source_type,external_id,company,title,
                raw_location,normalized_country,job_url,stage,reason_code,reason,match_score,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
            run_id, job.get('source') or '', job.get('source_type') or '',
            job.get('external_id') or '', job.get('company') or '', job.get('title') or '',
            job.get('raw_location') or '', job.get('normalized_country') or '',
            job.get('job_url') or '', rejection.get('stage') or 'hard_filter',
            rejection.get('code') or '', rejection.get('reason') or '', score, now_iso()))

    def list_rejections(self, run_id=None, limit=400):
        if run_id:
            rows = self.conn.execute(
                'SELECT * FROM rejected_jobs WHERE run_id=? ORDER BY reason_code, id LIMIT ?',
                (run_id, limit)).fetchall()
        else:
            rows = self.conn.execute(
                'SELECT * FROM rejected_jobs ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [row_to_dict(r) for r in rows]

    def rejection_groups(self, run_id=None):
        """Rejections rolled up into the four buckets the debug view shows."""
        totals = {}
        for row in self.rejection_summary(run_id):
            group = rejection_group(row['reason_code'])
            totals[group] = totals.get(group, 0) + int(row['count'] or 0)
        order = ['geography', 'role', 'seniority', 'work_model', 'salary', 'score', 'other']
        return [{'group': g, 'label': GROUP_LABELS.get(g, g), 'count': totals[g]}
                for g in order if totals.get(g)]

    def rejection_summary(self, run_id=None):
        if run_id:
            rows = self.conn.execute(
                'SELECT reason_code, COUNT(*) AS count FROM rejected_jobs WHERE run_id=? '
                'GROUP BY reason_code ORDER BY count DESC', (run_id,)).fetchall()
        else:
            rows = self.conn.execute(
                'SELECT reason_code, COUNT(*) AS count FROM rejected_jobs '
                'GROUP BY reason_code ORDER BY count DESC').fetchall()
        return [row_to_dict(r) for r in rows]
