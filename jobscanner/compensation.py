"""Compensation estimator.

Priority, highest first:

    1. an explicit salary in the posting
    2. locally stored market data for this company + role family
    3. a role-family + seniority + location baseline
    4. optional AI commentary (added on top, never replacing 1-3)

The result is always a *range* and always carries a confidence.  Nothing here
invents precision: when only the baseline applies the range is wide and the
confidence is Low.  A missing salary never rejects or downgrades a job - it is
information, not a verdict.
"""

import hashlib
import re

from .db import connect, row_to_dict, utc_now_iso
from .locations import fold

CURRENCY = 'CHF'

#: Role families used by the benchmark table, in priority order.  The first
#: family whose keywords appear in the title wins.
ROLE_FAMILIES = [
    ('engineering_leadership', [
        'head of engineering', 'engineering director', 'director of engineering',
        'engineering leadership', 'head of technology', 'cto', 'vp engineering',
        'engineering manager', 'head of software', 'director engineering',
    ]),
    ('platform_reliability', [
        'platform engineering', 'site reliability', 'sre', 'reliability',
        'infrastructure', 'cloud', 'devops', 'devsecops', 'observability', 'kubernetes',
    ]),
    ('technology_operations', [
        'technology operations', 'technical operations', 'engineering operations',
        'it operations', 'service operations', 'operations lead', 'head of operations',
        'operational excellence', 'aiops', 'ai operations',
    ]),
    ('program_management', [
        'program manager', 'programme manager', 'program management', 'tpm',
        'project manager', 'project management', 'program lead', 'delivery lead',
    ]),
    ('transformation', [
        'transformation', 'change management', 'modernisation', 'modernization',
        'technology strategy', 'operating model',
    ]),
    ('developer_experience', [
        'developer experience', 'developer productivity', 'engineering productivity',
        'engineering enablement', 'platform enablement', 'devex',
    ]),
]
DEFAULT_FAMILY = 'technology_operations'

#: Baseline Swiss total-compensation bands by role family and seniority tier.
#: base_min / base_max are annual CHF base; bonus is a percentage of base.
BASELINE = {
    'engineering_leadership': {
        'executive': (230000, 300000, 20, 40),
        'senior_leadership': (195000, 250000, 15, 30),
        'leadership': (170000, 215000, 10, 25),
        'senior_ic': (150000, 190000, 5, 15),
    },
    'platform_reliability': {
        'executive': (210000, 275000, 15, 35),
        'senior_leadership': (185000, 235000, 12, 25),
        'leadership': (165000, 205000, 10, 20),
        'senior_ic': (140000, 180000, 5, 15),
    },
    'technology_operations': {
        'executive': (215000, 280000, 18, 35),
        'senior_leadership': (185000, 240000, 15, 28),
        'leadership': (160000, 205000, 10, 22),
        'senior_ic': (135000, 175000, 5, 15),
    },
    'program_management': {
        'executive': (205000, 265000, 15, 30),
        'senior_leadership': (180000, 230000, 12, 25),
        'leadership': (155000, 195000, 10, 20),
        'senior_ic': (135000, 175000, 5, 15),
    },
    'transformation': {
        'executive': (220000, 290000, 20, 40),
        'senior_leadership': (190000, 245000, 15, 30),
        'leadership': (165000, 210000, 10, 22),
        'senior_ic': (140000, 180000, 5, 15),
    },
    'developer_experience': {
        'executive': (205000, 265000, 15, 30),
        'senior_leadership': (180000, 230000, 12, 25),
        'leadership': (158000, 200000, 10, 20),
        'senior_ic': (135000, 175000, 5, 15),
    },
}

SENIORITY_TIER = {
    'VP': 'executive', 'Senior Director': 'executive', 'Director': 'executive',
    'Head of': 'senior_leadership', 'Principal': 'senior_leadership',
    'Global Lead': 'senior_leadership', 'Senior Lead': 'senior_leadership',
    'Senior Manager': 'leadership', 'Lead': 'leadership', 'Manager': 'leadership',
    'Other': 'senior_ic',
}

