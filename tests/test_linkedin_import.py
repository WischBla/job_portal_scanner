"""Manual LinkedIn job-alert import: parser, privacy, dedupe, scoring.

The fixtures below are synthetic.  They reproduce the *structure* of a real
LinkedIn job alert - German wording, one block per job, the ``/comm/`` link
with its tracking query, the separator rules and the footer - without carrying
anyone's actual mail.
"""

import unittest
from email.message import EmailMessage

from fastapi.testclient import TestClient

import app as backend
from jobscanner import alert_import, db as jsdb, linkedin_alert
from jobscanner.repository import JobRepository
from tests.helpers import STRONG_DESCRIPTION, TempDatabase, make_job

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
TRACKED_URL = (
    'https://www.linkedin.com/comm/jobs/view/4428824971'
    '?refId=Abc123%3D%3D&trackingId=Zz9%3D&trk=eml-email_job_alert_digest_01-job_card-0-jobcard'
    '&trkEmail=eml-email_job_alert_digest_01-job_card-0-jobcard-null-9xy8z~mfabcd~ef'
    '&midToken=AQHxYv&midSig=1xAbCd&eid=9xy8z-mfabcd-ef&otpToken=MTMwODE4'
    '&lipi=urn%3Ali%3Apage%3Aemail_email_job_alert_digest_01'
    '&savedSearchId=1234567890&savedSearchAuthToken=AQEBCDEF'
)

ONE_JOB = '''Subject: Sebastian: Ihre Jobbenachrichtigung für "Head of Engineering"
Sent: Freitag, 12. September 2025 07:03
From: LinkedIn Jobbenachrichtigungen <jobs-noreply@linkedin.com>
To: sebastian.private@example.com

Medical Device Technical Lead
Roche
Basel, Basel, Schweiz

Dieses Unternehmen ist aktiv auf Personalsuche.
Jobangebot ansehen: https://www.linkedin.com/comm/jobs/view/4466403318?trk=eml&midToken=AQH

---------------------------------------------------------

Diese E-Mail wurde an sebastian.private@example.com gesendet.
Abmelden: https://www.linkedin.com/comm/psettings/email-unsubscribe
© 2025 LinkedIn Corporation, 1000 West Maude Avenue, Sunnyvale, CA 94085.
'''

MANY_JOBS = '''Subject: Sebastian: Ihre Jobbenachrichtigung
Sent: Freitag, 12. September 2025 07:03
From: LinkedIn Jobbenachrichtigungen <jobs-noreply@linkedin.com>
To: sebastian.private@example.com

Medical Device Technical Lead
Roche
Basel, Basel, Schweiz

Dieses Unternehmen ist aktiv auf Personalsuche.
Jobangebot ansehen: https://www.linkedin.com/comm/jobs/view/4466403318?trk=eml

---------------------------------------------------------

Site Reliability Engineer - Observability
Proton
Genf, Genf, Schweiz

Dieses Unternehmen ist aktiv auf Personalsuche.
Jobangebot ansehen: {tracked}

---------------------------------------------------------

Senior Site Reliability Engineer, DGX Cloud
NVIDIA
Zürich, Zürich, Schweiz

Jobangebot ansehen: https://www.linkedin.com/comm/jobs/view/4401122334?trk=eml

---------------------------------------------------------

Head of Engineering (m/w/d)
Rheinmetall
Zürich, Zürich, Schweiz

Vor 2 Tagen
Jobangebot ansehen: https://www.linkedin.com/comm/jobs/view/4409988776?trk=eml

---------------------------------------------------------

Diese E-Mail wurde an sebastian.private@example.com gesendet.
Ihr Profil: Senior Technology Leader | SRE | Platform Engineering
Abmelden: https://www.linkedin.com/comm/psettings/email-unsubscribe
'''.format(tracked=TRACKED_URL)

ENGLISH_JOBS = '''Subject: Sebastian, your job alert for "Head of Engineering"
From: LinkedIn Job Alerts <jobs-noreply@linkedin.com>
To: sebastian.private@example.com

Head of Platform Engineering
Swisscom
Bern, Bern, Switzerland

This company is actively recruiting.
View job: https://www.linkedin.com/comm/jobs/view/4500000001?trk=eml

---------------------------------------------------------

This email was intended for Sebastian.
Unsubscribe: https://www.linkedin.com/comm/psettings/email-unsubscribe
'''

NO_SEPARATORS = '''Medical Device Technical Lead
Roche
Basel, Basel, Schweiz
Jobangebot ansehen: https://www.linkedin.com/comm/jobs/view/4466403318?trk=eml
Site Reliability Engineer - Observability
Proton
Genf, Genf, Schweiz
Jobangebot ansehen: https://www.linkedin.com/comm/jobs/view/4428824971?trk=eml
'''


