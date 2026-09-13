"""The behaviour the personal Swiss configuration depends on.

These tests describe the *rules*, never the values: they assert that a
preferred city ranks rather than rejects, that a low-relevance domain costs
points rather than disappearing, that compensation never filters, and that
company priority cannot outrank a better match.  No personal data appears
here - the real values live only in the local database.
"""

import json
import unittest

from jobscanner import jobs_service, profile as profile_mod
from jobscanner.apply import fields
from jobscanner.db import connect, load_profile, save_profile
from jobscanner.filters import HardFilter
from jobscanner.scoring import MatchScorer

from .helpers import STRONG_DESCRIPTION, TempDatabase, make_job, make_profile

SWISS_PROFILE = {
    'country_mode': 'strict',
    'allowed_countries': ['Switzerland'],
    'allowed_locations': ['Zurich'],
    'optional_locations': ['Zug', 'Luzern', 'Bern', 'Basel', 'St. Gallen', 'Schwyz', 'Aargau'],
    'tertiary_locations': ['Geneva', 'Lausanne', 'Lugano'],
    'location_filter_mode': 'ranking',
    'seniority_levels': ['Head of', 'Director', 'Senior Director', 'Principal',
                         'Senior Lead', 'Global Lead', 'Lead'],
    'secondary_titles': ['Senior Engineering Manager', 'Senior Manager',
                         'Principal Technical Program Manager', 'Platform Engineering Lead'],
    'include_titles': ['Technology Operations', 'Platform Engineering',
                       'Site Reliability Engineering', 'Technology Transformation',
                       'Technical Program Management', 'Engineering'],
    'preferred_keywords': ['AWS', 'SRE', 'Kubernetes', 'Observability', 'AIOps'],
    'deprioritized_keywords': ['gtm', 'go-to-market', 'account executive'],
    'salary_mode': 'ranking',
    'allow_missing_salary': True,
    'minimum_salary_chf': 235000,
    'salary_target_chf': 300000,
    'salary_floor_chf': 200000,
    'minimum_match_score': 50,
}


def swiss_profile(**overrides):
    data = dict(SWISS_PROFILE)
    data.update(overrides)
    return make_profile(**data)


class GeographyTests(unittest.TestCase):
    """Switzerland stays a hard gate; the city inside it only ranks."""

    def setUp(self):
        self.filter = HardFilter()
        self.profile = swiss_profile()

    def _check(self, location, **extra):
        job = make_job(location=location, description=STRONG_DESCRIPTION, **extra)
        return self.filter.check(job, self.profile)

    def test_the_primary_area_is_accepted(self):
        self.assertIsNone(self._check('Zurich, Switzerland'))

    def test_other_swiss_regions_are_not_rejected_for_not_being_zurich(self):
        for city in ('Bern, Switzerland', 'Basel, Switzerland', 'Lausanne, Switzerland',
                     'Geneva, Switzerland', 'Lugano, Switzerland', 'St. Gallen, Switzerland'):
            self.assertIsNone(self._check(city), '{0} must survive the filter'.format(city))

    def test_a_swiss_city_nobody_listed_still_survives(self):
        # Winterthur is in nobody's tier list; ranking mode must keep it.
        self.assertIsNone(self._check('Winterthur, Switzerland'))

    def test_generic_regions_are_rejected(self):
        for location in ('Germany', 'Remote - Germany', 'Europe', 'EU', 'EMEA', 'DACH',
                         'Berlin, Germany', 'Remote (Europe)'):
            rejection = self._check(location)
            self.assertIsNotNone(rejection, '{0} must be rejected'.format(location))
            self.assertEqual(rejection['code'], 'not_switzerland')

    def test_switzerland_stated_in_the_description_is_accepted(self):
        job = make_job(location='Europe',
                       description='Candidates must be based in Switzerland. '
                                   + STRONG_DESCRIPTION)
        self.assertIsNone(self.filter.check(job, self.profile))

    def test_an_unknown_work_model_is_never_rejected(self):
        # No city and no remote / hybrid / onsite wording: the work model
        # genuinely cannot be determined, which must not cost the job its place.
        job = make_job(location='Switzerland', description='Lead the platform group.')
        self.assertEqual(job['work_model'], 'Unknown')
        self.assertIsNone(self.filter.check(job, self.profile))

    def test_the_preferred_city_ranks_above_an_unlisted_one(self):
        scorer = MatchScorer()
        primary = scorer.score(make_job(location='Zurich, Switzerland',
                                        description=STRONG_DESCRIPTION), self.profile)
        elsewhere = scorer.score(make_job(location='Winterthur, Switzerland',
                                          description=STRONG_DESCRIPTION), self.profile)
        self.assertGreater(primary['score'], elsewhere['score'])

    def test_radius_and_commute_are_preferences_that_no_filter_reads(self):
        profile = swiss_profile(preferred_radius_km=50, max_commute_minutes=50)
        self.assertEqual(profile['preferred_radius_km'], 50)
        # A job far outside any plausible 50 km radius is still accepted.
        job = make_job(location='Lugano, Switzerland', description=STRONG_DESCRIPTION)
        self.assertIsNone(HardFilter().check(job, profile))


