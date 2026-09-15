"""An application is a record, not a status counter.

Four layers, the same shape the rest of this suite uses.

``StatusTransitionTests``
    Every move through the pipeline, through the HTTP endpoint the screen
    actually calls: the record, the stage counters, ``applied_at`` written once
    and never rewritten, and the two closed outcomes.

``ApplicationDocumentTests``
    What was sent, and that it stays what was sent.  Linking from the document
    store, uploading a PDF and a DOCX, refusing anything else, removing an
    association - and the one that matters most: replacing the primary CV
    afterwards must not touch a single byte of an application already recorded.

``DocumentServingTests``
    Files leave the server through one endpoint, addressed by id, and a row
    whose path points outside ``documents/applications/`` is unreadable rather
    than served.

``ExistingRecordTests``
    The records already in the database keep working: no document association,
    no duplicate application, and a V1 link row that becomes a snapshot.

``ApplicationCardTests``
    Runs the real ``static/app.js`` in Node against a small but genuine DOM
    (``tests/js/application_cards_harness.js``) and measures what an
    Applications card does - built closed, opened once, closed again, with the
    status control on the closed card and the maintenance UI only inside it.
    Skipped where Node is not installed.
"""

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app as backend
from jobscanner import application_documents as appdocs
from jobscanner import applications as applications_mod
from jobscanner import db as jsdb
from jobscanner import documents as documents_mod
from jobscanner.repository import JobRepository
from jobscanner.schema_v2 import APPLICATION_STATUSES, CLOSED_STATUSES
from tests.helpers import STRONG_DESCRIPTION, make_job

HARNESS = Path(__file__).resolve().parent / 'js' / 'application_cards_harness.js'
NODE = shutil.which('node')

PDF_BYTES = b'%PDF-1.4\nnever mind the contents, only the bytes matter here\n%%EOF'
#: A tiny but real zip, which is what a .docx is.
DOCX_BYTES = (b'PK\x03\x04\x14\x00\x00\x00\x00\x00' + b'\x00' * 22
              + b'word/document.xml' + b'PK\x05\x06' + b'\x00' * 18)


class TempWorkspace:
    """A throwaway database *and* documents folder for one test case.

    Both, not just the database: an application document is a real file, and a
    test that wrote one into the checkout's own ``documents/`` folder would be
    editing the user's workspace.
    """

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        self._previous = (jsdb.get_db_path(), jsdb.workspace_root())
        root = Path(self._dir.name)
        (root / 'data').mkdir(parents=True, exist_ok=True)
        jsdb.set_workspace_root(root)
        jsdb.set_db_path(root / 'data' / 'app.db')
        jsdb.init_db()
        documents_mod.ensure_dirs()
        return self

    def __exit__(self, *exc):
        jsdb.set_db_path(self._previous[0])
        jsdb.set_workspace_root(self._previous[1])
        self._dir.cleanup()
        return False

    @property
    def root(self):
        return Path(self._dir.name)


