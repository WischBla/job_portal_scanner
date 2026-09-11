"""HTTP level: profile persistence, restart survival and tracker integration."""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from jobscanner import db as jsdb
from jobscanner import pipeline
from jobscanner.repository import JobRepository
from tests.helpers import TempDatabase
from tests.test_pipeline import fake_fetcher

import app as backend


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), backend.Handler)
        self.base = 'http://127.0.0.1:{0}'.format(self.server.server_address[1])
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base + path, data=data, method=method,
                          headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode())
        except HTTPError as exc:
            raise AssertionError('{0} {1} -> HTTP {2}: {3}'.format(
                method, path, exc.code, exc.read().decode())) from exc


class SearchProfileTests(ServerTestCase):
    def test_defaults_are_swiss_and_strict(self):
        profile = self.call('GET', '/api/search-profile')
        self.assertEqual(profile['country_mode'], 'strict')
        self.assertEqual(profile['allowed_countries'], ['Switzerland'])
        self.assertEqual(profile['allowed_locations'], ['Zurich', 'Zug', 'Luzern', 'Bern', 'Basel'])
        self.assertEqual(profile['remote_policy'],
                         {'allow_remote': True, 'allow_hybrid': True, 'allow_onsite': True})
        self.assertEqual(profile['hybrid_max_office_days'], 2)

    def test_save_then_reload_returns_exactly_what_was_saved(self):
        payload = self.call('GET', '/api/search-profile')
        payload.update({
            'allowed_locations': ['Zürich', 'Zug', 'Bern', 'Lucerne', 'Basel'],
            'minimum_match_score': 65,
            'hybrid_max_office_days': 1,
            'remote_policy': {'allow_remote': True, 'allow_hybrid': True, 'allow_onsite': False},
            'seniority_levels': ['Head of', 'Director', 'Principal', 'Lead'],
        })
        saved = self.call('PUT', '/api/search-profile', payload)
        reloaded = self.call('GET', '/api/search-profile')
        for field in ('allowed_locations', 'minimum_match_score', 'hybrid_max_office_days',
                      'remote_policy', 'seniority_levels', 'country_mode'):
            self.assertEqual(saved[field], reloaded[field], field)
        # User spellings are canonicalised once, then stay stable.
        self.assertEqual(reloaded['allowed_locations'], ['Zurich', 'Zug', 'Bern', 'Luzern', 'Basel'])
        self.assertEqual(reloaded['minimum_match_score'], 65)
        self.assertFalse(reloaded['remote_policy']['allow_onsite'])

    def test_profile_survives_a_restart(self):
        payload = self.call('GET', '/api/search-profile')
        payload['allowed_locations'] = ['Zurich']
        payload['minimum_match_score'] = 71
        self.call('PUT', '/api/search-profile', payload)
        # Simulate a restart: close every handle and re-run the migrations.
        jsdb.init_db()
        with jsdb.connect() as conn:
            reloaded = jsdb.load_profile(conn)
        self.assertEqual(reloaded['allowed_locations'], ['Zurich'])
        self.assertEqual(reloaded['minimum_match_score'], 71)

    def test_legacy_alias_still_works(self):
        self.assertEqual(self.call('GET', '/api/scout/profile')['country_mode'], 'strict')

    def test_all_work_models_off_is_refused(self):
        payload = self.call('GET', '/api/search-profile')
        payload['remote_policy'] = {'allow_remote': False, 'allow_hybrid': False, 'allow_onsite': False}
        with self.assertRaises(AssertionError):
            self.call('PUT', '/api/search-profile', payload)


class ScanUsesSavedProfileTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        with jsdb.connect() as conn:
            conn.execute('DELETE FROM job_sources')
            conn.execute('INSERT INTO job_sources (name,source_type,config_json,enabled,created_at,updated_at) '
                         "VALUES ('Fake','rss','{}',1,'','')")
            conn.commit()

    def scan(self):
        return pipeline.run_scan(fetcher=fake_fetcher())

    def test_scan_uses_the_profile_saved_through_the_api(self):
        payload = self.call('GET', '/api/search-profile')
        payload['allowed_locations'] = ['Zurich']
        payload['optional_locations'] = []
        self.call('PUT', '/api/search-profile', payload)
        self.scan()
        jobs = self.call('GET', '/api/scout/jobs')
        cities = {job['normalized_city'] for job in jobs}
        self.assertIn('Zurich', cities)
        self.assertNotIn('Bern', cities)

    def test_rejected_view_explains_every_drop(self):
        self.scan()
        rejected = self.call('GET', '/api/scout/rejected')
        codes = {item['reason_code'] for item in rejected['items']}
        self.assertIn('not_switzerland', codes)
        for item in rejected['items']:
            self.assertTrue(item['reason'], item)
        self.assertTrue(rejected['summary'])

    def test_summary_reports_the_run(self):
        self.scan()
        summary = self.call('GET', '/api/scout/summary')
        self.assertEqual(summary['country_mode'], 'strict')
        self.assertEqual(summary['last_run']['status'], 'ok')
        self.assertGreaterEqual(summary['last_run']['sources_scanned'], 1)


class ApplicationTrackerTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        with jsdb.connect() as conn:
            conn.execute('DELETE FROM job_sources')
            conn.execute('INSERT INTO job_sources (name,source_type,config_json,enabled,created_at,updated_at) '
                         "VALUES ('Fake','rss','{}',1,'','')")
            conn.commit()
        pipeline.run_scan(fetcher=fake_fetcher())

    def test_add_to_applications_copies_the_full_match_context(self):
        job = self.call('GET', '/api/scout/jobs')[0]
        result = self.call('POST', '/api/scout/jobs/{0}/convert'.format(job['id']), {})
        application = result['application']
        self.assertFalse(result['already_exists'])
        self.assertEqual(application['company'], job['company'])
        self.assertEqual(application['position'], job['title'])
        self.assertEqual(application['job_url'], job['job_url'])
        self.assertEqual(application['status'], 'Vorbereitung')  # == "Preparation"
        self.assertIn(job['normalized_city'], application['location'])
        self.assertEqual(application['work_model'], job['work_model'])
        self.assertEqual(application['level'], job['seniority'])
        self.assertIn(str(job['match_score']), application['summary'])
        self.assertIn('Job Scout', application['source'])
        self.assertIn(job['first_seen'], application['notes'])

    def test_converted_job_is_linked_and_not_offered_twice(self):
        job = self.call('GET', '/api/scout/jobs')[0]
        first = self.call('POST', '/api/scout/jobs/{0}/convert'.format(job['id']), {})
        again = self.call('POST', '/api/scout/jobs/{0}/convert'.format(job['id']), {})
        self.assertTrue(again['already_exists'])
        self.assertEqual(again['application']['id'], first['application']['id'])
        with jsdb.connect() as conn:
            stored = JobRepository(conn).get(job['id'])
        self.assertEqual(stored['state'], 'APPLIED')
        self.assertEqual(stored['application_id'], first['application']['id'])

    def test_applied_state_survives_a_rescan(self):
        job = self.call('GET', '/api/scout/jobs')[0]
        self.call('POST', '/api/scout/jobs/{0}/convert'.format(job['id']), {})
        pipeline.run_scan(fetcher=fake_fetcher())
        with jsdb.connect() as conn:
            self.assertEqual(JobRepository(conn).get(job['id'])['state'], 'APPLIED')
        self.assertEqual(len(self.call('GET', '/api/applications')), 1)

    def test_job_state_endpoint_accepts_the_canonical_states(self):
        job = self.call('GET', '/api/scout/jobs')[0]
        for state in ('SAVED', 'IGNORED', 'SEEN'):
            self.assertEqual(self.call('PUT', '/api/scout/jobs/{0}/state'.format(job['id']),
                                       {'state': state})['state'], state)


class MigrationTests(unittest.TestCase):
    def test_existing_application_data_survives_migration(self):
        with TempDatabase():
            with jsdb.connect() as conn:
                conn.execute("INSERT INTO applications (company,position,status,created_at,updated_at) "
                             "VALUES ('Roche','Director Ops','Interview 1','2026-01-01','2026-01-01')")
                conn.commit()
            jsdb.init_db()   # migrations run again, as on every start
            jsdb.init_db()   # and again: they must stay idempotent
            with jsdb.connect() as conn:
                rows = conn.execute('SELECT company, status FROM applications').fetchall()
        self.assertEqual([tuple(r) for r in rows], [('Roche', 'Interview 1')])

    def test_legacy_job_states_are_migrated(self):
        with TempDatabase():
            with jsdb.connect() as conn:
                conn.execute('''INSERT INTO discovered_jobs
                    (source,external_id,title,review_state,state,first_seen,last_seen)
                    VALUES ('Old','1','Head of Cloud','Gemerkt','','x','x')''')
                conn.commit()
            jsdb.init_db()
            with jsdb.connect() as conn:
                state = conn.execute('SELECT state FROM discovered_jobs').fetchone()[0]
        self.assertEqual(state, 'SAVED')


if __name__ == '__main__':
    unittest.main()
