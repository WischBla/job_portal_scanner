"""Source adapters, the source registry and per-company source health.

Every adapter is exercised against a recorded fixture of the real payload, so
the tests are offline but the shapes are the ones the live services return.
"""

import json
import unittest
from unittest import mock

from jobscanner import db as jsdb
from jobscanner.sources import amazon_jobs, phenom, registry, smartrecruiters, structured_jobs
from jobscanner.sources import successfactors as sf
from jobscanner.sources.base import SourceError
from jobscanner.watchlist import CompanyWatchlist, source_health
from tests.helpers import TempDatabase


# --------------------------------------------------------------------------
# fixtures: trimmed copies of what the live endpoints actually return
# --------------------------------------------------------------------------
SR_PAGE_1 = {
    'offset': 0, 'limit': 100, 'totalFound': 101,
    'content': [{
        'id': '744000149001009', 'name': 'Data Analytics Lead (Product Intelligence)',
        'company': {'identifier': 'Nexthink', 'name': 'Nexthink'},
        'releasedDate': '2026-09-11T11:58:16.911Z',
        'location': {'city': 'Lausanne', 'region': 'VD', 'country': 'ch',
                     'remote': False, 'hybrid': True,
                     'fullLocation': 'Lausanne, VD, Switzerland'},
    }] * 1 + [{
        'id': str(900 + n), 'name': 'Engineer {0}'.format(n),
        'company': {'identifier': 'Nexthink', 'name': 'Nexthink'},
        'releasedDate': '2026-09-01T00:00:00.000Z',
        'location': {'city': 'Madrid', 'region': 'MD', 'country': 'es'},
    } for n in range(99)],
}
SR_PAGE_2 = {
    'offset': 100, 'limit': 100, 'totalFound': 101,
    'content': [{
        'id': '1001', 'name': 'Head of Platform Engineering',
        'company': {'identifier': 'Nexthink', 'name': 'Nexthink'},
        'releasedDate': '2026-09-10T00:00:00.000Z',
        'location': {'city': 'Zurich', 'region': 'ZH', 'country': 'ch'},
    }],
}
SR_DETAIL = {
    'postingUrl': 'https://jobs.smartrecruiters.com/Nexthink/744000149001009-data-analytics-lead',
    'jobAd': {'sections': {
        'jobDescription': {'text': '<p>Own the <b>data platform</b> roadmap.</p>'},
        'qualifications': {'text': '<p>Kubernetes, SRE, observability.</p>'},
    }},
}

PHENOM_PAGE = {'refineSearch': {'data': {'jobs': [{
    'jobId': '202604-109161', 'jobSeqNo': 'ROCHGLOBAL202604109161EXTERNALENGLOBAL',
    'title': 'Head of Platform Engineering', 'city': 'Basel', 'state': 'Basel-City',
    'country': 'Switzerland', 'postedDate': '2026-08-20T00:00:00.000+0000',
    'multi_location': ['Basel, Basel-City, Switzerland'],
    'descriptionTeaser': 'Lead the platform engineering group.',
    'category': 'Engineering', 'ml_skills': ['kubernetes', 'terraform'],
    'applyUrl': 'https://roche.wd3.myworkdayjobs.com/roche-ext/job/Basel/x',
}]}}}

SF_LIST = '''<table><tbody>
<tr class="data-row">
  <td class="colTitle">
    <span class="jobTitle"><a href="/job/Zurich-AI-Product-Owner-Zuri/1433964433/"
      class="jobTitle-link">AI Product Owner</a></span>
  </td>
  <td class="colLocation"><span class="jobLocation">Zurich, Zurich, CH</span></td>
  <td class="colDate"><span class="jobDate">28 Aug 2026</span></td>
</tr>
<tr class="data-row">
  <td class="colTitle">
    <span class="jobTitle"><a class="jobTitle-link"
      href="/job/Basel-Head-of-Cloud-Basel/1412388733/">Head of Cloud Platform</a></span>
  </td>
  <td class="colLocation"><span class="jobLocation">Basel, CH</span></td>
  <td class="colDate"><span class="jobDate">01 Sep 2026</span></td>
</tr>
</tbody></table>'''
SF_DETAIL = ('<div><span itemprop="description" class="rtltextaligneligible">'
             '<span class="jobdescription"><p>Run the Swiss cloud platform team.</p></span>'
             '</span></div></div></div>')

