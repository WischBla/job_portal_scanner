"""HTTP level: the four screens, persistence across restarts, and data safety."""

import io
import unittest

from fastapi.testclient import TestClient

import app as backend
from jobscanner import db as jsdb
from jobscanner import documents as documents_mod
from jobscanner.repository import JobRepository
from tests.helpers import STRONG_DESCRIPTION, TempDatabase, make_job
from tests.test_pipeline import fake_fetcher


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.client = TestClient(backend.app)

    def seed_job(self, **overrides):
        job = make_job(title=overrides.pop('title', 'Head of Technology Operations'),
                       company=overrides.pop('company', 'Example AG'),
                       location=overrides.pop('location', 'Zurich, Switzerland'),
                       description=overrides.pop('description', STRONG_DESCRIPTION),
                       external_id=overrides.pop('external_id', '1'))
        scored = {'score': overrides.pop('score', 84), 'label': 'Excellent match',
                  'reasons': ['Head of scope', 'Platform engineering in title',
                              'Zurich', 'Leadership scope', 'AI relevance', 'Sixth reason'],
                  'concerns': ['Office presence unclear', 'Salary not published',
                               'Third concern', 'Fourth concern'],
                  'terms': [], 'breakdown': []}
        with jsdb.connect() as conn:
            job_id, _ = JobRepository(conn).upsert(job, scored)
            conn.commit()
        return job_id


class JobsScreenTests(ApiTestCase):
    def test_jobs_is_served_without_any_search_parameters(self):
        self.seed_job()
        response = self.client.get('/api/jobs')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['jobs'])

    def test_every_card_carries_score_reasons_concerns_and_salary(self):
        self.seed_job()
        card = self.client.get('/api/jobs').json()['jobs'][0]
        self.assertGreater(card['score'], 0)
        self.assertIn(card['classification'], ('Excellent', 'Strong', 'Review'))
        self.assertTrue(card['reasons'])
        self.assertTrue(card['concerns'])
        self.assertTrue(card['compensation']['display'].startswith('CHF'))
        self.assertIn(card['compensation']['confidence'], ('Low', 'Medium', 'High'))

    def test_reasons_and_concerns_are_capped(self):
        self.seed_job()
        card = self.client.get('/api/jobs').json()['jobs'][0]
        self.assertLessEqual(len(card['reasons']), 5)
        self.assertLessEqual(len(card['concerns']), 3)

    def test_jobs_are_sorted_by_score_then_recency(self):
        self.seed_job(external_id='1', score=70, title='Head of Cloud Operations')
        self.seed_job(external_id='2', score=88, title='Director Platform Engineering')
        self.seed_job(external_id='3', score=79, title='Head of SRE')
        scores = [job['score'] for job in self.client.get('/api/jobs').json()['jobs']]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_a_job_can_be_saved_and_ignored(self):
        job_id = self.seed_job()
        self.assertEqual(self.client.post('/api/jobs/{0}/state'.format(job_id),
                                          json={'state': 'SAVED'}).json()['state'], 'SAVED')
        self.client.post('/api/jobs/{0}/state'.format(job_id), json={'state': 'IGNORED'})
        self.assertEqual(self.client.get('/api/jobs').json()['jobs'], [])

    def test_unknown_states_are_refused(self):
        job_id = self.seed_job()
        self.assertEqual(self.client.post('/api/jobs/{0}/state'.format(job_id),
                                          json={'state': 'NONSENSE'}).status_code, 400)

    def test_analysis_works_without_ai_and_is_cached(self):
        job_id = self.seed_job()
        first = self.client.post('/api/jobs/{0}/analyse'.format(job_id), json={'force': False})
        self.assertEqual(first.status_code, 200)
        analysis = first.json()['analysis']
        self.assertEqual(analysis['_source'], 'template')
        self.assertTrue(analysis['fit_summary'])
        self.assertTrue(analysis['application_angle'])

    def test_scan_uses_the_stored_profile_and_returns_only_swiss_roles(self):
        from jobscanner import pipeline
        with jsdb.connect() as conn:
            conn.execute('DELETE FROM job_sources')
            conn.execute('INSERT INTO job_sources (name,source_type,config_json,enabled,'
                         "created_at,updated_at) VALUES ('Fake','rss','{}',1,'','')")
            conn.commit()
            result = pipeline.run_scan(conn, fake_fetcher())
        self.assertGreater(result['matched_count'], 0)
        self.assertGreater(result['rejected_count'], 0)
        jobs = self.client.get('/api/jobs').json()['jobs']
        self.assertTrue(jobs)
        titles = [job['title'] for job in jobs]
        self.assertNotIn('Junior DevOps Engineer', titles)
        for job in jobs:
            self.assertNotIn(job['country'], ('Germany', 'Austria', 'France'))
            self.assertTrue(job['compensation']['display'])


