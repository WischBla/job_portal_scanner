"""Personal-fit calibration: the two adjustments, and what must not move.

The cases here are deliberately written as *behaviour* rather than as exact
constants ("stakeholder management alone costs nothing", "an SRE posting is
never classified as feature engineering"), so the numbers can be recalibrated
later without the suite turning into a copy of the implementation.
"""

import unittest

from jobscanner import fit
from jobscanner.jobs_service import FEEDBACK_REASONS, FEEDBACK_VALUES, classify
from jobscanner.scoring import MatchScorer
from tests.helpers import make_job, make_profile


def assess(title, description, charges=()):
    return fit.assess({'title': title, 'description': description}, charges)


class OperatingStyleClassificationTests(unittest.TestCase):
    def test_normal_senior_collaboration_is_not_penalised(self):
        """The behaviours the profile calls normal must cost exactly nothing."""
        normal = [
            'Work cross-functionally with product and design teams.',
            'Stakeholder management across several engineering teams.',
            'Executive communication and reporting to senior leadership.',
            'Drive outcomes through influence without authority.',
            'Coordination across multiple teams in a matrix organisation.',
        ]
        for description in normal:
            style, _ = assess('Head of Platform Engineering', description)
            self.assertEqual(style['adjustment'], 0.0, description)
            self.assertEqual(style['classification'], 'TECHNICAL_OWNERSHIP', description)

    def test_the_documented_benign_example_stays_at_zero(self):
        style, _ = assess(
            'Head of Platform Engineering',
            'Partner with security and compliance stakeholders to deliver a '
            'reliable platform.')
        self.assertEqual(style['adjustment'], 0.0)

    def test_the_documented_stakeholder_example_is_penalised(self):
        style, _ = assess(
            'Director, Europe',
            'Own relationships with governments, regulators and senior external '
            'stakeholders across Europe. Government relations and regulatory '
            'liaison are the core of the role, including public policy work '
            'with ministries.')
        self.assertEqual(style['classification'], 'POLITICAL_EXTERNAL')
        self.assertLessEqual(style['adjustment'], -8.0)

    def test_engagement_management_reads_as_stakeholder_heavy(self):
        style, _ = assess(
            'Principal Engagement Manager',
            'Build and nurture trusted executive relationships. Relationship '
            'building and stakeholder orchestration across customer lines of '
            'business. Orchestrate across partners and customers.')
        self.assertEqual(style['classification'], 'STAKEHOLDER_HEAVY')

    def test_a_single_supporting_mention_never_fires(self):
        """Presence is not dominance - supporting words need a strong anchor."""
        style, _ = assess('Head of SRE',
                          'Occasional contact with external stakeholders and regulators.')
        self.assertEqual(style['adjustment'], 0.0)


class OperatingStyleBoundaryTests(unittest.TestCase):
    def test_the_adjustment_never_exceeds_its_floor(self):
        description = (' '.join(fit.POLITICAL_STRONG) + ' ' +
                       ' '.join(fit.POLITICAL_SUPPORTING)) * 3
        style, _ = assess('Head of Government Relations and Public Policy', description)
        self.assertGreaterEqual(style['adjustment'], fit.OPERATING_STYLE_FLOOR)

    def test_every_class_stays_inside_its_declared_band(self):
        for name, (low, high) in fit.OPERATING_STYLE_CLASSES.items():
            self.assertLessEqual(low, high, name)
            self.assertLessEqual(high, -fit.OPERATING_STYLE_FLOOR, name)

    def test_the_adjustment_is_never_positive(self):
        for title, description in (('Head of SRE', 'reliability and observability'),
                                   ('Engagement Manager', 'revenue recognition')):
            style, _ = assess(title, description)
            self.assertLessEqual(style['adjustment'], 0.0)


class CareerDirectionClassificationTests(unittest.TestCase):
    def test_wanted_shapes_are_not_penalised(self):
        cases = [
            ('Head of Site Reliability Engineering',
             'Own the reliability of production systems, incident management, '
             'SLOs and error budgets across the engineering organization.'),
            ('Principal Technical Program Manager',
             'Own technology roadmaps and a portfolio of programs across '
             'multiple teams, driving technical program management.'),
            ('Engineering Manager - Platform',
             'Line management for a platform engineering team, hiring and '
             'people management alongside observability and automation.'),
        ]
        for title, description in cases:
            _, direction = assess(title, description)
            self.assertEqual(direction['adjustment'], 0.0, title)

    def test_an_sre_role_is_never_classified_as_feature_engineering(self):
        """The exact false positive this module was corrected for."""
        _, direction = assess(
            'Site Reliability Engineer - Application Edge',
            'Develop and maintain services in Python and Rust. Design and build '
            'tooling. You will run on-call, own SLOs and improve reliability.')
        self.assertNotEqual(direction['classification'], 'PURE_FEATURE_ENGINEERING')
        self.assertEqual(direction['adjustment'], 0.0)

    def test_boilerplate_alone_is_not_feature_engineering_evidence(self):
        for phrase in ('design and build', 'develop and maintain', 'code reviews'):
            self.assertNotIn(phrase, [p.lower() for p in fit.FEATURE_STRONG])

    def test_feature_engineering_is_recognised(self):
        _, direction = assess(
            'Senior Software Engineer - Desktop Application',
            'Implement features for our desktop application. Work through user '
            'stories in each sprint and ship features to customers.')
        self.assertEqual(direction['classification'], 'PURE_FEATURE_ENGINEERING')
        self.assertLessEqual(direction['adjustment'], -7.0)

    def test_consulting_delivery_is_recognised(self):
        _, direction = assess(
            'Principal Delivery Consultant, Professional Services',
            'Lead client delivery for transformation programs. As an individual '
            'contributor you own the technical vision across client projects.')
        self.assertEqual(direction['classification'], 'CONSULTING_DELIVERY')
        self.assertLessEqual(direction['adjustment'], -4.0)

    def test_engagement_management_is_recognised(self):
        _, direction = assess(
            'Principal Engagement Manager, Professional Services',
            'Own program financial health including revenue recognition, '
            'commercial management, change request negotiation and scope '
            'management for fixed-price delivery.')
        self.assertEqual(direction['classification'], 'ACCOUNT_ENGAGEMENT_MANAGEMENT')
        self.assertLessEqual(direction['adjustment'], -6.0)

    def test_hands_on_is_not_by_itself_a_bad_shape(self):
        """The distinction is ownership vs. feature work, not hands-on vs. not."""
        _, direction = assess(
            'Principal Platform Engineer',
            'A deeply hands-on role owning the platform end to end: reliability, '
            'observability, automation and the engineering operating model.')
        self.assertEqual(direction['adjustment'], 0.0)


class CareerDirectionBoundaryTests(unittest.TestCase):
    def test_the_adjustment_never_exceeds_its_floor(self):
        description = (' '.join(fit.ENGAGEMENT_STRONG) + ' ' +
                       ' '.join(fit.ENGAGEMENT_SUPPORTING)) * 3
        _, direction = assess('Engagement Manager, Account Management', description)
        self.assertGreaterEqual(direction['adjustment'], fit.CAREER_DIRECTION_FLOOR)

    def test_every_class_stays_inside_its_declared_band(self):
        for name, (low, high) in fit.CAREER_DIRECTION_CLASSES.items():
            self.assertLessEqual(low, high, name)
            self.assertLessEqual(high, -fit.CAREER_DIRECTION_FLOOR, name)

    def test_a_title_that_names_a_wanted_shape_outranks_body_noise(self):
        _, direction = assess(
            'Head of Engineering Operations',
            'You will occasionally write code and review user stories.')
        self.assertEqual(direction['adjustment'], 0.0)


