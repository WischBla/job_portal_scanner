#!/usr/bin/env python3
import concurrent.futures
import html
import json
import os
import re
import sqlite3
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import xml.etree.ElementTree as ET
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / 'static'
DATA_DIR = BASE_DIR / 'data'
DB_PATH = DATA_DIR / 'applications.db'
HOST = '127.0.0.1'
PORT = int(os.environ.get('APPLICATION_TRACKER_PORT', '8765'))

APPLICATION_FIELDS = [
    'company', 'position', 'level', 'location', 'work_model', 'source', 'job_url',
    'applied_date', 'status', 'last_response', 'summary', 'next_action',
    'follow_up_date', 'contact_name', 'contact_details', 'salary_range', 'priority',
    'cv_version', 'cover_letter', 'notes'
]
EVENT_FIELDS = ['event_date', 'event_type', 'person', 'note', 'next_step', 'follow_up_date']

DEFAULT_PROFILE = {
    'target_roles': [
        'Director Technical Operations', 'Director Technology Operations', 'Director Engineering',
        'Director Platform Engineering', 'Director Cloud Engineering', 'Director Engineering Operations',
        'Director Site Reliability Engineering', 'Director DevOps', 'Director Technology Transformation',
        'Director Technical Program Management', 'Head of Technical Operations', 'Head of Technology Operations',
        'Head of Engineering', 'Head of Platform Engineering', 'Head of Cloud', 'Head of SRE', 'Head of DevOps',
        'Head of Engineering Operations', 'Head of AI Operations', 'Principal Technical Program Manager',
        'Senior Technical Program Manager', 'Technical Program Director', 'Engineering Transformation Lead',
        'Technology Transformation Lead'
    ],
    'skills': [
        'technical program management', 'technical project management', 'program management', 'project management',
        'engineering operations', 'technology operations', 'site reliability', 'SRE', 'platform engineering',
        'cloud', 'AWS', 'DevSecOps', 'DevOps', 'observability', 'CI/CD', 'application security',
        'AI operations', 'AIOps', 'agentic AI', 'automation', 'engineering leadership', 'architecture',
        'stakeholder management', 'transformation'
    ],
    'locations': ['Zürich', 'Zurich', 'Zug', 'Luzern', 'Lucerne', 'Bern', 'Basel', 'Aargau', 'Schwyz', 'St. Gallen', 'Switzerland', 'Schweiz'],
    'exclude_keywords': ['junior', 'intern', 'internship', 'graduate', 'trainee', 'sales', 'marketing', 'recruiter', 'talent acquisition', 'customer success'],
    'min_score': 58,
    'min_salary_chf': 235000,
    'prefer_remote': 1,
    'allow_hybrid': 1,
    'include_europe_remote': 1,
    'auto_hours': 12,
    'jobicy_enabled': 1,
    'arbeitnow_enabled': 1,
}


