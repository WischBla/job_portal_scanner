"""The recommended "Swiss Leadership Search" profile.

These tests pin down the promises the preset makes:

* geography stays a hard gate,
* nothing else is allowed to quietly become one (title, salary, city, language),
* and the score ranks opportunities instead of deciding the career.
"""

import unittest

from jobscanner import db as jsdb
from jobscanner import presets
from jobscanner.filters import HardFilter
from jobscanner.profile import sanitize
from jobscanner.scoring import MatchScorer
from tests.helpers import STRONG_DESCRIPTION, TempDatabase, make_job

LEADERSHIP_DESCRIPTION = (
    'You own technology operations for a global platform organisation: AWS cloud '
    'infrastructure, platform engineering, SRE practice, SLO and observability, '
    'incident management, CI/CD and DevSecOps. You drive an engineering '
    'transformation programme, work in a matrix across teams, own stakeholder '
    'management up to executive level and set the technology strategy. '
    'AI and automation initiatives are part of the roadmap.'
)


def recommended():
    return sanitize(presets.recommended_profile())


class PresetValuesTests(unittest.TestCase):
    """Section 1-11 of the brief, expressed as assertions."""

    def setUp(self):
        self.profile = recommended()

    def test_preset_is_registered_and_recommended(self):
        preset = presets.recommended_preset()
        self.assertEqual(preset['name'], 'Swiss Leadership Search')
        self.assertTrue(preset['is_recommended'])

    def test_minimum_score_is_62_not_75_80_or_82(self):
        self.assertEqual(self.profile['minimum_match_score'], 62)
        self.assertNotIn(self.profile['minimum_match_score'], (75, 80, 82))

    def test_salary_is_a_ranking_signal_and_may_be_missing(self):
        self.assertEqual(self.profile['salary_mode'], 'ranking')
        self.assertTrue(self.profile['allow_missing_salary'])

    def test_geography_is_switzerland_and_strict(self):
        self.assertEqual(self.profile['allowed_countries'], ['Switzerland'])
        self.assertEqual(self.profile['country_mode'], 'strict')

    def test_preferred_locations_rank_but_do_not_exclude(self):
        self.assertEqual(self.profile['location_filter_mode'], 'ranking')
        for city in ('Zurich', 'Zug', 'Luzern', 'Bern', 'Basel'):
            self.assertIn(city, self.profile['allowed_locations'], city)
        for city in ('St. Gallen', 'Schwyz', 'Aargau'):
            self.assertIn(city, self.profile['optional_locations'], city)
        self.assertEqual(self.profile['tertiary_locations'], ['Lugano'])

    def test_all_three_work_models_are_allowed(self):
        self.assertEqual(self.profile['remote_policy'],
                         {'allow_remote': True, 'allow_hybrid': True, 'allow_onsite': True})
        self.assertEqual(self.profile['hybrid_max_office_days'], 2)

    def test_languages_are_german_and_english_only(self):
        self.assertEqual(sorted(self.profile['language_preferences']), ['English', 'German'])

    def test_default_sorting_is_best_match(self):
        self.assertEqual(self.profile['sort_mode'], 'score')


class GeographyTests(unittest.TestCase):
    """Geography is the one hard gate and it must not leak."""

    def setUp(self):
        self.filter = HardFilter()
        self.profile = recommended()

    def check(self, location, title='Director Platform Engineering', description=None):
        job = make_job(title=title, location=location,
                       description=description if description is not None else LEADERSHIP_DESCRIPTION)
        return self.filter.check(job, self.profile)

    def test_swiss_national_and_remote_variants_are_accepted(self):
        for location in ['Switzerland', 'Remote Switzerland', 'Switzerland - Remote',
                         'Remote within Switzerland', 'Zurich, Switzerland', 'Zug', 'Basel, CH']:
            self.assertIsNone(self.check(location), location)

    def test_an_unlisted_swiss_city_still_gets_through(self):
        """Preferred cities rank. A Swiss job elsewhere must not disappear."""
        for location in ['Lausanne, Switzerland', 'Winterthur, Switzerland', 'Chur, Switzerland']:
            self.assertIsNone(self.check(location), location)

    def test_foreign_and_blanket_regions_are_rejected(self):
        for location in ['Germany', 'Berlin, Germany', 'Austria', 'Vienna, Austria', 'France',
                         'Paris, France', 'Remote Germany', 'Remote Europe', 'Remote EU',
                         'Remote EMEA', 'Europe', 'EMEA', 'DACH']:
            rejection = self.check(location)
            self.assertIsNotNone(rejection, location)
            self.assertIn(rejection['code'], ('not_switzerland', 'country_not_allowed'), location)

    def test_explicit_swiss_eligibility_rescues_a_region_posting(self):
        job = self.check('Remote EMEA',
                         description=LEADERSHIP_DESCRIPTION +
                         ' Candidates must be located in Switzerland for this role.')
        self.assertIsNone(job)


