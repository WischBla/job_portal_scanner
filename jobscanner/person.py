"""The applicant's personal profile - the single identity the app works with.

This is *not* the search profile (see ``jobscanner.profile``).  It holds who
the applicant is: contact data, authorisation, languages, availability,
compensation expectations, career history, achievements and leadership
context.  The apply assistant reads objective fields from here and nothing
else.

The values themselves live only in the local database.  Nothing personal is
hard-coded in this file, so a fresh installation starts blank and a commit can
never carry personal data with it.
"""

import json

#: Columns of ``person_profile`` that are stored as JSON text.
JSON_FIELDS = ('languages', 'career_history', 'technical_skills',
               'target_roles', 'secondary_target_roles', 'target_geography',
               'strengths', 'achievements')
INT_FIELDS = ('needs_sponsorship', 'willing_to_relocate',
              'comp_minimum_chf', 'comp_target_chf', 'comp_stretch_chf')
TEXT_FIELDS = ('first_name', 'last_name', 'headline', 'summary', 'email', 'phone',
               'address', 'postal_code', 'city', 'country', 'linkedin_url',
               'github_url', 'website_url', 'nationality', 'work_authorization',
               'relocation', 'availability', 'notice_period', 'earliest_start',
               'comp_notes', 'leadership_profile', 'travel_willingness')
FIELDS = TEXT_FIELDS + INT_FIELDS + JSON_FIELDS

#: The seed for a brand-new database: a *blank* profile.
#:
#: No personal value is hard-coded here on purpose.  Contact data, career
#: history, achievements and compensation expectations are the user's, they
#: live only in the local SQLite database, and they travel between machines in
#: the portable workspace - never in the repository.  A fresh installation
#: therefore starts empty and is filled in under Profile (or by importing a
#: workspace), which is also why nothing here can leak into a commit.
DEFAULT_PERSON = {
    'first_name': '',
    'last_name': '',
    'headline': '',
    'summary': '',
    'email': '',
    'phone': '',
    'address': '',
    'postal_code': '',
    'city': '',
    'country': '',
    'linkedin_url': '',
    'github_url': '',
    'website_url': '',
    'nationality': '',
    'work_authorization': '',
    'needs_sponsorship': 0,
    'relocation': '',
    'willing_to_relocate': 0,
    'languages': [],
    'availability': '',
    'notice_period': '',
    'earliest_start': '',
    'travel_willingness': '',
    'comp_minimum_chf': 0,
    'comp_target_chf': 0,
    'comp_stretch_chf': 0,
    'comp_notes': '',
    'career_history': [],
    'technical_skills': [],
    'leadership_profile': '',
    'strengths': [],
    'achievements': [],
    'target_roles': [],
    'secondary_target_roles': [],
    'target_geography': [],
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
