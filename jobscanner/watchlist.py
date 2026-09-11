"""Company watchlist.

A watchlist entry is a company worth keeping an eye on.  It has two possible
states:

``manual``
    No reliable, public, machine-readable job endpoint is known.  The entry
    only carries a careers URL so the UI can offer "Open careers page".  No
    HTML scraping is implemented for these - a brittle scraper that silently
    breaks is worse than an honest link.

``greenhouse`` / ``lever`` / ``rss``
    A real public endpoint is configured.  The entry then owns a row in
    ``job_sources``, which is what the scan pipeline actually reads.  Attaching
    or detaching an endpoint keeps the two tables in sync in one place.
"""

import json

from .db import now_iso, row_to_dict

PRIORITIES = ('A', 'B', 'C')

#: 'manual' is not a scanner source; the other three map onto real adapters.
WATCHLIST_SOURCE_TYPES = ('manual', 'greenhouse', 'lever', 'rss')

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
    if source_type != 'manual' and not identifier:
        raise ValueError('{0} needs a board token, site or feed URL.'.format(source_type))

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

    def _present(self, data):
        if data is None:
            return None
        data['enabled'] = bool(data.get('enabled'))
        data['automated'] = data.get('career_source_type') in ('greenhouse', 'lever', 'rss')
        # A manual entry is honest about what it can do: open the page yourself.
        data['action'] = 'scan' if data['automated'] else 'open_careers_page'
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

    def record_scan(self, company_name, status):
        """Called after a scan so the UI can show per-company freshness."""
        self.conn.execute(
            'UPDATE company_watchlist SET last_scan_at=?, last_scan_status=?, updated_at=? '
            'WHERE company_name=? COLLATE NOCASE',
            (now_iso(), str(status or '')[:120], now_iso(), company_name))

    # -- job_sources bridge ------------------------------------------------
    def _source_config(self, clean):
        identifier = clean['career_source_identifier']
        company = clean['company_name']
        if clean['career_source_type'] == 'greenhouse':
            return {'board_token': identifier, 'company': company}
        if clean['career_source_type'] == 'lever':
            return {'site': identifier, 'company': company, 'region': 'global'}
        return {'url': identifier, 'company': company}

    def _sync_source(self, entry_id, clean):
        """Create / update / remove the job_sources row this entry owns."""
        if clean['career_source_type'] == 'manual':
            return self._drop_source(entry_id)
        row = self.conn.execute('SELECT source_id FROM company_watchlist WHERE id=?',
                                (entry_id,)).fetchone()
        source_id = row['source_id'] if row else None
        name = 'Watchlist · {0}'.format(clean['company_name'])
        config = json.dumps(self._source_config(clean), ensure_ascii=False)
        ts = now_iso()
        if source_id and self.conn.execute('SELECT 1 FROM job_sources WHERE id=?',
                                           (source_id,)).fetchone():
            self.conn.execute(
                'UPDATE job_sources SET name=?,source_type=?,config_json=?,enabled=?,updated_at=? '
                'WHERE id=?',
                (name, clean['career_source_type'], config, clean['enabled'], ts, source_id))
        else:
            cursor = self.conn.execute(
                'INSERT INTO job_sources (name,source_type,config_json,enabled,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?)',
                (name, clean['career_source_type'], config, clean['enabled'], ts, ts))
            self.conn.execute('UPDATE company_watchlist SET source_id=? WHERE id=?',
                              (cursor.lastrowid, entry_id))

    def _drop_source(self, entry_id):
        row = self.conn.execute('SELECT source_id FROM company_watchlist WHERE id=?',
                                (entry_id,)).fetchone()
        if row and row['source_id']:
            self.conn.execute('DELETE FROM job_sources WHERE id=?', (row['source_id'],))
            self.conn.execute('UPDATE company_watchlist SET source_id=NULL WHERE id=?', (entry_id,))
