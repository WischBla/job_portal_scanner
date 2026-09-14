"""Career scope: is this role realistic for the career direction at all?

Five questions are kept apart in this codebase, and this module answers the
fifth one:

====================  ====================================================
Lifecycle             is the posting still live?        (``state``)
Enrichment            how much do we know about it?     (:mod:`evidence`)
Base match score      is it technically relevant?       (:mod:`scoring`)
Personal fit          is it the shape of job I want?    (:mod:`fit`)
**Career scope**      **would I realistically apply?**  *(this module)*
====================  ====================================================

The distinction that makes this its own module: a *Site Reliability Engineer -
Observability* posting is technically relevant (the domain is exactly right),
scores well and is perfectly alive - and it is still not a job this profile
would apply for, because the mandate is writing Go, building tooling and
carrying a pager.  Relevance is not fit, and fit is not scope.

Three values:

``IN_SCOPE``      leadership, ownership or technical-program scope.
``OUT_OF_SCOPE``  a hands-on implementation role.
``UNCERTAIN``     not enough evidence to say either.

Five rules shape every decision here:

0.  **OUT_OF_SCOPE is not EXPIRED.**  Nothing in this module deletes, retires
    or re-states a job.  It writes three columns and the Jobs screen uses them
    to choose a default *view*; an out-of-scope job is one filter chip away at
    all times and keeps its score, its state and its history.

1.  **Responsibilities override titles.**  A *Head of SRE* whose description
    says "no direct reports, daily coding, individual on-call" is an IC role,
    and a *Site Reliability Engineer* who owns an organisation is not.  Titles
    only decide when there are no responsibilities to read.

2.  **Never from one isolated word.**  An implementation verdict needs
    ``MIN_IMPLEMENTATION_FAMILIES`` *distinct kinds* of implementation
    responsibility.  A language name is never evidence on its own: "good
    understanding of Python" and "design, implement and maintain production
    Python services" are different statements and only the second one is a
    mandate.

3.  **Organisational ownership protects.**  Team leadership, multi-team scope,
    platform ownership, roadmap ownership, governance or budget outweigh the
    implementation verbs around them.  A technical leader may still read code.

4.  **Missing evidence is not negative evidence.**  A thin posting is
    ``UNCERTAIN`` and stays in the default list.  The single exception the
    specification allows is a title that names a software-development job
    function outright - *Senior Backend Engineer* says what it is even with no
    description - and that exception is deliberately narrow: the ambiguous
    engineer titles (SRE, platform, DevOps, cloud) stay ``UNCERTAIN`` until a
    description says which of the two roles they are.
"""

import re

from . import evidence as evidence_mod
from . import fit
from .locations import fold

# -- the three values ------------------------------------------------------
IN_SCOPE = 'IN_SCOPE'
OUT_OF_SCOPE = 'OUT_OF_SCOPE'
UNCERTAIN = 'UNCERTAIN'
SCOPES = (IN_SCOPE, UNCERTAIN, OUT_OF_SCOPE)

#: What the default Jobs view shows.  ``OUT_OF_SCOPE`` is missing from this
#: tuple and that is the entire visibility rule; it is not a lifecycle.
VISIBLE_BY_DEFAULT = (IN_SCOPE, UNCERTAIN)

#: Human labels for the card and the filter chips.
SCOPE_LABELS = {IN_SCOPE: 'In scope', UNCERTAIN: 'Uncertain', OUT_OF_SCOPE: 'Out of scope'}

# -- gates -----------------------------------------------------------------
#: Distinct kinds of implementation responsibility needed before a role is
#: called hands-on.  Two, so that one stray phrase can never produce one.
MIN_IMPLEMENTATION_FAMILIES = 2
#: Distinct kinds of leadership responsibility that establish scope on their
#: own, with no help from the title.
MIN_LEADERSHIP_FAMILIES = 2
#: How much more implementation evidence than ownership evidence it takes to
#: overturn a leadership *title*.  A title names the primary job, so one extra
#: implementation family is not a disagreement - it is an engineering team.
TITLE_LEADERSHIP_MARGIN = 2


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------

def normalize(value):
    """Fold, then flatten punctuation, so 'on-call' == 'on call'."""
    text = fold(value)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

# --------------------------------------------------------------------------
# title vocabulary
# --------------------------------------------------------------------------

