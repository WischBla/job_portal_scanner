"""AI analysis service: prompt building, caching and the template fallback.

``analyse_job`` always returns a usable result.  With no provider configured it
returns a deterministic, template-generated analysis derived from the match
breakdown - the UI looks the same, it is simply less specific.
"""

import hashlib
import json

from ..db import connect, row_to_dict, utc_now_iso
from ..settings import api_key_for, load_settings
from .base import AIError, AIResult
from .providers import PROVIDERS

SYSTEM_PROMPT = (
    'You advise one senior technology leader on whether a specific job is worth '
    'applying for. Be concrete, sober and short. Never invent facts that are not in '
    'the candidate profile or the job description. Answer with a single JSON object '
    'and nothing else, using exactly these keys: fit_summary (string, max 60 words), '
    'strongest_matches (array of at most 5 short strings), gaps (array of at most 4 '
    'short strings naming real gaps, empty if there are none), seniority_fit (string, '
    'one or two sentences), application_angle (string, how to position the application, '
    'max 60 words), salary_commentary (string, what compensation is realistic and why, '
    'no invented precision, max 50 words).'
)


def get_provider(settings=None):
    """The configured provider, or None when AI is off or unusable."""
    settings = settings or load_settings()
    if not settings.get('ai_enabled'):
        return None
    name = settings.get('ai_provider') or 'none'
    cls = PROVIDERS.get(name)
    if cls is None:
        return None
    key = api_key_for(name)
    if not key:
        return None
    return cls(settings.get('ai_model') or '', key,
               timeout=int(settings.get('ai_timeout_seconds') or 60))


def _fingerprint(job, person, provider_name, model):
    blob = '|'.join(str(x) for x in (
        job.get('id'), job.get('title'), job.get('company'),
        len(job.get('description') or ''), provider_name, model,
        (person or {}).get('updated_at')))
    return hashlib.sha1(blob.encode('utf-8')).hexdigest()[:20]


def build_prompt(job, person, match=None):
    person = person or {}
    skills = ', '.join(person.get('technical_skills') or [])[:1200]
    history = '\n'.join(
        '- {0} at {1} ({2}-{3}): {4}'.format(
            item.get('title', ''), item.get('company', ''), item.get('start', ''),
            item.get('end', ''), (item.get('highlights') or '')[:300])
        for item in (person.get('career_history') or [])[:8])
    languages = ', '.join('{0} ({1})'.format(item.get('language', ''), item.get('level', ''))
                          for item in (person.get('languages') or []))
    return (
        'CANDIDATE\n'
        'Name: {name}\n'
        'Headline: {headline}\n'
        'Summary: {summary}\n'
        'Leadership profile: {leadership}\n'
        'Technical skills: {skills}\n'
        'Languages: {languages}\n'
        'Work authorisation: {authorisation}\n'
        'Notice period: {notice}\n'
        'Compensation expectation: minimum CHF {cmin}, target CHF {ctarget}, stretch CHF {cstretch}\n'
        'Target roles: {targets}\n'
        'Career history:\n{history}\n\n'
        'JOB\n'
        'Title: {title}\n'
        'Company: {company}\n'
        'Location: {location} ({work_model})\n'
        'Deterministic match score: {score}/100 ({label})\n'
        'Full description:\n{description}\n'
    ).format(
        name=person.get('full_name') or '',
        headline=person.get('headline') or '',
        summary=(person.get('summary') or '')[:800],
        leadership=(person.get('leadership_profile') or '')[:800],
        skills=skills, languages=languages,
        authorisation=(person.get('work_authorization') or '')[:300],
        notice=person.get('notice_period') or '',
        cmin=int(person.get('comp_minimum_chf') or 0),
        ctarget=int(person.get('comp_target_chf') or 0),
        cstretch=int(person.get('comp_stretch_chf') or 0),
        targets=', '.join(person.get('target_roles') or [])[:600],
        history=history or '- not captured yet',
        title=job.get('title') or '', company=job.get('company') or '',
        location=job.get('normalized_city') or job.get('raw_location') or 'Switzerland',
        work_model=job.get('work_model') or 'Unknown',
        score=job.get('match_score') or (match or {}).get('score') or 0,
        label=job.get('match_label') or '',
        description=(job.get('description') or job.get('excerpt') or '')[:14000],
    )


