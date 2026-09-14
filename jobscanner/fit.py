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

Four rules shape every classifier here:

0.  **Missing evidence is not negative evidence.**  Both adjustments are
    deductions, and a deduction has to be earned by something the posting
    actually says.  A LinkedIn alert entry says a title, a company and a
    place; it does not say "this role is pure implementation" or "this role is
    stakeholder-heavy", so at LOW evidence (see :mod:`jobscanner.evidence`)
    neither adjustment fires at all and the Personal Fit Score is marked
    provisional.  At MEDIUM evidence they fire, but they are not allowed to
    reach the harsh end of their band: a thin posting is a thin posting, not a
    bad job.

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

from . import evidence as evidence_mod
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
    # Not a judgement, an admission: there is not enough description to judge.
    'INSUFFICIENT_EVIDENCE': (0.0, 0.0),
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

#: Evidence that the primary job is implementation - grouped into *families*,
#: because that is what makes the classifier evidence-based rather than
#: keyword-based.
#:
#: Each family is one distinct way a posting can say "this role builds things
#: with its own hands".  A hands-on IC classification needs
#: ``MIN_IC_FAMILIES`` of them, so a single occurrence of Python, Go,
#: Kubernetes, Terraform, "hands-on" or "engineering" can never produce one:
#: those live in ``IC_SUPPORTING`` and count for nothing until a real
#: responsibility family has already fired.
#:
#: Boilerplate that appears in any engineering posting - "design and build",
#: "develop and maintain", "code reviews" - is deliberately absent: an SRE
#: role says all three, and letting them count classified a Site Reliability
#: Engineer as feature engineering.
IC_RESPONSIBILITY_FAMILIES = {
    # writing production code is a primary responsibility
    'writes_production_code': [
        'write code', 'writing code', 'write production code',
        'writing production code', 'write software', 'writing software',
        'daily coding', 'coding daily', 'code every day', 'day to day coding',
        'hands on coding', 'coding skills', 'primarily coding', 'you will code',
        'write python', 'writing python', 'write go', 'writing go',
        'write java', 'writing java', 'write rust', 'writing rust',
        'write scala', 'writing scala', 'write typescript',
    ],
    # implementing features is the mandate
    'implements_features': [
        'implement features', 'implementing features', 'feature development',
        'develop features', 'build features', 'ship features', 'feature delivery',
        'delivery of features', 'implementation is the primary',
        'implementation is primary', 'implementation primary',
        'implementation focused', 'primarily implementation',
    ],
    # building backend / core services
    'builds_core_services': [
        'backend services', 'develop core services', 'build core services',
        'building core services', 'implement services', 'implementing services',
        'build microservices', 'develop microservices', 'core backend',
        'build the backend',
    ],
    # debugging production systems as a primary duty
    'debugs_production': [
        'debug production', 'debugging production', 'troubleshoot production',
        'troubleshooting production', 'debug live systems',
        'root cause analysis of production incidents',
    ],
    # heavy on-call operational ownership, carried personally
    'on_call_ownership': [
        'on call rotation', 'on call rota', 'on call duty', 'on call duties',
        'on call responsibilities', 'individual on call', 'personal on call',
        'participate in the on call', 'take part in the on call',
        'join the on call', '24 7 on call', 'carry the pager', 'pager rotation',
    ],
    # hands-on implementation of infrastructure / tooling
    'hands_on_implementation': [
        'hands on production engineering', 'hands on implementation',
        'hands on engineering work', 'hands on development', 'hands on build',
        'hands on operations', 'operate infrastructure', 'operating infrastructure',
        'operate the infrastructure', 'run the infrastructure',
        'operate production systems', 'operating production systems',
    ],
    # the posting says outright that there is no organisational mandate
    'declared_ic': [
        'individual contributor', 'as an individual contributor', 'ic role',
        'no direct reports', 'no reports', 'without direct reports',
        'not a people management role', 'non managerial',
    ],
}

