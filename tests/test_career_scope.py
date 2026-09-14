"""Career scope: would this role realistically be applied for?

The defect these tests pin down: the Jobs list was correct and unusable at the
same time.  Every active Swiss job was there - which is exactly right, and
which meant the first screen was full of *Senior Backend Engineer*,
*Site Reliability Engineer - Observability* and *Staff Software Engineer*.
Those are relevant jobs for this technical domain and they are not jobs this
career direction applies for, so a fifth, explicit question was needed.

Four properties hold across every test below, and each has its own case:

* the verdict is read from the **responsibilities**, so a *Head of SRE* that
  describes daily coding is out of scope and a *Site Reliability Engineer*
  that describes owning an organisation is not;
* **missing evidence is not negative evidence**, so a thin posting is
  UNCERTAIN and stays in the default list;
* the **user's own decision wins**: a saved or tracked job is visible whatever
  the classifier now says about it;
* and career scope is **not a lifecycle and not a score** - nothing here
  expires a job, deletes one, or moves a single point.
"""

import unittest

from jobscanner import career_scope, db as jsdb, jobs_service
from jobscanner import applications as applications_mod
from jobscanner.repository import JobRepository
from jobscanner.scoring import MatchScorer
from tests.helpers import TempDatabase, make_job, make_profile

# --------------------------------------------------------------------------
# fixtures - the eight acceptance cases from the specification
# --------------------------------------------------------------------------
HEAD_OF_SRE_LEADERSHIP = (
    'You will lead a team of site reliability engineers and own the SRE strategy for the '
    'group. SLO ownership across the platform, the platform roadmap and organisational '
    'improvement are yours. You will manage a team, define the operating model for incident '
    'management and drive continuous improvement across the engineering organisation.'
)
SRE_OBSERVABILITY_IC = (
    'You will write Python and Go, build observability tooling, operate infrastructure and '
    'debug production systems. You will implement observability pipelines and take part in '
    'the on call rotation for our Kubernetes estate.'
)
HEAD_OF_SRE_IC_REALITY = (
    'You will have no direct reports. This is a daily coding role: you will write Python, '
    'implement services for the platform, run your own individual on call and debug '
    'production systems. Implementation is the primary responsibility; you will operate '
    'infrastructure and build tooling yourself.'
)
ENGINEERING_MANAGER_PLATFORM = (
    'You will lead a team of platform engineers, take platform ownership of our cloud estate, '
    'own the platform roadmap and drive operational excellence. Hiring, headcount and '
    'performance reviews are part of the role.'
)
SENIOR_BACKEND_ENGINEER = (
    'You will implement backend services, write production code and own feature delivery for '
    'our core product. You will develop APIs and build microservices in Go.'
)
PRINCIPAL_TPM = (
    'You own the technical roadmap and the technical portfolio. You will run cross team '
    'programs, manage risks and dependencies across teams and report to executive '
    'stakeholders on progress.'
)
PRINCIPAL_SOFTWARE_ENGINEER = (
    'You will define the architecture and implement features yourself. Heavy implementation: '
    'write production code, build core services and develop APIs daily.'
)
ENTERPRISE_ARCHITECT = (
    'You own the enterprise technology strategy, define organizational standards, technology '
    'roadmaps and architecture governance across the organization. Cross organization '
    'architecture is your mandate and you provide executive decision support.'
)
SOFTWARE_ARCHITECT_CODING = (
    'Daily implementation is the core of this role. You will do Java coding, service '
    'development and write production code with the team. Hands on software development in a '
    'product squad.'
)
#: A leader who reviews code.  Specification 8: that alone is not an IC role.
HEAD_OF_TECH_OPS = (
    'You will lead the technology operations organisation of four teams, own the engineering '
    'operating model, the technical roadmap and the budget. A good understanding of Python '
    'and Kubernetes helps you review designs and challenge your architects.'
)


def scope_of(title, description=''):
    return career_scope.classify({'title': title, 'description': description})