class SeniorityAndRoleTests(unittest.TestCase):
    def setUp(self):
        self.scorer = MatchScorer()
        self.profile = swiss_profile()

    def _score(self, title, description=STRONG_DESCRIPTION, **extra):
        return self.scorer.score(
            make_job(title=title, location='Zurich, Switzerland',
                     description=description, **extra), self.profile)

    def test_leadership_titles_rank_highly(self):
        for title in ('Director Technology Operations',
                      'Head of Engineering and Platform Operations',
                      'Director Technology Transformation',
                      'Principal Technical Program Manager',
                      'Head of Site Reliability Engineering'):
            score = self._score(title)['score']
            self.assertGreaterEqual(score, 70, '{0} scored {1}'.format(title, score))

    def test_an_accepted_secondary_title_is_not_punished_for_its_noun(self):
        self.assertGreaterEqual(self._score('Senior Engineering Manager, Platform')['score'], 60)

    def test_execution_level_titles_rank_poorly(self):
        plain = 'Maintain servers and handle tickets for internal users.'
        for title in ('IT Support Engineer', 'Systems Administrator',
                      'Operations Coordinator', 'Monitoring Specialist'):
            score = self._score(title, description=plain)['score']
            self.assertLess(score, 60, '{0} scored {1}'.format(title, score))

    def test_an_unusual_title_is_never_hard_rejected_for_its_words(self):
        job = make_job(title='Technology Excellence Steward',
                       location='Zurich, Switzerland', description=STRONG_DESCRIPTION)
        self.assertIsNone(HardFilter().check(job, self.profile))


class DownRankTests(unittest.TestCase):
    """Low-relevance domains cost points; they never remove a posting."""

    def setUp(self):
        self.scorer = MatchScorer()
        self.profile = swiss_profile()

    def _score(self, title, description=STRONG_DESCRIPTION):
        return self.scorer.score(
            make_job(title=title, location='Zurich, Switzerland', description=description),
            self.profile)

    def test_a_purely_commercial_role_scores_poorly(self):
        commercial = ('Own the commercial pipeline, build the go-to-market motion and '
                      'close enterprise deals with our strategy team.')
        self.assertLess(self._score('GTM Lead', commercial)['score'], 60)

    def test_a_technical_role_in_a_commercial_domain_stays_relevant(self):
        technical = self._score('GTM Engineering Lead')['score']
        commercial = self._score('GTM Lead')['score']
        self.assertGreater(technical, commercial)
        self.assertGreaterEqual(technical, 60)

    def test_the_reason_is_always_written_down(self):
        result = self._score('GTM Lead')
        self.assertTrue([c for c in result['concerns'] if 'gtm' in c.lower()])

    def test_a_down_ranked_job_is_still_scored_not_filtered(self):
        job = make_job(title='GTM Lead', location='Zurich, Switzerland',
                       description=STRONG_DESCRIPTION)
        self.assertIsNone(HardFilter().check(job, self.profile))


class BandTests(unittest.TestCase):
    def test_the_five_bands(self):
        self.assertEqual(jobs_service.classify(92), 'Excellent')
        self.assertEqual(jobs_service.classify(80), 'Excellent')
        self.assertEqual(jobs_service.classify(79), 'Strong')
        self.assertEqual(jobs_service.classify(70), 'Strong')
        self.assertEqual(jobs_service.classify(69), 'Review')
        self.assertEqual(jobs_service.classify(60), 'Review')
        self.assertEqual(jobs_service.classify(59), 'Weak')
        self.assertEqual(jobs_service.classify(50), 'Weak')
        self.assertEqual(jobs_service.classify(49), 'Below threshold')

    def test_every_band_has_a_sentence(self):
        for band in ('Excellent', 'Strong', 'Review', 'Weak', 'Below threshold'):
            self.assertTrue(jobs_service.BAND_LABELS[band])


