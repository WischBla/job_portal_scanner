"""Company watchlist - the primary high-quality discovery source.

A watchlist entry is a company worth keeping an eye on.  It has two possible
shapes:

``manual``
    No reliable, public, machine-readable job endpoint could be verified.  The
    entry only carries a careers URL so the UI can offer "Open careers page".
    No HTML scraping is invented to paper over this - a brittle scraper that
    silently breaks is worse than an honest link, and the UI must never imply
    that the company is being scanned.

anything in ``jobscanner.sources.registry``
    A real public endpoint is configured (Greenhouse, Lever, SmartRecruiters,
    a SuccessFactors or Phenom career site, a public company API, schema.org
    JobPosting data or an RSS feed).  The entry then owns a row in
    ``job_sources``, which is what the scan pipeline actually reads.

Every entry also carries its own health: ``source_status`` (ACTIVE / MANUAL /
UNAVAILABLE / ERROR), when it was last checked, when it last succeeded, the
last error and how many jobs the last scan returned.  That is what makes a
broken integration visible instead of silently contributing nothing.

Discovery only: nothing here decides whether a job is relevant.
"""

import json

from .db import now_iso, row_to_dict
from .sources import registry

PRIORITIES = ('A', 'B', 'C')

WATCHLIST_SOURCE_TYPES = registry.WATCHLIST_SOURCE_TYPES
SOURCE_STATUSES = registry.SOURCE_STATUSES

_EDITABLE = ('company_name', 'enabled', 'priority', 'career_source_type',
             'career_source_identifier', 'career_url', 'notes')


def _clean(payload, existing=None):
    data = dict(existing or {})
    data.update({k: v for k, v in (payload or {}).items() if k in _EDITABLE})

    name = str(data.get('company_name') or '').strip()
    if not name:
        raise ValueError('A company name is required.')

    source_type = str(data.get('career_source_type') or 'manual').strip().lower()
    if source_type not in WATCHLIST_SOURCE_TYPES:
        raise ValueError('Unknown career source type: {0}'.format(source_type))

    identifier = str(data.get('career_source_identifier') or '').strip()
    kind = registry.get_kind(source_type)
    if kind.identifier_required and kind.automated and not identifier:
        raise ValueError('{0} needs a {1}.'.format(kind.label, kind.identifier_label.lower()))

    priority = str(data.get('priority') or 'B').strip().upper()[:1]
    return {
        'company_name': name,
        'enabled': 1 if data.get('enabled', True) else 0,
        'priority': priority if priority in PRIORITIES else 'B',
        'career_source_type': source_type,
        'career_source_identifier': identifier,
        'career_url': str(data.get('career_url') or '').strip(),
        'notes': str(data.get('notes') or '').strip(),
    }


