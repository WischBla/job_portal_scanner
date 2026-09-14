"""The Jobs list is a review queue: closed cards, and a visible application state.

Three layers, the same three the job-card UX has always been tested in:

``CollapsedCardTests``
    Runs the real ``static/app.js`` in Node against a small but genuine DOM
    (``tests/js/collapsed_cards_harness.js``) and measures what a card actually
    does - built closed, opened once, closed again, and never moving the page
    or the list while it does.  Skipped where Node is not installed.

``CollapsedCardContractTests``
    Structural guarantees read off the files that ship: the open/closed state
    is memory-only, toggling touches one card, and the tint that marks a
    tracked application is subtle and is never the only thing saying so.

``ApplicationStateApiTests``
    The Jobs endpoint carries the tracking state of the existing Applications
    records - in one query, for every filter, including the closed ones.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app as backend
from jobscanner import applications as applications_mod
from jobscanner import db as jsdb
from jobscanner import jobs_service
from jobscanner.repository import JobRepository
from jobscanner.schema_v2 import APPLICATION_STATUSES, CLOSED_STATUSES
from tests.helpers import STRONG_DESCRIPTION, TempDatabase, make_job

BASE_DIR = Path(__file__).resolve().parent.parent
APP_JS = BASE_DIR / 'static' / 'app.js'
STYLES = BASE_DIR / 'static' / 'styles.css'
HARNESS = Path(__file__).resolve().parent / 'js' / 'collapsed_cards_harness.js'
NODE = shutil.which('node')

ACTIVE_STATUSES = [s for s in APPLICATION_STATUSES if s not in CLOSED_STATUSES]


@unittest.skipUnless(NODE, 'Node is not installed; the frontend behaviour tests need it.')
class CollapsedCardTests(unittest.TestCase):
    """What a card really does, run through the shipped frontend."""

    @classmethod
    def setUpClass(cls):
        result = subprocess.run([NODE, str(HARNESS)], capture_output=True, text=True,
                                timeout=60, check=False)
        if result.returncode != 0:
            raise AssertionError('harness failed:\n' + result.stdout + result.stderr)
        cls.results = json.loads(result.stdout)

    # -- closed by default -------------------------------------------------
    def test_a_card_is_built_closed(self):
        card = self.results['collapsed_by_default']
        self.assertTrue(card['bodyHidden'])
        self.assertEqual(card['ariaExpanded'], 'false')
        self.assertNotIn('open', card['cardClass'].split())
        self.assertEqual(card['expandedSet'], 0)

    def test_a_closed_card_has_not_built_its_detail_yet(self):
        """The point of the exercise: several hundred rows, no detail DOM."""
        self.assertEqual(self.results['collapsed_by_default']['bodyChildren'], 0)

    def test_the_control_points_at_what_it_opens(self):
        card = self.results['collapsed_by_default']
        self.assertTrue(card['ariaControls'])
        self.assertEqual(card['ariaControls'], card['bodyId'])

    def test_a_closed_card_still_shows_what_a_decision_needs(self):
        text = self.results['collapsed_summary']['text']
        for expected in ('Principal Technical Program Manager',    # title
                         'AWS',                                    # company
                         'Zurich',                                 # location
                         '82', 'Strong',                           # fit and band
                         'Evidence: HIGH',                         # confidence
                         'APPLICATION · INTERVIEW',                # tracking state
                         'saved',                                  # still saved
                         'needs enrichment'):                      # still incomplete
            self.assertIn(expected, text)

    def test_a_closed_card_shows_none_of_the_detail(self):
        text = self.results['collapsed_by_default']['cardText']
        for hidden in ('Why it matches', 'Potential concerns', 'Base match score',
                       'Estimated compensation', 'Your verdict', 'Ignore', 'Apply'):
            self.assertNotIn(hidden, text)

    # -- expanding / collapsing -------------------------------------------
    def test_expanding_reveals_the_detail(self):
        opened = self.results['expand_and_collapse']['opened']
        self.assertFalse(opened['hidden'])
        self.assertEqual(opened['aria'], 'true')
        self.assertGreater(opened['children'], 0)
        self.assertIn('open', opened['cardClass'].split())
        self.assertTrue(opened['inExpandedSet'])

    def test_an_open_card_shows_everything_it_used_to(self):
        text = self.results['expand_and_collapse']['opened']['text']
        for expected in ('Why it matches', 'Potential concerns',
                         'Base match score', 'Operating style', 'Career direction',
                         'Personal fit score', 'Confidence',
                         'Estimated compensation', 'Details', 'Enrichment',
                         'Your verdict', 'YES', 'MAYBE', 'NO',
                         'Open job', 'Save', 'Apply', 'Analysis',
                         'Track application', 'Ignore'):
            self.assertIn(expected, text)

    def test_collapsing_puts_the_detail_away_again(self):
        closed = self.results['expand_and_collapse']['closed']
        self.assertTrue(closed['hidden'])
        self.assertEqual(closed['aria'], 'false')
        self.assertNotIn('open', closed['cardClass'].split())
        self.assertFalse(closed['inExpandedSet'])

    def test_the_detail_is_built_once_and_then_kept(self):
        closed = self.results['expand_and_collapse']['closed']
        self.assertGreater(closed['children'], 0)
        self.assertTrue(closed['sameDetail'])

    def test_toggling_changes_neither_the_order_nor_any_other_card(self):
        outcome = self.results['expand_and_collapse']
        self.assertEqual(outcome['orderBefore'], outcome['orderAfter'])
        self.assertTrue(outcome['sameNodes'])       # the list was not rebuilt

    def test_toggling_asks_the_server_for_nothing(self):
        self.assertEqual(self.results['expand_and_collapse']['fetches'], 0)
        self.assertEqual(self.results['dom_weight']['fetches'], 0)

    # -- what may toggle a card -------------------------------------------
    def test_a_safe_part_of_the_header_opens_and_closes_the_card(self):
        clicks = self.results['header_click']
        self.assertTrue(clicks['afterMeta'])
        self.assertFalse(clicks['afterSecondMeta'])

    def test_the_chevron_opens_the_card_exactly_once(self):
        """The button's own handler runs and the header must not undo it."""
        self.assertTrue(self.results['header_click']['afterChevron'])

    def test_a_button_in_the_header_never_toggles_the_card(self):
        clicks = self.results['header_click']
        self.assertTrue(clicks['unchangedByOtherButton'])
        self.assertTrue(clicks['isActionForButton'])
        self.assertTrue(clicks['isActionForChevron'])
        self.assertFalse(clicks['isActionForMeta'])

    # -- the application treatment ----------------------------------------
    def test_a_live_application_tints_its_card(self):
        cards = self.results['application_treatment']['cards']
        for status in ACTIVE_STATUSES:
            self.assertTrue(cards[status]['active'], status)
            self.assertFalse(cards[status]['closed'], status)

    def test_rejected_is_not_shown_as_a_live_application(self):
        card = self.results['application_treatment']['cards']['Rejected']
        self.assertFalse(card['active'])
        self.assertTrue(card['closed'])

    def test_withdrawn_is_not_shown_as_a_live_application_either(self):
        card = self.results['application_treatment']['cards']['Withdrawn']
        self.assertFalse(card['active'])
        self.assertTrue(card['closed'])

    def test_every_tracked_job_says_so_in_words(self):
        cards = self.results['application_treatment']['cards']
        for status in APPLICATION_STATUSES:
            self.assertEqual(cards[status]['badge'],
                             'APPLICATION · ' + status.upper())

    def test_a_closed_application_keeps_its_badge(self):
        cards = self.results['application_treatment']['cards']
        self.assertEqual(cards['Rejected']['badge'], 'APPLICATION · REJECTED')
        self.assertEqual(cards['Withdrawn']['badge'], 'APPLICATION · WITHDRAWN')

    def test_the_badge_is_visible_without_opening_the_card(self):
        cards = self.results['application_treatment']['cards']
        for status in APPLICATION_STATUSES:
            self.assertTrue(cards[status]['bodyHidden'], status)
        self.assertIn('APPLICATION · INTERVIEW', self.results['collapsed_summary']['text'])

    def test_an_untracked_job_gets_no_treatment_at_all(self):
        plain = self.results['application_treatment']['untracked']
        self.assertFalse(plain['tracked'])
        self.assertIsNone(plain['badge'])

    # -- the viewport ------------------------------------------------------
    def test_opening_a_card_keeps_it_on_the_same_pixel_row(self):
        kept = self.results['expand_keeps_the_card_still']
        self.assertEqual(kept['topBefore'], kept['topAfter'])

    def test_closing_a_card_at_the_bottom_used_to_move_the_page(self):
        """The same shape as the Ignore regression: the document gets shorter
        and the browser clamps the scroll."""
        bare = self.results['collapse_at_the_bottom']['unanchored']
        self.assertGreater(bare['scrollBefore'], bare['scrollAfter'])
        self.assertNotEqual(bare['drift'], 0)

    def test_closing_a_card_no_longer_moves_the_page(self):
        held = self.results['collapse_at_the_bottom']['anchored']
        self.assertEqual(held['scrollBefore'], held['scrollAfter'])
        self.assertEqual(held['drift'], 0)
        self.assertGreater(held['heldBySpacer'], 0)   # the spacer did the work

    # -- what it costs -----------------------------------------------------
    def test_a_closed_list_is_a_fraction_of_the_dom_of_an_open_one(self):
        weight = self.results['dom_weight']
        self.assertLess(weight['collapsed'] * 3, weight['expanded'])

    # -- an in-place update ------------------------------------------------
    def test_redrawing_a_card_keeps_it_open_if_it_was_open(self):
        """A verdict or a Save replaces one card; it must not close it."""
        rebuilt = self.results['rebuild_keeps_open_state']
        self.assertTrue(rebuilt['open'])
        self.assertIn('open', rebuilt['openClass'].split())
        self.assertTrue(rebuilt['shut'])


