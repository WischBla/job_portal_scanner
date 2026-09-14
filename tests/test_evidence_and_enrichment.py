"""Missing evidence is not negative evidence.

The defect these tests pin down had one shape in four places:

* a job with no description was scored as though the description had said
  nothing good about it, so a *Head of SRE* known only from a LinkedIn alert
  was ranked as a poor fit rather than as an unknown one;
* the hands-on-IC classifier fired on a single occurrence of a language name,
  so a role was called feature engineering on the strength of the word
  "Python";
* the Jobs list hid everything below a score threshold, which meant those
  same unknown roles disappeared entirely;
* and a job vanished from one scan was retired as EXPIRED, so a rate-limited
  source could quietly retire a company's whole board.

Each class below covers one of those, plus the enrichment path that is meant
to turn an unknown job into a known one.  The assertions are behavioural on
purpose ("a leadership title with no description keeps its score off the
floor", "a failed enrichment changes nothing") so the numbers can be
recalibrated without rewriting the suite.
"""

import unittest

from jobscanner import alert_import, db as jsdb, enrichment, evidence, fit, jobs_service
from jobscanner.repository import JobRepository
from jobscanner.scoring import MatchScorer
from tests.helpers import STRONG_DESCRIPTION, TempDatabase, make_job, make_profile

# --------------------------------------------------------------------------
# fixtures: the four calibration cases from the specification
# --------------------------------------------------------------------------
HANDS_ON_SRE = (
    'You will write Python and Go every day to build and maintain our observability '
    'stack. Operate infrastructure across our Kubernetes estate and take part in the '
    'on call rotation. This is a hands on production engineering role: you will debug '
    'production systems, implement services and ship improvements yourself.'
)
LEADERSHIP_SRE = (
    'You will lead the SRE organization of around thirty engineers. Define the '
    'reliability strategy for the group, manage engineering teams and own the SLA '
    'ownership model across multiple teams. You are accountable for the platform '
    'roadmap, for hiring and for the budget of the function. You will establish the '
    'operating model for incident management and drive continuous improvement across '
    'the engineering organisation.'
)
IC_REALITY_SRE = (
    'You will have no direct reports. This is a daily coding role: you will write '
    'Python, implement services for the platform, run your own individual on call and '
    'debug production systems. Implementation is the primary responsibility; you will '
    'operate infrastructure and build tooling yourself.'
)
THIN_POSTING = 'Rust. Zurich. Apply now.'


def assess(title, description=''):
    job = {'title': title, 'description': description}
    report = evidence.assess(job)
    style, direction = fit.assess(job, (), report)
    return report, style, direction


# --------------------------------------------------------------------------
# 1. evidence
# --------------------------------------------------------------------------
class EvidenceLevelTests(unittest.TestCase):
    def test_a_job_with_no_description_is_low_evidence(self):
        report = evidence.assess({'title': 'Head of SRE', 'description': ''})
        self.assertEqual(report['level'], evidence.LOW)
        self.assertEqual(report['state'], evidence.NEEDS_ENRICHMENT)
        self.assertTrue(report['provisional'])

    def test_a_full_description_is_high_evidence(self):
        report = evidence.assess({'title': 'Head of SRE',
                                  'description': LEADERSHIP_SRE + ' ' + STRONG_DESCRIPTION})
        self.assertEqual(report['level'], evidence.HIGH)
        self.assertEqual(report['state'], evidence.ENRICHED)
        self.assertFalse(report['provisional'])

    def test_a_thin_posting_lowers_confidence(self):
        """Even a direct-source posting can be too short to judge."""
        report = evidence.assess({'title': 'Software Senior Technical Lead Rust',
                                  'description': THIN_POSTING})
        self.assertIn(report['level'], (evidence.LOW, evidence.MEDIUM))
        self.assertNotEqual(report['level'], evidence.HIGH)

    def test_an_alert_teaser_is_not_a_description(self):
        """``excerpt`` holds the alert's informational line, not the job."""
        report = evidence.assess({
            'title': 'Head of SRE', 'description': '',
            'excerpt': 'Dieses Unternehmen ist aktiv auf Personalsuche.'})
        self.assertEqual(report['level'], evidence.LOW)

    def test_a_leadership_title_without_a_description_is_high_potential(self):
        for title in ('Head of SRE', 'Head of Platform Engineering', 'Head of Reliability',
                      'Director Technology Operations', 'Engineering Manager SRE',
                      'Principal Technical Program Manager'):
            report = evidence.assess({'title': title, 'description': ''})
            self.assertTrue(report['high_potential'], title)
            self.assertEqual(report['classification'], evidence.HIGH_POTENTIAL, title)

    def test_a_leadership_title_outside_the_domain_is_not_high_potential(self):
        """The title is a clue about *relevance*, not a magic word."""
        for title in ('Head of Sales', 'Director of Finance', 'Head of People'):
            self.assertFalse(evidence.title_is_high_potential(title), title)

    def test_high_potential_only_applies_while_evidence_is_missing(self):
        report = evidence.assess({'title': 'Head of SRE', 'description': LEADERSHIP_SRE})
        self.assertFalse(report['high_potential'])