class TrackingTestCase(unittest.TestCase):
    def setUp(self):
        self.workspace = TempWorkspace().__enter__()
        self.addCleanup(self.workspace.__exit__, None, None, None)
        self.client = TestClient(backend.app)

    # -- fixtures ----------------------------------------------------------
    def seed_job(self, external_id='1', title='Head of Technology Operations'):
        job = make_job(title=title, company='AWS EMEA SARL (Switzerland Branch)',
                       location='Zurich, Switzerland', description=STRONG_DESCRIPTION,
                       external_id=external_id)
        scored = {'score': 84, 'label': 'Excellent match',
                  'reasons': ['Head of scope'], 'concerns': ['Salary not published'],
                  'terms': [], 'breakdown': []}
        with jsdb.connect() as conn:
            job_id, _ = JobRepository(conn).upsert(job, scored)
            conn.commit()
        return job_id

    def make_application(self, **overrides):
        payload = {'company': 'AWS EMEA SARL (Switzerland Branch)',
                   'position': 'Principal Technical Program Manager',
                   'location': 'Zurich', 'status': 'Preparation'}
        payload.update(overrides)
        response = self.client.post('/api/applications', json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def store_document(self, kind='cv_en', filename='CV_EN.pdf', data=PDF_BYTES):
        response = self.client.post(
            '/api/profile/documents', data={'kind': kind},
            files={'file': (filename, io.BytesIO(data), 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['document']

    def set_status(self, application_id, status, expect=200):
        response = self.client.post('/api/applications/{0}/status'.format(application_id),
                                    json={'status': status})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def counts(self, board):
        return {stage['status']: stage['count'] for stage in board}


# ======================================================================
class StatusTransitionTests(TrackingTestCase):
    def test_preparation_becomes_applied_from_the_applications_screen(self):
        application = self.make_application()
        self.assertEqual(application['status'], 'Preparation')
        result = self.set_status(application['id'], 'Applied')
        self.assertEqual(result['application']['status'], 'Applied')
        self.assertTrue(result['application']['applied_at'])
        self.assertTrue(result['application']['is_applied'])

    def test_applied_becomes_screening(self):
        application = self.make_application()
        self.set_status(application['id'], 'Applied')
        result = self.set_status(application['id'], 'Screening')
        self.assertEqual(result['application']['status'], 'Screening')

    def test_screening_becomes_interview(self):
        application = self.make_application()
        for status in ('Applied', 'Screening', 'Interview'):
            result = self.set_status(application['id'], status)
        self.assertEqual(result['application']['status'], 'Interview')

    def test_every_canonical_status_can_be_reached_and_no_other(self):
        application = self.make_application()
        for status in APPLICATION_STATUSES:
            result = self.set_status(application['id'], status)
            self.assertEqual(result['application']['status'], status)
        listing = self.client.get('/api/applications').json()
        self.assertEqual(listing['statuses'], APPLICATION_STATUSES)

    def test_rejected_and_withdrawn_are_recorded_and_read_as_closed(self):
        for status in CLOSED_STATUSES:
            application = self.make_application(position='Role ' + status)
            self.set_status(application['id'], 'Applied')
            result = self.set_status(application['id'], status)
            self.assertEqual(result['application']['status'], status)
            self.assertFalse(result['application']['is_active'])
            self.assertTrue(result['application']['applied_at'],
                            'a closed application keeps the day it was sent')

    def test_stage_counters_update_with_the_same_request(self):
        application = self.make_application()
        board = self.counts(self.client.get('/api/applications').json()['board'])
        self.assertEqual(board['Preparation'], 1)
        self.assertEqual(board['Applied'], 0)

        after = self.counts(self.set_status(application['id'], 'Applied')['board'])
        self.assertEqual(after['Preparation'], 0)
        self.assertEqual(after['Applied'], 1)

        # And the counters the screen would fetch separately agree with them.
        reloaded = self.counts(self.client.get('/api/applications').json()['board'])
        self.assertEqual(reloaded, after)

    def test_applied_at_is_set_once_and_never_rewritten(self):
        application = self.make_application()
        self.assertEqual(application['applied_at'], '')
        first = self.set_status(application['id'], 'Applied')['application']['applied_at']
        self.assertTrue(first)

        for status in ('Screening', 'Interview', 'Rejected', 'Applied', 'Offer'):
            current = self.set_status(application['id'], status)['application']
            self.assertEqual(current['applied_at'], first,
                             'applied_at changed on the way to ' + status)

    def test_a_status_that_was_never_submitted_has_no_applied_timestamp(self):
        application = self.make_application()
        for status in ('Withdrawn', 'Rejected', 'Preparation'):
            current = self.set_status(application['id'], status)['application']
            self.assertEqual(current['applied_at'], '')

    def test_applied_date_is_filled_in_but_a_date_the_user_typed_wins(self):
        typed = self.make_application(applied_date='2026-01-05')
        self.set_status(typed['id'], 'Applied')
        self.assertEqual(self.client.get('/api/applications/{0}'.format(typed['id']))
                         .json()['applied_date'], '2026-01-05')

        blank = self.make_application(position='Another role')
        applied = self.set_status(blank['id'], 'Applied')['application']
        self.assertEqual(applied['applied_date'], applied['applied_at'][:10])

    def test_status_history_records_each_transition_in_order(self):
        application = self.make_application()
        for status in ('Applied', 'Screening', 'Interview'):
            self.set_status(application['id'], status)
        history = self.client.get('/api/applications/{0}'.format(application['id'])) \
            .json()['status_history']
        self.assertEqual([(h['from_status'], h['to_status']) for h in history],
                         [('', 'Preparation'), ('Preparation', 'Applied'),
                          ('Applied', 'Screening'), ('Screening', 'Interview')])
        self.assertTrue(all(h['changed_at'] for h in history))

    def test_setting_the_same_status_again_changes_nothing(self):
        application = self.make_application()
        self.set_status(application['id'], 'Applied')
        before = self.client.get('/api/applications/{0}'.format(application['id'])).json()
        self.set_status(application['id'], 'Applied')
        after = self.client.get('/api/applications/{0}'.format(application['id'])).json()
        self.assertEqual(len(after['status_history']), len(before['status_history']))
        self.assertEqual(after['applied_at'], before['applied_at'])
        self.assertEqual(after['updated_at'], before['updated_at'])

    def test_the_detail_form_takes_the_same_route_as_the_card(self):
        """A status inside a wider edit still sets the timestamp and history."""
        application = self.make_application()
        self.client.put('/api/applications/{0}'.format(application['id']),
                        json={'status': 'Applied', 'notes': 'Applied via Amazon Jobs'})
        current = self.client.get('/api/applications/{0}'.format(application['id'])).json()
        self.assertEqual(current['status'], 'Applied')
        self.assertTrue(current['applied_at'])
        self.assertEqual(current['notes'], 'Applied via Amazon Jobs')
        self.assertIn(('Preparation', 'Applied'),
                      [(h['from_status'], h['to_status']) for h in current['status_history']])

    def test_notes_are_plain_text_and_survive_a_round_trip(self):
        application = self.make_application()
        self.client.put('/api/applications/{0}'.format(application['id']),
                        json={'notes': 'Recruiter call scheduled\nSalary discussed'})
        current = self.client.get('/api/applications/{0}'.format(application['id'])).json()
        self.assertEqual(current['notes'], 'Recruiter call scheduled\nSalary discussed')

    def test_status_persists_across_a_restart(self):
        application = self.make_application()
        self.set_status(application['id'], 'Screening')
        with TestClient(backend.app) as fresh:
            reloaded = fresh.get('/api/applications/{0}'.format(application['id'])).json()
        self.assertEqual(reloaded['status'], 'Screening')
        self.assertTrue(reloaded['applied_at'])

    def test_an_unknown_application_is_a_404_and_a_blank_status_a_400(self):
        application = self.make_application()
        self.set_status(999, 'Applied', expect=404)
        self.set_status(application['id'], '', expect=400)


# ======================================================================
class LinkedJobTests(TrackingTestCase):
    def test_the_linked_job_card_follows_the_application_status(self):
        job_id = self.seed_job()
        created = self.client.post('/api/jobs/{0}/application'.format(job_id))
        self.assertEqual(created.status_code, 200, created.text)
        application_id = created.json()['id']

        for status in ('Applied', 'Screening', 'Interview', 'Final', 'Offer'):
            result = self.set_status(application_id, status)
            self.assertEqual(result['job']['job_id'], job_id)
            self.assertEqual(result['job']['application_status'], status)
            self.assertTrue(result['job']['application_active'],
                            status + ' is a live thread and must read as one')
            card = self.client.get('/api/jobs').json()['jobs'][0]
            self.assertEqual(card['application_status'], status)
            self.assertTrue(card['has_application'])

    def test_rejected_and_withdrawn_stay_tracked_but_read_as_closed(self):
        job_id = self.seed_job()
        application_id = self.client.post('/api/jobs/{0}/application'.format(job_id)).json()['id']
        for status in CLOSED_STATUSES:
            result = self.set_status(application_id, status)
            self.assertTrue(result['job']['has_application'])
            self.assertFalse(result['job']['application_active'])
            self.assertEqual(result['job']['application_status'], status)

    def test_tracking_the_same_job_twice_never_creates_a_second_record(self):
        job_id = self.seed_job()
        first = self.client.post('/api/jobs/{0}/application'.format(job_id)).json()
        self.set_status(first['id'], 'Applied')
        second = self.client.post('/api/jobs/{0}/application'.format(job_id)).json()
        self.assertEqual(second['id'], first['id'])
        self.assertEqual(second['status'], 'Applied', 'the existing record is returned as it is')
        self.assertEqual(len(self.client.get('/api/applications').json()['applications']), 1)


# ======================================================================
class ApplicationDocumentTests(TrackingTestCase):
    def attach_stored(self, application_id, document_id, kind='', expect=200):
        response = self.client.post(
            '/api/applications/{0}/documents'.format(application_id),
            json={'document_id': document_id, 'document_kind': kind})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def upload(self, application_id, filename, data, kind='cv', expect=200):
        response = self.client.post(
            '/api/applications/{0}/documents/upload'.format(application_id),
            data={'document_kind': kind},
            files={'file': (filename, io.BytesIO(data), 'application/octet-stream')})
        self.assertEqual(response.status_code, expect, response.text)
        return response.json()

    def test_an_existing_document_store_file_can_be_attached(self):
        application = self.make_application()
        stored = self.store_document(kind='cv_en', filename='CV_EN.pdf')
        result = self.attach_stored(application['id'], stored['id'])
        document = result['document']
        self.assertEqual(document['document_kind'], 'cv')
        self.assertEqual(document['kind_label'], 'CV / Resume')
        self.assertEqual(document['original_filename'], 'CV_EN.pdf')
        self.assertEqual(document['source'], 'DOCUMENT_STORE')
        self.assertEqual(document['source_document_id'], stored['id'])
        self.assertTrue(document['exists'])
        self.assertEqual(document['mime_type'], 'application/pdf')
        self.assertTrue(document['created_at'])

    def test_a_pdf_can_be_uploaded_straight_onto_the_application(self):
        application = self.make_application()
        document = self.upload(application['id'], 'Tailored_CV.pdf', PDF_BYTES)['document']
        self.assertEqual(document['source'], 'UPLOADED_FOR_APPLICATION')
        self.assertEqual(document['mime_type'], 'application/pdf')
        self.assertEqual(document['size_bytes'], len(PDF_BYTES))
        self.assertTrue(document['exists'])

    def test_a_docx_can_be_uploaded_straight_onto_the_application(self):
        application = self.make_application()
        document = self.upload(application['id'], 'Motivation.docx', DOCX_BYTES,
                               kind='cover_letter')['document']
        self.assertEqual(document['document_kind'], 'cover_letter')
        self.assertIn('wordprocessingml', document['mime_type'])
        self.assertTrue(document['exists'])

    def test_an_unsupported_file_type_is_refused(self):
        application = self.make_application()
        for filename in ('payload.exe', 'notes.txt', 'scan.png', 'archive'):
            self.upload(application['id'], filename, b'MZ nope', expect=400)
        self.assertEqual(
            self.client.get('/api/applications/{0}/documents'.format(application['id']))
            .json()['documents'], [])

    def test_every_supported_kind_is_offered_and_nothing_else(self):
        meta = self.client.get('/api/applications').json()
        self.assertEqual([k['kind'] for k in meta['document_kinds']],
                         ['cv', 'cover_letter', 'additional', 'certificate', 'other'])
        self.assertEqual(meta['document_formats'], ['PDF', 'DOCX'])

    def test_an_association_can_be_removed_without_touching_the_store(self):
        application = self.make_application()
        stored = self.store_document()
        document = self.attach_stored(application['id'], stored['id'])['document']

        response = self.client.delete('/api/applications/{0}/documents/{1}'.format(
            application['id'], document['id']))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['documents'], [])
        # The document store is untouched: removing it here was about this
        # application, not about the file.
        self.assertTrue(documents_mod.get(stored['id'])['exists'])

    def test_removing_a_document_twice_is_a_404_not_a_silent_success(self):
        application = self.make_application()
        stored = self.store_document()
        document = self.attach_stored(application['id'], stored['id'])['document']
        path = '/api/applications/{0}/documents/{1}'.format(application['id'], document['id'])
        self.assertEqual(self.client.delete(path).status_code, 200)
        self.assertEqual(self.client.delete(path).status_code, 404)

    # -- the one that matters ---------------------------------------------
    def test_a_later_primary_cv_does_not_change_a_recorded_application(self):
        """The whole reason this is a copy and not a link."""
        application = self.make_application()
        original = self.store_document(kind='cv_en', filename='CV_EN.pdf', data=PDF_BYTES)
        attached = self.attach_stored(application['id'], original['id'])['document']
        self.set_status(application['id'], 'Applied')

        before = self.client.get('/api/applications/{0}/documents/{1}/file'.format(
            application['id'], attached['id'])).content
        self.assertEqual(before, PDF_BYTES)

        # The user writes a better CV and makes it the primary one; then deletes
        # the old file entirely.
        replacement = self.store_document(kind='cv_en', filename='CV_EN.pdf',
                                          data=b'%PDF-1.7 a completely different CV')
        self.client.post('/api/profile/documents/{0}/primary'.format(replacement['id']))
        self.assertEqual(self.client.delete(
            '/api/profile/documents/{0}'.format(original['id'])).status_code, 200)

        current = self.client.get('/api/applications/{0}'.format(application['id'])).json()
        self.assertEqual(len(current['documents']), 1)
        recorded = current['documents'][0]
        self.assertEqual(recorded['id'], attached['id'])
        self.assertEqual(recorded['original_filename'], 'CV_EN.pdf')
        self.assertEqual(recorded['content_hash'], attached['content_hash'])
        self.assertTrue(recorded['exists'])
        after = self.client.get('/api/applications/{0}/documents/{1}/file'.format(
            application['id'], attached['id'])).content
        self.assertEqual(after, PDF_BYTES, 'the application still holds what was sent')

    def test_two_applications_that_used_the_same_cv_each_hold_their_own_copy(self):
        stored = self.store_document()
        first = self.make_application(position='Role one')
        second = self.make_application(position='Role two')
        a = self.attach_stored(first['id'], stored['id'])['document']
        b = self.attach_stored(second['id'], stored['id'])['document']
        self.assertNotEqual(a['id'], b['id'])

        with jsdb.connect() as conn:
            paths = [r['stored_path'] for r in conn.execute(
                'SELECT stored_path FROM application_documents ORDER BY id')]
        self.assertEqual(len(set(paths)), 2, 'each application owns its own file')
        for path, application_id in zip(paths, (first['id'], second['id'])):
            self.assertTrue(path.startswith('documents/applications/{0}/'.format(application_id)))

        # Removing one application's copy leaves the other alone.
        self.client.delete('/api/applications/{0}/documents/{1}'.format(first['id'], a['id']))
        still_there = self.client.get('/api/applications/{0}'.format(second['id'])).json()
        self.assertEqual(len(still_there['documents']), 1)
        self.assertTrue(still_there['documents'][0]['exists'])

    def test_document_paths_are_workspace_relative_and_never_reach_the_browser(self):
        application = self.make_application()
        stored = self.store_document()
        self.attach_stored(application['id'], stored['id'])
        payload = self.client.get('/api/applications/{0}'.format(application['id'])).json()
        as_text = json.dumps(payload)
        self.assertNotIn('stored_path', as_text)
        self.assertNotIn('absolute_path', as_text)
        self.assertNotIn(str(self.workspace.root), as_text)
        with jsdb.connect() as conn:
            path = conn.execute('SELECT stored_path FROM application_documents').fetchone()[0]
        self.assertFalse(Path(path).is_absolute())
        self.assertTrue(path.startswith('documents/applications/'))

    def test_the_summary_reports_only_the_kinds_that_are_really_there(self):
        application = self.make_application()
        self.assertEqual(self.client.get('/api/applications/{0}'.format(application['id']))
                         .json()['document_summary'], [])
        self.upload(application['id'], 'CV.pdf', PDF_BYTES, kind='cv')
        summary = self.client.get('/api/applications/{0}'.format(application['id'])) \
            .json()['document_summary']
        self.assertEqual([s['kind'] for s in summary], ['cv'])
        self.assertEqual(summary[0]['filename'], 'CV.pdf')

    def test_attaching_to_an_unknown_application_or_document_is_refused(self):
        application = self.make_application()
        self.attach_stored(999, 1, expect=400)
        self.attach_stored(application['id'], 999, expect=400)
        self.upload(999, 'CV.pdf', PDF_BYTES, expect=400)

    def test_a_document_store_file_that_has_gone_missing_is_reported_not_faked(self):
        application = self.make_application()
        stored = self.store_document()
        Path(documents_mod.get(stored['id'])['absolute_path']).unlink()
        self.attach_stored(application['id'], stored['id'], expect=400)
        self.assertEqual(self.client.get('/api/applications/{0}'.format(application['id']))
                         .json()['documents'], [])


# ======================================================================
class DocumentServingTests(TrackingTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.make_application()
        stored = self.store_document()
        self.document = self.client.post(
            '/api/applications/{0}/documents'.format(self.application['id']),
            json={'document_id': stored['id']}).json()['document']

    def url(self, link_id=None, application_id=None):
        return '/api/applications/{0}/documents/{1}/file'.format(
            application_id or self.application['id'], link_id or self.document['id'])

    def test_a_stored_document_is_served_with_its_own_name_and_type(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, PDF_BYTES)
        self.assertEqual(response.headers['content-type'], 'application/pdf')
        self.assertIn('CV_EN.pdf', response.headers.get('content-disposition', ''))

    def test_a_document_of_another_application_is_not_served(self):
        other = self.make_application(position='Another role')
        self.assertEqual(self.client.get(
            self.url(application_id=other['id'])).status_code, 404)

    def test_an_unknown_document_id_is_a_404(self):
        self.assertEqual(self.client.get(self.url(link_id=9999)).status_code, 404)

    def test_the_endpoint_takes_ids_and_offers_no_way_to_name_a_path(self):
        for attempt in ('../../../etc/passwd', '..%2f..%2fetc%2fpasswd', 'documents/cv/x.pdf'):
            response = self.client.get('/api/applications/{0}/documents/{1}/file'.format(
                self.application['id'], attempt))
            self.assertIn(response.status_code, (404, 422),
                          'a path where an id belongs must never be honoured')

    def test_a_traversing_path_in_the_database_is_refused_rather_than_served(self):
        """Belt and braces: only this module writes these rows, but if a row
        ever pointed outside the applications folder it must not be readable."""
        secret = self.workspace.root / 'secret.pdf'
        secret.write_bytes(b'%PDF-1.4 not yours')
        for path in ('documents/applications/../../secret.pdf',
                     '../secret.pdf',
                     str(secret),
                     'documents/cv/cv_en__CV_EN.pdf'):
            with jsdb.connect() as conn:
                conn.execute('UPDATE application_documents SET stored_path=? WHERE id=?',
                             (path, self.document['id']))
                conn.commit()
            self.assertEqual(self.client.get(self.url()).status_code, 404,
                             'served a file from ' + path)
            with jsdb.connect() as conn:
                row = conn.execute('SELECT * FROM application_documents WHERE id=?',
                                   (self.document['id'],)).fetchone()
            self.assertIsNone(appdocs.resolve(dict(row)))

    def test_a_document_whose_file_was_deleted_reports_missing_and_404s(self):
        with jsdb.connect() as conn:
            row = dict(conn.execute('SELECT * FROM application_documents WHERE id=?',
                                    (self.document['id'],)).fetchone())
        appdocs.resolve(row).unlink()
        current = self.client.get('/api/applications/{0}'.format(self.application['id'])).json()
        self.assertFalse(current['documents'][0]['exists'])
        self.assertEqual(self.client.get(self.url()).status_code, 404)


# ======================================================================
class ExistingRecordTests(TrackingTestCase):
    def test_an_application_without_documents_stays_valid_and_can_gain_them(self):
        application = self.make_application()
        listed = self.client.get('/api/applications').json()['applications'][0]
        self.assertEqual(listed['documents'], [])
        self.assertEqual(listed['document_summary'], [])
        self.assertEqual(
            self.client.get('/api/applications/{0}/documents'.format(application['id']))
            .json()['documents'], [])
        # And it can still be moved through the pipeline: an empty package is
        # never a reason to block a status change.
        self.assertEqual(self.set_status(application['id'], 'Applied')['application']['status'],
                         'Applied')
        stored = self.store_document()
        self.client.post('/api/applications/{0}/documents'.format(application['id']),
                         json={'document_id': stored['id']})
        self.assertEqual(len(self.client.get('/api/applications/{0}'.format(application['id']))
                             .json()['documents']), 1)

    def test_a_v1_pointer_row_becomes_a_snapshot_and_the_original_is_kept(self):
        """The migration of a database written before this feature existed."""
        application = self.make_application()
        stored = self.store_document()
        with jsdb.connect() as conn:
            conn.execute('DROP TABLE application_documents')
            conn.execute('''CREATE TABLE application_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL,
                document_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(application_id, document_id))''')
            conn.execute('INSERT INTO application_documents '
                         '(application_id, document_id, role, created_at) VALUES (?,?,?,?)',
                         (application['id'], stored['id'], 'cv', '2026-09-15T10:04:40'))
            conn.commit()

        jsdb.init_db()

        documents = self.client.get('/api/applications/{0}'.format(application['id'])) \
            .json()['documents']
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]['document_kind'], 'cv')
        self.assertEqual(documents[0]['original_filename'], 'CV_EN.pdf')
        self.assertEqual(documents[0]['source_document_id'], stored['id'])
        self.assertEqual(documents[0]['created_at'], '2026-09-15T10:04:40')
        self.assertTrue(documents[0]['exists'])
        self.assertEqual(self.client.get(
            '/api/applications/{0}/documents/{1}/file'.format(
                application['id'], documents[0]['id'])).content, PDF_BYTES)

        with jsdb.connect() as conn:
            legacy = conn.execute('SELECT application_id, document_id FROM '
                                  'application_documents_v1').fetchall()
        self.assertEqual([tuple(r) for r in legacy], [(application['id'], stored['id'])])

    def test_a_record_that_was_already_applied_keeps_its_date(self):
        """``applied_at`` is backfilled from the date the record already had."""
        application = self.make_application()
        with jsdb.connect() as conn:
            conn.execute("UPDATE applications SET status='Applied', applied_date='2026-09-13', "
                         "applied_at='' WHERE id=?", (application['id'],))
            conn.execute('DELETE FROM application_status_history WHERE application_id=?',
                         (application['id'],))
            conn.commit()

        jsdb.init_db()

        current = self.client.get('/api/applications/{0}'.format(application['id'])).json()
        self.assertEqual(current['status'], 'Applied')
        self.assertEqual(current['applied_at'], '2026-09-13T00:00:00')
        self.assertEqual([(h['from_status'], h['to_status']) for h in current['status_history']],
                         [('', 'Applied')])

    def test_a_draft_with_a_date_typed_into_it_is_not_treated_as_sent(self):
        application = self.make_application(applied_date='2026-09-15')
        with jsdb.connect() as conn:
            conn.execute("UPDATE applications SET applied_at='' WHERE id=?", (application['id'],))
            conn.commit()
        jsdb.init_db()
        current = self.client.get('/api/applications/{0}'.format(application['id'])).json()
        self.assertEqual(current['status'], 'Preparation')
        self.assertEqual(current['applied_at'], '')
        self.assertFalse(current['is_applied'])

    def test_the_migration_is_idempotent(self):
        application = self.make_application()
        stored = self.store_document()
        self.client.post('/api/applications/{0}/documents'.format(application['id']),
                         json={'document_id': stored['id']})
        self.set_status(application['id'], 'Applied')
        for _ in range(3):
            jsdb.init_db()
        current = self.client.get('/api/applications/{0}'.format(application['id'])).json()
        self.assertEqual(len(current['documents']), 1)
        self.assertEqual(len(current['status_history']), 2)
        self.assertEqual(len(self.client.get('/api/applications').json()['applications']), 1)


# ======================================================================
class ApplicationModuleTests(TrackingTestCase):
    """The module directly, for the things no endpoint exposes."""

    def test_canonical_kind_never_invents_a_sixth_kind(self):
        for value in ('cv_en', 'cv_de', 'CV', 'resume'):
            self.assertEqual(appdocs.canonical_kind(value), 'cv')
        for value in ('motivation_en', 'cover_letter', 'Motivation Letter'):
            self.assertEqual(appdocs.canonical_kind(value), 'cover_letter')
        for value in ('certificate', 'reference'):
            self.assertEqual(appdocs.canonical_kind(value), 'certificate')
        for value in ('', 'nonsense', None):
            self.assertEqual(appdocs.canonical_kind(value), 'other')
        self.assertTrue(all(appdocs.canonical_kind(k) in dict(appdocs.KINDS)
                            for k in documents_mod.KINDS))

    def test_the_apply_assistant_source_is_part_of_the_vocabulary(self):
        self.assertEqual(set(appdocs.SOURCES),
                         {'DOCUMENT_STORE', 'UPLOADED_FOR_APPLICATION', 'APPLY_ASSISTANT'})
        self.assertEqual(appdocs.canonical_source('nonsense'), 'DOCUMENT_STORE')

    def test_a_file_the_assistant_uploaded_can_be_kept_on_the_application(self):
        application = self.make_application()
        stored = self.store_document()
        document = appdocs.attach_stored(application['id'], stored['id'],
                                         source='APPLY_ASSISTANT')
        self.assertEqual(document['source'], 'APPLY_ASSISTANT')
        self.assertTrue(appdocs.has_stored_document(application['id'], stored['id'],
                                                    source='APPLY_ASSISTANT'))
        self.assertFalse(appdocs.has_stored_document(application['id'], stored['id'],
                                                     source='UPLOADED_FOR_APPLICATION'))

    def test_set_status_is_the_only_writer_and_reports_an_unknown_record(self):
        self.assertIsNone(applications_mod.set_status(999, 'Applied'))
        application = self.make_application()
        self.assertEqual(applications_mod.set_status(application['id'], 'Applied')['status'],
                         'Applied')

    def test_a_status_the_pipeline_does_not_know_is_mapped_not_stored(self):
        application = self.make_application()
        current = applications_mod.set_status(application['id'], 'Telefoninterview')
        self.assertEqual(current['status'], 'Screening')
        self.assertIn(current['status'], APPLICATION_STATUSES)

    def test_deleting_an_application_takes_its_documents_and_history_with_it(self):
        application = self.make_application()
        stored = self.store_document()
        appdocs.attach_stored(application['id'], stored['id'])
        self.client.delete('/api/applications/{0}'.format(application['id']))
        with jsdb.connect() as conn:
            self.assertEqual(conn.execute(
                'SELECT COUNT(*) FROM application_documents WHERE application_id=?',
                (application['id'],)).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                'SELECT COUNT(*) FROM application_status_history WHERE application_id=?',
                (application['id'],)).fetchone()[0], 0)


# ======================================================================
@unittest.skipUnless(NODE, 'Node is not installed; the frontend behaviour tests need it.')
class ApplicationCardTests(unittest.TestCase):
    """What an Applications card really does, run through the shipped frontend."""

    @classmethod
    def setUpClass(cls):
        result = subprocess.run([NODE, str(HARNESS)], capture_output=True, text=True,
                                timeout=60, check=False)
        if result.returncode != 0:
            raise AssertionError('harness failed:\n' + result.stdout + result.stderr)
        cls.results = json.loads(result.stdout)

    # -- closed by default -------------------------------------------------
    def test_a_card_is_built_closed(self):
        closed = self.results['collapsed_by_default']
        self.assertTrue(closed['bodyHidden'])
        self.assertEqual(closed['bodyChildren'], 0, 'the detail is not built until it is opened')
        self.assertEqual(closed['ariaExpanded'], 'false')
        self.assertEqual(closed['ariaControls'], closed['bodyId'])
        self.assertNotIn('open', closed['cardClass'].split())
        self.assertEqual(closed['openSet'], 0)

    def test_a_closed_card_carries_the_summary_a_decision_needs(self):
        text = self.results['collapsed_by_default']['headText']
        for fragment in ('Principal Technical Program Manager',
                         'AWS EMEA SARL (Switzerland Branch)', 'Zurich',
                         'Estimate: CHF 200k-250k', 'Applied: Sep 13, 2026'):
            self.assertIn(fragment, text)

    def test_a_closed_card_carries_the_status_control_and_nothing_that_maintains_the_record(self):
        summary = self.results['collapsed_summary']
        self.assertEqual(summary['statusOptions'], APPLICATION_STATUSES)
        self.assertEqual(summary['selected'], ['Preparation'])
        self.assertEqual(summary['documentRows'], 0)
        self.assertEqual(summary['attachButtons'], 0)
        self.assertEqual(summary['textareas'], 0, 'notes belong in the open card')
        self.assertNotIn('Status history', summary['text'])

    # -- opening and closing ----------------------------------------------
    def test_opening_a_card_builds_the_detail_and_reveals_it(self):
        opened = self.results['expand_and_collapse']['opened']
        self.assertFalse(opened['hidden'])
        self.assertEqual(opened['aria'], 'true')
        self.assertGreater(opened['children'], 0)
        self.assertIn('open', opened['cardClass'].split())
        self.assertTrue(opened['inOpenSet'])
        for fragment in ('Application documents', 'Status history', 'Notes', 'Dates'):
            self.assertIn(fragment, opened['text'])

    def test_closing_a_card_puts_the_detail_away_without_throwing_it_out(self):
        closed = self.results['expand_and_collapse']['closed']
        self.assertTrue(closed['hidden'])
        self.assertEqual(closed['aria'], 'false')
        self.assertGreater(closed['children'], 0)
        self.assertTrue(closed['sameDetail'], 'the detail is built once, not on every open')
        self.assertNotIn('open', closed['cardClass'].split())
        self.assertFalse(closed['inOpenSet'])

    def test_toggling_one_card_touches_nothing_else(self):
        result = self.results['expand_and_collapse']
        self.assertEqual(result['orderBefore'], result['orderAfter'])
        self.assertTrue(result['sameNodes'], 'the list is not rebuilt')
        self.assertTrue(result['otherBodiesHidden'])
        self.assertEqual(result['fetches'], 0, 'expanding a card fetches nothing')

    def test_the_header_toggles_but_a_control_inside_it_never_does(self):
        toggle = self.results['header_toggle']
        self.assertFalse(toggle['afterControl'], 'the status selector must not open the card')
        self.assertTrue(toggle['afterTitle'])
        self.assertTrue(toggle['controlIsAction'])

    # -- the Preparation affordance ---------------------------------------
    def test_mark_as_applied_is_offered_only_while_the_record_is_in_preparation(self):
        offered = self.results['mark_as_applied']['collapsedByStatus']
        self.assertEqual(offered['Preparation'], 1)
        for status in APPLICATION_STATUSES[1:]:
            self.assertEqual(offered[status], 0, status + ' must not offer it')
        self.assertTrue(self.results['mark_as_applied']['inExpandedBody'])

    def test_the_card_says_plainly_that_nothing_is_submitted(self):
        self.assertTrue(self.results['mark_as_applied']['neverSubmits'])

    def test_the_preparation_block_reports_the_package_honestly(self):
        block = self.results['preparation_block']
        self.assertIn('CV: CV_EN.pdf', block['ready'])
        self.assertIn('Cover Letter: Motivation_EN.pdf', block['ready'])
        self.assertIn('CV: not attached', block['bare'])
        self.assertIn('Cover Letter: not attached', block['bare'])
        self.assertIn('Mark as Applied', block['bare'],
                      'an empty package never blocks the transition')

    # -- documents ---------------------------------------------------------
    def test_the_open_card_lists_every_document_with_its_three_actions(self):
        documents = self.results['documents_on_open_card']
        self.assertEqual(documents['rows'], 3)
        self.assertEqual(documents['kinds'],
                         ['CV / Resume', 'Cover Letter', 'Certificate / Reference'])
        self.assertEqual(documents['actions'][0], ['Open', 'Replace', 'Remove'])
        self.assertEqual(documents['actions'][2], ['Replace', 'Remove'],
                         'a file that is gone cannot be opened, and must not pretend it can')
        self.assertIn('file missing', documents['bodyText'])
        self.assertEqual(documents['attachButtons'], 1)

    def test_a_record_with_no_documents_says_so_instead_of_inventing_one(self):
        empty = self.results['documents_empty_state']
        self.assertIn('No application documents attached', empty['text'])
        self.assertEqual(empty['rows'], 0)
        self.assertEqual(empty['attachButtons'], 1, 'and documents can still be added')

    # -- the rest ----------------------------------------------------------
    def test_a_closed_application_is_tinted_as_over_and_never_as_live(self):
        tint = self.results['tracked_tint']
        for status in APPLICATION_STATUSES:
            self.assertEqual(tint[status]['badge'], status, 'the status is said in words')
            if status in CLOSED_STATUSES:
                self.assertIn('tracked-closed', tint[status]['className'])
                self.assertNotIn('tracked-active', tint[status]['className'])
            else:
                self.assertIn('tracked-active', tint[status]['className'])

    def test_the_stage_counters_repaint_from_the_backend_without_fetching(self):
        counters = self.results['pipeline_counters']
        self.assertEqual([c['status'] for c in counters['first']], APPLICATION_STATUSES)
        self.assertEqual([c['count'] for c in counters['first']][1], '4')
        self.assertEqual([c['count'] for c in counters['second']][1], '0')
        self.assertEqual([c['count'] for c in counters['second']][2], '9')
        self.assertEqual(counters['fetches'], 0)

    def test_a_record_that_was_never_sent_shows_no_applied_date(self):
        line = self.results['applied_line']
        self.assertEqual(line['never'], '')
        self.assertIn('Applied:', line['sent'])


if __name__ == '__main__':
    unittest.main()
