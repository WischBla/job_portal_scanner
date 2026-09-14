"""A source that could not answer must never retire a job.

Yesterday's failure mode, written down: some source requests failed, and the
scan treated "this source told us nothing" exactly like "this source told us
the job is gone".  Those are different facts and only the second one is
evidence.

The rule, in one sentence: **only a successful, authoritative reconciliation
may move a job to EXPIRED.**  Everything else - 429, timeout, 500, a payload
that will not parse, a suspiciously empty answer - leaves that source's stored
jobs exactly where they are.

Three layers:

``RetryPolicyTests``
    The pure parts of the policy: how long to wait, and whether ``Retry-After``
    wins. No sleeping, no sockets - the delay function takes its jitter as an
    argument precisely so this can be asserted rather than timed.

``RequestHandlingTests``
    :func:`jobscanner.sources.base._open` against a fake ``urlopen``: what gets
    retried, what does not, how many times, and what the resulting error says
    about itself.

``LifecycleTests``
    The part that matters. One scan stores the jobs, a second scan fails in
    each of the six ways, and the jobs have to still be there.
"""

import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from jobscanner import db as jsdb
from jobscanner import pipeline
from jobscanner.repository import EXPIRE_AFTER_MISSES, JobRepository
from jobscanner.sources import base
from tests.helpers import STRONG_DESCRIPTION, TempDatabase

SOURCE = 'Fake Board'

JOBS = [
    {'source': SOURCE, 'source_type': 'greenhouse', 'external_id': 'a',
     'company': 'Example AG', 'title': 'Head of Platform Engineering',
     'location': 'Zurich, Switzerland', 'job_url': 'https://example.test/a',
     'description': STRONG_DESCRIPTION},
    {'source': SOURCE, 'source_type': 'greenhouse', 'external_id': 'b',
     'company': 'Example AG', 'title': 'Head of Site Reliability Engineering',
     'location': 'Bern, Switzerland', 'job_url': 'https://example.test/b',
     'description': STRONG_DESCRIPTION},
]