def now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db_connect() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            position TEXT NOT NULL,
            level TEXT DEFAULT '',
            location TEXT DEFAULT '',
            work_model TEXT DEFAULT '',
            source TEXT DEFAULT '',
            job_url TEXT DEFAULT '',
            applied_date TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Vorbereitung',
            last_response TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            next_action TEXT DEFAULT '',
            follow_up_date TEXT DEFAULT '',
            contact_name TEXT DEFAULT '',
            contact_details TEXT DEFAULT '',
            salary_range TEXT DEFAULT '',
            priority TEXT DEFAULT 'B - Interessant',
            cv_version TEXT DEFAULT '',
            cover_letter TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            application_id INTEGER NOT NULL,
            event_date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            person TEXT DEFAULT '',
            note TEXT DEFAULT '',
            next_step TEXT DEFAULT '',
            follow_up_date TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS job_search_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            target_roles TEXT NOT NULL,
            skills TEXT NOT NULL,
            locations TEXT NOT NULL,
            exclude_keywords TEXT NOT NULL,
            min_score INTEGER NOT NULL DEFAULT 58,
            min_salary_chf INTEGER NOT NULL DEFAULT 235000,
            prefer_remote INTEGER NOT NULL DEFAULT 1,
            allow_hybrid INTEGER NOT NULL DEFAULT 1,
            include_europe_remote INTEGER NOT NULL DEFAULT 1,
            auto_hours INTEGER NOT NULL DEFAULT 12,
            jobicy_enabled INTEGER NOT NULL DEFAULT 1,
            arbeitnow_enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS discovered_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            external_id TEXT NOT NULL,
            company TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            remote INTEGER,
            job_url TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            excerpt TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            salary_min REAL,
            salary_max REAL,
            salary_currency TEXT NOT NULL DEFAULT '',
            salary_period TEXT NOT NULL DEFAULT '',
            match_score INTEGER NOT NULL DEFAULT 0,
            match_label TEXT NOT NULL DEFAULT '',
            match_reasons TEXT NOT NULL DEFAULT '[]',
            matched_terms TEXT NOT NULL DEFAULT '[]',
            review_state TEXT NOT NULL DEFAULT 'Neu',
            is_new INTEGER NOT NULL DEFAULT 1,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            application_id INTEGER,
            UNIQUE(source, external_id),
            FOREIGN KEY(application_id) REFERENCES applications(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS scout_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'running',
            fetched_count INTEGER NOT NULL DEFAULT 0,
            matched_count INTEGER NOT NULL DEFAULT 0,
            new_count INTEGER NOT NULL DEFAULT 0,
            errors TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS job_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            config_json TEXT NOT NULL DEFAULT '{}',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_app_status ON applications(status);
        CREATE INDEX IF NOT EXISTS idx_app_followup ON applications(follow_up_date);
        CREATE INDEX IF NOT EXISTS idx_event_app ON events(application_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_score ON discovered_jobs(match_score DESC);
        CREATE INDEX IF NOT EXISTS idx_jobs_state ON discovered_jobs(review_state);
        CREATE INDEX IF NOT EXISTS idx_jobs_seen ON discovered_jobs(first_seen DESC);
        ''')
        existing = conn.execute('SELECT id FROM job_search_profile WHERE id = 1').fetchone()
        if not existing:
            conn.execute('''INSERT INTO job_search_profile
                (id,target_roles,skills,locations,exclude_keywords,min_score,min_salary_chf,prefer_remote,allow_hybrid,
                 include_europe_remote,auto_hours,jobicy_enabled,arbeitnow_enabled,updated_at)
                VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (
                json.dumps(DEFAULT_PROFILE['target_roles'], ensure_ascii=False),
                json.dumps(DEFAULT_PROFILE['skills'], ensure_ascii=False),
                json.dumps(DEFAULT_PROFILE['locations'], ensure_ascii=False),
                json.dumps(DEFAULT_PROFILE['exclude_keywords'], ensure_ascii=False),
                DEFAULT_PROFILE['min_score'], DEFAULT_PROFILE['min_salary_chf'], DEFAULT_PROFILE['prefer_remote'],
                DEFAULT_PROFILE['allow_hybrid'], DEFAULT_PROFILE['include_europe_remote'], DEFAULT_PROFILE['auto_hours'],
                DEFAULT_PROFILE['jobicy_enabled'], DEFAULT_PROFILE['arbeitnow_enabled'], now_iso()
            ))
        source_count = conn.execute('SELECT COUNT(*) FROM job_sources').fetchone()[0]
        if source_count == 0:
            ts = now_iso()
            defaults = [
                ('Arbeitnow', 'arbeitnow', '{}', 1),
                ('Remotive', 'remotive', '{}', 1),
                ('Jobicy', 'jobicy', '{}', 1),
            ]
            conn.executemany('INSERT INTO job_sources (name,source_type,config_json,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                             [(n,t,c,e,ts,ts) for n,t,c,e in defaults])


def row_to_dict(row):
    return dict(row) if row is not None else None


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = data.strip()
        if text:
            self.parts.append(text)


def strip_html(value):
    if not value:
        return ''
    parser = TextExtractor()
    try:
        parser.feed(html.unescape(str(value)))
        return re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()
    except Exception:
        return re.sub(r'<[^>]+>', ' ', str(value)).strip()


def normalize_list(value):
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r'[\n,;]+', str(value or ''))
    seen, out = set(), []
    for item in raw:
        clean = str(item).strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def profile_from_row(row):
    d = row_to_dict(row) or {}
    for key in ['target_roles', 'skills', 'locations', 'exclude_keywords']:
        try:
            d[key] = json.loads(d.get(key) or '[]')
        except json.JSONDecodeError:
            d[key] = []
    for key in ['prefer_remote', 'allow_hybrid', 'include_europe_remote', 'jobicy_enabled', 'arbeitnow_enabled']:
        d[key] = bool(d.get(key))
    return d


def get_profile_db(conn):
    return profile_from_row(conn.execute('SELECT * FROM job_search_profile WHERE id = 1').fetchone())


def infer_level(title):
    t = (title or '').casefold()
    if 'senior director' in t:
        return 'Senior Director'
    if 'director' in t:
        return 'Director'
    if 'head of' in t or t.startswith('head '):
        return 'Head of'
    if 'principal' in t:
        return 'Principal'
    if any(x in t for x in ['senior', 'lead', 'manager']):
        return 'Senior / Lead'
    return 'Andere'


def infer_work_model(job):
    text = f"{job.get('title','')} {job.get('location','')} {job.get('description','')}".casefold()
    if job.get('remote') is True or any(x in text for x in ['fully remote', 'remote-first', '100% remote', 'remote role']):
        return 'Remote'
    if any(x in text for x in ['hybrid', 'home office', 'homeoffice', 'remote days', 'work from home']):
        return 'Hybrid'
    return 'Onsite' if any(x in text for x in ['on-site', 'onsite', 'office-based']) else ''


def score_job(job, profile):
    title = (job.get('title') or '').casefold()
    location = (job.get('location') or '').casefold()
    desc = (job.get('description') or job.get('excerpt') or '').casefold()
    full = f'{title} {location} {desc}'
    score = 0
    reasons = []
    matched = []

    # Seniority / leadership signal.
    if any(x in title for x in ['head of', 'director', 'principal']):
        score += 28
        reasons.append('Ziel-Seniorität: Head / Director / Principal')
    elif any(x in title for x in ['vice president', ' vp ', 'vp,', 'vp -']):
        score += 20
        reasons.append('Executive / VP-Level')
    elif any(x in title for x in ['senior manager', 'engineering manager', 'technical lead', 'technology lead']):
        score += 13
        reasons.append('Senior Leadership-Level')
    elif 'manager' in title or 'lead' in title:
        score += 7

    if any(x in title for x in ['junior', 'intern', 'internship', 'graduate', 'trainee', 'entry level']):
        score -= 55
        reasons.append('Zu niedriges Senioritätslevel')

    # Target-role phrase affinity.
    role_hits = []
    for role in profile.get('target_roles', []):
        r = role.casefold()
        # reward meaningful multi-word overlap rather than demanding exact title equality
        tokens = [tok for tok in re.findall(r'[a-zäöü0-9+#.-]+', r) if len(tok) > 2 and tok not in {'and', 'the', 'senior'}]
        overlap = sum(1 for tok in tokens if tok in title)
        if tokens and overlap >= max(2, len(tokens) // 2):
            role_hits.append(role)
    if role_hits:
        boost = min(24, 8 + len(role_hits) * 4)
        score += boost
        reasons.append('Titel passt zu deinem Zielprofil')
        matched.extend(role_hits[:3])

    # Domain keywords in title carry more weight.
    domain_terms = [
        'technology', 'technical', 'engineering', 'platform', 'operations', 'reliability', 'sre', 'cloud',
        'devops', 'security', 'infrastructure', 'program', 'transformation', 'ai', 'automation'
    ]
    domain_hits = [term for term in domain_terms if term in title]
    if domain_hits:
        score += min(24, len(domain_hits) * 4)
        reasons.append('Technischer Leadership-Fokus')
        matched.extend(domain_hits[:5])

    # CV skill affinity in title/description.
    skill_hits = []
    for skill in profile.get('skills', []):
        s = skill.casefold()
        if s and s in full:
            skill_hits.append(skill)
    if skill_hits:
        score += min(22, len(skill_hits) * 2)
        reasons.append(f'{min(len(skill_hits), 9)} relevante Profil-/Skill-Treffer')
        matched.extend(skill_hits[:8])

    # Location and work model.
    loc_hits = [loc for loc in profile.get('locations', []) if loc.casefold() in location]
    switzerland = any(x in location for x in ['switzerland', 'schweiz', 'suisse', 'svizzera', 'zürich', 'zurich', 'zug', 'luzern', 'lucerne', 'bern', 'basel', 'lugano'])
    europe_remote = job.get('remote') is True and (not location or any(x in location for x in ['europe', 'emea', 'anywhere', 'worldwide', 'remote']))
    if loc_hits:
        score += 18
        reasons.append('Bevorzugter Standort')
        matched.extend(loc_hits[:2])
    elif switzerland:
        score += 14
        reasons.append('Schweiz')
    elif europe_remote and profile.get('include_europe_remote'):
        score += 10
        reasons.append('Remote in Europa / EMEA')
    elif location and not job.get('remote'):
        score -= 18

    if job.get('remote') is True and profile.get('prefer_remote'):
        score += 8
        reasons.append('Remote-kompatibel')
    else:
        model = infer_work_model(job)
        if model == 'Hybrid' and profile.get('allow_hybrid'):
            score += 5
            reasons.append('Hybrid-kompatibel')
        if model == 'Onsite' and profile.get('prefer_remote'):
            score -= 8

    # Salary is a bonus/penalty only when CHF data is actually available.
    currency = (job.get('salary_currency') or '').upper()
    smin, smax = job.get('salary_min'), job.get('salary_max')
    target_salary = int(profile.get('min_salary_chf') or 0)
    if currency == 'CHF' and target_salary and (smin is not None or smax is not None):
        upper = smax if smax is not None else smin
        lower = smin if smin is not None else smax
        if upper is not None and upper < target_salary:
            score -= 18
            reasons.append('Ausgeschriebenes CHF-Gehalt unter deiner Untergrenze')
        elif lower is not None and lower >= target_salary:
            score += 10
            reasons.append('Ausgeschriebenes Gehalt erreicht Zielbereich')

    # Explicit exclusions.
    exclude_title = [x for x in profile.get('exclude_keywords', []) if x.casefold() in title]
    if exclude_title:
        score -= 45
        reasons.append('Titel enthält Ausschlussbegriff')
    elif any(x.casefold() in desc for x in profile.get('exclude_keywords', [])):
        score -= 5

    score = max(0, min(100, round(score)))
    if score >= 82:
        label = 'Sehr starker Match'
    elif score >= 70:
        label = 'Starker Match'
    elif score >= 58:
        label = 'Prüfen'
    else:
        label = 'Randtreffer'
    # De-duplicate matched terms while preserving order.
    seen = set()
    matched_unique = []
    for term in matched:
        key = str(term).casefold()
        if key not in seen:
            seen.add(key)
            matched_unique.append(term)
    return score, label, reasons[:6], matched_unique[:10]


def http_json(url, timeout=15):
    req = Request(url, headers={
        'User-Agent': 'BewerbungsTrackerSchweiz/2.0 (personal local job discovery)',
        'Accept': 'application/json'
    })
    with urlopen(req, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or 'utf-8'
        return json.loads(response.read().decode(charset))


def normalize_pub_date(value):
    if value in (None, ''):
        return ''
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        except Exception:
            return str(value)
    return str(value)


def fetch_jobicy(profile):
    if not profile.get('jobicy_enabled'):
        return [], None
    urls = [
        'https://jobicy.com/api/v2/remote-jobs?' + urlencode({'count': 200, 'geo': 'switzerland'}),
    ]
    if profile.get('include_europe_remote'):
        for industry in ['engineering', 'management', 'project-management']:
            urls.append('https://jobicy.com/api/v2/remote-jobs?' + urlencode({'count': 200, 'geo': 'europe', 'industry': industry}))
    jobs = []
    seen = set()
    for url in urls:
        data = http_json(url)
        for item in (data.get('jobs') or []):
            external_id = str(item.get('id') or item.get('jobSlug') or item.get('url') or '')
            if not external_id or external_id in seen:
                continue
            seen.add(external_id)
            desc = strip_html(item.get('jobDescription') or '')
            jobs.append({
                'source': 'Jobicy', 'external_id': external_id,
                'company': str(item.get('companyName') or '').strip(),
                'title': str(item.get('jobTitle') or '').strip(),
                'location': str(item.get('jobGeo') or '').strip(),
                'remote': True,
                'job_url': str(item.get('url') or '').strip(),
                'description': desc,
                'excerpt': strip_html(item.get('jobExcerpt') or '')[:800],
                'published_at': normalize_pub_date(item.get('pubDate')),
                'salary_min': item.get('salaryMin'), 'salary_max': item.get('salaryMax'),
                'salary_currency': str(item.get('salaryCurrency') or '').strip(),
                'salary_period': str(item.get('salaryPeriod') or '').strip(),
            })
    return jobs, None


def fetch_arbeitnow(profile):
    if not profile.get('arbeitnow_enabled'):
        return [], None
    jobs = []
    seen = set()
    for page in range(1, 6):
        data = http_json('https://www.arbeitnow.com/api/job-board-api?' + urlencode({'page': page}))
        page_jobs = data.get('data') or []
        if not page_jobs:
            break
        for item in page_jobs:
            external_id = str(item.get('slug') or item.get('url') or '')
            if not external_id or external_id in seen:
                continue
            seen.add(external_id)
            jobs.append({
                'source': 'Arbeitnow', 'external_id': external_id,
                'company': str(item.get('company_name') or '').strip(),
                'title': str(item.get('title') or '').strip(),
                'location': str(item.get('location') or '').strip(),
                'remote': bool(item.get('remote')),
                'job_url': str(item.get('url') or '').strip(),
                'description': strip_html(item.get('description') or ''),
                'excerpt': '',
                'published_at': normalize_pub_date(item.get('created_at')),
                'salary_min': None, 'salary_max': None, 'salary_currency': '', 'salary_period': '',
            })
    return jobs, None


def fetch_remotive(profile, config=None, source_name='Remotive'):
    data = http_json('https://remotive.com/api/remote-jobs')
    jobs = []
    for item in (data.get('jobs') or []):
        external_id = str(item.get('id') or item.get('url') or '')
        if not external_id:
            continue
        jobs.append({
            'source': source_name, 'external_id': external_id,
            'company': str(item.get('company_name') or '').strip(),
            'title': str(item.get('title') or '').strip(),
            'location': str(item.get('candidate_required_location') or '').strip(),
            'remote': True,
            'job_url': str(item.get('url') or '').strip(),
            'description': strip_html(item.get('description') or ''),
            'excerpt': strip_html(item.get('description') or '')[:800],
            'published_at': normalize_pub_date(item.get('publication_date')),
            'salary_min': None, 'salary_max': None,
            'salary_currency': '', 'salary_period': '',
        })
    return jobs, None


def fetch_greenhouse(profile, config, source_name):
    token = str((config or {}).get('board_token') or '').strip()
    if not token:
        raise ValueError('Greenhouse: board_token fehlt.')
    url = f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true'
    data = http_json(url)
    jobs = []
    for item in (data.get('jobs') or []):
        external_id = str(item.get('id') or item.get('absolute_url') or '')
        if not external_id:
            continue
        loc = item.get('location') or {}
        jobs.append({
            'source': source_name, 'external_id': external_id,
            'company': str((config or {}).get('company') or source_name).strip(),
            'title': str(item.get('title') or '').strip(),
            'location': str(loc.get('name') or '').strip() if isinstance(loc, dict) else str(loc),
            'remote': None,
            'job_url': str(item.get('absolute_url') or '').strip(),
            'description': strip_html(item.get('content') or ''),
            'excerpt': strip_html(item.get('content') or '')[:800],
            'published_at': normalize_pub_date(item.get('updated_at')),
            'salary_min': None, 'salary_max': None, 'salary_currency': '', 'salary_period': '',
        })
    return jobs, None


def fetch_lever(profile, config, source_name):
    site = str((config or {}).get('site') or '').strip()
    region = str((config or {}).get('region') or 'global').strip().lower()
    if not site:
        raise ValueError('Lever: site fehlt.')
    base = 'https://api.eu.lever.co/v0/postings' if region == 'eu' else 'https://api.lever.co/v0/postings'
    data = http_json(f'{base}/{site}?mode=json')
    jobs = []
    for item in (data or []):
        external_id = str(item.get('id') or item.get('hostedUrl') or '')
        if not external_id:
            continue
        cats = item.get('categories') or {}
        loc = cats.get('location') if isinstance(cats, dict) else ''
        desc = ' '.join([strip_html(item.get('descriptionPlain') or item.get('description') or ''), strip_html(item.get('additionalPlain') or item.get('additional') or '')]).strip()
        jobs.append({
            'source': source_name, 'external_id': external_id,
            'company': str((config or {}).get('company') or source_name).strip(),
            'title': str(item.get('text') or '').strip(),
            'location': str(loc or '').strip(),
            'remote': None,
            'job_url': str(item.get('hostedUrl') or item.get('applyUrl') or '').strip(),
            'description': desc,
            'excerpt': desc[:800],
            'published_at': normalize_pub_date(item.get('createdAt')),
            'salary_min': None, 'salary_max': None, 'salary_currency': '', 'salary_period': '',
        })
    return jobs, None


def fetch_rss(profile, config, source_name):
    url = str((config or {}).get('url') or '').strip()
    if not url:
        raise ValueError('RSS: URL fehlt.')
    req = Request(url, headers={'User-Agent':'BewerbungsTrackerSchweiz/3.0','Accept':'application/rss+xml, application/atom+xml, application/xml, text/xml'})
    with urlopen(req, timeout=20) as response:
        raw = response.read()
    root = ET.fromstring(raw)
    jobs = []
    items = root.findall('.//item')
    if items:
        for item in items:
            title = (item.findtext('title') or '').strip()
            link = (item.findtext('link') or '').strip()
            guid = (item.findtext('guid') or link or title).strip()
            desc = strip_html(item.findtext('description') or '')
            pub = item.findtext('pubDate') or ''
            jobs.append({'source':source_name,'external_id':guid,'company':str((config or {}).get('company') or source_name),'title':title,'location':'','remote':None,'job_url':link,'description':desc,'excerpt':desc[:800],'published_at':pub,'salary_min':None,'salary_max':None,'salary_currency':'','salary_period':''})
    else:
        ns='{http://www.w3.org/2005/Atom}'
        for entry in root.findall(f'.//{ns}entry'):
            title=(entry.findtext(f'{ns}title') or '').strip()
            link=''
            le=entry.find(f'{ns}link')
            if le is not None: link=(le.attrib.get('href') or '').strip()
            eid=(entry.findtext(f'{ns}id') or link or title).strip()
            desc=strip_html(entry.findtext(f'{ns}summary') or entry.findtext(f'{ns}content') or '')
            pub=entry.findtext(f'{ns}updated') or entry.findtext(f'{ns}published') or ''
            jobs.append({'source':source_name,'external_id':eid,'company':str((config or {}).get('company') or source_name),'title':title,'location':'','remote':None,'job_url':link,'description':desc,'excerpt':desc[:800],'published_at':pub,'salary_min':None,'salary_max':None,'salary_currency':'','salary_period':''})
    return jobs, None


def fetch_source(source, profile):
    stype = source['source_type']
    try:
        config = json.loads(source.get('config_json') or '{}')
    except Exception:
        config = {}
    name = source['name']
    if stype == 'arbeitnow':
        jobs, err = fetch_arbeitnow(profile)
    elif stype == 'jobicy':
        jobs, err = fetch_jobicy(profile)
    elif stype == 'remotive':
        jobs, err = fetch_remotive(profile, config, name)
    elif stype == 'greenhouse':
        jobs, err = fetch_greenhouse(profile, config, name)
    elif stype == 'lever':
        jobs, err = fetch_lever(profile, config, name)
    elif stype == 'rss':
        jobs, err = fetch_rss(profile, config, name)
    else:
        raise ValueError(f'Unbekannter Quellentyp: {stype}')
    for j in jobs:
        j['source'] = name
    return jobs, err


def run_scout_search():
    started = utc_now_iso()
    with db_connect() as conn:
        profile = get_profile_db(conn)
        cur = conn.execute('INSERT INTO scout_runs (started_at,status) VALUES (?,?)', (started, 'running'))
        run_id = cur.lastrowid
        conn.execute('UPDATE discovered_jobs SET is_new = 0')

    with db_connect() as conn:
        sources = [row_to_dict(r) for r in conn.execute('SELECT * FROM job_sources WHERE enabled=1 ORDER BY id').fetchall()]
    all_jobs, errors = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(6, len(sources)))) as pool:
        future_map = {pool.submit(fetch_source, source, profile): source['name'] for source in sources}
        for future, name in [(f, future_map[f]) for f in future_map]:
            try:
                jobs, _ = future.result(timeout=60)
                all_jobs.extend(jobs)
            except Exception as exc:
                errors.append({'source': name, 'error': str(exc)[:400]})

    # De-duplicate across providers by normalized URL first, then company/title/location signature.
    unique, keys = [], set()
    for job in all_jobs:
        url_key = (job.get('job_url') or '').strip().casefold()
        sig = '|'.join([(job.get('company') or '').strip().casefold(), (job.get('title') or '').strip().casefold(), (job.get('location') or '').strip().casefold()])
        key = url_key or sig
        if key and key in keys:
            continue
        if key:
            keys.add(key)
        unique.append(job)

    matched_count = 0
    new_count = 0
    seen_at = utc_now_iso()
    min_score = int(profile.get('min_score') or 0)
    with db_connect() as conn:
        for job in unique:
            score, label, reasons, matched_terms = score_job(job, profile)
            if score < min_score:
                continue
            matched_count += 1
            existing = conn.execute('SELECT id, review_state FROM discovered_jobs WHERE source = ? AND external_id = ?', (job['source'], job['external_id'])).fetchone()
            values = (
                job['company'], job['title'], job['location'], 1 if job.get('remote') is True else 0 if job.get('remote') is False else None,
                job['job_url'], job['description'], job['excerpt'], job['published_at'], job.get('salary_min'), job.get('salary_max'),
                job.get('salary_currency') or '', job.get('salary_period') or '', score, label,
                json.dumps(reasons, ensure_ascii=False), json.dumps(matched_terms, ensure_ascii=False), seen_at
            )
            if existing:
                conn.execute('''UPDATE discovered_jobs SET company=?,title=?,location=?,remote=?,job_url=?,description=?,excerpt=?,published_at=?,
                    salary_min=?,salary_max=?,salary_currency=?,salary_period=?,match_score=?,match_label=?,match_reasons=?,matched_terms=?,last_seen=?
                    WHERE id=?''', values + (existing['id'],))
            else:
                conn.execute('''INSERT INTO discovered_jobs
                    (source,external_id,company,title,location,remote,job_url,description,excerpt,published_at,salary_min,salary_max,
                     salary_currency,salary_period,match_score,match_label,match_reasons,matched_terms,review_state,is_new,first_seen,last_seen)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'Neu',1,?,?)''', (
                    job['source'], job['external_id'], job['company'], job['title'], job['location'],
                    1 if job.get('remote') is True else 0 if job.get('remote') is False else None,
                    job['job_url'], job['description'], job['excerpt'], job['published_at'], job.get('salary_min'), job.get('salary_max'),
                    job.get('salary_currency') or '', job.get('salary_period') or '', score, label,
                    json.dumps(reasons, ensure_ascii=False), json.dumps(matched_terms, ensure_ascii=False), seen_at, seen_at
                ))
                new_count += 1
            # If the same posting is already tracked manually, do not present it as a new opportunity.
            if job.get('job_url'):
                app_row = conn.execute("SELECT id FROM applications WHERE job_url = ? AND job_url <> '' LIMIT 1", (job['job_url'],)).fetchone()
                if app_row:
                    conn.execute("UPDATE discovered_jobs SET review_state='Übernommen',is_new=0,application_id=? WHERE source=? AND external_id=?",
                                 (app_row['id'], job['source'], job['external_id']))
                    if not existing and new_count > 0:
                        new_count -= 1
        finished = utc_now_iso()
        status = 'ok' if not errors else ('partial' if matched_count or unique else 'error')
        conn.execute('''UPDATE scout_runs SET finished_at=?,status=?,fetched_count=?,matched_count=?,new_count=?,errors=? WHERE id=?''',
                     (finished, status, len(unique), matched_count, new_count, json.dumps(errors, ensure_ascii=False), run_id))

    return {
        'run_id': run_id, 'status': status, 'fetched_count': len(unique), 'matched_count': matched_count,
        'new_count': new_count, 'errors': errors, 'started_at': started, 'finished_at': finished
    }


def parse_job_row(row):
    d = row_to_dict(row)
    for key in ['match_reasons', 'matched_terms']:
        try:
            d[key] = json.loads(d.get(key) or '[]')
        except json.JSONDecodeError:
            d[key] = []
    d['remote'] = None if d.get('remote') is None else bool(d.get('remote'))
    d['is_new'] = bool(d.get('is_new'))
    return d


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed = urlparse(path)
        clean = parsed.path.lstrip('/')
        if not clean:
            clean = 'index.html'
        return str(STATIC_DIR / clean)

    def log_message(self, format, *args):
        pass

    def _send_json(self, payload, status=200, headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        if headers:
            for key, value in headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get('Content-Length', '0') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            return json.loads(raw.decode('utf-8'))
        except json.JSONDecodeError:
            raise ValueError('Ungültige JSON-Daten')

    def _not_found(self):
        self._send_json({'error': 'Nicht gefunden'}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith('/api/'):
            return super().do_GET()
        try:
            if path == '/api/applications':
                return self.get_applications(parse_qs(parsed.query))
            if path == '/api/dashboard':
                return self.get_dashboard()
            if path == '/api/export':
                return self.export_data()
            if path == '/api/scout/jobs':
                return self.get_scout_jobs(parse_qs(parsed.query))
            if path == '/api/scout/profile':
                return self.get_scout_profile()
            if path == '/api/scout/summary':
                return self.get_scout_summary()
            if path == '/api/scout/sources':
                return self.get_sources()
            parts = [p for p in path.split('/') if p]
            if len(parts) == 3 and parts[:2] == ['api', 'applications']:
                return self.get_application(int(parts[2]))
            if len(parts) == 4 and parts[:2] == ['api', 'applications'] and parts[3] == 'events':
                return self.get_events(int(parts[2]))
            self._not_found()
        except Exception as exc:
            self._send_json({'error': str(exc)}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == '/api/applications':
                return self.create_application(self._read_json())
            if path == '/api/import':
                return self.import_data(self._read_json())
            if path == '/api/scout/search':
                return self._send_json(run_scout_search())
            if path == '/api/scout/sources':
                return self.create_source(self._read_json())
            parts = [p for p in path.split('/') if p]
            if len(parts) == 4 and parts[:2] == ['api', 'applications'] and parts[3] == 'events':
                return self.create_event(int(parts[2]), self._read_json())
            if len(parts) == 4 and parts[:2] == ['api', 'scout'] and parts[2] == 'jobs' and parts[3].isdigit():
                self._not_found()
                return
            if len(parts) == 5 and parts[:3] == ['api', 'scout', 'jobs'] and parts[4] == 'convert':
                return self.convert_job(int(parts[3]))
            self._not_found()
        except ValueError as exc:
            self._send_json({'error': str(exc)}, 400)
        except Exception as exc:
            self._send_json({'error': str(exc)}, 500)

    def do_PUT(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split('/') if p]
        try:
            if parsed.path == '/api/scout/profile':
                return self.update_scout_profile(self._read_json())
            if len(parts) == 4 and parts[:3] == ['api', 'scout', 'sources']:
                return self.update_source(int(parts[3]), self._read_json())
            if len(parts) == 3 and parts[:2] == ['api', 'applications']:
                return self.update_application(int(parts[2]), self._read_json())
            if len(parts) == 5 and parts[:3] == ['api', 'scout', 'jobs'] and parts[4] == 'state':
                return self.update_job_state(int(parts[3]), self._read_json())
            self._not_found()
        except ValueError as exc:
            self._send_json({'error': str(exc)}, 400)
        except Exception as exc:
            self._send_json({'error': str(exc)}, 500)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split('/') if p]
        try:
            if len(parts) == 3 and parts[:2] == ['api', 'applications']:
                return self.delete_application(int(parts[2]))
            if len(parts) == 3 and parts[:2] == ['api', 'events']:
                return self.delete_event(int(parts[2]))
            if len(parts) == 4 and parts[:3] == ['api', 'scout', 'sources']:
                return self.delete_source(int(parts[3]))
            self._not_found()
        except Exception as exc:
            self._send_json({'error': str(exc)}, 500)

    # ---- Applications ----
    def get_applications(self, query):
        clauses, params = [], []
        status = (query.get('status') or [''])[0].strip()
        priority = (query.get('priority') or [''])[0].strip()
        search = (query.get('search') or [''])[0].strip()
        if status:
            clauses.append('status = ?'); params.append(status)
        if priority:
            clauses.append('priority = ?'); params.append(priority)
        if search:
            clauses.append('(company LIKE ? OR position LIKE ? OR location LIKE ? OR contact_name LIKE ?)')
            wildcard = f'%{search}%'; params.extend([wildcard] * 4)
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        sql = f'''SELECT *, (SELECT COUNT(*) FROM events e WHERE e.application_id = applications.id) AS event_count
            FROM applications{where}
            ORDER BY CASE priority WHEN 'A - Sehr interessant' THEN 1 WHEN 'B - Interessant' THEN 2 ELSE 3 END,
                     CASE WHEN follow_up_date <> '' THEN follow_up_date ELSE '9999-12-31' END, updated_at DESC'''
        with db_connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        self._send_json([row_to_dict(r) for r in rows])

    def get_application(self, app_id):
        with db_connect() as conn:
            row = conn.execute('SELECT * FROM applications WHERE id = ?', (app_id,)).fetchone()
        if not row:
            return self._not_found()
        self._send_json(row_to_dict(row))

    def create_application(self, payload):
        company = str(payload.get('company', '')).strip()
        position = str(payload.get('position', '')).strip()
        if not company or not position:
            raise ValueError('Unternehmen und Position sind Pflichtfelder.')
        values = {field: str(payload.get(field, '') or '').strip() for field in APPLICATION_FIELDS}
        values['company'] = company; values['position'] = position
        values['status'] = values['status'] or 'Vorbereitung'; values['priority'] = values['priority'] or 'B - Interessant'
        ts = now_iso(); cols = APPLICATION_FIELDS + ['created_at', 'updated_at']
        params = [values[c] for c in APPLICATION_FIELDS] + [ts, ts]
        with db_connect() as conn:
            cur = conn.execute(f"INSERT INTO applications ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", params)
            app_id = cur.lastrowid
            if values['applied_date']:
                conn.execute('''INSERT INTO events (application_id,event_date,event_type,person,note,next_step,follow_up_date,created_at)
                    VALUES (?,?,?,?,?,?,?,?)''', (app_id, values['applied_date'], 'Bewerbung versendet', '', 'Bewerbung eingereicht.', values['next_action'], values['follow_up_date'], ts))
        return self.get_application(app_id)

    def update_application(self, app_id, payload):
        with db_connect() as conn:
            if not conn.execute('SELECT id FROM applications WHERE id = ?', (app_id,)).fetchone():
                return self._not_found()
            updates, params = [], []
            for field in APPLICATION_FIELDS:
                if field in payload:
                    updates.append(f'{field} = ?'); params.append(str(payload.get(field, '') or '').strip())
            if not updates:
                raise ValueError('Keine Änderungen übermittelt.')
            updates.append('updated_at = ?'); params.append(now_iso()); params.append(app_id)
            conn.execute(f"UPDATE applications SET {', '.join(updates)} WHERE id = ?", params)
        return self.get_application(app_id)

    def delete_application(self, app_id):
        with db_connect() as conn:
            cur = conn.execute('DELETE FROM applications WHERE id = ?', (app_id,))
        if cur.rowcount == 0:
            return self._not_found()
        self._send_json({'ok': True})

    def get_events(self, app_id):
        with db_connect() as conn:
            rows = conn.execute('SELECT * FROM events WHERE application_id = ? ORDER BY event_date DESC, id DESC', (app_id,)).fetchall()
        self._send_json([row_to_dict(r) for r in rows])

    def create_event(self, app_id, payload):
        with db_connect() as conn:
            if not conn.execute('SELECT * FROM applications WHERE id = ?', (app_id,)).fetchone():
                return self._not_found()
            values = {field: str(payload.get(field, '') or '').strip() for field in EVENT_FIELDS}
            values['event_date'] = values['event_date'] or datetime.now().date().isoformat()
            values['event_type'] = values['event_type'] or 'Sonstiges'
            ts = now_iso()
            conn.execute('''INSERT INTO events (application_id,event_date,event_type,person,note,next_step,follow_up_date,created_at)
                VALUES (?,?,?,?,?,?,?,?)''', (app_id, values['event_date'], values['event_type'], values['person'], values['note'], values['next_step'], values['follow_up_date'], ts))
            update_parts = ['updated_at = ?']; update_params = [ts]
            if values['event_type'] != 'Bewerbung versendet':
                update_parts.append('last_response = ?'); update_params.append(values['event_date'])
            if values['next_step']:
                update_parts.append('next_action = ?'); update_params.append(values['next_step'])
            if values['follow_up_date']:
                update_parts.append('follow_up_date = ?'); update_params.append(values['follow_up_date'])
            status_map = {'Eingangsbestätigung':'Eingangsbestätigung','HR-Screening':'Screening / HR','Interview':'Interview 1','Case / Assessment':'Case / Assessment','Angebot':'Angebot','Absage':'Abgelehnt'}
            if status_map.get(values['event_type']):
                update_parts.append('status = ?'); update_params.append(status_map[values['event_type']])
            update_params.append(app_id)
            conn.execute(f"UPDATE applications SET {', '.join(update_parts)} WHERE id = ?", update_params)
        return self.get_events(app_id)

    def delete_event(self, event_id):
        with db_connect() as conn:
            cur = conn.execute('DELETE FROM events WHERE id = ?', (event_id,))
        if cur.rowcount == 0:
            return self._not_found()
        self._send_json({'ok': True})

    def get_dashboard(self):
        today = datetime.now().date().isoformat()
        with db_connect() as conn:
            total = conn.execute('SELECT COUNT(*) FROM applications').fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM applications WHERE status NOT IN ('Abgelehnt','Zurückgezogen')").fetchone()[0]
            interviews = conn.execute("SELECT COUNT(*) FROM applications WHERE status IN ('Interview 1','Interview 2','Case / Assessment','Final Interview')").fetchone()[0]
            offers = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'Angebot'").fetchone()[0]
            rejected = conn.execute("SELECT COUNT(*) FROM applications WHERE status = 'Abgelehnt'").fetchone()[0]
            followups = conn.execute("SELECT COUNT(*) FROM applications WHERE follow_up_date <> '' AND follow_up_date <= ? AND status NOT IN ('Abgelehnt','Zurückgezogen')", (today,)).fetchone()[0]
            responses = conn.execute("SELECT COUNT(*) FROM applications WHERE last_response <> ''").fetchone()[0]
            status_rows = conn.execute('SELECT status, COUNT(*) AS count FROM applications GROUP BY status ORDER BY count DESC').fetchall()
            due_rows = conn.execute('''SELECT id,company,position,follow_up_date,next_action,status FROM applications
                WHERE follow_up_date <> '' AND follow_up_date <= ? AND status NOT IN ('Abgelehnt','Zurückgezogen') ORDER BY follow_up_date ASC LIMIT 8''', (today,)).fetchall()
            scout_new = conn.execute("SELECT COUNT(*) FROM discovered_jobs WHERE is_new=1 AND review_state='Neu'").fetchone()[0]
            scout_strong = conn.execute("SELECT COUNT(*) FROM discovered_jobs WHERE match_score>=70 AND review_state NOT IN ('Ignoriert','Übernommen')").fetchone()[0]
        self._send_json({'total':total,'active':active,'interviews':interviews,'offers':offers,'rejected':rejected,'followups_due':followups,
            'response_rate':(responses/total if total else 0),'status_counts':[row_to_dict(r) for r in status_rows],
            'followups':[row_to_dict(r) for r in due_rows], 'scout_new': scout_new, 'scout_strong': scout_strong})

    # ---- Job Scout ----
    def get_scout_profile(self):
        with db_connect() as conn:
            profile = get_profile_db(conn)
        self._send_json(profile)

    def update_scout_profile(self, payload):
        profile = {
            'target_roles': normalize_list(payload.get('target_roles', [])),
            'skills': normalize_list(payload.get('skills', [])),
            'locations': normalize_list(payload.get('locations', [])),
            'exclude_keywords': normalize_list(payload.get('exclude_keywords', [])),
            'min_score': max(0, min(100, int(payload.get('min_score', DEFAULT_PROFILE['min_score']) or DEFAULT_PROFILE['min_score']))),
            'min_salary_chf': max(0, int(payload.get('min_salary_chf', DEFAULT_PROFILE['min_salary_chf']) or 0)),
            'prefer_remote': 1 if payload.get('prefer_remote') else 0,
            'allow_hybrid': 1 if payload.get('allow_hybrid') else 0,
            'include_europe_remote': 1 if payload.get('include_europe_remote') else 0,
            'auto_hours': max(0, min(168, int(payload.get('auto_hours', 12) or 0))),
            'jobicy_enabled': 1 if payload.get('jobicy_enabled') else 0,
            'arbeitnow_enabled': 1 if payload.get('arbeitnow_enabled') else 0,
        }
        if not profile['target_roles']:
            raise ValueError('Mindestens eine Zielrolle ist erforderlich.')
        with db_connect() as conn:
            conn.execute('''UPDATE job_search_profile SET target_roles=?,skills=?,locations=?,exclude_keywords=?,min_score=?,min_salary_chf=?,
                prefer_remote=?,allow_hybrid=?,include_europe_remote=?,auto_hours=?,jobicy_enabled=?,arbeitnow_enabled=?,updated_at=? WHERE id=1''', (
                json.dumps(profile['target_roles'], ensure_ascii=False), json.dumps(profile['skills'], ensure_ascii=False),
                json.dumps(profile['locations'], ensure_ascii=False), json.dumps(profile['exclude_keywords'], ensure_ascii=False),
                profile['min_score'], profile['min_salary_chf'], profile['prefer_remote'], profile['allow_hybrid'], profile['include_europe_remote'],
                profile['auto_hours'], profile['jobicy_enabled'], profile['arbeitnow_enabled'], now_iso()
            ))
            # Re-score already known jobs immediately against the new profile.
            rows = conn.execute('SELECT * FROM discovered_jobs').fetchall()
            for r in rows:
                j = parse_job_row(r)
                score, label, reasons, terms = score_job(j, profile)
                conn.execute('UPDATE discovered_jobs SET match_score=?,match_label=?,match_reasons=?,matched_terms=? WHERE id=?',
                    (score, label, json.dumps(reasons, ensure_ascii=False), json.dumps(terms, ensure_ascii=False), j['id']))
        return self.get_scout_profile()

    def get_scout_jobs(self, query):
        clauses, params = [], []
        state = (query.get('state') or [''])[0].strip()
        search = (query.get('search') or [''])[0].strip()
        new_only = (query.get('new_only') or ['0'])[0] == '1'
        try:
            min_score = int((query.get('min_score') or ['0'])[0] or 0)
        except ValueError:
            min_score = 0
        if state:
            clauses.append('review_state = ?'); params.append(state)
        else:
            clauses.append("review_state <> 'Ignoriert'")
        if new_only:
            clauses.append('is_new = 1')
        if min_score:
            clauses.append('match_score >= ?'); params.append(min_score)
        if search:
            wildcard = f'%{search}%'
            clauses.append('(company LIKE ? OR title LIKE ? OR location LIKE ? OR description LIKE ?)')
            params.extend([wildcard] * 4)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with db_connect() as conn:
            rows = conn.execute(f'''SELECT * FROM discovered_jobs{where}
                ORDER BY CASE review_state WHEN 'Neu' THEN 1 WHEN 'Gemerkt' THEN 2 WHEN 'Übernommen' THEN 3 ELSE 4 END,
                         is_new DESC, match_score DESC, first_seen DESC LIMIT 300''', params).fetchall()
        self._send_json([parse_job_row(r) for r in rows])

    def get_scout_summary(self):
        with db_connect() as conn:
            profile = get_profile_db(conn)
            last = conn.execute('SELECT * FROM scout_runs ORDER BY id DESC LIMIT 1').fetchone()
            counts = {
                'new': conn.execute("SELECT COUNT(*) FROM discovered_jobs WHERE is_new=1 AND review_state='Neu'").fetchone()[0],
                'strong': conn.execute("SELECT COUNT(*) FROM discovered_jobs WHERE match_score>=70 AND review_state NOT IN ('Ignoriert','Übernommen')").fetchone()[0],
                'saved': conn.execute("SELECT COUNT(*) FROM discovered_jobs WHERE review_state='Gemerkt'").fetchone()[0],
                'converted': conn.execute("SELECT COUNT(*) FROM discovered_jobs WHERE review_state='Übernommen'").fetchone()[0],
                'total': conn.execute("SELECT COUNT(*) FROM discovered_jobs WHERE review_state<>'Ignoriert'").fetchone()[0],
            }
        due = True
        last_data = row_to_dict(last) if last else None
        if last_data and last_data.get('finished_at') and profile.get('auto_hours', 0) > 0:
            try:
                last_dt = datetime.fromisoformat(last_data['finished_at'].replace('Z', '+00:00'))
                due = datetime.now(timezone.utc) - last_dt >= timedelta(hours=int(profile['auto_hours']))
            except Exception:
                due = True
        elif profile.get('auto_hours', 0) == 0:
            due = False
        if last_data:
            try: last_data['errors'] = json.loads(last_data.get('errors') or '[]')
            except json.JSONDecodeError: last_data['errors'] = []
        self._send_json({'counts': counts, 'last_run': last_data, 'auto_due': due, 'auto_hours': profile.get('auto_hours', 0)})

    def update_job_state(self, job_id, payload):
        new_state = str(payload.get('state') or '').strip()
        if new_state not in ['Neu', 'Gemerkt', 'Ignoriert', 'Übernommen']:
            raise ValueError('Ungültiger Job-Status.')
        with db_connect() as conn:
            cur = conn.execute('UPDATE discovered_jobs SET review_state=?,is_new=0 WHERE id=?', (new_state, job_id))
            if cur.rowcount == 0:
                return self._not_found()
            row = conn.execute('SELECT * FROM discovered_jobs WHERE id=?', (job_id,)).fetchone()
        self._send_json(parse_job_row(row))

    def convert_job(self, job_id):
        with db_connect() as conn:
            row = conn.execute('SELECT * FROM discovered_jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                return self._not_found()
            job = parse_job_row(row)
            if job.get('application_id'):
                existing = conn.execute('SELECT * FROM applications WHERE id=?', (job['application_id'],)).fetchone()
                if existing:
                    return self._send_json({'application': row_to_dict(existing), 'already_exists': True})
            priority = 'A - Sehr interessant' if job['match_score'] >= 80 else 'B - Interessant'
            salary = ''
            if job.get('salary_min') is not None or job.get('salary_max') is not None:
                parts = []
                if job.get('salary_min') is not None: parts.append(str(job['salary_min']))
                if job.get('salary_max') is not None: parts.append(str(job['salary_max']))
                salary = '–'.join(parts) + (f" {job.get('salary_currency')}" if job.get('salary_currency') else '')
            summary = f"Job-Scout Match {job['match_score']}/100 ({job['match_label']}). " + '; '.join(job.get('match_reasons') or [])
            ts = now_iso()
            vals = {
                'company': job['company'], 'position': job['title'], 'level': infer_level(job['title']),
                'location': job['location'], 'work_model': infer_work_model(job), 'source': f"Job Scout · {job['source']}",
                'job_url': job['job_url'], 'applied_date': '', 'status': 'Vorbereitung', 'last_response': '',
                'summary': summary, 'next_action': 'Stellenanzeige prüfen und Bewerbung vorbereiten', 'follow_up_date': '',
                'contact_name': '', 'contact_details': '', 'salary_range': salary, 'priority': priority,
                'cv_version': '', 'cover_letter': '', 'notes': f"Gefunden am {job['first_seen']} · Quelle: {job['source']}"
            }
            cols = APPLICATION_FIELDS + ['created_at', 'updated_at']
            params = [vals[c] for c in APPLICATION_FIELDS] + [ts, ts]
            cur = conn.execute(f"INSERT INTO applications ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", params)
            app_id = cur.lastrowid
            conn.execute("UPDATE discovered_jobs SET review_state='Übernommen',is_new=0,application_id=? WHERE id=?", (app_id, job_id))
            application = conn.execute('SELECT * FROM applications WHERE id=?', (app_id,)).fetchone()
        self._send_json({'application': row_to_dict(application), 'already_exists': False})

    # ---- Job sources ----
    def get_sources(self):
        with db_connect() as conn:
            rows = conn.execute('SELECT * FROM job_sources ORDER BY id').fetchall()
        result=[]
        for r in rows:
            d=row_to_dict(r)
            try: d['config']=json.loads(d.get('config_json') or '{}')
            except Exception: d['config']={}
            d['enabled']=bool(d.get('enabled'))
            result.append(d)
        self._send_json(result)

    def create_source(self, payload):
        name=str(payload.get('name') or '').strip()
        stype=str(payload.get('source_type') or '').strip().lower()
        if not name or stype not in ['arbeitnow','jobicy','remotive','greenhouse','lever','rss']:
            raise ValueError('Name und gültiger Quellentyp sind erforderlich.')
        config=payload.get('config') if isinstance(payload.get('config'),dict) else {}
        ts=now_iso()
        with db_connect() as conn:
            cur=conn.execute('INSERT INTO job_sources (name,source_type,config_json,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                             (name,stype,json.dumps(config,ensure_ascii=False),1 if payload.get('enabled',True) else 0,ts,ts))
            row=conn.execute('SELECT * FROM job_sources WHERE id=?',(cur.lastrowid,)).fetchone()
        self._send_json(row_to_dict(row),201)

    def update_source(self, source_id, payload):
        name=str(payload.get('name') or '').strip()
        stype=str(payload.get('source_type') or '').strip().lower()
        if not name or stype not in ['arbeitnow','jobicy','remotive','greenhouse','lever','rss']:
            raise ValueError('Name und gültiger Quellentyp sind erforderlich.')
        config=payload.get('config') if isinstance(payload.get('config'),dict) else {}
        with db_connect() as conn:
            cur=conn.execute('UPDATE job_sources SET name=?,source_type=?,config_json=?,enabled=?,updated_at=? WHERE id=?',
                             (name,stype,json.dumps(config,ensure_ascii=False),1 if payload.get('enabled',True) else 0,now_iso(),source_id))
            if cur.rowcount==0: return self._not_found()
        return self.get_sources()

    def delete_source(self, source_id):
        with db_connect() as conn:
            cur=conn.execute('DELETE FROM job_sources WHERE id=?',(source_id,))
        if cur.rowcount==0: return self._not_found()
        self._send_json({'ok':True})

    # ---- Backup ----
    def export_data(self):
        with db_connect() as conn:
            apps = [row_to_dict(r) for r in conn.execute('SELECT * FROM applications ORDER BY id').fetchall()]
            events = [row_to_dict(r) for r in conn.execute('SELECT * FROM events ORDER BY id').fetchall()]
            profile = row_to_dict(conn.execute('SELECT * FROM job_search_profile WHERE id=1').fetchone())
            jobs = [row_to_dict(r) for r in conn.execute('SELECT * FROM discovered_jobs ORDER BY id').fetchall()]
            runs = [row_to_dict(r) for r in conn.execute('SELECT * FROM scout_runs ORDER BY id').fetchall()]
            sources = [row_to_dict(r) for r in conn.execute('SELECT * FROM job_sources ORDER BY id').fetchall()]
        payload = {'format':'BewerbungsTrackerBackup','version':3,'exported_at':now_iso(),'applications':apps,'events':events,
                   'job_search_profile':profile,'discovered_jobs':jobs,'scout_runs':runs,'job_sources':sources}
        filename = f"bewerbungs-tracker-backup-{datetime.now().date().isoformat()}.json"
        self._send_json(payload, 200, {'Content-Disposition': f'attachment; filename="{filename}"'})

    def import_data(self, payload):
        if payload.get('format') != 'BewerbungsTrackerBackup':
            raise ValueError('Die Datei ist kein gültiges Bewerbungs-Tracker-Backup.')
        apps = payload.get('applications', []); events = payload.get('events', [])
        if not isinstance(apps, list) or not isinstance(events, list):
            raise ValueError('Backup-Daten sind ungültig.')
        with db_connect() as conn:
            conn.execute('DELETE FROM events'); conn.execute('DELETE FROM applications')
            for a in apps:
                fields = ['id'] + APPLICATION_FIELDS + ['created_at', 'updated_at']
                conn.execute(f"INSERT INTO applications ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [a.get(f, '') for f in fields])
            for e in events:
                fields = ['id','application_id'] + EVENT_FIELDS + ['created_at']
                conn.execute(f"INSERT INTO events ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [e.get(f, '') for f in fields])
            if int(payload.get('version') or 1) >= 2:
                conn.execute('DELETE FROM discovered_jobs'); conn.execute('DELETE FROM scout_runs')
                p = payload.get('job_search_profile')
                if isinstance(p, dict):
                    fields = ['target_roles','skills','locations','exclude_keywords','min_score','min_salary_chf','prefer_remote','allow_hybrid','include_europe_remote','auto_hours','jobicy_enabled','arbeitnow_enabled','updated_at']
                    conn.execute(f"UPDATE job_search_profile SET {','.join(f'{f}=?' for f in fields)} WHERE id=1", [p.get(f, '') for f in fields])
                for j in payload.get('discovered_jobs', []):
                    fields = ['id','source','external_id','company','title','location','remote','job_url','description','excerpt','published_at','salary_min','salary_max','salary_currency','salary_period','match_score','match_label','match_reasons','matched_terms','review_state','is_new','first_seen','last_seen','application_id']
                    conn.execute(f"INSERT INTO discovered_jobs ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [j.get(f) for f in fields])
                for r in payload.get('scout_runs', []):
                    fields = ['id','started_at','finished_at','status','fetched_count','matched_count','new_count','errors']
                    conn.execute(f"INSERT INTO scout_runs ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [r.get(f) for f in fields])
                if int(payload.get('version') or 1) >= 3 and isinstance(payload.get('job_sources'), list):
                    conn.execute('DELETE FROM job_sources')
                    for src in payload.get('job_sources', []):
                        fields=['id','name','source_type','config_json','enabled','created_at','updated_at']
                        conn.execute(f"INSERT INTO job_sources ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [src.get(f) for f in fields])
        self._send_json({'ok':True,'applications':len(apps),'events':len(events)})


def open_browser():
    webbrowser.open(f'http://{HOST}:{PORT}')


def create_server(host=None, port=None):
    """Initialise data dir + database and return a bound, ready-to-serve HTTP server.

    Used by main() and by the run.py launcher so that server setup exists only once.
    Binding happens here, so OSError (e.g. port already in use) surfaces to the caller.
    """
    init_db()
    return ThreadingHTTPServer((host or HOST, PORT if port is None else port), Handler)


def main():
    server = create_server()
    print(f'Bewerbungs-Tracker + Job Scout läuft auf http://{HOST}:{PORT}')
    print('Job-Suche benötigt Internet. Deine Bewerbungsdaten bleiben lokal in data/applications.db.')
    print('Beenden mit Ctrl+C')
    if os.environ.get('APPLICATION_TRACKER_NO_BROWSER') != '1':
        threading.Timer(0.6, open_browser).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nTracker beendet.')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