# --------------------------------------------------------------------------
# 1. the classifier
# --------------------------------------------------------------------------
class AcceptanceCaseTests(unittest.TestCase):
    """The eight worked cases the specification states, by name."""

    def assertScope(self, expected, title, description='', message=''):
        verdict = scope_of(title, description)
        self.assertEqual(expected, verdict['scope'],
                         '{0}: {1} -> {2} ({3})'.format(message or title, expected,
                                                        verdict['scope'], verdict['detail']))
        # Every verdict explains itself; an exclusion nobody can read back is
        # an exclusion nobody can correct.
        self.assertTrue(verdict['reason'])
        self.assertTrue(verdict['detail'])
        return verdict

    def test_case_a_head_of_sre_with_leadership_responsibilities(self):
        self.assertScope(career_scope.IN_SCOPE, 'Head of SRE', HEAD_OF_SRE_LEADERSHIP)

    def test_case_b_site_reliability_engineer_observability(self):
        verdict = self.assertScope(career_scope.OUT_OF_SCOPE,
                                   'Site Reliability Engineer - Observability',
                                   SRE_OBSERVABILITY_IC)
        self.assertEqual(career_scope.REASON_SRE_IC, verdict['reason'])

    def test_case_c_engineering_manager_cloud_platform_and_operations(self):
        self.assertScope(career_scope.IN_SCOPE,
                         'Engineering Manager Cloud Platform & Operations',
                         ENGINEERING_MANAGER_PLATFORM)

    def test_case_d_senior_backend_engineer_technical_lead(self):
        """"Technical Lead" next to "Backend Engineer" does not make it a lead role."""
        verdict = self.assertScope(career_scope.OUT_OF_SCOPE,
                                   'Senior Backend Engineer / Technical Lead',
                                   SENIOR_BACKEND_ENGINEER)
        self.assertEqual(career_scope.REASON_SOFTWARE_IC, verdict['reason'])

    def test_case_e_principal_technical_program_manager(self):
        self.assertScope(career_scope.IN_SCOPE, 'Principal Technical Program Manager',
                         PRINCIPAL_TPM)

    def test_case_f_principal_software_engineer(self):
        """Principal is not a verdict: the role family is."""
        self.assertScope(career_scope.OUT_OF_SCOPE, 'Principal Software Engineer',
                         PRINCIPAL_SOFTWARE_ENGINEER)

    def test_case_g_enterprise_architect_is_never_excluded_on_the_word_architect(self):
        verdict = self.assertScope(career_scope.IN_SCOPE, 'Enterprise Architect',
                                   ENTERPRISE_ARCHITECT)
        self.assertNotEqual(career_scope.OUT_OF_SCOPE, verdict['scope'])

    def test_case_h_software_architect_that_codes_daily(self):
        verdict = self.assertScope(career_scope.OUT_OF_SCOPE, 'Software Architect',
                                   SOFTWARE_ARCHITECT_CODING)
        self.assertEqual(career_scope.REASON_ARCHITECTURE_IC, verdict['reason'])


class ResponsibilitiesOverrideTitlesTests(unittest.TestCase):
    """The rule the whole module turns on, tested from both directions."""

    def test_a_head_of_sre_whose_reality_is_an_ic_role_is_out_of_scope(self):
        verdict = scope_of('Head of SRE', HEAD_OF_SRE_IC_REALITY)
        self.assertEqual(career_scope.OUT_OF_SCOPE, verdict['scope'])
        self.assertEqual([], verdict['leadership_families'],
                         '"no direct reports" must never read as evidence of reports')

    def test_an_sre_engineer_who_owns_an_organisation_is_in_scope(self):
        verdict = scope_of('Site Reliability Engineer', HEAD_OF_SRE_LEADERSHIP)
        self.assertEqual(career_scope.IN_SCOPE, verdict['scope'])

    def test_a_leader_who_reviews_code_is_still_a_leader(self):
        """Specification 8: organisational ownership protects against a false IC call."""
        verdict = scope_of('Head of Technology Operations', HEAD_OF_TECH_OPS)
        self.assertEqual(career_scope.IN_SCOPE, verdict['scope'])

    def test_a_language_name_alone_is_never_implementation_evidence(self):
        """"Understanding Python" and "maintain production Python services" differ."""
        understanding = scope_of(
            'Head of Platform Engineering',
            'You will lead the platform organisation. A good understanding of Python, '
            'Kubernetes and Terraform is expected so you can hold your own in design reviews.')
        self.assertNotEqual(career_scope.OUT_OF_SCOPE, understanding['scope'])
        self.assertLess(len(understanding['implementation_families']),
                        career_scope.MIN_IMPLEMENTATION_FAMILIES)

    def test_one_implementation_family_is_never_enough_on_its_own(self):
        verdict = scope_of(
            'Technology Transformation Lead',
            'You will shape our transformation programme across the organisation, define the '
            'operating model and occasionally write code to prototype an idea.')
        self.assertNotEqual(career_scope.OUT_OF_SCOPE, verdict['scope'])


