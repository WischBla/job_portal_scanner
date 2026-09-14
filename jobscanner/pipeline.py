"""The scan pipeline.

The order below is mandatory and enforced in one place:

    1. fetch (source adapters: aggregators + company watchlist sources)
    2. normalize (JobNormalizer + LocationNormalizer)
    3. deduplicate
    4. HARD FILTER          <- nothing invalid survives this line
    5. score (MatchScorer)
    6. sort
    7. persist (JobRepository)
    8. reconcile lifecycle   <- only against sources that answered authoritatively

A job that fails step 4 is never scored for display; it is only written to the
rejection log so the UI can explain why it disappeared.

**A score is not a lifecycle event.**  Step 6 used to include a
minimum-score cut that silently threw away everything below the profile's
threshold, which meant a role the scanner was unsure about - a Head of SRE
whose description it never saw - was indistinguishable from a role it had
judged and rejected.  Every job that passes the hard filter is now stored and
shown; the Personal Fit Score decides the *order* and the recommendation band,
never whether the job exists.

Source adapters *discover*; they never decide relevance.  A watchlist company
being priority A buys its jobs nothing here - the match score is the ranking,
and priority is only ever a tie-breaker between two equally good roles.

One source failing is contained to that source: it is recorded against the
company and the source kind, shown in Config, and the rest of the scan carries
on.  Crucially, a source that failed - rate limited, timed out, errored,
failed to parse, or returned a suspiciously empty list - also takes no part in
step 8.  Only a source whose fetch was a complete, authoritative answer may
retire any of its jobs, and even then only after the confirmation policy in
:meth:`JobRepository.expire_missing`.
"""

import concurrent.futures
import json

from .db import connect, load_profile, now_iso, row_to_dict, utc_now_iso
from .filters import HardFilter, rejection_group
from .normalizer import JobNormalizer, identity_key
from .repository import JobRepository
from .scoring import EXCELLENT_FROM, REVIEW_FROM, STRONG_FROM, WEAK_FROM, MatchScorer
from .sources import ACTIVE, ERROR, base as source_base, get_adapter
from .watchlist import CompanyWatchlist

FETCH_TIMEOUT = 180

#: A source that returned jobs last time and returns none now, with no error
#: to explain it, is not trusted to be authoritative.  Silent emptiness is the
#: shape a soft block, an expired token or a changed payload takes, and acting
#: on it would retire a whole company's jobs on the strength of a shrug.
SUSPICIOUS_EMPTY_AFTER = 1


def _enabled_sources(conn):
    return [row_to_dict(r) for r in conn.execute(
        'SELECT * FROM job_sources WHERE enabled=1 ORDER BY id').fetchall()]


def fetch_source(source, profile):
    """Run one adapter. Adapter failures are contained to that source.

    Diagnostics are collected for the duration of the call and attached to the
    result, so the scan can tell "this board is empty" from "this board
    throttled us" without the adapters having to know anything about it.
    """
    try:
        config = json.loads(source.get('config_json') or '{}')
    except (TypeError, ValueError):
        config = {}
    adapter = get_adapter(source['source_type'])
    source_base.start_probe(source['name'])
    jobs = adapter.fetch_jobs(profile, config, source['name']) or []
    for job in jobs:
        job['source'] = source['name']
        job.setdefault('source_type', source['source_type'])
    return jobs


def run_scan(conn=None, fetcher=None):
    """Execute a full scan. ``fetcher`` is injectable so tests need no network."""
    owns_connection = conn is None
    conn = conn or connect()
    try:
        return _run_scan(conn, fetcher or fetch_source)
    finally:
        if owns_connection:
            conn.close()


def source_outcome(source, jobs, error=None, probe=None):
    """Classify what one source's fetch did.

    The point of this function is the difference between "returned nothing"
    and "could not tell us anything".  Both look like zero jobs; only the
    first one is an answer.  A board that produced jobs on its last scan and
    silently produces none now, or one whose requests had to be retried or
    were throttled on the way, is reported as ``EMPTY_BUT_SUSPICIOUS`` - which
    keeps it out of the lifecycle reconciliation entirely.
    """
    diagnostics = dict(probe.as_dict() if probe is not None else
                       {'requests': 0, 'retries': 0, 'throttled': 0, 'http_status': 0,
                        'error': ''})
    if error is not None:
        outcome = getattr(error, 'outcome', source_base.HTTP_ERROR)
        status = int(getattr(error, 'status', 0) or 0) or diagnostics['http_status']
        diagnostics.update({'outcome': outcome, 'http_status': status,
                            'error': str(error)[:400] or type(error).__name__})
        return outcome, 0, diagnostics['error'], diagnostics

    count = len(jobs)
    if count:
        diagnostics['outcome'] = source_base.SUCCESS
        return (source_base.SUCCESS, count,
                '{0} postings returned.'.format(count), diagnostics)

    previously = int((source or {}).get('last_job_count') or 0)
    if previously >= SUSPICIOUS_EMPTY_AFTER or diagnostics['throttled'] or diagnostics['retries']:
        diagnostics['outcome'] = source_base.EMPTY_BUT_SUSPICIOUS
        why = ('it returned {0} last time'.format(previously) if previously
               else '{0} request(s) had to be retried'.format(diagnostics['retries']))
        diagnostics['error'] = ('Returned no postings, but {0} - not treated as an '
                                'authoritative answer.'.format(why))
        return (source_base.EMPTY_BUT_SUSPICIOUS, 0, diagnostics['error'], diagnostics)

    diagnostics['outcome'] = source_base.SUCCESS
    return (source_base.SUCCESS, 0, 'The endpoint answered with no postings.', diagnostics)