#: Cost-of-living / market multipliers by Swiss location.
LOCATION_FACTOR = {
    'zurich': 1.05, 'zug': 1.05, 'basel': 1.02, 'bern': 0.97, 'luzern': 0.97,
    'lucerne': 0.97, 'geneva': 1.02, 'lausanne': 0.97, 'st. gallen': 0.94,
    'st gallen': 0.94, 'schwyz': 1.0, 'aarau': 0.97, 'aargau': 0.97,
    'lugano': 0.90, 'winterthur': 0.99,
}

#: Companies whose Swiss packages sit clearly above the local market.
BIG_TECH = {'google', 'microsoft', 'amazon', 'amazon web services / aws', 'aws', 'meta',
            'apple', 'nvidia', 'netflix', 'openai', 'anthropic', 'databricks', 'stripe',
            'snowflake', 'datadog', 'oracle', 'salesforce'}

_MONEY_RE = re.compile(
    r"(?:chf|sfr|fr\.)\s*([0-9][0-9'’., ]{2,12})(?:\s*(k|k\.?|'000|000))?"
    r"(?:\s*(?:-|–|to|bis)\s*(?:chf|sfr)?\s*([0-9][0-9'’., ]{2,12})(?:\s*(k|k\.?))?)?",
    re.IGNORECASE)

CONFIDENCE_ORDER = {'Low': 0, 'Medium': 1, 'High': 2}


def role_family(title, description=''):
    """The most specific family whose vocabulary appears in the title.

    The longest matching phrase wins, so "Head of Technology Operations" is
    technology operations rather than generic engineering leadership.
    """
    title_text = fold(title or '')
    body_text = fold((description or '')[:1500])
    for text in (title_text, body_text):
        best, best_length = '', 0
        for family, keywords in ROLE_FAMILIES:
            for keyword in keywords:
                needle = fold(keyword)
                if needle in text and len(needle) > best_length:
                    best, best_length = family, len(needle)
        if best:
            return best
    return DEFAULT_FAMILY


def _tier(seniority):
    return SENIORITY_TIER.get(seniority or 'Other', 'senior_ic')


def _location_factor(job):
    city = fold(job.get('normalized_city') or '')
    return LOCATION_FACTOR.get(city, 1.0)


def _annualize(value, period):
    """Turn an hourly / daily / monthly figure into an annual one."""
    period = fold(period or '')
    if not value:
        return None
    if 'hour' in period or 'stunde' in period:
        return value * 1900
    if 'day' in period or 'tag' in period:
        return value * 220
    if 'month' in period or 'monat' in period:
        return value * 13
    if 'week' in period or 'woche' in period:
        return value * 46
    return value


def _parse_number(raw, suffix):
    text = str(raw or '').strip().replace("'", '').replace('’', '').replace(' ', '')
    text = text.rstrip('.,')
    if ',' in text and '.' in text:
        text = text.replace(',', '')
    elif text.count(',') == 1 and len(text.split(',')[-1]) in (1, 2):
        text = text.replace(',', '.')
    else:
        text = text.replace(',', '')
    try:
        value = float(text)
    except ValueError:
        return None
    if suffix and 'k' in suffix.lower():
        value *= 1000
    if value < 1000:          # "CHF 180" almost always means 180k in a posting
        value *= 1000
    return value


def salary_from_text(text):
    """Find a plausible annual CHF range in free text, or None."""
    best = None
    for match in _MONEY_RE.finditer(text or ''):
        low = _parse_number(match.group(1), match.group(2))
        high = _parse_number(match.group(3), match.group(4)) if match.group(3) else None
        if low is None:
            continue
        if high is not None and high < low:
            low, high = high, low
        if low < 60000:       # hourly rates, signing bonuses, prices - not a salary
            continue
        if low > 900000:
            continue
        candidate = (int(low), int(high or low))
        if best is None or candidate[1] > best[1]:
            best = candidate
    return best