#: Title shapes that name a leadership, management or program mandate.
#:
#: Bare "lead" is deliberately absent.  "Senior Backend Engineer / Technical
#: Lead" is a backend engineer, and treating every "Lead" as management is
#: exactly the title-driven reasoning this module is built to avoid - so only
#: *lead* compounds that name an organisational or platform scope are listed.
LEADERSHIP_TITLE_SHAPES = [
    'head of', 'head', 'deputy head', 'global head', 'group head',
    'director', 'senior director', 'managing director', 'vice president', 'vp',
    'chief', 'cto', 'cio', 'ciso',
    'engineering manager', 'senior engineering manager', 'group manager',
    'senior manager', 'department manager', 'delivery manager',
    'leiter', 'leiterin', 'bereichsleiter', 'abteilungsleiter', 'teamleiter',
    'technical program manager', 'technical programme manager',
    'program manager', 'programme manager', 'technical program lead',
    'technical programme lead', 'program lead', 'programme lead',
    'portfolio lead', 'portfolio manager',
    'engineering lead', 'engineering excellence lead', 'excellence lead',
    'transformation lead', 'technology transformation lead',
    'technical operations lead', 'technology operations lead',
    'platform lead', 'platform engineering lead', 'reliability lead',
    'sre lead', 'operations lead', 'infrastructure lead', 'technology lead',
    'team lead', 'teamlead', 'tech lead manager',
]

#: The subset of those that name a *program* mandate rather than a line one.
#: Only used to pick the sentence on the card.
PROGRAM_TITLE_SHAPES = [
    'technical program manager', 'technical programme manager', 'program manager',
    'programme manager', 'technical program lead', 'technical programme lead',
    'program lead', 'programme lead', 'portfolio lead', 'portfolio manager',
]

#: ... but a leadership title is only *this* career direction when it also
#: names a technology domain.  "Head of Gastronomy" is a leadership role and
#: not this one - it stays UNCERTAIN rather than being excluded, because the
#: OUT_OF_SCOPE verdict in this module means "hands-on implementation" and
#: nothing else.
TECHNICAL_DOMAINS = [
    'engineering', 'engineer', 'technology', 'technical', 'technik', 'tech',
    'platform', 'plattform', 'infrastructure', 'infrastruktur', 'cloud',
    'reliability', 'sre', 'site reliability', 'operations', 'operational',
    'devops', 'devsecops', 'observability', 'architecture', 'architect',
    'data', 'software', 'it', 'digital', 'security', 'cyber', 'program',
    'programme', 'transformation', 'automation', 'production', 'systems',
    'system', 'delivery', 'network', 'database', 'ai', 'machine learning',
    'quality', 'service', 'application', 'applications', 'product engineering',
    'informatik', 'ict', 'devsecops', 'sre', 'observability', 'plattform',
]

#: Titles that name a software-development job function outright.  These are
#: the only titles allowed to produce an OUT_OF_SCOPE verdict with no
#: description behind them (specification 10).
EXPLICIT_IC_TITLES = [
    'software engineer', 'software developer', 'software development engineer',
    'backend engineer', 'back end engineer', 'backend developer',
    'back end developer', 'frontend engineer', 'front end engineer',
    'frontend developer', 'front end developer', 'full stack engineer',
    'fullstack engineer', 'full stack developer', 'fullstack developer',
    'mobile engineer', 'mobile developer', 'ios engineer', 'ios developer',
    'android engineer', 'android developer', 'web developer',
    'application developer', 'applications developer', 'application engineer',
    'applications engineer', 'embedded software engineer', 'firmware engineer',
    'embedded engineer', 'game developer', 'python developer', 'java developer',
    'go developer', 'rust developer', 'c developer', 'php developer',
    'net developer', 'dotnet developer', 'javascript developer',
    'typescript developer', 'scala developer', 'kotlin developer',
    'swift developer', 'ruby developer', 'sdk developer',
    'data engineer', 'analytics engineer', 'machine learning engineer',
    'ml engineer', 'ai engineer', 'research engineer', 'algorithm engineer',
]