class CollapsedCardContractTests(unittest.TestCase):
    """Structural guarantees, read off the files that actually ship."""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding='utf-8')
        cls.css = STYLES.read_text(encoding='utf-8')

    def _body_of(self, name):
        start = self.js.index('function {0}('.format(name))
        rest = self.js[start + 10:]
        end = rest.find('\nfunction ')
        second = rest.find('\nasync function ')
        if second != -1 and (end == -1 or second < end):
            end = second
        return rest[:end if end != -1 else len(rest)]

    def _rule(self, selector):
        match = re.search(re.escape(selector) + r'\s*\{([^}]*)\}', self.css)
        self.assertIsNotNone(match, 'no rule for ' + selector)
        return match.group(1)

    def test_the_open_set_starts_empty_on_every_load(self):
        self.assertIn('expanded: new Set()', self.js)

    def test_the_open_state_is_never_stored_anywhere(self):
        for persisted in ('localStorage', 'sessionStorage', 'document.cookie', 'indexedDB'):
            self.assertNotIn(persisted, self.js)
        body = self._body_of('toggleJob')
        for call in ('api(', 'fetch(', 'loadJobs('):
            self.assertNotIn(call, body)

    def test_toggling_changes_one_card_and_never_rebuilds_the_list(self):
        body = self._body_of('toggleJob')
        self.assertIn('cardNode(job.id)', body)
        self.assertNotIn('renderJobs()', body)

    def test_toggling_holds_the_page_the_way_ignore_does(self):
        self.assertIn('keepingPlace(job.id', self._body_of('toggleJob'))
        self.assertIn('keepingPlace(anchorId', self._body_of('removeCard'))

    def test_the_card_body_is_filled_on_demand(self):
        card = self._body_of('jobCard')
        self.assertIn('hidden: !open', card)
        self.assertIn('if (open) fillCardBody(body, job)', card)

    def test_the_control_carries_its_accessible_state(self):
        card = self._body_of('jobCard')
        self.assertIn("'aria-expanded'", card)
        self.assertIn("'aria-controls'", card)
        self.assertIn("aria-expanded", self._body_of('toggleJob'))

    def test_only_the_inert_parts_of_the_header_toggle(self):
        self.assertIn('if (!isActionTarget(event.target)) toggleJob(job)', self.js)
        guard = self._body_of('isActionTarget')
        for tag in ("'button'", "'a'", "'input'", "'select'", "'textarea'", "'label'"):
            self.assertIn(tag, guard)

    def test_the_application_state_is_shown_as_text(self):
        self.assertIn("'APPLICATION · '", self.js)
        self.assertIn('String(job.application_status).toUpperCase()', self.js)

    def test_the_tint_is_never_the_only_indicator(self):
        """Both come from the same condition, so one cannot appear alone."""
        badge = self._body_of('applicationBadge')
        tint = self._body_of('trackedClass')
        self.assertIn('job.has_application', badge)
        self.assertIn('job.has_application', tint)
        self.assertIn('job.application_active', tint)

    def test_the_active_tint_is_a_pale_green_and_not_a_saturated_one(self):
        rule = self._rule('.card.tracked-active')
        background = re.search(r'background:\s*#([0-9a-fA-F]{6})', rule)
        self.assertIsNotNone(background)
        red, green, blue = (int(background.group(1)[i:i + 2], 16) for i in (0, 2, 4))
        self.assertGreater(green, red)                     # it is green
        self.assertGreaterEqual(min(red, green, blue), 0xE8)     # and very pale
        self.assertLessEqual(green - min(red, blue), 16)        # barely saturated

    def test_a_closed_application_gets_no_green_at_all(self):
        rule = self._rule('.card.tracked-closed')
        for colour in re.findall(r'#([0-9a-fA-F]{6})', rule):
            red, green, blue = (int(colour[i:i + 2], 16) for i in (0, 2, 4))
            self.assertEqual((red, green, blue), (red, red, red),
                             'a closed application must be grey, not ' + colour)

    def test_a_closed_card_is_the_compact_one(self):
        self.assertIn('.card.open { padding', self.css)


