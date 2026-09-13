"""ATS adapters.

An adapter knows where a specific applicant-tracking system keeps its form and
how its fields are labelled.  Everything an adapter cannot identify falls
through to the generic mapper, which reads labels, names, placeholders and
accessibility attributes.

No adapter ever submits.  ``SUBMIT_PATTERNS`` exists only so the assistant can
find the submit control and explicitly leave it alone.
"""

import re

from . import fields

SUBMIT_PATTERNS = [r'\bsubmit\b', r'\bsend application\b', r'\bapply now\b',
                   r'\babsenden\b', r'\bbewerbung senden\b', r'\bjetzt bewerben\b']


class GenericAdapter:
    """Label / name / placeholder / aria-attribute driven form mapper."""

    name = 'generic'
    label = 'Generic form mapper'
    #: Container that holds the application form, best guess first.
    form_selectors = ['form#application_form', 'form[action*="apply"]', 'form[id*="apply"]',
                      'form[class*="apply"]', 'form[class*="application"]', 'main form', 'form']
    #: Something that must appear before the form is considered loaded.
    ready_selector = 'input, textarea, select'
    #: Optional control that reveals a form hidden behind a button.
    open_form_patterns = [r'\bapply\b', r'\bapply for this job\b', r'\bapply now\b',
                          r'\bjetzt bewerben\b', r'\bbewerben\b']
    file_field_patterns = {
        'cv': [r'\bresume\b', r'\bcv\b', r'\blebenslauf\b', r'\bcurriculum\b'],
        'motivation': [r'\bcover letter\b', r'\bmotivation\b', r'\banschreiben\b'],
    }

    def matches(self, url, html=''):  # pragma: no cover - generic is the fallback
        return False

    def classify(self, label):
        return fields.classify(label)

    def file_kind(self, label):
        text = fields.normalize_label(label)
        for kind, patterns in self.file_field_patterns.items():
            if any(re.search(p, text) for p in patterns):
                return kind
        return ''


class GreenhouseAdapter(GenericAdapter):
    name = 'greenhouse'
    label = 'Greenhouse'
    form_selectors = ['form#application-form', 'form#application_form',
                      'div#application_form form', 'main form', 'form']
    ready_selector = 'input[name*="first_name"], input#first_name, input[autocomplete="given-name"]'


class LeverAdapter(GenericAdapter):
    name = 'lever'
    label = 'Lever'
    form_selectors = ['form.application-form', 'form[action*="lever"]', 'div.application form',
                      'main form', 'form']
    ready_selector = 'input[name="name"], input[name="email"], input[name="resume"]'

    def classify(self, label):
        # Lever's single "Full name" field is called plain "name".
        text = fields.normalize_label(label)
        if text in ('name', 'full name'):
            return 'full_name', ''
        if text == 'org':
            return '', 'Current company (subjective)'
        return fields.classify(label)


class SmartRecruitersAdapter(GenericAdapter):
    name = 'smartrecruiters'
    label = 'SmartRecruiters'
    form_selectors = ['form[data-test="application-form"]', 'section#st-jobApplication form',
                      'main form', 'form']
    ready_selector = 'input[name="firstName"], input[id*="firstName"], input[name="email"]'


class WorkdayAdapter(GenericAdapter):
    name = 'workday'
    label = 'Workday'
    form_selectors = ['div[data-automation-id="applyFlowPage"]', 'form', 'body']
    ready_selector = ('input[data-automation-id], button[data-automation-id], '
                      'div[data-automation-id="applyFlowPage"]')
    open_form_patterns = [r'\bapply\b', r'\bapply manually\b', r'\bautofill with resume\b',
                          r'\bjetzt bewerben\b']

    def classify(self, label):
        # Workday labels are verbose ("Do you now or in the future require
        # sponsorship ...") and mostly subjective; only the clear ones are filled.
        text = fields.normalize_label(label)
        if 'legal name' in text and 'first' in text:
            return 'first_name', ''
        if 'legal name' in text and 'last' in text:
            return 'last_name', ''
        if 'sponsorship' in text or 'work authorization' in text:
            return '', 'Sponsorship / authorisation question (answer yourself)'
        return fields.classify(label)


ADAPTERS = [GreenhouseAdapter(), LeverAdapter(), SmartRecruitersAdapter(), WorkdayAdapter()]
GENERIC = GenericAdapter()

HOST_HINTS = [
    ('greenhouse', ['greenhouse.io', 'boards.greenhouse', 'job-boards.greenhouse',
                    'job-boards.eu.greenhouse']),
    ('lever', ['lever.co', 'jobs.lever.co', 'jobs.eu.lever.co']),
    ('smartrecruiters', ['smartrecruiters.com', 'jobs.smartrecruiters.com']),
    ('workday', ['myworkdayjobs.com', 'myworkdaysite.com', 'wd1.', 'wd3.', 'wd5.', 'workday.com']),
]
_BY_NAME = {adapter.name: adapter for adapter in ADAPTERS}


def detect(url, html=''):
    """Pick an adapter from the URL, falling back to page markup, then generic."""
    text = (url or '').lower()
    for name, hosts in HOST_HINTS:
        if any(host in text for host in hosts):
            return _BY_NAME[name]
    markup = (html or '').lower()
    for name, hosts in HOST_HINTS:
        if any(host in markup for host in hosts):
            return _BY_NAME[name]
    return GENERIC


def is_submit(label):
    text = fields.normalize_label(label)
    return any(re.search(pattern, text) for pattern in SUBMIT_PATTERNS)
