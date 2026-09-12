"""Config: every setting that does not belong on the everyday Jobs screen.

Sections: Job Sources, Company Watchlist, Matching, AI, Salary Model,
Application Automation, System.
"""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..watchlist import CompanyWatchlist
from ..ai import service as ai_service
from ..db import (backup_database, backup_dir, connect, get_db_path, load_profile,
                  now_iso, row_to_dict, save_profile)
from ..settings import DEFAULT_SETTINGS, ai_status, load_settings, save_settings
from ..sources import adapter_types, get_adapter

router = APIRouter(prefix='/api/config', tags=['config'])


class SourcePayload(BaseModel, extra='ignore'):
    name: str = ''
    source_type: str = ''
    config: dict = {}
    enabled: bool = True


class WatchlistPayload(BaseModel, extra='ignore'):
    company_name: str = ''
    priority: str = 'B'
    career_url: str = ''
    career_source_type: str = 'manual'
    career_source_identifier: str = ''
    enabled: bool = True
    notes: str = ''


@router.get('')
def get_config():
    with connect() as conn:
        settings = load_settings(conn)
        return {
            'settings': settings,
            'defaults': DEFAULT_SETTINGS,
            'ai': ai_status(settings),
            'matching': load_profile(conn),
            'sources': _sources(conn),
            'source_types': _source_types(),
            'watchlist': CompanyWatchlist(conn).list(),
            'benchmarks': [row_to_dict(r) for r in conn.execute(
                'SELECT * FROM salary_benchmarks ORDER BY company, seniority').fetchall()],
            'system': _system(conn),
        }


@router.put('/settings')
def update_settings(payload: dict):
    with connect() as conn:
        settings = save_settings(payload or {}, conn)
        return {'settings': settings, 'ai': ai_status(settings)}


@router.put('/matching')
def update_matching(payload: dict):
    """The search profile. Built-in for Sebastian, but tunable here."""
    with connect() as conn:
        return {'matching': save_profile(conn, payload or {})}


# -- job sources -----------------------------------------------------------
def _sources(conn):
    rows = [row_to_dict(r) for r in conn.execute(
        'SELECT * FROM job_sources ORDER BY id').fetchall()]
    for row in rows:
        try:
            row['config'] = json.loads(row.pop('config_json') or '{}')
        except (TypeError, ValueError):
            row['config'] = {}
        row['enabled'] = bool(row['enabled'])
    return rows


def _source_types():
    out = []
    for name in adapter_types():
        adapter = get_adapter(name)
        out.append({'type': name, 'label': adapter.label,
                    'supports_location_query': adapter.supports_location_query,
                    'limitations': adapter.limitations})
    return out


@router.post('/sources')
def create_source(payload: SourcePayload):
    if not payload.name.strip() or not payload.source_type.strip():
        raise HTTPException(400, 'Name and source type are required.')
    with connect() as conn:
        ts = now_iso()
        conn.execute('INSERT INTO job_sources (name, source_type, config_json, enabled, '
                     'created_at, updated_at) VALUES (?,?,?,?,?,?)',
                     (payload.name.strip(), payload.source_type.strip(),
                      json.dumps(payload.config or {}, ensure_ascii=False),
                      1 if payload.enabled else 0, ts, ts))
        conn.commit()
        return {'sources': _sources(conn)}


@router.put('/sources/{source_id}')
def update_source(source_id: int, payload: dict):
    with connect() as conn:
        current = conn.execute('SELECT * FROM job_sources WHERE id=?', (source_id,)).fetchone()
        if current is None:
            raise HTTPException(404, 'Source not found')
        updates = {}
        if 'name' in payload:
            updates['name'] = str(payload['name'] or '').strip()
        if 'source_type' in payload:
            updates['source_type'] = str(payload['source_type'] or '').strip()
        if 'config' in payload:
            updates['config_json'] = json.dumps(payload['config'] or {}, ensure_ascii=False)
        if 'enabled' in payload:
            updates['enabled'] = 1 if payload['enabled'] else 0
        if updates:
            assignments = ','.join('{0}=?'.format(k) for k in updates)
            conn.execute('UPDATE job_sources SET {0}, updated_at=? WHERE id=?'.format(assignments),
                         list(updates.values()) + [now_iso(), source_id])
            conn.commit()
        return {'sources': _sources(conn)}


@router.delete('/sources/{source_id}')
def delete_source(source_id: int):
    with connect() as conn:
        conn.execute('DELETE FROM job_sources WHERE id=?', (source_id,))
        conn.commit()
        return {'sources': _sources(conn)}