#: Families whose presence makes the shape *feature engineering* rather than
#: senior IC work.  Everything else is hands-on senior IC.
FEATURE_FAMILIES = ('implements_features', 'builds_core_services', 'job_function')

#: An IC job function named in the *title*.  Counted as one family of its own,
#: because a title names the primary job - but on its own it is still only one
#: family, so it never classifies a role by itself.
IC_TITLE_SHAPES = [
    'software engineer', 'backend engineer', 'back end engineer', 'frontend engineer',
    'front end engineer', 'full stack engineer', 'fullstack engineer',
    'application developer', 'software developer', 'application engineer',
]

#: Never evidence on their own.  A language, a framework, an agile ritual or
#: the word "hands-on" only adds weight to a responsibility family that has
#: already fired - which is exactly the "coupled with implementation
#: responsibility" rule.
IC_SUPPORTING = [
    'python', 'go', 'golang', 'java', 'rust', 'typescript', 'javascript', 'scala',
    'kubernetes', 'terraform', 'ansible', 'docker', 'sql', 'react',
    'user stories', 'story points', 'product backlog', 'sprint', 'sprints',
    'scrum', 'agile ceremonies', 'feature backlog', 'product requirements',
    'build tooling', 'building tooling', 'develop tooling', 'code reviews',
    'pull requests', 'unit tests', 'hands on', 'deep technical expertise',
]

#: Evidence of real leadership / ownership scope, also grouped into families.
#:
#: This is what protects a role from a false IC classification (spec: "strong
#: leadership/ownership evidence should protect against false IC
#: classification").  It is read from the *responsibilities*, never from the
#: title: a Head of SRE whose description says "no reports, daily coding,
#: individual on-call" has no leadership family at all, and that is the whole
#: point - responsibilities win over titles.
LEADERSHIP_RESPONSIBILITY_FAMILIES = {
    'organizational_ownership': [
        'engineering organization', 'engineering organisation', 'sre organization',
        'sre organisation', 'technology organization', 'technology organisation',
        'platform organization', 'platform organisation', 'org wide',
        'organization wide', 'organisation wide', 'across the organization',
        'across the organisation', 'organizational capability',
        'organisational capability', 'operating model', 'organisational design',
        'organizational design',
    ],
    'team_leadership': [
        'lead a team', 'lead the team', 'leading a team', 'lead teams',
        'manage a team', 'managing a team', 'manage the team', 'manage teams',
        'manage engineering teams', 'managing engineering teams',
        'line management', 'people management', 'direct reports',
        'performance reviews', 'hiring', 'headcount', 'grow the team',
        'team leadership', 'mentorship', 'mentoring', 'coaching',
        'engineering managers',
    ],
    'strategy_and_roadmap': [
        'reliability strategy', 'sre strategy', 'platform strategy',
        'technical strategy', 'technology strategy', 'technical roadmap',
        'technology roadmap', 'platform roadmap', 'engineering roadmap',
        'define the strategy', 'set the strategy', 'shape the strategy',
        'technical vision', 'strategic direction', 'define the operating model',
    ],
    'multi_team_scope': [
        'multiple teams', 'several teams', 'multiple engineering teams',
        'cross team', 'across teams', 'multi team', 'portfolio of programs',
        'portfolio of programmes', 'portfolio ownership', 'cross team ownership',
    ],
    'planning_and_budget': [
        'budget', 'budget responsibility', 'budget ownership', 'workforce planning',
        'resource planning', 'annual planning', 'p l responsibility',
        'cost ownership', 'investment decisions',
    ],
    'service_ownership': [
        'slo ownership', 'sla ownership', 'own the slos', 'own the slas',
        'error budget policy', 'service level objectives', 'service level agreements',
        'availability targets', 'reliability of the platform',
        'accountable for reliability', 'accountable for availability',
    ],
}