def _fetch_all(sources, profile, fetcher):
    """Fetch every source in parallel; collect jobs and per-source outcomes.

    Returns ``(raw_jobs, outcomes, errors)`` where ``outcomes`` maps a source
    name to ``(outcome, job_count, detail, diagnostics)``.

    Sources are fetched concurrently, but each provider's own ceiling is
    applied inside :mod:`jobscanner.sources.base`, so two Lever boards in this
    pool still queue behind one another rather than doubling the rate seen by
    Lever.
    """
    raw_jobs, outcomes, errors = [], {}, []
    if not sources:
        return raw_jobs, outcomes, errors
    by_name = {source['name']: source for source in sources}

    def run(source):
        probe = None
        try:
            jobs = fetcher(source, profile) or []
            probe = source_base.current_probe()
            return jobs, None, probe
        except Exception as exc:  # noqa: BLE001 - SourceError and anything else
            return [], exc, source_base.current_probe()

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(sources))) as pool:
        futures = {pool.submit(run, source): source['name'] for source in sources}
        for future in concurrent.futures.as_completed(futures, timeout=FETCH_TIMEOUT + 60):
            name = futures[future]
            try:
                jobs, error, probe = future.result(timeout=FETCH_TIMEOUT)
            except Exception as exc:  # noqa: BLE001 - the pool itself gave up
                jobs, error, probe = [], exc, None
            outcome, count, detail, diagnostics = source_outcome(
                by_name.get(name), jobs, error, probe)
            outcomes[name] = (outcome, count, detail, diagnostics)
            if outcome == source_base.SUCCESS:
                raw_jobs.extend(jobs)
            else:
                errors.append({'source': name, 'error': detail, 'outcome': outcome,
                               'http_status': diagnostics.get('http_status') or 0,
                               'retries': diagnostics.get('retries') or 0})
    return raw_jobs, outcomes, errors


def _record_source_health(conn, sources, outcomes):
    """Persist what every source did, so Config can show it later.

    Both the coarse status the UI has always shown and the diagnostics that
    explain it: the HTTP status, how many retries it took, when it was last
    attempted and when it last answered *authoritatively*.  A failed attempt
    never overwrites ``last_job_count`` - the last number a source really
    returned is exactly what tells a suspicious empty response from a real
    one on the next scan.
    """
    ts = now_iso()
    for source in sources:
        outcome, count, detail, diagnostics = outcomes.get(
            source['name'],
            (source_base.NETWORK_ERROR, 0, 'Source did not answer.', {}))
        status = ACTIVE if outcome == source_base.SUCCESS else ERROR
        http_status = int(diagnostics.get('http_status') or 0)
        retries = int(diagnostics.get('retries') or 0)
        if outcome == source_base.SUCCESS:
            conn.execute(
                'UPDATE job_sources SET last_status=?, last_checked_at=?, last_success_at=?, '
                'last_error=?, last_job_count=?, last_outcome=?, last_http_status=?, '
                'last_retry_count=?, last_attempt_at=?, last_authoritative_at=? WHERE id=?',
                (status, ts, ts, '', int(count), outcome, http_status, retries, ts, ts,
                 source['id']))
        else:
            conn.execute(
                'UPDATE job_sources SET last_status=?, last_checked_at=?, last_error=?, '
                'last_outcome=?, last_http_status=?, last_retry_count=?, last_attempt_at=? '
                'WHERE id=?',
                (status, ts, str(detail)[:400], outcome, http_status, retries, ts,
                 source['id']))
    CompanyWatchlist(conn).record_scan_results(outcomes)