def headers(mapping):
    """A stand-in for the response headers, with the two accessors used."""
    class _Headers(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

        def get_content_charset(self):
            return 'utf-8'
    return _Headers(mapping or {})


# --------------------------------------------------------------------------
# 1. the policy itself
# --------------------------------------------------------------------------
class RetryPolicyTests(unittest.TestCase):
    def test_the_backoff_is_exponential_and_bounded(self):
        full = [base.backoff_delay(n, jitter=lambda: 1.0) for n in range(1, 8)]
        self.assertEqual(full[:3], [1.0, 2.0, 4.0])
        for delay in full:
            self.assertLessEqual(delay, base.MAX_BACKOFF)
        self.assertEqual(full[-1], base.MAX_BACKOFF)      # it really does stop growing

    def test_the_jitter_is_full_jitter(self):
        """Anywhere between zero and the ceiling - not the ceiling every time."""
        self.assertEqual(base.backoff_delay(3, jitter=lambda: 0.0), 0.0)
        self.assertEqual(base.backoff_delay(3, jitter=lambda: 0.5), 2.0)
        self.assertEqual(base.backoff_delay(3, jitter=lambda: 1.0), 4.0)

    def test_retry_after_is_honoured_and_never_undercut(self):
        self.assertEqual(base.backoff_delay(1, retry_after=9, jitter=lambda: 0.0), 9.0)
        self.assertGreaterEqual(base.backoff_delay(1, retry_after=9, jitter=lambda: 1.0), 9.0)

    def test_retry_after_beats_the_backoff_even_when_the_backoff_is_longer(self):
        """The server knows its own window; the formula is only a fallback."""
        self.assertEqual(base.backoff_delay(5, retry_after=2, jitter=lambda: 0.0), 2.0)

    def test_a_wild_retry_after_cannot_stall_a_local_scan(self):
        self.assertEqual(base.backoff_delay(1, retry_after=86400, jitter=lambda: 0.0),
                         base.MAX_RETRY_AFTER)

    def test_retry_after_is_read_in_both_legal_spellings(self):
        self.assertEqual(base.parse_retry_after('30'), 30.0)
        self.assertGreater(base.parse_retry_after('Wed, 21 Oct 2099 07:28:00 GMT'), 0)
        self.assertIsNone(base.parse_retry_after(''))
        self.assertIsNone(base.parse_retry_after('soon please'))

    def test_the_retry_count_is_bounded(self):
        self.assertGreaterEqual(base.MAX_ATTEMPTS, 2)
        self.assertLessEqual(base.MAX_ATTEMPTS, 5)

    def test_the_noisy_providers_are_paced(self):
        """SmartRecruiters and Lever get a real ceiling, not a burst."""
        for host in ('https://api.smartrecruiters.com/v1/companies/x/postings',
                     'https://api.lever.co/v0/postings/x',
                     'https://api.eu.lever.co/v0/postings/x'):
            limit = base.limit_for(host)
            self.assertLessEqual(limit.concurrency, 2, host)
            self.assertGreater(limit.min_interval, 0.0, host)

    def test_only_public_rate_limit_headers_are_ever_kept(self):
        kept = base.rate_limit_headers(headers({
            'Retry-After': '30', 'X-RateLimit-Remaining': '0',
            'Authorization': 'Bearer super-secret', 'Cookie': 'session=abc',
            'Set-Cookie': 'session=abc',
        }))
        self.assertEqual(kept, {'Retry-After': '30', 'X-RateLimit-Remaining': '0'})
        self.assertNotIn('Authorization', kept)
        self.assertNotIn('Cookie', kept)


# --------------------------------------------------------------------------
# 2. one request
# --------------------------------------------------------------------------
class FakeResponse(object):
    def __init__(self, body=b'{}', status=200):
        self._body = body
        self.status = status
        self.headers = headers({'Content-Type': 'application/json'})

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RequestHandlingTests(unittest.TestCase):
    def setUp(self):
        self.slept = []
        patcher = mock.patch.object(base, '_sleep', self.slept.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open(self, side_effect):
        with mock.patch.object(base, 'urlopen', side_effect=side_effect) as opened:
            try:
                body, _charset = base._open('https://example.test/x', {}, 5)
                return body, None, opened
            except base.SourceError as exc:
                return None, exc, opened

    def http_error(self, status, retry_after=None):
        head = headers({'Retry-After': str(retry_after)} if retry_after else {})
        return HTTPError('https://example.test/x', status, 'boom', head, None)

    def test_a_429_is_retried_and_reported_as_rate_limited(self):
        _body, error, opened = self.open(self.http_error(429, 5))
        self.assertEqual(error.outcome, base.RATE_LIMITED)
        self.assertEqual(error.status, 429)
        self.assertEqual(opened.call_count, base.MAX_ATTEMPTS)
        self.assertEqual(len(self.slept), base.MAX_ATTEMPTS - 1)
        for delay in self.slept:
            self.assertGreaterEqual(delay, 5.0)      # Retry-After was respected

    def test_a_429_that_clears_succeeds_without_an_error(self):
        attempts = [self.http_error(429, 1), FakeResponse(b'{"ok": true}')]
        body, error, opened = self.open(attempts)
        self.assertIsNone(error)
        self.assertEqual(body, b'{"ok": true}')
        self.assertEqual(opened.call_count, 2)

    def test_a_500_is_retried_then_reported_as_an_http_error(self):
        _body, error, opened = self.open(self.http_error(500))
        self.assertEqual(error.outcome, base.HTTP_ERROR)
        self.assertEqual(error.status, 500)
        self.assertEqual(opened.call_count, base.MAX_ATTEMPTS)

    def test_a_404_is_not_retried(self):
        """A 404 is an answer. Asking again three times is just noise."""
        _body, error, opened = self.open(self.http_error(404))
        self.assertEqual(error.outcome, base.HTTP_ERROR)
        self.assertEqual(error.status, 404)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(self.slept, [])

    def test_a_timeout_is_retried_and_reported_as_a_timeout(self):
        _body, error, opened = self.open(TimeoutError('timed out'))
        self.assertEqual(error.outcome, base.TIMEOUT)
        self.assertEqual(opened.call_count, base.MAX_ATTEMPTS)

    def test_an_unreachable_host_is_not_retried_forever(self):
        _body, error, opened = self.open(URLError('no route to host'))
        self.assertEqual(error.outcome, base.NETWORK_ERROR)
        self.assertEqual(opened.call_count, 1)

    def test_a_broken_payload_is_a_parse_error(self):
        with mock.patch.object(base, 'urlopen', return_value=FakeResponse(b'not json')):
            with self.assertRaises(base.SourceError) as caught:
                base.http_json('https://example.test/x')
        self.assertEqual(caught.exception.outcome, base.PARSE_ERROR)

    def test_the_probe_records_what_happened(self):
        probe = base.start_probe('Fake Board')
        self.open(self.http_error(429, 1))
        self.assertEqual(probe.last_status, 429)
        self.assertGreaterEqual(probe.throttled, 1)
        self.assertEqual(probe.as_dict()['http_status'], 429)


# --------------------------------------------------------------------------
# 3. what a failure is allowed to do to stored jobs
# --------------------------------------------------------------------------
class LifecycleTests(unittest.TestCase):
    """The rule the whole change exists for."""

    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        with jsdb.connect() as conn:
            conn.execute('DELETE FROM job_sources')
            conn.execute('INSERT INTO job_sources (name,source_type,config_json,enabled,'
                         "created_at,updated_at) VALUES (?,'greenhouse','{}',1,'','')",
                         (SOURCE,))
            conn.commit()

    def scan(self, payload):
        def fetch(source, profile):
            if isinstance(payload, Exception):
                raise payload
            return [dict(job) for job in payload]
        return pipeline.run_scan(fetcher=fetch)

    def states(self):
        with jsdb.connect() as conn:
            return {row[0]: row[1] for row in conn.execute(
                'SELECT title, state FROM discovered_jobs').fetchall()}

    def active(self):
        return {title for title, state in self.states().items() if state != 'EXPIRED'}

    def assert_nothing_expired(self, result, outcome):
        self.assertEqual(len(self.active()), 2)
        self.assertEqual(result['expired_count'], 0)
        self.assertEqual(result['authoritative_sources'], 0)
        self.assertEqual(result['source_results'][0]['outcome'], outcome)
        self.assertFalse(result['source_results'][0]['authoritative'])

    # -- the six failure modes --------------------------------------------
    def test_a_429_does_not_expire_existing_jobs(self):
        self.scan(JOBS)
        result = self.scan(base.SourceError('rate limited', outcome=base.RATE_LIMITED,
                                            status=429, retry_after=30))
        self.assert_nothing_expired(result, base.RATE_LIMITED)

    def test_a_timeout_does_not_expire_existing_jobs(self):
        self.scan(JOBS)
        result = self.scan(base.SourceError('timed out', outcome=base.TIMEOUT))
        self.assert_nothing_expired(result, base.TIMEOUT)

    def test_a_500_does_not_expire_existing_jobs(self):
        self.scan(JOBS)
        result = self.scan(base.SourceError('server error', outcome=base.HTTP_ERROR,
                                            status=500))
        self.assert_nothing_expired(result, base.HTTP_ERROR)

    def test_a_parser_failure_does_not_expire_existing_jobs(self):
        self.scan(JOBS)
        result = self.scan(base.SourceError('bad payload', outcome=base.PARSE_ERROR))
        self.assert_nothing_expired(result, base.PARSE_ERROR)

    def test_an_unexpected_crash_does_not_expire_existing_jobs(self):
        """An adapter bug is a failure of ours, not evidence about the board."""
        self.scan(JOBS)
        result = self.scan(RuntimeError('adapter blew up'))
        self.assertEqual(len(self.active()), 2)
        self.assertEqual(result['expired_count'], 0)

    def test_a_suspicious_empty_response_does_not_expire_existing_jobs(self):
        """Two jobs yesterday, silence today, no error: not an answer."""
        self.scan(JOBS)
        result = self.scan([])
        self.assert_nothing_expired(result, base.EMPTY_BUT_SUSPICIOUS)

    # -- and what a real answer is allowed to do --------------------------
    def test_an_authoritative_empty_response_expires_only_after_confirmation(self):
        """A board that never had anything, and honestly still has nothing.

        Even then it takes ``EXPIRE_AFTER_MISSES`` consecutive clean scans:
        one absent mention is not a confirmation.
        """
        self.scan(JOBS)
        with jsdb.connect() as conn:          # forget that it ever returned jobs
            conn.execute('UPDATE job_sources SET last_job_count=0')
            conn.commit()

        first = self.scan([])
        self.assertEqual(first['source_results'][0]['outcome'], base.SUCCESS)
        self.assertTrue(first['source_results'][0]['authoritative'])
        self.assertEqual(first['expired_count'], 0)       # one miss is not evidence
        self.assertEqual(len(self.active()), 2)

        second = self.scan([])
        self.assertEqual(second['expired_count'], 2)
        self.assertEqual(self.active(), set())

    def test_the_miss_counter_resets_when_a_job_comes_back(self):
        self.scan(JOBS)
        with jsdb.connect() as conn:
            conn.execute('UPDATE job_sources SET last_job_count=0')
            conn.commit()
        self.scan([])                                     # one miss
        self.scan(JOBS)                                   # and it is back
        with jsdb.connect() as conn:
            misses = [row[0] for row in conn.execute(
                'SELECT missing_scans FROM discovered_jobs').fetchall()]
        self.assertEqual(misses, [0, 0])

    def test_a_confirmed_filter_rejection_retires_immediately(self):
        """Seen and rejected is a decision, not a silence."""
        self.scan(JOBS)
        moved = [dict(job, location='Munich, Germany') for job in JOBS]
        result = self.scan(moved)
        self.assertEqual(result['filtered_count'], 2)
        self.assertEqual(self.active(), set())
        with jsdb.connect() as conn:
            reasons = {row[0] for row in conn.execute(
                'SELECT lifecycle_reason FROM discovered_jobs').fetchall()}
        self.assertEqual(reasons, {'no_longer_matches_filters'})

    def test_a_saved_job_is_never_retired_by_any_of_this(self):
        self.scan(JOBS)
        with jsdb.connect() as conn:
            repo = JobRepository(conn)
            repo.set_state(repo.list_jobs(limit=1)[0]['id'], 'SAVED')
            conn.execute('UPDATE job_sources SET last_job_count=0')
            conn.commit()
        for _ in range(EXPIRE_AFTER_MISSES + 1):
            self.scan([])
        self.assertIn('SAVED', self.states().values())

    def test_a_score_is_never_a_lifecycle_event(self):
        """However badly a job scores, the scan leaves its state alone."""
        self.scan(JOBS)
        with jsdb.connect() as conn:
            conn.execute('UPDATE discovered_jobs SET match_score=3')
            conn.commit()
        self.scan(JOBS)
        self.assertEqual(len(self.active()), 2)

    # -- diagnostics -------------------------------------------------------
    def test_the_failure_is_diagnosable_afterwards(self):
        self.scan(JOBS)
        self.scan(base.SourceError('rate limited', outcome=base.RATE_LIMITED, status=429))
        with jsdb.connect() as conn:
            row = dict(conn.execute('SELECT * FROM job_sources').fetchone())
        self.assertEqual(row['last_outcome'], base.RATE_LIMITED)
        self.assertEqual(row['last_http_status'], 429)
        self.assertTrue(row['last_attempt_at'])
        self.assertTrue(row['last_success_at'])       # the earlier success is kept
        self.assertEqual(row['last_job_count'], 2)    # and so is what it returned

    def test_the_diagnostics_reach_the_config_screen(self):
        from jobscanner.watchlist import source_health
        self.scan(JOBS)
        self.scan(base.SourceError('timed out', outcome=base.TIMEOUT))
        with jsdb.connect() as conn:
            health = source_health(conn)
        row = health['diagnostics'][0]
        for key in ('source', 'last_attempt_at', 'last_success_at', 'status', 'http_status',
                    'jobs_returned', 'retries', 'error', 'authoritative'):
            self.assertIn(key, row)
        self.assertFalse(row['authoritative'])
        self.assertEqual(row['outcome'], base.TIMEOUT)

    def test_no_diagnostic_field_can_carry_a_credential(self):
        from jobscanner.watchlist import source_health
        self.scan(JOBS)
        with jsdb.connect() as conn:
            blob = repr(source_health(conn))
        for secret in ('authorization', 'bearer', 'cookie', 'api_key', 'token='):
            self.assertNotIn(secret, blob.lower())


if __name__ == '__main__':
    unittest.main()