def eml_bytes(text):
    """The same alert, saved as .eml instead of .txt."""
    message = EmailMessage()
    message['Subject'] = 'Sebastian: Ihre Jobbenachrichtigung'
    message['From'] = 'LinkedIn Jobbenachrichtigungen <jobs-noreply@linkedin.com>'
    message['To'] = 'sebastian.private@example.com'
    message.set_content(text)
    return message.as_bytes()


# --------------------------------------------------------------------------
# parser
# --------------------------------------------------------------------------
class ParserTests(unittest.TestCase):
    def test_single_job_alert(self):
        jobs = linkedin_alert.parse(ONE_JOB)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['title'], 'Medical Device Technical Lead')
        self.assertEqual(jobs[0]['company'], 'Roche')
        self.assertEqual(jobs[0]['location'], 'Basel, Basel, Schweiz')
        self.assertEqual(jobs[0]['source'], 'linkedin')

    def test_several_jobs_in_one_alert(self):
        jobs = linkedin_alert.parse(MANY_JOBS)
        self.assertEqual([job['company'] for job in jobs],
                         ['Roche', 'Proton', 'NVIDIA', 'Rheinmetall'])
        self.assertEqual(jobs[3]['title'], 'Head of Engineering (m/w/d)')

    def test_english_alert_wording(self):
        jobs = linkedin_alert.parse(ENGLISH_JOBS)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['title'], 'Head of Platform Engineering')
        self.assertEqual(jobs[0]['company'], 'Swisscom')

    def test_informational_line_is_a_snippet_not_the_location(self):
        jobs = linkedin_alert.parse(MANY_JOBS)
        roche = jobs[0]
        self.assertEqual(roche['location'], 'Basel, Basel, Schweiz')
        self.assertIn('Personalsuche', roche['snippet'])
        self.assertEqual(jobs[3]['location'], 'Zürich, Zürich, Schweiz')

    def test_job_id_is_extracted(self):
        jobs = linkedin_alert.parse(MANY_JOBS)
        self.assertEqual([job['source_job_id'] for job in jobs],
                         ['4466403318', '4428824971', '4401122334', '4409988776'])

    def test_url_is_normalized_to_the_canonical_form(self):
        proton = linkedin_alert.parse(MANY_JOBS)[1]
        self.assertEqual(proton['url'], 'https://www.linkedin.com/jobs/view/4428824971')
        self.assertNotIn('?', proton['url'])
        self.assertNotIn('comm/', proton['url'])

    def test_every_tracking_and_auth_parameter_is_removed(self):
        cleaned = linkedin_alert.strip_tracking(TRACKED_URL)
        for parameter in ('savedSearchId', 'savedSearchAuthToken', 'trackingId', 'refId',
                          'lipi', 'midToken', 'midSig', 'trk', 'trkEmail', 'eid', 'otpToken'):
            self.assertNotIn(parameter, cleaned)
        self.assertTrue(cleaned.startswith('https://www.linkedin.com/comm/jobs/view/4428824971'))

    def test_no_tracking_parameter_survives_into_a_parsed_job(self):
        blob = repr(linkedin_alert.parse(MANY_JOBS))
        for parameter in ('savedSearchId', 'savedSearchAuthToken', 'trackingId', 'midToken',
                          'midSig', 'otpToken', 'trkEmail', 'lipi'):
            self.assertNotIn(parameter, blob)

    def test_recipient_address_footer_and_tagline_are_never_returned(self):
        blob = repr(linkedin_alert.parse(MANY_JOBS))
        self.assertNotIn('sebastian.private@example.com', blob)
        self.assertNotIn('Abmelden', blob)
        self.assertNotIn('Ihr Profil', blob)
        self.assertNotIn('LinkedIn Corporation', repr(linkedin_alert.parse(ONE_JOB)))

    def test_alert_saved_without_the_separator_rules_still_parses(self):
        jobs = linkedin_alert.parse(NO_SEPARATORS)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[1]['company'], 'Proton')

    def test_the_same_job_linked_twice_is_returned_once(self):
        jobs = linkedin_alert.parse(ONE_JOB + ONE_JOB)
        self.assertEqual(len(jobs), 1)

    def test_text_without_any_job_yields_nothing(self):
        self.assertEqual(linkedin_alert.parse('Just a normal e-mail.\n\nRegards\n'), [])

    def test_txt_upload(self):
        jobs = linkedin_alert.parse_upload('alert.txt', MANY_JOBS.encode('utf-8'))
        self.assertEqual(len(jobs), 4)

    def test_eml_upload(self):
        jobs = linkedin_alert.parse_upload('alert.eml', eml_bytes(MANY_JOBS))
        self.assertEqual(len(jobs), 4)
        self.assertEqual(jobs[1]['source_job_id'], '4428824971')

    def test_html_only_alert_keeps_its_line_structure(self):
        markup = (
            '<html><body><table><tr><td><p>Medical Device Technical Lead</p>'
            '<p>Roche</p><p>Basel, Basel, Schweiz</p>'
            '<p><a href="https://www.linkedin.com/comm/jobs/view/4466403318?trk=eml">'
            'Jobangebot ansehen</a></p></td></tr></table></body></html>')
        jobs = linkedin_alert.parse_upload('alert.html', markup.encode('utf-8'))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['company'], 'Roche')

    def test_latin1_export_is_decoded(self):
        jobs = linkedin_alert.parse_upload('alert.txt', MANY_JOBS.encode('cp1252'))
        self.assertEqual(jobs[2]['location'], 'Zürich, Zürich, Schweiz')