def template_analysis(job, person=None):
    """Deterministic stand-in used whenever AI is off or unavailable."""
    person = person or {}
    reasons = _json_list(job.get('match_reasons'))
    concerns = _json_list(job.get('match_concerns'))
    score = int(job.get('match_score') or 0)
    seniority = job.get('seniority') or 'Other'
    wanted = {str(x).casefold() for x in (person.get('target_roles') or [])}
    title = (job.get('title') or '').casefold()
    seniority_fit = (
        '{0} scope, which matches the target level.'.format(seniority)
        if seniority in ('Head of', 'Director', 'Senior Director', 'Principal',
                         'Global Lead', 'Senior Lead', 'VP')
        else 'Detected scope is "{0}" - confirm the actual mandate and reporting line.'.format(seniority))
    if any(role in title for role in wanted):
        seniority_fit += ' The title is one you explicitly target.'
    summary = ('Deterministic match {0}/100 ({1}). {2}'.format(
        score, job.get('match_label') or '',
        reasons[0] if reasons else 'Scored on role, domain, leadership and location signals.'))
    angle = ('Lead with technology and engineering operations at scale, platform and '
             'reliability ownership, and evidence of running transformation programmes '
             'through matrix organisations. Reference the concrete overlaps listed above.')
    return AIResult.coerce({
        'fit_summary': summary,
        'strongest_matches': reasons[:5],
        'gaps': concerns[:4],
        'seniority_fit': seniority_fit,
        'application_angle': angle,
        'salary_commentary': '',
    })


def cached_analysis(job_id, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        row = conn.execute('SELECT * FROM ai_analysis WHERE job_id=?', (job_id,)).fetchone()
        if row is None:
            return None
        data = row_to_dict(row)
        try:
            payload = json.loads(data.get('payload_json') or '{}')
        except (TypeError, ValueError):
            payload = {}
        payload = dict(AIResult.coerce(payload))
        payload['_provider'] = data.get('provider') or ''
        payload['_model'] = data.get('model') or ''
        payload['_created_at'] = data.get('created_at') or ''
        payload['_fingerprint'] = data.get('fingerprint') or ''
        return payload
    finally:
        if owns:
            conn.close()


def clear_analysis(job_id=None, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        if job_id:
            conn.execute('DELETE FROM ai_analysis WHERE job_id=?', (job_id,))
        else:
            conn.execute('DELETE FROM ai_analysis')
        conn.commit()
    finally:
        if owns:
            conn.close()


def analyse_job(job, person, settings=None, conn=None, force=False, provider=None):
    """Return an analysis for one job, using the cache whenever possible.

    Result shape is identical with and without AI; ``_source`` says which path
    produced it ('cache', provider name, or 'template').
    """
    settings = settings or load_settings()
    owns = conn is None
    conn = conn or connect()
    try:
        provider = provider if provider is not None else get_provider(settings)
        provider_name = getattr(provider, 'name', '') or 'template'
        model = getattr(provider, 'model', '') or ''
        mark = _fingerprint(job, person, provider_name, model)
        job_id = job.get('id')

        if job_id and not force:
            cached = cached_analysis(job_id, conn)
            if cached and cached.get('_fingerprint') == mark:
                cached['_source'] = 'cache'
                return cached

        if provider is None:
            result = dict(template_analysis(job, person))
            result['_source'] = 'template'
            result['_provider'] = ''
            return result

        try:
            result = dict(provider.analyse(SYSTEM_PROMPT, build_prompt(job, person)))
            result['_source'] = provider_name
        except AIError as exc:
            result = dict(template_analysis(job, person))
            result['_source'] = 'template'
            result['_error'] = str(exc)[:300]
            return result

        result['_provider'] = provider_name
        result['_model'] = model
        if job_id:
            conn.execute(
                '''INSERT INTO ai_analysis (job_id, provider, model, fingerprint, payload_json, created_at)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(job_id) DO UPDATE SET provider=excluded.provider,
                     model=excluded.model, fingerprint=excluded.fingerprint,
                     payload_json=excluded.payload_json, created_at=excluded.created_at''',
                (job_id, provider_name, model, mark,
                 json.dumps({k: v for k, v in result.items() if not k.startswith('_')},
                            ensure_ascii=False),
                 utc_now_iso()))
            conn.commit()
        return result
    finally:
        if owns:
            conn.close()


def _json_list(value):
    if isinstance(value, list):
        return [str(x) for x in value]
    try:
        data = json.loads(value or '[]')
        return [str(x) for x in data] if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []
