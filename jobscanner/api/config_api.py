"""Config: every setting that does not belong on the everyday Jobs screen.

Sections: Search Profile, Geography, Target Roles, Matching, Compensation,
Company Watchlist, Job Sources, AI, Application Automation, Documents,
Backup & Transfer, System.
"""

import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import documents as documents_mod
from .. import jobs_service, workspace
from ..watchlist import CompanyWatchlist, source_health
from ..ai import service as ai_service
from ..db import (SCHEMA_VERSION, backup_database, backup_dir, connect, get_db_path,
                  load_profile, now_iso, row_to_dict, save_profile, workspace_root)
from ..settings import DEFAULT_SETTINGS, ai_status, load_settings, save_settings
from ..sources import adapter_types, get_adapter, kind_catalogue

router = APIRouter(prefix='/api/config', tags=['config'])


class SourcePayload(BaseModel, extra='ignore'):
    name: str = ''
    source_type: str = ''
    config: dict = {}
    enabled: bool = True


class WatchlistPayload(BaseModel, extra='ignore'):
    """``career_source_type`` is validated against the source registry."""

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
            'watchlist_source_kinds': kind_catalogue(),
            'source_health': source_health(conn),
            'benchmarks': [row_to_dict(r) for r in conn.execute(
                'SELECT * FROM salary_benchmarks ORDER BY company, seniority').fetchall()],
            'documents': documents_mod.categories(conn),
            'feedback': {
                'verdicts': [v for v in jobs_service.FEEDBACK_VALUES if v],
                'reasons': jobs_service.FEEDBACK_REASONS,
                'summary': jobs_service.feedback_summary(conn),
            },
            'bands': _bands(),
            'weights': _weights(),
            'transfer': _transfer(),
            'system': _system(conn),
        }


def _bands():
    """How a score is read. Fixed, so a band always means the same thing."""
    return [
        {'from': 80, 'to': 100, 'label': 'Excellent / High Priority'},
        {'from': 70, 'to': 79, 'label': 'Strong Match'},
        {'from': 60, 'to': 69, 'label': 'Worth Reviewing'},
        {'from': 50, 'to': 59, 'label': 'Weak / Edge Match'},
        {'from': 0, 'to': 49, 'label': 'Normally hidden from the Jobs list'},
    ]


def _weights():
    from ..scoring import WEIGHTS
    labels = {
        'role': 'Role responsibilities / actual scope',
        'seniority': 'Seniority & organizational scope',
        'technical': 'Technical domain fit',
        'leadership': 'Leadership / transformation',
        'location': 'Location / work model',
        'salary': 'Compensation potential',
        'strategic': 'AI / strategic relevance',
    }
    order = ('role', 'seniority', 'technical', 'leadership', 'location', 'salary', 'strategic')
    return {'weights': [{'key': k, 'label': labels[k], 'points': WEIGHTS[k]} for k in order],
            'total': sum(WEIGHTS.values())}


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
        row['last_status'] = row.get('last_status') or ''
        row['last_job_count'] = int(row.get('last_job_count') or 0)
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
        return {'watchlist': watch.list(), 'source_health': source_health(conn)}


@router.put('/watchlist/{entry_id}')
def update_watchlist(entry_id: int, payload: dict):
    with connect() as conn:
        watch = CompanyWatchlist(conn)
        try:
            updated = watch.update(entry_id, payload or {})
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if updated is None:
            raise HTTPException(404, 'Entry not found')
        return {'watchlist': watch.list(), 'source_health': source_health(conn)}


@router.delete('/watchlist/{entry_id}')
def delete_watchlist(entry_id: int):
    with connect() as conn:
        watch = CompanyWatchlist(conn)
        watch.delete(entry_id)
        return {'watchlist': watch.list(), 'source_health': source_health(conn)}


@router.post('/watchlist/{entry_id}/verify')
def verify_watchlist_source(entry_id: int):
    """Hit the company's real endpoint now and store what came back.

    This is the only thing that can move an entry to ACTIVE: a guessed board
    token that 404s or answers empty stays UNAVAILABLE / ERROR and says so.
    """
    with connect() as conn:
        watch = CompanyWatchlist(conn)
        entry = watch.verify(entry_id)
        if entry is None:
            raise HTTPException(404, 'Entry not found')
        return {'entry': entry, 'watchlist': watch.list(),
                'source_health': source_health(conn)}


@router.get('/source-health')
def get_source_health():
    """Config -> Job Sources: coverage per source kind plus every failure."""
    with connect() as conn:
        return source_health(conn)


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


# -- backup & transfer -----------------------------------------------------
def _transfer():
    """What the Backup & Transfer panel needs to describe itself."""
    return {
        'workspace_root': str(workspace_root()),
        'database_path': str(get_db_path()),
        'documents_path': str(documents_mod.documents_dir()),
        'format_version': workspace.EXPORT_FORMAT_VERSION,
        'schema_version': SCHEMA_VERSION,
        'privacy_notice': workspace.PRIVACY_NOTICE,
        'excluded': ['.env', 'API keys and access tokens', 'browser profiles and cookies',
                     'caches and temporary files', 'the virtual environment', '.git/'],
        'cli': {
            'export': 'python3 run.py export-workspace ~/Desktop/job-assistant-backup.zip',
            'import': 'python3 run.py import-workspace ~/Desktop/job-assistant-backup.zip',
        },
    }


@router.get('/workspace')
def workspace_info():
    return _transfer()


@router.post('/workspace/export')
def export_workspace():
    """Build the portable archive and hand it straight to the browser."""
    target = Path(tempfile.mkdtemp(prefix='workspace-download-'))
    try:
        result = workspace.export_workspace(target / workspace.default_archive_name())
    except workspace.WorkspaceError as exc:
        raise HTTPException(400, str(exc))
    return FileResponse(result['archive'], media_type='application/zip',
                        filename=Path(result['archive']).name,
                        headers={'X-Workspace-Documents': str(result['document_count']),
                                 'X-Workspace-Privacy': workspace.PRIVACY_NOTICE})


@router.post('/workspace/inspect')
async def inspect_workspace(file: UploadFile = File(...)):
    """Validate an uploaded archive without changing anything."""
    staged = Path(tempfile.mkdtemp(prefix='workspace-inspect-')) / 'upload.zip'
    staged.write_bytes(await file.read())
    try:
        report = workspace.inspect_archive(staged)
    except workspace.WorkspaceError as exc:
        raise HTTPException(400, str(exc))
    return {'manifest': report['manifest'], 'schema_version': report['schema_version'],
            'documents': len(report['documents']),
            'needs_migration': report['needs_migration']}


@router.post('/workspace/import')
async def import_workspace(file: UploadFile = File(...)):
    """Replace this workspace with an uploaded archive.

    The current database and documents are copied to ``data/backups/`` before
    anything is replaced, and a failure restores them, so the destination is
    never left half-written.
    """
    staged = Path(tempfile.mkdtemp(prefix='workspace-import-')) / 'upload.zip'
    staged.write_bytes(await file.read())
    try:
        return workspace.import_workspace(staged)
    except workspace.WorkspaceError as exc:
        raise HTTPException(400, str(exc))


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