AMAZON_PAGE = {'hits': 1, 'jobs': [{
    'id_icims': '10530582', 'title': 'Principal Technical Program Manager',
    'company_name': 'AWS EMEA SARL (Switzerland Branch)',
    'normalized_location': 'Zurich, Zurich, CHE', 'posted_date': 'September 5, 2026',
    'job_path': '/en/jobs/10530582/principal-tpm', 'description': '<p>Own reliability.</p>',
    'basic_qualifications': '<p>10 years.</p>', 'preferred_qualifications': '',
    'description_short': 'Own reliability.',
}]}

JSONLD_PAGE = '''<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"WebPage","name":"Careers"},
 {"@type":"JobPosting","title":"Director Technology Operations",
  "datePosted":"2026-09-01","employmentType":"FULL_TIME",
  "identifier":{"@type":"PropertyValue","value":"REQ-42"},
  "hiringOrganization":{"@type":"Organization","name":"Example AG"},
  "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
    "addressLocality":"Zurich","addressCountry":"CH"}},
  "baseSalary":{"@type":"MonetaryAmount","currency":"CHF",
    "value":{"@type":"QuantitativeValue","minValue":190000,"maxValue":230000,"unitText":"YEAR"}},
  "description":"<p>Own platform, SRE and cloud.</p>",
  "url":"https://example.test/jobs/42"}]}
</script></head><body></body></html>'''


class SmartRecruitersTests(unittest.TestCase):
    def fetch(self, pages=(SR_PAGE_1, SR_PAGE_2), detail=SR_DETAIL):
        calls = []

        def fake_json(url, **kwargs):
            calls.append(url)
            if '/postings/' in url:
                return detail
            return pages[0] if 'offset=0' in url else pages[1]

        with mock.patch.object(smartrecruiters, 'http_json', side_effect=fake_json):
            jobs = smartrecruiters.SmartRecruitersAdapter().fetch_jobs(
                {}, {'company_id': 'Nexthink', 'company': 'Nexthink'}, 'Watchlist · Nexthink')
        return jobs, calls

    def test_postings_are_normalized(self):
        jobs, _ = self.fetch()
        job = jobs[0]
        self.assertEqual(job['external_id'], '744000149001009')
        self.assertEqual(job['title'], 'Data Analytics Lead (Product Intelligence)')
        self.assertEqual(job['company'], 'Nexthink')
        self.assertEqual(job['location'], 'Lausanne, VD, Switzerland')
        self.assertEqual(job['published_at'], '2026-09-11T11:58:16.911Z')
        self.assertEqual(job['job_url'], SR_DETAIL['postingUrl'])
        self.assertIn('data platform', job['description'])
        self.assertIn('Kubernetes', job['description'])
        self.assertEqual(job['source_type'], 'smartrecruiters')

    def test_pagination_walks_every_page(self):
        jobs, calls = self.fetch()
        self.assertEqual(len(jobs), 101)
        self.assertIn('Head of Platform Engineering', [j['title'] for j in jobs])
        self.assertTrue(any('offset=100' in url for url in calls))

    def test_pagination_stops_on_a_short_page(self):
        short = {'offset': 0, 'limit': 100, 'totalFound': 1, 'content': SR_PAGE_2['content']}
        jobs, calls = self.fetch(pages=(short, short))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(len([c for c in calls if 'offset=' in c]), 1)

    def test_a_missing_company_identifier_is_an_error(self):
        with self.assertRaises(SourceError):
            smartrecruiters.SmartRecruitersAdapter().fetch_jobs({}, {}, 'x')

    def test_a_posting_without_a_detail_page_still_survives(self):
        """One broken detail lookup must not lose the whole board."""
        def fake_json(url, **kwargs):
            if '/postings/' in url:
                raise SourceError('HTTP 500')
            return SR_PAGE_2 if 'offset=0' in url else {'content': []}

        with mock.patch.object(smartrecruiters, 'http_json', side_effect=fake_json):
            jobs = smartrecruiters.SmartRecruitersAdapter().fetch_jobs(
                {}, {'company_id': 'Nexthink'}, 'Nexthink')
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['description'], '')
        self.assertTrue(jobs[0]['job_url'].endswith('/Nexthink/1001'))


