"""MatchScorer - an explainable 0-100 relevance score.

Weights (sum = 100):

    seniority               20
    role / responsibility   25
    technical domain        20
    leadership / transform. 15
    location / work model   10
    salary evidence          5
    strategic / AI relevance  5

Every dimension returns its own points, a short reason and - where relevant -
a concern, so the UI can always answer "why 84?".  Scoring never decides
whether a job is shown from a geography point of view; the HardFilter already
did that.
"""

import re

from .locations import fold

WEIGHTS = {
    'seniority': 20,
    'role': 25,
    'technical': 20,
    'leadership': 15,
    'location': 10,
    'salary': 5,
    'strategic': 5,
}

# Seniority tiers relative to the profile's selected levels.
TIER = {
    'VP': 3, 'Senior Director': 3, 'Head of': 3, 'Director': 3, 'Principal': 3,
    'Global Lead': 2, 'Senior Lead': 2, 'Senior Manager': 2, 'Lead': 2,
    'Manager': 1, 'Other': 0,
}

TECHNICAL_TERMS = [
    'aws', 'azure', 'gcp', 'google cloud', 'cloud', 'kubernetes', 'k8s', 'docker',
    'terraform', 'ansible', 'sre', 'site reliability', 'devops', 'devsecops',
    'ci/cd', 'cicd', 'continuous delivery', 'observability', 'monitoring', 'prometheus',
    'grafana', 'slo', 'sli', 'sla', 'incident management', 'on-call', 'platform engineering',
    'infrastructure', 'linux', 'networking', 'microservices', 'api', 'automation',
    'security', 'application security', 'appsec', 'zero trust', 'iam', 'data platform',
    'reliability', 'capacity', 'performance engineering', 'architecture',
]
LEADERSHIP_TERMS = [
    'leadership', 'lead a team', 'line management', 'people management', 'hiring',
    'mentoring', 'coaching', 'stakeholder', 'cross-functional', 'cross functional',
    'transformation', 'change management', 'strategy', 'roadmap', 'budget', 'p&l',
    'governance', 'operating model', 'organisational', 'organizational', 'vision',
    'executive', 'c-level', 'steering', 'portfolio', 'program management',
    'operational excellence', 'continuous improvement',
]
STRATEGIC_TERMS = [
    'ai', 'artificial intelligence', 'aiops', 'agentic', 'machine learning', 'llm',
    'genai', 'generative ai', 'mlops', 'data-driven', 'innovation', 'digital transformation',
    'automation', 'modernisation', 'modernization',
]
PEOPLE_LEADERSHIP_TERMS = ['direct reports', 'line management', 'people management',
                           'lead a team', 'manage a team', 'team of', 'hiring', 'performance reviews']
#: Matrix / program leadership counts as fully relevant - a disciplinary
#: people-management mandate is explicitly NOT a requirement of this profile.
MATRIX_LEADERSHIP_TERMS = ['matrix', 'cross-functional', 'cross functional', 'program leadership',
                           'technical leadership', 'influence without authority', 'virtual team',
                           'steering', 'program management', 'portfolio']

#: Classification bands.  Fixed, so "Excellent" always means the same thing;
#: the profile's minimum score only decides what is shown, not what it is called.
#:
#:   80-100  Excellent / high priority
#:   70-79   Strong match
#:   60-69   Worth reviewing
#:   50-59   Weak / edge match
#:   < 50    normally hidden from the Jobs list (still stored, still explained)
EXCELLENT_FROM = 80
STRONG_FROM = 70
REVIEW_FROM = 60
WEAK_FROM = 50
LABELS = [(EXCELLENT_FROM, 'Excellent match'), (STRONG_FROM, 'Strong match'),
          (REVIEW_FROM, 'Worth reviewing'), (WEAK_FROM, 'Weak match'),
          (0, 'Below threshold')]

#: Titles that signal an execution-level scope rather than a leadership,
#: program or strategy mandate.  They cost points; they never reject, because
#: an unusual title can still sit on a real mandate ("Principal Engineer,
#: Platform" is not junior).
LOW_SCOPE_TITLE_TERMS = ['specialist', 'coordinator', 'administrator', 'support engineer',
                         'sysadmin', 'system administrator', 'technician', 'operator',
                         'analyst', 'consultant']