#: Engineer titles that are an IC job *by default* but routinely carry a real
#: organisational mandate.  They need a description before they are excluded.
AMBIGUOUS_IC_TITLES = [
    'site reliability engineer', 'reliability engineer', 'sre engineer',
    'devops engineer', 'devsecops engineer', 'cloud engineer',
    'platform engineer', 'infrastructure engineer', 'systems engineer',
    'system engineer', 'network engineer', 'security engineer',
    'database engineer', 'operations engineer', 'automation engineer',
    'integration engineer', 'performance engineer', 'support engineer',
    'qa engineer', 'test engineer', 'quality engineer', 'solutions engineer',
    'solution engineer', 'system administrator', 'systems administrator',
    'systems specialist', 'cloud specialist',
]

#: Any architecture title.  Never a verdict on its own (specification 6).
ARCHITECT_TITLES = [
    'architect', 'architecture', 'architekt', 'chief architect',
    'enterprise architect', 'principal architect', 'platform architect',
    'solution architect', 'solutions architect', 'software architect',
    'application architect', 'cloud architect', 'data architect',
    'integration architect', 'security architect', 'domain architect',
]

#: Architecture that is direction-setting rather than delivery.  Read from the
#: responsibilities, as everywhere else in this module.
STRATEGIC_ARCHITECTURE = [
    'architecture strategy', 'architectural strategy', 'technology strategy',
    'technical strategy', 'architecture roadmap', 'technology roadmap',
    'technical roadmap', 'architecture governance', 'technical governance',
    'technology governance', 'architecture standards', 'technology standards',
    'engineering standards', 'reference architecture', 'architecture principles',
    'target architecture', 'enterprise architecture', 'architecture vision',
    'architecture review board', 'design authority', 'cross team architecture',
    'cross organization architecture', 'cross organisation architecture',
    'platform direction', 'technology direction', 'strategic direction',
    'executive decision', 'decision support', 'technology transformation',
    'organizational standards', 'organisational standards', 'operating model',
]


# --------------------------------------------------------------------------
# responsibility vocabulary
# --------------------------------------------------------------------------

#: Additional implementation families on top of the ones the personal-fit
#: model already reads.  Kept as families rather than a flat list because the
#: *number of distinct kinds* is the gate: "write production code" three times
#: is one statement said three times.
EXTRA_IMPLEMENTATION_FAMILIES = {
    # the language is named as something the role *does*, not as background
    'codes_in_language': [
        'code in python', 'code in go', 'code in java', 'code in rust',
        'code in c', 'coding in python', 'coding in java', 'coding in go',
        'python coding', 'java coding', 'go coding', 'programming in python',
        'programming in java', 'programming in go', 'write clean code',
        'writing clean code', 'write efficient code', 'production python',
        'production go', 'production java', 'python services', 'go services',
        'java services', 'hands on software development',
        'software development in', 'day to day implementation',
        'daily implementation', 'implement and maintain', 'develop and operate',
    ],
    # building the services / components themselves
    'develops_services': [
        'service development', 'develop services', 'developing services',
        'develop and maintain services', 'implement and maintain services',
        'maintain production services', 'application components',
        'implement application components', 'build application components',
        'develop application components', 'develop components',
    ],
    # building tooling and pipelines by hand
    'builds_tooling': [
        'build tooling', 'building tooling', 'build production tooling',
        'develop tooling', 'developing tooling', 'implement tooling',
        'implementing tooling', 'build internal tooling',
        'build observability tooling', 'build automation tooling',
        'build and maintain tooling', 'implement observability pipelines',
        'build observability pipelines', 'build pipelines',
    ],
    # APIs as a deliverable
    'develops_apis': [
        'develop apis', 'developing apis', 'develop api', 'build apis',
        'building apis', 'implement apis', 'implementing apis',
        'develop rest apis', 'design and implement apis', 'api development',
    ],
    # debugging application code specifically
    'debugs_application_code': [
        'debug application code', 'debug the application', 'debug code',
        'troubleshoot code', 'fix bugs', 'bug fixing', 'resolve defects',
    ],
}

