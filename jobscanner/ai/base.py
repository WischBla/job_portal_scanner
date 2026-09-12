"""Provider contract and the shape every provider must return."""

import json
import re

#: The analysis contract.  Providers must fill these keys; the template
#: fallback fills exactly the same ones, so the UI never branches on provider.
RESULT_KEYS = ('fit_summary', 'strongest_matches', 'gaps', 'seniority_fit',
               'application_angle', 'salary_commentary')


class AIError(RuntimeError):
    """Any provider failure. Never fatal - the caller falls back to templates."""


class AIResult(dict):
    @classmethod
    def empty(cls):
        return cls({'fit_summary': '', 'strongest_matches': [], 'gaps': [],
                    'seniority_fit': '', 'application_angle': '', 'salary_commentary': ''})

    @classmethod
    def coerce(cls, data):
        result = cls.empty()
        data = data or {}
        for key in RESULT_KEYS:
            value = data.get(key)
            if key in ('strongest_matches', 'gaps'):
                if isinstance(value, str):
                    value = [line.strip('-* ') for line in value.splitlines() if line.strip()]
                result[key] = [str(item).strip() for item in (value or []) if str(item).strip()][:6]
            else:
                result[key] = str(value or '').strip()
        return result


class AIProvider:
    """One remote model. Implementations only do transport + JSON extraction."""

    name = ''

    def __init__(self, model, api_key, timeout=60):
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, system_prompt, user_prompt):  # pragma: no cover - interface
        raise NotImplementedError

    def analyse(self, system_prompt, user_prompt):
        return AIResult.coerce(extract_json(self.complete(system_prompt, user_prompt)))


def extract_json(text):
    """Models like to wrap JSON in prose or fences; take the first object."""
    if not text:
        raise AIError('Empty response from the AI provider.')
    text = text.strip()
    fenced = re.search(r'```(?:json)?\s*(.+?)```', text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find('{')
    end = text.rfind('}')
    if start == -1 or end <= start:
        raise AIError('AI response contained no JSON object.')
    try:
        return json.loads(text[start:end + 1])
    except ValueError as exc:
        raise AIError('AI response was not valid JSON: {0}'.format(exc)) from exc