def _deduplicate(normalized_jobs):
    """Collapse the same posting seen more than once.

    Two keys, in this order:

    ``dedupe_key``
        The canonical job URL (tracking parameters stripped).  This is what
        catches the identical posting syndicated to two portals.

    ``identity_key``
        normalized company + title + location.  Applied only *across* sources,
        which is exactly the aggregator-versus-company-source case: Arbeitnow
        and the company's own Greenhouse board list one role under two URLs.
        Restricting it to different sources matters, because a single employer
        really can have two open roles with the same title in the same city.

    When two copies of one posting collide, the **richer** one wins rather than
    the one that happened to arrive first.  That difference is worth real
    points: an aggregator usually carries a title and a stub, the company's own
    board carries the whole advert, and keeping whichever thread finished first
    meant a job with a perfectly good description sometimes ended up stored as
    an evidence-LOW stub.
    """
    out = []
    by_dedupe_key, by_identity = {}, {}
    duplicates = 0

    def richer(candidate, incumbent):
        return len(str(candidate.get('description') or '')) > \
            len(str(incumbent.get('description') or ''))

    for job in normalized_jobs:
        key = job['dedupe_key'] or job['source_key']
        identity = job.get('identity_key') or ''

        index = by_dedupe_key.get(key)
        if index is None and identity:
            owner = by_identity.get(identity)
            if owner is not None and owner[0] != job.get('source'):
                index = owner[1]
        if index is not None:
            duplicates += 1
            if richer(job, out[index]):
                out[index] = job
            continue

        out.append(job)
        by_dedupe_key[key] = len(out) - 1
        if identity:
            by_identity.setdefault(identity, (job.get('source'), len(out) - 1))
    return out, duplicates


def _run_scan(conn, fetcher):
    started = utc_now_iso()
    profile = load_profile(conn)                       # <- always the saved profile
    repo = JobRepository(conn)

    cursor = conn.execute(
        'INSERT INTO scout_runs (started_at,status,profile_snapshot) VALUES (?,?,?)',
        (started, 'running', json.dumps(profile, ensure_ascii=False)))
    run_id = cursor.lastrowid
    repo.mark_all_seen()
    repo.release_orphaned_applications()
    repo.clear_rejections()
    conn.commit()

    # 1: fetch.  Aggregators and company watchlist sources are the same kind of
    # thing to the pipeline - a source that returns raw jobs.
    sources = _enabled_sources(conn)
    company_source_names = set(CompanyWatchlist(conn).active_source_names())
    raw_jobs, outcomes, errors = _fetch_all(sources, profile, fetcher)
    # The one list that is allowed to retire anything.  A source that was rate
    # limited, timed out, errored, failed to parse or answered with a
    # suspicious emptiness is deliberately absent from it: its jobs are left
    # exactly as they are until it can answer properly again.
    authoritative_sources = [name for name, outcome in outcomes.items()
                             if outcome[0] in source_base.AUTHORITATIVE_OUTCOMES]
    _record_source_health(conn, sources, outcomes)
    conn.commit()

    normalizer = JobNormalizer()
    hard_filter = HardFilter()
    scorer = MatchScorer()

    # 2 + 3: normalize, then collapse duplicates across portals.
    normalized = [normalizer.normalize(raw) for raw in raw_jobs]
    normalized, duplicates = _deduplicate(normalized)

    # 4: hard filter. 5: score everything that survived - and keep all of it.
    #
    # There is no minimum-score cut here any more.  A low Personal Fit Score
    # ranks a job last and labels it "Low Priority"; it never removes it, and
    # it never removes one the scanner simply knows too little about.
    accepted, rejected_count, geo_passed = [], 0, 0
    filtered_keys = []
    swiss_eligible = sum(1 for job in normalized if job.get('switzerland_eligible'))
    for job in normalized:
        rejection = hard_filter.check(job, profile)
        if rejection:
            if rejection_group(rejection.get('code')) != 'geography':
                geo_passed += 1     # survived geography, dropped for another reason
            repo.record_rejection(run_id, job, rejection)
            rejected_count += 1
            # The source delivered it and the filter looked at it and said no:
            # a confirmed decision, so a stored copy is retired at once.
            filtered_keys.append(job.get('source_key'))
            continue
        geo_passed += 1
        accepted.append((job, scorer.score(job, profile)))

    # 6: sort, 7: persist.
    accepted.sort(key=_ranking(conn), reverse=False)
    seen_at = utc_now_iso()
    new_count, seen_ids = 0, []
    for job, scored in accepted:
        job_id, is_new = repo.upsert(job, scored, seen_at)
        seen_ids.append(job_id)
        if is_new:
            new_count += 1
    # 8: reconcile.  Two different facts, handled differently: a posting the
    # scan saw and rejected is retired immediately (tightening a filter has to
    # take effect), while a posting a healthy source simply stopped mentioning
    # has to be missing from two consecutive authoritative scans first.
    filtered_count = repo.retire_filtered(filtered_keys)
    expired_count = repo.expire_missing(authoritative_sources, seen_ids)
    repo.link_existing_applications()

    finished = utc_now_iso()
    status = 'ok' if not errors else ('partial' if accepted or normalized else 'error')
    bands = _band_counts(accepted)
    enrichment = _enrichment_counts(accepted)
    companies_scanned = len([s for s in sources if s['name'] in company_source_names])
    conn.execute('''UPDATE scout_runs SET finished_at=?,status=?,fetched_count=?,matched_count=?,
                    new_count=?,errors=?,sources_scanned=?,rejected_count=?,geo_passed_count=?,
                    companies_scanned=?,swiss_eligible_count=?,duplicate_count=?,
                    source_failure_count=? WHERE id=?''',
                 (finished, status, len(normalized), len(accepted), new_count,
                  json.dumps(errors, ensure_ascii=False), len(sources), rejected_count,
                  geo_passed, companies_scanned, swiss_eligible, duplicates,
                  len(errors), run_id))
    conn.commit()

    strong = sum(1 for _, s in accepted if s['score'] >= STRONG_FROM)
    excellent = sum(1 for _, s in accepted if s['score'] >= EXCELLENT_FROM)
    return {
        'bands': bands,
        'enrichment': enrichment,
        'needs_enrichment_count': enrichment.get('NEEDS_ENRICHMENT', 0),
        'high_potential_count': sum(
            1 for _, s in accepted if (s.get('evidence') or {}).get('high_potential')),
        'filtered_count': filtered_count,
        'run_id': run_id,
        'status': status,
        'started_at': started,
        'finished_at': finished,
        'sources_scanned': len(sources),
        'companies_scanned': companies_scanned,
        'fetched_count': len(raw_jobs),
        'unique_count': len(normalized),
        'duplicate_count': duplicates,
        'swiss_eligible_count': swiss_eligible,
        'rejected_count': rejected_count,
        'matched_count': len(accepted),
        'geo_passed_count': geo_passed,
        'strong_count': strong,
        'excellent_count': excellent,
        'new_count': new_count,
        'expired_count': expired_count,
        'source_failure_count': len(errors),
        'errors': errors,
        'authoritative_sources': len(authoritative_sources),
        'source_results': [
            {'source': name, 'status': ACTIVE if outcome[0] == source_base.SUCCESS else ERROR,
             'outcome': outcome[0], 'jobs': outcome[1], 'detail': outcome[2],
             'http_status': (outcome[3] or {}).get('http_status') or 0,
             'retries': (outcome[3] or {}).get('retries') or 0,
             'authoritative': outcome[0] in source_base.AUTHORITATIVE_OUTCOMES,
             'is_company': name in company_source_names}
            for name, outcome in sorted(outcomes.items())
        ],
    }