#: Additional ownership families on top of the personal-fit model's, covering
#: the shapes specification 8 names as protective.
EXTRA_LEADERSHIP_FAMILIES = {
    'technical_governance': [
        'technical governance', 'technology governance', 'architecture governance',
        'governance framework', 'engineering standards', 'technology standards',
        'organizational standards', 'organisational standards', 'design authority',
        'architecture review board', 'decision rights', 'steering committee',
        'steering board', 'define standards', 'set standards',
    ],
    'portfolio_and_programs': [
        'technical portfolio', 'portfolio', 'program portfolio',
        'programme portfolio', 'cross team programs', 'cross team programmes',
        'cross organization programs', 'cross organisation programmes',
        'dependencies across teams', 'risks and dependencies',
        'risk and dependencies', 'manage dependencies', 'program governance',
        'programme governance', 'technical program management',
        'technical programme management',
    ],
    'platform_ownership': [
        'platform ownership', 'own the platform', 'ownership of the platform',
        'service ownership', 'own the roadmap', 'roadmap ownership',
        'product ownership', 'own the technology', 'end to end ownership',
        'accountable for the platform', 'operational excellence',
    ],
    'executive_scope': [
        'executive stakeholders', 'executive leadership', 'executive level',
        'board level', 'c level stakeholders', 'report to the cto',
        'reports to the cto', 'reporting to the cto', 'report to the cio',
        'executive sponsors', 'executive reporting',
    ],
    'capability_building': [
        'capability building', 'build the capability', 'engineering capability',
        'engineering operating model', 'technology operating model',
        'engineering excellence', 'engineering productivity',
        'ways of working', 'organizational improvement',
        'organisational improvement', 'continuous improvement culture',
        'technology transformation', 'transformation program',
        'transformation programme',
    ],
}

#: Leadership vocabulary that is **normal senior behaviour**, not a mandate.
#:
#: This is the single most important list in the module and it is a
#: *subtraction*.  Nearly every senior engineering posting says it wants
#: someone who mentors, coaches, works cross-team, cares about operational
#: excellence and helps with hiring - and in the personal-fit model those
#: phrases are perfectly good evidence, because there they only ever move a
#: score by a point or two.  Here a single one of them decides whether a job
#: is *shown at all*, and at that weight they are worthless: they rescued
#: *Staff Software Engineer* and *Site Reliability Engineer - Storage* from a
#: classification that was otherwise unanimous.
#:
#: So the career-scope reading of "ownership" is the strict one: a phrase has
#: to name the mandate itself (manage a team, own the roadmap, budget
#: responsibility, define the operating model), not a quality the person
#: should have.  ``fit`` keeps its own, broader reading - the two models
#: measure different things and this is where they are allowed to differ.
WEAK_LEADERSHIP_PHRASES = [
    # things a senior individual contributor does every day
    'hiring', 'mentorship', 'mentoring', 'coaching',
    'cross team', 'across teams', 'across the organization', 'across the organisation',
    'technical vision', 'strategic direction', 'budget',
    'operational excellence', 'end to end ownership', 'service ownership',
    'product ownership', 'engineering standards', 'steering committee',
    'steering board', 'ways of working', 'engineering productivity',
    'continuous improvement culture', 'portfolio',
]

#: Phrases that *deny* a leadership mandate.  They are scrubbed out of the
#: text before the leadership families are read, because "no direct reports"
#: contains "direct reports" and a naive match would turn the clearest
#: statement of an IC role into evidence of leadership.
LEADERSHIP_NEGATIONS = [
    'no direct reports', 'without direct reports', 'no reports',
    'not a people management role', 'no people management',
    'no line management', 'without line management', 'non managerial',
    'this is not a management role', 'not a management role',
    'individual contributor role', 'as an individual contributor',
]

#: Reasons shown on the card.  Short by design - the card must not turn into a
#: second job description.
REASON_SOFTWARE_IC = 'Hands-on software engineering role'
REASON_SRE_IC = 'Implementation-heavy SRE IC role'
REASON_ARCHITECTURE_IC = 'Hands-on architecture role'
REASON_PLATFORM_IC = 'Hands-on platform / infrastructure role'
REASON_DATA_IC = 'Hands-on data engineering role'
REASON_DECLARED_IC = 'Individual contributor role without organisational scope'

REASON_LEADERSHIP = 'Leadership and ownership scope'
REASON_MANAGEMENT_TITLE = 'Engineering / technology management role'
REASON_PROGRAM = 'Technical program leadership'
REASON_STRATEGIC_ARCHITECTURE = 'Strategic architecture and governance'

REASON_THIN = 'Not enough description to judge the scope'
REASON_THIN_LEADERSHIP = 'Leadership title, no description yet'
REASON_UNCLEAR = 'No clear leadership or implementation evidence'
REASON_NON_TECHNICAL = 'Leadership role outside the technology domain'


