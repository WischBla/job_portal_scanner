"""Personal-fit calibration: two explicit adjustments on top of the base score.

The base ``MatchScorer`` answers "is this job technically relevant?".  It is
deliberately keyword-driven, and that is exactly why a Principal Delivery
Consultant and a Head of SRE can come out within a point of each other: both
posting texts are full of cloud, architecture, automation and transformation
vocabulary.  Relevance is not fit.

This module answers the second question - "is this the *shape* of job I want?"
- with two named, bounded, individually explainable adjustments:

``Operating Style Adjustment``   0 .. -15
    How much of the actual mandate is relationship, orchestration and external
    representation work rather than technical ownership.

``Career Direction Adjustment``  0 .. -12
    Whether the role is technical leadership with ownership, transformation or
    organisational scope, or whether the primary job is individual feature
    implementation, consulting delivery or account/engagement management.

Three rules shape every classifier here:

1.  **Responsibility-driven, not title-driven.**  A title hit weighs more than
    a body hit because a title names the primary job, but a title alone never
    saturates an adjustment - the responsibilities have to corroborate it.

2.  **Dominance, not presence.**  Half of a normal senior posting mentions
    customers, stakeholders or regulation.  Nothing is penalised unless at
    least one *unambiguous* indicator is present, and a generic mention only
    ever adds weight to an indicator that already fired.  Cross-functional
    collaboration, stakeholder management, executive communication, influence
    without authority and coordination across teams are normal senior
    behaviour and carry no penalty at all.

3.  **No double-penalty.**  The base score already charges for some of these
    signals through ``deprioritized_keywords``.  Whatever it charged for a
    term is credited back here before the adjustment is applied, so one signal
    is never paid for twice.
"""

import re

from .locations import fold

#: Bounds from the calibration spec.  Magnitudes; both adjustments are <= 0.
OPERATING_STYLE_FLOOR = -15.0
CAREER_DIRECTION_FLOOR = -12.0

#: An indicator has to carry this much weight before anything is deducted.
#: One strong phrase buried in a description (2.5) is never enough on its own;
#: a strong phrase in the *title* (6.0) is, because a title names the job.
GATE = 4.0
#: Where the evidence is considered as dominant as it is going to get.
SATURATION = 14.0

TITLE_WEIGHT = 6.0
STRONG_WEIGHT = 2.5
SUPPORTING_WEIGHT = 1.0
SUPPORTING_CAP = 3.0

#: How much counter-evidence of real technical ownership can give back.
RESCUE_PER_TERM = 0.8
RESCUE_CAP = 5.0
#: When the *title* already names the disqualifying shape, the shape is the
#: job - technical vocabulary in the body can soften it, but not explain it
#: away.
RESCUE_CAP_WITH_TITLE_HIT = 2.0


# --------------------------------------------------------------------------
# Operating style
# --------------------------------------------------------------------------

#: Unambiguous "this role represents the company to the outside world".
POLITICAL_STRONG = [
    'government relations', 'government engagement', 'government affairs',
    'government stakeholders', 'engage with governments', 'engaging with governments',
    'public policy', 'public affairs', 'policy makers', 'policymakers',
    'regulator engagement', 'regulatory liaison', 'regulatory affairs',
    'regulatory stakeholders', 'engage with regulators', 'engaging with regulators',
    'supervisory authorities', 'regulatory authorities', 'external affairs',
    'political stakeholder', 'political stakeholders', 'policy stakeholders',
    'sovereignty policy', 'policy coordination', 'lobbying', 'ministries',
    'public sector stakeholders', 'industry associations',
]
#: Only ever counted once a strong political indicator has already fired.
POLITICAL_SUPPORTING = [
    'regulators', 'regulatory', 'government', 'sovereignty', 'sovereign',
    'public sector', 'external stakeholders', 'compliance authorities', 'jurisdictions',
]