class CompanyWatchlist:
    def __init__(self, conn):
        self.conn = conn

    # -- reads -------------------------------------------------------------
    def list(self):
        rows = self.conn.execute(
            'SELECT * FROM company_watchlist ORDER BY priority, company_name COLLATE NOCASE'
        ).fetchall()
        return [self._present(row_to_dict(r)) for r in rows]

    def get(self, entry_id):
        row = self.conn.execute('SELECT * FROM company_watchlist WHERE id=?', (entry_id,)).fetchone()
        return self._present(row_to_dict(row)) if row else None

    def get_by_name(self, company_name):
        row = self.conn.execute(
            'SELECT * FROM company_watchlist WHERE company_name=? COLLATE NOCASE',
            (str(company_name or ''),)).fetchone()
        return self._present(row_to_dict(row)) if row else None

    def active_source_names(self):
        """``job_sources.name`` for every enabled, automated watchlist entry."""
        rows = self.conn.execute(
            'SELECT s.name FROM company_watchlist w JOIN job_sources s ON s.id = w.source_id '
            'WHERE w.enabled=1 AND s.enabled=1').fetchall()
        return [r['name'] for r in rows]

    def _present(self, data):
        if data is None:
            return None
        data['enabled'] = bool(data.get('enabled'))
        source_type = data.get('career_source_type') or 'manual'
        data['automated'] = registry.is_automated(source_type)
        data['source_label'] = registry.source_label(source_type)
        data['source_status'] = data.get('source_status') or (
            registry.MANUAL if not data['automated'] else registry.UNAVAILABLE)
        # A manual entry is honest about what it can do: open the page yourself.
        data['action'] = 'scan' if data['automated'] else 'open_careers_page'
        data['source_url'] = data.get('source_url') or data.get('career_url') or ''
        data['job_count_last_scan'] = int(data.get('job_count_last_scan') or 0)
        return data

    # -- writes ------------------------------------------------------------
    def create(self, payload):
        clean = _clean(payload)
        if self.conn.execute('SELECT 1 FROM company_watchlist WHERE company_name=? COLLATE NOCASE',
                             (clean['company_name'],)).fetchone():
            raise ValueError('{0} is already on the watchlist.'.format(clean['company_name']))
        ts = now_iso()
        columns = list(clean) + ['created_at', 'updated_at']
        cursor = self.conn.execute(
            'INSERT INTO company_watchlist ({0}) VALUES ({1})'.format(
                ','.join(columns), ','.join('?' for _ in columns)),
            list(clean.values()) + [ts, ts])
        entry_id = cursor.lastrowid
        self._sync_source(entry_id, clean)
        self.conn.commit()
        return self.get(entry_id)

    def update(self, entry_id, payload):
        existing = self.get(entry_id)
        if existing is None:
            return None
        clean = _clean(payload, existing)
        assignments = ','.join('{0}=?'.format(key) for key in clean)
        self.conn.execute(
            'UPDATE company_watchlist SET {0}, updated_at=? WHERE id=?'.format(assignments),
            list(clean.values()) + [now_iso(), entry_id])
        self._sync_source(entry_id, clean)
        self.conn.commit()
        return self.get(entry_id)

    def delete(self, entry_id):
        entry = self.get(entry_id)
        if entry is None:
            return False
        self._drop_source(entry_id)
        self.conn.execute('DELETE FROM company_watchlist WHERE id=?', (entry_id,))
        self.conn.commit()
        return True

    # -- source health -----------------------------------------------------
    def record_check(self, entry_id, status, job_count=0, detail=''):
        """Store the outcome of one source check or scan.

        ``last_success_at`` only moves forward on a real success, so an entry
        that broke three days ago keeps saying when it last worked.
        """
        status = status if status in SOURCE_STATUSES else registry.ERROR
        ts = now_iso()
        if status == registry.ACTIVE:
            self.conn.execute(
                'UPDATE company_watchlist SET source_status=?, last_checked_at=?, '
                'last_success_at=?, last_error=?, job_count_last_scan=?, last_scan_at=?, '
                'last_scan_status=?, updated_at=? WHERE id=?',
                (status, ts, ts, '', int(job_count or 0), ts, str(detail or '')[:120],
                 ts, entry_id))
        else:
            self.conn.execute(
                'UPDATE company_watchlist SET source_status=?, last_checked_at=?, '
                'last_error=?, job_count_last_scan=?, last_scan_at=?, last_scan_status=?, '
                'updated_at=? WHERE id=?',
                (status, ts, str(detail or '')[:400], int(job_count or 0), ts,
                 str(detail or '')[:120], ts, entry_id))
        self.conn.commit()
        return self.get(entry_id)

    def verify(self, entry_id):
        """Hit the live endpoint and persist what came back."""
        entry = self.get(entry_id)
        if entry is None:
            return None
        status, count, detail = registry.verify(
            entry['career_source_type'], entry['career_source_identifier'],
            entry['company_name'])
        return self.record_check(entry_id, status, count, detail)

    def record_scan_results(self, results):
        """Apply one scan's per-source outcome to every entry it belongs to.

        ``results`` maps ``job_sources.name`` -> ``(status, job_count, detail)``.
        """
        by_source = {r['name']: r['watch_id'] for r in self.conn.execute(
            'SELECT s.name AS name, w.id AS watch_id FROM company_watchlist w '
            'JOIN job_sources s ON s.id = w.source_id').fetchall()}
        touched = 0
        for name, outcome in (results or {}).items():
            entry_id = by_source.get(name)
            if entry_id is None:
                continue
            status, count, detail = outcome
            self.record_check(entry_id, status, count, detail)
            touched += 1
        return touched

    # -- job_sources bridge ------------------------------------------------
    def _sync_source(self, entry_id, clean):
        """Create / update / remove the job_sources row this entry owns."""
        resolved = registry.resolve(clean['career_source_type'],
                                    clean['career_source_identifier'],
                                    clean['company_name'])
        self.conn.execute('UPDATE company_watchlist SET source_url=? WHERE id=?',
                          (resolved['source_url'], entry_id))
        if not resolved['automated']:
            self.conn.execute(
                "UPDATE company_watchlist SET source_status='MANUAL', last_error='', "
                'job_count_last_scan=0 WHERE id=?', (entry_id,))
            return self._drop_source(entry_id)

        row = self.conn.execute('SELECT source_id, source_status FROM company_watchlist WHERE id=?',
                                (entry_id,)).fetchone()
        source_id = row['source_id'] if row else None
        # Attaching an endpoint is a claim, not a result: until a scan or an
        # explicit check proves it answers, it is UNAVAILABLE, never ACTIVE.
        if (row['source_status'] if row else '') in ('', 'MANUAL'):
            self.conn.execute("UPDATE company_watchlist SET source_status='UNAVAILABLE' WHERE id=?",
                              (entry_id,))
        name = 'Watchlist · {0}'.format(clean['company_name'])
        config = json.dumps(resolved['config'], ensure_ascii=False)
        ts = now_iso()
        if source_id and self.conn.execute('SELECT 1 FROM job_sources WHERE id=?',
                                           (source_id,)).fetchone():
            self.conn.execute(
                'UPDATE job_sources SET name=?,source_type=?,config_json=?,enabled=?,updated_at=? '
                'WHERE id=?',
                (name, resolved['source_type'], config, clean['enabled'], ts, source_id))
        else:
            cursor = self.conn.execute(
                'INSERT INTO job_sources (name,source_type,config_json,enabled,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?)',
                (name, resolved['source_type'], config, clean['enabled'], ts, ts))
            self.conn.execute('UPDATE company_watchlist SET source_id=? WHERE id=?',
                              (cursor.lastrowid, entry_id))

    def _drop_source(self, entry_id):
        row = self.conn.execute('SELECT source_id FROM company_watchlist WHERE id=?',
                                (entry_id,)).fetchone()
        if row and row['source_id']:
            self.conn.execute('DELETE FROM job_sources WHERE id=?', (row['source_id'],))
            self.conn.execute('UPDATE company_watchlist SET source_id=NULL WHERE id=?', (entry_id,))


