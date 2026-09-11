"""End-to-end: the saved profile drives the scan, and states survive rescans."""

import unittest

from jobscanner import db as jsdb
from jobscanner import pipeline
from jobscanner.repository import JobRepository
from tests.helpers import STRONG_DESCRIPTION, TempDatabase, make_profile

RAW_JOBS = [
    {'source': 'Fake', 'source_type': 'fake', 'external_id': 'ch-zurich',
     'company': 'Example AG', 'title': 'Director Platform Engineering',
     'location': 'Zurich, Switzerland', 'job_url': 'https://example.test/ch-zurich',
     'description': STRONG_DESCRIPTION},
    {'source': 'Fake', 'source_type': 'fake', 'external_id': 'ch-bern',
     'company': 'Bern Tech', 'title': 'Head of Engineering Operations',
     'location': 'Bern, Switzerland', 'job_url': 'https://example.test/ch-bern',
     'description': STRONG_DESCRIPTION},
    {'source': 'Fake', 'source_type': 'fake', 'external_id': 'de-munich',
     'company': 'Munich GmbH', 'title': 'Director Platform Engineering',
     'location': 'Munich, Germany', 'job_url': 'https://example.test/de-munich',
     'description': STRONG_DESCRIPTION},
    {'source': 'Fake', 'source_type': 'fake', 'external_id': 'eu-remote',
     'company': 'Euro Corp', 'title': 'Head of Site Reliability Engineering',
     'location': 'Remote Europe', 'job_url': 'https://example.test/eu-remote',
     'description': STRONG_DESCRIPTION},
    {'source': 'Fake', 'source_type': 'fake', 'external_id': 'emea-remote',
     'company': 'EMEA Ltd', 'title': 'Director Cloud Operations',
     'location': 'EMEA', 'job_url': 'https://example.test/emea-remote',
     'description': STRONG_DESCRIPTION},
    {'source': 'Fake', 'source_type': 'fake', 'external_id': 'junior',
     'company': 'Example AG', 'title': 'Junior DevOps Engineer',
     'location': 'Zurich, Switzerland', 'job_url': 'https://example.test/junior',
     'description': STRONG_DESCRIPTION},
]
# Same posting, different portal - must collapse into one row.
DUPLICATE = {'source': 'Other', 'source_type': 'other', 'external_id': 'copy-1',
             'company': 'Example AG', 'title': 'Director Platform Engineering',
             'location': 'Zurich, CH', 'job_url': 'https://example.test/ch-zurich?utm_source=x',
             'description': STRONG_DESCRIPTION}