class PhenomTests(unittest.TestCase):
    def test_jobs_are_normalized_and_the_country_filter_is_sent_upstream(self):
        seen = {}

        def fake_json(url, timeout=None, payload=None, **kwargs):
            seen['url'] = url
            seen['payload'] = payload
            return PHENOM_PAGE

        with mock.patch.object(phenom, 'http_json', side_effect=fake_json):
            jobs = phenom.PhenomAdapter().fetch_jobs(
                {}, {'base_url': 'https://careers.roche.com', 'company': 'Roche'},
                'Watchlist · Roche')
        self.assertEqual(seen['url'], 'https://careers.roche.com/widgets')
        self.assertEqual(seen['payload']['selected_fields']['country'], ['Switzerland'])
        job = jobs[0]
        self.assertEqual(job['company'], 'Roche')
        self.assertEqual(job['location'], 'Basel, Basel-City, Switzerland')
        self.assertIn('kubernetes', job['description'])
        self.assertTrue(job['job_url'].startswith(
            'https://careers.roche.com/global/en/job/ROCHGLOBAL202604109161EXTERNALENGLOBAL/'))

    def test_the_legacy_response_envelope_is_understood(self):
        legacy = {'eagerLoadRefineSearch': PHENOM_PAGE['refineSearch']}
        with mock.patch.object(phenom, 'http_json', return_value=legacy):
            jobs = phenom.PhenomAdapter().fetch_jobs(
                {}, {'base_url': 'https://careers.abb', 'company': 'ABB'}, 'ABB')
        self.assertEqual(len(jobs), 1)

    def test_an_unrecognised_response_is_an_error(self):
        with mock.patch.object(phenom, 'http_json', return_value={'nope': True}):
            with self.assertRaises(SourceError):
                phenom.PhenomAdapter().fetch_jobs(
                    {}, {'base_url': 'https://careers.abb'}, 'ABB')

    def test_a_missing_base_url_is_an_error(self):
        with self.assertRaises(SourceError):
            phenom.PhenomAdapter().fetch_jobs({}, {}, 'x')


class SuccessFactorsTests(unittest.TestCase):
    def fetch(self, list_html=SF_LIST):
        pages = []

        def fake_text(url, **kwargs):
            pages.append(url)
            if '/search/' in url:
                return list_html if 'startrow=0' in url else '<table></table>'
            return SF_DETAIL

        with mock.patch.object(sf, 'http_text', side_effect=fake_text):
            jobs = sf.SuccessFactorsAdapter().fetch_jobs(
                {}, {'base_url': 'https://careers.swissre.com', 'company': 'Swiss Re'},
                'Watchlist · Swiss Re')
        return jobs, pages

    def test_rows_are_parsed_in_both_attribute_orders(self):
        jobs, _ = self.fetch()
        self.assertEqual([j['title'] for j in jobs],
                         ['AI Product Owner', 'Head of Cloud Platform'])
        self.assertEqual(jobs[0]['location'], 'Zurich, Zurich, CH')
        self.assertEqual(jobs[0]['external_id'], '1433964433')
        self.assertEqual(jobs[0]['published_at'], '28 Aug 2026')
        self.assertEqual(jobs[0]['job_url'],
                         'https://careers.swissre.com/job/Zurich-AI-Product-Owner-Zuri/1433964433/')
        self.assertEqual(jobs[0]['company'], 'Swiss Re')

    def test_the_description_comes_from_the_detail_page(self):
        jobs, _ = self.fetch()
        self.assertIn('Swiss cloud platform team', jobs[0]['description'])

    def test_the_switzerland_query_is_sent_upstream_and_paging_stops(self):
        _, pages = self.fetch()
        search_pages = [p for p in pages if '/search/' in p]
        self.assertIn('locationsearch=Switzerland', search_pages[0])
        # Two rows is a short page, so exactly one search request is made.
        self.assertEqual(len(search_pages), 1)

    def test_a_missing_base_url_is_an_error(self):
        with self.assertRaises(SourceError):
            sf.SuccessFactorsAdapter().fetch_jobs({}, {}, 'x')


