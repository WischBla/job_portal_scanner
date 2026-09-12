"""Sebastian's personal profile - the single identity the whole app works with.

This is *not* the search profile (see ``jobscanner.profile``).  It holds who
the applicant is: contact data, authorisation, languages, availability,
compensation expectations, career history and skills.  The apply assistant
reads objective fields from here and nothing else.
"""

import json

#: Columns of ``person_profile`` that are stored as JSON text.
JSON_FIELDS = ('languages', 'career_history', 'technical_skills',
               'target_roles', 'target_geography')
INT_FIELDS = ('needs_sponsorship', 'willing_to_relocate',
              'comp_minimum_chf', 'comp_target_chf', 'comp_stretch_chf')
TEXT_FIELDS = ('first_name', 'last_name', 'headline', 'summary', 'email', 'phone',
               'address', 'postal_code', 'city', 'country', 'linkedin_url',
               'github_url', 'website_url', 'nationality', 'work_authorization',
               'relocation', 'availability', 'notice_period', 'earliest_start',
               'comp_notes', 'leadership_profile')
FIELDS = TEXT_FIELDS + INT_FIELDS + JSON_FIELDS

DEFAULT_PERSON = {
    'first_name': 'Sebastian',
    'last_name': 'Bierwisch',
    'headline': 'Technology Operations & Engineering Leadership',
    'summary': ('Senior technology leader focused on technology and engineering operations, '
                'platform and reliability engineering, and large technical transformation '
                'programmes. Comfortable leading through matrix organisations and across '
                'international teams, with a strong automation and AI-operations angle.'),
    'email': 'sebastian.bierwisch@amplimind.io',
    'phone': '',
    'address': '',
    'postal_code': '',
    'city': '',
    'country': 'Switzerland',
    'linkedin_url': 'https://www.linkedin.com/in/sebastian-bierwisch',
    'github_url': '',
    'website_url': '',
    'nationality': 'German',
    'work_authorization': ('EU citizen. Eligible to work in Switzerland via the standard '
                           'EU/EFTA permit process (B permit); no employer sponsorship required.'),
    'needs_sponsorship': 0,
    'relocation': 'Open to relocating within Switzerland for the right role.',
    'willing_to_relocate': 1,
    'languages': [
        {'language': 'German', 'level': 'Native'},
        {'language': 'English', 'level': 'Fluent (C1/C2), working language'},
    ],
    'availability': 'Available after the contractual notice period.',
    'notice_period': '3 months',
    'earliest_start': '',
    'comp_minimum_chf': 235000,
    'comp_target_chf': 300000,
    'comp_stretch_chf': 350000,
    'comp_notes': ('Minimum attractive total compensation CHF 235-250k. Target CHF 280-350k+. '
                   'Big Tech packages CHF 350k+ including equity.'),
    'career_history': [
        {'title': 'Head of Technology Operations', 'company': '', 'start': '', 'end': '',
         'location': '', 'highlights': ''},
    ],
    'technical_skills': [
        'Cloud (AWS, Azure, GCP)', 'Kubernetes', 'Terraform', 'Platform Engineering',
        'Site Reliability Engineering', 'Observability (SLI/SLO)', 'Incident Management',
        'CI/CD', 'DevOps', 'DevSecOps', 'Automation', 'AIOps', 'AI Engineering',
        'Technical Program Management', 'Technology Strategy', 'Operating Models',
    ],
    'leadership_profile': ('Leads through a mix of direct line management and matrix / program '
                           'leadership. Experienced in building and restructuring technology '
                           'operations organisations, running transformation programmes, '
                           'stakeholder management up to executive level, and vendor governance.'),
    'target_roles': [
        'Head of Technology Operations', 'Director Engineering Operations',
        'Senior Director Platform Engineering', 'Head of SRE / Reliability',
        'Director Cloud & Infrastructure', 'Head of Engineering Transformation',
        'Principal / Senior Technical Program Manager', 'Head of Developer Experience',
        'Head of AI Operations',
    ],
    'target_geography': ['Switzerland', 'Zurich', 'Zug', 'Lucerne', 'Bern', 'Basel',
                         'St. Gallen', 'Schwyz', 'Aargau', 'Lugano'],
}


def to_row(person):
    """Profile dict -> column values ready for SQL."""
    row = {}
    for field in TEXT_FIELDS:
        row[field] = str(person.get(field) or '')
    for field in INT_FIELDS:
        try:
            row[field] = int(person.get(field) or 0)
        except (TypeError, ValueError):
            row[field] = 0
    for field in JSON_FIELDS:
        row[field] = json.dumps(person.get(field) or [], ensure_ascii=False)
    return row


def from_row(row):
    """SQL row -> profile dict with JSON fields decoded."""
    data = dict(row or {})
    data.pop('id', None)
    for field in JSON_FIELDS:
        try:
            data[field] = json.loads(data.get(field) or '[]')
        except (TypeError, ValueError):
            data[field] = []
    for field in INT_FIELDS:
        data[field] = int(data.get(field) or 0)
    data['full_name'] = '{0} {1}'.format(data.get('first_name') or '',
                                         data.get('last_name') or '').strip()
    return data


def merge(current, payload):
    """Apply a partial update; unknown keys are ignored, known keys validated."""
    merged = dict(current)
    for field in FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if field in JSON_FIELDS:
            merged[field] = value if isinstance(value, list) else []
        elif field in INT_FIELDS:
            try:
                merged[field] = int(value or 0)
            except (TypeError, ValueError):
                merged[field] = 0
        else:
            merged[field] = str(value or '').strip()
    return merged