def _band(value_min, value_max, factor=1.0, uplift_pct=0):
    scale = factor * (1 + (uplift_pct or 0) / 100.0)
    return int(round(value_min * scale / 1000.0) * 1000), int(round(value_max * scale / 1000.0) * 1000)


def _benchmark_row(conn, company, family, seniority):
    if not conn or not company:
        return None
    rows = [row_to_dict(r) for r in conn.execute(
        'SELECT * FROM salary_benchmarks WHERE LOWER(company)=LOWER(?)', (company,)).fetchall()]
    if not rows:
        return None
    exact = [r for r in rows if r['role_family'] == family and r['seniority'] == seniority]
    same_family = [r for r in rows if r['role_family'] == family]
    return (exact or same_family or rows)[0]


def estimate(job, settings=None, conn=None, ai_payload=None):
    """Return the compensation estimate for one normalized or stored job."""
    settings = settings or {}
    uplift = int(settings.get('salary_market_uplift_pct') or 0)
    title = job.get('title') or ''
    company = job.get('company') or ''
    description = job.get('description') or job.get('excerpt') or ''
    family = role_family(title, description)
    seniority = job.get('seniority') or 'Other'
    tier = _tier(seniority)
    factor = _location_factor(job)
    sources = []

    base_min = base_max = bonus_min = bonus_max = None
    equity = ''
    confidence = 'Low'
    basis = 'baseline'

    # -- 1. explicit salary ------------------------------------------------
    published = None
    currency = (job.get('salary_currency') or '').upper()
    low, high = job.get('salary_min'), job.get('salary_max')
    if low is not None or high is not None:
        low = _annualize(low, job.get('salary_period'))
        high = _annualize(high, job.get('salary_period'))
        if currency in ('', CURRENCY):
            published = (int(low or high), int(high or low))
        else:
            sources.append('Posting publishes {0} {1:,.0f}-{2:,.0f}, not directly comparable to CHF.'
                           .format(currency, low or high or 0, high or low or 0).replace(',', "'"))
    if published is None:
        parsed = salary_from_text('{0}\n{1}'.format(title, description[:8000]))
        if parsed:
            published = parsed
            sources.append('Range read from the job description.')
    if published:
        base_min, base_max = published
        if base_max == base_min:
            base_max = int(base_min * 1.12)
        basis = 'published'
        confidence = 'High'
        sources.insert(0, 'Salary published in the posting.')

    # -- 2. company + role benchmark --------------------------------------
    benchmark = _benchmark_row(conn, company, family, seniority) if basis != 'published' else None
    if benchmark:
        base_min, base_max = _band(benchmark['base_min'], benchmark['base_max'], factor, uplift)
        bonus_min = int(base_min * benchmark['bonus_pct_min'] / 100.0)
        bonus_max = int(base_max * benchmark['bonus_pct_max'] / 100.0)
        equity = benchmark['equity'] or ''
        basis = 'benchmark'
        confidence = benchmark['confidence'] or 'Medium'
        sources.append('Local market data for {0} ({1}).'.format(company, seniority))

    # -- 3. baseline -------------------------------------------------------
    if base_min is None:
        table = BASELINE.get(family) or BASELINE[DEFAULT_FAMILY]
        bmin, bmax, pmin, pmax = table[tier]
        if fold(company) in BIG_TECH:
            bmin, bmax = int(bmin * 1.2), int(bmax * 1.3)
            equity = 'Likely a significant equity component'
            sources.append('Large technology employer - packages are usually equity-heavy.')
        base_min, base_max = _band(bmin, bmax, factor, uplift)
        bonus_min = int(base_min * pmin / 100.0)
        bonus_max = int(base_max * pmax / 100.0)
        basis = 'baseline'
        confidence = 'Low'
        sources.append('Swiss baseline for {0} / {1}.'.format(
            family.replace('_', ' '), seniority))

    if basis == 'published':
        # A published base still needs a plausible variable component.
        bonus_min = bonus_min if bonus_min is not None else 0
        bonus_max = bonus_max if bonus_max is not None else int(base_max * 0.15)
        if fold(company) in BIG_TECH and not equity:
            equity = 'Likely a significant equity component'
    if not equity:
        equity = 'Possible, not stated' if tier in ('executive', 'senior_leadership') else 'Unlikely'

    total_min = int(base_min + (bonus_min or 0))
    total_max = int(base_max + (bonus_max or 0))

    # -- 4. AI commentary (never overrides the numbers) --------------------
    commentary = ''
    if ai_payload and ai_payload.get('salary_commentary'):
        commentary = str(ai_payload['salary_commentary'])[:800]
        sources.append('AI commentary attached.')
        if confidence == 'Low':
            confidence = 'Medium'

    if fold(job.get('work_model') or '') == 'remote' and basis != 'published':
        sources.append('Remote roles are sometimes benchmarked below the Zurich market.')

    return {
        'currency': CURRENCY,
        'basis': basis,
        'total_min': total_min, 'total_max': total_max,
        'base_min': int(base_min), 'base_max': int(base_max),
        'bonus_min': int(bonus_min or 0), 'bonus_max': int(bonus_max or 0),
        'equity': equity,
        'confidence': confidence,
        'role_family': family,
        'commentary': ' '.join(sources + ([commentary] if commentary else [])).strip(),
        'display': format_estimate(total_min, total_max),
    }