#: Counter-evidence for the IC path: any leadership phrase, flattened.
LEADERSHIP_RESCUE_TERMS = [phrase for phrases in LEADERSHIP_RESPONSIBILITY_FAMILIES.values()
                           for phrase in phrases]

#: How many distinct responsibility families a hands-on IC classification
#: needs.  Two, so that one stray phrase can never produce one.
MIN_IC_FAMILIES = 2

#: At MEDIUM evidence an adjustment may not reach the harsh end of its band -
#: a thin posting is a thin posting, not a bad job (spec 14).
MEDIUM_EVIDENCE_CAP = 0.75

#: Back-compatible flat views of the IC vocabulary.  Nothing in this module
#: reads them; they are what the calibration tests assert against, and keeping
#: them derived means they cannot drift from the families above.
FEATURE_STRONG = (IC_TITLE_SHAPES
                  + IC_RESPONSIBILITY_FAMILIES['implements_features']
                  + IC_RESPONSIBILITY_FAMILIES['builds_core_services']
                  + IC_RESPONSIBILITY_FAMILIES['writes_production_code'])
FEATURE_SUPPORTING = list(IC_SUPPORTING)
IC_STRONG = list(IC_RESPONSIBILITY_FAMILIES['declared_ic'])

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
    # Not a judgement, an admission: there is not enough description to judge.
    'INSUFFICIENT_EVIDENCE': (0.0, 0.0),
    evidence_mod.HIGH_POTENTIAL: (0.0, 0.0),
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

    def __init__(self, title_hits, strong_hits, supporting_hits, families=()):
        self.title_hits = title_hits
        self.strong_hits = strong_hits
        self.supporting_hits = supporting_hits
        #: Distinct *kinds* of responsibility evidence, not phrase count.  Two
        #: phrases from one family are one way of saying the same thing.
        self.families = set(families)

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
    families = {'title'} if title_hits else set()
    if strong_hits:
        families.add('body')
    return Evidence(title_hits, strong_hits, supporting_hits, families)


def _gather_families(title, description, families_map, supporting,
                     title_shapes=(), title_family=''):
    """Evidence from a family map: which distinct kinds of signal are present.

    The families are the gate; the phrases are only the weight.  A posting
    that says "implement features" three times has said one thing once, which
    is why ``MIN_IC_FAMILIES`` counts families and not hits.
    """
    title_hits = _phrase_hits(title, title_shapes) if title_shapes else []
    families = set()
    strong_hits = []
    for name, phrases in families_map.items():
        hits = [p for p in _phrase_hits(description, phrases) if p not in title_hits]
        if hits:
            families.add(name)
            strong_hits.extend(hits)
    if title_hits and title_family:
        families.add(title_family)
    # Supporting words are worth nothing until a real family has fired.
    supporting_hits = _phrase_hits(description, supporting) if families else []
    return Evidence(title_hits, strong_hits, supporting_hits, families)


def ic_evidence(title, description):
    """How much the responsibilities say "this role implements things"."""
    return _gather_families(title, description, IC_RESPONSIBILITY_FAMILIES,
                            IC_SUPPORTING, IC_TITLE_SHAPES, 'job_function')


def leadership_evidence(title, description):
    """How much the responsibilities say "this role leads and owns things"."""
    return _gather_families(title, description, LEADERSHIP_RESPONSIBILITY_FAMILIES, [])


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

def _insufficient(level, high_potential=False):
    """The honest answer when the posting does not describe the job.

    Zero, never a deduction: an unknown role is not a bad role.  The
    classification says which of the two kinds of unknown it is, so the card
    can offer "enrich this first" instead of a score nobody should trust.
    """
    if high_potential:
        return _result(evidence_mod.HIGH_POTENTIAL, 0.0, [], [],
                       'leadership scope in the title but no responsibilities to read - '
                       'enrich before judging')
    return _result('INSUFFICIENT_EVIDENCE', 0.0, [], [],
                   'not enough description to judge the shape of this role')


