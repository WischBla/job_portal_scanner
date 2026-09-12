"""Application settings - everything the Config screen owns.

Settings live in ``app_settings`` as JSON values.  Secrets never do: API keys
are read from the environment (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``) and
only their presence is ever reported to the UI.
"""

import json
import os

from .db import connect, now_iso, row_to_dict

#: key -> default value.  Unknown keys are rejected by ``save_settings``.
DEFAULT_SETTINGS = {
    # -- AI ---------------------------------------------------------------
    'ai_enabled': False,
    'ai_provider': 'none',            # none | anthropic | openai
    'ai_model': 'claude-sonnet-5',
    'ai_max_jobs_per_scan': 10,
    'ai_analyse_min_score': 70,
    'ai_timeout_seconds': 60,
    # -- salary model -----------------------------------------------------
    'salary_show_estimates': True,
    'salary_market_uplift_pct': 0,    # global nudge for the baseline table
    'salary_min_confidence': 'Low',
    # -- application automation -------------------------------------------
    'apply_enabled': True,
    'apply_headless': False,
    'apply_autofill': True,
    'apply_upload_cv': True,
    'apply_upload_motivation': False,
    'apply_cv_language': 'en',        # en | de
    'apply_never_submit': True,       # read-only reminder; submission is never implemented
    # -- system -----------------------------------------------------------
    'jobs_page_size': 60,
    'scan_hide_ignored': True,
}

#: Settings the UI may not change (they encode a safety guarantee).
LOCKED_SETTINGS = {'apply_never_submit'}

AI_PROVIDERS = ('none', 'anthropic', 'openai')


def load_settings(conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        values = dict(DEFAULT_SETTINGS)
        for row in conn.execute('SELECT key, value FROM app_settings').fetchall():
            data = row_to_dict(row)
            if data['key'] not in DEFAULT_SETTINGS:
                continue
            try:
                values[data['key']] = json.loads(data['value'])
            except (TypeError, ValueError):
                pass
        values['apply_never_submit'] = True
        return values
    finally:
        if owns:
            conn.close()


def save_settings(payload, conn=None):
    owns = conn is None
    conn = conn or connect()
    try:
        ts = now_iso()
        for key, value in (payload or {}).items():
            if key not in DEFAULT_SETTINGS or key in LOCKED_SETTINGS:
                continue
            value = _coerce(key, value)
            conn.execute('INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?) '
                         'ON CONFLICT(key) DO UPDATE SET value=excluded.value, '
                         'updated_at=excluded.updated_at',
                         (key, json.dumps(value, ensure_ascii=False), ts))
        conn.commit()
        return load_settings(conn)
    finally:
        if owns:
            conn.close()


def _coerce(key, value):
    default = DEFAULT_SETTINGS[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if key == 'ai_provider' and value not in AI_PROVIDERS:
        return 'none'
    return str(value or '')


def api_key_for(provider):
    """API keys come from the environment only - never from the database."""
    if provider == 'anthropic':
        return os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if provider == 'openai':
        return os.environ.get('OPENAI_API_KEY', '').strip()
    return ''


def ai_status(settings=None):
    """What the Config screen shows about AI, without leaking the key itself."""
    settings = settings or load_settings()
    provider = settings.get('ai_provider') or 'none'
    has_key = bool(api_key_for(provider))
    enabled = bool(settings.get('ai_enabled')) and provider != 'none' and has_key
    if not settings.get('ai_enabled'):
        reason = 'AI analysis is switched off. Deterministic matching is used.'
    elif provider == 'none':
        reason = 'No AI provider selected.'
    elif not has_key:
        reason = 'No API key found in the environment ({0}).'.format(
            'ANTHROPIC_API_KEY' if provider == 'anthropic' else 'OPENAI_API_KEY')
    else:
        reason = 'Active: {0} / {1}.'.format(provider, settings.get('ai_model'))
    return {'enabled': enabled, 'provider': provider, 'model': settings.get('ai_model'),
            'has_api_key': has_key, 'reason': reason,
            'env_var': 'ANTHROPIC_API_KEY' if provider == 'anthropic' else
                       ('OPENAI_API_KEY' if provider == 'openai' else '')}
