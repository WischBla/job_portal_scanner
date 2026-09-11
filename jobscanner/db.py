"""SQLite access and idempotent schema migrations.

The existing database is never recreated or dropped.  Every migration step is
additive and guarded, so running it repeatedly (or against a fresh file) is
safe and preserves existing application-tracker data.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import profile as profile_mod

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / 'data' / 'applications.db'

# Overridable for tests via set_db_path() or the JOB_TRACKER_DB env variable.
_DB_PATH = Path(os.environ.get('JOB_TRACKER_DB') or DEFAULT_DB_PATH)

SCHEMA_VERSION = 4


def set_db_path(path):
    global _DB_PATH
    _DB_PATH = Path(path)
    return _DB_PATH


def get_db_path():
    return _DB_PATH


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def connect():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def row_to_dict(row):
    return dict(row) if row is not None else None


def _columns(conn, table):
    return {r[1] for r in conn.execute('PRAGMA table_info({0})'.format(table)).fetchall()}


def _table_exists(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


def _add_column(conn, table, column, ddl):
    if column not in _columns(conn, table):
        conn.execute('ALTER TABLE {0} ADD COLUMN {1} {2}'.format(table, column, ddl))


BASE_SCHEMA = '''
CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    position TEXT NOT NULL,
    level TEXT DEFAULT '',
    location TEXT DEFAULT '',
    work_model TEXT DEFAULT '',
    source TEXT DEFAULT '',
    job_url TEXT DEFAULT '',
    applied_date TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Vorbereitung',
    last_response TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    next_action TEXT DEFAULT '',
    follow_up_date TEXT DEFAULT '',
    contact_name TEXT DEFAULT '',
    contact_details TEXT DEFAULT '',
    salary_range TEXT DEFAULT '',
    priority TEXT DEFAULT 'B - Interessant',
    cv_version TEXT DEFAULT '',
    cover_letter TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    person TEXT DEFAULT '',
    note TEXT DEFAULT '',
    next_step TEXT DEFAULT '',
    follow_up_date TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS discovered_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    remote INTEGER,
    job_url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    salary_min REAL,
    salary_max REAL,
    salary_currency TEXT NOT NULL DEFAULT '',
    salary_period TEXT NOT NULL DEFAULT '',
    match_score INTEGER NOT NULL DEFAULT 0,
    match_label TEXT NOT NULL DEFAULT '',
    match_reasons TEXT NOT NULL DEFAULT '[]',
    matched_terms TEXT NOT NULL DEFAULT '[]',
    review_state TEXT NOT NULL DEFAULT 'Neu',
    is_new INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    application_id INTEGER,
    UNIQUE(source, external_id),
    FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS scout_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    fetched_count INTEGER NOT NULL DEFAULT 0,
    matched_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_app_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_app_followup ON applications(follow_up_date);
CREATE INDEX IF NOT EXISTS idx_event_app ON events(application_id);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON discovered_jobs(match_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_seen ON discovered_jobs(first_seen DESC);
'''

# --- migration 002: the canonical search profile ---------------------------
SEARCH_PROFILE_DDL = '''
CREATE TABLE IF NOT EXISTS search_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    country_mode TEXT NOT NULL DEFAULT 'strict',
    allowed_countries TEXT NOT NULL DEFAULT '[]',
    allowed_locations TEXT NOT NULL DEFAULT '[]',
    optional_locations TEXT NOT NULL DEFAULT '[]',
    remote_policy TEXT NOT NULL DEFAULT '{}',
    hybrid_max_office_days INTEGER NOT NULL DEFAULT 2,
    seniority_levels TEXT NOT NULL DEFAULT '[]',
    include_titles TEXT NOT NULL DEFAULT '[]',
    exclude_titles TEXT NOT NULL DEFAULT '[]',
    required_keywords TEXT NOT NULL DEFAULT '[]',
    preferred_keywords TEXT NOT NULL DEFAULT '[]',
    excluded_keywords TEXT NOT NULL DEFAULT '[]',
    minimum_match_score INTEGER NOT NULL DEFAULT 65,
    minimum_salary_chf INTEGER NOT NULL DEFAULT 0,
    allow_missing_salary INTEGER NOT NULL DEFAULT 1,
    language_preferences TEXT NOT NULL DEFAULT '[]',
    sources_enabled TEXT NOT NULL DEFAULT '[]',
    auto_hours INTEGER NOT NULL DEFAULT 12,
    updated_at TEXT NOT NULL
);
'''

# --- migration 003: rejection log for the debug view -----------------------
REJECTED_DDL = '''
CREATE TABLE IF NOT EXISTS rejected_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    source TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    raw_location TEXT NOT NULL DEFAULT '',
    normalized_country TEXT NOT NULL DEFAULT '',
    job_url TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT 'hard_filter',
    reason_code TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    match_score INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rejected_run ON rejected_jobs(run_id);
'''

DEFAULT_SOURCES = [
    ('Arbeitnow', 'arbeitnow', '{}', 1),
    ('Remotive', 'remotive', '{}', 1),
    ('Jobicy', 'jobicy', '{}', 1),
]

# Legacy review_state values -> canonical job states.
LEGACY_STATE_MAP = {
    'Neu': 'NEW',
    'Gesehen': 'SEEN',
    'Gemerkt': 'SAVED',
    'Ignoriert': 'IGNORED',
    'Übernommen': 'APPLIED',
    'Abgelaufen': 'EXPIRED',
}


def migrate(conn):
    """Apply every migration step.  Idempotent and non-destructive."""
    conn.executescript(BASE_SCHEMA)
    conn.executescript(SEARCH_PROFILE_DDL)
    conn.executescript(REJECTED_DDL)

    # -- discovered_jobs: normalized location + explainable scoring ---------
    _add_column(conn, 'discovered_jobs', 'source_type', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'source_key', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'dedupe_key', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'raw_location', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'normalized_country', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'normalized_city', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'normalized_region', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'is_remote', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'discovered_jobs', 'is_hybrid', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'discovered_jobs', 'switzerland_eligible', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'discovered_jobs', 'location_confidence', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'location_reason', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'work_model', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'office_days', 'INTEGER')
    _add_column(conn, 'discovered_jobs', 'seniority', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'match_breakdown', "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, 'discovered_jobs', 'match_concerns', "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, 'discovered_jobs', 'state', "TEXT NOT NULL DEFAULT 'NEW'")
    _add_column(conn, 'discovered_jobs', 'state_changed_at', "TEXT NOT NULL DEFAULT ''")

    # Backfill the canonical state from the legacy German review_state once.
    if 'review_state' in _columns(conn, 'discovered_jobs'):
        for legacy, canonical in LEGACY_STATE_MAP.items():
            conn.execute("UPDATE discovered_jobs SET state=? WHERE state IN ('', 'NEW') AND review_state=?",
                         (canonical, legacy))
    conn.execute("UPDATE discovered_jobs SET state='NEW' WHERE state NOT IN "
                 "('NEW','SEEN','SAVED','IGNORED','APPLIED','EXPIRED')")
    conn.execute("UPDATE discovered_jobs SET raw_location=location WHERE raw_location=''")
    conn.execute("UPDATE discovered_jobs SET source_key = source || ':' || external_id WHERE source_key=''")
    conn.execute("UPDATE discovered_jobs SET dedupe_key = source_key WHERE dedupe_key=''")
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_source_key ON discovered_jobs(source_key)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_state ON discovered_jobs(state)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON discovered_jobs(dedupe_key)')

    # -- scout_runs: richer run statistics ---------------------------------
    _add_column(conn, 'scout_runs', 'sources_scanned', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'scout_runs', 'rejected_count', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'scout_runs', 'profile_snapshot', "TEXT NOT NULL DEFAULT '{}'")

    _seed_sources(conn)
    _seed_profile(conn)
    conn.execute('INSERT OR REPLACE INTO schema_meta (key,value) VALUES (?,?)',
                 ('schema_version', str(SCHEMA_VERSION)))
    conn.commit()


def _seed_sources(conn):
    if conn.execute('SELECT COUNT(*) FROM job_sources').fetchone()[0] == 0:
        ts = now_iso()
        conn.executemany(
            'INSERT INTO job_sources (name,source_type,config_json,enabled,created_at,updated_at) '
            'VALUES (?,?,?,?,?,?)',
            [(n, t, c, e, ts, ts) for n, t, c, e in DEFAULT_SOURCES])


def _seed_profile(conn):
    """Create the canonical profile row, carrying over the legacy profile once."""
    if conn.execute('SELECT id FROM search_profile WHERE id=1').fetchone():
        return
    data = dict(profile_mod.DEFAULT_PROFILE)

    if _table_exists(conn, 'job_search_profile'):
        legacy = conn.execute('SELECT * FROM job_search_profile WHERE id=1').fetchone()
        if legacy:
            legacy = dict(legacy)

            def _json_list(key):
                try:
                    value = json.loads(legacy.get(key) or '[]')
                    return value if isinstance(value, list) else []
                except (TypeError, ValueError):
                    return []

            locations = [loc for loc in _json_list('locations')]
            if locations:
                from .locations import canonical_location_name
                canonical = []
                for loc in locations:
                    name = canonical_location_name(loc)
                    if name and name != 'Switzerland' and name not in canonical:
                        canonical.append(name)
                if canonical:
                    data['allowed_locations'] = canonical
            excludes = _json_list('exclude_keywords')
            if excludes:
                merged = list(data['excluded_keywords'])
                for item in excludes:
                    if item.casefold() not in {x.casefold() for x in merged}:
                        merged.append(item)
                data['excluded_keywords'] = merged
            skills = _json_list('skills')
            if skills:
                merged = list(data['preferred_keywords'])
                for item in skills:
                    if item.casefold() not in {x.casefold() for x in merged}:
                        merged.append(item)
                data['preferred_keywords'] = merged
            roles = _json_list('target_roles')
            if roles:
                merged = list(data['include_titles'])
                for item in roles:
                    if item.casefold() not in {x.casefold() for x in merged}:
                        merged.append(item)
                data['include_titles'] = merged
            try:
                data['minimum_salary_chf'] = int(legacy.get('min_salary_chf') or 0)
            except (TypeError, ValueError):
                pass
            try:
                data['auto_hours'] = int(legacy.get('auto_hours') or 12)
            except (TypeError, ValueError):
                pass
            data['remote_policy'] = {
                'allow_remote': True,
                'allow_hybrid': bool(legacy.get('allow_hybrid', 1)),
                'allow_onsite': True,
            }
            # min_score is intentionally NOT carried over: the old scale mixed a
            # location bonus into the score, so the numbers are not comparable.

    row = profile_mod.to_row(profile_mod.sanitize(data))
    columns = ['id'] + list(row.keys()) + ['updated_at']
    values = [1] + list(row.values()) + [now_iso()]
    conn.execute('INSERT INTO search_profile ({0}) VALUES ({1})'.format(
        ','.join(columns), ','.join('?' for _ in columns)), values)


def init_db():
    conn = connect()
    try:
        migrate(conn)
    finally:
        conn.close()


def load_profile(conn):
    row = conn.execute('SELECT * FROM search_profile WHERE id=1').fetchone()
    if row is None:
        _seed_profile(conn)
        row = conn.execute('SELECT * FROM search_profile WHERE id=1').fetchone()
    return profile_mod.from_row(row_to_dict(row))


def save_profile(conn, payload):
    """Persist a sanitised profile and return exactly what was stored."""
    clean = profile_mod.sanitize(payload)
    row = profile_mod.to_row(clean)
    assignments = ','.join('{0}=?'.format(key) for key in row)
    conn.execute('UPDATE search_profile SET {0}, updated_at=? WHERE id=1'.format(assignments),
                 list(row.values()) + [now_iso()])
    # sources_enabled is part of the profile but job_sources stays the place
    # where sources actually live - keep the two in sync in one direction.
    if clean['sources_enabled']:
        wanted = {name.casefold() for name in clean['sources_enabled']}
        for source in conn.execute('SELECT id,name FROM job_sources').fetchall():
            enabled = 1 if source['name'].casefold() in wanted else 0
            conn.execute('UPDATE job_sources SET enabled=?,updated_at=? WHERE id=?',
                         (enabled, now_iso(), source['id']))
    conn.commit()
    return load_profile(conn)
