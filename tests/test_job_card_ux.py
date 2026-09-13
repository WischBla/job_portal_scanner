"""Job-card actions: the page must not move, and Ignore must be reversible.

Three layers, because the defect lived in three places:

``ViewportAnchorTests``
    Runs the real ``static/app.js`` in Node against a small model of the two
    browser behaviours that caused the regression - a card's position, and the
    scroll being clamped when the document gets shorter.  Skipped where Node
    is not installed; the app itself never needs it.

``FrontendContractTests``
    Reads the shipped frontend and asserts the structural properties that keep
    the regression from coming back: no button can submit, and no card action
    rebuilds the whole list.

``JobActionApiTests``
    The endpoints behind the buttons: a verdict persists and re-sorts nothing,
    Ignore is undoable to the exact state the job had, and an ignored job
    stays reachable and restorable.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app as backend
from jobscanner import db as jsdb
from jobscanner.repository import JobRepository
from tests.helpers import STRONG_DESCRIPTION, TempDatabase, make_job

BASE_DIR = Path(__file__).resolve().parent.parent
APP_JS = BASE_DIR / 'static' / 'app.js'
INDEX_HTML = BASE_DIR / 'static' / 'index.html'
HARNESS = Path(__file__).resolve().parent / 'js' / 'viewport_anchor_harness.js'
NODE = shutil.which('node')

#: One card, roughly life-size - the unit the harness measures movement in.
CARD_HEIGHT = 660


@unittest.skipUnless(NODE, 'Node is not installed; the frontend geometry tests need it.')
class ViewportAnchorTests(unittest.TestCase):
    """What the anchoring actually does to the viewport."""

    @classmethod
    def setUpClass(cls):
        result = subprocess.run([NODE, str(HARNESS)], capture_output=True, text=True,
                                timeout=60, check=False)
        if result.returncode != 0:
            raise AssertionError('harness failed:\n' + result.stdout + result.stderr)
        cls.results = json.loads(result.stdout)

    def test_the_regression_is_reproduced_by_the_model(self):
        """Without an anchor, removing a card at the bottom moves the page."""
        moved = self.results['unanchored_removal_moves_the_page']
        self.assertGreater(moved['before'], moved['after'])

    def test_repeated_ignores_used_to_walk_the_list_back_to_the_top(self):
        trail = self.results['repeated_ignores_at_the_bottom']['unanchored']
        self.assertEqual(trail[0], max(trail))
        self.assertEqual(trail[-1], 0)          # literally the top of the page

    def test_repeated_ignores_no_longer_move_the_page_at_all(self):
        """Same list, same clicks, through the real handler: the spacer holds
        the height the removed cards used to provide, so the page stays put
        and the card next to each one never shifts."""
        outcome = self.results['repeated_ignores_at_the_bottom']
        self.assertEqual(len(set(outcome['anchored'])), 1)
        self.assertEqual(outcome['anchored'][0], max(outcome['unanchored']))
        self.assertEqual(outcome['neighbourDrift'], [0, 0, 0, 0])

    def test_the_spacer_lets_go_once_it_can_do_so_without_moving_anything(self):
        spacer = self.results['spacer_lets_go']
        self.assertGreater(spacer['heldAfterIgnore'], 0)
        self.assertEqual(spacer['heldAfterScrolling'], 0)

    def test_removing_a_card_keeps_its_neighbour_on_the_same_pixel_row(self):
        kept = self.results['anchored_removal_keeps_the_neighbour_still']
        self.assertEqual(kept['topBefore'], kept['topAfter'])

    def test_ignoring_mid_list_no_longer_pulls_the_next_card_under_the_cursor(self):
        """The accident: the next card's Ignore button used to arrive exactly
        where the cursor already was."""
        jump = self.results['ignore_in_the_middle']
        self.assertEqual(jump['unanchoredJump'], -CARD_HEIGHT)
        self.assertEqual(jump['anchoredJump'], 0)

    def test_a_verdict_growing_its_card_does_not_move_that_card(self):
        grown = self.results['card_growth_keeps_the_card_still']
        self.assertEqual(grown['topBefore'], grown['topAfter'])

    def test_without_a_card_to_anchor_on_the_scroll_position_is_restored(self):
        self.assertEqual(self.results['scroll_fallback']['scrollY'], 1200)

    def test_buttons_never_submit_unless_asked_to(self):
        types = self.results['button_types']
        self.assertEqual(types['plain'], 'button')
        self.assertEqual(types['withClass'], 'button')
        self.assertEqual(types['explicit'], 'submit')   # an explicit type still wins

    def test_counts_are_adjusted_and_restored_symmetrically(self):
        counts = self.results['counts']
        self.assertEqual(counts['afterIgnore'],
                         {'total': 9, 'ignored': 3, 'saved': 0, 'excellent': 0, 'strong': 3})
        self.assertEqual(counts['afterUndo'],
                         {'total': 10, 'ignored': 2, 'saved': 1, 'excellent': 1, 'strong': 3})


class FrontendContractTests(unittest.TestCase):
    """Structural guarantees, read off the files that actually ship."""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding='utf-8')
        cls.html = INDEX_HTML.read_text(encoding='utf-8')

    def _body_of(self, name):
        """The source of one top-level function, up to the next one."""
        start = self.js.index('function {0}('.format(name))
        rest = self.js[start + 10:]
        end = rest.find('\nfunction ')
        second = rest.find('\nasync function ')
        if second != -1 and (end == -1 or second < end):
            end = second
        return rest[:end if end != -1 else len(rest)]

    def test_every_static_button_declares_its_type(self):
        buttons = [line for line in self.html.splitlines() if '<button' in line]
        self.assertTrue(buttons)
        for line in buttons:
            self.assertIn('type="button"', line, 'submit-by-default button: ' + line.strip())

    def test_created_buttons_default_to_type_button(self):
        self.assertIn("if (tag === 'button') node.type = 'button';", self.js)

    def test_no_card_action_navigates_or_reloads(self):
        for forbidden in ('location.href', 'location.reload', 'location.assign',
                          'window.location ='):
            self.assertNotIn(forbidden, self.js)

    def test_a_verdict_updates_one_card_and_never_rebuilds_the_list(self):
        body = self._body_of('setFeedback')
        self.assertIn('replaceCard(job)', body)
        self.assertNotIn('renderJobs()', body)
        self.assertNotIn('loadJobs(', body)

    def test_save_updates_one_card_instead_of_reloading_the_list(self):
        body = self._body_of('setJobState')
        self.assertIn('replaceCard(job)', body)

    def test_ignore_is_undoable_from_the_toast(self):
        body = self._body_of('ignoreJob')
        self.assertIn("label: 'UNDO'", body)
        self.assertIn('undoIgnore(job, previous, index)', body)
        self.assertIn('removeCard(node, anchorId)', body)   # the page stays put

    def test_ignore_asks_for_no_confirmation_dialog(self):
        body = self._body_of('ignoreJob')
        for blocking in ('confirm(', 'dialog(', 'alert('):
            self.assertNotIn(blocking, body)

    def test_undo_restores_the_state_the_job_had(self):
        body = self._body_of('undoIgnore')
        self.assertIn('state: previous', body)

    def test_every_card_carries_its_job_id(self):
        self.assertIn("'data-job-id': job.id", self.js)

    def test_ignored_jobs_are_reachable_without_a_new_navigation_item(self):
        self.assertIn('id="ignored-btn"', self.html)
        self.assertIn("api('/api/jobs' + (ignored ? '?state=IGNORED' : ''))", self.js)
        self.assertIn("text: 'Restore'", self.js)
        self.assertEqual(self.html.count('class="tab"'), 4)


class JobActionApiTests(unittest.TestCase):
    """The endpoints the buttons call."""

    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.client = TestClient(backend.app)

    def seed(self, external_id='1', score=80, title='Head of Platform Engineering'):
        job = make_job(title=title, company='Example AG', location='Zurich, Switzerland',
                       description=STRONG_DESCRIPTION, external_id=external_id)
        scored = {'score': score, 'label': 'Strong', 'reasons': [], 'concerns': [],
                  'terms': [], 'breakdown': []}
        with jsdb.connect() as conn:
            job_id, _ = JobRepository(conn).upsert(job, scored)
            conn.commit()
        return job_id

    def state_of(self, job_id):
        with jsdb.connect() as conn:
            return conn.execute('SELECT state FROM discovered_jobs WHERE id=?',
                                (job_id,)).fetchone()[0]

    def ids(self, query=''):
        return [job['id'] for job in self.client.get('/api/jobs' + query).json()['jobs']]

    # -- verdicts ----------------------------------------------------------
    def test_a_verdict_persists(self):
        job_id = self.seed()
        card = self.client.post('/api/jobs/{0}/feedback'.format(job_id),
                                json={'verdict': 'MAYBE'}).json()
        self.assertEqual(card['feedback'], 'MAYBE')
        self.assertEqual(self.client.get('/api/jobs/{0}'.format(job_id)).json()['feedback'],
                         'MAYBE')

    def test_a_verdict_changes_neither_the_score_nor_the_order(self):
        first = self.seed(external_id='1', score=90, title='Director Platform Engineering')
        middle = self.seed(external_id='2', score=80, title='Head of SRE')
        last = self.seed(external_id='3', score=70, title='Head of Cloud Operations')
        before = self.ids()
        scores = {job['id']: job['score'] for job in self.client.get('/api/jobs').json()['jobs']}

        for verdict in ('NO', 'MAYBE', 'YES'):
            self.client.post('/api/jobs/{0}/feedback'.format(middle), json={'verdict': verdict})

        self.assertEqual(self.ids(), before)
        self.assertEqual(before, [first, middle, last])
        after = {job['id']: job['score'] for job in self.client.get('/api/jobs').json()['jobs']}
        self.assertEqual(after, scores)

    def test_a_verdict_never_removes_a_job_from_the_list(self):
        job_id = self.seed()
        self.client.post('/api/jobs/{0}/feedback'.format(job_id), json={'verdict': 'NO'})
        self.assertIn(job_id, self.ids())

    # -- ignore / undo -----------------------------------------------------
    def test_ignoring_takes_the_job_out_of_the_list(self):
        job_id = self.seed()
        self.client.post('/api/jobs/{0}/state'.format(job_id), json={'state': 'IGNORED'})
        self.assertNotIn(job_id, self.ids())

    def test_undo_puts_a_saved_job_back_as_saved(self):
        """The accident that started this: the job that was ignored by mistake
        was a saved one, and coming back as merely SEEN would lose that."""
        job_id = self.seed()
        self.client.post('/api/jobs/{0}/state'.format(job_id), json={'state': 'SAVED'})
        self.client.post('/api/jobs/{0}/feedback'.format(job_id), json={'verdict': 'YES'})
        before = self.client.get('/api/jobs/{0}'.format(job_id)).json()

        self.client.post('/api/jobs/{0}/state'.format(job_id), json={'state': 'IGNORED'})
        self.assertEqual(self.state_of(job_id), 'IGNORED')

        undone = self.client.post('/api/jobs/{0}/state'.format(job_id),
                                  json={'state': 'SAVED'}).json()
        self.assertEqual(undone['state'], 'SAVED')
        self.assertIn(job_id, self.ids())
        # nothing else about the job moved
        after = self.client.get('/api/jobs/{0}'.format(job_id)).json()
        self.assertEqual(after['score'], before['score'])
        self.assertEqual(after['feedback'], 'YES')
        self.assertEqual(after['personal_fit'], before['personal_fit'])

    def test_undo_returns_the_full_card_so_it_can_be_put_back_in_place(self):
        job_id = self.seed()
        self.client.post('/api/jobs/{0}/state'.format(job_id), json={'state': 'IGNORED'})
        card = self.client.post('/api/jobs/{0}/state'.format(job_id),
                                json={'state': 'SEEN'}).json()
        for key in ('id', 'title', 'company', 'score', 'classification', 'state'):
            self.assertIn(key, card)

    # -- the ignored list --------------------------------------------------
    def test_ignored_jobs_stay_reachable(self):
        kept = self.seed(external_id='1')
        ignored = self.seed(external_id='2', title='Head of SRE')
        self.client.post('/api/jobs/{0}/state'.format(ignored), json={'state': 'IGNORED'})

        self.assertEqual(self.ids('?state=IGNORED'), [ignored])
        self.assertEqual(self.ids(), [kept])
        self.assertEqual(self.client.get('/api/jobs').json()['counts']['ignored'], 1)

    def test_an_ignored_job_can_be_restored_from_that_list(self):
        job_id = self.seed()
        self.client.post('/api/jobs/{0}/state'.format(job_id), json={'state': 'IGNORED'})
        self.client.post('/api/jobs/{0}/state'.format(job_id), json={'state': 'SEEN'})
        self.assertEqual(self.ids('?state=IGNORED'), [])
        self.assertIn(job_id, self.ids())

    def test_restoring_keeps_the_link_to_an_application(self):
        job_id = self.seed()
        application = self.client.post('/api/jobs/{0}/application'.format(job_id)).json()
        self.client.post('/api/jobs/{0}/state'.format(job_id), json={'state': 'IGNORED'})
        card = self.client.post('/api/jobs/{0}/state'.format(job_id),
                                json={'state': 'SEEN'}).json()
        self.assertEqual(card['application_id'], application['id'])


if __name__ == '__main__':
    unittest.main()
