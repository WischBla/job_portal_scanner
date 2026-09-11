"""The one canonical search profile.

SQLite is the single source of truth.  The frontend never keeps an independent
copy: it reads this profile, edits it, PUTs it back and re-reads it.  The scan
pipeline loads the very same row, so "saved" and "used by the scanner" cannot
drift apart.
"""

import json

from .locations import canonical_location_name

COUNTRY_MODES = ('strict', 'preferred', 'off')
REMOTE_KEYS = ('allow_remote', 'allow_hybrid', 'allow_onsite')

#: How the preferred-location lists are used.
#:   'hard'    - a Swiss job in an unlisted city is rejected (old behaviour)
#:   'ranking' - every Swiss job stays, listed cities simply score higher
LOCATION_FILTER_MODES = ('hard', 'ranking')

#: How a published salary is used.
#:   'ignore'  - salary never influences anything
#:   'ranking' - salary ranks, it never removes a job (recommended)
#:   'hard'    - `minimum_salary_chf` becomes a hard filter
SALARY_MODES = ('ignore', 'ranking', 'hard')

SORT_MODES = ('score', 'newest', 'company', 'location')

SENIORITY_LEVELS = [
    'Head of', 'Director', 'Senior Director', 'VP', 'Principal',
    'Senior Lead', 'Global Lead', 'Lead', 'Senior Manager', 'Manager', 'Other',
]

# Role areas the profile targets.  These drive the "role / responsibility"
# scoring dimension - they are NOT a title whitelist.
DEFAULT_TARGET_AREAS = [
    'Technology Operations', 'Technical Operations', 'Engineering Operations',
    'Platform Engineering', 'Platform Operations', 'Site Reliability Engineering', 'SRE',
    'Reliability Engineering', 'Cloud Engineering', 'Cloud Operations', 'Infrastructure',
    'DevOps', 'DevSecOps', 'Technology Transformation', 'Engineering Transformation',
    'AI Operations', 'AIOps', 'AI Engineering', 'Engineering Productivity',
    'Developer Productivity', 'Developer Experience', 'Technical Program Management',
    'Technical Project Management', 'Technical Program Manager', 'Technical Project Manager',
    'Principal TPM', 'Senior TPM', 'Technology Strategy', 'Operational Excellence',
    'Technology Enablement', 'Engineering Enablement', 'Service Operations',
]

DEFAULT_PREFERRED_KEYWORDS = [
    'AWS', 'Cloud', 'SRE', 'DevOps', 'DevSecOps', 'Platform Engineering', 'Observability',
    'CI/CD', 'Reliability', 'SLO', 'SLI', 'Incident Management', 'Operations', 'Automation',
    'AI', 'AIOps', 'Agentic AI', 'Technical Program Management', 'Technical Project Management',
    'Engineering Leadership', 'Technology Strategy', 'Transformation', 'Security',
    'Application Security', 'Stakeholder Management', 'Cross-functional Leadership',
    'Kubernetes', 'Terraform', 'Azure', 'GCP', 'Architecture', 'Monitoring',
]

DEFAULT_EXCLUDED_KEYWORDS = [
    'sales', 'account executive', 'account manager', 'business development', 'recruiter',
    'talent acquisition', 'human resources', 'hr business partner', 'finance', 'accountant',
    'controller', 'legal counsel', 'marketing', 'seo', 'copywriter', 'graphic design',
    'ux designer', 'frontend developer', 'front-end developer', 'web designer',
    'helpdesk', 'help desk', 'service desk', 'first level support', '1st level support',
    'customer success', 'call center', 'field sales',
]

DEFAULT_EXCLUDE_TITLES = [
    'junior', 'intern', 'internship', 'praktikant', 'praktikum', 'working student',
    'werkstudent', 'apprentice', 'apprenticeship', 'lehrstelle', 'trainee', 'graduate program',
    'entry level', 'volunteer', 'freelance writer',
]

