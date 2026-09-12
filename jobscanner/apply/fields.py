"""Mapping between application-form fields and the personal profile.

Two rules govern everything here:

1.  Only *objective* facts are ever filled automatically - name, contact data,
    LinkedIn, work authorisation, notice period, relocation.
2.  Anything subjective ("Why this company?", "Salary expectation",
    "Describe your leadership style") is never answered automatically. It is
    reported as ``review required`` so the user writes or approves the answer.
"""

import re

#: field key -> patterns matched against label / name / id / placeholder / aria-label.
FIELD_PATTERNS = [
    ('first_name', [r'\bfirst[\s_-]*name\b', r'\bgiven[\s_-]*name\b', r'\bvorname\b', r'^fname$']),
    ('last_name', [r'\blast[\s_-]*name\b', r'\bfamily[\s_-]*name\b', r'\bsurname\b',
                   r'\bnachname\b', r'^lname$']),
    ('full_name', [r'\bfull[\s_-]*name\b', r'^name$', r'\byour name\b', r'\bvollst.ndiger name\b']),
    ('email', [r'\be[\s_-]*mail\b', r'^email', r'\bemail address\b']),
    ('phone', [r'\bphone\b', r'\bmobile\b', r'\btelephone\b', r'\btelefon\b', r'\bhandy\b']),
    ('linkedin_url', [r'\blinkedin\b', r'\blinked[\s_-]*in profile\b']),
    ('github_url', [r'\bgithub\b']),
    ('website_url', [r'\bwebsite\b', r'\bportfolio\b', r'\bpersonal site\b', r'\bhomepage\b']),
    ('city', [r'\bcity\b', r'\bstadt\b', r'\bort\b', r'\bcurrent location\b', r'\blocation\b']),
    ('postal_code', [r'\bpostal[\s_-]*code\b', r'\bzip\b', r'\bplz\b']),
    ('address', [r'\baddress\b', r'\bstreet\b', r'\bstrasse\b', r'\badresse\b']),
    ('country', [r'\bcountry\b', r'\bland\b']),
    ('nationality', [r'\bnationality\b', r'\bcitizenship\b', r'\bstaatsangeh.rigkeit\b']),
    ('work_authorization', [r'\bwork authoriz', r'\bwork authoris', r'\bwork permit\b',
                            r'\beligible to work\b', r'\bright to work\b',
                            r'\b(legally\s+)?authori[sz]ed to work\b',
                            r'\barbeitserlaubnis\b', r'\barbeitsbewilligung\b',
                            r'\bvisa status\b', r'\bsponsorship\b']),
    ('notice_period', [r'\bnotice period\b', r'\bk.ndigungsfrist\b', r'\bavailability\b',
                       r'\bearliest start\b', r'\bstart date\b', r'\bavailable from\b',
                       r'\bwhen can you start\b', r'\bearliest (possible )?starting date\b',
                       r'\bverf.gbar\b', r'\beintrittsdatum\b']),
    ('relocation', [r'\brelocat', r'\bumzug\b', r'\bwilling to move\b']),
]

#: A label that qualifies or narrows another question ("If yes, specify ...")
#: is always ambiguous out of context, so it is never answered automatically -
#: whatever else it happens to contain.
CONDITIONAL_PATTERNS = [
    r'\bif ["\u201c]?yes\b', r'\bif ["\u201c]?no\b', r'\bif so\b', r'\bif other\b',
    r'\bif applicable\b', r'\bif any\b', r'\bplease specify\b', r'\bcan you specify\b',
    r'\bfalls ja\b', r'\bwenn ja\b', r'\bbitte angeben\b',
]

#: Anything matching these is subjective and is never answered automatically.
REVIEW_PATTERNS = [
    (r'\bwhy (do|are|would|should) you\b', 'Motivation question'),
    (r'\bwhy\b.*\b(company|us|role|position|join|interested|work|apply)\b', 'Motivation question'),
    (r'\bwarum\b', 'Motivation question (German)'),
    (r'\bcover letter\b|\bmotivation letter\b|\banschreiben\b|\bmotivationsschreiben\b',
     'Cover / motivation letter'),
    (r'\bsalary\b|\bcompensation\b|\bgehalt\b|\blohn\b|\bexpected (salary|pay)\b|\bsalary expectation\b',
     'Compensation expectation'),
    (r'\bleadership style\b|\bmanagement style\b|\bf.hrungsstil\b', 'Leadership question'),
    (r'\bgood fit\b|\bwhy you\b|\bwhat makes you\b|\bstrengths\b|\bweakness\b',
     'Self-assessment question'),
    (r'\btell us\b|\bdescribe\b|\bexplain\b|\bexample of\b|\bexperience with\b',
     'Open free-text question'),
    (r'\bexcites you\b|\binterests you\b|\bmotivates you\b|\bappeals to you\b',
     'Motivation question'),
    (r'\bgender\b|\bethnic\b|\brace\b|\bdisabilit\b|\bveteran\b|\bdiversity\b',
     'Voluntary demographic question'),
    (r'\bhow did you hear\b|\breferral\b|\bwie sind sie auf uns\b', 'Source / referral question'),
    (r'\bnotice\b.*\bexplain\b', 'Open question'),
]

#: Field keys whose value comes from a derived answer rather than a raw column.
DERIVED = ('full_name', 'work_authorization', 'notice_period', 'relocation')


def normalize_label(*parts):
    text = ' '.join(str(p or '') for p in parts)
    text = re.sub(r'\s+', ' ', text).strip().lower()
    return re.sub(r'[*∗]+', '', text).strip()


def classify(label):
    """Return (field_key, reason). ``field_key`` is '' when review is required."""
    text = normalize_label(label)
    if not text:
        return '', 'Field could not be identified'
    for pattern in CONDITIONAL_PATTERNS:
        if re.search(pattern, text):
            return '', 'Follow-up question - answer it in context yourself'
    for pattern, reason in REVIEW_PATTERNS:
        if re.search(pattern, text):
            return '', reason
    for key, patterns in FIELD_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, text):
                return key, ''
    return '', 'Unrecognised field'


def value_for(key, person):
    """The objective answer for one field key, or '' when there is none."""
    person = person or {}
    if key == 'full_name':
        return '{0} {1}'.format(person.get('first_name') or '',
                                person.get('last_name') or '').strip()
    if key == 'work_authorization':
        return person.get('work_authorization') or ''
    if key == 'notice_period':
        notice = person.get('notice_period') or ''
        start = person.get('earliest_start') or ''
        return start or notice
    if key == 'relocation':
        return person.get('relocation') or ''
    return str(person.get(key) or '')


def plan(person):
    """Every objective value the assistant is allowed to type, by field key."""
    values = {}
    for key, _ in FIELD_PATTERNS:
        value = value_for(key, person)
        if value:
            values[key] = value
    return values
