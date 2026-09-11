#!/usr/bin/env python3
"""HTTP layer of the local Swiss job scanner + application tracker.

This module only does transport: routing, JSON, static files.  All filtering,
scoring and persistence logic lives in the ``jobscanner`` package so it can be
unit tested without a server.  Nothing here re-implements location rules.
"""

import json
import os
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from jobscanner import db as jsdb
from jobscanner import pipeline
from jobscanner import presets as presets_mod
from jobscanner import profile as profile_mod
from jobscanner.locations import LocationNormalizer
from jobscanner.repository import SORT_ORDERS, JOB_STATES, JobRepository
from jobscanner.scoring import EXCELLENT_FROM, STRONG_FROM
from jobscanner.sources import adapter_types, get_adapter
from jobscanner.watchlist import CompanyWatchlist, PRIORITIES, WATCHLIST_SOURCE_TYPES

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / 'static'
DATA_DIR = BASE_DIR / 'data'
DB_PATH = jsdb.get_db_path()
HOST = '127.0.0.1'
PORT = int(os.environ.get('APPLICATION_TRACKER_PORT', '8765'))

APPLICATION_FIELDS = [
    'company', 'position', 'level', 'location', 'work_model', 'source', 'job_url',
    'applied_date', 'status', 'last_response', 'summary', 'next_action',
    'follow_up_date', 'contact_name', 'contact_details', 'salary_range', 'priority',
    'cv_version', 'cover_letter', 'notes'
]
EVENT_FIELDS = ['event_date', 'event_type', 'person', 'note', 'next_step', 'follow_up_date']

# The tracker keeps its German status vocabulary; these are the same stages the
# brief lists in English (Preparation, Applied, HR Screening, ...).
APPLICATION_STATUSES = [
    'Vorbereitung', 'Beworben', 'Eingangsbestätigung', 'Screening / HR', 'Interview 1',
    'Interview 2', 'Case / Assessment', 'Final Interview', 'Angebot', 'On Hold',
    'Abgelehnt', 'Zurückgezogen',
]
INITIAL_APPLICATION_STATUS = 'Vorbereitung'   # == "Preparation"

now_iso = jsdb.now_iso
utc_now_iso = jsdb.utc_now_iso
db_connect = jsdb.connect
row_to_dict = jsdb.row_to_dict
init_db = jsdb.init_db


