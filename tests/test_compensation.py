"""Compensation estimator: priority order, ranges, confidence, no fake precision."""

import unittest

from jobscanner import compensation
from jobscanner.db import connect
from tests.helpers import TempDatabase, make_job


class SalaryParsingTests(unittest.TestCase):
    def test_reads_an_explicit_chf_range(self):
        self.assertEqual(
            compensation.salary_from_text('Salary range CHF 180,000 - CHF 220,000 per year'),
            (180000, 220000))

    def test_reads_a_k_notation_range(self):
        self.assertEqual(compensation.salary_from_text('Wir bieten CHF 150k bis 190k'),
                         (150000, 190000))

    def test_reads_swiss_apostrophe_notation(self):
        self.assertEqual(compensation.salary_from_text("CHF 185'000 - 215'000"),
                         (185000, 215000))

    def test_ignores_prices_and_small_amounts(self):
        self.assertIsNone(compensation.salary_from_text('Lunch allowance of CHF 18 per day'))

    def test_ignores_text_without_a_currency(self):
        self.assertIsNone(compensation.salary_from_text('We offer 200000 opportunities'))


class RoleFamilyTests(unittest.TestCase):
    def test_the_most_specific_phrase_wins(self):
        self.assertEqual(compensation.role_family('Head of Technology Operations'),
                         'technology_operations')
        self.assertEqual(compensation.role_family('Head of Technology'), 'engineering_leadership')

    def test_falls_back_to_the_description(self):
        self.assertEqual(
            compensation.role_family('Head of Chapter', 'You own site reliability engineering.'),
            'platform_reliability')


class EstimateTests(unittest.TestCase):
    def test_published_salary_wins_and_is_high_confidence(self):
        job = make_job(title='Head of Platform Engineering',
                       description='The salary range is CHF 210,000 - CHF 250,000.')
        result = compensation.estimate(job)
        self.assertEqual(result['basis'], 'published')
        self.assertEqual(result['confidence'], 'High')
        self.assertEqual(result['base_min'], 210000)
        self.assertEqual(result['base_max'], 250000)

    def test_company_benchmark_beats_the_baseline(self):
        with TempDatabase():
            with connect() as conn:
                job = make_job(title='Director Engineering Operations', company='UBS',
                               location='Zurich, Switzerland')
                result = compensation.estimate(job, conn=conn)
                self.assertEqual(result['basis'], 'benchmark')
                self.assertEqual(result['confidence'], 'Medium')

    def test_baseline_is_used_when_nothing_else_is_known(self):
        job = make_job(title='Head of Technology Operations', company='Unknown AG',
                       location='Bern, Switzerland')
        result = compensation.estimate(job)
        self.assertEqual(result['basis'], 'baseline')
        self.assertEqual(result['confidence'], 'Low')

    def test_every_estimate_is_a_range_with_a_confidence(self):
        job = make_job(title='Head of SRE', location='Zug, Switzerland')
        result = compensation.estimate(job)
        self.assertLess(result['total_min'], result['total_max'])
        self.assertIn(result['confidence'], ('Low', 'Medium', 'High'))
        self.assertTrue(result['display'].startswith('CHF '))

    def test_big_tech_is_estimated_above_the_local_market(self):
        local = compensation.estimate(make_job(title='Director Engineering Operations',
                                               company='Local AG', location='Zurich, Switzerland'))
        big = compensation.estimate(make_job(title='Director Engineering Operations',
                                             company='Google', location='Zurich, Switzerland'))
        self.assertGreater(big['total_max'], local['total_max'])

    def test_a_missing_salary_never_produces_an_empty_estimate(self):
        job = make_job(title='Principal Technical Program Manager', location='Basel, Switzerland')
        result = compensation.estimate(job)
        self.assertGreater(result['total_min'], 0)
        self.assertTrue(result['commentary'])

    def test_ai_commentary_is_attached_but_never_replaces_the_numbers(self):
        job = make_job(title='Head of Cloud', location='Zurich, Switzerland')
        plain = compensation.estimate(job)
        with_ai = compensation.estimate(job, ai_payload={'salary_commentary': 'Expect the upper band.'})
        self.assertEqual(plain['total_min'], with_ai['total_min'])
        self.assertIn('Expect the upper band.', with_ai['commentary'])

    def test_estimates_are_cached_per_job(self):
        with TempDatabase():
            with connect() as conn:
                conn.execute("INSERT INTO discovered_jobs (id, source, external_id, title, company, "
                             "first_seen, last_seen) VALUES (1,'T','1','Head of SRE','Acme','a','a')")
                conn.commit()
                job = dict(conn.execute('SELECT * FROM discovered_jobs WHERE id=1').fetchone())
                first = compensation.get_or_create(job, conn=conn)
                second = compensation.get_or_create(job, conn=conn)
                self.assertEqual(first['total_min'], second['total_min'])
                self.assertEqual(
                    conn.execute('SELECT COUNT(*) FROM compensation_estimates').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