def _cap_for(level, high):
    """The harshest deduction this evidence level is allowed to produce."""
    if level == evidence_mod.MEDIUM:
        return high * MEDIUM_EVIDENCE_CAP
    return high


def operating_style(title, description, credit=0.0, level=evidence_mod.HIGH):
    """How much of the mandate is relationship / external representation work."""
    if level == evidence_mod.LOW:
        return _insufficient(level)
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
    raw = min(_scaled(intensity, low, high), _cap_for(level, high))
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


def career_direction(title, description, credit=0.0, level=evidence_mod.HIGH,
                     high_potential=False):
    """Whether the role's shape matches the career direction, not just the stack.

    Four questions, in this order:

    1.  Is there enough description to judge at all?  If not, nothing is
        deducted and the role is marked for enrichment instead.
    2.  Does the posting name a consulting / engagement shape?  Unchanged from
        the original calibration - those classifiers already read
        responsibilities rather than titles.
    3.  Do at least ``MIN_IC_FAMILIES`` *distinct kinds* of implementation
        responsibility appear?  One mention of Python or "hands-on" is not a
        hands-on IC role.
    4.  If they do - does the posting describe leadership and ownership at
        least as loudly?  If it does, the IC reading loses.  A title never
        wins this argument on its own, in either direction: a Head of SRE that
        describes daily coding and no reports is an IC role, and a Site
        Reliability *Engineer* that describes owning an organisation is not.
    """
    if level == evidence_mod.LOW:
        return _insufficient(level, high_potential)

    blob = '{0} {1}'.format(title, description).strip()
    commercial = {
        'CONSULTING_DELIVERY': _gather(title, description,
                                       CONSULTING_STRONG, CONSULTING_SUPPORTING),
        'ACCOUNT_ENGAGEMENT_MANAGEMENT': _gather(title, description,
                                                 ENGAGEMENT_STRONG, ENGAGEMENT_SUPPORTING),
    }
    ic = ic_evidence(title, description)
    leadership = leadership_evidence(title, description)

    classification = max(commercial, key=lambda k: commercial[k].intensity)
    evidence = commercial[classification]
    commercial_fired = evidence.has_strong and evidence.intensity >= GATE

    # The IC reading has to clear two bars the commercial ones do not: enough
    # distinct families, and more of them than the leadership evidence.
    ic_fired = len(ic.families) >= MIN_IC_FAMILIES and len(ic.families) > len(leadership.families)

    if not commercial_fired and not ic_fired:
        return _wanted_shape(title, blob, leadership)

    if ic_fired and (not commercial_fired or ic.intensity > evidence.intensity):
        # A posting that says outright "no direct reports" has named the shape
        # itself; that reading outranks what the implementation verbs suggest.
        classification = ('PURE_SENIOR_IC'
                          if 'declared_ic' in ic.families
                          or not ic.families & set(FEATURE_FAMILIES)
                          else 'PURE_FEATURE_ENGINEERING')
        evidence = ic
        rescue_terms = LEADERSHIP_RESCUE_TERMS
        corroborated = commercial_fired
    else:
        # A title that names a wanted shape outranks body-level counter-evidence
        # for the commercial classifiers: "Site Reliability Engineer -
        # Application Edge" is an SRE role even though the text mentions
        # clients; only a negative signal in the *title* may overrule it.
        if not evidence.title_hits and _names_wanted_shape(title):
            return _wanted_shape(title, blob, leadership)
        rescue_terms = ORG_SCOPE
        # When a second poor shape clears the gate on its own evidence, the
        # posting has named its shape twice in two different ways - a Principal
        # Delivery Consultant that also says "as an individual contributor".
        # Technical vocabulary elsewhere does not buy that back.
        corroborated = ic_fired or sum(
            1 for e in commercial.values() if e.has_strong and e.intensity >= GATE) > 1

    low, high = CAREER_DIRECTION_CLASSES[classification]
    raw = min(_scaled(evidence.intensity, low, high), _cap_for(level, high))

    if corroborated:
        rescue, rescue_hits = 0.0, []
    else:
        rescue, rescue_hits = _rescue(blob, rescue_terms, bool(evidence.title_hits))

    penalty = max(0.0, raw - rescue - max(0.0, credit))
    if penalty <= 0:
        return _wanted_shape(title, blob, leadership)

    penalty = min(penalty, -CAREER_DIRECTION_FLOOR)
    return _result(classification, -round(penalty, 1), evidence.phrases(), rescue_hits[:3],
                   _explain_career(classification, evidence))