class ArchitectureTests(unittest.TestCase):
    """Specification 6: "Architect" is never a verdict by itself."""

    def test_strategic_architecture_is_in_scope(self):
        self.assertEqual(career_scope.IN_SCOPE,
                         scope_of('Principal Architect', ENTERPRISE_ARCHITECT)['scope'])

    def test_implementation_heavy_architecture_is_out_of_scope(self):
        for title in ('Software Architect', 'Solution Architect', 'Application Architect',
                      'Platform Architect'):
            self.assertEqual(career_scope.OUT_OF_SCOPE,
                             scope_of(title, SOFTWARE_ARCHITECT_CODING)['scope'], title)

    def test_an_architecture_role_that_says_neither_is_uncertain_not_excluded(self):
        verdict = scope_of('Cloud Architect',
                           'You will join our cloud team in Zurich. We work with AWS and '
                           'Azure and value curiosity, ownership and a pragmatic mindset in '
                           'a growing international environment across our offices.')
        self.assertEqual(career_scope.UNCERTAIN, verdict['scope'])


class ThinEvidenceTests(unittest.TestCase):
    """Specification 10: a thin posting is unknown, not bad."""

    def test_a_thin_head_role_is_uncertain_and_stays_visible(self):
        verdict = scope_of('Head of SRE')
        self.assertEqual(career_scope.UNCERTAIN, verdict['scope'])
        self.assertTrue(career_scope.is_visible_by_default(verdict['scope']))

    def test_an_explicit_engineering_title_may_be_excluded_from_the_title_alone(self):
        for title in ('Senior Backend Engineer', 'Staff Software Engineer',
                      'Full Stack Developer', 'Data Engineer'):
            self.assertEqual(career_scope.OUT_OF_SCOPE, scope_of(title)['scope'], title)

    def test_an_ambiguous_engineer_title_stays_uncertain_until_a_description_arrives(self):
        """Conservative by design: these titles routinely carry a real mandate."""
        for title in ('Site Reliability Engineer', 'Platform Engineer', 'DevOps Engineer',
                      'Cloud Engineer'):
            self.assertEqual(career_scope.UNCERTAIN, scope_of(title)['scope'], title)

    def test_a_thin_posting_with_no_recognisable_shape_is_uncertain(self):
        self.assertEqual(career_scope.UNCERTAIN, scope_of('Rust. Zurich. Apply now.')['scope'])


