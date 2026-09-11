"""Domain layer of the local Swiss job scanner.

The package is deliberately free of HTTP/UI concerns so the filtering rules can
be unit tested without a running server:

    sources/      JobSourceAdapter implementations (one per portal)
    normalizer    raw source payload -> NormalizedJob
    locations     LocationNormalizer (the Switzerland decision)
    filters       HardFilter (runs BEFORE any scoring)
    scoring       MatchScorer (explainable 0-100 score)
    repository    JobRepository (dedupe + persistence + job states)
    pipeline      fetch -> normalize -> dedupe -> hard filter -> score -> persist
    profile       the single canonical SearchProfile stored in SQLite
    db            connection handling and idempotent migrations
"""

__all__ = [
    'db', 'profile', 'locations', 'normalizer', 'filters', 'scoring',
    'repository', 'pipeline', 'sources',
]
