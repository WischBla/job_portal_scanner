"""Apply assistant: what gets filled, what is left for review, what is never done."""

import unittest

from jobscanner.apply import adapters, assistant, fields
from jobscanner.person import DEFAULT_PERSON


def control(idx, label, kind='text', tag='input', **extra):
    data = {'idx': idx, 'label': label, 'type': kind, 'tag': tag, 'name': '', 'id': '',
            'placeholder': '', 'aria': '', 'autocomplete': '', 'required': False}
    data.update(extra)
    return data


class AdapterDetectionTests(unittest.TestCase):
    def test_known_ats_hosts_are_recognised(self):
        cases = {
            'https://boards.greenhouse.io/acme/jobs/1': 'greenhouse',
            'https://job-boards.eu.greenhouse.io/proton/jobs/4897535101': 'greenhouse',
            'https://jobs.lever.co/acme/abc': 'lever',
            'https://jobs.smartrecruiters.com/Acme/123': 'smartrecruiters',
            'https://acme.wd5.myworkdayjobs.com/External/job/1': 'workday',
        }
        for url, expected in cases.items():
            self.assertEqual(adapters.detect(url).name, expected, url)

    def test_unknown_hosts_fall_back_to_the_generic_mapper(self):
        self.assertEqual(adapters.detect('https://careers.example.ch/apply/1').name, 'generic')

    def test_markup_is_used_when_the_url_says_nothing(self):
        self.assertEqual(
            adapters.detect('https://acme.com/careers',
                            '<iframe src="https://boards.greenhouse.io/acme"></iframe>').name,
            'greenhouse')


class FieldClassificationTests(unittest.TestCase):
    def test_objective_fields_are_recognised(self):
        cases = {
            'First Name *': 'first_name',
            'Nachname': 'last_name',
            'Email address': 'email',
            'Mobile phone': 'phone',
            'LinkedIn Profile URL': 'linkedin_url',
            'What is your notice period?': 'notice_period',
            'Are you legally authorized to work in Switzerland?': 'work_authorization',
        }
        for label, expected in cases.items():
            self.assertEqual(fields.classify(label)[0], expected, label)

    def test_subjective_questions_are_never_classified_as_fillable(self):
        questions = [
            'Why do you want to work at Acme?',
            'Why us?',
            'Why are you a good fit for this role?',
            'Describe your leadership style',
            'What are your salary expectations?',
            'Gehaltsvorstellung',
            'Tell us about a time you led a transformation',
        ]
        for question in questions:
            key, reason = fields.classify(question)
            self.assertEqual(key, '', question)
            self.assertTrue(reason, question)

    def test_lever_full_name_field_is_understood(self):
        self.assertEqual(adapters.LeverAdapter().classify('Name')[0], 'full_name')


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.person = dict(DEFAULT_PERSON)
        self.person['phone'] = '+41 79 000 00 00'

    def test_objective_fields_are_filled_from_the_profile(self):
        controls = [control(0, 'First Name'), control(1, 'Last Name'),
                    control(2, 'Email', 'email'), control(3, 'LinkedIn')]
        report = assistant.build_report(controls, self.person, adapters.GENERIC)
        filled = {item['field']: item['value'] for item in report['filled']}
        self.assertEqual(filled['first_name'], 'Sebastian')
        self.assertEqual(filled['last_name'], 'Bierwisch')
        self.assertEqual(filled['email'], self.person['email'])
        self.assertIn('linkedin.com', filled['linkedin_url'])

    def test_subjective_questions_land_in_review(self):
        controls = [control(0, 'Why this company?', tag='textarea', kind='textarea'),
                    control(1, 'Salary expectation')]
        report = assistant.build_report(controls, self.person, adapters.GENERIC)
        self.assertEqual(report['filled'], [])
        self.assertEqual(len(report['review']), 2)
        self.assertTrue(all(item['reason'] for item in report['review']))

    def test_dropdowns_and_checkboxes_are_always_left_to_the_user(self):
        controls = [control(0, 'Country', tag='select', kind='select'),
                    control(1, 'I accept the privacy policy', kind='checkbox')]
        report = assistant.build_report(controls, self.person, adapters.GENERIC)
        self.assertEqual(report['filled'], [])
        self.assertEqual(len(report['review']), 2)

    def test_file_inputs_are_classified_by_kind(self):
        controls = [control(0, 'Resume/CV', kind='file'),
                    control(1, 'Cover letter', kind='file'),
                    control(2, 'Portfolio attachment', kind='file')]
        report = assistant.build_report(controls, self.person, adapters.GENERIC)
        self.assertEqual([f['kind'] for f in report['files']], ['cv', 'motivation', 'unknown'])

    def test_a_field_is_only_filled_once(self):
        controls = [control(0, 'Email'), control(1, 'Confirm email')]
        report = assistant.build_report(controls, self.person, adapters.GENERIC)
        self.assertEqual(len(report['filled']), 1)


class SafetyTests(unittest.TestCase):
    def test_submit_controls_are_recognised_so_they_can_be_avoided(self):
        for label in ('Submit Application', 'Send application', 'Jetzt bewerben'):
            self.assertTrue(adapters.is_submit(label), label)

    def test_the_assistant_module_never_clicks_a_submit_control(self):
        source = (assistant.__file__ or '')
        with open(source, encoding='utf-8') as handle:
            code = handle.read()
        # _find_submit only reports; nothing may click the located control.
        self.assertNotIn('click(submit', code)
        self.assertIn('deliberately NOT clicked', code)

    def test_reports_always_say_that_nothing_was_submitted(self):
        controls = [control(0, 'First Name')]
        report = assistant.build_report(controls, dict(DEFAULT_PERSON), adapters.GENERIC)
        self.assertNotIn('submitted', report)  # build_report never submits anything


if __name__ == '__main__':
    unittest.main()


class ConditionalQuestionTests(unittest.TestCase):
    """Follow-up questions are ambiguous out of context and are never answered."""

    def test_conditional_follow_ups_go_to_review(self):
        labels = [
            'If "yes" can you specify the type of working permit you hold?',
            'If applicable, please specify your notice period',
            'Falls ja, bitte angeben',
            'If other, please specify',
        ]
        for label in labels:
            key, reason = fields.classify(label)
            self.assertEqual(key, '', label)
            self.assertTrue(reason, label)

    def test_a_conditional_beats_an_otherwise_matching_field(self):
        # The label mentions nationality, but it only qualifies a previous answer.
        key, _ = fields.classify('If "yes", what is your nationality?')
        self.assertEqual(key, '')

    def test_company_specific_motivation_questions_are_recognised(self):
        for label in ('What is it about Proton that excites you?',
                      'What interests you about this role?'):
            self.assertEqual(fields.classify(label), ('', 'Motivation question'), label)

    def test_start_date_questions_map_to_availability(self):
        self.assertEqual(fields.classify('When can you start working with us?')[0], 'notice_period')
