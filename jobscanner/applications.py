"""The application pipeline and its chronological timeline."""

from .db import connect, now_iso, row_to_dict
from .schema_v2 import APPLICATION_STATUSES, canonical_status

EDITABLE = ('company', 'position', 'level', 'location', 'work_model', 'source', 'job_url',
            'applied_date', 'status', 'last_response', 'summary', 'next_action',
            'follow_up_date', 'contact_name', 'contact_details', 'salary_range',
            'priority', 'cv_version', 'cover_letter', 'notes', 'match_summary',
            'salary_estimate')

EVENT_TYPES = ('Note', 'Applied', 'Reply', 'Screening call', 'Interview', 'Final round',
               'Offer', 'Rejection', 'Withdrawn', 'Follow-up')


def list_applications(conn=None, status=''):
    owns = conn is None
    conn = conn or connect()
    try:
        sql = 'SELECT * FROM applications'
        params = []
        if status:
            sql += ' WHERE status=?'
            params.append(canonical_status(status))
        sql += (" ORDER BY CASE status WHEN 'Offer' THEN 0 WHEN 'Final' THEN 1 "
                "WHEN 'Interview' THEN 2 WHEN 'Screening' THEN 3 WHEN 'Applied' THEN 4 "
                "WHEN 'Preparation' THEN 5 ELSE 6 END, updated_at DESC")
        return [row_to_dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        if owns:
            conn.close()


def get_application(application_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        row = conn.execute('SELECT * FROM applications WHERE id=?', (application_id,)).fetchone()
        if row is None:
            return None
        data = row_to_dict(row)
        data['events'] = list_events(application_id, conn)
        data['documents'] = list_documents(application_id, conn)
        return data
    finally:
        if owns:
            conn.close()


def create(payload, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        data = {key: str(payload.get(key) or '').strip() for key in EDITABLE}
        if not data['company'] or not data['position']:
            raise ValueError('Company and position are required.')
        data['status'] = canonical_status(data['status'] or 'Preparation')
        data['priority'] = data['priority'] or 'B'
        ts = now_iso()
        columns = list(data) + ['job_id', 'match_score', 'created_at', 'updated_at']
        values = list(data.values()) + [payload.get('job_id'), payload.get('match_score'), ts, ts]
        cursor = conn.execute('INSERT INTO applications ({0}) VALUES ({1})'.format(
            ','.join(columns), ','.join('?' for _ in columns)), values)
        application_id = cursor.lastrowid
        add_event(application_id, {
            'event_type': 'Note',
            'note': 'Application created with status "{0}".'.format(data['status']),
        }, conn=conn, commit=False)
        conn.commit()
        return get_application(application_id, conn)
    finally:
        if owns:
            conn.close()


def update(application_id, payload, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        current = conn.execute('SELECT * FROM applications WHERE id=?',
                               (application_id,)).fetchone()
        if current is None:
            return None
        current = row_to_dict(current)
        updates = {}
        for key in EDITABLE:
            if key in payload:
                value = str(payload[key] or '').strip()
                updates[key] = canonical_status(value) if key == 'status' else value
        if not updates:
            return get_application(application_id, conn)
        assignments = ','.join('{0}=?'.format(k) for k in updates)
        conn.execute('UPDATE applications SET {0}, updated_at=? WHERE id=?'.format(assignments),
                     list(updates.values()) + [now_iso(), application_id])
        if 'status' in updates and updates['status'] != current['status']:
            add_event(application_id, {
                'event_type': 'Note',
                'note': 'Status changed from "{0}" to "{1}".'.format(
                    current['status'], updates['status']),
            }, conn=conn, commit=False)
        conn.commit()
        return get_application(application_id, conn)
    finally:
        if owns:
            conn.close()


def delete(application_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        cursor = conn.execute('DELETE FROM applications WHERE id=?', (application_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        if owns:
            conn.close()


# -- timeline --------------------------------------------------------------
def list_events(application_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        return [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM events WHERE application_id=? ORDER BY event_date DESC, id DESC',
            (application_id,)).fetchall()]
    finally:
        if owns:
            conn.close()


def add_event(application_id, payload, conn=None, commit=True):
    owns = conn is None
    conn = conn or connect()
    try:
        ts = now_iso()
        event = {
            'application_id': application_id,
            'event_date': str(payload.get('event_date') or '').strip() or ts[:10],
            'event_type': str(payload.get('event_type') or 'Note').strip() or 'Note',
            'person': str(payload.get('person') or '').strip(),
            'note': str(payload.get('note') or '').strip(),
            'next_step': str(payload.get('next_step') or '').strip(),
            'follow_up_date': str(payload.get('follow_up_date') or '').strip(),
            'created_at': ts,
        }
        cursor = conn.execute(
            'INSERT INTO events ({0}) VALUES ({1})'.format(
                ','.join(event), ','.join('?' for _ in event)), list(event.values()))
        if event['next_step'] or event['follow_up_date']:
            conn.execute('UPDATE applications SET next_action=?, follow_up_date=?, updated_at=? '
                         'WHERE id=?',
                         (event['next_step'] or '', event['follow_up_date'] or '',
                          ts, application_id))
        if commit:
            conn.commit()
        return row_to_dict(conn.execute('SELECT * FROM events WHERE id=?',
                                        (cursor.lastrowid,)).fetchone())
    finally:
        if owns:
            conn.close()


def delete_event(event_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        cursor = conn.execute('DELETE FROM events WHERE id=?', (event_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        if owns:
            conn.close()


# -- documents used --------------------------------------------------------
def list_documents(application_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        rows = conn.execute(
            'SELECT ad.id AS link_id, ad.role, d.* FROM application_documents ad '
            'JOIN documents d ON d.id = ad.document_id WHERE ad.application_id=? '
            'ORDER BY d.kind', (application_id,)).fetchall()
        return [row_to_dict(r) for r in rows]
    finally:
        if owns:
            conn.close()


def attach_document(application_id, document_id, role='', conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        conn.execute('INSERT OR IGNORE INTO application_documents '
                     '(application_id, document_id, role, created_at) VALUES (?,?,?,?)',
                     (application_id, document_id, role, now_iso()))
        conn.commit()
        return list_documents(application_id, conn)
    finally:
        if owns:
            conn.close()


def detach_document(application_id, document_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        conn.execute('DELETE FROM application_documents WHERE application_id=? AND document_id=?',
                     (application_id, document_id))
        conn.commit()
        return True
    finally:
        if owns:
            conn.close()


# -- conversion from a discovered job --------------------------------------
def from_job(job_id, conn=None, extra=None):
    """Create (or return) the application that belongs to a discovered job."""
    from . import jobs_service

    owns = conn is None
    conn = conn or connect()
    try:
        row = conn.execute('SELECT * FROM discovered_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return None
        job = row_to_dict(row)
        if job.get('application_id'):
            existing = get_application(job['application_id'], conn)
            if existing:
                return existing

        card = jobs_service.get_card(job_id, conn, with_ai=True)
        estimate = (card or {}).get('compensation') or {}
        reasons = (card or {}).get('reasons') or []
        payload = {
            'company': job.get('company') or 'Unknown',
            'position': job.get('title') or 'Unknown',
            'level': job.get('seniority') or '',
            'location': job.get('normalized_city') or job.get('raw_location') or '',
            'work_model': job.get('work_model') or '',
            'source': job.get('source') or '',
            'job_url': job.get('job_url') or '',
            'status': 'Preparation',
            'priority': 'A' if int(job.get('match_score') or 0) >= 80 else 'B',
            'summary': (job.get('excerpt') or '')[:600],
            'match_summary': 'Match {0}/100 ({1}). {2}'.format(
                job.get('match_score') or 0, (card or {}).get('classification') or '',
                ' | '.join(reasons[:3]))[:900],
            'salary_estimate': estimate.get('display') or '',
            'salary_range': estimate.get('display') or '',
            'job_id': job_id,
            'match_score': job.get('match_score') or 0,
        }
        payload.update(extra or {})
        application = create(payload, conn)
        conn.execute("UPDATE discovered_jobs SET application_id=?, state='APPLIED', "
                     'state_changed_at=? WHERE id=?',
                     (application['id'], now_iso(), job_id))
        conn.commit()
        return application
    finally:
        if owns:
            conn.close()


def board(conn=None):
    """Counts per pipeline stage, in pipeline order."""
    owns = conn is None
    conn = conn or connect()
    try:
        counts = {status: 0 for status in APPLICATION_STATUSES}
        for row in conn.execute('SELECT status, COUNT(*) AS n FROM applications GROUP BY status'):
            counts[canonical_status(row['status'])] = row['n']
        return [{'status': status, 'count': counts[status]} for status in APPLICATION_STATUSES]
    finally:
        if owns:
            conn.close()
