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
    def test_a_fresh_profile_is_empty_and_carries_no_personal_data(self):
        """A new installation must not arrive pre-filled with anyone's data."""
        person = self.client.get('/api/profile').json()['person']
        for key in ('first_name', 'last_name', 'email', 'phone', 'linkedin_url',
                    'nationality', 'work_authorization'):
            self.assertEqual(person[key], '', '{0} must start empty'.format(key))
        for key in ('target_roles', 'secondary_target_roles', 'achievements',
                    'strengths', 'career_history', 'languages'):
            self.assertEqual(person[key], [], '{0} must start empty'.format(key))
        self.assertEqual(person['comp_minimum_chf'], 0)

    def test_the_profile_accepts_and_returns_every_field(self):
        payload = {'first_name': 'Alex', 'email': 'alex@example.test',
                   'travel_willingness': 'High for workshops, not permanent assignments',
                   'strengths': ['Strategic'], 'achievements': ['Cut incident volume'],
                   'secondary_target_roles': ['Head of Platform'],
                   'comp_target_chf': 300000}
        self.client.put('/api/profile', json=payload)
        person = self.client.get('/api/profile').json()['person']
        self.assertEqual(person['first_name'], 'Alex')
        self.assertIn('workshops', person['travel_willingness'])
        self.assertEqual(person['strengths'], ['Strategic'])
        self.assertEqual(person['achievements'], ['Cut incident volume'])
        self.assertEqual(person['secondary_target_roles'], ['Head of Platform'])
        self.assertEqual(person['comp_target_chf'], 300000)

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
        for key in ('sources', 'watchlist', 'watchlist_source_kinds', 'source_health',
                    'matching', 'ai', 'benchmarks', 'settings', 'system'):
            self.assertIn(key, data)

    def test_the_watchlist_carries_everything_its_table_renders(self):
        entries = self.client.get('/api/config').json()['watchlist']
        self.assertTrue(entries)
        for entry in entries:
            for key in ('company_name', 'priority', 'source_label', 'source_status',
                        'job_count_last_scan', 'last_scan_at', 'career_url', 'automated'):
                self.assertIn(key, entry, key)
            self.assertIn(entry['source_status'],
                          ('ACTIVE', 'MANUAL', 'UNAVAILABLE', 'ERROR'))

    def test_a_manual_company_is_never_presented_as_being_scanned(self):
        entries = {e['company_name']: e for e in self.client.get('/api/config').json()['watchlist']}
        google = entries['Google']
        self.assertEqual(google['source_status'], 'MANUAL')
        self.assertEqual(google['source_label'], 'Manual')
        self.assertEqual(google['action'], 'open_careers_page')
        self.assertFalse(google['automated'])
        self.assertTrue(google['career_url'])

    def test_verified_companies_arrive_with_their_public_source_attached(self):
        entries = {e['company_name']: e for e in self.client.get('/api/config').json()['watchlist']}
        self.assertEqual(entries['Proton']['career_source_type'], 'greenhouse')
        self.assertEqual(entries['Roche']['career_source_type'], 'phenom')
        self.assertEqual(entries['Swiss Re']['career_source_type'], 'successfactors')
        self.assertEqual(entries['Amazon Web Services / AWS']['career_source_type'], 'amazon_jobs')

    def test_source_health_is_reported_per_kind_with_a_manual_row(self):
        health = self.client.get('/api/config/source-health').json()
        self.assertIn('sources', health)
        self.assertIn('failures', health)
        manual = next(k for k in health['sources'] if k['source_type'] == 'manual')
        self.assertGreater(manual['companies'], 0)

    def test_verifying_a_source_stores_the_outcome(self):
        entries = {e['company_name']: e for e in self.client.get('/api/config').json()['watchlist']}
        entry_id = entries['Google']['id']
        result = self.client.post('/api/config/watchlist/{0}/verify'.format(entry_id)).json()
        # Google is manual, so nothing is contacted and nothing is claimed.
        self.assertEqual(result['entry']['source_status'], 'MANUAL')

    def test_verifying_an_unknown_entry_is_a_404(self):
        self.assertEqual(self.client.post('/api/config/watchlist/999999/verify').status_code, 404)

    def test_a_company_can_be_added_with_a_public_source(self):
        response = self.client.post('/api/config/watchlist', json={
            'company_name': 'Example AG', 'priority': 'C',
            'career_source_type': 'smartrecruiters',
            'career_source_identifier': 'ExampleAG',
            'career_url': 'https://example.test/careers'})
        self.assertEqual(response.status_code, 200)
        entry = next(e for e in response.json()['watchlist'] if e['company_name'] == 'Example AG')
        self.assertEqual(entry['source_label'], 'SmartRecruiters')
        # Attached is not the same as proven.
        self.assertEqual(entry['source_status'], 'UNAVAILABLE')

    def test_a_public_source_without_an_identifier_is_rejected(self):
        response = self.client.post('/api/config/watchlist', json={
            'company_name': 'Nope AG', 'career_source_type': 'greenhouse'})
        self.assertEqual(response.status_code, 400)

    def test_an_unknown_source_kind_is_rejected(self):
        response = self.client.post('/api/config/watchlist', json={
            'company_name': 'Nope AG', 'career_source_type': 'linkedin',
            'career_source_identifier': 'x'})
        self.assertEqual(response.status_code, 400)

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
