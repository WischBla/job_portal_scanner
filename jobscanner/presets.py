"""Built-in search presets.

A preset is a *complete, ready to use* search profile that ships with the
application.  Presets live in their own table (``search_presets``) and are
never the row the scanner reads - the scanner always reads the single active
profile in ``search_profile``.  Applying a preset copies its values into that
active profile; nothing happens implicitly.

Consequence: a manually configured profile is never overwritten by an update
of this file.  The only way a preset reaches the active profile is an explicit
"apply" / "restore" action.
"""

RECOMMENDED_KEY = 'sebastian-swiss-leadership'

# --------------------------------------------------------------------------
# Vocabulary of the recommended profile
# --------------------------------------------------------------------------

#: Tier 1 - the locations the search is really aimed at.
TIER1_LOCATIONS = ['Zurich', 'Zug', 'Luzern', 'Bern', 'Basel']
#: Tier 2 - still clearly commutable / acceptable.
TIER2_LOCATIONS = ['St. Gallen', 'Schwyz', 'Aargau']
#: Tier 3 - possible, but the weakest of the preferred set.
TIER3_LOCATIONS = ['Lugano']

#: Seniority labels the normalizer can detect that are a direct hit.
TARGET_SENIORITY = [
    'Head of', 'Director', 'Senior Director', 'Principal',
    'Global Lead', 'Senior Lead', 'Lead',
]

#: Titles that are NOT the primary target but are explicitly still welcome.
#: A title from this list can never be rejected for "wrong seniority" and it
#: lifts the seniority dimension to a solid mid score - responsibility and
#: scope decide the rest, not the word in the title.
SECONDARY_TITLES = [
    'Engineering Manager', 'Senior Engineering Manager', 'Technical Program Lead',
    'Principal Technical Program Manager', 'Senior Technical Program Manager',
    'Technical Program Manager', 'Technical Project Manager', 'Technical Program Management',
    'Technology Manager', 'Platform Engineering Manager', 'SRE Manager',
    'Cloud Engineering Manager', 'Infrastructure Manager', 'Operations Manager',
    'Engineering Operations Manager', 'Technology Lead', 'Engineering Lead',
    'Operations Lead', 'Platform Lead', 'Transformation Lead', 'Delivery Lead',
]

#: Role / responsibility areas.  These drive the "role fit" dimension.  They
#: are a *vocabulary*, never a title whitelist: a title does not have to match
#: literally for a job to score well.
TARGET_AREAS = [
    'Technology Operations', 'Technical Operations', 'Engineering Operations',
    'Platform Engineering', 'Platform Operations', 'Site Reliability Engineering', 'SRE',
    'Reliability Engineering', 'Reliability', 'Cloud Engineering', 'Cloud Operations',
    'Infrastructure Engineering', 'Infrastructure', 'DevOps', 'DevSecOps',
    'Engineering Productivity', 'Developer Productivity', 'Developer Experience',
    'Technical Program Management', 'Technical Project Management',
    'Technical Program Manager', 'Technical Project Manager',
    'Technology Strategy', 'Technology Transformation', 'Engineering Transformation',
    'Cloud Transformation', 'Digital Transformation', 'Operational Excellence',
    'Engineering Excellence', 'AI Operations', 'AIOps', 'AI Engineering',
    'Engineering Automation', 'Technical Governance', 'Technology Enablement',
    'Engineering Enablement', 'Service Operations', 'Service Reliability',
    'Production Engineering', 'Systems Engineering', 'Observability',
]

#: Technology / capability vocabulary from the CV.  Feeds the technical fit
#: dimension; repeated keywords are never rewarded twice.
PROFILE_KEYWORDS = [
    'AWS', 'Azure', 'GCP', 'Cloud Architecture', 'Cloud', 'Kubernetes', 'Terraform',
    'Platform Engineering', 'Site Reliability Engineering', 'SRE', 'Reliability',
    'SLO', 'SLI', 'Observability', 'Monitoring', 'Incident Management', 'On-call',
    'DevOps', 'DevSecOps', 'CI/CD', 'Continuous Delivery', 'Automation',
    'Engineering Automation', 'Internal Tools', 'Developer Experience',
    'Application Security', 'Security', 'Zero Trust', 'Architecture',
    'Technical Program Management', 'Technical Project Management',
    'Matrix Leadership', 'Cross-functional Leadership', 'Stakeholder Management',
    'Technology Strategy', 'Transformation', 'Operational Excellence',
    'AI', 'AIOps', 'Agentic AI', 'Machine Learning', 'Operations',
]

#: Function areas that are simply a different career.  These are matched
#: against the *title* and a strong technical signal in the same title rescues
#: the posting ("Head of Sales Engineering" survives, "Head of Sales" does not).
EXCLUDED_KEYWORDS = [
    'sales', 'account executive', 'account manager', 'business development',
    'recruiter', 'recruiting', 'talent acquisition', 'human resources',
    'hr business partner', 'finance', 'accountant', 'controller', 'legal counsel',
    'marketing', 'seo', 'copywriter', 'graphic designer', 'ux designer',
    'helpdesk', 'first level support', 'service desk', 'customer success',
]