class AmazonJobsTests(unittest.TestCase):
    def test_jobs_are_normalized(self):
        with mock.patch.object(amazon_jobs, 'http_json', return_value=AMAZON_PAGE):
            jobs = amazon_jobs.AmazonJobsAdapter().fetch_jobs(
                {}, {'company': 'Amazon Web Services / AWS'}, 'Watchlist · AWS')
        job = jobs[0]
        self.assertEqual(job['external_id'], '10530582')
        self.assertEqual(job['company'], 'AWS EMEA SARL (Switzerland Branch)')
        self.assertEqual(job['location'], 'Zurich, Zurich, CHE')
        self.assertEqual(job['job_url'], 'https://www.amazon.jobs/en/jobs/10530582/principal-tpm')
        self.assertIn('Own reliability.', job['description'])


class StructuredJobsTests(unittest.TestCase):
    def test_a_jobposting_inside_a_graph_is_found_and_normalized(self):
        with mock.patch.object(structured_jobs, 'http_text', return_value=JSONLD_PAGE):
            jobs = structured_jobs.StructuredJobsAdapter().fetch_jobs(
                {}, {'url': 'https://example.test/careers', 'company': 'Example AG'},
                'Watchlist · Example AG')
        job = jobs[0]
        self.assertEqual(job['title'], 'Director Technology Operations')
        self.assertEqual(job['external_id'], 'REQ-42')
        self.assertEqual(job['company'], 'Example AG')
        self.assertEqual(job['location'], 'Zurich, CH')
        self.assertEqual(job['published_at'], '2026-09-01')
        self.assertEqual(job['job_url'], 'https://example.test/jobs/42')
        self.assertEqual((job['salary_min'], job['salary_max']), (190000.0, 230000.0))
        self.assertEqual(job['salary_currency'], 'CHF')
        self.assertEqual(job['description'], 'Own platform, SRE and cloud.')

    def test_employment_type_and_remote_are_read(self):
        posting = json.loads(
            '{"@type":"JobPosting","title":"SRE Lead","jobLocationType":"TELECOMMUTE",'
            '"employmentType":["FULL_TIME","CONTRACTOR"],'
            '"applicantLocationRequirements":{"@type":"Country","name":"Switzerland"}}')
        job = structured_jobs.normalize_posting(posting)
        self.assertEqual(job['employment_type'], 'FULL_TIME, CONTRACTOR')
        self.assertTrue(job['remote'])
        self.assertEqual(job['location'], 'Switzerland')

    def test_a_page_without_structured_data_is_an_error_not_an_empty_success(self):
        with mock.patch.object(structured_jobs, 'http_text', return_value='<html></html>'):
            with self.assertRaises(SourceError):
                structured_jobs.StructuredJobsAdapter().fetch_jobs(
                    {}, {'url': 'https://example.test/careers'}, 'x')

    def test_a_missing_url_is_an_error(self):
        with self.assertRaises(SourceError):
            structured_jobs.StructuredJobsAdapter().fetch_jobs({}, {}, 'x')


