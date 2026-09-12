"""Migration safety: existing user data survives the upgrade to V2."""

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from jobscanner import db as jsdb

V1_SCHEMA = '''
CREATE TABLE applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL, position TEXT NOT NULL, level TEXT DEFAULT '',
    location TEXT DEFAULT '', work_model TEXT DEFAULT '', source TEXT DEFAULT '',
    job_url TEXT DEFAULT '', applied_date TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Vorbereitung', last_response TEXT DEFAULT '',
    summary TEXT DEFAULT '', next_action TEXT DEFAULT '', follow_up_date TEXT DEFAULT '',
    contact_name TEXT DEFAULT '', contact_details TEXT DEFAULT '', salary_range TEXT DEFAULT '',
    priority TEXT DEFAULT 'B', cv_version TEXT DEFAULT '', cover_letter TEXT DEFAULT '',
    notes TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, application_id INTEGER NOT NULL,
    event_date TEXT NOT NULL, event_type TEXT NOT NULL, person TEXT DEFAULT '',
    note TEXT DEFAULT '', next_step TEXT DEFAULT '', follow_up_date TEXT DEFAULT '',
    created_at TEXT NOT NULL);
CREATE TABLE discovered_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, external_id TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '', remote INTEGER, job_url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '', excerpt TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '', salary_min REAL, salary_max REAL,
    salary_currency TEXT NOT NULL DEFAULT '', salary_period TEXT NOT NULL DEFAULT '',
    match_score INTEGER NOT NULL DEFAULT 0, match_label TEXT NOT NULL DEFAULT '',
    match_reasons TEXT NOT NULL DEFAULT '[]', matched_terms TEXT NOT NULL DEFAULT '[]',
    review_state TEXT NOT NULL DEFAULT 'Neu', is_new INTEGER NOT NULL DEFAULT 1,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, application_id INTEGER,
    UNIQUE(source, external_id));
CREATE TABLE scout_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'running',
    fetched_count INTEGER NOT NULL DEFAULT 0, matched_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '[]');
CREATE TABLE job_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, source_type TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
'''