class CompensationTests(unittest.TestCase):
    def setUp(self):
        self.scorer = MatchScorer()
        self.profile = swiss_profile()

    def test_a_job_without_a_published_salary_is_never_filtered(self):
        job = make_job(location='Zurich, Switzerland', description=STRONG_DESCRIPTION)
        self.assertIsNone(HardFilter().check(job, self.profile))

    def test_a_missing_salary_costs_no_points(self):
        result = self.scorer.score(
            make_job(location='Zurich, Switzerland', description=STRONG_DESCRIPTION),
            self.profile)
        salary = [b for b in result['breakdown'] if b['dimension'] == 'salary'][0]
        self.assertEqual(salary['points'], salary['max'])
        self.assertTrue([c for c in result['concerns'] if 'not published' in c.lower()])

    def test_a_low_published_salary_costs_points_without_rejecting(self):
        job = make_job(location='Zurich, Switzerland', description=STRONG_DESCRIPTION,
                       salary_min=90000, salary_max=110000, salary_currency='CHF')
        self.assertIsNone(HardFilter().check(job, self.profile))
        result = self.scorer.score(job, self.profile)
        self.assertTrue([c for c in result['concerns'] if 'below your range' in c.lower()])


class RankingTests(unittest.TestCase):
    """Company priority breaks ties and does nothing else."""

    def test_a_better_match_outranks_a_priority_a_company(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                conn.execute("INSERT INTO company_watchlist (company_name, priority, "
                             "career_source_type, created_at, updated_at) "
                             "VALUES ('Priority A AG','A','manual','x','x')")
                for company, score in (('Priority A AG', 64), ('Unwatched GmbH', 82)):
                    conn.execute(
                        "INSERT INTO discovered_jobs (source, external_id, company, title, "
                        "job_url, match_score, state, first_seen, last_seen, source_key) "
                        "VALUES ('T', ?, ?, 'Head of Platform', 'https://x.test/'||?, ?, "
                        "'NEW', '2026-01-01', '2026-01-01', 'T:'||?)",
                        (company, company, company, score, company))
                conn.commit()
                cards = jobs_service.list_cards(conn)
            self.assertEqual([c['company'] for c in cards],
                             ['Unwatched GmbH', 'Priority A AG'])

    def test_priority_only_separates_equal_scores(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                conn.execute("INSERT INTO company_watchlist (company_name, priority, "
                             "career_source_type, created_at, updated_at) "
                             "VALUES ('Priority A AG','A','manual','x','x')")
                for company in ('Unwatched GmbH', 'Priority A AG'):
                    conn.execute(
                        "INSERT INTO discovered_jobs (source, external_id, company, title, "
                        "job_url, match_score, state, first_seen, last_seen, source_key) "
                        "VALUES ('T', ?, ?, 'Head of Platform', 'https://x.test/'||?, 75, "
                        "'NEW', '2026-01-01', '2026-01-01', 'T:'||?)",
                        (company, company, company, company))
                conn.commit()
                cards = jobs_service.list_cards(conn)
            self.assertEqual(cards[0]['company'], 'Priority A AG')


class FeedbackTests(unittest.TestCase):
    def _job(self, conn):
        conn.execute(
            "INSERT INTO discovered_jobs (source, external_id, company, title, job_url, "
            "match_score, state, first_seen, last_seen, source_key) "
            "VALUES ('T','1','Example AG','Head of Platform','https://x.test/1',75,'NEW',"
            "'2026-01-01','2026-01-01','T:1')")
        conn.commit()
        return conn.execute('SELECT id FROM discovered_jobs').fetchone()[0]

    def test_a_verdict_is_stored_and_read_back(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                job_id = self._job(conn)
                card = jobs_service.set_feedback(job_id, 'YES', conn=conn)
                self.assertEqual(card['feedback'], 'YES')
                self.assertEqual(jobs_service.get_card(job_id, conn)['feedback'], 'YES')

    def test_a_reason_only_qualifies_a_no(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                job_id = self._job(conn)
                card = jobs_service.set_feedback(job_id, 'NO', 'Too junior', conn=conn)
                self.assertEqual(card['feedback_reason'], 'Too junior')
                card = jobs_service.set_feedback(job_id, 'YES', 'Too junior', conn=conn)
                self.assertEqual(card['feedback_reason'], '')

    def test_an_unknown_verdict_or_reason_is_refused(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                job_id = self._job(conn)
                with self.assertRaises(ValueError):
                    jobs_service.set_feedback(job_id, 'PERHAPS', conn=conn)
                with self.assertRaises(ValueError):
                    jobs_service.set_feedback(job_id, 'NO', 'Made-up reason', conn=conn)

    def test_a_verdict_never_hides_the_job_or_changes_its_score(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                job_id = self._job(conn)
                jobs_service.set_feedback(job_id, 'NO', 'Too commercial', conn=conn)
                cards = jobs_service.list_cards(conn)
                self.assertEqual(len(cards), 1)
                self.assertEqual(cards[0]['score'], 75)
                self.assertEqual(cards[0]['state'], 'NEW')

    def test_the_summary_counts_verdicts_and_reasons(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                job_id = self._job(conn)
                jobs_service.set_feedback(job_id, 'NO', 'Wrong location', conn=conn)
                summary = jobs_service.feedback_summary(conn)
            self.assertEqual(summary['total'], 1)
            self.assertEqual(summary['verdicts']['NO'], 1)
            self.assertEqual(summary['reasons']['Wrong location'], 1)


class ApplyAssistantTests(unittest.TestCase):
    """Every question the brief names must always be "review required"."""

    SUBJECTIVE = [
        'Why do you want to join us?',
        'Why are you leaving?',
        'Leadership philosophy?',
        'Salary expectation?',
        'Describe a difficult situation.',
        'Why Switzerland?',
        'Why our company?',
    ]

    def test_subjective_questions_are_never_answered_automatically(self):
        for question in self.SUBJECTIVE:
            key, reason = fields.classify(question)
            self.assertEqual(key, '', '{0} must require review'.format(question))
            self.assertTrue(reason)

    def test_objective_fields_are_filled_from_the_profile(self):
        person = {'first_name': 'A', 'last_name': 'B', 'email': 'a@example.test',
                  'phone': '+00 0', 'linkedin_url': 'https://example.test/in/a',
                  'nationality': 'German', 'notice_period': 'a notice period'}
        plan = fields.plan(person)
        self.assertEqual(plan['email'], 'a@example.test')
        self.assertEqual(plan['full_name'], 'A B')
        self.assertEqual(plan['nationality'], 'German')

    def test_the_earliest_start_wins_over_the_notice_period(self):
        person = {'notice_period': 'a notice period', 'earliest_start': 'a start date'}
        self.assertEqual(fields.value_for('notice_period', person), 'a start date')


class ProfilePersistenceTests(unittest.TestCase):
    """New configuration survives a save / load round trip in SQLite."""

    def test_the_new_fields_round_trip(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                saved = save_profile(conn, dict(
                    load_profile(conn),
                    deprioritized_keywords=['gtm', 'account executive'],
                    preferred_radius_km=50,
                    max_commute_minutes=50,
                    minimum_match_score=50,
                    tertiary_locations=['Geneva', 'Lausanne', 'Lugano'],
                ))
                self.assertEqual(saved['deprioritized_keywords'], ['gtm', 'account executive'])
                reloaded = load_profile(conn)
            self.assertEqual(reloaded['preferred_radius_km'], 50)
            self.assertEqual(reloaded['max_commute_minutes'], 50)
            self.assertEqual(reloaded['minimum_match_score'], 50)
            self.assertEqual(reloaded['tertiary_locations'], ['Geneva', 'Lausanne', 'Lugano'])

    def test_a_partial_update_never_wipes_the_rest(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                save_profile(conn, dict(load_profile(conn), deprioritized_keywords=['gtm']))
                before = load_profile(conn)
                save_profile(conn, dict(before, minimum_match_score=55))
                after = load_profile(conn)
            self.assertEqual(after['deprioritized_keywords'], ['gtm'])
            self.assertEqual(after['allowed_locations'], before['allowed_locations'])

    def test_the_person_profile_keeps_the_new_columns(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                conn.execute(
                    'UPDATE person_profile SET travel_willingness=?, strengths=?, '
                    'achievements=?, secondary_target_roles=? WHERE id=1',
                    ('A travel preference with workshops in it',
                     json.dumps(['A strength', 'Another strength']),
                     json.dumps(['An achievement']),
                     json.dumps(['Head of Platform'])))
                conn.commit()
                row = dict(conn.execute('SELECT * FROM person_profile WHERE id=1').fetchone())
            self.assertIn('workshops', row['travel_willingness'])
            self.assertEqual(json.loads(row['strengths']), ['A strength', 'Another strength'])
            self.assertEqual(json.loads(row['achievements']), ['An achievement'])
            self.assertEqual(json.loads(row['secondary_target_roles']), ['Head of Platform'])


class WeightTests(unittest.TestCase):
    def test_the_weights_are_the_configured_ones_and_sum_to_100(self):
        from jobscanner.scoring import WEIGHTS
        self.assertEqual(WEIGHTS, {'role': 25, 'seniority': 20, 'technical': 20,
                                   'leadership': 15, 'location': 10, 'salary': 5,
                                   'strategic': 5})
        self.assertEqual(sum(WEIGHTS.values()), 100)

    def test_repeating_a_keyword_earns_nothing_extra(self):
        profile = swiss_profile()
        scorer = MatchScorer()
        once = scorer.score(make_job(location='Zurich, Switzerland',
                                     description=STRONG_DESCRIPTION), profile)
        many = scorer.score(make_job(location='Zurich, Switzerland',
                                     description=STRONG_DESCRIPTION + (' AWS' * 50)), profile)
        self.assertEqual(once['score'], many['score'])


if __name__ == '__main__':
    unittest.main()