#: Unambiguous "managing the relationship *is* the job".
STAKEHOLDER_STRONG = [
    'engagement management', 'engagement manager', 'executive relationship',
    'executive relationships', 'customer relationship management', 'customer relationships',
    'client relationship', 'client relationships', 'relationship management',
    'relationship building', 'partner ecosystem management', 'partner ecosystem',
    'gtm orchestration', 'go to market orchestration', 'stakeholder orchestration',
    'orchestration across', 'orchestrate across', 'account management',
    'executive customer engagement', 'executive customer engagements',
    'trusted advisor to customers', 'build and nurture', 'nurture trusted',
]
STAKEHOLDER_SUPPORTING = [
    'external stakeholders', 'senior external', 'executive stakeholders',
    'customer engagements', 'client engagements', 'escalation management',
    'steering committee', 'steering committees', 'c level', 'vp',
]

#: Evidence that the mandate is genuinely technical ownership.  Used as the
#: counterweight for both adjustments.
TECHNICAL_OWNERSHIP = [
    'site reliability', 'sre', 'reliability engineering', 'reliability',
    'observability', 'platform engineering', 'platform ownership', 'infrastructure',
    'kubernetes', 'incident management', 'on call', 'slo', 'sli', 'error budget',
    'automation', 'ci cd', 'devops', 'devsecops', 'monitoring', 'capacity planning',
    'performance engineering', 'technical debt', 'architecture', 'operating model',
    'engineering excellence', 'engineering productivity', 'developer experience',
    'toolchain', 'production systems', 'availability', 'resilience',
]

OPERATING_STYLE_CLASSES = {
    'TECHNICAL_OWNERSHIP': (0.0, 0.0),
    'BALANCED': (0.0, 3.0),
    'STAKEHOLDER_HEAVY': (4.0, 9.0),
    'POLITICAL_EXTERNAL': (8.0, 15.0),
}


# --------------------------------------------------------------------------
# Career direction
# --------------------------------------------------------------------------

CONSULTING_STRONG = [
    'professional services', 'delivery consultant', 'principal consultant',
    'senior consultant', 'managing consultant', 'management consulting',
    'consulting services', 'consulting practice', 'advisory services',
    'client delivery', 'client projects', 'client engagements', 'customer projects',
    'implementation projects', 'statement of work', 'billable', 'proserve',
    'utilisation target', 'utilization target', 'engagement delivery',
]
CONSULTING_SUPPORTING = ['consultant', 'consulting', 'clients', 'client', 'engagements']

ENGAGEMENT_STRONG = [
    'engagement manager', 'engagement management', 'account manager',
    'account executive', 'account management', 'revenue recognition',
    'commercial management', 'change request negotiation', 'program financial health',
    'programme financial health', 'book of business', 'scope management',
    'revenue forecasting', 'gross margin', 'p l responsibility', 'contract negotiation',
    'upsell', 'renewals', 'quota', 'sales pipeline',
]
ENGAGEMENT_SUPPORTING = ['commercial', 'revenue', 'margin', 'forecasting', 'negotiation']

#: The primary job is building a component or a feature.
#:
#: Every phrase here has to be evidence that *implementation is the job*.
#: Boilerplate that appears in any engineering posting - "design and build",
#: "develop and maintain", "code reviews" - is deliberately absent: an SRE
#: role says all three, and letting them count classified a Site Reliability
#: Engineer as feature engineering.  Programming-language names are absent for
#: the same reason; every infrastructure posting lists Python and Go too.
FEATURE_STRONG = [
    'software engineer', 'backend engineer', 'back end engineer', 'frontend engineer',
    'front end engineer', 'full stack engineer', 'fullstack engineer',
    'application developer', 'software developer', 'desktop application',
    'write code', 'writing code', 'hands on coding', 'coding skills',
    'implement features', 'feature development', 'develop features', 'build features',
    'ship features', 'delivery of features', 'feature delivery',
    'backend services', 'develop core services',
    'user stories', 'story points', 'product backlog',
]
FEATURE_SUPPORTING = ['sprint', 'sprints', 'scrum', 'agile ceremonies',
                      'feature backlog', 'product requirements']