class MigrationTests(unittest.TestCase):
    """A realistic V1 file is upgraded in place, additively."""

    def setUp(self):
        self.previous = jsdb.get_db_path()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(jsdb.set_db_path, self.previous)
        self.path = Path(self.dir.name) / 'app.db'
        conn = sqlite3.connect(str(self.path))
        conn.executescript(V1_SCHEMA)
        conn.execute("INSERT INTO applications (company, position, status, notes, "
                     "created_at, updated_at) VALUES "
                     "('Roche','Head of Cloud','Beworben','keep me','2026-01-01','2026-01-01')")
        conn.execute("INSERT INTO events (application_id, event_date, event_type, note, created_at) "
                     "VALUES (1,'2026-01-02','Bewerbung','sent','2026-01-02')")
        conn.execute("INSERT INTO discovered_jobs (source, external_id, company, title, "
                     "match_score, match_label, review_state, first_seen, last_seen) VALUES "
                     "('Jobicy','42','Proton','Head of SRE',84,'Starker Match','Gespeichert','a','a')")
        conn.commit()
        conn.close()
        jsdb.set_db_path(self.path)

    def migrate(self):
        jsdb.init_db(backup=False)

    def test_existing_rows_survive(self):
        self.migrate()
        with jsdb.connect() as conn:
            application = dict(conn.execute('SELECT * FROM applications').fetchone())
            self.assertEqual(application['company'], 'Roche')
            self.assertEqual(application['notes'].split('\n')[0], 'keep me')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM discovered_jobs').fetchone()[0], 1)

    def test_german_statuses_become_the_canonical_pipeline(self):
        self.migrate()
        with jsdb.connect() as conn:
            self.assertEqual(conn.execute('SELECT status FROM applications').fetchone()[0], 'Applied')

    def test_legacy_review_states_and_labels_are_translated(self):
        self.migrate()
        with jsdb.connect() as conn:
            job = dict(conn.execute('SELECT * FROM discovered_jobs').fetchone())
            self.assertEqual(job['state'], 'SAVED')
            self.assertIn(job['match_label'], ('Excellent', 'Strong', 'Review'))

    def test_jobs_scored_by_v1_are_re_explained_in_the_current_vocabulary(self):
        with jsdb.connect() as conn:
            conn.execute("UPDATE discovered_jobs SET match_reasons=?, description=?",
                         ('["Ziel-Seniorit\u00e4t: Head / Director"]',
                          'You lead the platform engineering group, own AWS cloud '
                          'infrastructure, SRE practice and drive transformation.'))
            conn.commit()
        self.migrate()
        with jsdb.connect() as conn:
            job = dict(conn.execute('SELECT * FROM discovered_jobs').fetchone())
            self.assertNotIn('Ziel-Seniorit', job['match_reasons'])
            self.assertTrue(job['match_breakdown'] != '[]')
            # The job itself, its state and its link are untouched.
            self.assertEqual(job['state'], 'SAVED')
            self.assertEqual(job['company'], 'Proton')

    def test_v2_tables_are_created_and_seeded(self):
        self.migrate()
        with jsdb.connect() as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('person_profile', 'documents', 'app_settings', 'ai_analysis',
                          'compensation_estimates', 'salary_benchmarks', 'apply_sessions',
                          'application_documents'):
                self.assertIn(table, tables)
            self.assertEqual(conn.execute(
                'SELECT first_name FROM person_profile WHERE id=1').fetchone()[0], 'Sebastian')
            self.assertGreater(
                conn.execute('SELECT COUNT(*) FROM salary_benchmarks').fetchone()[0], 0)

    def test_migration_is_idempotent(self):
        self.migrate()
        self.migrate()
        self.migrate()
        with jsdb.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM applications').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM person_profile').fetchone()[0], 1)

    def test_user_edits_to_the_person_profile_are_not_overwritten(self):
        self.migrate()
        with jsdb.connect() as conn:
            conn.execute("UPDATE person_profile SET phone='+41 79 000 00 00' WHERE id=1")
            conn.commit()
        self.migrate()
        with jsdb.connect() as conn:
            self.assertEqual(conn.execute('SELECT phone FROM person_profile').fetchone()[0],
                             '+41 79 000 00 00')

    def test_a_backup_is_written_next_to_the_database_before_a_schema_change(self):
        jsdb.init_db(backup=True)
        folder = Path(self.dir.name) / 'backups'
        self.assertTrue(list(folder.glob('*.db')), 'no backup was written')


class LegacyAdoptionTests(unittest.TestCase):
    """The original applications.db is copied to app.db, never moved or deleted."""

    def setUp(self):
        self.previous_path = jsdb.get_db_path()
        self.previous_default = jsdb.DEFAULT_DB_PATH
        self.previous_legacy = jsdb.LEGACY_DB_PATH
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(self._restore)
        root = Path(self.dir.name)
        jsdb.DEFAULT_DB_PATH = root / 'app.db'
        jsdb.LEGACY_DB_PATH = root / 'applications.db'
        conn = sqlite3.connect(str(jsdb.LEGACY_DB_PATH))
        conn.executescript(V1_SCHEMA)
        conn.execute("INSERT INTO applications (company, position, created_at, updated_at) "
                     "VALUES ('Legacy AG','Head of Ops','2026-01-01','2026-01-01')")
        conn.commit()
        conn.close()
        jsdb.set_db_path(jsdb.DEFAULT_DB_PATH)

    def _restore(self):
        jsdb.DEFAULT_DB_PATH = self.previous_default
        jsdb.LEGACY_DB_PATH = self.previous_legacy
        jsdb.set_db_path(self.previous_path)

    def test_the_v1_database_is_adopted_and_left_in_place(self):
        jsdb.init_db(backup=False)
        self.assertTrue(jsdb.LEGACY_DB_PATH.exists(), 'the original database was removed')
        with jsdb.connect() as conn:
            self.assertEqual(conn.execute('SELECT company FROM applications').fetchone()[0],
                             'Legacy AG')

    def test_adoption_only_happens_once(self):
        jsdb.init_db(backup=False)
        with jsdb.connect() as conn:
            conn.execute("INSERT INTO applications (company, position, created_at, updated_at) "
                         "VALUES ('New AG','Director','2026-02-01','2026-02-01')")
            conn.commit()
        jsdb.init_db(backup=False)
        with jsdb.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM applications').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