class RegistryTests(unittest.TestCase):
    def test_every_kind_resolves_to_the_config_its_adapter_expects(self):
        cases = {
            'greenhouse': ('proton', 'board_token'),
            'lever': ('sonarsource', 'site'),
            'smartrecruiters': ('Nexthink', 'company_id'),
            'successfactors': ('https://careers.swissre.com', 'base_url'),
            'phenom': ('https://careers.roche.com', 'base_url'),
            'amazon_jobs': ('CHE', 'country_code'),
            'jsonld': ('https://example.test/jobs', 'url'),
            'rss': ('https://example.test/feed.xml', 'url'),
        }
        for kind, (identifier, key) in cases.items():
            resolved = registry.resolve(kind, identifier, 'Example AG')
            self.assertEqual(resolved['config'][key], identifier, kind)
            self.assertEqual(resolved['config']['company'], 'Example AG', kind)
            self.assertTrue(resolved['automated'], kind)
            self.assertTrue(resolved['source_url'], kind)

    def test_an_aggregator_gets_its_adapter_label_not_a_raw_type_name(self):
        """Aggregators are not watchlist kinds, but the UI still names them."""
        self.assertEqual(registry.source_label('arbeitnow'), 'Arbeitnow')
        self.assertEqual(registry.source_label('remotive'), 'Remotive')
        self.assertEqual(registry.source_label('greenhouse'), 'Greenhouse')
        self.assertEqual(registry.source_label('nonsense'), 'nonsense')

    def test_manual_is_not_automated(self):
        resolved = registry.resolve('manual', '', 'Example AG')
        self.assertFalse(resolved['automated'])

    def test_an_unknown_kind_is_rejected(self):
        with self.assertRaises(SourceError):
            registry.resolve('linkedin', 'x', 'y')

    def test_an_invalid_board_identifier_never_becomes_active(self):
        """A guessed token that 404s is an ERROR, not a saved integration."""
        with mock.patch.object(registry, 'get_adapter') as get:
            get.return_value.verify.side_effect = SourceError('HTTP 404 from boards-api')
            status, count, detail = registry.verify('greenhouse', 'not-a-board', 'X')
        self.assertEqual(status, registry.ERROR)
        self.assertEqual(count, 0)
        self.assertIn('404', detail)

    def test_an_endpoint_that_answers_empty_is_unavailable_not_active(self):
        with mock.patch.object(registry, 'get_adapter') as get:
            get.return_value.verify.return_value = (0, 'nothing')
            status, count, _ = registry.verify('greenhouse', 'empty-board', 'X')
        self.assertEqual(status, registry.UNAVAILABLE)
        self.assertEqual(count, 0)

    def test_a_live_endpoint_with_postings_becomes_active(self):
        with mock.patch.object(registry, 'get_adapter') as get:
            get.return_value.verify.return_value = (34, '34 postings returned.')
            status, count, _ = registry.verify('greenhouse', 'proton', 'Proton')
        self.assertEqual((status, count), (registry.ACTIVE, 34))

    def test_a_missing_identifier_is_unavailable(self):
        status, _, detail = registry.verify('greenhouse', '', 'X')
        self.assertEqual(status, registry.UNAVAILABLE)
        self.assertIn('board token', detail.lower())

    def test_verifying_a_manual_entry_contacts_nothing(self):
        status, count, _ = registry.verify('manual', '', 'Google')
        self.assertEqual((status, count), (registry.MANUAL, 0))


class SourceStatusPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def test_an_attached_source_starts_unavailable_until_it_is_proven(self):
        with jsdb.connect() as conn:
            entry = CompanyWatchlist(conn).create({
                'company_name': 'Example AG', 'career_source_type': 'greenhouse',
                'career_source_identifier': 'exampleag'})
        self.assertEqual(entry['source_status'], 'UNAVAILABLE')

    def test_a_manual_entry_is_manual_and_offers_only_the_careers_link(self):
        with jsdb.connect() as conn:
            entry = CompanyWatchlist(conn).create({
                'company_name': 'Example AG', 'career_url': 'https://example.test/careers'})
        self.assertEqual(entry['source_status'], 'MANUAL')
        self.assertEqual(entry['action'], 'open_careers_page')
        self.assertFalse(entry['automated'])

    def test_a_successful_check_is_persisted(self):
        with jsdb.connect() as conn:
            watch = CompanyWatchlist(conn)
            entry = watch.create({'company_name': 'Example AG',
                                  'career_source_type': 'greenhouse',
                                  'career_source_identifier': 'exampleag'})
            updated = watch.record_check(entry['id'], 'ACTIVE', 34, '34 postings returned.')
        self.assertEqual(updated['source_status'], 'ACTIVE')
        self.assertEqual(updated['job_count_last_scan'], 34)
        self.assertTrue(updated['last_success_at'])
        self.assertEqual(updated['last_error'], '')

    def test_a_failure_keeps_the_last_successful_timestamp(self):
        """'Last successful scan: 4 days ago' has to survive today's failure."""
        with jsdb.connect() as conn:
            watch = CompanyWatchlist(conn)
            entry = watch.create({'company_name': 'Example AG',
                                  'career_source_type': 'greenhouse',
                                  'career_source_identifier': 'exampleag'})
            watch.record_check(entry['id'], 'ACTIVE', 12, 'ok')
            good = watch.get(entry['id'])['last_success_at']
            broken = watch.record_check(entry['id'], 'ERROR', 0, 'HTTP 403')
        self.assertEqual(broken['source_status'], 'ERROR')
        self.assertEqual(broken['last_error'], 'HTTP 403')
        self.assertEqual(broken['last_success_at'], good)
        self.assertEqual(broken['job_count_last_scan'], 0)

    def test_verify_uses_the_registry_and_stores_the_outcome(self):
        with jsdb.connect() as conn:
            watch = CompanyWatchlist(conn)
            entry = watch.create({'company_name': 'Example AG',
                                  'career_source_type': 'greenhouse',
                                  'career_source_identifier': 'exampleag'})
            with mock.patch.object(registry, 'verify', return_value=('ACTIVE', 7, 'ok')):
                checked = watch.verify(entry['id'])
        self.assertEqual(checked['source_status'], 'ACTIVE')
        self.assertEqual(checked['job_count_last_scan'], 7)

    def test_source_health_groups_by_kind_and_lists_failures(self):
        with jsdb.connect() as conn:
            watch = CompanyWatchlist(conn)
            good = watch.create({'company_name': 'Good AG',
                                 'career_source_type': 'greenhouse',
                                 'career_source_identifier': 'good'})
            bad = watch.create({'company_name': 'Bad AG',
                                'career_source_type': 'greenhouse',
                                'career_source_identifier': 'bad'})
            conn.execute("UPDATE job_sources SET last_status='ACTIVE', last_job_count=12, "
                         "last_success_at='2026-09-01T00:00:00' WHERE name=?",
                         ('Watchlist · Good AG',))
            conn.execute("UPDATE job_sources SET last_status='ERROR', last_error='HTTP 403' "
                         'WHERE name=?', ('Watchlist · Bad AG',))
            conn.commit()
            health = source_health(conn)

        greenhouse = next(k for k in health['sources'] if k['source_type'] == 'greenhouse')
        self.assertGreaterEqual(greenhouse['companies'], 2)
        self.assertEqual(greenhouse['status'], 'Partial')
        failures = {f['company']: f for f in health['failures']}
        self.assertIn('Bad AG', failures)
        self.assertEqual(failures['Bad AG']['error'], 'HTTP 403')
        self.assertNotIn('Good AG', failures)
        self.assertEqual(bad['company_name'], 'Bad AG')
        self.assertEqual(good['company_name'], 'Good AG')

    def test_manual_companies_are_counted_but_never_shown_as_a_live_source(self):
        with jsdb.connect() as conn:
            health = source_health(conn)
        manual = next(k for k in health['sources'] if k['source_type'] == 'manual')
        self.assertGreater(manual['companies'], 0)
        self.assertEqual(manual['status'], '-')
        self.assertEqual(manual['jobs_returned'], 0)