IC_STRONG = ['individual contributor', 'no direct reports', 'ic role',
             'as an individual contributor']
IC_SUPPORTING = ['hands on', 'deep technical expertise']

#: Evidence for the shapes that are explicitly wanted.  Doubles as the rescue
#: term list for the career-direction adjustment.
ORG_SCOPE = [
    'engineering organization', 'engineering organisation', 'organization wide',
    'organisation wide', 'org wide', 'across the organization', 'across the organisation',
    'operating model', 'transformation program', 'transformation programme',
    'technology roadmap', 'portfolio of programs', 'portfolio of programmes',
    'technical program management', 'technical programme management',
    'multiple teams', 'several teams', 'line management', 'direct reports',
    'people management', 'headcount', 'hiring', 'engineering manager', 'head of',
    'engineering excellence', 'site reliability', 'sre', 'platform engineering',
    'reliability', 'observability', 'incident management', 'operational excellence',
    'continuous improvement', 'governance', 'strategy',
]

#: Signals that name a wanted shape outright.
PROGRAM_SHAPE = ['technical program management', 'technical programme management',
                 'technical program manager', 'technical programme manager',
                 'program management', 'programme management', 'portfolio',
                 'technical project management', 'platform engineering',
                 'platform operations']
MANAGEMENT_SHAPE = ['engineering manager', 'line management', 'direct reports',
                    'people management', 'head of', 'director', 'hiring',
                    'performance reviews', 'headcount']
RELIABILITY_SHAPE = ['site reliability', 'sre', 'reliability engineering',
                     'observability', 'incident management', 'slo', 'error budget',
                     'production systems', 'technology operations', 'engineering operations']
ARCHITECTURE_SHAPE = ['architect', 'architecture', 'chief architect',
                      'enterprise architecture', 'solution architecture']

CAREER_DIRECTION_CLASSES = {
    'STRATEGIC_TECHNICAL_LEADERSHIP': (0.0, 0.0),
    'TECHNICAL_PROGRAM_PLATFORM_LEADERSHIP': (0.0, 0.0),
    'ENGINEERING_MANAGEMENT_RELIABILITY': (0.0, 0.0),
    'BROAD_ARCHITECTURE_WITH_ORG_SCOPE': (0.0, 2.0),
    'PURE_SENIOR_IC': (4.0, 8.0),
    'CONSULTING_DELIVERY': (4.0, 9.0),
    'PURE_FEATURE_ENGINEERING': (7.0, 12.0),
    'ACCOUNT_ENGAGEMENT_MANAGEMENT': (6.0, 12.0),
}

#: Terms the base score's ``deprioritized_keywords`` rule may already have
#: charged for, mapped to the adjustment that would otherwise charge again.
#: The credit is applied in :func:`assess`, never here.
OPERATING_OVERLAP = {'gtm', 'go-to-market', 'go to market'}
CAREER_OVERLAP = {
    'gtm', 'go-to-market', 'go to market', 'sales', 'account executive',
    'account manager', 'business development', 'marketing', 'product marketing',
    'sap consultant', 'sap functional consultant', 'consultant', 'consulting',
}


# --------------------------------------------------------------------------
# matching helpers
# --------------------------------------------------------------------------

def normalize(value):
    """Fold, then flatten punctuation so 'go-to-market' == 'go to market'."""
    text = fold(value)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _phrase_hits(text, phrases):
    """Distinct phrases present in ``text`` as whole words, original spelling."""
    found, seen = [], set()
    for phrase in phrases:
        needle = normalize(phrase)
        if not needle or needle in seen:
            continue
        if re.search(r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])', text):
            seen.add(needle)
            found.append(phrase)
    return found


