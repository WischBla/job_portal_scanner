"""How much do we actually know about this job?

Discovery, enrichment, scoring and visibility are four different questions.
This module answers the second one, and it exists because the scanner used to
conflate it with the third: a LinkedIn alert entry carries a title, a company,
a location and a link, and scoring that as if it were a job description made a
*Head of SRE* look like a poor fit.  It was not a poor fit; it was an unknown
one.

The rule the whole module encodes:

    **Missing evidence is not negative evidence.**

Three levels, decided from what the posting actually says rather than from how
long it is - a hundred characters of pure responsibilities says more about the
shape of a role than a thousand characters of company boilerplate:

``HIGH``    a full canonical job description: enough text *and* enough distinct
            responsibility statements to judge the shape of the role.
``MEDIUM``  partial but meaningful responsibilities.  Real evidence, so it is
            allowed to move the score - but not to the extremes.
``LOW``     title / company / location only, or so thin that nothing about the
            mandate can be read out of it.  Nothing negative is concluded.

Each level maps onto one enrichment state, which is what the Jobs screen shows
and what the ``Needs Enrichment`` filter selects on:

    HIGH -> ENRICHED,  MEDIUM -> PARTIAL,  LOW -> NEEDS_ENRICHMENT

Finally, a LOW-evidence posting whose *title* names a leadership or ownership
scope is marked ``HIGH_POTENTIAL_NEEDS_ENRICHMENT``.  That is not a score
bonus and it is deliberately not one: it only says "find the description
before judging this one".
"""

import re

from .locations import fold

# -- levels ----------------------------------------------------------------
HIGH = 'HIGH'
MEDIUM = 'MEDIUM'
LOW = 'LOW'
LEVELS = (HIGH, MEDIUM, LOW)

# -- enrichment states -----------------------------------------------------
ENRICHED = 'ENRICHED'
PARTIAL = 'PARTIAL'
NEEDS_ENRICHMENT = 'NEEDS_ENRICHMENT'
STATES = (ENRICHED, PARTIAL, NEEDS_ENRICHMENT)

STATE_FOR_LEVEL = {HIGH: ENRICHED, MEDIUM: PARTIAL, LOW: NEEDS_ENRICHMENT}

#: What a LOW-evidence posting with a leadership title is called.  A priority
#: for enrichment, never a score.
HIGH_POTENTIAL = 'HIGH_POTENTIAL_NEEDS_ENRICHMENT'

# -- thresholds ------------------------------------------------------------
#: Below this much text no posting has described a mandate, whatever words it
#: managed to use.
MIN_TEXT = 80
#: A full posting: this much text *and* this many distinct responsibility cues.
FULL_TEXT = 600
FULL_CUES = 6
#: The least evidence that still says something about the mandate.  Two
#: distinct statements, because one verb is a sentence, not a job description.
MIN_CUES = 2

#: Stems of the verbs and phrases that introduce a responsibility or a
#: requirement.  They are matched as prefixes, so one entry covers "manage",
#: "manages", "managing" and "management" - a posting says the same thing in
#: all four and counting them separately would reward grammar rather than
#: content.
#:
#: Being generic is the point: these measure whether the posting *describes
#: the job* at all, not what the job is.  The shape of the role is decided in
#: :mod:`jobscanner.fit`, from far more specific evidence.
RESPONSIBILITY_CUES = [
    'you will', 'you would', 'your role', 'in this role', 'the role',
    'your task', 'your mission', 'we are looking for', 'what you bring',
    'your profile', 'about the role', 'reporting to', 'report to',
    'responsib', 'accountab', 'requirement', 'qualificat', 'experienc',
    'lead', 'own', 'manag', 'driv', 'defin', 'build', 'built', 'implement',
    'develop', 'deliver', 'design', 'operat', 'run', 'ensur', 'establish',
    'improv', 'maintain', 'support', 'collaborat', 'partner', 'mentor',
    'coach', 'ship', 'work', 'contribut', 'scale', 'creat', 'overse',
    'coordinat', 'plan', 'review', 'monitor', 'optimis', 'optimiz',
]

