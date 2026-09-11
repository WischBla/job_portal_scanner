"""Adapter contract + registry + shared HTTP helpers."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_AGENT = 'SwissJobScanner/4.0 (personal local job discovery)'
DEFAULT_TIMEOUT = 20

_REGISTRY = {}


class SourceError(RuntimeError):
    """Raised when a portal cannot be queried; never aborts the whole scan."""


class JobSourceAdapter:
    """Interface every portal adapter implements.

    ``fetch_jobs(profile, config)`` returns a list of raw job dicts.  It may use
    ``profile`` to narrow the upstream query (e.g. send "Switzerland" as a
    location parameter) but must not filter results itself.
    """

    type_name = ''
    label = ''
    #: What the adapter can actually do upstream - shown in the UI and README.
    supports_location_query = False
    limitations = ''

    def fetch_jobs(self, profile, config, source_name):  # pragma: no cover - interface
        raise NotImplementedError


def register(adapter_cls):
    _REGISTRY[adapter_cls.type_name] = adapter_cls
    return adapter_cls


def get_adapter(type_name):
    cls = _REGISTRY.get(str(type_name or '').strip().lower())
    if cls is None:
        raise SourceError('Unknown source type: {0}'.format(type_name))
    return cls()


def adapter_types():
    return sorted(_REGISTRY)


def http_json(url, timeout=DEFAULT_TIMEOUT):
    request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or 'utf-8'
            return json.loads(response.read().decode(charset, errors='replace'))
    except HTTPError as exc:
        raise SourceError('HTTP {0} from {1}'.format(exc.code, url)) from exc
    except URLError as exc:
        raise SourceError('Could not reach {0}: {1}'.format(url, exc.reason)) from exc
    except ValueError as exc:
        raise SourceError('Invalid JSON from {0}: {1}'.format(url, exc)) from exc


def http_bytes(url, accept='application/rss+xml, application/atom+xml, application/xml, text/xml',
               timeout=DEFAULT_TIMEOUT):
    request = Request(url, headers={'User-Agent': USER_AGENT, 'Accept': accept})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        raise SourceError('HTTP {0} from {1}'.format(exc.code, url)) from exc
    except URLError as exc:
        raise SourceError('Could not reach {0}: {1}'.format(url, exc.reason)) from exc
