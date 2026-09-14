"""SQLite access and idempotent schema migrations.

The existing database is never recreated or dropped.  Every migration step is
additive and guarded, so running it repeatedly (or against a fresh file) is
safe and preserves existing application-tracker data.
"""

import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import presets as presets_mod
from . import profile as profile_mod
from . import schema_v2

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
DEFAULT_DB_PATH = DATA_DIR / 'app.db'
#: V1 database of the original scanner.  It is adopted, never deleted.
LEGACY_DB_PATH = DATA_DIR / 'applications.db'

# Overridable for tests via set_db_path() or the JOB_TRACKER_DB env variable.
_DB_PATH = Path(os.environ.get('JOB_TRACKER_DB') or DEFAULT_DB_PATH)

#: Root of the portable workspace: ``data/`` and ``documents/`` live under it.
#: Everything the user owns is addressed *relative* to this directory and never
#: by an absolute path, so the whole workspace can be archived on one machine
#: and unpacked into a different checkout on another.
_WORKSPACE_ROOT = Path(os.environ.get('JOB_ASSISTANT_WORKSPACE') or BASE_DIR)

SCHEMA_VERSION = 13


def set_db_path(path):
    global _DB_PATH
    _DB_PATH = Path(path)
    return _DB_PATH


def get_db_path():
    return _DB_PATH


def set_workspace_root(path):
    """Point ``documents/`` and every relative document path at another root.

    Used by the workspace import/export round trip and by the tests that prove
    a workspace really is machine independent.
    """
    global _WORKSPACE_ROOT
    _WORKSPACE_ROOT = Path(path).resolve()
    return _WORKSPACE_ROOT


def workspace_root():
    return _WORKSPACE_ROOT


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def adopt_legacy_database():
    """Carry the V1 database over to data/app.db on first V2 start.

    The original file stays exactly where it is - it becomes the untouched
    pre-migration copy of the user's data.
    """
    if _DB_PATH != DEFAULT_DB_PATH or _DB_PATH.exists() or not LEGACY_DB_PATH.exists():
        return False
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(LEGACY_DB_PATH), str(_DB_PATH))
    return True


def backup_dir():
    """Backups always live next to the database that is actually in use."""
    return _DB_PATH.parent / 'backups'


