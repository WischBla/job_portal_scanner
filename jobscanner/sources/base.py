"""Adapter contract + registry + shared HTTP helpers.

Every request any adapter makes goes through :func:`_open`, which is why the
rate-limit and failure handling lives here rather than being re-implemented
thirteen times.  Three things happen on the way out and back:

**A request is paced before it is sent.**  Each provider has its own
concurrency ceiling and minimum interval (:data:`PROVIDER_LIMITS`), so a board
that documents a modest rate - SmartRecruiters and Lever both do - gets a
steady trickle rather than a burst of a hundred detail lookups.

**A refusal is retried properly, or not at all.**  429, 500, 502, 503, 504 and
timeouts are transient and are retried a bounded number of times with
exponential backoff and full jitter; a 404 or a 401 is not transient and is
not retried.  ``Retry-After`` is honoured when the server sends one, because a
server that says "wait nine seconds" has told us more than any backoff formula
knows.  The jitter matters as much as the backoff: without it every source
that got a 429 in the same scan would come back at the same instant.

**What happened is recorded, not just whether it worked.**  A source that was
rate limited, a source that timed out, a source that answered 500, a source
whose payload did not parse and a source that honestly returned nothing look
identical in a boolean.  They are told apart here, through
:data:`OUTCOMES`, and the distinction is load-bearing: only an authoritative
success may ever retire a job.  An absent answer is not evidence that a job is
gone.

Nothing here bypasses anything.  Every endpoint the adapters use is public and
unauthenticated; the browser user agent exists because some CDNs answer an API
user agent with a challenge page, and no token or credential is ever sent, so
there is none to leak into the diagnostics either.
"""

import concurrent.futures
import contextvars
import json
import random
import socket
import threading
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

USER_AGENT = 'SwissJobScanner/5.0 (personal local job discovery)'
#: Career sites that are served by a CDN answer a plain API user agent with a
#: challenge page.  A normal desktop browser string is what those pages expect;
#: it is *not* a bypass - every URL below is public and unauthenticated.
BROWSER_USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
DEFAULT_TIMEOUT = 20

_REGISTRY = {}


# --------------------------------------------------------------------------
# What a request, and a whole source, actually did
# --------------------------------------------------------------------------
SUCCESS = 'SUCCESS'
RATE_LIMITED = 'RATE_LIMITED'
TIMEOUT = 'TIMEOUT'
HTTP_ERROR = 'HTTP_ERROR'
PARSE_ERROR = 'PARSE_ERROR'
NETWORK_ERROR = 'NETWORK_ERROR'
EMPTY_BUT_SUSPICIOUS = 'EMPTY_BUT_SUSPICIOUS'
OUTCOMES = (SUCCESS, RATE_LIMITED, TIMEOUT, HTTP_ERROR, PARSE_ERROR, NETWORK_ERROR,
            EMPTY_BUT_SUSPICIOUS)

#: The only outcome that counts as a complete, authoritative reconciliation of
#: what a source is currently advertising.  Everything else leaves the stored
#: jobs of that source exactly as they are - see
#: :meth:`jobscanner.repository.JobRepository.expire_missing`.
AUTHORITATIVE_OUTCOMES = (SUCCESS,)

OUTCOME_LABELS = {
    SUCCESS: 'Answered normally',
    RATE_LIMITED: 'Rate limited by the provider',
    TIMEOUT: 'Timed out',
    HTTP_ERROR: 'HTTP error',
    PARSE_ERROR: 'Response could not be parsed',
    NETWORK_ERROR: 'Could not be reached',
    EMPTY_BUT_SUSPICIOUS: 'Answered, but the empty result is not trustworthy',
}


# --------------------------------------------------------------------------
# Retry policy
# --------------------------------------------------------------------------
#: Bounded on purpose.  Three attempts is enough to ride out a burst limit and
#: few enough that a genuinely broken source fails the scan quickly instead of
#: holding it open for minutes.
MAX_ATTEMPTS = 3
#: Exponential: 1s, 2s, 4s ... before jitter.
BASE_BACKOFF = 1.0
MAX_BACKOFF = 20.0
#: However long a ``Retry-After`` asks for, a local scan will not sit still
#: longer than this.
MAX_RETRY_AFTER = 60.0
#: Status codes worth trying again.  404 and 401 are answers, not accidents.
RETRY_STATUSES = (429, 500, 502, 503, 504, 408)
RATE_LIMIT_STATUSES = (429,)
#: Response headers worth remembering.  Deliberately a whitelist: nothing that
#: could carry a credential is ever copied into the diagnostics.
RATE_LIMIT_HEADERS = ('Retry-After', 'X-RateLimit-Limit', 'X-RateLimit-Remaining',
                      'X-RateLimit-Reset', 'RateLimit-Limit', 'RateLimit-Remaining',
                      'RateLimit-Reset')