def _merge(base, extra, drop=()):
    """One family map from two, without mutating either."""
    dropped = {normalize(phrase) for phrase in drop}
    merged = {}
    for source in (base, extra):
        for name, phrases in source.items():
            kept = [p for p in phrases if normalize(p) not in dropped]
            if kept or name in merged:
                merged[name] = merged.get(name, []) + kept
    return {name: phrases for name, phrases in merged.items() if phrases}


#: The two family maps the classifier reads.  Derived from the personal-fit
#: vocabulary rather than copied, so the two models cannot drift apart on what
#: "implementation" and "ownership" mean - minus the phrases that are normal
#: senior behaviour rather than an organisational mandate.
IMPLEMENTATION_FAMILIES = _merge(fit.IC_RESPONSIBILITY_FAMILIES,
                                 EXTRA_IMPLEMENTATION_FAMILIES)
LEADERSHIP_FAMILIES = _merge(fit.LEADERSHIP_RESPONSIBILITY_FAMILIES,
                             EXTRA_LEADERSHIP_FAMILIES,
                             drop=WEAK_LEADERSHIP_PHRASES)


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------

def _hits(text, phrases):
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


def _families(text, family_map):
    """Which distinct kinds of responsibility the text describes."""
    found = {}
    for name, phrases in family_map.items():
        hits = _hits(text, phrases)
        if hits:
            found[name] = hits
    return found