# --------------------------------------------------------------------------
# import service
# --------------------------------------------------------------------------
class ImportServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def seed(self, **overrides):
        job = make_job(
            title=overrides.pop('title', 'Site Reliability Engineer - Observability'),
            company=overrides.pop('company', 'Proton'),
            location=overrides.pop('location', 'Geneva, Switzerland'),
            description=overrides.pop('description', STRONG_DESCRIPTION),
            source=overrides.pop('source', 'Proton (Greenhouse)'),
            source_type=overrides.pop('source_type', 'greenhouse'),
            external_id=overrides.pop('external_id', '7788'),
            url=overrides.pop('url', 'https://boards.greenhouse.io/proton/jobs/7788'))
        scored = {'score': 82, 'label': 'Strong', 'reasons': [], 'concerns': [],
                  'terms': [], 'breakdown': []}
        with jsdb.connect() as conn:
            job_id, _ = JobRepository(conn).upsert(job, scored)
            conn.commit()
        return job_id

    def import_alert(self, text=MANY_JOBS):
        entries = alert_import.parse_alert(text=text)
        with jsdb.connect() as conn:
            preview = alert_import.preview(entries, conn)
            outcome = alert_import.import_entries(preview['jobs'], conn)
        return preview, outcome

    def rows(self):
        with jsdb.connect() as conn:
            return [dict(r) for r in conn.execute(
                'SELECT * FROM discovered_jobs ORDER BY id').fetchall()]

    # -- preview -----------------------------------------------------------
    def test_preview_writes_nothing(self):
        entries = alert_import.parse_alert(text=MANY_JOBS)
        with jsdb.connect() as conn:
            result = alert_import.preview(entries, conn)
        self.assertEqual(result['found'], 4)
        self.assertEqual(result['new'], 4)
        self.assertEqual(self.rows(), [])

    def test_preview_marks_a_known_job(self):
        self.seed()
        entries = alert_import.parse_alert(text=MANY_JOBS)
        with jsdb.connect() as conn:
            result = alert_import.preview(entries, conn)
        known = [job for job in result['jobs'] if job['status'] == alert_import.KNOWN]
        self.assertEqual(len(known), 1)
        self.assertEqual(known[0]['company'], 'Proton')
        self.assertTrue(known[0]['match_reason'])

    # -- import ------------------------------------------------------------
    def test_import_creates_one_job_per_entry(self):
        _preview, outcome = self.import_alert()
        self.assertEqual(outcome['imported'], 4)
        self.assertEqual(outcome['linked'], 0)
        self.assertEqual(len(self.rows()), 4)

    def test_imported_jobs_carry_provenance_and_the_canonical_url(self):
        self.import_alert()
        proton = [r for r in self.rows() if r['company'] == 'Proton'][0]
        self.assertEqual(proton['discovered_via'], 'linkedin')
        self.assertEqual(proton['linkedin_job_id'], '4428824971')
        self.assertEqual(proton['job_url'], 'https://www.linkedin.com/jobs/view/4428824971')
        self.assertEqual(proton['source_type'], 'linkedin')

    def test_no_tracking_parameter_reaches_the_database(self):
        self.import_alert()
        blob = repr(self.rows())
        for parameter in ('savedSearchId', 'savedSearchAuthToken', 'trackingId', 'refId',
                          'midToken', 'midSig', 'otpToken', 'trkEmail', 'eid=', 'lipi'):
            self.assertNotIn(parameter, blob)
        self.assertNotIn('sebastian.private@example.com', blob)

    def test_importing_the_same_alert_twice_creates_no_second_card(self):
        self.import_alert()
        _preview, second = self.import_alert()
        self.assertEqual(second['imported'], 0)
        self.assertEqual(second['linked'], 4)
        self.assertEqual(len(self.rows()), 4)

    def test_a_new_entry_is_flagged_as_needing_a_description(self):
        self.import_alert()
        for row in self.rows():
            self.assertEqual(row['needs_details'], 1)
            self.assertEqual(row['description'], '')

    # -- the Proton case from the specification ----------------------------
    def test_a_job_already_known_from_greenhouse_is_not_duplicated(self):
        existing_id = self.seed()
        preview, outcome = self.import_alert()

        entry = [job for job in preview['jobs'] if job['company'] == 'Proton'][0]
        self.assertEqual(entry['status'], alert_import.KNOWN)
        self.assertEqual(entry['existing_job_id'], existing_id)

        self.assertEqual(outcome['linked'], 1)
        self.assertEqual(outcome['imported'], 3)
        proton = [r for r in self.rows() if r['company'] == 'Proton']
        self.assertEqual(len(proton), 1)
        self.assertEqual(proton[0]['id'], existing_id)

    def test_the_existing_record_stays_authoritative(self):
        self.seed()
        before = self.rows()[0]
        self.import_alert()
        after = [r for r in self.rows() if r['company'] == 'Proton'][0]
        # canonical source, application URL, description and score are untouched
        self.assertEqual(after['source'], before['source'])
        self.assertEqual(after['source_type'], 'greenhouse')
        self.assertEqual(after['job_url'], 'https://boards.greenhouse.io/proton/jobs/7788')
        self.assertEqual(after['description'], before['description'])
        self.assertEqual(after['match_score'], before['match_score'])
        self.assertEqual(after['needs_details'], 0)
        # only the provenance is added
        self.assertEqual(after['discovered_via'], 'linkedin')
        self.assertEqual(after['linkedin_job_id'], '4428824971')
        self.assertEqual(after['linkedin_url'], 'https://www.linkedin.com/jobs/view/4428824971')

    def test_a_retired_job_advertised_again_comes_back(self):
        job_id = self.seed()
        with jsdb.connect() as conn:
            conn.execute("UPDATE discovered_jobs SET state='EXPIRED' WHERE id=?", (job_id,))
            conn.commit()
        self.import_alert()
        after = [r for r in self.rows() if r['id'] == job_id][0]
        self.assertEqual(after['state'], 'SEEN')

    def test_a_state_the_user_chose_survives_an_import(self):
        job_id = self.seed()
        with jsdb.connect() as conn:
            JobRepository(conn).set_state(job_id, 'SAVED')
            conn.commit()
        self.import_alert()
        after = [r for r in self.rows() if r['id'] == job_id][0]
        self.assertEqual(after['state'], 'SAVED')

    # -- the four dedupe keys ---------------------------------------------
    def test_dedupe_matches_on_the_canonical_application_url(self):
        job_id = self.seed(url='https://www.linkedin.com/jobs/view/4428824971')
        entry = alert_import.sanitize_entry({'title': 'Something else entirely',
                                             'company': 'Another AG',
                                             'source_job_id': '4428824971'})
        with jsdb.connect() as conn:
            rows = alert_import._candidates(conn)
        found, reason = alert_import.find_existing(entry, rows)
        self.assertEqual(found['id'], job_id)
        self.assertEqual(reason, 'url')

    def test_dedupe_matches_on_the_external_id_of_the_original_source(self):
        job_id = self.seed()
        entry = alert_import.sanitize_entry({'title': 'x', 'company': 'y',
                                             'source_job_id': '9999'})
        entry['source_type'] = 'greenhouse'
        entry['external_id'] = '7788'
        with jsdb.connect() as conn:
            rows = alert_import._candidates(conn)
        found, reason = alert_import.find_existing(entry, rows)
        self.assertEqual(found['id'], job_id)
        self.assertEqual(reason, 'external_id')

    def test_dedupe_matches_on_company_title_and_location(self):
        job_id = self.seed()
        entry = alert_import.sanitize_entry({
            'title': 'Site Reliability Engineer - Observability', 'company': 'Proton',
            'location': 'Genf, Genf, Schweiz', 'source_job_id': '4428824971'})
        with jsdb.connect() as conn:
            rows = alert_import._candidates(conn)
        found, reason = alert_import.find_existing(entry, rows)
        self.assertEqual(found['id'], job_id)
        self.assertEqual(reason, 'identity')

    def test_a_different_city_is_not_the_same_job(self):
        self.seed()
        entry = alert_import.sanitize_entry({
            'title': 'Site Reliability Engineer - Observability', 'company': 'Proton',
            'location': 'Basel, Basel, Schweiz', 'source_job_id': '4428824971'})
        with jsdb.connect() as conn:
            rows = alert_import._candidates(conn)
        found, _reason = alert_import.find_existing(entry, rows)
        self.assertIsNone(found)

    # -- scoring -----------------------------------------------------------
    def test_an_imported_job_is_scored_by_the_normal_pipeline(self):
        self.import_alert()
        row = [r for r in self.rows() if r['company'] == 'Rheinmetall'][0]
        self.assertEqual(row['personal_fit_score'], row['match_score'])
        self.assertEqual(row['match_score'],
                         max(0, row['base_score']
                             + int(row['operating_style_adjustment'])
                             + int(row['career_direction_adjustment'])))

    def test_adding_a_description_rescores_the_job(self):
        self.import_alert()
        row = [r for r in self.rows() if r['company'] == 'NVIDIA'][0]
        before = row['match_score']
        card = alert_import.add_description(row['id'], STRONG_DESCRIPTION)
        after = [r for r in self.rows() if r['id'] == row['id']][0]
        self.assertEqual(after['needs_details'], 0)
        self.assertEqual(after['description'], STRONG_DESCRIPTION)
        self.assertGreater(after['match_score'], before)
        self.assertFalse(card['needs_details'])

    def test_an_empty_description_is_refused(self):
        self.import_alert()
        row = self.rows()[0]
        with self.assertRaises(alert_import.AlertImportError):
            alert_import.add_description(row['id'], '   ')


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class ImportApiTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.client = TestClient(backend.app)

    def preview(self, **kwargs):
        return self.client.post('/api/jobs/import/linkedin/preview', **kwargs)

    def test_txt_upload_is_previewed(self):
        response = self.preview(
            files={'file': ('alert.txt', MANY_JOBS.encode('utf-8'), 'text/plain')})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['found'], 4)
        self.assertEqual(body['jobs'][0]['status'], 'NEW')

    def test_eml_upload_is_previewed(self):
        response = self.preview(
            files={'file': ('alert.eml', eml_bytes(ONE_JOB), 'message/rfc822')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['found'], 1)

    def test_pasted_text_is_previewed(self):
        response = self.preview(data={'text': MANY_JOBS})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['found'], 4)

    def test_an_empty_request_is_rejected(self):
        self.assertEqual(self.preview(data={'text': '  '}).status_code, 400)

    def test_text_without_a_job_is_rejected_with_an_explanation(self):
        response = self.preview(data={'text': 'Nothing to see here.'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('.txt', response.json()['detail'])

    def test_selected_jobs_are_imported_and_appear_on_the_jobs_screen(self):
        found = self.preview(data={'text': MANY_JOBS}).json()['jobs']
        chosen = [job for job in found if job['company'] in ('Roche', 'NVIDIA')]
        response = self.client.post('/api/jobs/import/linkedin', json={'jobs': chosen})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['imported'], 2)
        companies = {card['company'] for card in self.client.get('/api/jobs').json()['jobs']}
        self.assertEqual(companies, {'Roche', 'NVIDIA'})

    def test_an_imported_card_says_it_needs_a_description(self):
        found = self.preview(data={'text': ONE_JOB}).json()['jobs']
        self.client.post('/api/jobs/import/linkedin', json={'jobs': found})
        card = self.client.get('/api/jobs').json()['jobs'][0]
        self.assertTrue(card['needs_details'])
        self.assertEqual(card['discovered_via'], 'linkedin')
        self.assertEqual(card['url'], 'https://www.linkedin.com/jobs/view/4466403318')

    def test_a_description_can_be_added_afterwards(self):
        found = self.preview(data={'text': ONE_JOB}).json()['jobs']
        self.client.post('/api/jobs/import/linkedin', json={'jobs': found})
        job_id = self.client.get('/api/jobs').json()['jobs'][0]['id']
        response = self.client.post('/api/jobs/{0}/description'.format(job_id),
                                    json={'description': STRONG_DESCRIPTION})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['needs_details'])

    def test_importing_nothing_is_rejected(self):
        self.assertEqual(
            self.client.post('/api/jobs/import/linkedin', json={'jobs': []}).status_code, 400)

    def test_the_navigation_is_unchanged(self):
        markup = self.client.get('/').text
        self.assertEqual(markup.count('class="tab"'), 4)
        self.assertIn('Import LinkedIn Alert', markup)


if __name__ == '__main__':
    unittest.main()