class ApplicationsScreenTests(ApiTestCase):
    def test_pipeline_has_the_eight_canonical_stages(self):
        data = self.client.get('/api/applications').json()
        self.assertEqual([stage['status'] for stage in data['board']],
                         ['Preparation', 'Applied', 'Screening', 'Interview',
                          'Final', 'Offer', 'Rejected', 'Withdrawn'])

    def test_a_job_becomes_an_application_with_its_match_analysis(self):
        job_id = self.seed_job()
        application = self.client.post('/api/jobs/{0}/application'.format(job_id)).json()
        self.assertEqual(application['company'], 'Example AG')
        self.assertEqual(application['status'], 'Preparation')
        self.assertIn('Match', application['match_summary'])
        self.assertTrue(application['salary_estimate'])
        self.assertEqual(self.client.get('/api/jobs/{0}'.format(job_id)).json()['state'], 'APPLIED')

    def test_converting_the_same_job_twice_reuses_the_application(self):
        job_id = self.seed_job()
        first = self.client.post('/api/jobs/{0}/application'.format(job_id)).json()
        second = self.client.post('/api/jobs/{0}/application'.format(job_id)).json()
        self.assertEqual(first['id'], second['id'])

    def test_status_changes_are_written_to_the_timeline(self):
        created = self.client.post('/api/applications',
                                   json={'company': 'Acme', 'position': 'Head of Cloud'}).json()
        self.client.put('/api/applications/{0}'.format(created['id']),
                        json={'status': 'Interview'})
        detail = self.client.get('/api/applications/{0}'.format(created['id'])).json()
        self.assertEqual(detail['status'], 'Interview')
        self.assertTrue(any('Interview' in event['note'] for event in detail['events']))

    def test_timeline_entries_update_the_next_action(self):
        created = self.client.post('/api/applications',
                                   json={'company': 'Acme', 'position': 'Head of SRE'}).json()
        self.client.post('/api/applications/{0}/events'.format(created['id']),
                         json={'event_type': 'Screening call', 'note': 'Call with the hiring manager',
                               'next_step': 'Send references', 'follow_up_date': '2026-10-01'})
        detail = self.client.get('/api/applications/{0}'.format(created['id'])).json()
        self.assertEqual(detail['next_action'], 'Send references')
        self.assertEqual(detail['follow_up_date'], '2026-10-01')
        self.assertEqual(detail['events'][0]['event_type'], 'Screening call')

    def test_company_and_position_are_required(self):
        self.assertEqual(self.client.post('/api/applications', json={'company': ''}).status_code, 400)