class Evidence(object):
    """What one indicator family found, and how dominant it looks."""

    def __init__(self, title_hits, strong_hits, supporting_hits):
        self.title_hits = title_hits
        self.strong_hits = strong_hits
        self.supporting_hits = supporting_hits

    @property
    def has_strong(self):
        return bool(self.title_hits or self.strong_hits)

    @property
    def intensity(self):
        # Presence is not dominance: a supporting mention on its own is worth
        # exactly nothing, which is what keeps "partner with security and
        # compliance stakeholders" from ever costing a point.
        if not self.has_strong:
            return 0.0
        supporting = min(SUPPORTING_CAP, len(self.supporting_hits) * SUPPORTING_WEIGHT)
        return (len(self.title_hits) * TITLE_WEIGHT
                + len(self.strong_hits) * STRONG_WEIGHT
                + supporting)

    def phrases(self, limit=3):
        return (self.title_hits + self.strong_hits)[:limit]


def _gather(title, description, strong, supporting):
    title_hits = _phrase_hits(title, strong)
    strong_hits = [p for p in _phrase_hits(description, strong) if p not in title_hits]
    supporting_hits = _phrase_hits(description, supporting)
    return Evidence(title_hits, strong_hits, supporting_hits)


def _scaled(intensity, low, high):
    """Map evidence intensity onto a penalty magnitude inside its band."""
    if intensity < GATE:
        return 0.0
    span = max(1.0, SATURATION - GATE)
    ratio = min(1.0, (intensity - GATE) / span)
    return low + (high - low) * ratio


def _rescue(blob, terms, had_title_hit):
    hits = _phrase_hits(blob, terms)
    cap = RESCUE_CAP_WITH_TITLE_HIT if had_title_hit else RESCUE_CAP
    return min(cap, len(hits) * RESCUE_PER_TERM), hits


# --------------------------------------------------------------------------
# the two adjustments
# --------------------------------------------------------------------------

def operating_style(title, description, credit=0.0):
    """How much of the mandate is relationship / external representation work."""
    blob = '{0} {1}'.format(title, description).strip()
    political = _gather(title, description, POLITICAL_STRONG, POLITICAL_SUPPORTING)
    stakeholder = _gather(title, description, STAKEHOLDER_STRONG, STAKEHOLDER_SUPPORTING)

    if political.intensity >= stakeholder.intensity and political.has_strong:
        classification = 'POLITICAL_EXTERNAL'
        evidence, intensity = political, political.intensity
    elif stakeholder.has_strong:
        classification = 'STAKEHOLDER_HEAVY'
        evidence, intensity = stakeholder, stakeholder.intensity
    else:
        return _result('TECHNICAL_OWNERSHIP', 0.0, [], [],
                       'no dominant relationship or external-representation mandate')

    low, high = OPERATING_STYLE_CLASSES[classification]
    raw = _scaled(intensity, low, high)
    if raw <= 0:
        return _result('TECHNICAL_OWNERSHIP', 0.0, evidence.phrases(), [],
                       'relationship signals present but not dominant')

    rescue, rescue_hits = _rescue(blob, TECHNICAL_OWNERSHIP, bool(evidence.title_hits))
    penalty = max(0.0, raw - rescue - max(0.0, credit))

    # Below the floor of the heavy bands the honest label is "balanced":
    # some orchestration weight, not a relationship job.
    if penalty < OPERATING_STYLE_CLASSES['STAKEHOLDER_HEAVY'][0]:
        classification = 'BALANCED' if penalty > 0 else 'TECHNICAL_OWNERSHIP'
        penalty = min(penalty, OPERATING_STYLE_CLASSES['BALANCED'][1])

    penalty = min(penalty, -OPERATING_STYLE_FLOOR)
    return _result(classification, -round(penalty, 1), evidence.phrases(), rescue_hits[:3],
                   _explain_operating(classification, evidence))