class SeparationOfConcernsTests(unittest.TestCase):
    """Career scope is not a score, and not a lifecycle."""

    def test_the_score_never_changes_the_career_scope(self):
        job = {'title': 'Senior Backend Engineer', 'description': SENIOR_BACKEND_ENGINEER}
        verdicts = set()
        for score in (0, 41, 62, 88, 100):
            scored = dict(job, match_score=score, personal_fit_score=score, base_score=score)
            verdicts.add(career_scope.classify(scored)['scope'])
        self.assertEqual({career_scope.OUT_OF_SCOPE}, verdicts)

    def test_the_career_scope_never_changes_the_score(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                repo = JobRepository(conn)
                job = make_job(title='Senior Backend Engineer', external_id='1',
                               description=SENIOR_BACKEND_ENGINEER)
                scored = MatchScorer().score(job, make_profile())
                job_id, _ = repo.upsert(job, scored)
                conn.commit()
                row = conn.execute('SELECT match_score, personal_fit_score, base_score, '
                                   'career_scope FROM discovered_jobs WHERE id=?',
                                   (job_id,)).fetchone()
        self.assertEqual(career_scope.OUT_OF_SCOPE, row[3])
        self.assertEqual(scored['score'], row[0])
        self.assertEqual(scored['personal_fit'], row[1])
        self.assertEqual(scored['base_score'], row[2])

    def test_an_out_of_scope_job_is_never_expired_or_deleted(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                repo = JobRepository(conn)
                for index, (title, description) in enumerate((
                        ('Senior Backend Engineer', SENIOR_BACKEND_ENGINEER),
                        ('Site Reliability Engineer - Observability', SRE_OBSERVABILITY_IC))):
                    job = make_job(title=title, external_id=str(index + 1),
                                   description=description)
                    repo.upsert(job, MatchScorer().score(job, make_profile()))
                conn.commit()
                rows = conn.execute('SELECT state, career_scope, lifecycle_reason '
                                    'FROM discovered_jobs').fetchall()
        self.assertEqual(2, len(rows))
        for state, scope, lifecycle in rows:
            self.assertEqual(career_scope.OUT_OF_SCOPE, scope)
            self.assertEqual('NEW', state)          # alive, just not in the default view
            self.assertEqual('', lifecycle)


# --------------------------------------------------------------------------
# 2. what the Jobs screen does with it
# --------------------------------------------------------------------------
class DefaultViewTests(unittest.TestCase):
    """The default list shows leadership shapes; everything else is one chip away."""

    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def seed(self, title, description='', external_id=None, state=''):
        with jsdb.connect() as conn:
            job = make_job(title=title, description=description,
                           external_id=external_id or title)
            job_id, _ = JobRepository(conn).upsert(job, MatchScorer().score(job, make_profile()))
            if state:
                conn.execute('UPDATE discovered_jobs SET state=? WHERE id=?', (state, job_id))
            conn.commit()
        return job_id

    def titles(self, view=None):
        with jsdb.connect() as conn:
            kwargs = {} if view is None else {'view': view}
            return [card['title'] for card in jobs_service.list_cards(conn, **kwargs)]

    def test_the_default_view_hides_hands_on_roles_and_keeps_leadership_ones(self):
        head = self.seed('Head of SRE', HEAD_OF_SRE_LEADERSHIP)
        manager = self.seed('Engineering Manager Platform', ENGINEERING_MANAGER_PLATFORM)
        tpm = self.seed('Principal Technical Program Manager', PRINCIPAL_TPM)
        backend = self.seed('Senior Backend Engineer', SENIOR_BACKEND_ENGINEER)
        sre_ic = self.seed('Site Reliability Engineer - Observability', SRE_OBSERVABILITY_IC)
        architect = self.seed('Software Architect', SOFTWARE_ARCHITECT_CODING)

        with jsdb.connect() as conn:
            default = {card['id'] for card in jobs_service.list_cards(conn)}
            everything = {card['id'] for card in jobs_service.list_cards(conn, view='all')}
            excluded = {card['id'] for card in
                        jobs_service.list_cards(conn, view=jobs_service.OUT_OF_SCOPE_VIEW)}

        self.assertEqual({head, manager, tpm}, default)
        self.assertEqual({backend, sre_ic, architect}, excluded)
        self.assertEqual(default | excluded, everything,
                         'All active must still contain every active job')

    def test_an_uncertain_job_is_in_the_default_view(self):
        thin = self.seed('Head of Platform Engineering')
        self.assertIn(thin, [card['id'] for card in self._cards()])

    def _cards(self, view=None):
        with jsdb.connect() as conn:
            kwargs = {} if view is None else {'view': view}
            return jobs_service.list_cards(conn, **kwargs)

    def test_the_card_carries_the_scope_and_its_reason(self):
        self.seed('Site Reliability Engineer - Observability', SRE_OBSERVABILITY_IC)
        card = self._cards(view=jobs_service.OUT_OF_SCOPE_VIEW)[0]
        self.assertEqual(career_scope.OUT_OF_SCOPE, card['career_scope'])
        self.assertEqual(career_scope.REASON_SRE_IC, card['career_scope_reason'])
        self.assertTrue(card['career_scope_detail'])

    def test_the_counts_report_all_three_scopes(self):
        self.seed('Head of SRE', HEAD_OF_SRE_LEADERSHIP)
        self.seed('Head of Platform Engineering')
        self.seed('Senior Backend Engineer', SENIOR_BACKEND_ENGINEER)
        with jsdb.connect() as conn:
            counts = jobs_service.counts(conn)
        self.assertEqual(1, counts['in_scope'])
        self.assertEqual(1, counts['uncertain_scope'])
        self.assertEqual(1, counts['out_of_scope'])
        self.assertEqual(3, counts['total'], 'every job is still active')
        self.assertEqual(2, counts['filters'][jobs_service.LEADERSHIP])
        self.assertEqual(3, counts['filters'][jobs_service.ALL])

    def test_a_score_band_filter_still_selects_exactly_its_band(self):
        """Specification 12: scoring ranks inside a view, it does not define one."""
        self.seed('Head of SRE', HEAD_OF_SRE_LEADERSHIP)
        self.seed('Senior Backend Engineer', SENIOR_BACKEND_ENGINEER)
        with jsdb.connect() as conn:
            bands = sum(len(jobs_service.list_cards(conn, view=key))
                        for key in ('top', 'review', 'edge', 'low'))
        self.assertEqual(2, bands)


class SavedAndTrackedProtectionTests(unittest.TestCase):
    """Specification 13: an explicit decision outranks the classifier."""

    def setUp(self):
        self.db = TempDatabase().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)

    def seed(self, external_id='1'):
        with jsdb.connect() as conn:
            job = make_job(title='Senior Backend Engineer', external_id=external_id,
                           description=SENIOR_BACKEND_ENGINEER)
            job_id, _ = JobRepository(conn).upsert(job, MatchScorer().score(job, make_profile()))
            conn.commit()
        self.assertEqual(career_scope.OUT_OF_SCOPE, self._scope(job_id))
        return job_id

    def _scope(self, job_id):
        with jsdb.connect() as conn:
            return conn.execute('SELECT career_scope FROM discovered_jobs WHERE id=?',
                                (job_id,)).fetchone()[0]

    def _default_ids(self):
        with jsdb.connect() as conn:
            return [card['id'] for card in jobs_service.list_cards(conn)]

    def test_an_out_of_scope_job_that_was_saved_stays_visible(self):
        job_id = self.seed()
        self.assertNotIn(job_id, self._default_ids())
        with jsdb.connect() as conn:
            conn.execute("UPDATE discovered_jobs SET state='SAVED' WHERE id=?", (job_id,))
            conn.commit()
        self.assertIn(job_id, self._default_ids())
        self.assertEqual(career_scope.OUT_OF_SCOPE, self._scope(job_id),
                         'saving shows the job; it does not relabel it')

    def test_an_out_of_scope_job_with_an_application_record_stays_visible(self):
        job_id = self.seed()
        self.assertNotIn(job_id, self._default_ids())
        applications_mod.from_job(job_id)
        self.assertIn(job_id, self._default_ids())

    def test_a_closed_application_still_protects_the_job(self):
        """Rejected is still a tracking record, and still the user's own decision."""
        job_id = self.seed()
        application = applications_mod.from_job(job_id)
        applications_mod.update(application['id'], {'status': 'Rejected'})
        self.assertIn(job_id, self._default_ids())


class RescoreKeepsTheScopeCurrentTests(unittest.TestCase):
    """A job that has just been given its description is reclassified with it."""

    def test_adding_a_description_reclassifies_the_job(self):
        with TempDatabase() as database:
            with database.connect() as conn:
                job = make_job(title='Site Reliability Engineer', external_id='1')
                job_id, _ = JobRepository(conn).upsert(
                    job, MatchScorer().score(job, make_profile()))
                conn.commit()
                before = conn.execute('SELECT career_scope FROM discovered_jobs WHERE id=?',
                                      (job_id,)).fetchone()[0]
                conn.execute('UPDATE discovered_jobs SET description=? WHERE id=?',
                             (SRE_OBSERVABILITY_IC, job_id))
                conn.commit()
                jobs_service.rescore(conn)
                after = conn.execute('SELECT career_scope, state FROM discovered_jobs '
                                     'WHERE id=?', (job_id,)).fetchone()
        self.assertEqual(career_scope.UNCERTAIN, before)
        self.assertEqual(career_scope.OUT_OF_SCOPE, after[0])
        self.assertEqual('NEW', after[1], 'a rescore never touches the lifecycle')


if __name__ == '__main__':
    unittest.main()