#: Titles that are unmistakably below the target level.
EXCLUDED_TITLES = [
    'junior', 'entry level', 'entry-level', 'graduate', 'graduate program', 'intern',
    'internship', 'praktikant', 'praktikum', 'working student', 'werkstudent',
    'apprentice', 'apprenticeship', 'lehrstelle', 'trainee',
]

#: Short human summary of what the career profile actually is.  Stored with the
#: preset so the UI can show it without hard-coding it in JavaScript.
CAREER_SUMMARY = (
    '15+ years engineering and technology experience. Technology Operations, '
    'Engineering Leadership, Technical Program Leadership and Technology '
    'Transformation. Matrix, technical and program leadership count as fully '
    'relevant - a disciplinary people-management title is not required.'
)

RECOMMENDED_PROFILE = {
    # -- geography: the only hard gate -----------------------------------
    'country_mode': 'strict',
    'allowed_countries': ['Switzerland'],
    'allowed_locations': list(TIER1_LOCATIONS),
    'optional_locations': list(TIER2_LOCATIONS),
    'tertiary_locations': list(TIER3_LOCATIONS),
    # Preferred locations rank, they do not exclude: a Swiss job in a city that
    # is not listed still shows up, it just scores a little lower.
    'location_filter_mode': 'ranking',

    # -- work model -------------------------------------------------------
    'remote_policy': {'allow_remote': True, 'allow_hybrid': True, 'allow_onsite': True},
    'hybrid_max_office_days': 2,

    # -- seniority and roles ---------------------------------------------
    'seniority_levels': list(TARGET_SENIORITY),
    'secondary_titles': list(SECONDARY_TITLES),
    'include_titles': list(TARGET_AREAS),
    'exclude_titles': list(EXCLUDED_TITLES),
    'required_keywords': [],
    'preferred_keywords': list(PROFILE_KEYWORDS),
    'excluded_keywords': list(EXCLUDED_KEYWORDS),

    # -- scoring ----------------------------------------------------------
    # 62 deliberately: the score ranks opportunities, it does not make the
    # career decision.  75 / 80 / 82 would hide good unusual roles.
    'minimum_match_score': 62,

    # -- salary -----------------------------------------------------------
    'salary_mode': 'ranking',          # ignore | ranking | hard
    'allow_missing_salary': True,
    'minimum_salary_chf': 235000,      # ranking floor, tunable in Profile
    'salary_target_chf': 300000,       # ranking target, tunable in Profile
    'salary_floor_chf': 200000,        # published and clearly below -> penalty

    # -- languages --------------------------------------------------------
    'language_preferences': ['English', 'German'],

    # -- presentation -----------------------------------------------------
    'sort_mode': 'score',
    'auto_hours': 12,
    'sources_enabled': [],
    'preset_key': RECOMMENDED_KEY,
}

PRESETS = [
    {
        'key': RECOMMENDED_KEY,
        'name': 'Swiss Leadership Search',
        'description': (
            'Head / Director / Principal / Lead technology leadership roles that can '
            'realistically be worked from Switzerland. Strict Switzerland geography, '
            'broad role vocabulary, salary used for ranking only.'
        ),
        'career_summary': CAREER_SUMMARY,
        'is_recommended': True,
        'profile': dict(RECOMMENDED_PROFILE),
    },
]

_BY_KEY = {preset['key']: preset for preset in PRESETS}


def all_presets():
    """Every built-in preset, deep enough copied to be safe to hand out."""
    return [_copy(preset) for preset in PRESETS]


def get_preset(key):
    preset = _BY_KEY.get(str(key or '').strip())
    return _copy(preset) if preset else None


def recommended_preset():
    return get_preset(RECOMMENDED_KEY)


def recommended_profile():
    """The recommended preset's profile values, ready for ``sanitize``."""
    return dict(RECOMMENDED_PROFILE)


def summarize(profile):
    """The five lines the simplified settings view shows at a glance."""
    policy = profile.get('remote_policy') or {}
    models = [label for key, label in (('allow_remote', 'Remote'), ('allow_hybrid', 'Hybrid'),
                                       ('allow_onsite', 'Onsite')) if policy.get(key)]
    levels = list(profile.get('seniority_levels') or [])
    salary_labels = {'ignore': 'Not considered', 'ranking': 'Ranking signal only',
                     'hard': 'Hard minimum'}
    return {
        'country': ', '.join(profile.get('allowed_countries') or ['Switzerland']),
        'country_mode': profile.get('country_mode') or 'strict',
        'work_model': ' + '.join(models) or '—',
        'target_level': ' / '.join(levels[:5]) or '—',
        'minimum_match_score': profile.get('minimum_match_score'),
        'salary': salary_labels.get(profile.get('salary_mode') or 'ranking', 'Ranking signal only'),
        'locations': ', '.join((profile.get('allowed_locations') or [])[:6]),
    }


def _copy(preset):
    clone = dict(preset)
    clone['profile'] = dict(preset['profile'])
    for key, value in clone['profile'].items():
        if isinstance(value, list):
            clone['profile'][key] = list(value)
        elif isinstance(value, dict):
            clone['profile'][key] = dict(value)
    return clone
