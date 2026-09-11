"""The scan pipeline.

The order below is mandatory and enforced in one place:

    1. fetch (source adapters)
    2. normalize (JobNormalizer + LocationNormalizer)
    3. deduplicate
    4. HARD FILTER          <- nothing invalid survives this line
    5. score (MatchScorer)
    6. minimum-score cut + sort
    7. persist (JobRepository)

A job that fails step 4 is never scored for display; it is only written to the
rejection log so the UI can explain why it disappeared.
"""

import concurrent.futures
import json

from .db import connect, load_profile, row_to_dict, utc_now_iso
from .filters import HardFilter, rejection_group
from .normalizer import JobNormalizer
from .repository import JobRepository
from .scoring import EXCELLENT_FROM, STRONG_FROM, MatchScorer
from .sources import SourceError, get_adapter

FETCH_TIMEOUT = 90


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

    sources = _enabled_sources(conn)
    raw_jobs, errors, healthy_sources = [], [], []
    if sources:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(sources))) as pool:
            futures = {pool.submit(fetcher, source, profile): source['name'] for source in sources}
            for future in concurrent.futures.as_completed(futures, timeout=FETCH_TIMEOUT + 30):
                name = futures[future]
                try:
                    raw_jobs.extend(future.result(timeout=FETCH_TIMEOUT))
                    healthy_sources.append(name)
                except (SourceError, Exception) as exc:  # noqa: B014 - SourceError is an Exception
                    errors.append({'source': name, 'error': str(exc)[:400]})

    normalizer = JobNormalizer()
    hard_filter = HardFilter()
    scorer = MatchScorer()

    # 2 + 3: normalize, then collapse duplicates across portals.
    normalized, seen_keys = [], set()
    duplicates = 0
    for raw in raw_jobs:
        job = normalizer.normalize(raw)
        key = job['dedupe_key'] or job['source_key']
        if key in seen_keys:
            duplicates += 1
            continue
        seen_keys.add(key)
        normalized.append(job)

    # 4: hard filter. 5: score only what survived.
    accepted, rejected_count, geo_passed = [], 0, 0
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
    accepted.sort(key=lambda pair: pair[1]['score'], reverse=True)
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
    conn.execute('''UPDATE scout_runs SET finished_at=?,status=?,fetched_count=?,matched_count=?,
                    new_count=?,errors=?,sources_scanned=?,rejected_count=?,geo_passed_count=?
                    WHERE id=?''',
                 (finished, status, len(normalized), len(accepted), new_count,
                  json.dumps(errors, ensure_ascii=False), len(sources), rejected_count,
                  geo_passed, run_id))
    conn.commit()

    strong = sum(1 for _, s in accepted if s['score'] >= STRONG_FROM)
    excellent = sum(1 for _, s in accepted if s['score'] >= EXCELLENT_FROM)
    return {
        'run_id': run_id,
        'status': status,
        'started_at': started,
        'finished_at': finished,
        'sources_scanned': len(sources),
        'fetched_count': len(raw_jobs),
        'unique_count': len(normalized),
        'duplicate_count': duplicates,
        'rejected_count': rejected_count,
        'matched_count': len(accepted),
        'geo_passed_count': geo_passed,
        'strong_count': strong,
        'excellent_count': excellent,
        'new_count': new_count,
        'expired_count': expired_count,
        'errors': errors,
    }