# --------------------------------------------------------------------------
# 2. scoring with insufficient evidence
# --------------------------------------------------------------------------
class LowEvidenceScoringTests(unittest.TestCase):
    def test_low_evidence_produces_no_negative_adjustment(self):
        _report, style, direction = assess('Head of SRE', '')
        self.assertEqual(style['adjustment'], 0.0)
        self.assertEqual(direction['adjustment'], 0.0)

    def test_low_evidence_is_never_read_as_a_bad_shape(self):
        """The five readings that must not be inferred from silence."""
        _report, style, direction = assess('Head of SRE', '')
        forbidden = ('PURE_SENIOR_IC', 'PURE_FEATURE_ENGINEERING', 'CONSULTING_DELIVERY',
                     'STAKEHOLDER_HEAVY', 'POLITICAL_EXTERNAL',
                     'ACCOUNT_ENGAGEMENT_MANAGEMENT')
        self.assertNotIn(direction['classification'], forbidden)
        self.assertNotIn(style['classification'], forbidden)

    def test_the_personal_fit_is_marked_provisional(self):
        result = MatchScorer().score(make_job(title='Head of SRE', description=''),
                                     make_profile())
        self.assertTrue(result['evidence']['provisional'])
        self.assertEqual(result['evidence']['confidence'], evidence.LOW)

    def test_a_title_only_head_of_sre_is_not_downgraded_by_its_own_silence(self):
        """The specification's case B, as a number.

        The same title with a real leadership description is the ceiling. A
        job we know nothing about may score lower than one we know is good -
        it must not score lower than one we know is *bad*.
        """
        profile = make_profile()
        blind = MatchScorer().score(make_job(title='Head of SRE', description=''), profile)
        known_ic = MatchScorer().score(
            make_job(title='Head of SRE', description=IC_REALITY_SRE), profile)
        self.assertEqual(blind['operating_style']['adjustment'], 0.0)
        self.assertEqual(blind['career_direction']['adjustment'], 0.0)
        self.assertLess(known_ic['career_direction']['adjustment'], 0.0)

    def test_absence_is_not_reported_as_a_finding(self):
        """The concerns list must not read like a verdict either."""
        result = MatchScorer().score(make_job(title='Head of SRE', description=''),
                                     make_profile())
        joined = ' '.join(result['concerns']).lower()
        self.assertIn('no job description stored yet', joined)
        self.assertNotIn('no leadership or transformation responsibilities', joined)
        self.assertNotIn('no technology keywords', joined)


