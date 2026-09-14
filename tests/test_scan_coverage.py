"""Scan behaviour once company sources are in play.

Covers the things that only show up when the watchlist is a real discovery
source: aggregator/company deduplication, a broken company source not killing
the run, the Switzerland gate still applying to company jobs, and company
priority staying a tie-breaker instead of a ranking.
"""

import unittest

from jobscanner import db as jsdb
from jobscanner import pipeline
from jobscanner.watchlist import CompanyWatchlist
from tests.helpers import STRONG_DESCRIPTION, TempDatabase

AGGREGATOR = 'Arbeitnow'
COMPANY_SOURCE = 'Watchlist · Example AG'
BROKEN_SOURCE = 'Watchlist · Broken AG'


def raw(source, source_type, external_id, title, company, location, url, description=None):
    return {'source': source, 'source_type': source_type, 'external_id': external_id,
            'company': company, 'title': title, 'location': location, 'job_url': url,
            'description': description if description is not None else STRONG_DESCRIPTION}


class ScanCoverageTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        with jsdb.connect() as conn:
            conn.execute('DELETE FROM job_sources')
            conn.execute('DELETE FROM company_watchlist')
            conn.commit()
            watch = CompanyWatchlist(conn)
            watch.create({'company_name': 'Example AG', 'priority': 'A',
                          'career_source_type': 'greenhouse',
                          'career_source_identifier': 'exampleag'})
            watch.create({'company_name': 'Broken AG', 'priority': 'A',
                          'career_source_type': 'greenhouse',
                          'career_source_identifier': 'brokenag'})
            conn.execute('INSERT INTO job_sources (name,source_type,config_json,enabled,'
                         "created_at,updated_at) VALUES (?,'arbeitnow','{}',1,'','')",
                         (AGGREGATOR,))
            conn.commit()

    def scan(self, payloads):
        """``payloads`` maps a source name to a list of raw jobs or an exception."""
        def fetch(source, profile):
            payload = payloads.get(source['name'], [])
            if isinstance(payload, Exception):
                raise payload
            return [dict(job) for job in payload]
        return pipeline.run_scan(fetcher=fetch)

    def jobs(self):
        with jsdb.connect() as conn:
            return [dict(r) for r in conn.execute(
                'SELECT * FROM discovered_jobs ORDER BY match_score DESC, id').fetchall()]

    # -- deduplication -----------------------------------------------------
    def test_the_same_url_from_two_sources_is_stored_once(self):
        url = 'https://example.test/jobs/head-of-platform'
        result = self.scan({
            COMPANY_SOURCE: [raw(COMPANY_SOURCE, 'greenhouse', 'gh-1',
                                 'Head of Platform Engineering', 'Example AG',
                                 'Zurich, Switzerland', url)],
            AGGREGATOR: [raw(AGGREGATOR, 'arbeitnow', 'an-1',
                             'Head of Platform Engineering', 'Example AG',
                             'Zurich, Switzerland', url + '?utm_source=feed')],
        })
        self.assertEqual(result['duplicate_count'], 1)
        self.assertEqual(len(self.jobs()), 1)

    def test_the_richer_copy_of_a_duplicate_is_the_one_that_is_kept(self):
        """An aggregator stub must not displace the company's full advert.

        Whichever fetch finished first used to win, so a job with a perfectly
        good description could end up stored as an evidence-LOW stub and be
        marked as needing enrichment it did not need.
        """
        url = 'https://example.test/jobs/head-of-platform'
        self.scan({
            AGGREGATOR: [raw(AGGREGATOR, 'arbeitnow', 'an-1',
                             'Head of Platform Engineering', 'Example AG',
                             'Zurich, Switzerland', url + '?utm_source=feed',
                             description='')],
            COMPANY_SOURCE: [raw(COMPANY_SOURCE, 'greenhouse', 'gh-1',
                                 'Head of Platform Engineering', 'Example AG',
                                 'Zurich, Switzerland', url)],
        })
        jobs = self.jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['description'], STRONG_DESCRIPTION)
        self.assertNotEqual(jobs[0]['enrichment_state'], 'NEEDS_ENRICHMENT')

    def test_the_same_role_under_two_urls_collapses_across_sources(self):
        """The aggregator and the company board rarely share a URL."""
        result = self.scan({
            COMPANY_SOURCE: [raw(COMPANY_SOURCE, 'greenhouse', 'gh-1',
                                 'Head of Platform Engineering', 'Example AG',
                                 'Zurich, Switzerland', 'https://boards.test/example/1')],
            AGGREGATOR: [raw(AGGREGATOR, 'arbeitnow', 'an-1',
                             'Head of Platform Engineering', 'Example AG',
                             'Zurich, CH', 'https://arbeitnow.test/example-head-of-platform')],
        })
        self.assertEqual(result['duplicate_count'], 1)
        self.assertEqual(len(self.jobs()), 1)

    def test_two_real_openings_with_one_title_inside_one_source_both_survive(self):
        """A big employer genuinely does post the same title twice in a city."""
        result = self.scan({
            COMPANY_SOURCE: [
                raw(COMPANY_SOURCE, 'greenhouse', 'gh-1', 'Head of Platform Engineering',
                    'Example AG', 'Zurich, Switzerland', 'https://boards.test/example/1'),
                raw(COMPANY_SOURCE, 'greenhouse', 'gh-2', 'Head of Platform Engineering',
                    'Example AG', 'Zurich, Switzerland', 'https://boards.test/example/2'),
            ],
        })
        self.assertEqual(result['duplicate_count'], 0)
        self.assertEqual(len(self.jobs()), 2)

    # -- failure containment ----------------------------------------------
    def test_a_broken_company_source_does_not_abort_the_scan(self):
        result = self.scan({
            COMPANY_SOURCE: [raw(COMPANY_SOURCE, 'greenhouse', 'gh-1',
                                 'Head of Platform Engineering', 'Example AG',
                                 'Zurich, Switzerland', 'https://boards.test/example/1')],
            BROKEN_SOURCE: RuntimeError('HTTP 403 from boards-api.greenhouse.io'),
        })
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['matched_count'], 1)
        self.assertEqual(result['source_failure_count'], 1)
        self.assertEqual(result['errors'][0]['source'], BROKEN_SOURCE)
        self.assertIn('403', result['errors'][0]['error'])

    def test_a_failure_is_recorded_against_the_company_and_the_source(self):
        self.scan({
            COMPANY_SOURCE: [raw(COMPANY_SOURCE, 'greenhouse', 'gh-1', 'Head of Platform',
                                 'Example AG', 'Zurich, Switzerland',
                                 'https://boards.test/example/1')],
            BROKEN_SOURCE: RuntimeError('HTTP 403'),
        })
        with jsdb.connect() as conn:
            watch = CompanyWatchlist(conn)
            broken = watch.get_by_name('Broken AG')
            good = watch.get_by_name('Example AG')
            statuses = {r['name']: dict(r) for r in conn.execute(
                'SELECT name, last_status, last_error, last_job_count FROM job_sources')}
        self.assertEqual(broken['source_status'], 'ERROR')
        self.assertIn('403', broken['last_error'])
        self.assertEqual(good['source_status'], 'ACTIVE')
        self.assertEqual(good['job_count_last_scan'], 1)
        self.assertEqual(statuses[BROKEN_SOURCE]['last_status'], 'ERROR')
        self.assertEqual(statuses[COMPANY_SOURCE]['last_status'], 'ACTIVE')

    def test_a_failing_source_does_not_expire_its_stored_jobs(self):
        payload = [raw(COMPANY_SOURCE, 'greenhouse', 'gh-1', 'Head of Platform Engineering',
                       'Example AG', 'Zurich, Switzerland', 'https://boards.test/example/1')]
        self.scan({COMPANY_SOURCE: payload})
        self.assertEqual(self.jobs()[0]['state'], 'NEW')
        self.scan({COMPANY_SOURCE: RuntimeError('HTTP 500')})
        self.assertNotEqual(self.jobs()[0]['state'], 'EXPIRED')

    # -- geography still decides ------------------------------------------
    def test_switzerland_filtering_still_applies_to_company_sources(self):
        result = self.scan({COMPANY_SOURCE: [
            raw(COMPANY_SOURCE, 'greenhouse', 'ch', 'Head of Platform Engineering',
                'Example AG', 'Zurich, Switzerland', 'https://boards.test/example/ch'),
            raw(COMPANY_SOURCE, 'greenhouse', 'de', 'Head of Platform Engineering',
                'Example AG', 'Berlin, Germany', 'https://boards.test/example/de'),
            raw(COMPANY_SOURCE, 'greenhouse', 'emea', 'Head of Platform Engineering',
                'Example AG', 'Remote - EMEA', 'https://boards.test/example/emea'),
            raw(COMPANY_SOURCE, 'greenhouse', 'dach', 'Head of Platform Engineering',
                'Example AG', 'DACH', 'https://boards.test/example/dach'),
        ]})
        self.assertEqual(result['matched_count'], 1)
        self.assertEqual([j['normalized_city'] for j in self.jobs()], ['Zurich'])

    def test_swiss_eligibility_is_counted_in_the_scan_summary(self):
        result = self.scan({COMPANY_SOURCE: [
            raw(COMPANY_SOURCE, 'greenhouse', 'ch1', 'Head of Platform Engineering',
                'Example AG', 'Zug, Switzerland', 'https://boards.test/example/1'),
            raw(COMPANY_SOURCE, 'greenhouse', 'ch2', 'Recruiting Manager',
                'Example AG', 'Lucerne, Switzerland', 'https://boards.test/example/2'),
            raw(COMPANY_SOURCE, 'greenhouse', 'de', 'Head of Platform Engineering',
                'Example AG', 'Munich, Germany', 'https://boards.test/example/3'),
        ]})
        # Both Swiss jobs are eligible; only one of them is relevant.
        self.assertEqual(result['swiss_eligible_count'], 2)
        self.assertEqual(result['matched_count'], 1)
        self.assertEqual(result['fetched_count'], 3)

    def test_the_summary_reports_what_the_brief_asks_for(self):
        result = self.scan({
            COMPANY_SOURCE: [raw(COMPANY_SOURCE, 'greenhouse', 'gh-1',
                                 'Head of Platform Engineering', 'Example AG',
                                 'Zurich, Switzerland', 'https://boards.test/example/1')],
            BROKEN_SOURCE: RuntimeError('HTTP 403'),
        })
        for key in ('sources_scanned', 'companies_scanned', 'fetched_count',
                    'swiss_eligible_count', 'matched_count', 'new_count',
                    'source_failure_count'):
            self.assertIn(key, result, key)
        self.assertEqual(result['sources_scanned'], 3)      # 2 companies + 1 aggregator
        self.assertEqual(result['companies_scanned'], 2)
        self.assertEqual(result['new_count'], 1)

    def test_the_summary_is_persisted_on_the_run(self):
        result = self.scan({COMPANY_SOURCE: [
            raw(COMPANY_SOURCE, 'greenhouse', 'gh-1', 'Head of Platform Engineering',
                'Example AG', 'Zurich, Switzerland', 'https://boards.test/example/1')]})
        with jsdb.connect() as conn:
            row = dict(conn.execute('SELECT * FROM scout_runs WHERE id=?',
                                    (result['run_id'],)).fetchone())
        self.assertEqual(row['companies_scanned'], 2)
        self.assertEqual(row['swiss_eligible_count'], 1)
        self.assertEqual(row['source_failure_count'], 0)