class SeniorityAndRoleTests(unittest.TestCase):
    def setUp(self):
        self.filter = HardFilter()
        self.scorer = MatchScorer()
        self.profile = recommended()

    def job(self, title, **kwargs):
        kwargs.setdefault('location', 'Zurich, Switzerland')
        kwargs.setdefault('description', LEADERSHIP_DESCRIPTION)
        return make_job(title=title, **kwargs)

    def score(self, title, **kwargs):
        return self.scorer.score(self.job(title, **kwargs), self.profile)

    def test_engineering_manager_is_not_automatically_rejected(self):
        for title in ['Engineering Manager', 'Senior Engineering Manager',
                      'Platform Engineering Manager', 'SRE Manager', 'Cloud Engineering Manager',
                      'Technical Program Manager', 'Principal Technical Program Manager']:
            self.assertIsNone(self.filter.check(self.job(title), self.profile), title)

    def test_engineering_manager_can_reach_the_threshold_on_scope(self):
        result = self.score('Engineering Manager, Platform Operations')
        self.assertGreaterEqual(result['score'], self.profile['minimum_match_score'])

    def test_unusual_but_relevant_titles_score_strongly(self):
        for title in ['Director, Technology Enablement', 'Head of Engineering Productivity',
                      'Director, Cloud Transformation', 'Principal Technical Program Manager',
                      'Head of Reliability Engineering', 'Director, Engineering Excellence',
                      'Global Lead Operational Excellence']:
            result = self.score(title)
            self.assertIsNone(self.filter.check(self.job(title), self.profile), title)
            self.assertGreaterEqual(result['score'], 70, '{0} -> {1}'.format(title, result['score']))

    def test_head_of_sales_is_rejected(self):
        rejection = self.filter.check(self.job('Head of Sales'), self.profile)
        self.assertIsNotNone(rejection)
        self.assertEqual(rejection['code'], 'unrelated_function')

    def test_head_of_sales_engineering_is_not_blindly_rejected(self):
        """One overlapping word must not delete a technical leadership role."""
        for title in ['Head of Sales Engineering', 'Director of Engineering, Growth',
                      'Head of Sales Engineering Operations']:
            self.assertIsNone(self.filter.check(self.job(title), self.profile), title)

    def test_junior_levels_are_rejected(self):
        for title in ['Junior DevOps Engineer', 'Graduate Cloud Engineer', 'Intern Platform',
                      'Working Student SRE', 'Trainee Technology Operations',
                      'Entry Level Systems Engineer', 'Apprentice Infrastructure']:
            self.assertIsNotNone(self.filter.check(self.job(title), self.profile), title)

    def test_unrelated_functions_are_rejected(self):
        for title in ['Account Executive DACH', 'Technical Recruiter', 'Talent Acquisition Lead',
                      'HR Business Partner', 'Graphic Designer', 'UX Designer',
                      'Service Desk Agent', '1st Level Support Agent']:
            self.assertIsNotNone(self.filter.check(self.job(title), self.profile), title)

    def test_matrix_leadership_is_not_treated_as_a_gap(self):
        result = self.score('Principal Technical Program Manager')
        self.assertFalse([c for c in result['concerns'] if 'leadership scope unclear' in c])


class SalaryTests(unittest.TestCase):
    def setUp(self):
        self.filter = HardFilter()
        self.scorer = MatchScorer()
        self.profile = recommended()

    def job(self, **kwargs):
        kwargs.setdefault('title', 'Director Platform Engineering')
        kwargs.setdefault('location', 'Zurich, Switzerland')
        kwargs.setdefault('description', LEADERSHIP_DESCRIPTION)
        return make_job(**kwargs)

    def test_missing_salary_never_removes_a_job(self):
        self.assertIsNone(self.filter.check(self.job(), self.profile))

    def test_missing_salary_does_not_kill_an_otherwise_strong_match(self):
        without = self.scorer.score(self.job(), self.profile)
        published = self.scorer.score(
            self.job(salary_min=300000, salary_max=350000, salary_currency='CHF'), self.profile)
        self.assertGreaterEqual(without['score'], self.profile['minimum_match_score'])
        self.assertEqual(without['score'], published['score'])
        self.assertIn('Compensation not published', without['concerns'])

    def test_published_salary_clearly_below_the_range_costs_real_points(self):
        low = self.scorer.score(
            self.job(salary_min=150000, salary_max=180000, salary_currency='CHF'), self.profile)
        high = self.scorer.score(
            self.job(salary_min=300000, salary_max=350000, salary_currency='CHF'), self.profile)
        self.assertLess(low['score'], high['score'] - 10)
        self.assertTrue([c for c in low['concerns'] if 'clearly below' in c])

    def test_a_low_published_salary_is_still_not_deleted_by_default(self):
        job = self.job(salary_min=150000, salary_max=180000, salary_currency='CHF')
        self.assertIsNone(self.filter.check(job, self.profile))

    def test_salary_can_be_made_a_hard_filter_on_request(self):
        profile = sanitize(dict(presets.recommended_profile(), salary_mode='hard'))
        job = self.job(salary_min=150000, salary_max=180000, salary_currency='CHF')
        self.assertEqual(self.filter.check(job, profile)['code'], 'salary_below_minimum')


