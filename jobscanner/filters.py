"""HardFilter - the gate that runs BEFORE any scoring.

Nothing that fails here can ever reach the result list, no matter how well it
would have scored.  Every rejection carries a machine-readable ``code`` and a
human sentence for the "why was this filtered" debug view.
"""

from .locations import fold

# Function areas that are simply not this profile.  Matched against the title.
#
# ABSOLUTE terms name the job function itself - "Technical Recruiter" is still a
# recruiter, so a technical word in the title must not rescue it.
ABSOLUTE_UNRELATED_TERMS = [
    'account executive', 'recruiter', 'recruiting', 'talent acquisition',
    'human resources', 'hr manager', 'hr business partner', 'payroll', 'accountant',
    'accounting', 'controller', 'auditor', 'legal counsel', 'lawyer', 'paralegal',
    'brand manager', 'content writer', 'copywriter', 'seo specialist',
    'graphic design', 'graphic designer', 'ux designer', 'ui designer',
    'web designer', 'illustrator', 'helpdesk', 'help desk', 'service desk',
    'first level support', '1st level support', 'customer support agent',
    'call center', 'receptionist', 'office manager',
]
# SOFT terms often appear as the *domain* of a technical leadership role
# ("Head of Sales Engineering Operations"), so a technical core term rescues them.
SOFT_UNRELATED_TERMS = [
    'sales', 'marketing', 'business development', 'account manager', 'finance', 'tax ',
]
# Titles that are unmistakably too junior, whatever else they say.
IMPOSSIBLE_SENIORITY_TERMS = [
    'junior', 'jr.', 'intern', 'internship', 'praktikant', 'praktikum',
    'working student', 'werkstudent', 'apprentice', 'apprenticeship', 'lehrstelle',
    'lehrling', 'ausbildung', 'trainee', 'graduate program', 'graduate programme',
    'entry level', 'entry-level', 'student assistant', 'schulerpraktikum',
]
# Strong technical-leadership signals.  Their presence protects a title from the
# unrelated-role list ("Director of Engineering, Marketing Platform" survives).
TECHNICAL_CORE_TERMS = [
    'engineering', 'engineer', 'technology', 'technical', 'platform', 'infrastructure',
    'cloud', 'devops', 'devsecops', 'sre', 'site reliability', 'reliability',
    'operations', 'observability', 'architecture', 'architect', 'data', 'software',
    'security engineering', 'program management', 'project management', 'aiops',
    'developer', 'it ',
]
# Pure-frontend / pure-design roles the profile explicitly does not want.
PURE_FRONTEND_TERMS = ['frontend developer', 'front-end developer', 'front end developer',
                       'frontend engineer', 'react developer', 'ui developer']


class Rejection(dict):
    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)


def _reject(code, reason):
    return Rejection(code=code, reason=reason, stage='hard_filter')


def _contains(haystack, needle):
    needle = fold(needle)
    return bool(needle) and needle in haystack