def career_direction(title, description, credit=0.0):
    """Whether the role's shape matches the career direction, not just the stack."""
    blob = '{0} {1}'.format(title, description).strip()
    candidates = {
        'CONSULTING_DELIVERY': _gather(title, description,
                                       CONSULTING_STRONG, CONSULTING_SUPPORTING),
        'ACCOUNT_ENGAGEMENT_MANAGEMENT': _gather(title, description,
                                                 ENGAGEMENT_STRONG, ENGAGEMENT_SUPPORTING),
        'PURE_FEATURE_ENGINEERING': _gather(title, description,
                                            FEATURE_STRONG, FEATURE_SUPPORTING),
        'PURE_SENIOR_IC': _gather(title, description, IC_STRONG, IC_SUPPORTING),
    }
    classification = max(candidates, key=lambda k: candidates[k].intensity)
    evidence = candidates[classification]

    if not evidence.has_strong or evidence.intensity < GATE:
        return _wanted_shape(title, blob)

    # A title that names a wanted shape outranks body-level counter-evidence.
    # "Site Reliability Engineer - Application Edge" is an SRE role even though
    # the text happens to mention building things; only a negative signal in
    # the *title* may overrule what the title says the job is.
    if not evidence.title_hits and _names_wanted_shape(title):
        return _wanted_shape(title, blob)

    low, high = CAREER_DIRECTION_CLASSES[classification]
    raw = _scaled(evidence.intensity, low, high)

    # When a second poor shape clears the gate on its own evidence, the
    # posting has named its shape twice in two different ways - a Principal
    # Delivery Consultant that also says "in this role as an individual
    # contributor".  Technical vocabulary elsewhere in the text does not buy
    # that back, so the counterweight is withdrawn.
    corroborated = sum(1 for e in candidates.values()
                       if e.has_strong and e.intensity >= GATE) > 1
    if corroborated:
        rescue, rescue_hits = 0.0, []
    else:
        rescue, rescue_hits = _rescue(blob, ORG_SCOPE, bool(evidence.title_hits))

    penalty = max(0.0, raw - rescue - max(0.0, credit))
    if penalty <= 0:
        return _wanted_shape(title, blob)

    penalty = min(penalty, -CAREER_DIRECTION_FLOOR)
    return _result(classification, -round(penalty, 1), evidence.phrases(), rescue_hits[:3],
                   _explain_career(classification, evidence))


def _names_wanted_shape(title):
    """Does the title itself name one of the preferred role shapes?"""
    return bool(_phrase_hits(title, RELIABILITY_SHAPE)
                or _phrase_hits(title, PROGRAM_SHAPE)
                or _phrase_hits(title, MANAGEMENT_SHAPE))


def _wanted_shape(title, blob):
    """Name the preferred shape a posting matches, so a 0 is explained too."""
    if _phrase_hits(title, RELIABILITY_SHAPE) or _phrase_hits(blob, RELIABILITY_SHAPE):
        if _phrase_hits(blob, MANAGEMENT_SHAPE):
            return _result('ENGINEERING_MANAGEMENT_RELIABILITY', 0.0, [], [],
                           'engineering management with reliability / operations scope')
        return _result('TECHNICAL_PROGRAM_PLATFORM_LEADERSHIP', 0.0, [], [],
                       'platform / reliability ownership')
    if _phrase_hits(title, PROGRAM_SHAPE) or _phrase_hits(blob, PROGRAM_SHAPE):
        return _result('TECHNICAL_PROGRAM_PLATFORM_LEADERSHIP', 0.0, [], [],
                       'technical program / platform leadership')
    if _phrase_hits(title, MANAGEMENT_SHAPE):
        return _result('ENGINEERING_MANAGEMENT_RELIABILITY', 0.0, [], [],
                       'engineering management scope')
    if _phrase_hits(title, ARCHITECTURE_SHAPE):
        # Architecture without organisational scope is a narrower shape than
        # the direction calls for - the spec allows a token deduction.
        if not _phrase_hits(blob, ORG_SCOPE):
            return _result('BROAD_ARCHITECTURE_WITH_ORG_SCOPE', -2.0, [], [],
                           'architecture scope without organisational mandate')
        return _result('BROAD_ARCHITECTURE_WITH_ORG_SCOPE', 0.0, [], [],
                       'broad architecture with organisational scope')
    return _result('STRATEGIC_TECHNICAL_LEADERSHIP', 0.0, [], [],
                   'technical leadership shape')


