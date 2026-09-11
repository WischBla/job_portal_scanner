"""Company watchlist: seeding, priorities and the job_sources bridge."""

import json
import unittest

from jobscanner import db as jsdb
from jobscanner.watchlist import CompanyWatchlist
from tests.helpers import TempDatabase

PRIORITY_A = {
    'Google', 'Microsoft', 'Amazon Web Services / AWS', 'NVIDIA', 'UBS', 'SIX',
    'Swiss Re', 'Roche', 'Novartis', 'ABB', 'Swisscom',
}


class WatchlistSeedTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def entries(self):
        with jsdb.connect() as conn:
            return CompanyWatchlist(conn).list()

    def test_the_watchlist_is_seeded(self):
        names = {e['company_name'] for e in self.entries()}
        for company in ['Google', 'Microsoft', 'Meta', 'IBM', 'Red Hat', 'UBS', 'SIX',
                        'Swiss Re', 'Zurich Insurance', 'Swisscom', 'PostFinance', 'Roche',
                        'Novartis', 'ABB', 'Hitachi Energy', 'Siemens Switzerland',
                        'Adnovum', 'Avaloq', 'Scandit', 'Proton']:
            self.assertIn(company, names, company)

    def test_priority_a_companies_are_marked_a(self):
        by_name = {e['company_name']: e for e in self.entries()}
        for company in PRIORITY_A:
            self.assertEqual(by_name[company]['priority'], 'A', company)

    def test_every_other_seeded_company_is_priority_b(self):
        for entry in self.entries():
            if entry['company_name'] not in PRIORITY_A:
                self.assertEqual(entry['priority'], 'B', entry['company_name'])

    def test_every_entry_carries_the_fields_the_brief_asks_for(self):
        for entry in self.entries():
            for field in ('company_name', 'enabled', 'priority', 'career_source_type',
                          'career_source_identifier', 'career_url', 'last_scan_at',
                          'last_scan_status'):
                self.assertIn(field, entry, field)

    def test_companies_without_a_machine_readable_source_only_offer_a_link(self):
        """No brittle scraper is created just to support a company."""
        for entry in self.entries():
            if entry['career_source_type'] == 'manual':
                self.assertEqual(entry['action'], 'open_careers_page')
                self.assertTrue(entry['career_url'], entry['company_name'])
                self.assertFalse(entry['automated'])

    def test_seeding_is_idempotent_and_keeps_user_edits(self):
        with jsdb.connect() as conn:
            watchlist = CompanyWatchlist(conn)
            entry = next(e for e in watchlist.list() if e['company_name'] == 'Proton')
            watchlist.update(entry['id'], {'priority': 'A', 'notes': 'mine'})
        jsdb.init_db()
        jsdb.init_db()
        by_name = {e['company_name']: e for e in self.entries()}
        self.assertEqual(by_name['Proton']['priority'], 'A')
        self.assertEqual(by_name['Proton']['notes'], 'mine')
        self.assertEqual(len([e for e in self.entries() if e['company_name'] == 'Proton']), 1)


class WatchlistSourceBridgeTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def sources(self):
        with jsdb.connect() as conn:
            return {r['name']: dict(r) for r in
                    conn.execute('SELECT * FROM job_sources').fetchall()}

    def test_a_manual_entry_creates_no_scanner_source(self):
        with jsdb.connect() as conn:
            CompanyWatchlist(conn).create({
                'company_name': 'Example AG', 'career_source_type': 'manual',
                'career_url': 'https://example.test/careers'})
        self.assertNotIn('Watchlist · Example AG', self.sources())

    def test_attaching_a_public_board_creates_a_real_source(self):
        with jsdb.connect() as conn:
            entry = CompanyWatchlist(conn).create({
                'company_name': 'Example AG', 'career_source_type': 'greenhouse',
                'career_source_identifier': 'exampleag'})
        source = self.sources()['Watchlist · Example AG']
        self.assertEqual(source['source_type'], 'greenhouse')
        self.assertEqual(json.loads(source['config_json'])['board_token'], 'exampleag')
        self.assertTrue(entry['automated'])

    def test_detaching_the_board_removes_the_source_again(self):
        with jsdb.connect() as conn:
            watchlist = CompanyWatchlist(conn)
            entry = watchlist.create({'company_name': 'Example AG',
                                      'career_source_type': 'lever',
                                      'career_source_identifier': 'exampleag'})
            self.assertIn('Watchlist · Example AG', self.sources())
            watchlist.update(entry['id'], {'career_source_type': 'manual',
                                           'career_source_identifier': ''})
        self.assertNotIn('Watchlist · Example AG', self.sources())

    def test_deleting_an_entry_removes_its_source(self):
        with jsdb.connect() as conn:
            watchlist = CompanyWatchlist(conn)
            entry = watchlist.create({'company_name': 'Example AG',
                                      'career_source_type': 'rss',
                                      'career_source_identifier': 'https://example.test/jobs.xml'})
            watchlist.delete(entry['id'])
        self.assertNotIn('Watchlist · Example AG', self.sources())

    def test_a_public_source_needs_an_identifier(self):
        with jsdb.connect() as conn:
            with self.assertRaises(ValueError):
                CompanyWatchlist(conn).create({'company_name': 'Example AG',
                                               'career_source_type': 'greenhouse'})

    def test_a_company_cannot_be_added_twice(self):
        with jsdb.connect() as conn:
            watchlist = CompanyWatchlist(conn)
            watchlist.create({'company_name': 'Example AG'})
            with self.assertRaises(ValueError):
                watchlist.create({'company_name': 'example ag'})


if __name__ == '__main__':
    unittest.main()