# --------------------------------------------------------------------------
# 3. evidence-based hands-on IC detection
# --------------------------------------------------------------------------
class HandsOnDetectionTests(unittest.TestCase):
    def test_a_hands_on_sre_description_is_penalised(self):
        """Case A: the responsibilities really are implementation."""
        _report, _style, direction = assess('Site Reliability Engineer - Observability',
                                            HANDS_ON_SRE)
        self.assertIn(direction['classification'],
                      ('PURE_SENIOR_IC', 'PURE_FEATURE_ENGINEERING'))
        self.assertLess(direction['adjustment'], 0.0)

    def test_a_leadership_sre_description_is_not(self):
        """Case C: same domain, opposite shape."""
        _report, _style, direction = assess('Head of SRE', LEADERSHIP_SRE)
        self.assertEqual(direction['adjustment'], 0.0)

    def test_responsibilities_beat_the_title(self):
        """Case D: a leadership title over an IC job is an IC job."""
        _report, _style, direction = assess('Head of SRE', IC_REALITY_SRE)
        self.assertLess(direction['adjustment'], 0.0)

    def test_one_language_mention_is_not_evidence(self):
        for term in ('Python', 'Go', 'Kubernetes', 'Terraform', 'hands-on', 'engineering'):
            description = ('You will own the reliability of our platform and define the '
                           'technical roadmap. Experience with {0} is useful. You will '
                           'lead a team and manage engineering teams.'.format(term))
            _report, _style, direction = assess('Head of Platform Engineering', description)
            self.assertEqual(direction['adjustment'], 0.0, term)

    def test_an_ic_classification_needs_several_distinct_signals(self):
        single = ('You will occasionally write code. You will own the platform roadmap '
                  'and define the reliability strategy for multiple teams.')
        _report, _style, direction = assess('Head of SRE', single)
        self.assertEqual(direction['adjustment'], 0.0)

    def test_a_thin_posting_is_not_forced_into_a_harsh_classification(self):
        """Specification case 14: uncertain, not PURE_FEATURE_ENGINEERING."""
        _report, _style, direction = assess('Software Senior Technical Lead Rust',
                                            THIN_POSTING)
        self.assertNotEqual(direction['classification'], 'PURE_FEATURE_ENGINEERING')
        self.assertEqual(direction['adjustment'], 0.0)

    def test_leadership_evidence_protects_against_a_false_positive(self):
        """Implementation words inside a real leadership mandate."""
        mixed = (HANDS_ON_SRE + ' ' + LEADERSHIP_SRE)
        _report, _style, direction = assess('Head of SRE', mixed)
        self.assertEqual(direction['adjustment'], 0.0)


# --------------------------------------------------------------------------
# 4. visibility
# --------------------------------------------------------------------------
class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def seed(self, title, score, external_id, description=''):
        job = make_job(title=title, description=description, external_id=external_id,
                       location='Zurich, Switzerland')
        scored = {'score': score, 'label': jobs_service.classify(score), 'reasons': [],
                  'concerns': [], 'terms': [], 'breakdown': []}
        with jsdb.connect() as conn:
            job_id, _ = JobRepository(conn).upsert(job, scored)
            conn.commit()
        return job_id

    def test_a_job_below_sixty_five_is_in_the_default_list(self):
        low = self.seed('Cloud Operations Lead', 41, '1')
        high = self.seed('Head of Platform Engineering', 88, '2', STRONG_DESCRIPTION)
        with jsdb.connect() as conn:
            ids = [card['id'] for card in jobs_service.list_cards(conn)]
        self.assertIn(low, ids)
        self.assertIn(high, ids)

    def test_every_band_has_its_own_view_and_all_active_contains_them_all(self):
        self.seed('Head of Platform Engineering', 88, 'a', STRONG_DESCRIPTION)
        self.seed('Head of SRE', 78, 'b', STRONG_DESCRIPTION)
        self.seed('Head of Cloud', 68, 'c', STRONG_DESCRIPTION)
        self.seed('Cloud Lead', 58, 'd', STRONG_DESCRIPTION)
        self.seed('Operations Lead', 20, 'e')
        with jsdb.connect() as conn:
            everything = jobs_service.list_cards(conn)
            bands = {key: jobs_service.list_cards(conn, view=key)
                     for key in ('top', 'review', 'edge', 'low')}
        self.assertEqual(len(everything), 5)
        self.assertEqual(sum(len(v) for v in bands.values()), 5)

    def test_the_needs_enrichment_view_selects_exactly_the_unknown_jobs(self):
        unknown = self.seed('Head of SRE', 55, '1')
        self.seed('Head of Platform Engineering', 88, '2', STRONG_DESCRIPTION)
        with jsdb.connect() as conn:
            cards = jobs_service.list_cards(conn, view='enrich')
        self.assertEqual([card['id'] for card in cards], [unknown])

    def test_the_counts_report_what_is_known_not_only_what_scored(self):
        self.seed('Head of SRE', 55, '1')
        self.seed('Head of Platform Engineering', 88, '2', STRONG_DESCRIPTION)
        with jsdb.connect() as conn:
            counts = jobs_service.counts(conn)
        self.assertEqual(counts['total'], 2)
        self.assertEqual(counts['needs_enrichment'], 1)
        self.assertEqual(counts['high_potential'], 1)
        self.assertEqual(counts['filters']['all'], 2)

    def test_a_low_scoring_job_keeps_its_state_and_its_row(self):
        """Visibility is not lifecycle: a bad score is not a deletion."""
        job_id = self.seed('Operations Lead', 12, '1')
        with jsdb.connect() as conn:
            row = conn.execute('SELECT state FROM discovered_jobs WHERE id=?',
                               (job_id,)).fetchone()
        self.assertEqual(row[0], 'NEW')


