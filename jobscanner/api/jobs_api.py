"""Jobs: the default screen. One action (scan), then a ranked list."""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import applications as applications_mod
from .. import jobs_service, pipeline
from ..ai import service as ai_service
from ..apply import ApplyError, assistant
from ..db import connect, now_iso, row_to_dict
from ..person import from_row
from ..settings import load_settings

router = APIRouter(prefix='/api', tags=['jobs'])

VALID_STATES = ('NEW', 'SEEN', 'SAVED', 'IGNORED', 'APPLIED')


class StatePayload(BaseModel):
    state: str


class AnalysePayload(BaseModel):
    force: bool = False


class FeedbackPayload(BaseModel, extra='ignore'):
    """A personal verdict. Stored for later calibration; it changes no rule."""

    verdict: str = ''
    reason: str = ''
    note: str = ''


def _person(conn):
    row = conn.execute('SELECT * FROM person_profile WHERE id=1').fetchone()
    return from_row(row_to_dict(row)) if row else {}


@router.get('/jobs')
def list_jobs(state: str = '', limit: int = 200):
    with connect() as conn:
        settings = load_settings(conn)
        include_ignored = state == 'IGNORED' or not settings.get('scan_hide_ignored', True)
        return {
            'jobs': jobs_service.list_cards(conn, state=state, limit=limit,
                                            include_ignored=include_ignored),
            'counts': jobs_service.counts(conn),
            'last_scan': _last_scan(conn),
        }


@router.get('/jobs/{job_id}')
def get_job(job_id: int):
    card = jobs_service.get_card(job_id)
    if card is None:
        raise HTTPException(404, 'Job not found')
    return card


@router.post('/jobs/{job_id}/state')
def set_state(job_id: int, payload: StatePayload):
    state = payload.state.upper()
    if state not in VALID_STATES:
        raise HTTPException(400, 'Unknown state: {0}'.format(payload.state))
    with connect() as conn:
        row = conn.execute('SELECT id FROM discovered_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Job not found')
        conn.execute('UPDATE discovered_jobs SET state=?, state_changed_at=?, is_new=0 WHERE id=?',
                     (state, now_iso(), job_id))
        conn.commit()
        return jobs_service.get_card(job_id, conn, with_ai=False)


@router.get('/jobs/feedback/options')
def feedback_options():
    """What the Jobs screen may offer, and what has been recorded so far."""
    with connect() as conn:
        return {'verdicts': [v for v in jobs_service.FEEDBACK_VALUES if v],
                'reasons': jobs_service.FEEDBACK_REASONS,
                'summary': jobs_service.feedback_summary(conn)}


@router.post('/jobs/{job_id}/feedback')
def set_feedback(job_id: int, payload: FeedbackPayload):
    with connect() as conn:
        try:
            card = jobs_service.set_feedback(job_id, payload.verdict, payload.reason,
                                             payload.note, conn=conn)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if card is None:
            raise HTTPException(404, 'Job not found')
        return card


@router.post('/jobs/{job_id}/analyse')
def analyse(job_id: int, payload: AnalysePayload = AnalysePayload()):
    """AI (or template) analysis. Cached, so reopening a job costs nothing."""
    with connect() as conn:
        row = conn.execute('SELECT * FROM discovered_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Job not found')
        settings = load_settings(conn)
        result = ai_service.analyse_job(row_to_dict(row), _person(conn), settings=settings,
                                        conn=conn, force=payload.force)
        return {'analysis': result, 'ai': load_settings(conn).get('ai_enabled', False)}


@router.post('/scan')
def scan():
    result = pipeline.run_scan()
    with connect() as conn:
        result['jobs'] = jobs_service.list_cards(conn, limit=200)
        result['counts'] = jobs_service.counts(conn)
    return result


@router.get('/scan/last')
def last_scan():
    with connect() as conn:
        return _last_scan(conn) or {}


def _last_scan(conn):
    row = conn.execute('SELECT * FROM scout_runs ORDER BY id DESC LIMIT 1').fetchone()
    return row_to_dict(row) if row else None


# -- apply assistant -------------------------------------------------------
class ApplyPayload(BaseModel):
    url: str = ''


@router.get('/apply/status')
def apply_status():
    ok, message = assistant.SERVICE.available()
    with connect() as conn:
        settings = load_settings(conn)
        docs = assistant.document_choice(settings, conn)
    return {
        'available': ok,
        'message': message,
        'enabled': bool(settings.get('apply_enabled', True)),
        'never_submits': True,
        'cv': (docs['cv'] or {}).get('filename', ''),
        'motivation': (docs['motivation'] or {}).get('filename', ''),
    }


@router.post('/jobs/{job_id}/apply')
def apply_to_job(job_id: int, payload: ApplyPayload = ApplyPayload()):
    """Open the real application page and prefill the objective fields.

    This endpoint never submits anything; the user reviews and clicks Submit.
    """
    with connect() as conn:
        row = conn.execute('SELECT * FROM discovered_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Job not found')
        job = row_to_dict(row)
        settings = load_settings(conn)
        if not settings.get('apply_enabled', True):
            raise HTTPException(400, 'Application automation is switched off in Config.')
        person = _person(conn)
        docs = assistant.document_choice(settings, conn)
        url = payload.url or job.get('job_url') or ''
        session_id = conn.execute(
            'INSERT INTO apply_sessions (job_id, url, status, created_at, updated_at) '
            'VALUES (?,?,?,?,?)', (job_id, url, 'running', now_iso(), now_iso())).lastrowid
        conn.commit()

    try:
        report = assistant.SERVICE.open_application(url, person, settings, docs)
    except ApplyError as exc:
        with connect() as conn:
            conn.execute('UPDATE apply_sessions SET status=?, message=?, updated_at=? WHERE id=?',
                         ('error', str(exc)[:500], now_iso(), session_id))
            conn.commit()
        raise HTTPException(503, str(exc))

    with connect() as conn:
        conn.execute(
            'UPDATE apply_sessions SET adapter=?, status=?, filled_json=?, review_json=?, '
            'uploads_json=?, message=?, url=?, updated_at=? WHERE id=?',
            (report['adapter'], 'prepared',
             json.dumps(report['filled'], ensure_ascii=False),
             json.dumps(report['review'], ensure_ascii=False),
             json.dumps(report['uploads'], ensure_ascii=False),
             ' '.join(report['notes'])[:500], report['url'], now_iso(), session_id))
        conn.execute("UPDATE discovered_jobs SET state='SAVED', state_changed_at=? "
                     "WHERE id=? AND state IN ('NEW','SEEN')", (now_iso(), job_id))
        conn.commit()
    report['session_id'] = session_id
    report['reminder'] = ('Nothing was submitted. Review every field in the browser window '
                          'and click Submit yourself.')
    return report


@router.post('/apply/close')
def close_apply():
    return assistant.SERVICE.close()


@router.post('/jobs/{job_id}/application')
def create_application_from_job(job_id: int):
    application = applications_mod.from_job(job_id)
    if application is None:
        raise HTTPException(404, 'Job not found')
    return application