class ApplicationStateApiTests(unittest.TestCase):
    """The Jobs endpoint carries the state of the existing Applications rows."""

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

    def track(self, job_id, status=''):
        application = self.client.post('/api/jobs/{0}/application'.format(job_id)).json()
        if status:
            self.client.put('/api/applications/{0}'.format(application['id']),
                            json={'status': status})
        return application

    def card(self, job_id):
        return next(job for job in self.client.get('/api/jobs').json()['jobs']
                    if job['id'] == job_id)

    # -- the linkage -------------------------------------------------------
    def test_an_untracked_job_says_so(self):
        card = self.card(self.seed())
        self.assertFalse(card['has_application'])
        self.assertEqual(card['application_status'], '')
        self.assertFalse(card['application_active'])

    def test_a_tracked_job_carries_its_status(self):
        job_id = self.seed()
        self.track(job_id)
        card = self.card(job_id)
        self.assertTrue(card['has_application'])
        self.assertEqual(card['application_status'], 'Preparation')
        self.assertTrue(card['application_active'])

    def test_the_status_is_the_one_in_the_applications_table(self):
        """One source of truth: the pipeline record, not a copy of it."""
        job_id = self.seed()
        application = self.track(job_id)
        for status in APPLICATION_STATUSES:
            applications_mod.update(application['id'], {'status': status})
            self.assertEqual(self.card(job_id)['application_status'], status)

    def test_every_live_stage_is_reported_as_active(self):
        job_id = self.seed()
        application = self.track(job_id)
        for status in ACTIVE_STATUSES:
            applications_mod.update(application['id'], {'status': status})
            self.assertTrue(self.card(job_id)['application_active'], status)

    def test_rejected_is_tracked_but_not_active(self):
        job_id = self.seed()
        application = self.track(job_id)
        applications_mod.update(application['id'], {'status': 'Rejected'})
        card = self.card(job_id)
        self.assertTrue(card['has_application'])
        self.assertEqual(card['application_status'], 'Rejected')
        self.assertFalse(card['application_active'])

    def test_withdrawn_is_tracked_but_not_active(self):
        job_id = self.seed()
        application = self.track(job_id)
        applications_mod.update(application['id'], {'status': 'Withdrawn'})
        card = self.card(job_id)
        self.assertTrue(card['has_application'])
        self.assertEqual(card['application_status'], 'Withdrawn')
        self.assertFalse(card['application_active'])

    def test_a_legacy_status_label_is_reported_in_the_current_vocabulary(self):
        job_id = self.seed()
        application = self.track(job_id)
        with jsdb.connect() as conn:
            conn.execute('UPDATE applications SET status=? WHERE id=?',
                         ('Absage', application['id']))
            conn.commit()
        card = self.card(job_id)
        self.assertEqual(card['application_status'], 'Rejected')
        self.assertFalse(card['application_active'])

    def test_an_application_created_against_a_job_is_found_from_the_job(self):
        """The link is written from both ends; either one is enough."""
        job_id = self.seed()
        applications_mod.create({'company': 'Example AG', 'position': 'Head of Platform',
                                 'status': 'Screening', 'job_id': job_id})
        card = self.card(job_id)
        self.assertTrue(card['has_application'])
        self.assertEqual(card['application_status'], 'Screening')

    def test_the_single_job_endpoint_carries_it_too(self):
        job_id = self.seed()
        self.track(job_id)
        card = self.client.get('/api/jobs/{0}'.format(job_id)).json()
        self.assertEqual(card['application_status'], 'Preparation')

    # -- filters -----------------------------------------------------------
    def test_the_status_survives_every_filter(self):
        job_id = self.seed(score=90)
        application = self.track(job_id)
        applications_mod.update(application['id'], {'status': 'Interview'})
        self.client.post('/api/jobs/{0}/state'.format(job_id), json={'state': 'SAVED'})
        for view in ('all', 'top', 'saved'):
            jobs = self.client.get('/api/jobs?filter={0}'.format(view)).json()['jobs']
            card = next(job for job in jobs if job['id'] == job_id)
            self.assertEqual(card['application_status'], 'Interview', view)
            self.assertTrue(card['application_active'], view)

    def test_an_ignored_job_keeps_its_application_state(self):
        job_id = self.seed()
        application = self.track(job_id)
        applications_mod.update(application['id'], {'status': 'Rejected'})
        self.client.post('/api/jobs/{0}/state'.format(job_id), json={'state': 'IGNORED'})
        card = next(job for job in self.client.get('/api/jobs?state=IGNORED').json()['jobs']
                    if job['id'] == job_id)
        self.assertEqual(card['application_status'], 'Rejected')
        self.assertFalse(card['application_active'])

    # -- one query, not one per card ---------------------------------------
    def _application_queries(self, count):
        for index in range(count):
            job_id = self.seed(external_id=str(index + 1), score=80 - index,
                               title='Head of Platform {0}'.format(index))
            self.track(job_id)
        statements = []
        with jsdb.connect() as conn:
            conn.set_trace_callback(statements.append)
            cards = jobs_service.list_cards(conn)
            conn.set_trace_callback(None)
        self.assertEqual(len(cards), count)
        self.assertTrue(all(card['has_application'] for card in cards))
        return [sql for sql in statements
                if re.search(r'\bapplications\b', sql, re.IGNORECASE)]

    def test_the_list_reads_the_application_state_in_one_query(self):
        self.assertEqual(len(self._application_queries(1)), 1)

    def test_twenty_tracked_jobs_still_cost_exactly_one_query(self):
        """No N+1: the status is a column of the list query, not a lookup."""
        self.assertEqual(len(self._application_queries(20)), 1)


if __name__ == '__main__':
    unittest.main()
