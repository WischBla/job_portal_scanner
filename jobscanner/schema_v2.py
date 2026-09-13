"""V2 schema: personal profile, documents, settings, AI cache, compensation, apply sessions.

Every statement here is additive and guarded.  ``migrate_v2`` may run against a
fresh file or against the V1 database of the original scanner; in both cases no
existing row is dropped or rewritten beyond the documented status mapping.
"""

import json

from .locations import fold

PERSON_DDL = '''
CREATE TABLE IF NOT EXISTS person_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    headline TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    postal_code TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    linkedin_url TEXT NOT NULL DEFAULT '',
    github_url TEXT NOT NULL DEFAULT '',
    website_url TEXT NOT NULL DEFAULT '',
    nationality TEXT NOT NULL DEFAULT '',
    work_authorization TEXT NOT NULL DEFAULT '',
    needs_sponsorship INTEGER NOT NULL DEFAULT 0,
    relocation TEXT NOT NULL DEFAULT '',
    willing_to_relocate INTEGER NOT NULL DEFAULT 0,
    languages TEXT NOT NULL DEFAULT '[]',
    availability TEXT NOT NULL DEFAULT '',
    notice_period TEXT NOT NULL DEFAULT '',
    earliest_start TEXT NOT NULL DEFAULT '',
    comp_minimum_chf INTEGER NOT NULL DEFAULT 0,
    comp_target_chf INTEGER NOT NULL DEFAULT 0,
    comp_stretch_chf INTEGER NOT NULL DEFAULT 0,
    comp_notes TEXT NOT NULL DEFAULT '',
    career_history TEXT NOT NULL DEFAULT '[]',
    technical_skills TEXT NOT NULL DEFAULT '[]',
    leadership_profile TEXT NOT NULL DEFAULT '',
    target_roles TEXT NOT NULL DEFAULT '[]',
    secondary_target_roles TEXT NOT NULL DEFAULT '[]',
    target_geography TEXT NOT NULL DEFAULT '[]',
    travel_willingness TEXT NOT NULL DEFAULT '',
    strengths TEXT NOT NULL DEFAULT '[]',
    achievements TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    mime_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    is_primary INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents(kind, is_primary DESC);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ai_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(job_id),
    FOREIGN KEY(job_id) REFERENCES discovered_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS compensation_estimates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    basis TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'CHF',
    total_min INTEGER,
    total_max INTEGER,
    base_min INTEGER,
    base_max INTEGER,
    bonus_min INTEGER,
    bonus_max INTEGER,
    equity TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'Low',
    commentary TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(job_id),
    FOREIGN KEY(job_id) REFERENCES discovered_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS salary_benchmarks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL DEFAULT '',
    role_family TEXT NOT NULL DEFAULT '',
    seniority TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT 'Switzerland',
    base_min INTEGER NOT NULL DEFAULT 0,
    base_max INTEGER NOT NULL DEFAULT 0,
    bonus_pct_min INTEGER NOT NULL DEFAULT 0,
    bonus_pct_max INTEGER NOT NULL DEFAULT 0,
    equity TEXT NOT NULL DEFAULT '',
    confidence TEXT NOT NULL DEFAULT 'Medium',
    notes TEXT NOT NULL DEFAULT '',
    is_builtin INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_benchmark_company ON salary_benchmarks(company);

CREATE TABLE IF NOT EXISTS application_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL,
    document_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(application_id, document_id),
    FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS apply_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    application_id INTEGER,
    url TEXT NOT NULL DEFAULT '',
    adapter TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    filled_json TEXT NOT NULL DEFAULT '[]',
    review_json TEXT NOT NULL DEFAULT '[]',
    uploads_json TEXT NOT NULL DEFAULT '[]',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apply_job ON apply_sessions(job_id);
'''

#: V1 stored German status labels.  V2 uses the canonical English pipeline.
STATUS_MAP = {
    'Vorbereitung': 'Preparation',
    'Beworben': 'Applied',
    'Screening': 'Screening',
    'Telefoninterview': 'Screening',
    'Interview': 'Interview',
    'Technisches Interview': 'Interview',
    'Finale Runde': 'Final',
    'Final': 'Final',
    'Angebot': 'Offer',
    'Offer': 'Offer',
    'Abgelehnt': 'Rejected',
    'Absage': 'Rejected',
    'Zurückgezogen': 'Withdrawn',
    'Zurueckgezogen': 'Withdrawn',
}