def scrub_negations(text):
    """Remove denials of a leadership mandate before reading ownership.

    "No direct reports" is the clearest possible statement that a role has no
    organisational scope; it must never be counted as evidence that it has
    one.
    """
    for phrase in LEADERSHIP_NEGATIONS:
        needle = normalize(phrase)
        if needle:
            text = re.sub(r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


# --------------------------------------------------------------------------
# the classifier
# --------------------------------------------------------------------------

def _ic_reason(title):
    """Which flavour of hands-on role this is, for the one-line card reason.

    Most specific job function first: "Senior Software Engineer, Data Science"
    is a software engineer who works on data, not a data engineer.
    """
    if _hits(title, ARCHITECT_TITLES):
        return REASON_ARCHITECTURE_IC
    if _hits(title, ['site reliability', 'sre', 'reliability']):
        return REASON_SRE_IC
    if _hits(title, ['software engineer', 'software developer', 'software development engineer',
                     'backend engineer', 'back end engineer', 'frontend engineer',
                     'front end engineer', 'full stack engineer', 'fullstack engineer',
                     'application developer', 'application engineer']):
        return REASON_SOFTWARE_IC
    if _hits(title, ['data engineer', 'machine learning', 'ml engineer', 'ai engineer',
                     'analytics engineer', 'data']):
        return REASON_DATA_IC
    if _hits(title, ['platform', 'infrastructure', 'cloud', 'devops', 'devsecops',
                     'systems', 'system', 'network', 'observability']):
        return REASON_PLATFORM_IC
    return REASON_SOFTWARE_IC


def _detail(scope, reason, implementation, leadership, extra=''):
    """Why this verdict, in one readable sentence.

    Every classification is explainable on the card: an exclusion that cannot
    be read back is an exclusion nobody can correct.
    """
    def names(found):
        return ', '.join(sorted(found)[:3]).replace('_', ' ')

    if scope == OUT_OF_SCOPE and implementation:
        return '{0}: the responsibilities describe {1}{2}.'.format(
            reason, names(implementation),
            ' with no organisational ownership' if not leadership else
            ' and only {0}'.format(names(leadership)))
    if scope == OUT_OF_SCOPE:
        return '{0}: {1}'.format(reason, extra or 'the title names a software-development '
                                                  'job function.')
    if scope == IN_SCOPE and leadership:
        return '{0}: the responsibilities describe {1}.'.format(reason, names(leadership))
    if scope == IN_SCOPE:
        return '{0}: {1}'.format(reason, extra or 'the title names a leadership mandate in '
                                                  'a technology domain.')
    return extra or '{0}.'.format(reason)


def _verdict(scope, reason, detail, implementation=None, leadership=None, level=''):
    return {
        'scope': scope,
        'reason': reason,
        'detail': detail,
        'evidence_level': level,
        'implementation_families': sorted(implementation or {}),
        'leadership_families': sorted(leadership or {}),
    }


def classify(job, report=None):
    """The career-scope verdict for one job.

    ``report`` is the evidence verdict from :mod:`jobscanner.evidence`.  It
    decides whether the responsibilities are allowed to speak at all: with a
    LOW-evidence posting there are no responsibilities, only a title, and a
    title is allowed to settle exactly one case (an explicit
    software-development job function) and to raise no others.

    The score is never read.  Ranking and visibility are separate questions
    and this function is the visibility one.
    """
    report = report or evidence_mod.assess(job)
    level = report.get('level') or evidence_mod.LOW
    title = normalize(job.get('title'))
    description = normalize(evidence_mod.description_of(job))[:20000]
    blob = '{0} {1}'.format(title, description).strip()

    title_leadership = _hits(title, LEADERSHIP_TITLE_SHAPES)
    title_technical = _hits(title, TECHNICAL_DOMAINS)
    title_explicit_ic = _hits(title, EXPLICIT_IC_TITLES)
    title_ambiguous_ic = _hits(title, AMBIGUOUS_IC_TITLES)
    title_architect = _hits(title, ARCHITECT_TITLES)

    # -- no description: the title decides, and only where it is explicit ---
    if level == evidence_mod.LOW:
        if title_explicit_ic and not title_leadership:
            reason = _ic_reason(title)
            return _verdict(OUT_OF_SCOPE, reason,
                            _detail(OUT_OF_SCOPE, reason, {}, {},
                                    'the title names a software-development job function '
                                    '("{0}") and nothing in the posting says otherwise.'
                                    .format(title_explicit_ic[0])),
                            level=level)
        if title_leadership and title_technical:
            return _verdict(UNCERTAIN, REASON_THIN_LEADERSHIP,
                            'Leadership scope in the title but no description yet - '
                            'enrich this before judging it.', level=level)
        return _verdict(UNCERTAIN, REASON_THIN,
                        'Only a title, a company and a location - too thin to judge '
                        'the scope of the role.', level=level)

    # -- there is a description: the responsibilities decide ----------------
    implementation = _families(description, IMPLEMENTATION_FAMILIES)
    leadership = _families(scrub_negations(blob), LEADERSHIP_FAMILIES)

    # An IC job function in the title counts as one implementation family of
    # its own - a title names the primary job - but never more than one, so it
    # can still not classify a role by itself.
    if (title_explicit_ic or title_ambiguous_ic) and not title_leadership:
        implementation = dict(implementation)
        implementation.setdefault('job_function', title_explicit_ic + title_ambiguous_ic)

    # A title that names a software-development job function has said what the
    # primary job is, and only a described organisational mandate overturns it:
    # not one leadership phrase, but more distinct ownership evidence than
    # implementation evidence.  "Senior Backend Engineer / Technical Lead" that
    # mentors juniors is a backend engineer who mentors juniors.
    if title_explicit_ic and not title_leadership:
        if (len(leadership) >= MIN_LEADERSHIP_FAMILIES
                and len(leadership) > len(implementation)):
            return _verdict(IN_SCOPE, REASON_LEADERSHIP,
                            _detail(IN_SCOPE, REASON_LEADERSHIP, implementation, leadership),
                            implementation, leadership, level)
        reason = _ic_reason(title)
        return _verdict(OUT_OF_SCOPE, reason,
                        _detail(OUT_OF_SCOPE, reason, implementation, leadership,
                                'the title names a software-development job function '
                                '("{0}") and the responsibilities describe no '
                                'organisational mandate.'.format(title_explicit_ic[0])),
                        implementation, leadership, level)

    # Elsewhere implementation has to out-number ownership to win - and when
    # the title itself names a leadership mandate it has to out-number it
    # clearly, so a Head of Platform whose team builds tooling keeps its scope
    # while a Head of SRE with no reports and daily coding does not.
    margin = TITLE_LEADERSHIP_MARGIN if title_leadership else 1
    implementation_dominant = (len(implementation) >= MIN_IMPLEMENTATION_FAMILIES
                               and len(implementation) >= len(leadership) + margin)

    if implementation_dominant:
        reason = (REASON_DECLARED_IC
                  if 'declared_ic' in implementation and len(implementation) == 2
                  else _ic_reason(title))
        return _verdict(OUT_OF_SCOPE, reason,
                        _detail(OUT_OF_SCOPE, reason, implementation, leadership),
                        implementation, leadership, level)

    # Organisational ownership protects: a technical leader may still read
    # code, and the implementation verbs around a real mandate do not make the
    # mandate an implementation job.
    if len(leadership) >= MIN_LEADERSHIP_FAMILIES or (leadership and title_leadership):
        if not title_technical:
            # Real leadership, different profession.  UNCERTAIN rather than
            # OUT_OF_SCOPE: in this module OUT_OF_SCOPE means "hands-on
            # implementation" and nothing else, so a Head of Gastronomy is
            # listed and honestly labelled instead of being excluded.
            return _verdict(UNCERTAIN, REASON_NON_TECHNICAL,
                            'Genuine leadership scope, but the title names no technology '
                            'domain.', implementation, leadership, level)
        reason = REASON_PROGRAM if _hits(title, PROGRAM_TITLE_SHAPES) else REASON_LEADERSHIP
        return _verdict(IN_SCOPE, reason,
                        _detail(IN_SCOPE, reason, implementation, leadership),
                        implementation, leadership, level)

    # The ambiguous engineer titles - SRE, platform, DevOps, cloud - are an IC
    # job by default and a real mandate often enough that they are never
    # excluded on the title alone.  Once there is a description and it
    # describes no ownership at all, the default reading stands.
    if title_ambiguous_ic and not title_leadership and not leadership:
        reason = _ic_reason(title)
        return _verdict(OUT_OF_SCOPE, reason,
                        _detail(OUT_OF_SCOPE, reason, implementation, {},
                                'the title names an engineering job function ("{0}") and the '
                                'responsibilities describe no organisational ownership.'
                                .format(title_ambiguous_ic[0])),
                        implementation, leadership, level)

    if title_leadership and title_technical:
        return _verdict(IN_SCOPE, REASON_MANAGEMENT_TITLE,
                        _detail(IN_SCOPE, REASON_MANAGEMENT_TITLE, implementation, leadership),
                        implementation, leadership, level)

    # Architecture: strategy and governance are in scope, delivery is not, and
    # the word "Architect" on its own decides neither.
    if title_architect:
        strategic = _hits(blob, STRATEGIC_ARCHITECTURE)
        if strategic:
            return _verdict(IN_SCOPE, REASON_STRATEGIC_ARCHITECTURE,
                            '{0}: the responsibilities describe {1}.'.format(
                                REASON_STRATEGIC_ARCHITECTURE, ', '.join(strategic[:3])),
                            implementation, leadership, level)
        return _verdict(UNCERTAIN, REASON_UNCLEAR,
                        'An architecture role whose responsibilities name neither a '
                        'strategy mandate nor hands-on delivery.',
                        implementation, leadership, level)

    if title_leadership:
        return _verdict(UNCERTAIN, REASON_NON_TECHNICAL,
                        'A leadership title, but the role names no technology domain.',
                        implementation, leadership, level)

    return _verdict(UNCERTAIN, REASON_UNCLEAR,
                    'The responsibilities describe neither organisational ownership nor '
                    'hands-on implementation clearly enough to decide.',
                    implementation, leadership, level)


# --------------------------------------------------------------------------
# persistence
# --------------------------------------------------------------------------

#: The columns :func:`scope_columns` fills.  Deliberately separate from
#: ``fit.SCORE_COLUMNS``: career scope is not a score and must never be read
#: as one.
SCOPE_COLUMNS = ('career_scope', 'career_scope_reason', 'career_scope_detail')


def scope_columns(job, report=None):
    """The three stored columns for one job."""
    verdict = classify(job, report)
    return {
        'career_scope': verdict['scope'],
        'career_scope_reason': verdict['reason'],
        'career_scope_detail': verdict['detail'],
    }


def label(scope):
    return SCOPE_LABELS.get(str(scope or ''), SCOPE_LABELS[UNCERTAIN])


def is_visible_by_default(scope):
    """Visibility, and only visibility.  Nothing here is a lifecycle rule."""
    return str(scope or UNCERTAIN) in VISIBLE_BY_DEFAULT


__all__ = ['IN_SCOPE', 'OUT_OF_SCOPE', 'UNCERTAIN', 'SCOPES', 'VISIBLE_BY_DEFAULT',
           'SCOPE_LABELS', 'SCOPE_COLUMNS', 'classify', 'scope_columns', 'label',
           'is_visible_by_default', 'normalize', 'scrub_negations',
           'IMPLEMENTATION_FAMILIES', 'LEADERSHIP_FAMILIES']