class CompanyPriorityTests(unittest.TestCase):
    """Priority may break a tie. It may never beat a better match."""

    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        with jsdb.connect() as conn:
            conn.execute('DELETE FROM job_sources')
            conn.execute('DELETE FROM company_watchlist')
            conn.commit()
            watch = CompanyWatchlist(conn)
            watch.create({'company_name': 'Google', 'priority': 'A'})
            watch.create({'company_name': 'UBS', 'priority': 'B'})
            conn.execute('INSERT INTO job_sources (name,source_type,config_json,enabled,'
                         "created_at,updated_at) VALUES ('Fake','rss','{}',1,'','')")
            # The point here is the ordering, not the cut-off: keep both jobs.
            conn.execute('UPDATE search_profile SET minimum_match_score=0 WHERE id=1')
            conn.commit()

    def scan(self, jobs):
        return pipeline.run_scan(fetcher=lambda source, profile: [dict(j) for j in jobs])

    def ordered(self):
        with jsdb.connect() as conn:
            return [(r['company'], r['match_score']) for r in conn.execute(
                'SELECT company, match_score FROM discovered_jobs '
                'ORDER BY match_score DESC, id').fetchall()]

    def test_a_weak_priority_a_role_does_not_beat_a_strong_priority_b_role(self):
        result = self.scan([
            raw('Fake', 'rss', 'g1', 'Technical Program Manager', 'Google',
                'Zurich, Switzerland', 'https://g.test/1', 'Coordinate a few projects.'),
            raw('Fake', 'rss', 'u1', 'Head of Platform Engineering', 'UBS',
                'Zurich, Switzerland', 'https://u.test/1'),
        ])
        self.assertEqual(result['matched_count'], 2)
        order = self.ordered()
        self.assertEqual(order[0][0], 'UBS')
        self.assertGreater(order[0][1], order[1][1])

    def test_priority_breaks_a_tie_between_equally_good_roles(self):
        self.scan([
            raw('Fake', 'rss', 'u1', 'Head of Platform Engineering', 'UBS',
                'Zurich, Switzerland', 'https://u.test/1'),
            raw('Fake', 'rss', 'g1', 'Head of Platform Engineering', 'Google',
                'Zurich, Switzerland', 'https://g.test/1'),
        ])
        scores = {company: score for company, score in self.ordered()}
        self.assertEqual(scores['Google'], scores['UBS'])   # identical roles, identical score
        ranking = pipeline._ranking(jsdb.connect())
        google = ranking(({'company': 'Google', 'published_at': ''}, {'score': 80}))
        ubs = ranking(({'company': 'UBS', 'published_at': ''}, {'score': 80}))
        other = ranking(({'company': 'Nobody Ltd', 'published_at': ''}, {'score': 80}))
        self.assertLess(google, ubs)        # A before B at the same score
        self.assertLess(ubs, other)         # watched before unwatched
        better_elsewhere = ranking(({'company': 'Nobody Ltd', 'published_at': ''}, {'score': 90}))
        self.assertLess(better_elsewhere, google)   # score always wins

    def test_the_newer_posting_wins_when_score_and_priority_match(self):
        ranking = pipeline._ranking(jsdb.connect())
        newer = ranking(({'company': 'Google', 'published_at': '2026-09-10'}, {'score': 80}))
        older = ranking(({'company': 'Google', 'published_at': '2026-01-10'}, {'score': 80}))
        self.assertLess(newer, older)


if __name__ == '__main__':
    unittest.main()
