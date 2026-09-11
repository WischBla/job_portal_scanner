"""Scoring must be explainable and must rank the profile's targets highest."""

import unittest

from jobscanner.scoring import WEIGHTS, MatchScorer
from tests.helpers import STRONG_DESCRIPTION, make_job, make_profile


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.scorer = MatchScorer()
        self.profile = make_profile()

    def score(self, **kwargs):
        kwargs.setdefault('description', STRONG_DESCRIPTION)
        kwargs.setdefault('location', 'Zurich, Switzerland')
        return self.scorer.score(make_job(**kwargs), self.profile)

    def test_weights_sum_to_100(self):
        self.assertEqual(sum(WEIGHTS.values()), 100)

    def test_score_stays_in_range(self):
        for result in (self.score(), self.score(title='Office Clerk', description='filing')):
            self.assertGreaterEqual(result['score'], 0)
            self.assertLessEqual(result['score'], 100)

    def test_every_dimension_is_explained(self):
        result = self.score()
        self.assertEqual({b['dimension'] for b in result['breakdown']}, set(WEIGHTS))
        for part in result['breakdown']:
            self.assertEqual(part['max'], WEIGHTS[part['dimension']])
            self.assertLessEqual(part['points'], part['max'] + 0.001)
        self.assertTrue(result['reasons'])
        self.assertTrue(result['label'])

    def test_target_role_beats_generic_role(self):
        target = self.score(title='Director Platform Engineering')
        generic = self.score(title='Software Engineer', description='We build web apps with React.')
        self.assertGreater(target['score'], generic['score'] + 30)

    def test_title_does_not_have_to_match_literally(self):
        """'Director, Technology Enablement' is a good job even off-vocabulary."""
        for title in ['Director, Technology Enablement', 'Head of Engineering Productivity',
                      'Global Lead Technology Transformation']:
            self.assertGreaterEqual(self.score(title=title)['score'], 65, title)

    def test_seniority_drives_the_seniority_dimension(self):
        senior = self.score(title='Head of Platform Engineering')
        junior = self.score(title='Platform Engineer')
        self.assertGreater(_dimension(senior, 'seniority'), _dimension(junior, 'seniority'))

    def test_preferred_location_scores_higher_than_a_secondary_one(self):
        preferred = self.score(location='Zurich, Switzerland')
        secondary = self.score(location='Lugano, Switzerland')
        self.assertGreater(_dimension(preferred, 'location'), _dimension(secondary, 'location'))

    def test_missing_salary_becomes_a_concern_not_a_mystery(self):
        self.assertIn('no published salary', self.score()['concerns'])

    def test_description_only_eligibility_is_flagged(self):
        result = self.scorer.score(
            make_job(location='Remote Europe',
                     description=STRONG_DESCRIPTION + ' Candidates must reside in Switzerland.'),
            self.profile)
        self.assertTrue(any('description' in c for c in result['concerns']))

    def test_office_days_above_the_limit_is_a_concern(self):
        result = self.score(location='Zurich, Switzerland',
                            description=STRONG_DESCRIPTION + ' Hybrid role with 4 days per week in the office.')
        self.assertTrue(any('office days' in c for c in result['concerns']))


def _dimension(result, name):
    return next(b['points'] for b in result['breakdown'] if b['dimension'] == name)


if __name__ == '__main__':
    unittest.main()
