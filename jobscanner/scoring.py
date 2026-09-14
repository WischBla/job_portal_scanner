"""MatchScorer - an explainable 0-100 relevance score, then personal fit.

Two layers, deliberately kept apart:

``Base Match Score``
    Is this job technically relevant?  The seven weighted dimensions below,
    minus the legacy down-ranking for low-relevance domains.  This is the
    number the scanner has always produced and it is never hidden.

``Personal Fit Score``
    Base + Operating Style Adjustment + Career Direction Adjustment, both
    computed in :mod:`jobscanner.fit`.  This is what the Jobs screen ranks by,
    because relevance and fit are not the same question.

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

from . import evidence as evidence_mod
from . import fit
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

#: Recommendation bands.  They read the *Personal Fit Score*, so the label
#: answers "should I look at this?" rather than "does the vocabulary overlap?".
#: Fixed thresholds, so "Exceptional Fit" always means the same thing; the
#: profile's minimum score only decides what is shown, not what it is called.
#:
#:   85-100  Exceptional Fit
#:   75-84   Strong Fit
#:   65-74   Worth Reviewing
#:   55-64   Edge Case
#:   < 55    Low Priority - ranked last, never deleted
EXCELLENT_FROM = 85
STRONG_FROM = 75
REVIEW_FROM = 65
WEAK_FROM = 55
LABELS = [(EXCELLENT_FROM, 'Exceptional Fit'), (STRONG_FROM, 'Strong Fit'),
          (REVIEW_FROM, 'Worth Reviewing'), (WEAK_FROM, 'Edge Case'),
          (0, 'Low Priority')]

#: Titles that signal an execution-level scope rather than a leadership,
#: program or strategy mandate.  They cost points; they never reject, because
#: an unusual title can still sit on a real mandate ("Principal Engineer,
#: Platform" is not junior).
#:
#: 'consultant' deliberately no longer appears here.  It used to be an opaque
#: title rule that fired or stayed silent depending on whether the posting
#: happened to mention any leadership word; the Career Direction Adjustment
#: now judges consulting delivery from the actual responsibilities and says so
#: in words, which is both more accurate and explainable.
LOW_SCOPE_TITLE_TERMS = ['specialist', 'coordinator', 'administrator', 'support engineer',
                         'sysadmin', 'system administrator', 'technician', 'operator',
                         'analyst']

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

        penalty, penalty_concerns, charges = self._downrank(job, profile, title, description)
        base = sum(p['points'] for p in parts) - penalty
        base = max(0, min(100, int(round(base))))

        # Layer two: is this the *shape* of role the profile wants?  The
        # charges from the base score travel with it so one signal is never
        # paid for twice.  The evidence verdict decides whether the two
        # adjustments are allowed to fire at all.
        report = evidence_mod.assess(job)
        style, direction = fit.assess(job, charges, report)
        total = fit.personal_fit(base, style['adjustment'], direction['adjustment'])

        reasons = [r for p in parts for r in p['reasons']]
        concerns = [c for p in parts for c in p['concerns']] + penalty_concerns + advisories
        concerns = _filter_concerns(concerns, report)
        concerns += _fit_concerns(style, direction)
        terms = []
        for p in parts:
            for term in p.get('terms', []):
                if term.casefold() not in {t.casefold() for t in terms}:
                    terms.append(term)

        label = next(name for threshold, name in LABELS if total >= threshold)
        breakdown = [{'dimension': p['dimension'], 'points': round(p['points'], 1),
                      'max': p['max'], 'detail': p['detail']} for p in parts]
        return {
            # What is actually known about this job.  Every consumer - the
            # card, the filters, the counts - reads the score *and* this,
            # because a number computed from four words is not the same claim
            # as a number computed from a job description.
            'evidence': report,
            # 'score' is the ranked number, so every existing call site orders
            # by personal fit without having to know this module changed.
            'score': total,
            'personal_fit': total,
            'base_score': base,
            'operating_style': style,
            'career_direction': direction,
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
            # Compensation is worth five points of the base model, so it costs
            # at most those five.  It used to return -12, which quietly made
            # salary a seventeen-point swing and let one published figure
            # outweigh the entire leadership dimension.
            return self._part('salary', 0, text,
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

        Returns ``(penalty, concerns, charges)``.  ``charges`` itemises what
        was deducted for which term so :mod:`jobscanner.fit` can credit the
        overlap back instead of charging for the same signal a second time.
        """
        charges = []
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
        raw = [(t, per_title_hit) for t in title_hits]
        raw += [(t, DOWNRANK_BODY_PENALTY) for t in body_hits]
        uncapped = sum(points for _, points in raw)
        penalty = min(DOWNRANK_MAX, uncapped)
        # What was actually deducted, per term - the cap is shared out
        # proportionally so the credit in `fit` can never exceed what the base
        # score really charged.
        shrink = (penalty / uncapped) if uncapped > penalty > 0 else 1.0
        charges = [{'term': term, 'points': points * shrink, 'kind': 'deprioritized'}
                   for term, points in raw]
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
            charges.append({'term': scope_hits[0], 'points': LOW_SCOPE_PENALTY,
                            'kind': 'low_scope'})
            concerns.append('"{0}" scope with no leadership or program mandate described'
                            .format(scope_hits[0]))
        return penalty, concerns, charges

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


#: Concerns that describe what the posting does *not* say.  Truthful when
#: there is a description to be silent about; misleading when there is no
#: description at all, because they read as findings rather than as gaps.
#: With LOW evidence they are replaced by a single honest sentence.
ABSENCE_CONCERNS = (
    'no target responsibility area found',
    'no technology keywords from your profile found',
    'no leadership or transformation responsibilities described',
    'leadership scope unclear',
    'no AI / automation angle mentioned',
    'Office presence not specified',
)


def _filter_concerns(concerns, report):
    """Absence is only a concern once there is something to be absent from."""
    if report['level'] != evidence_mod.LOW:
        return concerns
    kept = [c for c in concerns
            if not any(c.startswith(prefix) for prefix in ABSENCE_CONCERNS)]
    kept.insert(0, 'No job description stored yet, so nothing about the actual '
                   'responsibilities is known - enrich this job before judging it')
    return kept


def _fit_concerns(style, direction):
    """The two adjustments as sentences, so a move down is never unexplained."""
    notes = []
    if style['adjustment'] < 0:
        notes.append('Operating style {0:+.0f}: {1}'.format(
            style['adjustment'], style['detail']))
    if direction['adjustment'] < 0:
        notes.append('Career direction {0:+.0f}: {1}'.format(
            direction['adjustment'], direction['detail']))
    return notes


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