def format_estimate(total_min, total_max):
    return 'CHF {0}k-{1}k'.format(int(round(total_min / 1000.0)), int(round(total_max / 1000.0)))


def fingerprint(job, settings):
    blob = '|'.join(str(x) for x in (
        job.get('title'), job.get('company'), job.get('seniority'),
        job.get('normalized_city'), job.get('salary_min'), job.get('salary_max'),
        job.get('salary_currency'), (settings or {}).get('salary_market_uplift_pct')))
    return hashlib.sha1(blob.encode('utf-8')).hexdigest()[:20]


def get_or_create(job, settings=None, conn=None, ai_payload=None):
    """Cached estimate for a stored job; recomputed only when inputs change."""
    owns = conn is None
    conn = conn or connect()
    try:
        job_id = job.get('id')
        mark = fingerprint(job, settings)
        if job_id:
            row = conn.execute('SELECT * FROM compensation_estimates WHERE job_id=?',
                               (job_id,)).fetchone()
            if row is not None:
                data = row_to_dict(row)
                if data.get('fingerprint') == mark and not ai_payload:
                    data['display'] = format_estimate(data['total_min'], data['total_max'])
                    return data
        result = estimate(job, settings=settings, conn=conn, ai_payload=ai_payload)
        result['fingerprint'] = mark
        if job_id:
            conn.execute(
                '''INSERT INTO compensation_estimates
                     (job_id, basis, currency, total_min, total_max, base_min, base_max,
                      bonus_min, bonus_max, equity, confidence, commentary, fingerprint, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(job_id) DO UPDATE SET
                     basis=excluded.basis, total_min=excluded.total_min, total_max=excluded.total_max,
                     base_min=excluded.base_min, base_max=excluded.base_max,
                     bonus_min=excluded.bonus_min, bonus_max=excluded.bonus_max,
                     equity=excluded.equity, confidence=excluded.confidence,
                     commentary=excluded.commentary, fingerprint=excluded.fingerprint''',
                (job_id, result['basis'], CURRENCY, result['total_min'], result['total_max'],
                 result['base_min'], result['base_max'], result['bonus_min'], result['bonus_max'],
                 result['equity'], result['confidence'], result['commentary'], mark, utc_now_iso()))
            conn.commit()
        return result
    finally:
        if owns:
            conn.close()