def backoff_delay(attempt, retry_after=None, jitter=None):
    """How long to wait before attempt number ``attempt`` (1-based).

    ``Retry-After`` wins when the server sent one - it is the only party that
    knows its own window - capped so a misconfigured header cannot stall a
    local scan.  Otherwise: exponential, bounded, with *full* jitter, which is
    what stops every source that was throttled in the same scan from coming
    back in the same millisecond.

    Pure, and ``jitter`` is injectable, so the policy can be asserted on
    without waiting for real seconds to pass.
    """
    roll = random.random if jitter is None else jitter
    if retry_after is not None:
        wait = max(0.0, min(float(retry_after), MAX_RETRY_AFTER))
        return round(wait + roll(), 3)          # stagger, never earlier than asked
    ceiling = min(MAX_BACKOFF, BASE_BACKOFF * (2 ** max(0, attempt - 1)))
    return round(ceiling * roll(), 3)


def parse_retry_after(value):
    """``Retry-After`` as seconds, from either of its two legal spellings."""
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return max(0.0, float(int(text)))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


# --------------------------------------------------------------------------
# Per-provider pacing
# --------------------------------------------------------------------------
class Limit(object):
    """A provider's ceiling: how many at once, and how close together."""

    def __init__(self, concurrency, min_interval, detail=''):
        self.concurrency = max(1, int(concurrency))
        self.min_interval = max(0.0, float(min_interval))
        self.detail = detail


#: Per-host ceilings.  The two that matter most are the ones whose documented
#: limits a detail-page pass can actually reach: SmartRecruiters fetches one
#: request per posting and Lever asks for a modest sustained rate, so both get
#: a low concurrency and a real minimum interval instead of a burst.
PROVIDER_LIMITS = {
    'api.smartrecruiters.com': Limit(2, 0.35, 'documented public Posting API limits'),
    'api.lever.co': Limit(2, 0.5, 'documented normal request rate'),
    'api.eu.lever.co': Limit(2, 0.5, 'documented normal request rate'),
    'boards-api.greenhouse.io': Limit(3, 0.15, ''),
    'www.amazon.jobs': Limit(2, 0.4, ''),
    'remotive.com': Limit(2, 0.4, ''),
    'jobicy.com': Limit(2, 0.4, ''),
    'www.arbeitnow.com': Limit(2, 0.3, ''),
}
DEFAULT_LIMIT = Limit(4, 0.0)

_GATES = {}
_GATES_LOCK = threading.Lock()


class _Gate(object):
    """One provider's concurrency semaphore plus its minimum-interval clock."""

    def __init__(self, limit):
        self.limit = limit
        self.semaphore = threading.BoundedSemaphore(limit.concurrency)
        self.lock = threading.Lock()
        self.next_allowed = 0.0

    def __enter__(self):
        self.semaphore.acquire()
        if self.limit.min_interval:
            with self.lock:
                wait = self.next_allowed - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self.next_allowed = time.monotonic() + self.limit.min_interval
        return self

    def __exit__(self, *exc):
        self.semaphore.release()
        return False


def limit_for(url):
    host = (urlsplit(str(url or '')).netloc or '').casefold()
    return PROVIDER_LIMITS.get(host, DEFAULT_LIMIT)


def _gate(url):
    host = (urlsplit(str(url or '')).netloc or '').casefold()
    with _GATES_LOCK:
        gate = _GATES.get(host)
        if gate is None:
            gate = _Gate(PROVIDER_LIMITS.get(host, DEFAULT_LIMIT))
            _GATES[host] = gate
        return gate


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------
class SourceProbe(object):
    """What one source's fetch did at the HTTP level.

    Collected so Config can answer "why did this source return nothing?" with
    a status code and a retry count instead of a shrug, and so the scan can
    tell a real empty board from a throttled one.  It holds no request
    headers and no credentials - there are none to hold.
    """

    def __init__(self, name=''):
        self.name = name
        self.requests = 0
        self.retries = 0
        self.throttled = 0
        self.statuses = []
        self.last_status = 0
        self.last_error = ''
        self.outcome = SUCCESS
        self.lock = threading.Lock()

    def record(self, status=0, retried=0, throttled=0, error=''):
        with self.lock:
            self.requests += 1
            self.retries += int(retried)
            self.throttled += int(throttled)
            if status:
                self.last_status = int(status)
                self.statuses.append(int(status))
            if error:
                self.last_error = str(error)[:400]

    def as_dict(self):
        return {
            'requests': self.requests,
            'retries': self.retries,
            'throttled': self.throttled,
            'http_status': self.last_status,
            'outcome': self.outcome,
            'error': self.last_error,
        }


_PROBE = contextvars.ContextVar('source_probe', default=None)


def start_probe(name=''):
    """Begin collecting diagnostics for the current source fetch."""
    probe = SourceProbe(name)
    _PROBE.set(probe)
    return probe


def current_probe():
    return _PROBE.get()


def _note(status=0, retried=0, throttled=0, error=''):
    probe = _PROBE.get()
    if probe is not None:
        probe.record(status=status, retried=retried, throttled=throttled, error=error)