DEFAULT_ALLOWED_LOCATIONS = ['Zurich', 'Zug', 'Luzern', 'Bern', 'Basel']
DEFAULT_OPTIONAL_LOCATIONS = ['St. Gallen', 'Schwyz', 'Aargau', 'Lugano']
DEFAULT_TERTIARY_LOCATIONS = []

# Titles that are not the primary target but must never be thrown away for
# "wrong seniority" - scope and responsibility decide, not the noun.
DEFAULT_SECONDARY_TITLES = []

DEFAULT_PROFILE = {
    'country_mode': 'strict',
    'allowed_countries': ['Switzerland'],
    'allowed_locations': list(DEFAULT_ALLOWED_LOCATIONS),
    'optional_locations': list(DEFAULT_OPTIONAL_LOCATIONS),
    'remote_policy': {'allow_remote': True, 'allow_hybrid': True, 'allow_onsite': True},
    'hybrid_max_office_days': 2,
    'seniority_levels': ['Head of', 'Director', 'Senior Director', 'Principal',
                         'Senior Lead', 'Global Lead', 'Lead'],
    'include_titles': list(DEFAULT_TARGET_AREAS),
    'exclude_titles': list(DEFAULT_EXCLUDE_TITLES),
    'required_keywords': [],
    'preferred_keywords': list(DEFAULT_PREFERRED_KEYWORDS),
    'excluded_keywords': list(DEFAULT_EXCLUDED_KEYWORDS),
    'minimum_match_score': 65,
    'minimum_salary_chf': 0,
    'allow_missing_salary': True,
    'language_preferences': ['English', 'German'],
    'sources_enabled': [],           # empty list == "whatever job_sources says"
    'auto_hours': 12,
    # -- added with the leadership preset -------------------------------
    'tertiary_locations': list(DEFAULT_TERTIARY_LOCATIONS),
    'secondary_titles': list(DEFAULT_SECONDARY_TITLES),
    'location_filter_mode': 'hard',
    'salary_mode': 'hard',
    'salary_target_chf': 0,
    'salary_floor_chf': 0,
    'sort_mode': 'score',
    'preset_key': '',
}

_LIST_FIELDS = ['allowed_countries', 'allowed_locations', 'optional_locations',
                'tertiary_locations', 'seniority_levels', 'secondary_titles',
                'include_titles', 'exclude_titles',
                'required_keywords', 'preferred_keywords', 'excluded_keywords',
                'language_preferences', 'sources_enabled']
_JSON_FIELDS = _LIST_FIELDS + ['remote_policy']
_INT_FIELDS = ['hybrid_max_office_days', 'minimum_match_score', 'minimum_salary_chf',
               'salary_target_chf', 'salary_floor_chf', 'auto_hours']
_BOOL_FIELDS = ['allow_missing_salary']
_STR_FIELDS = ['country_mode', 'location_filter_mode', 'salary_mode', 'sort_mode', 'preset_key']

FIELDS = _STR_FIELDS + _JSON_FIELDS + _INT_FIELDS + _BOOL_FIELDS


def clean_list(value):
    """Accept a list or a newline/comma/semicolon separated string."""
    if isinstance(value, (list, tuple, set, frozenset)) or (
            hasattr(value, '__iter__') and not isinstance(value, (str, bytes, dict))):
        raw = list(value)
    elif value is None:
        raw = []
    else:
        import re
        raw = re.split(r'[\n,;]+', str(value))
    seen, out = set(), []
    for item in raw:
        text = str(item).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _as_choice(value, allowed, default):
    text = str(value or '').strip().lower()
    return text if text in allowed else default


def _as_int(value, default, low, high):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = int(default)
    return max(low, min(high, number))