def _result(classification, adjustment, phrases, rescue_hits, detail):
    return {
        'classification': classification,
        'adjustment': float(adjustment),
        'evidence': list(phrases),
        'counter_evidence': list(rescue_hits),
        'detail': detail,
    }


def _explain_operating(classification, evidence):
    phrases = ', '.join(evidence.phrases())
    if classification == 'POLITICAL_EXTERNAL':
        return 'external / political representation is a named responsibility ({0})'.format(phrases)
    if classification == 'STAKEHOLDER_HEAVY':
        return 'relationship and orchestration work dominates the mandate ({0})'.format(phrases)
    return 'balanced mandate with some orchestration weight ({0})'.format(phrases)


def _explain_career(classification, evidence):
    phrases = ', '.join(evidence.phrases())
    texts = {
        'CONSULTING_DELIVERY': 'consulting / customer delivery dominates the responsibilities ({0})',
        'ACCOUNT_ENGAGEMENT_MANAGEMENT': 'engagement and commercial ownership is the primary job ({0})',
        'PURE_FEATURE_ENGINEERING': 'primary scope is component / feature implementation ({0})',
        'PURE_SENIOR_IC': 'senior individual-contributor scope without organisational mandate ({0})',
    }
    return texts.get(classification, '{0}').format(phrases)


# --------------------------------------------------------------------------
# combined assessment
# --------------------------------------------------------------------------

def credits(charges):
    """Split what the base score already charged into per-adjustment credits.

    ``charges`` is what ``MatchScorer`` deducted through its
    ``deprioritized_keywords`` and low-scope rules, as ``{'term', 'points'}``
    entries.  Anything it already charged for a term that this module would
    charge for again is handed back here, which is the whole no-double-penalty
    mechanism: one signal, one deduction.
    """
    operating = career = 0.0
    for charge in charges or []:
        term = str(charge.get('term') or '').strip().lower()
        points = float(charge.get('points') or 0.0)
        if term in OPERATING_OVERLAP:
            operating += points
        if term in CAREER_OVERLAP:
            career += points
    return operating, career


def assess(job, charges=()):
    """Both adjustments for one job, already credited for base-score overlap."""
    title = normalize(job.get('title'))
    description = normalize(job.get('description') or job.get('excerpt') or '')[:20000]
    operating_credit, career_credit = credits(charges)
    style = operating_style(title, description, credit=operating_credit)
    direction = career_direction(title, description, credit=career_credit)
    style['credit'] = round(operating_credit, 1)
    direction['credit'] = round(career_credit, 1)
    return style, direction


def personal_fit(base_score, operating_adjustment, career_adjustment):
    """Personal Fit = Base + Operating Style + Career Direction, clamped 0-100."""
    total = float(base_score) + float(operating_adjustment) + float(career_adjustment)
    return max(0, min(100, int(round(total))))


#: The columns :func:`score_columns` fills, in the order the schema declares
#: them.  Both the scan pipeline and the rescore path write all of them, so a
#: job's stored base score and adjustments can never drift apart from the
#: personal fit that was ranked.
SCORE_COLUMNS = (
    'base_score', 'operating_style_adjustment', 'operating_style_class',
    'operating_style_detail', 'career_direction_adjustment',
    'career_direction_class', 'career_direction_detail', 'personal_fit_score',
)


def score_columns(scored):
    """The personal-fit columns for one ``MatchScorer.score`` result."""
    style = scored.get('operating_style') or {}
    direction = scored.get('career_direction') or {}
    base = int(scored.get('base_score', scored.get('score', 0)) or 0)
    return {
        'base_score': base,
        'operating_style_adjustment': float(style.get('adjustment') or 0.0),
        'operating_style_class': str(style.get('classification') or ''),
        'operating_style_detail': str(style.get('detail') or ''),
        'career_direction_adjustment': float(direction.get('adjustment') or 0.0),
        'career_direction_class': str(direction.get('classification') or ''),
        'career_direction_detail': str(direction.get('detail') or ''),
        'personal_fit_score': int(scored.get('personal_fit', scored.get('score', base)) or 0),
    }