class SourceError(RuntimeError):
    """Raised when a portal cannot be queried; never aborts the whole scan.

    Carries *what kind* of failure it was, because the caller has to be able
    to tell "the provider throttled us" from "the board is empty" - only the
    second one is allowed to retire jobs.
    """

    def __init__(self, message, outcome=HTTP_ERROR, status=0, retry_after=None, attempts=1):
        super().__init__(message)
        self.outcome = outcome
        self.status = int(status or 0)
        self.retry_after = retry_after
        self.attempts = int(attempts or 1)


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
        raise SourceError('Unknown source type: {0}'.format(type_name),
                          outcome=HTTP_ERROR)
    return cls()


def adapter_types():
    return sorted(_REGISTRY)


def _sleep(seconds):
    """Indirection so the retry policy can be tested without real waiting."""
    if seconds > 0:
        time.sleep(seconds)


def _open(url, headers, timeout, data=None):
    """One paced, bounded-retry request.  Returns ``(body, charset)``.

    The loop is deliberately small: pace, send, classify, decide whether the
    failure is worth another attempt, wait, repeat.  Whatever it ends up
    raising says which kind of failure it was, so the scan can act on the
    difference instead of guessing.
    """
    gate = _gate(url)
    last = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = Request(url, data=data, headers=headers)
        try:
            with gate:
                with urlopen(request, timeout=timeout) as response:
                    charset = response.headers.get_content_charset() or 'utf-8'
                    body = response.read()
            _note(status=getattr(response, 'status', 200) or 200,
                  retried=attempt - 1)
            return body, charset
        except HTTPError as exc:                       # a real answer, just not a good one
            status = int(getattr(exc, 'code', 0) or 0)
            retry_after = parse_retry_after(_header(exc, 'Retry-After'))
            throttled = 1 if status in RATE_LIMIT_STATUSES else 0
            outcome = RATE_LIMITED if throttled else HTTP_ERROR
            last = SourceError(
                _http_message(status, url, retry_after),
                outcome=outcome, status=status, retry_after=retry_after, attempts=attempt)
            _note(status=status, throttled=throttled, error=str(last))
            if status not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                break
            _sleep(backoff_delay(attempt, retry_after))
        except socket.timeout as exc:
            last = SourceError('Timed out after {0}s: {1}'.format(timeout, url),
                               outcome=TIMEOUT, attempts=attempt)
            _note(error=str(last))
            if attempt == MAX_ATTEMPTS:
                break
            _sleep(backoff_delay(attempt))
        except URLError as exc:
            timed_out = isinstance(exc.reason, socket.timeout) or 'timed out' in str(exc.reason)
            last = SourceError(
                'Could not reach {0}: {1}'.format(url, exc.reason),
                outcome=TIMEOUT if timed_out else NETWORK_ERROR, attempts=attempt)
            _note(error=str(last))
            if not timed_out or attempt == MAX_ATTEMPTS:
                break
            _sleep(backoff_delay(attempt))
    raise last


def _header(exc, name):
    headers = getattr(exc, 'headers', None)
    if headers is None:
        return ''
    try:
        return headers.get(name, '') or ''
    except Exception:        # noqa: BLE001 - a malformed header is not a crash
        return ''


def rate_limit_headers(headers):
    """The public rate-limit headers a response carried, and nothing else.

    A whitelist rather than a copy: diagnostics are shown in Config and must
    never become a place where a header with a token in it turns up.
    """
    out = {}
    if headers is None:
        return out
    for name in RATE_LIMIT_HEADERS:
        try:
            value = headers.get(name)
        except Exception:    # noqa: BLE001
            value = None
        if value:
            out[name] = str(value)[:80]
    return out


def _http_message(status, url, retry_after):
    if status in RATE_LIMIT_STATUSES:
        wait = '' if retry_after is None else ' (Retry-After: {0:.0f}s)'.format(retry_after)
        return 'HTTP 429 rate limited by {0}{1}'.format(urlsplit(url).netloc or url, wait)
    return 'HTTP {0} from {1}'.format(status, url)


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
        raise SourceError('Invalid JSON from {0}: {1}'.format(url, exc),
                          outcome=PARSE_ERROR) from exc


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
    hammering a site with more than a few concurrent requests.  The real
    ceiling is the per-provider gate in :func:`_open`; this number only decides
    how many threads queue up behind it.

    The calling context is carried into each worker, so a detail page fetched
    in one of these threads still records its status, its retries and its
    throttling against the source that asked for it.
    """
    items = list(items)
    if not items:
        return []
    results = []
    workers = max(1, min(max_workers, len(items)))
    probe = _PROBE.get()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for outcome in pool.map(_safely(worker, probe), items):
            if outcome is not None:
                results.append(outcome)
    return results


def _safely(worker, probe=None):
    def call(item):
        # Each pool thread starts with a fresh context, so the probe has to be
        # handed over explicitly - otherwise a detail page's 429 would be
        # invisible to the source that asked for it.
        if probe is not None:
            _PROBE.set(probe)
        try:
            return worker(item)
        except Exception:  # noqa: BLE001 - one bad detail page must not lose the rest
            return None
    return call
