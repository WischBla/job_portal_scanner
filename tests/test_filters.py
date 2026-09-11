"""Hard filters run before scoring and must not be bypassable by a good score."""

import unittest

from jobscanner.filters import HardFilter
from tests.helpers import STRONG_DESCRIPTION, make_job, make_profile


class GeographyTests(unittest.TestCase):
    def setUp(self):
        self.filter = HardFilter()
        self.profile = make_profile()

    def check(self, job):
        return self.filter.check(job, self.profile)

    def test_swiss_job_passes(self):
        self.assertIsNone(self.check(make_job(location='Zurich, Switzerland')))

    def test_germany_is_rejected_even_with_a_perfect_title(self):
        job = make_job(title='Director Platform Engineering', location='Munich, Germany',
                       description=STRONG_DESCRIPTION)
        rejection = self.check(job)
        self.assertIsNotNone(rejection)
        self.assertEqual(rejection['code'], 'not_switzerland')

    def test_remote_europe_is_rejected(self):
        rejection = self.check(make_job(location='Remote Europe'))
        self.assertEqual(rejection['code'], 'not_switzerland')

    def test_emea_is_rejected(self):
        self.assertEqual(self.check(make_job(location='EMEA'))['code'], 'not_switzerland')

    def test_location_outside_the_selected_cities_is_rejected(self):
        self.profile = make_profile(allowed_locations=['Zurich'], optional_locations=[])
        rejection = self.check(make_job(location='Bern, Switzerland'))
        self.assertEqual(rejection['code'], 'location_not_selected')
        self.assertIsNone(self.check(make_job(location='Zurich, Switzerland')))

    def test_country_wide_remote_survives_a_city_list(self):
        self.profile = make_profile(allowed_locations=['Zurich'], optional_locations=[])
        self.assertIsNone(self.check(make_job(location='Remote - Switzerland')))

    def test_mode_off_lets_everything_through(self):
        self.profile = make_profile(country_mode='off')
        self.assertIsNone(self.check(make_job(location='Berlin, Germany')))


class WorkModelTests(unittest.TestCase):
    def setUp(self):
        self.filter = HardFilter()

    def test_hybrid_can_be_switched_off(self):
        profile = make_profile(remote_policy={'allow_remote': True, 'allow_hybrid': False,
                                              'allow_onsite': True})
        job = make_job(location='Zurich, Switzerland', description='Hybrid, 2 days in the office.')
        self.assertEqual(self.filter.check(job, profile)['code'], 'work_model')

    def test_onsite_can_be_switched_off(self):
        profile = make_profile(remote_policy={'allow_remote': True, 'allow_hybrid': True,
                                              'allow_onsite': False})
        self.assertEqual(self.filter.check(make_job(location='Zurich, Switzerland'), profile)['code'],
                         'work_model')


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.filter = HardFilter()
        self.profile = make_profile()

    def check(self, **kwargs):
        return self.filter.check(make_job(location='Zurich, Switzerland', **kwargs), self.profile)

    def test_junior_and_internship_are_rejected(self):
        for title in ['Junior DevOps Engineer', 'Internship Cloud Operations',
                      'Working Student Platform', 'Praktikant IT Operations']:
            self.assertIsNotNone(self.check(title=title), title)

    def test_unrelated_functions_are_rejected(self):
        for title in ['Account Executive DACH', 'Technical Recruiter', 'HR Business Partner',
                      'Graphic Designer', 'Frontend Developer', '1st Level Support Agent']:
            self.assertIsNotNone(self.check(title=title), title)

    def test_technical_leadership_survives_a_neighbouring_keyword(self):
        """A technical title is not thrown away just because a word overlaps."""
        self.assertIsNone(self.check(title='Director of Engineering, Marketing Platform'))
        self.assertIsNone(self.check(title='Head of Sales Engineering Operations'))

    def test_unusual_but_relevant_titles_pass(self):
        for title in ['Director, Technology Enablement', 'Head of Engineering Productivity',
                      'Principal Technical Program Manager', 'Global Lead Operational Excellence']:
            self.assertIsNone(self.check(title=title), title)

    def test_required_keywords_are_enforced(self):
        self.profile = make_profile(required_keywords=['kubernetes'])
        self.assertIsNotNone(self.check(description=STRONG_DESCRIPTION))
        self.assertIsNone(self.check(description=STRONG_DESCRIPTION + ' Kubernetes at scale.'))


class SalaryTests(unittest.TestCase):
    def setUp(self):
        self.filter = HardFilter()

    def test_missing_salary_can_be_required(self):
        profile = make_profile(minimum_salary_chf=200000, allow_missing_salary=False)
        self.assertEqual(self.filter.check(make_job(location='Zurich, Switzerland'), profile)['code'],
                         'missing_salary')

    def test_published_salary_below_minimum_is_rejected(self):
        profile = make_profile(minimum_salary_chf=200000)
        job = make_job(location='Zurich, Switzerland', salary_min=120000, salary_max=150000,
                       salary_currency='CHF')
        self.assertEqual(self.filter.check(job, profile)['code'], 'salary_below_minimum')

    def test_foreign_currency_is_not_compared(self):
        profile = make_profile(minimum_salary_chf=200000)
        job = make_job(location='Zurich, Switzerland', salary_min=90000, salary_max=110000,
                       salary_currency='USD')
        self.assertIsNone(self.filter.check(job, profile))


if __name__ == '__main__':
    unittest.main()