class ConcernTests(unittest.TestCase):
    def setUp(self):
        self.scorer = MatchScorer()
        self.profile = recommended()

    def score(self, **kwargs):
        kwargs.setdefault('title', 'Director Platform Engineering')
        kwargs.setdefault('location', 'Zurich, Switzerland')
        kwargs.setdefault('description', LEADERSHIP_DESCRIPTION)
        return self.scorer.score(make_job(**kwargs), self.profile)

    def test_a_french_requirement_produces_a_concern_not_a_rejection(self):
        result = self.score(description=LEADERSHIP_DESCRIPTION +
                            ' Fluent French is required for this position.')
        self.assertTrue([c for c in result['concerns'] if 'French' in c])
        self.assertIsNone(HardFilter().check(
            make_job(title='Director Platform Engineering', location='Zurich, Switzerland',
                     description=LEADERSHIP_DESCRIPTION + ' Fluent French is required.'),
            self.profile))

    def test_a_french_speaking_office_alone_is_not_flagged(self):
        result = self.score(description=LEADERSHIP_DESCRIPTION +
                            ' Our Geneva office is French speaking and very welcoming.')
        self.assertFalse([c for c in result['concerns'] if 'French' in c])

    def test_unspecified_office_presence_is_a_concern_not_a_filter(self):
        result = self.score()
        self.assertTrue([c for c in result['concerns'] if 'Office presence not specified' in c])


class PersistenceTests(unittest.TestCase):
    """Sections 18-19: presets live apart from the profile and nothing is lost."""

    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def test_a_fresh_database_starts_on_the_recommended_preset(self):
        with jsdb.connect() as conn:
            profile = jsdb.load_profile(conn)
        self.assertEqual(profile['preset_key'], presets.RECOMMENDED_KEY)
        self.assertEqual(profile['minimum_match_score'], 62)

    def test_the_preset_is_stored_separately_from_the_active_profile(self):
        with jsdb.connect() as conn:
            profile = jsdb.load_profile(conn)
            profile['minimum_match_score'] = 90
            profile['preset_key'] = ''
            jsdb.save_profile(conn, profile)
            stored = jsdb.get_preset(conn, presets.RECOMMENDED_KEY)
            active = jsdb.load_profile(conn)
        self.assertEqual(active['minimum_match_score'], 90)   # the edit stuck
        self.assertEqual(stored['profile']['minimum_match_score'], 62)  # the preset did not move

    def test_a_manually_configured_profile_survives_a_restart(self):
        with jsdb.connect() as conn:
            profile = jsdb.load_profile(conn)
            profile['minimum_match_score'] = 71
            profile['allowed_locations'] = ['Zurich']
            profile['preset_key'] = ''
            jsdb.save_profile(conn, profile)
        jsdb.init_db()      # a restart re-runs every migration
        jsdb.init_db()
        with jsdb.connect() as conn:
            reloaded = jsdb.load_profile(conn)
        self.assertEqual(reloaded['minimum_match_score'], 71)
        self.assertEqual(reloaded['allowed_locations'], ['Zurich'])
        self.assertEqual(reloaded['preset_key'], '')

    def test_the_recommended_preset_can_be_restored(self):
        with jsdb.connect() as conn:
            jsdb.save_profile(conn, dict(jsdb.load_profile(conn),
                                         minimum_match_score=95, preset_key=''))
            restored = jsdb.apply_preset(conn, presets.RECOMMENDED_KEY)
        self.assertEqual(restored['minimum_match_score'], 62)
        self.assertEqual(restored['preset_key'], presets.RECOMMENDED_KEY)
        self.assertEqual(restored['salary_mode'], 'ranking')

    def test_applying_a_preset_keeps_applications_and_job_states(self):
        with jsdb.connect() as conn:
            conn.execute("INSERT INTO applications (company,position,status,created_at,updated_at) "
                         "VALUES ('Roche','Director Ops','Interview 1','2026-01-01','2026-01-01')")
            conn.execute("INSERT INTO discovered_jobs (source,external_id,title,state,"
                         "first_seen,last_seen) VALUES ('X','1','Head of Cloud','SAVED','a','a')")
            conn.commit()
            jsdb.apply_preset(conn, presets.RECOMMENDED_KEY)
        jsdb.init_db()
        with jsdb.connect() as conn:
            # V2 has a fixed pipeline, so the free-text label is mapped onto the
            # closest stage - the row itself and its history survive untouched.
            status, notes = conn.execute('SELECT status, notes FROM applications').fetchone()
            self.assertEqual(status, 'Interview')
            self.assertIn('Interview 1', notes)
            self.assertEqual(conn.execute('SELECT company FROM applications').fetchone()[0],
                             'Roche')
            self.assertEqual(conn.execute('SELECT state FROM discovered_jobs').fetchone()[0],
                             'SAVED')

    def test_unknown_preset_keys_are_refused(self):
        with jsdb.connect() as conn:
            with self.assertRaises(ValueError):
                jsdb.apply_preset(conn, 'does-not-exist')


if __name__ == '__main__':
    unittest.main()
