"""The application pipeline, its documents and its chronological timeline.

An application is a record of something that was done, not a counter.  Three
things follow from that and live here:

``set_status``
    The single writer of ``applications.status``.  Everything that changes a
    status - the selector on the card, "Mark as Applied", the detail dialog -
    goes through it, so the timestamp, the history entry and the timeline note
    can never disagree with the status itself.

``applied_at``
    Set exactly once, the first time the record reaches a status that can only
    be reached by actually sending the application.  Going back to Applied
    after a rejection does not rewrite it: the date it was sent is a fact about
    the past.

``documents``
    Delegated to :mod:`jobscanner.application_documents`, which keeps a copy of
    what was sent rather than a pointer at the current CV.

Nothing in this module ever contacts an employer.  Every status here is local
tracking of something the user did themselves.
"""

from . import application_documents as appdocs
from .db import SUBMITTED_STATUSES, connect, now_iso, row_to_dict
from .schema_v2 import APPLICATION_STATUSES, CLOSED_STATUSES, canonical_status

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
        rows = [_decorate(row_to_dict(r)) for r in conn.execute(sql, params).fetchall()]
        # The card can be expanded without a second request, so the documents
        # and the history come along - in two queries for the whole list, not
        # two per card.
        documents = appdocs.list_many([r['id'] for r in rows], conn)
        history = _history_many([r['id'] for r in rows], conn)
        for row in rows:
            row['documents'] = documents.get(row['id'], [])
            row['document_summary'] = appdocs.summary(row['documents'])
            row['status_history'] = history.get(row['id'], [])
        return rows
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
        data = _decorate(row_to_dict(row))
        data['events'] = list_events(application_id, conn)
        data['documents'] = appdocs.list_for(application_id, conn)
        data['document_summary'] = appdocs.summary(data['documents'])
        data['status_history'] = list_status_history(application_id, conn)
        return data
    finally:
        if owns:
            conn.close()


def _decorate(data):
    """The two derived facts every screen asks for, computed in one place."""
    if not data:
        return None
    data['status'] = canonical_status(data.get('status'))
    data['is_active'] = data['status'] not in CLOSED_STATUSES
    data['is_applied'] = bool(str(data.get('applied_at') or '').strip())
    return data


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
        # A record created straight into a submitted status was sent before it
        # was tracked, so its applied timestamp is recorded at once rather than
        # waiting for a transition that has already happened.
        if data['status'] in SUBMITTED_STATUSES:
            columns.append('applied_at')
            values.append(ts)
        cursor = conn.execute('INSERT INTO applications ({0}) VALUES ({1})'.format(
            ','.join(columns), ','.join('?' for _ in columns)), values)
        application_id = cursor.lastrowid
        _record_status_change(conn, application_id, '', data['status'], ts)
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
        # A status inside a wider edit is still a status change, so it takes
        # the same route as the selector on the card: one writer, one set of
        # side effects.  Everything else is a plain field update.
        status = updates.pop('status', None)
        if updates:
            assignments = ','.join('{0}=?'.format(k) for k in updates)
            conn.execute(
                'UPDATE applications SET {0}, updated_at=? WHERE id=?'.format(assignments),
                list(updates.values()) + [now_iso(), application_id])
        if status is not None:
            _set_status(conn, current, status)
        conn.commit()
        return get_application(application_id, conn)
    finally:
        if owns:
            conn.close()


def set_status(application_id, status, conn=None):
    """Move one application to another status.  The only way a status changes.

    Purely local bookkeeping: nothing is sent anywhere, no form is submitted
    and no employer is contacted.  Returns the updated record, or ``None`` if
    there is no such application.
    """
    owns = conn is None
    conn = conn or connect()
    try:
        current = row_to_dict(conn.execute('SELECT * FROM applications WHERE id=?',
                                           (application_id,)).fetchone())
        if current is None:
            return None
        _set_status(conn, current, status)
        conn.commit()
        return get_application(application_id, conn)
    finally:
        if owns:
            conn.close()


def _set_status(conn, current, status):
    """Write the status, the timestamps, the history entry and the note.

    ``applied_at`` is written once and never rewritten: a record that goes
    Applied -> Rejected -> Applied keeps the day it was really sent.
    ``applied_date`` - the older, user-editable date field - is filled in from
    it only when it is still empty, so a date the user typed themselves always
    wins.
    """
    application_id = current['id']
    target = canonical_status(status)
    if target == canonical_status(current.get('status')):
        return False
    ts = now_iso()
    fields = {'status': target, 'updated_at': ts}
    if target in SUBMITTED_STATUSES and not str(current.get('applied_at') or '').strip():
        fields['applied_at'] = ts
        if not str(current.get('applied_date') or '').strip():
            fields['applied_date'] = ts[:10]
    conn.execute('UPDATE applications SET {0} WHERE id=?'.format(
        ','.join('{0}=?'.format(k) for k in fields)),
        list(fields.values()) + [application_id])
    _record_status_change(conn, application_id, current.get('status') or '', target, ts)
    add_event(application_id, {
        'event_type': 'Note', 'event_date': ts[:10],
        'note': 'Status changed from "{0}" to "{1}".'.format(
            current.get('status') or '-', target),
    }, conn=conn, commit=False)
    return True


# -- status history --------------------------------------------------------
def _record_status_change(conn, application_id, from_status, to_status, changed_at):
    conn.execute('INSERT INTO application_status_history '
                 '(application_id, from_status, to_status, changed_at) VALUES (?,?,?,?)',
                 (application_id, from_status, to_status, changed_at))


def _history_many(application_ids, conn):
    ids = [int(i) for i in application_ids]
    out = {i: [] for i in ids}
    if not ids:
        return out
    rows = conn.execute(
        'SELECT * FROM application_status_history WHERE application_id IN ({0}) '
        'ORDER BY application_id, id'.format(','.join('?' for _ in ids)), ids).fetchall()
    for row in rows:
        out[row['application_id']].append(row_to_dict(row))
    return out


def list_status_history(application_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        return [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM application_status_history WHERE application_id=? ORDER BY id',
            (application_id,)).fetchall()]
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


# -- documents sent --------------------------------------------------------
# Thin delegations.  The store itself lives in ``application_documents`` because
# what it does - copy the bytes, so the record stops depending on a file the
# document store may replace tomorrow - is a topic of its own.
def list_documents(application_id, conn=None):
    return appdocs.list_for(application_id, conn)


def attach_document(application_id, document_id, kind='', label='', conn=None,
                    source=appdocs.DEFAULT_SOURCE):
    """Copy a document-store file onto this application, exactly as it is now."""
    return appdocs.attach_stored(application_id, document_id, kind=kind, label=label,
                                 conn=conn, source=source)


def upload_document(application_id, kind, filename, data, label='', conn=None,
                    source='UPLOADED_FOR_APPLICATION'):
    """Store a PDF or DOCX uploaded straight onto this application."""
    return appdocs.attach_upload(application_id, kind, filename, data, label=label,
                                 conn=conn, source=source)


def detach_document(application_id, link_id, conn=None):
    return appdocs.remove(application_id, link_id, conn=conn)


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