def backup_database(tag='migration', keep=15):
    """Timestamped copy of the live database; returns the path or ''."""
    if not _DB_PATH.exists():
        return ''
    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    target = target_dir / '{0}-{1}-{2}.db'.format(_DB_PATH.stem, tag, stamp)
    shutil.copy2(str(_DB_PATH), str(target))
    # Keep the folder from growing without bound; the newest copies are enough.
    copies = sorted(target_dir.glob('*.db'), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in copies[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass
    return str(target)


def connect():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    adopt_legacy_database()
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
    minimum_match_score INTEGER NOT NULL DEFAULT 65,   -- a marker, never a gate
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

# --- migration 005: built-in presets, stored separately from the active profile ---
PRESETS_DDL = '''
CREATE TABLE IF NOT EXISTS search_presets (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    career_summary TEXT NOT NULL DEFAULT '',
    is_recommended INTEGER NOT NULL DEFAULT 0,
    is_builtin INTEGER NOT NULL DEFAULT 1,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
'''

# --- migration 005: company watchlist ------------------------------------
WATCHLIST_DDL = '''
CREATE TABLE IF NOT EXISTS company_watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    priority TEXT NOT NULL DEFAULT 'B',
    career_source_type TEXT NOT NULL DEFAULT 'manual',
    career_source_identifier TEXT NOT NULL DEFAULT '',
    career_url TEXT NOT NULL DEFAULT '',
    source_id INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    last_scan_at TEXT NOT NULL DEFAULT '',
    last_scan_status TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(source_id) REFERENCES job_sources(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_watchlist_priority ON company_watchlist(priority, company_name);
'''

# --- migration 007: per-source health so a broken integration is never silent ---
SOURCE_HEALTH_COLUMNS = (
    ('source_url', "TEXT NOT NULL DEFAULT ''"),
    ('source_status', "TEXT NOT NULL DEFAULT 'MANUAL'"),
    ('last_checked_at', "TEXT NOT NULL DEFAULT ''"),
    ('last_success_at', "TEXT NOT NULL DEFAULT ''"),
    ('last_error', "TEXT NOT NULL DEFAULT ''"),
    ('job_count_last_scan', 'INTEGER NOT NULL DEFAULT 0'),
    ('last_outcome', "TEXT NOT NULL DEFAULT ''"),
    ('last_http_status', 'INTEGER NOT NULL DEFAULT 0'),
    ('last_retry_count', 'INTEGER NOT NULL DEFAULT 0'),
)
JOB_SOURCE_HEALTH_COLUMNS = (
    ('last_status', "TEXT NOT NULL DEFAULT ''"),
    ('last_checked_at', "TEXT NOT NULL DEFAULT ''"),
    ('last_success_at', "TEXT NOT NULL DEFAULT ''"),
    ('last_error', "TEXT NOT NULL DEFAULT ''"),
    ('last_job_count', 'INTEGER NOT NULL DEFAULT 0'),
    # -- migration 012: what the request actually did ---------------------
    # A source that failed and a source that honestly returned nothing look
    # identical in a status column alone, and only one of them may ever
    # retire a job.  These record enough to tell them apart afterwards.
    ('last_outcome', "TEXT NOT NULL DEFAULT ''"),
    ('last_http_status', 'INTEGER NOT NULL DEFAULT 0'),
    ('last_retry_count', 'INTEGER NOT NULL DEFAULT 0'),
    ('last_attempt_at', "TEXT NOT NULL DEFAULT ''"),
    ('last_authoritative_at', "TEXT NOT NULL DEFAULT ''"),
)

#: Marks a one-shot data migration as done, so it can correct seeded rows once
#: without ever overwriting an edit the user made afterwards.
SEED_MARKER_KEY = 'company_source_seed'
SEED_MARKER_VALUE = '1'

#: Seed list: (company, priority, careers URL).
#: Every company starts as ``manual``; an automated source is attached below
#: only where a real public endpoint was verified against the live service.
WATCHLIST_SEED = [
    ('Google', 'A', 'https://www.google.com/about/careers/applications/jobs/results/?location=Switzerland'),
    ('Microsoft', 'A', 'https://jobs.careers.microsoft.com/global/en/search?lc=Switzerland'),
    ('Amazon Web Services / AWS', 'A', 'https://www.amazon.jobs/en/search?loc_query=Switzerland'),
    ('Meta', 'B', 'https://www.metacareers.com/jobs'),
    ('NVIDIA', 'A', 'https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite'),
    ('IBM', 'B', 'https://www.ibm.com/careers/search'),
    ('Red Hat', 'B', 'https://www.redhat.com/en/jobs'),
    ('UBS', 'A', 'https://jobs.ubs.com/'),
    ('SIX', 'A', 'https://www.six-group.com/en/company/careers.html'),
    ('Swiss Re', 'A', 'https://careers.swissre.com/'),
    ('Zurich Insurance', 'A', 'https://www.careers.zurich.com/'),
    ('Swisscom', 'A', 'https://www.swisscom.ch/en/about/career.html'),
    ('PostFinance', 'B', 'https://www.postfinance.ch/en/about-us/jobs-career.html'),
    ('Roche', 'A', 'https://careers.roche.com/global/en'),
    ('Novartis', 'A', 'https://www.novartis.com/careers'),
    ('ABB', 'A', 'https://careers.abb/'),
    ('Hitachi Energy', 'A', 'https://www.hitachienergy.com/careers'),
    ('Siemens Switzerland', 'B', 'https://jobs.siemens.com/careers?location=Switzerland'),
    ('Zuehlke', 'B', 'https://www.zuehlke.com/en/careers'),
    ('Adnovum', 'B', 'https://www.adnovum.com/en/company/careers'),
    ('Avaloq', 'B', 'https://www.avaloq.com/careers'),
    ('Scandit', 'B', 'https://www.scandit.com/careers/'),
    ('Proton', 'B', 'https://proton.me/careers'),
    # Priority C: Swiss technology employers that were added because a public
    # endpoint could actually be verified, not because they were on the brief.
    ('On', 'C', 'https://www.on.com/en-ch/careers'),
    ('SonarSource', 'C', 'https://www.sonarsource.com/company/careers/'),
    ('ANYbotics', 'C', 'https://www.anybotics.com/careers/'),
    ('Nexthink', 'C', 'https://www.nexthink.com/careers'),
]

#: company -> (source kind, identifier).  Each pair below was confirmed with a
#: live request that returned real, current postings for that company on
#: 2026-09-12.  Nothing is guessed: a token that 404s, answers empty or belongs
#: to a different employer is not listed here, and the company stays MANUAL.
#:
#: Companies deliberately absent: Google, Microsoft, Meta, IBM and NVIDIA
#: publish no stable public feed; UBS and Avaloq answer automated requests with
#: HTTP 403 and must not be worked around; Swisscom, Zuehlke, Red Hat and
#: Hitachi Energy are Workday-only, which the brief excludes as a discovery
#: source; PostFinance, Novartis and Siemens render their result lists in the
#: browser, so there is nothing server-side to read.
VERIFIED_COMPANY_SOURCES = {
    'Amazon Web Services / AWS': ('amazon_jobs', 'CHE'),
    'Roche': ('phenom', 'https://careers.roche.com'),
    'ABB': ('phenom', 'https://careers.abb'),
    'Swiss Re': ('successfactors', 'https://careers.swissre.com'),
    'SIX': ('successfactors', 'https://jobs.six-group.com'),
    'Zurich Insurance': ('successfactors', 'https://www.careers.zurich.com'),
    'Adnovum': ('successfactors', 'https://careers.adnovum.com'),
    'Proton': ('greenhouse', 'proton'),
    'Scandit': ('greenhouse', 'scandit'),
    'On': ('greenhouse', 'onrunning'),
    'SonarSource': ('lever', 'sonarsource'),
    'ANYbotics': ('lever', 'anybotics'),
    'Nexthink': ('smartrecruiters', 'Nexthink'),
}

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
    'Gespeichert': 'SAVED',
    'Ignoriert': 'IGNORED',
    'Übernommen': 'APPLIED',
    'Abgelaufen': 'EXPIRED',
}


def migrate(conn):
    """Apply every migration step.  Idempotent and non-destructive."""
    conn.executescript(BASE_SCHEMA)
    conn.executescript(SEARCH_PROFILE_DDL)
    conn.executescript(REJECTED_DDL)
    conn.executescript(PRESETS_DDL)
    conn.executescript(WATCHLIST_DDL)

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
    _add_column(conn, 'scout_runs', 'geo_passed_count', 'INTEGER NOT NULL DEFAULT 0')

    # -- search_profile: fields introduced with the leadership preset ------
    _add_column(conn, 'search_profile', 'tertiary_locations', "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, 'search_profile', 'secondary_titles', "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, 'search_profile', 'location_filter_mode', "TEXT NOT NULL DEFAULT 'hard'")
    _add_column(conn, 'search_profile', 'salary_mode', "TEXT NOT NULL DEFAULT 'hard'")
    _add_column(conn, 'search_profile', 'salary_target_chf', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'search_profile', 'salary_floor_chf', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'search_profile', 'sort_mode', "TEXT NOT NULL DEFAULT 'score'")
    _add_column(conn, 'search_profile', 'preset_key', "TEXT NOT NULL DEFAULT ''")

    # -- migration 008: down-ranking and commute preferences ----------------
    # Low-relevance domains are a *ranking* signal, not a gate: they cost
    # points and say so, instead of silently removing a posting that happens
    # to mention the wrong word.
    _add_column(conn, 'search_profile', 'deprioritized_keywords', "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, 'search_profile', 'preferred_radius_km', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'search_profile', 'max_commute_minutes', 'INTEGER NOT NULL DEFAULT 0')

    # -- migration 008: personal YES / MAYBE / NO feedback -------------------
    # Recorded for later calibration only.  Nothing here feeds back into the
    # scorer; a verdict is data about the user, not a new filter rule.
    _add_column(conn, 'discovered_jobs', 'feedback', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'feedback_reason', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'feedback_note', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'feedback_at', "TEXT NOT NULL DEFAULT ''")

    # -- company_watchlist / job_sources: source resolution + health --------
    for column, ddl in SOURCE_HEALTH_COLUMNS:
        _add_column(conn, 'company_watchlist', column, ddl)
    for column, ddl in JOB_SOURCE_HEALTH_COLUMNS:
        _add_column(conn, 'job_sources', column, ddl)
    conn.execute("UPDATE company_watchlist SET source_status='MANUAL' "
                 "WHERE source_status NOT IN ('ACTIVE','MANUAL','UNAVAILABLE','ERROR')")

    # -- scout_runs: the scan summary the Jobs screen shows ------------------
    _add_column(conn, 'scout_runs', 'companies_scanned', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'scout_runs', 'swiss_eligible_count', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'scout_runs', 'duplicate_count', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'scout_runs', 'source_failure_count', 'INTEGER NOT NULL DEFAULT 0')

    # -- migration 010: personal-fit calibration ----------------------------
    # The base match score stays exactly what it was and is never overwritten;
    # the two adjustments and the resulting personal fit are stored next to it
    # so the Jobs screen can always show why a technically strong job moved
    # down.  `match_score` now carries the Personal Fit Score, because that is
    # what the list ranks by.
    _add_column(conn, 'discovered_jobs', 'base_score', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'discovered_jobs', 'operating_style_adjustment',
                'REAL NOT NULL DEFAULT 0')
    _add_column(conn, 'discovered_jobs', 'operating_style_class', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'operating_style_detail', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'career_direction_adjustment',
                'REAL NOT NULL DEFAULT 0')
    _add_column(conn, 'discovered_jobs', 'career_direction_class', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'career_direction_detail', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'personal_fit_score', 'INTEGER NOT NULL DEFAULT 0')
    # Jobs stored before this migration have no adjustment yet; until the next
    # rescore their personal fit is simply their base score.
    conn.execute('UPDATE discovered_jobs SET base_score=match_score, '
                 'personal_fit_score=match_score WHERE base_score=0 AND personal_fit_score=0')

    # -- migration 011: manually imported LinkedIn job alerts ---------------
    # Provenance is not the same thing as the canonical source: a job that the
    # company's own board already delivered stays a Greenhouse job and keeps
    # its application URL, it only records that a LinkedIn alert mentioned it
    # too.  ``needs_details`` marks a job whose description is still missing,
    # which is exactly what an alert entry gives you: a title, a company and a
    # link, which is not enough for a trustworthy fit score.
    _add_column(conn, 'discovered_jobs', 'discovered_via', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'linkedin_job_id', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'linkedin_url', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'needs_details', 'INTEGER NOT NULL DEFAULT 0')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_linkedin ON discovered_jobs(linkedin_job_id)')

    # -- migration 012: evidence, enrichment and an honest lifecycle -------
    # Discovery, enrichment, scoring and visibility are four different
    # questions.  These columns are what lets the rest of the code keep them
    # apart: how much is known about a job (``evidence_level``), what should
    # be done about it (``enrichment_state``), how far the Personal Fit Score
    # can be trusted (``fit_confidence`` / ``fit_provisional``), where a
    # description came from (``enrichment_source``) - and, separately from all
    # of that, why a job left the list (``lifecycle_reason``) and how many
    # successful scans have now failed to find it (``missing_scans``).
    #
    # Nothing here is a filter.  A job is never hidden, retired or deleted
    # because of an evidence column; a score has never been a lifecycle event
    # and after this migration it cannot become one.
    _add_column(conn, 'discovered_jobs', 'evidence_level', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'evidence_detail', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'enrichment_state', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'fit_confidence', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'fit_provisional', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'discovered_jobs', 'high_potential', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'discovered_jobs', 'enrichment_source', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'enrichment_detail', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'enrichment_at', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'missing_scans', 'INTEGER NOT NULL DEFAULT 0')
    _add_column(conn, 'discovered_jobs', 'lifecycle_reason', "TEXT NOT NULL DEFAULT ''")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_enrichment '
                 'ON discovered_jobs(enrichment_state)')
    # Rows stored before this migration are re-assessed by the rescore below;
    # until then the honest default is "we do not know", not "ENRICHED".
    conn.execute("UPDATE discovered_jobs SET enrichment_state='NEEDS_ENRICHMENT', "
                 "evidence_level='LOW', fit_confidence='LOW' WHERE enrichment_state=''")

    # -- migration 013: career scope ---------------------------------------
    # Whether a role is realistic for the intended career direction is a fifth
    # question, separate from the lifecycle, the enrichment state, the base
    # score and the personal-fit adjustments - and it is the only one of the
    # five that decides which *view* a job appears in by default.
    #
    # It is still not a lifecycle.  OUT_OF_SCOPE is not EXPIRED: no row is
    # deleted, retired or re-stated here, and a job classified out of scope
    # keeps its score, its state, its feedback and its application link.  One
    # filter chip shows it again.
    _add_column(conn, 'discovered_jobs', 'career_scope', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'career_scope_reason', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'discovered_jobs', 'career_scope_detail', "TEXT NOT NULL DEFAULT ''")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_jobs_career_scope '
                 'ON discovered_jobs(career_scope)')

    _seed_sources(conn)
    _seed_presets(conn)
    _seed_watchlist(conn)
    _attach_verified_company_sources(conn)
    _merge_duplicate_sources(conn)
    _seed_profile(conn)
    schema_v2.migrate_v2(conn, now_iso(), profile=load_profile(conn))

    # -- migration 009: the parts of the person that had nowhere to live ----
    # Career achievements, CliftonStrengths and travel willingness are private
    # matching / interview context.  They are stored, never auto-inserted into
    # a generated document.
    _add_column(conn, 'person_profile', 'secondary_target_roles', "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, 'person_profile', 'travel_willingness', "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, 'person_profile', 'strengths', "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, 'person_profile', 'achievements', "TEXT NOT NULL DEFAULT '[]'")

    _portabilise_document_paths(conn)
    _assess_existing_evidence(conn)
    _classify_career_scope(conn)
    conn.execute('INSERT OR REPLACE INTO schema_meta (key,value) VALUES (?,?)',
                 ('schema_version', str(SCHEMA_VERSION)))
    conn.commit()


CAREER_SCOPE_MARKER_KEY = 'career_scope_classified'


def _classify_career_scope(conn):
    """Give every stored job its career-scope verdict.

    Runs for rows that do not have one yet, which makes it both the one-shot
    migration of an existing database and the repair path for a row written by
    some code path that predates the classifier.  Purely additive: the three
    ``career_scope*`` columns are the only thing written, so a job's state, its
    score, its feedback and its application link cannot be touched from here.
    """
    from . import career_scope

    rows = conn.execute(
        "SELECT id, title, description FROM discovered_jobs WHERE career_scope=''").fetchall()
    for row in rows:
        columns = career_scope.scope_columns({'title': row[1], 'description': row[2]})
        conn.execute('UPDATE discovered_jobs SET career_scope=?, career_scope_reason=?, '
                     'career_scope_detail=? WHERE id=?',
                     (columns['career_scope'], columns['career_scope_reason'],
                      columns['career_scope_detail'], row[0]))
    if rows:
        conn.execute('INSERT OR REPLACE INTO schema_meta (key,value) VALUES (?,?)',
                     (CAREER_SCOPE_MARKER_KEY, now_iso()))
    return len(rows)


EVIDENCE_MARKER_KEY = 'evidence_rescored'


def _assess_existing_evidence(conn):
    """Give every stored job its evidence verdict - exactly once.

    Jobs discovered before migration 012 were scored by a model that treated a
    missing description as a finding rather than as a gap, so their Personal
    Fit Score and their two adjustments were computed under the old rule.  One
    rescore brings them onto the current model; the marker makes it a one-shot
    data migration rather than a cost on every start.

    Additive, like every other rescore path: only the score columns and their
    explanations change.  A job's state, its feedback and its link to an
    application are never touched.
    """
    done = conn.execute('SELECT value FROM schema_meta WHERE key=?',
                        (EVIDENCE_MARKER_KEY,)).fetchone()
    if done:
        return 0
    count = schema_v2.rescore_existing_jobs(conn, load_profile(conn))
    conn.execute('INSERT OR REPLACE INTO schema_meta (key,value) VALUES (?,?)',
                 (EVIDENCE_MARKER_KEY, now_iso()))
    return count


def _seed_sources(conn):
    if conn.execute('SELECT COUNT(*) FROM job_sources').fetchone()[0] == 0:
        ts = now_iso()
        conn.executemany(
            'INSERT INTO job_sources (name,source_type,config_json,enabled,created_at,updated_at) '
            'VALUES (?,?,?,?,?,?)',
            [(n, t, c, e, ts, ts) for n, t, c, e in DEFAULT_SOURCES])


def _seed_presets(conn):
    """Keep the built-in presets in sync with the code.

    Presets are code-owned, so an upsert is safe: it never touches the active
    profile in ``search_profile``, only the catalogue the UI offers.
    """
    ts = now_iso()
    for preset in presets_mod.all_presets():
        conn.execute(
            '''INSERT INTO search_presets (key,name,description,career_summary,is_recommended,
                    is_builtin,payload_json,created_at,updated_at)
               VALUES (?,?,?,?,?,1,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                    name=excluded.name, description=excluded.description,
                    career_summary=excluded.career_summary,
                    is_recommended=excluded.is_recommended,
                    payload_json=excluded.payload_json, updated_at=excluded.updated_at''',
            (preset['key'], preset['name'], preset.get('description') or '',
             preset.get('career_summary') or '', 1 if preset.get('is_recommended') else 0,
             json.dumps(preset['profile'], ensure_ascii=False), ts, ts))


def _seed_watchlist(conn):
    """Seed the company watchlist once; user edits are never overwritten."""
    ts = now_iso()
    for company, priority, url in WATCHLIST_SEED:
        conn.execute(
            '''INSERT OR IGNORE INTO company_watchlist
                 (company_name,enabled,priority,career_source_type,career_source_identifier,
                  career_url,notes,last_scan_at,last_scan_status,source_status,
                  created_at,updated_at)
               VALUES (?,1,?,'manual','',?,'','','','MANUAL',?,?)''',
            (company, priority, url, ts, ts))


def _attach_verified_company_sources(conn):
    """Attach the verified public endpoints - exactly once.

    Run unconditionally this would fight the user: re-attaching a source they
    detached, or resetting a priority they changed.  A marker in ``schema_meta``
    makes it a one-shot data migration instead, so an existing database is
    upgraded on the first start after this release and never touched again.

    Only entries that are still untouched (``manual`` with no identifier) get a
    source, and the seeded priority is only corrected while it still matches
    what an earlier release seeded.
    """
    from .watchlist import CompanyWatchlist   # local: watchlist imports this module

    done = conn.execute('SELECT value FROM schema_meta WHERE key=?',
                        (SEED_MARKER_KEY,)).fetchone()
    if done and str(done[0]) == SEED_MARKER_VALUE:
        return

    seeded_priorities = {name: priority for name, priority, _ in WATCHLIST_SEED}
    watchlist = CompanyWatchlist(conn)
    for entry in watchlist.list():
        name = entry['company_name']
        payload = {}
        wanted_priority = seeded_priorities.get(name)
        if wanted_priority and entry.get('priority') != wanted_priority:
            payload['priority'] = wanted_priority
        source = VERIFIED_COMPANY_SOURCES.get(name)
        if source and entry.get('career_source_type') == 'manual' and \
                not entry.get('career_source_identifier'):
            payload['career_source_type'], payload['career_source_identifier'] = source
        if payload:
            watchlist.update(entry['id'], payload)

    conn.execute('INSERT OR REPLACE INTO schema_meta (key,value) VALUES (?,?)',
                 (SEED_MARKER_KEY, SEED_MARKER_VALUE))


def _merge_duplicate_sources(conn):
    """Fold a hand-made source into the watchlist entry that now owns that board.

    Someone who added "Proton Careers" by hand before the watchlist could do it
    would otherwise end up fetching the same Greenhouse board twice - once as
    their own source and once as ``Watchlist - Proton``.  The older row wins so
    the user's original entry (and its id) is what survives; the redundant one
    is removed and the watchlist points at what is left.

    Safe to run on every start: once there is nothing duplicated, it does
    nothing.
    """
    from .sources import registry

    owned = conn.execute(
        'SELECT w.id AS watch_id, s.id AS source_id, s.source_type, s.config_json '
        'FROM company_watchlist w JOIN job_sources s ON s.id = w.source_id').fetchall()
    orphans = conn.execute(
        'SELECT id, source_type, config_json FROM job_sources '
        'WHERE id NOT IN (SELECT source_id FROM company_watchlist WHERE source_id IS NOT NULL)'
    ).fetchall()
    if not owned or not orphans:
        return

    def identity(source_type, config_json):
        try:
            kind = registry.get_kind(source_type)
        except Exception:       # noqa: BLE001 - an aggregator has no identity field
            return None
        if not kind.identity_field:
            return None
        try:
            config = json.loads(config_json or '{}')
        except (TypeError, ValueError):
            return None
        value = str(config.get(kind.identity_field) or '').strip().rstrip('/').casefold()
        return (source_type, value) if value else None

    by_identity = {}
    for row in orphans:
        key = identity(row['source_type'], row['config_json'])
        if key and key not in by_identity:
            by_identity[key] = row['id']

    merged = []
    for row in owned:
        key = identity(row['source_type'], row['config_json'])
        orphan_id = by_identity.get(key)
        if orphan_id is None or orphan_id == row['source_id']:
            continue
        keep, drop = sorted((orphan_id, row['source_id']))
        conn.execute('UPDATE company_watchlist SET source_id=? WHERE id=?', (keep, row['watch_id']))
        conn.execute('DELETE FROM job_sources WHERE id=?', (drop,))
        by_identity.pop(key, None)
        merged.append(row['watch_id'])
    if not merged:
        return

    # Re-sync so the surviving row reads as the watchlist source it now is.
    from .watchlist import CompanyWatchlist
    watchlist = CompanyWatchlist(conn)
    for watch_id in merged:
        watchlist.update(watch_id, {})


def _portabilise_document_paths(conn):
    """Rewrite any absolute document path as a workspace-relative one.

    Older rows (and anything written before the workspace became portable)
    could hold ``/Users/<someone>/.../documents/cv/x.pdf``.  That path is
    meaningless on another machine, so it is reduced to ``documents/cv/x.pdf``
    here.  The conversion is additive and conservative: a row is only rewritten
    when a ``documents/`` segment can actually be found in it, and the file on
    disk is never touched.
    """
    if not _table_exists(conn, 'documents'):
        return 0
    changed = 0
    for row in conn.execute('SELECT id, path FROM documents').fetchall():
        portable = portable_document_path(row['path'])
        if portable and portable != str(row['path'] or ''):
            conn.execute('UPDATE documents SET path=? WHERE id=?', (portable, row['id']))
            changed += 1
    return changed


def portable_document_path(path):
    """``<anything>/documents/cv/x.pdf`` -> ``documents/cv/x.pdf``.

    Returns '' when the value carries no ``documents/`` segment at all, which
    means it cannot be made portable and is better left exactly as it is.
    """
    text = str(path or '').strip().replace('\\', '/')
    if not text:
        return ''
    if text.startswith('documents/'):
        return text
    parts = text.split('/')
    if 'documents' in parts:
        return '/'.join(parts[parts.index('documents'):])
    return ''


def load_presets(conn):
    """Every stored preset, newest built-ins first, recommended on top."""
    rows = conn.execute('SELECT * FROM search_presets ORDER BY is_recommended DESC, name').fetchall()
    out = []
    for row in rows:
        data = row_to_dict(row)
        try:
            payload = json.loads(data.get('payload_json') or '{}')
        except (TypeError, ValueError):
            payload = {}
        data.pop('payload_json', None)
        data['is_recommended'] = bool(data.get('is_recommended'))
        data['is_builtin'] = bool(data.get('is_builtin'))
        data['profile'] = profile_mod.sanitize(payload)
        data['summary'] = presets_mod.summarize(data['profile'])
        out.append(data)
    return out


def get_preset(conn, key):
    for preset in load_presets(conn):
        if preset['key'] == key:
            return preset
    return None


def apply_preset(conn, key):
    """Copy a preset into the active search profile.

    This is the ONLY path by which a preset reaches the profile the scanner
    uses.  Migrations and application updates never do it implicitly.
    """
    preset = get_preset(conn, key)
    if preset is None:
        raise ValueError('Unknown search preset: {0}'.format(key))
    payload = dict(preset['profile'])
    payload['preset_key'] = preset['key']
    return save_profile(conn, payload)


def _seed_profile(conn):
    """Create the canonical profile row, carrying over the legacy profile once."""
    if conn.execute('SELECT id FROM search_profile WHERE id=1').fetchone():
        return
    # A brand new database starts on the recommended preset - there is nothing
    # to preserve yet.  An existing profile row is never touched here.
    data = presets_mod.recommended_profile()

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
            #
            # Carrying legacy values over means the result is no longer exactly
            # the recommended preset - say so, so the UI offers "restore".
            data['preset_key'] = ''

    row = profile_mod.to_row(profile_mod.sanitize(data))
    columns = ['id'] + list(row.keys()) + ['updated_at']
    values = [1] + list(row.values()) + [now_iso()]
    conn.execute('INSERT INTO search_profile ({0}) VALUES ({1})'.format(
        ','.join(columns), ','.join('?' for _ in columns)), values)


def schema_version(conn):
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    try:
        return int(row[0]) if row else 0
    except (TypeError, ValueError):
        return 0


def init_db(backup=True):
    """Open, back up (only when the schema actually changes) and migrate."""
    existed = _DB_PATH.exists() or LEGACY_DB_PATH.exists()
    conn = connect()
    try:
        current = schema_version(conn) if _table_exists(conn, 'schema_meta') else 0
        if backup and existed and current != SCHEMA_VERSION:
            backup_database('v{0}'.format(current or 'pre'))
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