# -- watchlist -------------------------------------------------------------
@router.post('/watchlist')
def create_watchlist(payload: WatchlistPayload):
    with connect() as conn:
        watch = CompanyWatchlist(conn)
        try:
            watch.create(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {'watchlist': watch.list()}


@router.put('/watchlist/{entry_id}')
def update_watchlist(entry_id: int, payload: dict):
    with connect() as conn:
        watch = CompanyWatchlist(conn)
        if watch.update(entry_id, payload or {}) is None:
            raise HTTPException(404, 'Entry not found')
        return {'watchlist': watch.list()}


@router.delete('/watchlist/{entry_id}')
def delete_watchlist(entry_id: int):
    with connect() as conn:
        watch = CompanyWatchlist(conn)
        watch.delete(entry_id)
        return {'watchlist': watch.list()}


# -- AI --------------------------------------------------------------------
@router.post('/ai/clear-cache')
def clear_ai_cache():
    ai_service.clear_analysis()
    return {'cleared': True}


@router.post('/ai/test')
def test_ai():
    """Round-trip check that never stores anything."""
    settings = load_settings()
    provider = ai_service.get_provider(settings)
    if provider is None:
        return {'ok': False, 'message': ai_status(settings)['reason']}
    try:
        result = provider.analyse(
            ai_service.SYSTEM_PROMPT,
            'CANDIDATE\nName: Test\n\nJOB\nTitle: Head of Platform Engineering\n'
            'Company: Test AG\nLocation: Zurich (Hybrid)\nFull description:\n'
            'Lead the platform engineering group.\n')
        return {'ok': True, 'message': 'Provider answered.',
                'sample': result.get('fit_summary', '')[:200]}
    except Exception as exc:  # noqa: BLE001 - the message is the point
        return {'ok': False, 'message': '{0}: {1}'.format(type(exc).__name__, exc)[:400]}


# -- salary model ----------------------------------------------------------
@router.post('/benchmarks')
def create_benchmark(payload: dict):
    with connect() as conn:
        conn.execute(
            '''INSERT INTO salary_benchmarks (company, role_family, seniority, location,
                 base_min, base_max, bonus_pct_min, bonus_pct_max, equity, confidence,
                 notes, is_builtin, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,0,?)''',
            (str(payload.get('company') or '').strip(),
             str(payload.get('role_family') or 'technology_operations').strip(),
             str(payload.get('seniority') or 'Head of').strip(),
             str(payload.get('location') or 'Switzerland').strip(),
             int(payload.get('base_min') or 0), int(payload.get('base_max') or 0),
             int(payload.get('bonus_pct_min') or 0), int(payload.get('bonus_pct_max') or 0),
             str(payload.get('equity') or ''), str(payload.get('confidence') or 'Medium'),
             str(payload.get('notes') or ''), now_iso()))
        conn.execute('DELETE FROM compensation_estimates')
        conn.commit()
        return {'benchmarks': [row_to_dict(r) for r in conn.execute(
            'SELECT * FROM salary_benchmarks ORDER BY company, seniority').fetchall()]}


@router.delete('/benchmarks/{benchmark_id}')
def delete_benchmark(benchmark_id: int):
    with connect() as conn:
        conn.execute('DELETE FROM salary_benchmarks WHERE id=?', (benchmark_id,))
        conn.execute('DELETE FROM compensation_estimates')
        conn.commit()
        return {'deleted': True}


@router.post('/salary/recalculate')
def recalculate_salary():
    with connect() as conn:
        conn.execute('DELETE FROM compensation_estimates')
        conn.commit()
    return {'cleared': True}


# -- system ----------------------------------------------------------------
def _system(conn):
    def count(table):
        return conn.execute('SELECT COUNT(*) FROM {0}'.format(table)).fetchone()[0]
    folder = backup_dir()
    backups = sorted((p.name for p in folder.glob('*.db')), reverse=True) if folder.exists() else []
    return {
        'database_path': str(get_db_path()),
        'backups': backups[:10],
        'counts': {
            'jobs': count('discovered_jobs'),
            'applications': count('applications'),
            'events': count('events'),
            'documents': count('documents'),
            'rejections': count('rejected_jobs'),
            'scans': count('scout_runs'),
        },
    }


@router.post('/system/backup')
def make_backup():
    path = backup_database('manual')
    return {'backup': path}