def source_health(conn):
    """Config -> Job Sources: one row per source kind, plus every failure.

    Companies whose integration is broken are listed explicitly; a source that
    stopped working must never just quietly return nothing.
    """
    rows = [row_to_dict(r) for r in conn.execute(
        'SELECT s.*, w.company_name AS company_name, w.source_status AS watch_status '
        'FROM job_sources s LEFT JOIN company_watchlist w ON w.source_id = s.id '
        'ORDER BY s.source_type, s.name').fetchall()]

    kinds, failures = {}, []
    for row in rows:
        kind = kinds.setdefault(row['source_type'], {
            'source_type': row['source_type'],
            'label': registry.source_label(row['source_type']),
            'sources': 0, 'companies': 0, 'ok': 0, 'failing': 0, 'unchecked': 0,
            'jobs_returned': 0, 'last_success_at': '',
        })
        kind['sources'] += 1
        # Aggregators are portals, not employers - only watchlist-backed rows
        # are counted as companies so the Config table does not claim that
        # "Arbeitnow" is one company being watched.
        if row.get('company_name'):
            kind['companies'] += 1
        kind['jobs_returned'] += int(row.get('last_job_count') or 0)
        status = row.get('last_status') or ''
        if status == registry.ACTIVE:
            kind['ok'] += 1
        elif status in (registry.ERROR, registry.UNAVAILABLE):
            kind['failing'] += 1
            failures.append({
                'source': row['name'],
                'company': row.get('company_name') or '',
                'source_type': row['source_type'],
                'status': status,
                'error': row.get('last_error') or '',
                'last_success_at': row.get('last_success_at') or '',
            })
        else:
            kind['unchecked'] += 1
        if (row.get('last_success_at') or '') > kind['last_success_at']:
            kind['last_success_at'] = row.get('last_success_at') or ''

    manual_count = conn.execute(
        "SELECT COUNT(*) FROM company_watchlist WHERE career_source_type='manual'").fetchone()[0]
    summary = sorted(kinds.values(), key=lambda k: k['label'].casefold())
    for kind in summary:
        kind['status'] = ('OK' if kind['ok'] and not kind['failing'] else
                          ('Failing' if kind['failing'] and not kind['ok'] else
                           ('Partial' if kind['failing'] else '-')))
    summary.append({
        'source_type': 'manual', 'label': registry.SOURCE_KINDS['manual'].label,
        'sources': 0, 'companies': manual_count, 'ok': 0, 'failing': 0, 'unchecked': 0,
        'jobs_returned': 0, 'last_success_at': '', 'status': '-',
    })
    return {'sources': summary, 'failures': failures}
