"""Concrete providers.

Only the standard library is used for transport so the app keeps working
without extra dependencies.  API keys are passed in by the caller, which reads
them from the environment - they are never stored in SQLite.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import AIError, AIProvider


def _post_json(url, headers, payload, timeout):
    body = json.dumps(payload).encode('utf-8')
    request = Request(url, data=body, method='POST',
                      headers=dict({'Content-Type': 'application/json'}, **headers))
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8', errors='replace'))
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:400]
        raise AIError('HTTP {0} from {1}: {2}'.format(exc.code, url, detail)) from exc
    except URLError as exc:
        raise AIError('Could not reach {0}: {1}'.format(url, exc.reason)) from exc
    except ValueError as exc:
        raise AIError('Invalid JSON from {0}: {1}'.format(url, exc)) from exc


class AnthropicProvider(AIProvider):
    name = 'anthropic'
    endpoint = 'https://api.anthropic.com/v1/messages'
    api_version = '2023-06-01'

    def complete(self, system_prompt, user_prompt):
        data = _post_json(self.endpoint, {
            'x-api-key': self.api_key,
            'anthropic-version': self.api_version,
        }, {
            'model': self.model,
            'max_tokens': 1600,
            'system': system_prompt,
            'messages': [{'role': 'user', 'content': user_prompt}],
        }, self.timeout)
        parts = [block.get('text', '') for block in (data.get('content') or [])
                 if block.get('type') == 'text']
        return '\n'.join(parts)


class OpenAIProvider(AIProvider):
    name = 'openai'
    endpoint = 'https://api.openai.com/v1/chat/completions'

    def complete(self, system_prompt, user_prompt):
        data = _post_json(self.endpoint, {
            'Authorization': 'Bearer {0}'.format(self.api_key),
        }, {
            'model': self.model,
            'messages': [{'role': 'system', 'content': system_prompt},
                         {'role': 'user', 'content': user_prompt}],
            'response_format': {'type': 'json_object'},
        }, self.timeout)
        choices = data.get('choices') or []
        if not choices:
            raise AIError('OpenAI returned no choices.')
        return choices[0].get('message', {}).get('content') or ''


PROVIDERS = {'anthropic': AnthropicProvider, 'openai': OpenAIProvider}
