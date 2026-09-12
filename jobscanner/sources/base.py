"""Adapter contract + registry + shared HTTP helpers."""

import concurrent.futures
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

USER_AGENT = 'SwissJobScanner/5.0 (personal local job discovery)'
#: Career sites that are served by a CDN answer a plain API user agent with a
#: challenge page.  A normal desktop browser string is what those pages expect;
#: it is *not* a bypass - every URL below is public and unauthenticated.
BROWSER_USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
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

    def verify(self, config):
        """Prove the configured endpoint really answers for this company.

        Returns ``(job_count, detail)``.  A source is only ever marked ACTIVE
        when this ran against the live endpoint and came back with postings,
        which is what stops a guessed board token from being saved.
        """
        jobs = self.fetch_jobs({}, config, config.get('company') or self.label) or []
        return len(jobs), '{0} postings returned.'.format(len(jobs))


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


def _open(url, headers, timeout, data=None):
    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or 'utf-8'
            return response.read(), charset
    except HTTPError as exc:
        raise SourceError('HTTP {0} from {1}'.format(exc.code, url)) from exc
    except URLError as exc:
        raise SourceError('Could not reach {0}: {1}'.format(url, exc.reason)) from exc


def http_json(url, timeout=DEFAULT_TIMEOUT, payload=None, user_agent=USER_AGENT):
    """GET (or POST, when ``payload`` is given) a public JSON endpoint."""
    headers = {'User-Agent': user_agent, 'Accept': 'application/json'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    body, charset = _open(url, headers, timeout, data)
    try:
        return json.loads(body.decode(charset, errors='replace'))
    except ValueError as exc:
        raise SourceError('Invalid JSON from {0}: {1}'.format(url, exc)) from exc


def http_bytes(url, accept='application/rss+xml, application/atom+xml, application/xml, text/xml',
               timeout=DEFAULT_TIMEOUT, user_agent=USER_AGENT):
    body, _ = _open(url, {'User-Agent': user_agent, 'Accept': accept}, timeout)
    return body


def http_text(url, timeout=DEFAULT_TIMEOUT, user_agent=BROWSER_USER_AGENT):
    """Fetch a public, server-rendered page as text."""
    body, charset = _open(
        url, {'User-Agent': user_agent,
              'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
              'Accept-Language': 'en-GB,en;q=0.9,de;q=0.8'}, timeout)
    return body.decode(charset, errors='replace')


def fetch_in_parallel(items, worker, max_workers=6):
    """Run ``worker`` over ``items``; individual failures are skipped, not fatal.

    Career sites are fetched one detail page at a time, so a handful of threads
    turns "40 postings" from a minute into a couple of seconds without ever
    hammering a site with more than a few concurrent requests.
    """
    items = list(items)
    if not items:
        return []
    results = []
    workers = max(1, min(max_workers, len(items)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for outcome in pool.map(_safely(worker), items):
            if outcome is not None:
                results.append(outcome)
    return results


def _safely(worker):
    def call(item):
        try:
            return worker(item)
        except Exception:  # noqa: BLE001 - one bad detail page must not lose the rest
            return None
    return call