# --------------------------------------------------------------------------
# 5. enrichment
# --------------------------------------------------------------------------
GREENHOUSE_BOARD = [{
    'title': 'Head of SRE', 'location': 'Zurich, Switzerland',
    'job_url': 'https://boards.greenhouse.io/example/jobs/999',
    'description': LEADERSHIP_SRE,
}]


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def seed(self, title='Head of SRE', description='', external_id='1',
             company='Example AG', url=None, source='LinkedIn alert',
             source_type='linkedin'):
        job = make_job(title=title, description=description, external_id=external_id,
                       company=company, location='Zurich, Switzerland', url=url,
                       source=source, source_type=source_type)
        scored = MatchScorer().score(job, make_profile())
        with jsdb.connect() as conn:
            job_id, _ = JobRepository(conn).upsert(job, scored)
            conn.commit()
        return job_id

    def row(self, job_id):
        with jsdb.connect() as conn:
            return dict(conn.execute('SELECT * FROM discovered_jobs WHERE id=?',
                                     (job_id,)).fetchone())

    # -- step 1: the local database ---------------------------------------
    def test_a_duplicate_already_in_the_database_is_reused(self):
        self.seed(external_id='canonical', description=LEADERSHIP_SRE,
                  source='Watchlist - Example AG', source_type='greenhouse',
                  url='https://boards.greenhouse.io/example/jobs/999')
        thin = self.seed(external_id='alert', url='https://www.linkedin.com/jobs/view/1234')
        with jsdb.connect() as conn:
            result = enrichment.enrich_job(thin, conn)
        self.assertEqual(result['status'], enrichment.ENRICHED)
        self.assertEqual(result['source'], enrichment.FROM_LOCAL_DUPLICATE)
        self.assertEqual(self.row(thin)['description'], LEADERSHIP_SRE)

    def test_reuse_never_matches_a_different_role(self):
        self.seed(title='Head of Security', external_id='other',
                  description=LEADERSHIP_SRE, source_type='greenhouse')
        thin = self.seed(title='Head of SRE', external_id='alert',
                         url='https://www.linkedin.com/jobs/view/1234')
        with jsdb.connect() as conn:
            result = enrichment.enrich_job(thin, conn)
        self.assertEqual(result['status'], enrichment.NOT_FOUND)

    # -- step 2: the company's own source ---------------------------------
    def activate_company_source(self):
        from jobscanner.watchlist import CompanyWatchlist
        with jsdb.connect() as conn:
            conn.execute('DELETE FROM company_watchlist')
            watch = CompanyWatchlist(conn)
            entry = watch.create({'company_name': 'Example AG',
                                  'career_source_type': 'greenhouse',
                                  'career_source_identifier': 'example'})
            watch.record_check(entry['id'], 'ACTIVE', 1, 'ok')
            conn.commit()

    def test_a_direct_company_source_supplies_the_description(self):
        self.activate_company_source()
        thin = self.seed(url='https://www.linkedin.com/jobs/view/1234')
        with jsdb.connect() as conn:
            session = enrichment.Session(conn, fetcher=lambda source: GREENHOUSE_BOARD)
            result = enrichment.enrich_job(thin, conn, session)
        self.assertEqual(result['status'], enrichment.ENRICHED)
        self.assertEqual(result['source'], enrichment.FROM_COMPANY_SOURCE)
        stored = self.row(thin)
        self.assertEqual(stored['description'], LEADERSHIP_SRE)
        self.assertNotEqual(stored['enrichment_state'], evidence.NEEDS_ENRICHMENT)
        # the canonical application URL is adopted; LinkedIn stays provenance
        self.assertEqual(stored['job_url'], 'https://boards.greenhouse.io/example/jobs/999')

    def test_an_unproven_company_source_is_not_asked(self):
        """A claimed endpoint is not a verified one."""
        from jobscanner.watchlist import CompanyWatchlist
        with jsdb.connect() as conn:
            conn.execute('DELETE FROM company_watchlist')
            CompanyWatchlist(conn).create({'company_name': 'Example AG',
                                           'career_source_type': 'greenhouse',
                                           'career_source_identifier': 'example'})
            conn.commit()
        thin = self.seed(url='https://www.linkedin.com/jobs/view/1234')
        asked = []
        with jsdb.connect() as conn:
            session = enrichment.Session(
                conn, fetcher=lambda source: asked.append(source) or GREENHOUSE_BOARD)
            enrichment.enrich_job(thin, conn, session)
        self.assertEqual(asked, [])

    def test_a_company_source_match_still_requires_the_same_role(self):
        self.activate_company_source()
        thin = self.seed(title='Head of Data Platform',
                         url='https://www.linkedin.com/jobs/view/1234')
        with jsdb.connect() as conn:
            session = enrichment.Session(conn, fetcher=lambda source: GREENHOUSE_BOARD)
            result = enrichment.enrich_job(thin, conn, session)
        self.assertEqual(result['status'], enrichment.NOT_FOUND)

    # -- step 3: the public original posting ------------------------------
    def test_a_public_posting_is_read_when_the_job_has_a_canonical_url(self):
        thin = self.seed(url='https://careers.example.test/jobs/42')
        with jsdb.connect() as conn:
            session = enrichment.Session(
                conn, fetcher=lambda source: [],
                page_fetcher=lambda url, company='': LEADERSHIP_SRE)
            result = enrichment.enrich_job(thin, conn, session)
        self.assertEqual(result['status'], enrichment.ENRICHED)
        self.assertEqual(result['source'], enrichment.FROM_PUBLIC_POSTING)

    def test_linkedin_is_never_fetched(self):
        """The one URL the enricher must refuse to open."""
        thin = self.seed(url='https://www.linkedin.com/jobs/view/1234')
        fetched = []
        with jsdb.connect() as conn:
            session = enrichment.Session(
                conn, fetcher=lambda source: [],
                page_fetcher=lambda url, company='': fetched.append(url) or LEADERSHIP_SRE)
            enrichment.enrich_job(thin, conn, session)
        self.assertEqual(fetched, [])

    # -- step 4: failure ---------------------------------------------------
    def test_a_failed_enrichment_leaves_the_job_intact(self):
        thin = self.seed(url='https://www.linkedin.com/jobs/view/1234')
        before = self.row(thin)
        with jsdb.connect() as conn:
            session = enrichment.Session(conn, fetcher=lambda source: [],
                                         page_fetcher=lambda url, company='': '')
            result = enrichment.enrich_job(thin, conn, session)
        after = self.row(thin)
        self.assertEqual(result['status'], enrichment.NOT_FOUND)
        self.assertEqual(result['detail'], enrichment.NOT_FOUND_MESSAGE)
        for column in ('title', 'company', 'description', 'state', 'match_score', 'job_url'):
            self.assertEqual(after[column], before[column], column)
        self.assertEqual(after['enrichment_state'], evidence.NEEDS_ENRICHMENT)

    def test_a_stub_is_not_accepted_as_a_description(self):
        """A board that answers with a non-breaking space has found nothing.

        Observed live: a Greenhouse board returned a one-character ``content``
        field, enrichment reported success, and the job was left exactly as
        unknown as before - a false success is worse than an honest failure.
        """
        thin = self.seed(url='https://careers.example.test/jobs/42')
        with jsdb.connect() as conn:
            session = enrichment.Session(
                conn, fetcher=lambda source: [],
                page_fetcher=lambda url, company='': '\u00a0')
            result = enrichment.enrich_job(thin, conn, session)
        self.assertEqual(result['status'], enrichment.NOT_FOUND)
        self.assertEqual(self.row(thin)['description'], '')

    def test_a_rescan_never_undoes_enrichment(self):
        """The description a scan brings back is often poorer than the one we
        went and found. Refreshing a job must not make it poorer."""
        from jobscanner.repository import JobRepository
        job_id = self.seed(external_id='1', url='https://careers.example.test/jobs/42')
        with jsdb.connect() as conn:
            session = enrichment.Session(
                conn, fetcher=lambda source: [],
                page_fetcher=lambda url, company='': LEADERSHIP_SRE)
            enrichment.enrich_job(job_id, conn, session)
        enriched = self.row(job_id)

        stub = make_job(title='Head of SRE', company='Example AG',
                        location='Zurich, Switzerland', description='\u00a0',
                        external_id='1', source='LinkedIn alert', source_type='linkedin',
                        url='https://careers.example.test/jobs/42')
        with jsdb.connect() as conn:
            JobRepository(conn).upsert(stub, MatchScorer().score(stub, make_profile()))
            conn.commit()
        after = self.row(job_id)
        self.assertEqual(after['description'], LEADERSHIP_SRE)
        self.assertEqual(after['match_score'], enriched['match_score'])
        self.assertNotEqual(after['enrichment_state'], evidence.NEEDS_ENRICHMENT)

    def test_a_source_that_raises_does_not_damage_the_job(self):
        thin = self.seed(url='https://careers.example.test/jobs/42')
        def explode(url, company=''):
            raise RuntimeError('careers page is down')
        with jsdb.connect() as conn:
            session = enrichment.Session(conn, fetcher=lambda source: [],
                                         page_fetcher=explode)
            result = enrichment.enrich_job(thin, conn, session)
        self.assertEqual(result['status'], enrichment.NOT_FOUND)
        self.assertEqual(self.row(thin)['state'], 'NEW')

    # -- rescoring ---------------------------------------------------------
    def test_an_enriched_job_is_rescored_by_the_normal_pipeline(self):
        thin = self.seed()
        before = self.row(thin)
        with jsdb.connect() as conn:
            session = enrichment.Session(
                conn, fetcher=lambda source: [],
                page_fetcher=lambda url, company='': LEADERSHIP_SRE)
            enrichment.enrich_job(thin, conn, session)
        after = self.row(thin)
        self.assertGreater(after['match_score'], before['match_score'])
        self.assertEqual(after['fit_provisional'], 0)
        self.assertNotEqual(after['fit_confidence'], evidence.LOW)