def _names_wanted_shape(title):
    """Does the title itself name one of the preferred role shapes?"""
    return bool(_phrase_hits(title, RELIABILITY_SHAPE)
                or _phrase_hits(title, PROGRAM_SHAPE)
                or _phrase_hits(title, MANAGEMENT_SHAPE))


def _wanted_shape(title, blob, leadership=None):
    """Name the preferred shape a posting matches, so a 0 is explained too."""
    if leadership is not None and len(leadership.families) >= 2:
        return _result('STRATEGIC_TECHNICAL_LEADERSHIP', 0.0,
                       leadership.phrases(), [],
                       'leadership and ownership scope described in the responsibilities '
                       '({0})'.format(', '.join(sorted(leadership.families)[:3]).replace('_', ' ')))
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
        'PURE_FEATURE_ENGINEERING': 'the responsibilities describe feature implementation as the '
                                    'primary job ({0})',
        'PURE_SENIOR_IC': 'the responsibilities describe hands-on individual work rather than '
                          'organisational ownership ({0})',
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


def assess(job, charges=(), report=None):
    """Both adjustments for one job, already credited for base-score overlap.

    ``report`` is the evidence verdict from :mod:`jobscanner.evidence`; it is
    computed here when the caller has not already done it.  It decides whether
    the adjustments are allowed to fire at all, which is the one rule that
    keeps a missing description from reading as a bad job.

    ``excerpt`` is deliberately not a description fallback: an imported alert
    entry keeps its informational line there, and scoring that as if it
    described the role is exactly the defect this release fixes.
    """
    report = report or evidence_mod.assess(job)
    level = report['level']
    title = normalize(job.get('title'))
    description = normalize(evidence_mod.description_of(job))[:20000]
    operating_credit, career_credit = credits(charges)
    style = operating_style(title, description, credit=operating_credit, level=level)
    direction = career_direction(title, description, credit=career_credit, level=level,
                                 high_potential=report['high_potential'])
    for verdict in (style, direction):
        verdict['evidence_level'] = level
        verdict['provisional'] = bool(report['provisional'])
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
    'evidence_level', 'evidence_detail', 'enrichment_state', 'fit_confidence',
    'fit_provisional', 'high_potential',
)


def score_columns(scored, job=None):
    """The personal-fit columns for one ``MatchScorer.score`` result.

    The evidence verdict travels with the score because it is *about* the
    score: "78, confidence HIGH" and "78, provisional" are two different
    statements, and the second one must never be shown as the first.

    When a caller passes a score that predates the evidence model, the verdict
    is read off ``job`` rather than defaulted.  Defaulting would be a guess in
    the one direction the whole release is about: it would mark a job with a
    perfectly good description as needing enrichment.
    """
    style = scored.get('operating_style') or {}
    direction = scored.get('career_direction') or {}
    report = scored.get('evidence') or (evidence_mod.assess(job) if job is not None else {})
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
        'evidence_level': str(report.get('level') or evidence_mod.LOW),
        'evidence_detail': str(report.get('detail') or ''),
        'enrichment_state': str(report.get('state') or evidence_mod.NEEDS_ENRICHMENT),
        'fit_confidence': str(report.get('confidence') or evidence_mod.LOW),
        'fit_provisional': 1 if report.get('provisional') else 0,
        'high_potential': 1 if report.get('high_potential') else 0,
    }