def sanitize(payload):
    """Validate and normalise an incoming profile payload.

    Anything missing falls back to the default, so a partial PUT never wipes
    the profile and the returned dict is always complete and storable.
    """
    data = dict(payload or {})
    out = {}

    mode = str(data.get('country_mode') or DEFAULT_PROFILE['country_mode']).strip().lower()
    out['country_mode'] = mode if mode in COUNTRY_MODES else 'strict'
    out['location_filter_mode'] = _as_choice(data.get('location_filter_mode'),
                                             LOCATION_FILTER_MODES,
                                             DEFAULT_PROFILE['location_filter_mode'])
    out['salary_mode'] = _as_choice(data.get('salary_mode'), SALARY_MODES,
                                    DEFAULT_PROFILE['salary_mode'])
    out['sort_mode'] = _as_choice(data.get('sort_mode'), SORT_MODES, DEFAULT_PROFILE['sort_mode'])
    # An empty preset_key means "this profile was configured by hand".
    out['preset_key'] = str(data.get('preset_key') or '').strip()

    for field in _LIST_FIELDS:
        out[field] = clean_list(data.get(field, DEFAULT_PROFILE[field]))

    if not out['allowed_countries']:
        out['allowed_countries'] = list(DEFAULT_PROFILE['allowed_countries'])
    # Preferred locations are stored canonically ("Zürich" and "Zurich" are one).
    for field in ('allowed_locations', 'optional_locations', 'tertiary_locations'):
        out[field] = clean_list(canonical_location_name(x) for x in out[field])

    policy = data.get('remote_policy')
    if not isinstance(policy, dict):
        policy = DEFAULT_PROFILE['remote_policy']
    out['remote_policy'] = {key: bool(policy.get(key, DEFAULT_PROFILE['remote_policy'][key]))
                            for key in REMOTE_KEYS}
    if not any(out['remote_policy'].values()):
        raise ValueError('At least one work model (remote, hybrid or onsite) must stay enabled.')

    out['hybrid_max_office_days'] = _as_int(data.get('hybrid_max_office_days'),
                                            DEFAULT_PROFILE['hybrid_max_office_days'], 0, 5)
    out['minimum_match_score'] = _as_int(data.get('minimum_match_score'),
                                         DEFAULT_PROFILE['minimum_match_score'], 0, 100)
    out['minimum_salary_chf'] = _as_int(data.get('minimum_salary_chf'),
                                        DEFAULT_PROFILE['minimum_salary_chf'], 0, 10_000_000)
    out['salary_target_chf'] = _as_int(data.get('salary_target_chf'),
                                       DEFAULT_PROFILE['salary_target_chf'], 0, 10_000_000)
    out['salary_floor_chf'] = _as_int(data.get('salary_floor_chf'),
                                      DEFAULT_PROFILE['salary_floor_chf'], 0, 10_000_000)
    out['auto_hours'] = _as_int(data.get('auto_hours'), DEFAULT_PROFILE['auto_hours'], 0, 168)
    out['allow_missing_salary'] = bool(data.get('allow_missing_salary', DEFAULT_PROFILE['allow_missing_salary']))

    if not out['seniority_levels']:
        out['seniority_levels'] = list(DEFAULT_PROFILE['seniority_levels'])
    return out


def to_row(profile):
    """Serialise a sanitised profile into column values."""
    row = {field: str(profile.get(field) or '') for field in _STR_FIELDS}
    for field in _JSON_FIELDS:
        row[field] = json.dumps(profile[field], ensure_ascii=False)
    for field in _INT_FIELDS:
        row[field] = int(profile[field])
    for field in _BOOL_FIELDS:
        row[field] = 1 if profile[field] else 0
    return row


def from_row(row):
    """Deserialise a database row back into a profile dict."""
    data = dict(row or {})
    out = {field: (data.get(field) if data.get(field) is not None else DEFAULT_PROFILE[field])
           for field in _STR_FIELDS}
    out['country_mode'] = out['country_mode'] or 'strict'
    for field in _JSON_FIELDS:
        try:
            value = json.loads(data.get(field) or 'null')
        except (TypeError, ValueError):
            value = None
        out[field] = value if value is not None else DEFAULT_PROFILE[field]
    for field in _INT_FIELDS:
        try:
            out[field] = int(data.get(field))
        except (TypeError, ValueError):
            out[field] = DEFAULT_PROFILE[field]
    for field in _BOOL_FIELDS:
        out[field] = bool(data.get(field))
    out['updated_at'] = data.get('updated_at') or ''
    return out