class NoDoublePenaltyTests(unittest.TestCase):
    """One signal, one deduction - whichever layer charges for it."""

    def test_a_base_score_charge_is_credited_back(self):
        description = ('GTM orchestration and go-to-market alignment is the core of '
                       'the role. Relationship management with partners and executive '
                       'relationships across the ecosystem.')
        without, _ = assess('Programme Lead', description)
        charged = [{'term': 'go-to-market', 'points': 3.0, 'kind': 'deprioritized'}]
        with_credit, _ = assess('Programme Lead', description, charged)
        self.assertLess(without['adjustment'], 0.0)
        self.assertGreater(with_credit['adjustment'], without['adjustment'])
        self.assertAlmostEqual(with_credit['adjustment'] - without['adjustment'], 3.0, places=1)

    def test_the_credit_never_turns_into_a_bonus(self):
        style, direction = assess(
            'Head of Platform', 'Reliability and observability ownership.',
            [{'term': 'sales', 'points': 14.0, 'kind': 'deprioritized'}])
        self.assertEqual(style['adjustment'], 0.0)
        self.assertEqual(direction['adjustment'], 0.0)

    def test_credits_are_split_by_which_adjustment_would_charge_again(self):
        operating, career = fit.credits([
            {'term': 'go-to-market', 'points': 3.0},
            {'term': 'account executive', 'points': 14.0},
            {'term': 'helpdesk', 'points': 3.0},
        ])
        self.assertAlmostEqual(operating, 3.0)
        self.assertAlmostEqual(career, 17.0)

    def test_the_scorer_hands_its_charges_to_the_adjustments(self):
        """End to end: what the base score deducted reaches the credit."""
        profile = make_profile(deprioritized_keywords=['go-to-market'],
                               include_titles=['Platform Engineering'])
        job = make_job(title='Platform Engineering Lead',
                       description='Go-to-market orchestration with partner ecosystem '
                                   'management and executive relationships.')
        result = MatchScorer().score(job, profile)
        self.assertGreaterEqual(result['operating_style']['credit'], 3.0)


class PersonalFitTests(unittest.TestCase):
    def test_the_formula_is_base_plus_both_adjustments(self):
        self.assertEqual(fit.personal_fit(80, -5.0, -3.0), 72)
        self.assertEqual(fit.personal_fit(64, 0.0, 0.0), 64)

    def test_the_result_is_clamped_to_the_display_range(self):
        self.assertEqual(fit.personal_fit(10, -15.0, -12.0), 0)
        self.assertEqual(fit.personal_fit(100, 0.0, 0.0), 100)

    def test_the_scorer_reports_all_four_values(self):
        profile = make_profile(include_titles=['Site Reliability Engineering'])
        job = make_job(title='Head of Site Reliability Engineering',
                       description='Own reliability, incident management and SLOs.')
        result = MatchScorer().score(job, profile)
        for key in ('base_score', 'operating_style', 'career_direction', 'personal_fit'):
            self.assertIn(key, result)
        self.assertEqual(result['personal_fit'],
                         fit.personal_fit(result['base_score'],
                                          result['operating_style']['adjustment'],
                                          result['career_direction']['adjustment']))
        # The ranked number and the personal fit are the same number.
        self.assertEqual(result['score'], result['personal_fit'])

    def test_the_base_score_is_never_hidden(self):
        profile = make_profile(include_titles=['Professional Services'])
        job = make_job(title='Principal Engagement Manager, Professional Services',
                       description='Own revenue recognition, commercial management and '
                                   'change request negotiation across client delivery.')
        result = MatchScorer().score(job, profile)
        self.assertGreater(result['base_score'], result['personal_fit'])
        self.assertTrue(any('Career direction' in c for c in result['concerns']))

    def test_a_move_down_is_always_explained(self):
        profile = make_profile()
        job = make_job(title='Senior Software Engineer',
                       description='Implement features and work through user stories '
                                   'each sprint, shipping features to production.')
        result = MatchScorer().score(job, profile)
        self.assertLess(result['career_direction']['adjustment'], 0.0)
        self.assertTrue(result['career_direction']['detail'])