class WatchlistMigrationTests(unittest.TestCase):
    """The verified sources are attached once, additively, without data loss."""

    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def entries(self):
        with jsdb.connect() as conn:
            return {e['company_name']: e for e in CompanyWatchlist(conn).list()}

    def test_verified_companies_get_their_source(self):
        entries = self.entries()
        for company, (kind, identifier) in jsdb.VERIFIED_COMPANY_SOURCES.items():
            self.assertEqual(entries[company]['career_source_type'], kind, company)
            self.assertEqual(entries[company]['career_source_identifier'], identifier, company)
            self.assertTrue(entries[company]['automated'], company)

    def test_unverified_companies_stay_manual(self):
        for company in ('Google', 'Microsoft', 'Meta', 'UBS', 'NVIDIA', 'Swisscom'):
            entry = self.entries()[company]
            self.assertEqual(entry['career_source_type'], 'manual', company)
            self.assertEqual(entry['source_status'], 'MANUAL', company)
            self.assertTrue(entry['career_url'], company)

    def test_a_company_source_owns_a_job_sources_row(self):
        with jsdb.connect() as conn:
            names = {r['name'] for r in conn.execute('SELECT name FROM job_sources').fetchall()}
        self.assertIn('Watchlist · Proton', names)
        self.assertIn('Watchlist · Roche', names)
        self.assertNotIn('Watchlist · Google', names)

    def test_the_attachment_runs_once_and_respects_later_edits(self):
        with jsdb.connect() as conn:
            watch = CompanyWatchlist(conn)
            roche = watch.get_by_name('Roche')
            watch.update(roche['id'], {'career_source_type': 'manual',
                                       'career_source_identifier': ''})
        jsdb.init_db()
        jsdb.init_db()
        self.assertEqual(self.entries()['Roche']['career_source_type'], 'manual')

    def test_a_hand_made_source_for_the_same_board_is_adopted_not_duplicated(self):
        """Someone who added "Proton Careers" by hand keeps that row, once."""
        with jsdb.connect() as conn:
            conn.execute('DELETE FROM company_watchlist')
            conn.execute('DELETE FROM job_sources')
            conn.execute("INSERT INTO job_sources (id,name,source_type,config_json,enabled,"
                         "created_at,updated_at) VALUES (4,'Proton Careers','greenhouse',"
                         '\'{"board_token": "proton", "company": "Proton"}\',1,\'\',\'\')')
            conn.execute("DELETE FROM schema_meta WHERE key=?", (jsdb.SEED_MARKER_KEY,))
            conn.commit()
        jsdb.init_db()
        with jsdb.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT id, name FROM job_sources WHERE source_type='greenhouse' "
                "AND config_json LIKE '%proton%'")]
            proton = CompanyWatchlist(conn).get_by_name('Proton')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], 4)                 # the user's original row survives
        self.assertEqual(rows[0]['name'], 'Watchlist \u00b7 Proton')
        self.assertEqual(proton['career_source_type'], 'greenhouse')

    def test_the_merge_leaves_a_clean_database_alone(self):
        with jsdb.connect() as conn:
            before = conn.execute('SELECT COUNT(*) FROM job_sources').fetchone()[0]
        jsdb.init_db()
        with jsdb.connect() as conn:
            after = conn.execute('SELECT COUNT(*) FROM job_sources').fetchone()[0]
        self.assertEqual(before, after)

    def test_the_migration_adds_columns_without_touching_existing_rows(self):
        with jsdb.connect() as conn:
            columns = {r[1] for r in conn.execute('PRAGMA table_info(company_watchlist)')}
            before = conn.execute('SELECT COUNT(*) FROM company_watchlist').fetchone()[0]
        for column in ('source_url', 'source_status', 'last_checked_at', 'last_success_at',
                       'last_error', 'job_count_last_scan'):
            self.assertIn(column, columns)
        jsdb.init_db()
        with jsdb.connect() as conn:
            after = conn.execute('SELECT COUNT(*) FROM company_watchlist').fetchone()[0]
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