#: Title shapes that make a posting worth enriching before it is judged.
#: Deliberately generic role vocabulary - no company is ever named here, and a
#: title on this list buys a job exactly one thing: priority for enrichment.
HIGH_POTENTIAL_TITLE_SHAPES = [
    'head of', 'director', 'vice president', 'vp of', 'chief',
    'engineering manager', 'senior engineering manager', 'group manager',
    'principal technical program manager', 'principal program manager',
    'technical program manager', 'programme manager', 'head', 'leiter',
    'bereichsleiter', 'global lead', 'global head', 'senior manager',
]
#: ... but only when the title also names a domain this profile works in, so
#: "Head of Sales" is not a high-potential technology job.
HIGH_POTENTIAL_DOMAINS = [
    'engineering', 'technology', 'technical', 'platform', 'infrastructure',
    'cloud', 'reliability', 'sre', 'site reliability', 'operations', 'devops',
    'observability', 'architecture', 'data', 'software', 'it', 'digital',
    'security', 'program', 'programme', 'transformation', 'automation',
    'production', 'systems', 'delivery',
]


def normalize(value):
    """Fold, then flatten punctuation, so 'on-call' == 'on call'."""
    text = fold(value)
    text = re.sub(r'[^a-z0-9]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _present(text, phrases):
    """Distinct phrases from ``phrases`` present in ``text`` as whole words."""
    found = set()
    for phrase in phrases:
        needle = normalize(phrase)
        if not needle or needle in found:
            continue
        if re.search(r'(?<![a-z0-9])' + re.escape(needle) + r'(?![a-z0-9])', text):
            found.add(needle)
    return found


def _cues(text):
    """Distinct responsibility cues, matched as word-initial stems."""
    found = set()
    for stem in RESPONSIBILITY_CUES:
        needle = normalize(stem)
        if not needle or needle in found:
            continue
        if re.search(r'(?<![a-z0-9])' + re.escape(needle) + r'[a-z]*(?![a-z0-9])', text):
            found.add(needle)
    return found


def description_of(job):
    """The text that is allowed to count as evidence about the role.

    ``excerpt`` is deliberately *not* a fallback.  An imported alert entry
    stores its informational line ("Dieses Unternehmen ist aktiv auf
    Personalsuche") there, and treating that as a job description is precisely
    the mistake this module exists to prevent.
    """
    return str((job or {}).get('description') or '').strip()


def title_is_high_potential(title):
    """Does the title name a leadership scope in a domain this profile wants?

    A clue, never a verdict: it decides what to enrich first and nothing else.
    Responsibilities remain authoritative everywhere they exist.
    """
    text = normalize(title)
    if not text:
        return False
    return bool(_present(text, HIGH_POTENTIAL_TITLE_SHAPES)
                and _present(text, HIGH_POTENTIAL_DOMAINS))


def assess(job):
    """What is known about one job.

    Returns the level, the enrichment state it implies, the confidence the
    card shows, whether the Personal Fit Score is provisional, and the two
    measurements the verdict was made from - so "Evidence: LOW" can always be
    explained rather than asserted.
    """
    description = description_of(job)
    text = normalize(description)
    length = len(text)
    cues = len(_cues(text))
    high_potential = title_is_high_potential(job.get('title'))

    if length < MIN_TEXT or cues < MIN_CUES:
        level = LOW
    elif length >= FULL_TEXT and cues >= FULL_CUES:
        level = HIGH
    else:
        level = MEDIUM

    return {
        'level': level,
        'state': STATE_FOR_LEVEL[level],
        'confidence': level,
        'provisional': level == LOW,
        'high_potential': bool(high_potential and level == LOW),
        'classification': HIGH_POTENTIAL if (high_potential and level == LOW) else '',
        'characters': length,
        'responsibility_signals': cues,
        'detail': _detail(level, length, cues, high_potential),
    }


def _detail(level, length, cues, high_potential):
    if level == LOW and high_potential:
        return ('Leadership scope in the title but no description yet - enrich this '
                'before judging it.')
    if level == LOW and not length:
        return 'No job description stored: title, company and location only.'
    if level == LOW:
        return ('Only {0} characters and {1} responsibility statement(s) - too thin to '
                'judge the shape of the role.'.format(length, cues))
    if level == MEDIUM:
        return ('Partial description: {0} responsibility statement(s), enough to rank '
                'but not to be certain.'.format(cues))
    return 'Full job description: {0} responsibility statement(s).'.format(cues)


def is_provisional(level):
    return level == LOW


__all__ = ['HIGH', 'MEDIUM', 'LOW', 'LEVELS', 'ENRICHED', 'PARTIAL', 'NEEDS_ENRICHMENT',
           'STATES', 'STATE_FOR_LEVEL', 'HIGH_POTENTIAL', 'assess', 'description_of',
           'title_is_high_potential', 'normalize', 'is_provisional']