class RankingTests(unittest.TestCase):
    def test_ranking_follows_personal_fit_not_base_score(self):
        profile = make_profile(
            include_titles=['Site Reliability Engineering', 'Professional Services'])
        consultant = make_job(
            external_id='c', title='Principal Delivery Consultant, Professional Services',
            description='Lead client delivery and client projects as an individual '
                        'contributor across cloud, automation and architecture.')
        reliability = make_job(
            external_id='r', title='Head of Site Reliability Engineering',
            description='Own reliability, observability, incident management, SLOs '
                        'and the engineering operating model across multiple teams.')
        scorer = MatchScorer()
        a, b = scorer.score(consultant, profile), scorer.score(reliability, profile)
        self.assertLess(a['personal_fit'] - b['personal_fit'],
                        a['base_score'] - b['base_score'])

    def test_a_principal_title_alone_does_not_guarantee_a_high_fit(self):
        profile = make_profile()
        job = make_job(title='Principal Engagement Manager',
                       description='Own the account, revenue recognition, commercial '
                                   'management and contract negotiation. Build and '
                                   'nurture trusted executive relationships.')
        result = MatchScorer().score(job, profile)
        self.assertLess(result['personal_fit'], result['base_score'])


class RecommendationBandTests(unittest.TestCase):
    def test_the_bands_follow_the_calibration_spec(self):
        self.assertEqual(classify(85), 'Exceptional')
        self.assertEqual(classify(100), 'Exceptional')
        self.assertEqual(classify(84), 'Strong')
        self.assertEqual(classify(75), 'Strong')
        self.assertEqual(classify(74), 'Review')
        self.assertEqual(classify(65), 'Review')
        self.assertEqual(classify(64), 'Edge')
        self.assertEqual(classify(55), 'Edge')
        self.assertEqual(classify(54), 'Low priority')
        self.assertEqual(classify(0), 'Low priority')


class MissingSalaryTests(unittest.TestCase):
    def test_a_missing_salary_does_not_penalise_a_strong_role(self):
        profile = make_profile(salary_mode='ranking', minimum_salary_chf=235000,
                               salary_target_chf=280000,
                               include_titles=['Site Reliability Engineering'])
        job = make_job(title='Head of Site Reliability Engineering',
                       description='Own reliability and incident management.')
        salary = next(p for p in MatchScorer().score(job, profile)['breakdown']
                      if p['dimension'] == 'salary')
        self.assertEqual(salary['points'], salary['max'])

    def test_compensation_can_never_cost_more_than_its_five_points(self):
        profile = make_profile(salary_mode='ranking', salary_floor_chf=200000)
        job = make_job(title='Head of Platform', salary_min=90000, salary_max=95000,
                       salary_currency='CHF')
        salary = next(p for p in MatchScorer().score(job, profile)['breakdown']
                      if p['dimension'] == 'salary')
        self.assertGreaterEqual(salary['points'], 0)
        self.assertLessEqual(salary['points'], salary['max'])

    def test_a_missing_salary_is_never_a_rejection_in_ranking_mode(self):
        from jobscanner.filters import HardFilter
        profile = make_profile(salary_mode='ranking', minimum_salary_chf=235000)
        job = make_job(title='Head of Platform Engineering')
        self.assertIsNone(HardFilter().check(job, profile))


class FeedbackVocabularyTests(unittest.TestCase):
    def test_the_three_verdicts_exist(self):
        for verdict in ('YES', 'MAYBE', 'NO'):
            self.assertIn(verdict, FEEDBACK_VALUES)

    def test_the_calibration_reasons_are_offered(self):
        expected = [
            'Too stakeholder-heavy', 'Too political / external', 'Too consulting-heavy',
            'Too hands-on IC', 'Too software-development focused',
            'Too little technical ownership', 'Too little transformation scope',
            'Seniority too low', 'Compensation likely too low', 'Location/work model poor',
            'Excellent technical ownership', 'Excellent SRE / platform fit',
            'Excellent transformation scope', 'Excellent technical program fit',
        ]
        for reason in expected:
            self.assertIn(reason, FEEDBACK_REASONS)


if __name__ == '__main__':
    unittest.main()