def infer_level(title):
    from jobscanner.normalizer import detect_seniority
    return detect_seniority(title)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        clean = urlparse(path).path.lstrip('/') or 'index.html'
        target = (STATIC_DIR / clean).resolve()
        try:
            target.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return str(STATIC_DIR / 'index.html')
        return str(target)

    def log_message(self, format, *args):
        pass

    # -- plumbing ---------------------------------------------------------
    def _send_json(self, payload, status=200, headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get('Content-Length', '0') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            return json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('Ungültige JSON-Daten')

    def _not_found(self):
        self._send_json({'error': 'Nicht gefunden'}, 404)

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith('/api/'):
            return super().do_GET()
        query = parse_qs(parsed.query)
        try:
            routes = {
                '/api/applications': lambda: self.get_applications(query),
                '/api/dashboard': self.get_dashboard,
                '/api/export': self.export_data,
                '/api/search-profile': self.get_search_profile,
                '/api/scout/profile': self.get_search_profile,          # legacy alias
                '/api/presets': self.get_presets,
                '/api/watchlist': self.get_watchlist,
                '/api/scout/jobs': lambda: self.get_scout_jobs(query),
                '/api/scout/summary': self.get_scout_summary,
                '/api/scout/rejected': lambda: self.get_rejected(query),
                '/api/scout/sources': self.get_sources,
                '/api/meta': self.get_meta,
            }
            if path in routes:
                return routes[path]()
            parts = [p for p in path.split('/') if p]
            if len(parts) == 3 and parts[:2] == ['api', 'applications']:
                return self.get_application(int(parts[2]))
            if len(parts) == 4 and parts[:2] == ['api', 'applications'] and parts[3] == 'events':
                return self.get_events(int(parts[2]))
            self._not_found()
        except ValueError as exc:
            self._send_json({'error': str(exc)}, 400)
        except Exception as exc:
            self._send_json({'error': str(exc)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == '/api/applications':
                return self.create_application(self._read_json())
            if path == '/api/import':
                return self.import_data(self._read_json())
            if path in ('/api/scan', '/api/scout/search'):
                return self._send_json(pipeline.run_scan())
            if path == '/api/scout/sources':
                return self.create_source(self._read_json())
            if path == '/api/watchlist':
                return self.create_watchlist_entry(self._read_json())
            parts = [p for p in path.split('/') if p]
            if len(parts) == 4 and parts[:2] == ['api', 'presets'] and parts[3] == 'apply':
                return self.apply_preset(parts[2])
            if len(parts) == 4 and parts[:2] == ['api', 'applications'] and parts[3] == 'events':
                return self.create_event(int(parts[2]), self._read_json())
            if len(parts) == 5 and parts[:3] == ['api', 'scout', 'jobs'] and parts[4] == 'convert':
                return self.convert_job(int(parts[3]))
            self._not_found()
        except ValueError as exc:
            self._send_json({'error': str(exc)}, 400)
        except Exception as exc:
            self._send_json({'error': str(exc)}, 500)

    def do_PUT(self):
        path = urlparse(self.path).path
        parts = [p for p in path.split('/') if p]
        try:
            if path in ('/api/search-profile', '/api/scout/profile'):
                return self.update_search_profile(self._read_json())
            if len(parts) == 4 and parts[:3] == ['api', 'scout', 'sources']:
                return self.update_source(int(parts[3]), self._read_json())
            if len(parts) == 3 and parts[:2] == ['api', 'applications']:
                return self.update_application(int(parts[2]), self._read_json())
            if len(parts) == 5 and parts[:3] == ['api', 'scout', 'jobs'] and parts[4] == 'state':
                return self.update_job_state(int(parts[3]), self._read_json())
            if len(parts) == 3 and parts[:2] == ['api', 'watchlist']:
                return self.update_watchlist_entry(int(parts[2]), self._read_json())
            self._not_found()
        except ValueError as exc:
            self._send_json({'error': str(exc)}, 400)
        except Exception as exc:
            self._send_json({'error': str(exc)}, 500)

    def do_DELETE(self):
        parts = [p for p in urlparse(self.path).path.split('/') if p]
        try:
            if len(parts) == 3 and parts[:2] == ['api', 'applications']:
                return self.delete_application(int(parts[2]))
            if len(parts) == 3 and parts[:2] == ['api', 'events']:
                return self.delete_event(int(parts[2]))
            if len(parts) == 4 and parts[:3] == ['api', 'scout', 'sources']:
                return self.delete_source(int(parts[3]))
            if len(parts) == 3 and parts[:2] == ['api', 'watchlist']:
                return self.delete_watchlist_entry(int(parts[2]))
            self._not_found()
        except Exception as exc:
            self._send_json({'error': str(exc)}, 500)

    # ---------------- Applications ----------------
    def get_applications(self, query):
        clauses, params = [], []
        status = (query.get('status') or [''])[0].strip()
        priority = (query.get('priority') or [''])[0].strip()
        search = (query.get('search') or [''])[0].strip()
        if status:
            clauses.append('status = ?'); params.append(status)
        if priority:
            clauses.append('priority = ?'); params.append(priority)
        if search:
            clauses.append('(company LIKE ? OR position LIKE ? OR location LIKE ? OR contact_name LIKE ?)')
            params.extend(['%{0}%'.format(search)] * 4)
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        sql = '''SELECT *, (SELECT COUNT(*) FROM events e WHERE e.application_id = applications.id) AS event_count
            FROM applications{0}
            ORDER BY CASE priority WHEN 'A - Sehr interessant' THEN 1 WHEN 'B - Interessant' THEN 2 ELSE 3 END,
                     CASE WHEN follow_up_date <> '' THEN follow_up_date ELSE '9999-12-31' END, updated_at DESC'''.format(where)
        with db_connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        self._send_json([row_to_dict(r) for r in rows])

    def get_application(self, app_id):
        with db_connect() as conn:
            row = conn.execute('SELECT * FROM applications WHERE id = ?', (app_id,)).fetchone()
        if not row:
            return self._not_found()
        self._send_json(row_to_dict(row))

    def create_application(self, payload):
        company = str(payload.get('company', '')).strip()
        position = str(payload.get('position', '')).strip()
        if not company or not position:
            raise ValueError('Unternehmen und Position sind Pflichtfelder.')
        values = {field: str(payload.get(field, '') or '').strip() for field in APPLICATION_FIELDS}
        values['company'], values['position'] = company, position
        values['status'] = values['status'] or INITIAL_APPLICATION_STATUS
        values['priority'] = values['priority'] or 'B - Interessant'
        ts = now_iso()
        cols = APPLICATION_FIELDS + ['created_at', 'updated_at']
        params = [values[c] for c in APPLICATION_FIELDS] + [ts, ts]
        with db_connect() as conn:
            cur = conn.execute('INSERT INTO applications ({0}) VALUES ({1})'.format(
                ','.join(cols), ','.join('?' for _ in cols)), params)
            app_id = cur.lastrowid
            if values['applied_date']:
                conn.execute('''INSERT INTO events (application_id,event_date,event_type,person,note,next_step,follow_up_date,created_at)
                    VALUES (?,?,?,?,?,?,?,?)''', (app_id, values['applied_date'], 'Bewerbung versendet', '',
                                                  'Bewerbung eingereicht.', values['next_action'],
                                                  values['follow_up_date'], ts))
        return self.get_application(app_id)

    def update_application(self, app_id, payload):
        with db_connect() as conn:
            if not conn.execute('SELECT id FROM applications WHERE id = ?', (app_id,)).fetchone():
                return self._not_found()
            updates, params = [], []
            for field in APPLICATION_FIELDS:
                if field in payload:
                    updates.append('{0} = ?'.format(field))
                    params.append(str(payload.get(field, '') or '').strip())
            if not updates:
                raise ValueError('Keine Änderungen übermittelt.')
            updates.append('updated_at = ?')
            params.extend([now_iso(), app_id])
            conn.execute('UPDATE applications SET {0} WHERE id = ?'.format(', '.join(updates)), params)
        return self.get_application(app_id)

    def delete_application(self, app_id):
        with db_connect() as conn:
            cur = conn.execute('DELETE FROM applications WHERE id = ?', (app_id,))
        if cur.rowcount == 0:
            return self._not_found()
        self._send_json({'ok': True})

    def get_events(self, app_id):
        with db_connect() as conn:
            rows = conn.execute('SELECT * FROM events WHERE application_id = ? ORDER BY event_date DESC, id DESC',
                                (app_id,)).fetchall()
        self._send_json([row_to_dict(r) for r in rows])

    def create_event(self, app_id, payload):
        with db_connect() as conn:
            if not conn.execute('SELECT id FROM applications WHERE id = ?', (app_id,)).fetchone():
                return self._not_found()
            values = {field: str(payload.get(field, '') or '').strip() for field in EVENT_FIELDS}
            values['event_date'] = values['event_date'] or datetime.now().date().isoformat()
            values['event_type'] = values['event_type'] or 'Sonstiges'
            ts = now_iso()
            conn.execute('''INSERT INTO events (application_id,event_date,event_type,person,note,next_step,follow_up_date,created_at)
                VALUES (?,?,?,?,?,?,?,?)''', (app_id, values['event_date'], values['event_type'], values['person'],
                                              values['note'], values['next_step'], values['follow_up_date'], ts))
            parts, params = ['updated_at = ?'], [ts]
            if values['event_type'] != 'Bewerbung versendet':
                parts.append('last_response = ?'); params.append(values['event_date'])
            if values['next_step']:
                parts.append('next_action = ?'); params.append(values['next_step'])
            if values['follow_up_date']:
                parts.append('follow_up_date = ?'); params.append(values['follow_up_date'])
            status_map = {'Bewerbung versendet': 'Beworben', 'Eingangsbestätigung': 'Eingangsbestätigung',
                          'HR-Screening': 'Screening / HR', 'Interview': 'Interview 1',
                          'Case / Assessment': 'Case / Assessment', 'Angebot': 'Angebot', 'Absage': 'Abgelehnt'}
            if status_map.get(values['event_type']):
                parts.append('status = ?'); params.append(status_map[values['event_type']])
            params.append(app_id)
            conn.execute('UPDATE applications SET {0} WHERE id = ?'.format(', '.join(parts)), params)
        return self.get_events(app_id)

    def delete_event(self, event_id):
        with db_connect() as conn:
            cur = conn.execute('DELETE FROM events WHERE id = ?', (event_id,))
        if cur.rowcount == 0:
            return self._not_found()
        self._send_json({'ok': True})

    def get_dashboard(self):
        today = datetime.now().date().isoformat()
        with db_connect() as conn:
            def one(sql, *params):
                return conn.execute(sql, params).fetchone()[0]
            total = one('SELECT COUNT(*) FROM applications')
            data = {
                'total': total,
                'active': one("SELECT COUNT(*) FROM applications WHERE status NOT IN ('Abgelehnt','Zurückgezogen')"),
                'interviews': one("SELECT COUNT(*) FROM applications WHERE status IN "
                                  "('Interview 1','Interview 2','Case / Assessment','Final Interview')"),
                'offers': one("SELECT COUNT(*) FROM applications WHERE status = 'Angebot'"),
                'rejected': one("SELECT COUNT(*) FROM applications WHERE status = 'Abgelehnt'"),
                'followups_due': one("SELECT COUNT(*) FROM applications WHERE follow_up_date <> '' "
                                     "AND follow_up_date <= ? AND status NOT IN ('Abgelehnt','Zurückgezogen')", today),
            }
            responses = one("SELECT COUNT(*) FROM applications WHERE last_response <> ''")
            data['response_rate'] = (responses / total) if total else 0
            data['status_counts'] = [row_to_dict(r) for r in conn.execute(
                'SELECT status, COUNT(*) AS count FROM applications GROUP BY status ORDER BY count DESC').fetchall()]
            data['followups'] = [row_to_dict(r) for r in conn.execute(
                '''SELECT id,company,position,follow_up_date,next_action,status FROM applications
                   WHERE follow_up_date <> '' AND follow_up_date <= ?
                     AND status NOT IN ('Abgelehnt','Zurückgezogen')
                   ORDER BY follow_up_date ASC LIMIT 8''', (today,)).fetchall()]
            minimum = int(jsdb.load_profile(conn).get('minimum_match_score') or 0)
            counts = JobRepository(conn).counts(minimum_score=minimum)
        data['scout_new'] = counts['new']
        data['scout_strong'] = counts['strong']
        data['scout'] = {
            'new_strong': counts['new_strong'],
            'saved': counts['saved'],
            'applications': total,
            'interviews': data['interviews'],
            'offers': data['offers'],
        }
        self._send_json(data)

    # ---------------- Search profile ----------------
    def get_search_profile(self):
        with db_connect() as conn:
            profile = jsdb.load_profile(conn)
            profile['available_sources'] = [row_to_dict(r) for r in conn.execute(
                'SELECT name, source_type, enabled FROM job_sources ORDER BY id').fetchall()]
            presets = jsdb.load_presets(conn)
        recommended = next((p for p in presets if p['is_recommended']), None)
        profile['defaults'] = profile_mod.DEFAULT_PROFILE
        profile['seniority_options'] = profile_mod.SENIORITY_LEVELS
        profile['summary'] = presets_mod.summarize(profile)
        profile['presets'] = presets
        profile['recommended_preset'] = recommended
        # True only while the active profile still IS the recommended preset;
        # any manual edit clears preset_key and flips this to False.
        profile['is_recommended_active'] = bool(
            recommended and profile.get('preset_key') == recommended['key'])
        profile['options'] = {
            'country_modes': list(profile_mod.COUNTRY_MODES),
            'location_filter_modes': list(profile_mod.LOCATION_FILTER_MODES),
            'salary_modes': list(profile_mod.SALARY_MODES),
            'sort_modes': list(profile_mod.SORT_MODES),
        }
        self._send_json(profile)

    def update_search_profile(self, payload):
        with db_connect() as conn:
            jsdb.save_profile(conn, payload)
        return self.get_search_profile()

    def get_presets(self):
        with db_connect() as conn:
            active = jsdb.load_profile(conn).get('preset_key') or ''
            presets = jsdb.load_presets(conn)
        self._send_json({'presets': presets, 'active_preset': active})

    def apply_preset(self, key):
        """Copy a preset into the active profile - the explicit apply/restore."""
        with db_connect() as conn:
            jsdb.apply_preset(conn, key)
        return self.get_search_profile()

    # ---------------- Company watchlist ----------------
    def get_watchlist(self):
        with db_connect() as conn:
            entries = CompanyWatchlist(conn).list()
        self._send_json({
            'entries': entries,
            'priorities': list(PRIORITIES),
            'source_types': list(WATCHLIST_SOURCE_TYPES),
        })

    def create_watchlist_entry(self, payload):
        with db_connect() as conn:
            entry = CompanyWatchlist(conn).create(payload)
        self._send_json(entry, 201)

    def update_watchlist_entry(self, entry_id, payload):
        with db_connect() as conn:
            entry = CompanyWatchlist(conn).update(entry_id, payload)
        if entry is None:
            return self._not_found()
        self._send_json(entry)

    def delete_watchlist_entry(self, entry_id):
        with db_connect() as conn:
            removed = CompanyWatchlist(conn).delete(entry_id)
        if not removed:
            return self._not_found()
        self._send_json({'ok': True})

    def get_meta(self):
        self._send_json({
            'job_states': list(JOB_STATES),
            'application_statuses': APPLICATION_STATUSES,
            'source_types': [{'type': t, 'label': get_adapter(t).label,
                              'supports_location_query': get_adapter(t).supports_location_query,
                              'limitations': get_adapter(t).limitations}
                             for t in adapter_types()],
            'schema_version': jsdb.SCHEMA_VERSION,
            'sort_modes': list(SORT_ORDERS),
            'watchlist_source_types': list(WATCHLIST_SOURCE_TYPES),
            'score_bands': {'excellent': EXCELLENT_FROM, 'strong': STRONG_FROM},
        })

    # ---------------- Job scout ----------------
    def get_scout_jobs(self, query):
        state = (query.get('state') or [''])[0].strip().upper()
        search = (query.get('search') or [''])[0].strip()
        new_only = (query.get('new_only') or ['0'])[0] == '1'
        try:
            min_score = int((query.get('min_score') or ['0'])[0] or 0)
        except ValueError:
            min_score = 0
        if state and state not in JOB_STATES:
            raise ValueError('Unbekannter Job-Status: {0}'.format(state))
        sort = (query.get('sort') or [''])[0].strip().lower()
        with db_connect() as conn:
            if not sort:
                sort = jsdb.load_profile(conn).get('sort_mode') or 'score'
            if sort not in SORT_ORDERS:
                raise ValueError('Unknown sort order: {0}'.format(sort))
            jobs = JobRepository(conn).list_jobs(state=state, search=search,
                                                 min_score=min_score, new_only=new_only,
                                                 sort=sort)
        self._send_json(jobs)

    def get_scout_summary(self):
        with db_connect() as conn:
            profile = jsdb.load_profile(conn)
            repo = JobRepository(conn)
            minimum = int(profile.get('minimum_match_score') or 0)
            counts = repo.counts(minimum_score=minimum)
            last = row_to_dict(conn.execute('SELECT * FROM scout_runs ORDER BY id DESC LIMIT 1').fetchone())
            sources = [row_to_dict(r) for r in conn.execute(
                'SELECT name, enabled FROM job_sources ORDER BY id').fetchall()]
            rejection_summary = repo.rejection_summary(last['id'] if last else None)
            rejection_groups = repo.rejection_groups(last['id'] if last else None)
        if last:
            try:
                last['errors'] = json.loads(last.get('errors') or '[]')
            except (TypeError, ValueError):
                last['errors'] = []
            last.pop('profile_snapshot', None)
        auto_hours = int(profile.get('auto_hours') or 0)
        due = bool(auto_hours)
        if last and last.get('finished_at') and auto_hours:
            try:
                last_dt = datetime.fromisoformat(last['finished_at'].replace('Z', '+00:00'))
                due = datetime.now(timezone.utc) - last_dt >= timedelta(hours=auto_hours)
            except ValueError:
                due = True
        # The funnel: does the search actually work, and where do jobs go?
        funnel = {
            'fetched': (last or {}).get('fetched_count') or 0,
            'swiss_eligible': (last or {}).get('geo_passed_count') or 0,
            'relevant': counts['relevant'],
            'strong': counts['strong'],
            'excellent': counts['excellent'],
            'minimum_match_score': minimum,
            'strong_from': STRONG_FROM,
            'excellent_from': EXCELLENT_FROM,
        }
        self._send_json({
            'counts': counts,
            'funnel': funnel,
            'last_run': last,
            'auto_due': due,
            'auto_hours': auto_hours,
            'minimum_match_score': minimum,
            'country_mode': profile.get('country_mode'),
            'sort_mode': profile.get('sort_mode') or 'score',
            'profile_summary': presets_mod.summarize(profile),
            'active_sources': [s['name'] for s in sources if s['enabled']],
            'rejection_summary': rejection_summary,
            'rejection_groups': rejection_groups,
        })

    def get_rejected(self, query):
        try:
            limit = min(1000, max(1, int((query.get('limit') or ['300'])[0])))
        except ValueError:
            limit = 300
        with db_connect() as conn:
            last = conn.execute('SELECT id FROM scout_runs ORDER BY id DESC LIMIT 1').fetchone()
            repo = JobRepository(conn)
            run_id = last['id'] if last else None
            self._send_json({
                'run_id': run_id,
                'summary': repo.rejection_summary(run_id),
                'groups': repo.rejection_groups(run_id),
                'items': repo.list_rejections(run_id, limit=limit),
            })

    def update_job_state(self, job_id, payload):
        state = str(payload.get('state') or '').strip().upper()
        if state not in JOB_STATES:
            raise ValueError('Ungültiger Job-Status: {0}'.format(state))
        with db_connect() as conn:
            repo = JobRepository(conn)
            if not repo.set_state(job_id, state):
                return self._not_found()
            job = repo.get(job_id)
        self._send_json(job)

    def convert_job(self, job_id):
        """ADD TO APPLICATIONS - copies the full match context into the tracker."""
        with db_connect() as conn:
            repo = JobRepository(conn)
            job = repo.get(job_id)
            if not job:
                return self._not_found()
            if job.get('application_id'):
                existing = conn.execute('SELECT * FROM applications WHERE id=?',
                                        (job['application_id'],)).fetchone()
                if existing:
                    return self._send_json({'application': row_to_dict(existing), 'already_exists': True})

            salary = ''
            if job.get('salary_min') is not None or job.get('salary_max') is not None:
                pieces = [str(int(v)) for v in (job.get('salary_min'), job.get('salary_max')) if v is not None]
                salary = '–'.join(pieces) + (' {0}'.format(job.get('salary_currency')) if job.get('salary_currency') else '')
            explanation = ['MATCH SCORE: {0}/100 ({1})'.format(job['match_score'], job['match_label'])]
            if job.get('match_reasons'):
                explanation.append('Stärken: ' + '; '.join(job['match_reasons']))
            if job.get('match_concerns'):
                explanation.append('Offene Punkte: ' + '; '.join(job['match_concerns']))
            location = job.get('normalized_city') or job.get('normalized_country') or job.get('raw_location') or ''
            if job.get('normalized_city') and job.get('normalized_country'):
                location = '{0}, {1}'.format(job['normalized_city'], job['normalized_country'])

            values = {
                'company': job['company'], 'position': job['title'],
                'level': job.get('seniority') or '', 'location': location,
                'work_model': job.get('work_model') or '',
                'source': 'Job Scout · {0}'.format(job['source']),
                'job_url': job['job_url'], 'applied_date': '',
                'status': INITIAL_APPLICATION_STATUS, 'last_response': '',
                'summary': ' | '.join(explanation),
                'next_action': 'Stellenanzeige prüfen und Bewerbung vorbereiten',
                'follow_up_date': '', 'contact_name': '', 'contact_details': '',
                'salary_range': salary,
                'priority': 'A - Sehr interessant' if job['match_score'] >= 80 else 'B - Interessant',
                'cv_version': '', 'cover_letter': '',
                'notes': 'Gefunden am {0} · Quelle: {1} · Standort laut Quelle: {2}'.format(
                    job['first_seen'], job['source'], job.get('raw_location') or '—'),
            }
            ts = now_iso()
            cols = APPLICATION_FIELDS + ['created_at', 'updated_at']
            params = [values[c] for c in APPLICATION_FIELDS] + [ts, ts]
            cur = conn.execute('INSERT INTO applications ({0}) VALUES ({1})'.format(
                ','.join(cols), ','.join('?' for _ in cols)), params)
            app_id = cur.lastrowid
            repo.set_state(job_id, 'APPLIED', application_id=app_id)
            application = conn.execute('SELECT * FROM applications WHERE id=?', (app_id,)).fetchone()
        self._send_json({'application': row_to_dict(application), 'already_exists': False})

    # ---------------- Job sources ----------------
    def get_sources(self):
        with db_connect() as conn:
            rows = conn.execute('SELECT * FROM job_sources ORDER BY id').fetchall()
        result = []
        for row in rows:
            data = row_to_dict(row)
            try:
                data['config'] = json.loads(data.get('config_json') or '{}')
            except (TypeError, ValueError):
                data['config'] = {}
            data['enabled'] = bool(data.get('enabled'))
            try:
                adapter = get_adapter(data['source_type'])
                data['limitations'] = adapter.limitations
                data['supports_location_query'] = adapter.supports_location_query
            except Exception:
                data['limitations'] = ''
                data['supports_location_query'] = False
            result.append(data)
        self._send_json(result)

    def _source_payload(self, payload):
        name = str(payload.get('name') or '').strip()
        stype = str(payload.get('source_type') or '').strip().lower()
        if not name or stype not in adapter_types():
            raise ValueError('Name und gültiger Quellentyp sind erforderlich.')
        config = payload.get('config') if isinstance(payload.get('config'), dict) else {}
        return name, stype, config, 1 if payload.get('enabled', True) else 0

    def create_source(self, payload):
        name, stype, config, enabled = self._source_payload(payload)
        ts = now_iso()
        with db_connect() as conn:
            cur = conn.execute(
                'INSERT INTO job_sources (name,source_type,config_json,enabled,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?)',
                (name, stype, json.dumps(config, ensure_ascii=False), enabled, ts, ts))
            row = conn.execute('SELECT * FROM job_sources WHERE id=?', (cur.lastrowid,)).fetchone()
        self._send_json(row_to_dict(row), 201)

    def update_source(self, source_id, payload):
        name, stype, config, enabled = self._source_payload(payload)
        with db_connect() as conn:
            cur = conn.execute(
                'UPDATE job_sources SET name=?,source_type=?,config_json=?,enabled=?,updated_at=? WHERE id=?',
                (name, stype, json.dumps(config, ensure_ascii=False), enabled, now_iso(), source_id))
            if cur.rowcount == 0:
                return self._not_found()
        return self.get_sources()

    def delete_source(self, source_id):
        with db_connect() as conn:
            cur = conn.execute('DELETE FROM job_sources WHERE id=?', (source_id,))
        if cur.rowcount == 0:
            return self._not_found()
        self._send_json({'ok': True})

    # ---------------- Backup ----------------
    def export_data(self):
        with db_connect() as conn:
            def rows(sql):
                return [row_to_dict(r) for r in conn.execute(sql).fetchall()]
            payload = {
                'format': 'BewerbungsTrackerBackup', 'version': 4, 'exported_at': now_iso(),
                'applications': rows('SELECT * FROM applications ORDER BY id'),
                'events': rows('SELECT * FROM events ORDER BY id'),
                'search_profile': row_to_dict(conn.execute('SELECT * FROM search_profile WHERE id=1').fetchone()),
                'discovered_jobs': rows('SELECT * FROM discovered_jobs ORDER BY id'),
                'scout_runs': rows('SELECT * FROM scout_runs ORDER BY id'),
                'job_sources': rows('SELECT * FROM job_sources ORDER BY id'),
                'company_watchlist': rows('SELECT * FROM company_watchlist ORDER BY id'),
            }
        filename = 'bewerbungs-tracker-backup-{0}.json'.format(datetime.now().date().isoformat())
        self._send_json(payload, 200, {'Content-Disposition': 'attachment; filename="{0}"'.format(filename)})

    def import_data(self, payload):
        if payload.get('format') != 'BewerbungsTrackerBackup':
            raise ValueError('Die Datei ist kein gültiges Bewerbungs-Tracker-Backup.')
        apps = payload.get('applications', [])
        events = payload.get('events', [])
        if not isinstance(apps, list) or not isinstance(events, list):
            raise ValueError('Backup-Daten sind ungültig.')
        with db_connect() as conn:
            conn.execute('DELETE FROM events')
            conn.execute('DELETE FROM applications')
            for app in apps:
                fields = ['id'] + APPLICATION_FIELDS + ['created_at', 'updated_at']
                conn.execute('INSERT INTO applications ({0}) VALUES ({1})'.format(
                    ','.join(fields), ','.join('?' for _ in fields)), [app.get(f, '') for f in fields])
            for event in events:
                fields = ['id', 'application_id'] + EVENT_FIELDS + ['created_at']
                conn.execute('INSERT INTO events ({0}) VALUES ({1})'.format(
                    ','.join(fields), ','.join('?' for _ in fields)), [event.get(f, '') for f in fields])
            version = int(payload.get('version') or 1)
            if version >= 4:
                profile = payload.get('search_profile')
                if isinstance(profile, dict):
                    jsdb.save_profile(conn, profile_mod.from_row(profile))
                if isinstance(payload.get('job_sources'), list):
                    conn.execute('DELETE FROM job_sources')
                    for source in payload['job_sources']:
                        fields = ['id', 'name', 'source_type', 'config_json', 'enabled', 'created_at', 'updated_at']
                        conn.execute('INSERT INTO job_sources ({0}) VALUES ({1})'.format(
                            ','.join(fields), ','.join('?' for _ in fields)), [source.get(f) for f in fields])
                if isinstance(payload.get('company_watchlist'), list):
                    conn.execute('DELETE FROM company_watchlist')
                    fields = ['id', 'company_name', 'enabled', 'priority', 'career_source_type',
                              'career_source_identifier', 'career_url', 'source_id', 'notes',
                              'last_scan_at', 'last_scan_status', 'created_at', 'updated_at']
                    for entry in payload['company_watchlist']:
                        conn.execute('INSERT INTO company_watchlist ({0}) VALUES ({1})'.format(
                            ','.join(fields), ','.join('?' for _ in fields)),
                            [entry.get(f) for f in fields])
            conn.commit()
        self._send_json({'ok': True, 'applications': len(apps), 'events': len(events)})


# ---------------------------------------------------------------------------
# Server bootstrap
# ---------------------------------------------------------------------------

def open_browser():
    webbrowser.open('http://{0}:{1}'.format(HOST, PORT))


def create_server(host=None, port=None):
    """Initialise data dir + database and return a bound, ready-to-serve server."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    return ThreadingHTTPServer((host or HOST, PORT if port is None else port), Handler)


def main():
    server = create_server()
    print('Swiss Job Scanner + Bewerbungs-Tracker: http://{0}:{1}'.format(HOST, PORT))
    print('Job-Suche benötigt Internet. Deine Bewerbungsdaten bleiben lokal in data/applications.db.')
    print('Beenden mit Ctrl+C')
    if os.environ.get('APPLICATION_TRACKER_NO_BROWSER') != '1':
        threading.Timer(0.6, open_browser).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nTracker beendet.')
    finally:
        server.server_close()


# Kept for callers that still import these names from app.py.
LocationNormalizer = LocationNormalizer

if __name__ == '__main__':
    main()