#: How much a low-relevance domain costs.  Bounded on purpose: the point is to
#: sink a commercial role to the bottom of the list, not to make it disappear.
DOWNRANK_TITLE_PENALTY = 14.0
#: The same word in a title that also names a target responsibility area.
DOWNRANK_RESCUED_PENALTY = 5.0
DOWNRANK_BODY_PENALTY = 3.0
DOWNRANK_MAX = 28.0
LOW_SCOPE_PENALTY = 6.0

#: Languages the profile does not want to be required to speak.
EXTRA_LANGUAGES = {'french': 'French', 'francais': 'French', 'franzosisch': 'French',
                   'italian': 'Italian', 'italiano': 'Italian', 'italienisch': 'Italian'}
LANGUAGE_REQUIREMENT_CUES = ['required', 'require', 'requirement', 'mandatory', 'must',
                             'fluent', 'fluency', 'proficiency', 'proficient', 'native',
                             'erforderlich', 'zwingend', 'voraussetzung', 'verhandlungssicher',
                             'muttersprache', 'flie', 'notwendig']
LANGUAGE_WINDOW = 120


def _hits(text, terms):
    """Whole-token matches, de-duplicated case-insensitively."""
    found, seen = [], set()
    for term in terms:
        needle = fold(term)
        if not needle or needle in seen:
            continue
        if re.search(r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])', text):
            seen.add(needle)
            found.append(term)
    return found