APPLICATION_STATUSES = ['Preparation', 'Applied', 'Screening', 'Interview',
                        'Final', 'Offer', 'Rejected', 'Withdrawn']

#: Locally curated market data.  Deliberately coarse: these are public-range
#: estimates for Swiss senior technology roles, not offer data.  ``role_family``
#: is matched with the keyword table in ``compensation.py``.
BENCHMARK_SEED = [
    # company,           role_family,   seniority,  base_min, base_max, bon_min, bon_max, equity,      confidence
    ('Google', 'engineering_leadership', 'Director', 260000, 330000, 20, 45, 'Significant RSU grant', 'Medium'),
    ('Google', 'engineering_leadership', 'Head of', 230000, 290000, 15, 35, 'RSU grant', 'Medium'),
    ('Google', 'program_management', 'Principal', 200000, 250000, 15, 30, 'RSU grant', 'Medium'),
    ('Microsoft', 'engineering_leadership', 'Director', 230000, 300000, 20, 40, 'Stock award', 'Medium'),
    ('Microsoft', 'program_management', 'Principal', 190000, 240000, 15, 30, 'Stock award', 'Medium'),
    ('Amazon Web Services / AWS', 'engineering_leadership', 'Director', 220000, 290000, 0, 10, 'RSU-heavy package', 'Medium'),
    ('NVIDIA', 'engineering_leadership', 'Director', 230000, 300000, 15, 35, 'RSU grant', 'Low'),
    ('Meta', 'engineering_leadership', 'Director', 250000, 330000, 15, 35, 'RSU grant', 'Low'),
    ('Apple', 'engineering_leadership', 'Director', 230000, 300000, 15, 30, 'RSU grant', 'Low'),
    ('UBS', 'engineering_leadership', 'Director', 200000, 260000, 20, 50, 'Deferred award possible', 'Medium'),
    ('UBS', 'technology_operations', 'Head of', 180000, 230000, 15, 35, 'Deferred award possible', 'Medium'),
    ('Swiss Re', 'technology_operations', 'Head of', 180000, 225000, 15, 30, 'LTI possible', 'Medium'),
    ('Zurich Insurance', 'technology_operations', 'Head of', 170000, 215000, 10, 25, 'LTI possible', 'Low'),
    ('SIX', 'technology_operations', 'Head of', 175000, 215000, 10, 25, 'Rarely offered', 'Medium'),
    ('Swisscom', 'technology_operations', 'Head of', 165000, 205000, 10, 20, 'Rarely offered', 'Medium'),
    ('Roche', 'technology_operations', 'Director', 185000, 235000, 15, 30, 'LTI / share plan', 'Medium'),
    ('Novartis', 'technology_operations', 'Director', 185000, 235000, 15, 30, 'LTI / share plan', 'Medium'),
    ('ABB', 'engineering_leadership', 'Head of', 165000, 205000, 10, 20, 'LTI possible', 'Low'),
    ('Proton', 'engineering_leadership', 'Head of', 160000, 200000, 0, 10, 'None / limited', 'Low'),
]


def _seed_person(conn, now):
    row = conn.execute('SELECT id FROM person_profile WHERE id=1').fetchone()
    if row:
        return
    from .person import DEFAULT_PERSON, to_row
    values = to_row(DEFAULT_PERSON)
    values['updated_at'] = now
    columns = ', '.join(values)
    placeholders = ', '.join('?' for _ in values)
    conn.execute('INSERT INTO person_profile (id, {0}) VALUES (1, {1})'.format(columns, placeholders),
                 tuple(values.values()))


def _seed_benchmarks(conn, now):
    if conn.execute('SELECT COUNT(*) FROM salary_benchmarks').fetchone()[0]:
        return
    conn.executemany(
        '''INSERT INTO salary_benchmarks
             (company, role_family, seniority, location, base_min, base_max,
              bonus_pct_min, bonus_pct_max, equity, confidence, notes, is_builtin, updated_at)
           VALUES (?,?,?,'Switzerland',?,?,?,?,?,?,'',1,?)''',
        [(c, f, s, bmin, bmax, pmin, pmax, eq, conf, now)
         for c, f, s, bmin, bmax, pmin, pmax, eq, conf in BENCHMARK_SEED])


def _seed_settings(conn, now):
    from .settings import DEFAULT_SETTINGS
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute('INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?,?,?)',
                     (key, json.dumps(value, ensure_ascii=False), now))