class ProfileScreenTests(ApiTestCase):
    def test_the_profile_is_seeded_for_sebastian(self):
        person = self.client.get('/api/profile').json()['person']
        self.assertEqual(person['first_name'], 'Sebastian')
        self.assertEqual(person['last_name'], 'Bierwisch')
        self.assertTrue(person['linkedin_url'])
        self.assertTrue(person['work_authorization'])
        self.assertEqual(person['comp_minimum_chf'], 235000)
        self.assertTrue(person['target_roles'])

    def test_the_profile_survives_a_restart(self):
        self.client.put('/api/profile', json={'phone': '+41 79 123 45 67',
                                              'technical_skills': ['Kubernetes', 'AWS']})
        with TestClient(backend.app) as fresh:
            person = fresh.get('/api/profile').json()['person']
        self.assertEqual(person['phone'], '+41 79 123 45 67')
        self.assertEqual(person['technical_skills'], ['Kubernetes', 'AWS'])

    def test_documents_are_stored_on_disk_and_referenced_in_sqlite(self):
        response = self.client.post(
            '/api/profile/documents',
            data={'kind': 'cv_en'},
            files={'file': ('cv.pdf', io.BytesIO(b'%PDF-1.4 test'), 'application/pdf')})
        self.assertEqual(response.status_code, 200)
        document = response.json()['document']
        self.addCleanup(documents_mod.delete, document['id'])
        self.assertTrue(document['exists'])
        self.assertTrue(document['path'].startswith('documents/'))
        with jsdb.connect() as conn:
            columns = {row[1] for row in conn.execute('PRAGMA table_info(documents)')}
        self.assertNotIn('content', columns)
        self.assertNotIn('blob', columns)
        self.assertIn('path', columns)

    def test_unsupported_file_types_are_refused(self):
        response = self.client.post(
            '/api/profile/documents',
            data={'kind': 'cv_en'},
            files={'file': ('payload.exe', io.BytesIO(b'MZ'), 'application/octet-stream')})
        self.assertEqual(response.status_code, 400)


class ConfigScreenTests(ApiTestCase):
    def test_config_exposes_every_required_section(self):
        data = self.client.get('/api/config').json()
        for key in ('sources', 'watchlist', 'matching', 'ai', 'benchmarks', 'settings', 'system'):
            self.assertIn(key, data)

    def test_ai_is_off_by_default_and_never_returns_a_key(self):
        data = self.client.get('/api/config').json()
        self.assertFalse(data['settings']['ai_enabled'])
        self.assertEqual(data['settings']['ai_provider'], 'none')
        self.assertNotIn('api_key', str(data['settings']))
        self.assertIn('has_api_key', data['ai'])

    def test_settings_round_trip(self):
        self.client.put('/api/config/settings', json={'salary_market_uplift_pct': 5,
                                                      'apply_upload_motivation': True})
        settings = self.client.get('/api/config').json()['settings']
        self.assertEqual(settings['salary_market_uplift_pct'], 5)
        self.assertTrue(settings['apply_upload_motivation'])

    def test_the_never_submit_guarantee_cannot_be_switched_off(self):
        self.client.put('/api/config/settings', json={'apply_never_submit': False})
        self.assertTrue(self.client.get('/api/config').json()['settings']['apply_never_submit'])

    def test_matching_profile_is_swiss_and_leadership_focused_out_of_the_box(self):
        matching = self.client.get('/api/config').json()['matching']
        self.assertEqual(matching['country_mode'], 'strict')
        self.assertEqual(matching['allowed_countries'], ['Switzerland'])
        for city in ('Zurich', 'Zug', 'Bern', 'Basel'):
            self.assertIn(city, matching['allowed_locations'])
        for level in ('Head of', 'Director', 'Principal'):
            self.assertIn(level, matching['seniority_levels'])

    def test_apply_status_reports_the_never_submit_guarantee(self):
        status = self.client.get('/api/apply/status').json()
        self.assertTrue(status['never_submits'])


class ShellTests(ApiTestCase):
    def test_health_reports_the_database_in_use(self):
        data = self.client.get('/api/health').json()
        self.assertEqual(data['status'], 'ok')
        self.assertTrue(data['database'])

    def test_the_single_page_app_is_served(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        for label in ('Jobs', 'Applications', 'Profile', 'Config', 'Scan for new jobs'):
            self.assertIn(label, response.text)

    def test_the_jobs_screen_has_no_search_or_filter_form(self):
        html = self.client.get('/').text
        self.assertNotIn('<form', html)
        self.assertNotIn('Filter', html)


if __name__ == '__main__':
    unittest.main()
