"""The scan pipeline.

The order below is mandatory and enforced in one place:

    1. fetch (source adapters: aggregators + company watchlist sources)
    2. normalize (JobNormalizer + LocationNormalizer)
    3. deduplicate
    4. HARD FILTER          <- nothing invalid survives this line
    5. score (MatchScorer)
    6. minimum-score cut + sort
    7. persist (JobRepository)

A job that fails step 4 is never scored for display; it is only written to the
rejection log so the UI can explain why it disappeared.

Source adapters *discover*; they never decide relevance.  A watchlist company
being priority A buys its jobs nothing here - the match score is the ranking,
and priority is only ever a tie-breaker between two equally good roles.

One source failing is contained to that source: it is recorded against the
company and the source kind, shown in Config, and the rest of the scan carries
on.
"""

import concurrent.futures
import json

from .db import connect, load_profile, now_iso, row_to_dict, utc_now_iso
from .filters import HardFilter, rejection_group
from .normalizer import JobNormalizer, identity_key
from .repository import JobRepository
from .scoring import EXCELLENT_FROM, STRONG_FROM, MatchScorer
from .sources import ACTIVE, ERROR, get_adapter
from .watchlist import CompanyWatchlist

FETCH_TIMEOUT = 180


def _enabled_sources(conn):
    return [row_to_dict(r) for r in conn.execute(
        'SELECT * FROM job_sources WHERE enabled=1 ORDER BY id').fetchall()]


def fetch_source(source, profile):
    """Run one adapter. Adapter failures are contained to that source."""
    try:
        config = json.loads(source.get('config_json') or '{}')
    except (TypeError, ValueError):
        config = {}
    adapter = get_adapter(source['source_type'])
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


def _fetch_all(sources, profile, fetcher):
    """Fetch every source in parallel; collect jobs and per-source outcomes.

    Returns ``(raw_jobs, outcomes, errors)`` where ``outcomes`` maps a source
    name to ``(status, job_count, detail)``.
    """
    raw_jobs, outcomes, errors = [], {}, []
    if not sources:
        return raw_jobs, outcomes, errors
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(sources))) as pool:
        futures = {pool.submit(fetcher, source, profile): source['name'] for source in sources}
        for future in concurrent.futures.as_completed(futures, timeout=FETCH_TIMEOUT + 60):
            name = futures[future]
            try:
                jobs = future.result(timeout=FETCH_TIMEOUT) or []
            except Exception as exc:  # noqa: BLE001 - SourceError and anything else
                message = str(exc)[:400] or type(exc).__name__
                outcomes[name] = (ERROR, 0, message)
                errors.append({'source': name, 'error': message})
                continue
            raw_jobs.extend(jobs)
            outcomes[name] = (ACTIVE, len(jobs), '{0} postings returned.'.format(len(jobs)))
    return raw_jobs, outcomes, errors


def _record_source_health(conn, sources, outcomes):
    """Persist what every source did, so Config can show it later."""
    ts = now_iso()
    for source in sources:
        status, count, detail = outcomes.get(source['name'], (ERROR, 0, 'Source did not answer.'))
        if status == ACTIVE:
            conn.execute(
                'UPDATE job_sources SET last_status=?, last_checked_at=?, last_success_at=?, '
                'last_error=?, last_job_count=? WHERE id=?',
                (status, ts, ts, '', int(count), source['id']))
        else:
            conn.execute(
                'UPDATE job_sources SET last_status=?, last_checked_at=?, last_error=?, '
                'last_job_count=? WHERE id=?', (status, ts, str(detail)[:400], 0, source['id']))
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
    """
    out = []
    by_dedupe_key, by_identity = set(), {}
    duplicates = 0
    for job in normalized_jobs:
        key = job['dedupe_key'] or job['source_key']
        if key in by_dedupe_key:
            duplicates += 1
            continue
        identity = job.get('identity_key') or ''
        owner = by_identity.get(identity)
        if identity and owner is not None and owner != job.get('source'):
            duplicates += 1
            continue
        by_dedupe_key.add(key)
        if identity:
            by_identity.setdefault(identity, job.get('source'))
        out.append(job)
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
    healthy_sources = [name for name, (status, _, _) in outcomes.items() if status == ACTIVE]
    _record_source_health(conn, sources, outcomes)
    conn.commit()

    normalizer = JobNormalizer()
    hard_filter = HardFilter()
    scorer = MatchScorer()

    # 2 + 3: normalize, then collapse duplicates across portals.
    normalized = [normalizer.normalize(raw) for raw in raw_jobs]
    normalized, duplicates = _deduplicate(normalized)

    # 4: hard filter. 5: score only what survived.
    accepted, rejected_count, geo_passed = [], 0, 0
    swiss_eligible = sum(1 for job in normalized if job.get('switzerland_eligible'))
    for job in normalized:
        rejection = hard_filter.check(job, profile)
        if rejection:
            if rejection_group(rejection.get('code')) != 'geography':
                geo_passed += 1     # survived geography, dropped for another reason
            repo.record_rejection(run_id, job, rejection)
            rejected_count += 1
            continue
        geo_passed += 1
        scored = scorer.score(job, profile)
        minimum = int(profile.get('minimum_match_score') or 0)
        if scored['score'] < minimum:
            repo.record_rejection(run_id, job, {
                'stage': 'score',
                'code': 'below_minimum_score',
                'reason': 'Match score {0} is below your minimum of {1}.'.format(scored['score'], minimum),
            }, score=scored['score'])
            rejected_count += 1
            continue
        accepted.append((job, scored))

    # 6: sort, 7: persist.
    accepted.sort(key=_ranking(conn), reverse=False)
    seen_at = utc_now_iso()
    new_count, seen_ids = 0, []
    for job, scored in accepted:
        job_id, is_new = repo.upsert(job, scored, seen_at)
        seen_ids.append(job_id)
        if is_new:
            new_count += 1
    # Anything a healthy source no longer matches is retired, so tightening a
    # filter really does remove results instead of leaving stale rows behind.
    expired_count = repo.expire_missing(healthy_sources, seen_ids)
    repo.link_existing_applications()

    finished = utc_now_iso()
    status = 'ok' if not errors else ('partial' if accepted or normalized else 'error')
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
        'source_results': [
            {'source': name, 'status': outcome[0], 'jobs': outcome[1], 'detail': outcome[2],
             'is_company': name in company_source_names}
            for name, outcome in sorted(outcomes.items())
        ],
    }


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