class MatchScorer:
    def score(self, job, profile):
        title = fold(job.get('title'))
        description = fold(job.get('description') or job.get('excerpt') or '')[:20000]
        blob = '{0} {1}'.format(title, description)

        parts = [
            self._seniority(job, profile, title),
            self._role(job, profile, title, description),
            self._technical(job, profile, title, description),
            self._leadership(job, profile, blob),
            self._location(job, profile),
            self._salary(job, profile),
            self._strategic(job, profile, blob),
        ]
        # Advisories carry no points: they add "things to clarify" without
        # letting an unpublished detail push a good job below the threshold.
        advisories = self._advisories(job, profile, description)

        penalty, penalty_concerns = self._downrank(job, profile, title, description)
        total = sum(p['points'] for p in parts) - penalty
        total = max(0, min(100, int(round(total))))
        reasons = [r for p in parts for r in p['reasons']]
        concerns = [c for p in parts for c in p['concerns']] + penalty_concerns + advisories
        terms = []
        for p in parts:
            for term in p.get('terms', []):
                if term.casefold() not in {t.casefold() for t in terms}:
                    terms.append(term)

        label = next(name for threshold, name in LABELS if total >= threshold)
        breakdown = [{'dimension': p['dimension'], 'points': round(p['points'], 1),
                      'max': p['max'], 'detail': p['detail']} for p in parts]
        return {
            'score': total,
            'label': label,
            'reasons': reasons,
            'concerns': concerns,
            'terms': terms[:12],
            'breakdown': breakdown,
        }

    # -- dimensions --------------------------------------------------------
    def _part(self, dimension, points, detail, reasons=(), concerns=(), terms=()):
        return {'dimension': dimension, 'points': points, 'max': WEIGHTS[dimension],
                'detail': detail, 'reasons': list(reasons), 'concerns': list(concerns),
                'terms': list(terms)}

    def _seniority(self, job, profile, title):
        maximum = WEIGHTS['seniority']
        detected = job.get('seniority') or 'Other'
        wanted = {fold(x) for x in (profile.get('seniority_levels') or [])}
        if fold(detected) in wanted:
            return self._part('seniority', maximum, detected,
                              reasons=['{0} scope'.format(detected)])

        # A title the profile lists as "also relevant" (Engineering Manager,
        # Principal TPM, Platform Lead ...) is a real candidate, not a near
        # miss.  The description decides the rest.
        secondary = _hits(title, profile.get('secondary_titles') or [])
        own, best = TIER.get(detected, 0), max((TIER.get(x, 0) for x in
                                                (profile.get('seniority_levels') or [])), default=3)
        if own >= best:
            return self._part('seniority', maximum * 0.8, detected,
                              reasons=['{0} scope (equivalent seniority)'.format(detected)])
        if secondary:
            return self._part('seniority', maximum * 0.65, '{0} / {1}'.format(detected, secondary[0]),
                              reasons=['{0} - a scope you explicitly accept'.format(secondary[0])],
                              concerns=['confirm the actual scope and mandate of the role'],
                              terms=secondary[:2])
        if own == best - 1:
            return self._part('seniority', maximum * 0.5, detected,
                              reasons=['{0} scope'.format(detected)],
                              concerns=['seniority one step below the target levels'])
        return self._part('seniority', maximum * 0.15, detected,
                          concerns=['title does not signal a Head / Director / Principal / Lead scope'])

    def _role(self, job, profile, title, description):
        maximum = WEIGHTS['role']
        areas = profile.get('include_titles') or []
        title_hits = _hits(title, areas)
        body_hits = [a for a in _hits(description, areas) if a not in title_hits]
        # A title hit is worth far more than a mention buried in the text.
        points = 0.0
        if title_hits:
            points = 15 + (len(title_hits) - 1) * 5
        points = min(maximum, points + len(body_hits) * 1.5)
        reasons, concerns = [], []
        if title_hits:
            reasons.append('Role area in title: {0}'.format(', '.join(title_hits[:3])))
        elif body_hits:
            reasons.append('Role area in description: {0}'.format(', '.join(body_hits[:3])))
        else:
            concerns.append('no target responsibility area found in title or description')
        # Generic technical-leadership titles still deserve partial credit.
        if not title_hits:
            generic = _hits(title, ['engineering', 'technology', 'technical', 'operations',
                                    'platform', 'infrastructure', 'cloud', 'reliability',
                                    'program', 'transformation', 'enablement', 'productivity'])
            if generic:
                points = max(points, min(maximum * 0.6, 4 + len(generic) * 4))
                reasons.append('Adjacent technical leadership title ({0})'.format(', '.join(generic[:3])))
        detail = '{0} title hit(s), {1} description hit(s)'.format(len(title_hits), len(body_hits))
        return self._part('role', points, detail, reasons, concerns, title_hits[:4] + body_hits[:3])

    def _technical(self, job, profile, title, description):
        maximum = WEIGHTS['technical']
        terms = list(dict.fromkeys(list(profile.get('preferred_keywords') or []) + TECHNICAL_TERMS))
        title_hits = _hits(title, terms)
        body_hits = [t for t in _hits(description, terms) if t not in title_hits]
        points = min(maximum, len(title_hits) * 5 + len(body_hits) * 1.6)
        reasons, concerns = [], []
        shown = (title_hits + body_hits)[:4]
        if shown:
            reasons.append('Technology match: {0}'.format(', '.join(shown)))
        else:
            concerns.append('no technology keywords from your profile found')
        detail = '{0} technology keyword(s)'.format(len(title_hits) + len(body_hits))
        return self._part('technical', points, detail, reasons, concerns, shown)

    def _leadership(self, job, profile, blob):
        maximum = WEIGHTS['leadership']
        hits = _hits(blob, LEADERSHIP_TERMS)
        points = min(maximum, 4 + len(hits) * 2.2) if hits else 0.0
        reasons, concerns = [], []
        if hits:
            reasons.append('Leadership / transformation scope: {0}'.format(', '.join(hits[:3])))
        else:
            concerns.append('no leadership or transformation responsibilities described')
        # Matrix / technical / program leadership is fully relevant here, so it
        # answers the "who do you lead?" question just as well as head count.
        if not _hits(blob, PEOPLE_LEADERSHIP_TERMS) and not _hits(blob, MATRIX_LEADERSHIP_TERMS):
            concerns.append('leadership scope unclear (neither team nor matrix leadership described)')
        return self._part('leadership', points, '{0} leadership signal(s)'.format(len(hits)),
                          reasons, concerns, hits[:3])

    def _location(self, job, profile):
        maximum = WEIGHTS['location']
        preferred = {fold(x) for x in (profile.get('allowed_locations') or [])}
        optional = {fold(x) for x in (profile.get('optional_locations') or [])}
        tertiary = {fold(x) for x in (profile.get('tertiary_locations') or [])}
        city = fold(job.get('normalized_city') or '')
        model = job.get('work_model') or 'Unknown'
        reasons, concerns = [], []
        points = maximum * 0.4

        if city and city in preferred:
            points = maximum
            reasons.append(job.get('normalized_city'))
        elif city and city in optional:
            points = maximum * 0.8
            reasons.append('{0} (secondary location)'.format(job.get('normalized_city')))
        elif city and city in tertiary:
            points = maximum * 0.65
            reasons.append('{0} (tertiary location)'.format(job.get('normalized_city')))
        elif job.get('switzerland_eligible'):
            points = maximum * 0.7
            reasons.append(job.get('normalized_city') or 'Switzerland')
        if job.get('location_confidence') == 'medium':
            points *= 0.8
            concerns.append('Swiss eligibility comes from the description, not from structured data')

        if model == 'Remote':
            reasons.append('Remote')
        elif model == 'Hybrid':
            days = job.get('office_days')
            limit = int(profile.get('hybrid_max_office_days') or 2)
            if days is not None and days > limit:
                points *= 0.7
                concerns.append('{0} office days per week exceeds your maximum of {1}'.format(days, limit))
                reasons.append('Hybrid')
            else:
                reasons.append('Hybrid' + (' ({0} office days)'.format(days) if days else ''))
        elif model == 'Onsite':
            points *= 0.85
            reasons.append('Onsite')
        detail = '{0}, {1}'.format(job.get('normalized_city') or job.get('normalized_country')
                                   or job.get('raw_location') or 'unknown', model)
        return self._part('location', points, detail, reasons, concerns)

    def _salary(self, job, profile):
        """Salary ranks; it does not decide.

        Most Head / Director / Principal postings publish nothing.  Treating
        that as a penalty would systematically hide exactly the roles this
        profile is looking for, so a missing salary scores neutral-full and
        only produces a "things to clarify" note.  A *published* figure that is
        clearly below the range is the one case that costs real points.
        """
        maximum = WEIGHTS['salary']
        mode = (profile.get('salary_mode') or 'hard').lower()
        low, high = job.get('salary_min'), job.get('salary_max')
        currency = (job.get('salary_currency') or '').upper()

        if mode == 'ignore':
            return self._part('salary', maximum, 'salary not considered')

        if low is None and high is None:
            # Never below the threshold just because nothing was published.
            return self._part('salary', maximum, 'no salary published',
                              concerns=['Compensation not published'])

        top = high if high is not None else low
        bottom = low if low is not None else high
        text = '{0}{1}'.format(int(top or 0), ' ' + currency if currency else '')

        if currency and currency != 'CHF':
            return self._part('salary', maximum * 0.7, text,
                              reasons=['Salary published ({0})'.format(text)],
                              concerns=['published in {0} - not directly comparable to CHF'.format(currency)])

        target = int(profile.get('salary_target_chf') or 0)
        interesting = int(profile.get('minimum_salary_chf') or 0)
        floor = int(profile.get('salary_floor_chf') or 0)

        if floor and top is not None and top < floor:
            # Substantial, explicit penalty - but the job is still listed unless
            # salary was configured as a hard filter.
            return self._part('salary', -12, text,
                              concerns=['Published compensation ({0}) is clearly below your range'
                                        .format(_chf(top))])
        if target and bottom is not None and bottom >= target:
            return self._part('salary', maximum, text,
                              reasons=['Published salary reaches your target band'])
        if interesting and top is not None and top >= interesting:
            return self._part('salary', maximum * 0.8, text,
                              reasons=['Published salary reaches your minimum expectation'])
        if interesting and top is not None:
            return self._part('salary', maximum * 0.4, text,
                              concerns=['Published compensation ({0}) is below your expectation'
                                        .format(_chf(top))])
        return self._part('salary', maximum * 0.7, text, reasons=['Salary published'])

    # -- down-ranking (points off, never a rejection) ----------------------
    def _downrank(self, job, profile, title, description):
        """Penalty for low-relevance domains and execution-level scope.

        Deliberately a *score* mechanism rather than a filter.  A technically
        strategic role that happens to mention go-to-market keeps most of its
        points; a purely commercial one loses enough to sink below the display
        threshold, and the reason is written down either way.
        """
        terms = profile.get('deprioritized_keywords') or []
        title_hits = _hits(title, terms)
        body_hits = [t for t in _hits(description, terms) if t not in title_hits]

        # The domain word is only the whole story when the title says nothing
        # else.  "GTM Engineering Lead" names an engineering mandate in a
        # commercial domain and keeps most of its points; "GTM Lead" does not.
        rescued = bool(title_hits) and bool(
            _hits(title, profile.get('include_titles') or [])
            or _hits(title, profile.get('secondary_titles') or []))
        per_title_hit = DOWNRANK_RESCUED_PENALTY if rescued else DOWNRANK_TITLE_PENALTY
        penalty = min(DOWNRANK_MAX,
                      len(title_hits) * per_title_hit
                      + len(body_hits) * DOWNRANK_BODY_PENALTY)
        concerns = []
        if title_hits and rescued:
            concerns.append('Commercial domain in the title ({0}) - confirm how technical the '
                            'mandate really is'.format(', '.join(title_hits[:3])))
        elif title_hits:
            concerns.append('Low-relevance domain in the title: {0}'.format(
                ', '.join(title_hits[:3])))
        elif body_hits:
            concerns.append('Low-relevance domain signals in the description: {0}'.format(
                ', '.join(body_hits[:3])))

        # An execution-level title only costs points when nothing in the
        # posting describes leadership, program or transformation scope.
        scope_hits = _hits(title, LOW_SCOPE_TITLE_TERMS)
        if scope_hits and not _hits('{0} {1}'.format(title, description), LEADERSHIP_TERMS):
            penalty += LOW_SCOPE_PENALTY
            concerns.append('"{0}" scope with no leadership or program mandate described'
                            .format(scope_hits[0]))
        return penalty, concerns

    # -- advisories (no points, only "things to clarify") ------------------
    def _advisories(self, job, profile, description):
        notes = []
        limit = int(profile.get('hybrid_max_office_days') or 2)
        model = job.get('work_model') or 'Unknown'
        days = job.get('office_days')
        if model in ('Hybrid', 'Onsite', 'Unknown') and days is None:
            notes.append('Office presence not specified (potentially more than {0} onsite days)'
                         .format(limit))
        for language in _required_languages(description):
            notes.append('Additional language requirement: {0}'.format(language))
        return notes

    def _strategic(self, job, profile, blob):
        maximum = WEIGHTS['strategic']
        hits = _hits(blob, STRATEGIC_TERMS)
        points = min(maximum, len(hits) * 2.0)
        reasons = ['AI / automation relevance: {0}'.format(', '.join(hits[:3]))] if hits else []
        concerns = [] if hits else ['no AI / automation angle mentioned']
        return self._part('strategic', points, '{0} strategic signal(s)'.format(len(hits)),
                          reasons, concerns, hits[:3])


def _chf(value):
    return "CHF {0:,.0f}".format(float(value)).replace(',', "'")


def _required_languages(description):
    """French / Italian that the posting states as a requirement.

    Deliberately conservative: the language word has to sit next to a real
    requirement cue.  A job that merely mentions a French-speaking office is
    not flagged, and nothing here ever rejects a posting.
    """
    text = description or ''
    found = []
    for token, label in EXTRA_LANGUAGES.items():
        if label in found:
            continue
        for match in re.finditer(r'(?<![a-z])' + re.escape(token) + r'(?![a-z])', text):
            start = max(0, match.start() - LANGUAGE_WINDOW)
            window = text[start:match.end() + LANGUAGE_WINDOW]
            if any(cue in window for cue in LANGUAGE_REQUIREMENT_CUES):
                found.append(label)
                break
    return found