def fake_fetcher(extra=()):
    payload = RAW_JOBS + list(extra)

    def fetch(source, profile):
        return [dict(job) for job in payload]
    return fetch


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        with jsdb.connect() as conn:
            # One enabled source is enough; the fetcher is injected.
            conn.execute('DELETE FROM job_sources')
            conn.execute('INSERT INTO job_sources (name,source_type,config_json,enabled,created_at,updated_at) '
                         "VALUES ('Fake','rss','{}',1,'','')")
            conn.commit()

    def scan(self, extra=()):
        return pipeline.run_scan(fetcher=fake_fetcher(extra))

    def titles(self):
        with jsdb.connect() as conn:
            return {job['title']: job for job in JobRepository(conn).list_jobs(limit=500)}

    # -- geography ------------------------------------------------------
    def test_strict_mode_keeps_only_swiss_jobs(self):
        result = self.scan()
        found = self.titles()
        self.assertIn('Director Platform Engineering', found)
        self.assertIn('Head of Engineering Operations', found)
        companies = {job['company'] for job in found.values()}
        self.assertNotIn('Munich GmbH', companies)
        self.assertNotIn('Euro Corp', companies)
        self.assertNotIn('EMEA Ltd', companies)
        self.assertGreater(result['rejected_count'], 0)

    def test_rejections_explain_themselves(self):
        self.scan()
        with jsdb.connect() as conn:
            rejections = JobRepository(conn).list_rejections()
        by_company = {r['company']: r for r in rejections}
        self.assertEqual(by_company['Munich GmbH']['reason_code'], 'not_switzerland')
        self.assertIn('Germany', by_company['Munich GmbH']['reason'])
        self.assertEqual(by_company['Euro Corp']['reason_code'], 'not_switzerland')
        self.assertIn('region', by_company['Euro Corp']['reason'].lower())

    # -- the profile actually drives the scan ---------------------------
    def test_changing_the_location_filter_changes_the_next_scan(self):
        self.scan()
        self.assertIn('Head of Engineering Operations', self.titles())

        with jsdb.connect() as conn:
            jsdb.save_profile(conn, dict(make_profile(allowed_locations=['Zurich'],
                                                      optional_locations=[])))
        self.scan()
        found = self.titles()
        self.assertIn('Director Platform Engineering', found)
        self.assertNotIn('Head of Engineering Operations', found)

    def test_changing_the_minimum_score_changes_the_next_scan(self):
        with jsdb.connect() as conn:
            jsdb.save_profile(conn, dict(make_profile(minimum_match_score=99)))
        self.scan()
        self.assertEqual(self.titles(), {})
        with jsdb.connect() as conn:
            jsdb.save_profile(conn, dict(make_profile(minimum_match_score=50)))
        self.scan()
        self.assertTrue(self.titles())

    def test_country_mode_off_admits_foreign_jobs(self):
        with jsdb.connect() as conn:
            jsdb.save_profile(conn, dict(make_profile(country_mode='off')))
        self.scan()
        self.assertIn('Munich GmbH', {job['company'] for job in self.titles().values()})

    # -- deduplication ---------------------------------------------------
    def test_repeated_scans_do_not_duplicate(self):
        self.scan()
        with jsdb.connect() as conn:
            first = conn.execute('SELECT COUNT(*) FROM discovered_jobs').fetchone()[0]
        self.scan()
        self.scan()
        with jsdb.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM discovered_jobs').fetchone()[0], first)

    def test_same_posting_from_two_portals_is_stored_once(self):
        result = self.scan(extra=[DUPLICATE])
        self.assertGreaterEqual(result['duplicate_count'], 1)
        with jsdb.connect() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM discovered_jobs WHERE company='Example AG'").fetchone()[0]
        self.assertEqual(rows, 1)

    # -- job states ------------------------------------------------------
    def test_saved_and_ignored_survive_a_rescan(self):
        self.scan()
        with jsdb.connect() as conn:
            repo = JobRepository(conn)
            jobs = repo.list_jobs(limit=10)
            saved_id, ignored_id = jobs[0]['id'], jobs[1]['id']
            repo.set_state(saved_id, 'SAVED')
            repo.set_state(ignored_id, 'IGNORED')
            conn.commit()
        self.scan()
        with jsdb.connect() as conn:
            states = dict(conn.execute('SELECT id, state FROM discovered_jobs').fetchall())
        self.assertEqual(states[saved_id], 'SAVED')
        self.assertEqual(states[ignored_id], 'IGNORED')

    def test_is_new_only_marks_genuinely_new_finds(self):
        self.scan()
        with jsdb.connect() as conn:
            self.assertTrue(conn.execute('SELECT COUNT(*) FROM discovered_jobs WHERE is_new=1').fetchone()[0])
        second = self.scan()
        self.assertEqual(second['new_count'], 0)
        with jsdb.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM discovered_jobs WHERE is_new=1').fetchone()[0], 0)

    def test_pipeline_order_hard_filter_before_scoring(self):
        """A German job must never be stored, whatever it would have scored."""
        self.scan()
        with jsdb.connect() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM discovered_jobs WHERE normalized_country='Germany'").fetchone()[0]
        self.assertEqual(rows, 0)


if __name__ == '__main__':
    unittest.main()