def _band_counts(accepted):
    """How the run's jobs fall into the five fixed recommendation bands."""
    counts = {'Exceptional': 0, 'Strong': 0, 'Review': 0, 'Edge': 0, 'Low priority': 0}
    for _job, scored in accepted:
        score = int(scored['score'])
        if score >= EXCELLENT_FROM:
            counts['Exceptional'] += 1
        elif score >= STRONG_FROM:
            counts['Strong'] += 1
        elif score >= REVIEW_FROM:
            counts['Review'] += 1
        elif score >= WEAK_FROM:
            counts['Edge'] += 1
        else:
            counts['Low priority'] += 1
    return counts


def _enrichment_counts(accepted):
    """How much the run actually knows about what it found."""
    counts = {}
    for _job, scored in accepted:
        state = (scored.get('evidence') or {}).get('state') or 'NEEDS_ENRICHMENT'
        counts[state] = counts.get(state, 0) + 1
    return counts


#: Company priority is a tie-breaker only.  A weak role at a priority-A company
#: must never outrank a strong one elsewhere, so the score is compared first and
#: priority only separates jobs that already scored the same.
PRIORITY_RANK = {'A': 0, 'B': 1, 'C': 2}
_UNWATCHED_RANK = 3


def _ranking(conn):
    priorities = {row['company_name'].casefold(): row['priority'] for row in
                  conn.execute('SELECT company_name, priority FROM company_watchlist').fetchall()}

    def key(pair):
        job, scored = pair
        priority = priorities.get((job.get('company') or '').casefold())
        return (
            -int(scored['score']),                                   # match score DESC
            PRIORITY_RANK.get(priority, _UNWATCHED_RANK),            # company priority
            _sortable_date(job.get('published_at')),                 # posting date DESC
        )
    return key


def _sortable_date(value):
    """Newest first: ISO dates sort lexically, so invert with a reverse-ish key."""
    text = str(value or '')
    return tuple(-ord(c) for c in text[:24]) if text else (1,)