# --------------------------------------------------------------------------
# 6. the import path end to end
# --------------------------------------------------------------------------
ALERT = '''Head of SRE
Example AG
Zürich, Zürich, Schweiz

Jobangebot ansehen: https://www.linkedin.com/comm/jobs/view/4400000001?trk=eml
'''


class ImportEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def test_the_preview_says_what_each_entry_is_worth(self):
        entries = alert_import.parse_alert(text=ALERT)
        with jsdb.connect() as conn:
            preview = alert_import.preview(entries, conn)
        entry = preview['jobs'][0]
        self.assertEqual(entry['status'], alert_import.NEW)
        self.assertEqual(entry['enrichment_state'], evidence.NEEDS_ENRICHMENT)
        self.assertEqual(entry['evidence'], evidence.LOW)
        self.assertTrue(entry['high_potential'])
        self.assertEqual(preview['needs_enrichment'], 1)

    def test_the_preview_says_when_an_entry_is_immediately_useful(self):
        job = make_job(title='Head of SRE', company='Example AG',
                       location='Zurich, Switzerland', description=LEADERSHIP_SRE,
                       external_id='7788', source='Greenhouse', source_type='greenhouse')
        with jsdb.connect() as conn:
            JobRepository(conn).upsert(job, MatchScorer().score(job, make_profile()))
            conn.commit()
            preview = alert_import.preview(alert_import.parse_alert(text=ALERT), conn)
        entry = preview['jobs'][0]
        self.assertEqual(entry['status'], alert_import.KNOWN)
        self.assertNotEqual(entry['enrichment_state'], evidence.NEEDS_ENRICHMENT)
        self.assertIn('full description', entry['enrichment_note'])

    def test_an_import_attempts_enrichment_exactly_once(self):
        entries = alert_import.parse_alert(text=ALERT)
        calls = []
        with jsdb.connect() as conn:
            preview = alert_import.preview(entries, conn)
            session = enrichment.Session(
                conn, fetcher=lambda source: [],
                page_fetcher=lambda url, company='': calls.append(url) or '')
            result = alert_import.import_entries(preview['jobs'], conn, session=session)
        self.assertEqual(result['imported'], 1)
        self.assertEqual(result['enrichment']['attempted'], 1)
        self.assertEqual(result['enrichment']['not_found'], 1)
        self.assertEqual(calls, [])          # the only URL was LinkedIn's

    def test_an_import_that_finds_a_description_scores_it_properly(self):
        entries = alert_import.parse_alert(text=ALERT)
        canonical = make_job(title='Head of SRE', company='Example AG',
                             location='Zurich, Switzerland', description=LEADERSHIP_SRE,
                             external_id='gh-1', source='Greenhouse',
                             source_type='greenhouse',
                             url='https://boards.greenhouse.io/example/jobs/1')
        with jsdb.connect() as conn:
            # The canonical job exists but under a different identity, so the
            # alert entry is genuinely new and enrichment has to find it.
            conn.execute('UPDATE discovered_jobs SET title=?', ('placeholder',))
            JobRepository(conn).upsert(canonical, MatchScorer().score(canonical, make_profile()))
            conn.execute("UPDATE discovered_jobs SET company='Example AG (Group)' "
                         "WHERE source_type='greenhouse'")
            conn.commit()
            preview = alert_import.preview(entries, conn)
            session = enrichment.Session(
                conn, fetcher=lambda source: [],
                page_fetcher=lambda url, company='': '')
            alert_import.import_entries(preview['jobs'], conn, session=session)
            imported = dict(conn.execute(
                "SELECT * FROM discovered_jobs WHERE discovered_via='linkedin'").fetchone())
        # Nothing was found, so nothing was invented - and it stays visible.
        self.assertEqual(imported['enrichment_state'], evidence.NEEDS_ENRICHMENT)
        self.assertEqual(imported['state'], 'NEW')

    def test_an_imported_job_is_visible_and_marked_provisional(self):
        entries = alert_import.parse_alert(text=ALERT)
        with jsdb.connect() as conn:
            preview = alert_import.preview(entries, conn)
            session = enrichment.Session(conn, fetcher=lambda source: [],
                                         page_fetcher=lambda url, company='': '')
            alert_import.import_entries(preview['jobs'], conn, session=session)
            cards = jobs_service.list_cards(conn)
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertTrue(card['provisional'])
        self.assertTrue(card['high_potential'])
        self.assertEqual(card['confidence'], evidence.LOW)
        self.assertEqual(card['enrichment_state'], evidence.NEEDS_ENRICHMENT)


if __name__ == '__main__':
    unittest.main()