def rescore_existing_jobs(conn, profile):
    """Re-run the current scorer over rows scored by an older version.

    V1 stored German reason text and a different label vocabulary.  Re-scoring
    is additive - only the explanation columns change, never the job, its state
    or its link to an application - and it means jobs that were already in the
    database show the same kind of card as freshly scanned ones.
    """
    import json as _json

    from . import fit as _fit
    from .scoring import MatchScorer

    scorer = MatchScorer()
    extra = ','.join('{0}=?'.format(c) for c in _fit.SCORE_COLUMNS)
    rows = conn.execute('SELECT * FROM discovered_jobs').fetchall()
    for row in rows:
        job = dict(row)
        result = scorer.score(job, profile)
        columns = _fit.score_columns(result)
        conn.execute(
            'UPDATE discovered_jobs SET match_score=?, match_label=?, match_reasons=?, '
            'match_concerns=?, match_breakdown=?, matched_terms=?, {0} WHERE id=?'.format(extra),
            [result['score'], _label(result['score']),
             _json.dumps(result['reasons'], ensure_ascii=False),
             _json.dumps(result['concerns'], ensure_ascii=False),
             _json.dumps(result['breakdown'], ensure_ascii=False),
             _json.dumps(result['terms'], ensure_ascii=False)]
            + [columns[c] for c in _fit.SCORE_COLUMNS] + [job['id']])
    # Estimates are derived from the job, so drop the cache once.
    conn.execute('DELETE FROM compensation_estimates')
    return len(rows)


def _label(score):
    """The same five bands the scorer and the Jobs screen use."""
    from .jobs_service import classify
    return classify(score)


def migrate_v2(conn, now, profile=None):
    """Create the V2 tables, map legacy statuses and seed local defaults."""
    conn.executescript(PERSON_DDL)

    # Applications gained V2 fields; the German status labels become canonical.
    columns = {r[1] for r in conn.execute('PRAGMA table_info(applications)').fetchall()}
    for column, ddl in (('job_id', 'INTEGER'),
                        ('match_score', 'INTEGER'),
                        ('match_summary', "TEXT NOT NULL DEFAULT ''"),
                        ('salary_estimate', "TEXT NOT NULL DEFAULT ''")):
        if column not in columns:
            conn.execute('ALTER TABLE applications ADD COLUMN {0} {1}'.format(column, ddl))
    _canonicalize_statuses(conn)

    _seed_person(conn, now)
    _seed_benchmarks(conn, now)
    _seed_settings(conn, now)

    # Jobs scored by V1 carry German explanations; bring them onto the current
    # scorer exactly once, then remember that it has been done.
    done = conn.execute("SELECT value FROM schema_meta WHERE key='v2_rescored'").fetchone()
    if not done and profile is not None:
        rescore_existing_jobs(conn, profile)
        conn.execute("INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('v2_rescored', ?)",
                     (now,))
    else:
        for row in conn.execute('SELECT id, match_score FROM discovered_jobs').fetchall():
            conn.execute('UPDATE discovered_jobs SET match_label=? WHERE id=?',
                         (_label(row[1] or 0), row[0]))


def canonical_status(value):
    """Map any historic label onto the V2 pipeline without losing meaning.

    Exact match, then the German legacy table, then a substring match so a
    hand-written "Interview 1" still lands in Interview.  Only a genuinely
    unrecognisable label falls back to Preparation.
    """
    text = str(value or '').strip()
    if text in APPLICATION_STATUSES:
        return text
    mapped = STATUS_MAP.get(text)
    if mapped:
        return mapped
    folded = fold(text)
    for status in APPLICATION_STATUSES:
        if fold(status) == folded:
            return status
    for legacy, canonical in STATUS_MAP.items():
        if fold(legacy) and fold(legacy) in folded:
            return canonical
    for status in APPLICATION_STATUSES:
        if fold(status) in folded:
            return status
    return 'Preparation'


def _canonicalize_statuses(conn):
    """Rewrite legacy status labels in place, keeping a note of the original."""
    rows = conn.execute('SELECT id, status, notes FROM applications').fetchall()
    for row in rows:
        current = str(row[1] or '')
        canonical = canonical_status(current)
        if canonical == current:
            continue
        notes = str(row[2] or '')
        marker = 'Previous status label: "{0}".'.format(current)
        if marker not in notes:
            notes = (notes + '\n' + marker).strip()
        conn.execute('UPDATE applications SET status=?, notes=? WHERE id=?',
                     (canonical, notes, row[0]))