class HardFilter:
    """Stateless; a profile is passed to every call so tests stay trivial."""

    def check(self, job, profile):
        """Return None when the job passes, otherwise a Rejection."""
        title = fold(job.get('title'))
        body = fold('{0}\n{1}'.format(job.get('title') or '', job.get('description') or ''))

        if not job.get('title'):
            return _reject('malformed', 'Posting has no job title.')
        if not job.get('job_url'):
            return _reject('malformed', 'Posting has no link, so it cannot be opened or tracked.')

        geo = self._check_geography(job, profile)
        if geo:
            return geo
        work = self._check_work_model(job, profile)
        if work:
            return work

        for phrase in profile.get('exclude_titles') or []:
            if _contains(title, phrase):
                return _reject('excluded_title',
                               'Title contains the excluded phrase "{0}".'.format(phrase))
        for term in IMPOSSIBLE_SENIORITY_TERMS:
            if _contains(title, term):
                return _reject('impossible_seniority',
                               'Title marks the role as "{0}", which is below the target seniority.'.format(term.strip()))

        for term in ABSOLUTE_UNRELATED_TERMS:
            if _contains(title, term):
                return _reject('unrelated_function',
                               'Title names another job function ("{0}").'.format(term.strip()))
        has_technical_core = any(_contains(title, term) for term in TECHNICAL_CORE_TERMS)
        for term in SOFT_UNRELATED_TERMS:
            if _contains(title, term) and not has_technical_core:
                return _reject('unrelated_function',
                               'Title belongs to another function ("{0}") with no technical leadership signal.'.format(term.strip()))
        for term in PURE_FRONTEND_TERMS:
            if _contains(title, term):
                return _reject('unrelated_function',
                               'Title is a pure frontend role ("{0}").'.format(term))
        for phrase in profile.get('excluded_keywords') or []:
            if _contains(title, phrase) and not has_technical_core:
                return _reject('excluded_keyword',
                               'Title contains the excluded keyword "{0}".'.format(phrase))

        required = profile.get('required_keywords') or []
        missing = [kw for kw in required if not _contains(body, kw)]
        if missing:
            return _reject('missing_required_keyword',
                           'Required keyword(s) not found: {0}.'.format(', '.join(missing[:5])))

        return self._check_salary(job, profile)

    # -- geography ---------------------------------------------------------
    def _check_geography(self, job, profile):
        mode = (profile.get('country_mode') or 'strict').lower()
        country = job.get('normalized_country') or ''
        allowed_countries = {fold(c) for c in (profile.get('allowed_countries') or [])}

        if mode == 'strict':
            swiss_wanted = 'switzerland' in allowed_countries or not allowed_countries
            if swiss_wanted and not job.get('switzerland_eligible'):
                return _reject('not_switzerland', job.get('location_reason') or
                               'Location could not be confirmed as Switzerland.')
            if not swiss_wanted:
                if not country or fold(country) not in allowed_countries:
                    return _reject('country_not_allowed',
                                   'Country {0} is not in the allowed list.'.format(country or 'unknown'))
        elif mode == 'preferred':
            if country and allowed_countries and fold(country) not in allowed_countries:
                return _reject('country_not_allowed',
                               'Country {0} is not allowed in preferred-country mode.'.format(country))

        if mode == 'off':
            return None

        allowed_locations = profile.get('allowed_locations') or []
        optional_locations = profile.get('optional_locations') or []
        if allowed_locations:
            wanted = {fold(x) for x in allowed_locations} | {fold(x) for x in optional_locations}
            city = fold(job.get('normalized_city') or '')
            region = fold(job.get('normalized_region') or '')
            if city and city not in wanted and region not in wanted:
                return _reject('location_not_selected',
                               'City "{0}" is not one of the selected locations ({1}).'.format(
                                   job.get('normalized_city'), ', '.join(allowed_locations)))
        return None

    # -- work model --------------------------------------------------------
    def _check_work_model(self, job, profile):
        policy = profile.get('remote_policy') or {}
        model = job.get('work_model') or 'Unknown'
        if model == 'Hybrid' and not policy.get('allow_hybrid', True):
            return _reject('work_model', 'Hybrid roles are disabled in the profile.')
        if model == 'Remote' and not policy.get('allow_remote', True):
            return _reject('work_model', 'Remote roles are disabled in the profile.')
        if model == 'Onsite' and not policy.get('allow_onsite', True):
            return _reject('work_model', 'Onsite roles are disabled in the profile.')
        if model == 'Onsite' and not job.get('switzerland_eligible') and \
                (profile.get('country_mode') or 'strict').lower() == 'strict':
            return _reject('onsite_outside_switzerland',
                           'Onsite role outside Switzerland is not acceptable in strict mode.')
        return None

    # -- salary ------------------------------------------------------------
    def _check_salary(self, job, profile):
        minimum = int(profile.get('minimum_salary_chf') or 0)
        currency = (job.get('salary_currency') or '').upper()
        low, high = job.get('salary_min'), job.get('salary_max')
        has_salary = low is not None or high is not None
        if not minimum:
            return None
        if not has_salary:
            if profile.get('allow_missing_salary', True):
                return None
            return _reject('missing_salary',
                           'No salary published and postings without salary are not accepted.')
        if currency and currency != 'CHF':
            return None  # cannot compare reliably; treated as "unknown" salary
        top = high if high is not None else low
        if top is not None and top < minimum:
            return _reject('salary_below_minimum',
                           'Published salary tops out at {0:,.0f} CHF, below the {1:,.0f} CHF minimum.'
                           .format(top, minimum).replace(',', "'"))
        return None
