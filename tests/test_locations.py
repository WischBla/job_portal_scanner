"""Location rules: what counts as Switzerland and what does not."""

import unittest

from jobscanner.locations import LocationNormalizer, canonical_location_name

SWISS_DESCRIPTION = 'The employee must reside in Switzerland and hold a valid work permit.'


class AcceptTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = LocationNormalizer()

    def assertAccepted(self, raw, **kwargs):
        verdict = self.normalizer.normalize(raw, **kwargs)
        self.assertTrue(verdict['switzerland_eligible'],
                        'expected {0!r} to be accepted, got: {1}'.format(raw, verdict['reason']))
        return verdict

    def test_swiss_city_with_country(self):
        verdict = self.assertAccepted('Zurich, Switzerland')
        self.assertEqual(verdict['normalized_country'], 'Switzerland')
        self.assertEqual(verdict['normalized_city'], 'Zurich')
        self.assertEqual(verdict['location_confidence'], 'high')

    def test_german_spelling(self):
        self.assertEqual(self.assertAccepted('Zürich, Schweiz')['normalized_city'], 'Zurich')

    def test_country_code(self):
        self.assertEqual(self.assertAccepted('Zug, CH')['normalized_city'], 'Zug')

    def test_bare_city(self):
        self.assertEqual(self.assertAccepted('Bern')['normalized_city'], 'Bern')

    def test_country_only(self):
        self.assertAccepted('Switzerland')

    def test_remote_switzerland(self):
        verdict = self.assertAccepted('Remote Switzerland')
        self.assertTrue(verdict['is_remote'])

    def test_remote_dash_switzerland(self):
        self.assertAccepted('Remote - Switzerland')

    def test_switzerland_among_other_countries(self):
        self.assertAccepted('Switzerland or Germany')

    def test_region_plus_explicit_description_evidence(self):
        verdict = self.assertAccepted('Remote Europe', description=SWISS_DESCRIPTION)
        self.assertEqual(verdict['location_confidence'], 'medium')
        self.assertTrue(verdict['evidence'])

    def test_spelling_variants_map_to_one_city(self):
        for raw, expected in [('Zuerich', 'Zurich'), ('Lucerne', 'Luzern'), ('Basle', 'Basel'),
                              ('Genf', 'Geneva'), ('Geneva', 'Geneva'), ('St. Gallen', 'St. Gallen')]:
            self.assertEqual(self.assertAccepted(raw)['normalized_city'], expected, raw)

    def test_canton_refines_region(self):
        verdict = self.assertAccepted('Aargau')
        self.assertEqual(verdict['normalized_region'], 'Aargau')


class RejectTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = LocationNormalizer()

    def assertRejected(self, raw, **kwargs):
        verdict = self.normalizer.normalize(raw, **kwargs)
        self.assertFalse(verdict['switzerland_eligible'],
                         'expected {0!r} to be rejected'.format(raw))
        self.assertTrue(verdict['reason'])
        return verdict

    def test_german_cities_and_country(self):
        self.assertEqual(self.assertRejected('Berlin, Germany')['normalized_country'], 'Germany')
        self.assertEqual(self.assertRejected('Munich')['normalized_country'], 'Germany')
        self.assertEqual(self.assertRejected('München')['normalized_country'], 'Germany')
        self.assertRejected('Deutschland')
        self.assertRejected('Frankfurt')
        self.assertRejected('Hamburg')

    def test_remote_germany(self):
        self.assertRejected('Remote Germany')
        self.assertRejected('Germany Remote')

    def test_blanket_regions(self):
        for raw in ['Remote Europe', 'Remote EU', 'Europe', 'European Union', 'DACH',
                    'EMEA', 'EMEA remote', 'Remote EMEA', 'Anywhere', 'Worldwide']:
            self.assertRejected(raw)

    def test_other_countries(self):
        for raw in ['Austria', 'France', 'Netherlands', 'Vienna, Austria', 'Paris, France']:
            self.assertRejected(raw)

    def test_generic_wording_is_not_evidence(self):
        """European / DACH / German speaking must never imply Switzerland."""
        text = ('We are a European company with a strong DACH focus. German speaking '
                'candidates from Central Europe are welcome.')
        self.assertRejected('Remote Europe', description=text)

    def test_explicit_exclusion_vetoes_evidence(self):
        text = 'We hire across Europe, excluding Switzerland for payroll reasons.'
        self.assertRejected('Remote Europe', description=text)

    def test_ambiguous_two_letter_codes_alone(self):
        """'BE' is Belgium, 'FR' is France - never Switzerland on their own."""
        for raw in ['BE', 'FR', 'NE', 'GE']:
            self.assertRejected(raw)


class WorkModelTests(unittest.TestCase):
    def setUp(self):
        self.normalizer = LocationNormalizer()

    def test_hybrid_with_office_days(self):
        verdict = self.normalizer.normalize('Zurich, Switzerland',
                                            description='Hybrid setup with 2 days per week in the office.')
        self.assertTrue(verdict['is_hybrid'])
        self.assertEqual(verdict['work_model'], 'Hybrid')
        self.assertEqual(verdict['office_days'], 2)

    def test_remote_hint_from_source(self):
        verdict = self.normalizer.normalize('Switzerland', remote_hint=True)
        self.assertTrue(verdict['is_remote'])
        self.assertEqual(verdict['work_model'], 'Remote')


class CanonicalNameTests(unittest.TestCase):
    def test_user_input_is_canonicalised(self):
        self.assertEqual(canonical_location_name('Zürich'), 'Zurich')
        self.assertEqual(canonical_location_name('Lucerne'), 'Luzern')
        self.assertEqual(canonical_location_name('Schweiz'), 'Switzerland')
        self.assertEqual(canonical_location_name('Winterthur'), 'Winterthur')


if __name__ == '__main__':
    unittest.main()
