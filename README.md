# Job Assistant

A personal job discovery and application assistant built for **a single user**.

It finds senior technology leadership roles that can realistically be worked from
Switzerland, explains why each one fits, estimates what it is likely to pay, helps
fill in the application form, and tracks the applications that follow.

There are no accounts, no tenants, no dashboards and no search forms. The
application already knows the profile.

```
python3 run.py          ->  http://127.0.0.1:8765
```

---

## Install

Python 3.9 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium   # for the apply assistant
.venv/bin/python run.py
```

`run.py` migrates the database (backing it up first), creates the document
folders, starts the server on `http://127.0.0.1:8765` and opens a browser.

Playwright is optional. Without it everything works except the Apply button,
which then tells you what to install.

---

## The four screens

### Jobs (default)

One button: **Scan for new jobs**. Below it, every relevant opportunity sorted by
match score, then by recency. Each card shows:

* the score and its classification - **Excellent** (80+), **Strong** (70+), **Review**
* title, company, location, work model, posting age
* **Why it matches** - at most 5 reasons
* **Potential concerns** - at most 3
* **Estimated compensation** - a CHF range with base / bonus / equity and a confidence
* actions: Open job, Save, Apply, Analysis, Track application, Ignore

No search box, no filters, no source configuration. All of that lives in Config.

### Applications

A simple pipeline: Preparation, Applied, Screening, Interview, Final, Offer,
Rejected, Withdrawn. Each application keeps company, role, job URL, the match
analysis, the salary estimate, contact, next action and next date, notes, the
documents used, and a chronological activity timeline. Status changes write
themselves into the timeline.

### Profile

Who you are: personal and contact data, LinkedIn, nationality and work
authorisation, relocation, languages, availability and notice period,
compensation expectations, career history, technical skills, leadership profile,
target roles and target geography.

Documents (CV DE/EN, motivation letter DE/EN, certificates, employment
references) are stored **on disk** under `documents/`. SQLite keeps only the
metadata and the path - never the file itself.

### Config

Everything complicated: Job Sources, Company Watchlist, Matching, AI, Salary
Model, Application Automation, System.

---

## Job source coverage

The company watchlist is the primary discovery source; the aggregators are a
thin supplement. Each company is either scanned through a verified public
endpoint or honestly marked MANUAL.

| Source kind | How it is read | Companies |
|---|---|---|
| Greenhouse | public board API | Proton, Scandit, On |
| Lever | public postings API | SonarSource, ANYbotics |
| SmartRecruiters | public Posting API + job-ad detail | Nexthink |
| SuccessFactors career site | public server-rendered search + `itemprop="description"` | Swiss Re, SIX, Zurich Insurance, Adnovum |
| Phenom career site | public unauthenticated careers endpoint, filtered by country | Roche, ABB |
| Amazon Jobs | `amazon.jobs` public search JSON | Amazon Web Services / AWS |
| schema.org JobPosting | JSON-LD in a public, server-rendered career page | available for any company that publishes it |
| RSS / Atom | public feed | available |

Everything above is public and unauthenticated. Nothing logs in, works around a
CAPTCHA or an anti-bot check, or scrapes LinkedIn or jobs.ch.

### Why a company stays MANUAL

MANUAL is a real answer, not a gap to paper over: the entry keeps its place on
the watchlist, offers "Open careers page", and the application never pretends it
is being scanned.

* **Google, Microsoft, Meta, IBM, NVIDIA** - no stable public feed.
* **UBS, Avaloq** - the careers site answers an automated request with HTTP 403.
  Working around that is out of scope by design.
* **Swisscom, Zuehlke, Red Hat, Hitachi Energy** - Workday only, which is not a
  discovery source here (the Apply Assistant still supports Workday forms).
* **PostFinance, Novartis, Siemens Switzerland** - results are rendered in the
  browser, so there is nothing server-side to read.

### Source health

Config → Job Sources shows coverage per source kind and lists every failure by
company, with its error and when it last worked. A source that breaks is
visible; it never just quietly stops contributing. Config → Company Watchlist
shows the same per company: source, status, jobs from the last scan, last scan.

A scan reports what it did - sources scanned, companies scanned, jobs fetched,
Swiss eligible, relevant, new, source failures - and one failing source never
aborts the run.

### Company priority

Priority A / B / C is a tie-breaker only. Ranking is `match score DESC`, then
company priority, then posting date. A weak role at a priority-A company never
outranks a strong one elsewhere.

---

## Matching

Two stages, in a fixed order.

**Stage 1 - deterministic hard rules** (`jobscanner/filters.py`). Nothing that
fails here is ever scored or shown:

* not confirmably Switzerland - Germany-only, Austria-only, France-only,
  generic "Europe", "EMEA"
* junior, intern, working student, apprentice, trainee, graduate programme
* recruiter, HR, pure sales, marketing, helpdesk / first-level support and other
  unrelated functions

Every rejection is logged with a machine-readable code, so Config can explain
where a scan's results went.

**Stage 2 - relevance scoring** (`jobscanner/scoring.py`), 0-100. This is the
**Base Match Score**: it answers "is this job technically relevant?".

| Dimension | Points |
|---|---|
| Seniority / scope | 20 |
| Role responsibility | 25 |
| Technical domain | 20 |
| Leadership / transformation | 15 |
| Location / work model | 10 |
| Compensation potential | 5 |
| AI / strategic relevance | 5 |

Matching is semantic rather than exact-title: "Head of Platform Reliability"
scores on what the role *is*, not on whether the title is on a list. Repeated
keywords are not rewarded - each term counts once.

A missing salary or an unstated office-day policy produces a **concern**, never a
rejection.

**Down-ranking.** A configurable list of low-relevance domains (go-to-market,
pure sales, marketing, helpdesk, desktop support, pure SAP or data-analyst
roles) costs points and is named as a concern, instead of removing the posting.
A title that names both the domain *and* a target responsibility area keeps most
of its points, so "GTM Engineering Lead" stays visible while "GTM Lead" sinks to
the bottom of the list. An execution-level title (specialist, coordinator,
administrator, support engineer) costs points only when nothing in the posting
describes a leadership, program or transformation mandate.

**Stage 3 - personal fit** (`jobscanner/fit.py`). Relevance is not fit. A
Principal Delivery Consultant and a Head of SRE both fill a posting with cloud,
architecture and transformation vocabulary, so the base score rates them almost
identically. Two named, bounded adjustments answer the second question - "is
this the *shape* of job I want?":

```
Personal Fit Score = Base Match Score
                   + Operating Style Adjustment     (0 .. -15)
                   + Career Direction Adjustment    (0 .. -12)
```

**Operating Style Adjustment** - how much of the mandate is relationship,
orchestration and external-representation work rather than technical ownership.

| Classification | Adjustment |
|---|---|
| `TECHNICAL_OWNERSHIP` | 0 |
| `BALANCED` | 0 to -3 |
| `STAKEHOLDER_HEAVY` | -4 to -9 |
| `POLITICAL_EXTERNAL` | -8 to -15 |

**Career Direction Adjustment** - whether the role is technical leadership with
ownership, transformation or organisational scope, or whether the primary job is
feature implementation, consulting delivery or account management.

| Classification | Adjustment |
|---|---|
| `STRATEGIC_TECHNICAL_LEADERSHIP` | 0 |
| `TECHNICAL_PROGRAM_PLATFORM_LEADERSHIP` | 0 |
| `ENGINEERING_MANAGEMENT_RELIABILITY` | 0 |
| `BROAD_ARCHITECTURE_WITH_ORG_SCOPE` | 0 to -2 |
| `PURE_SENIOR_IC` | -4 to -8 |
| `CONSULTING_DELIVERY` | -4 to -9 |
| `ACCOUNT_ENGAGEMENT_MANAGEMENT` | -6 to -12 |
| `PURE_FEATURE_ENGINEERING` | -7 to -12 |

Three rules keep the adjustments honest:

* **Responsibility-driven, not title-driven.** A title hit weighs more than a
  body hit because a title names the primary job, but a title alone never
  saturates an adjustment. A title that names a *wanted* shape also protects the
  posting: "Site Reliability Engineer - Application Edge" is an SRE role even
  though the text mentions building things.
* **Dominance, not presence.** Normal senior behaviour - cross-functional
  collaboration, stakeholder management, executive communication, influence
  without authority, coordination across teams - costs exactly nothing. A
  penalty needs an unambiguous indicator ("engagement management", "government
  relations", "revenue recognition"); generic words only ever add weight to an
  indicator that has already fired.
* **No double-penalty.** Whatever the base score already charged through
  `deprioritized_keywords` is credited back before an adjustment is applied. One
  signal, one deduction.

The card always shows all four numbers. The base score is never hidden, so a
technically strong job that moved down the list always says why.

**How to read a Personal Fit Score:**

| Score | Meaning |
|---|---|
| 85-100 | Exceptional fit |
| 75-84 | Strong fit |
| 65-74 | Worth reviewing |
| 55-64 | Edge case |
| below 55 | Low priority - ranked last, never deleted |

Ranking uses the Personal Fit Score. Company priority is only ever a
tie-breaker: the ordering is fit first, then priority, then posting date, so a
priority-A job that scored 64 can never appear above a priority-B job that
scored 82.

`POST /api/jobs/rescore` re-applies the current profile and fit model to every
stored job. Nothing but the score and its explanation is rewritten - state,
feedback and the link to an application all survive.

### The search profile

The criteria are **configuration, not code**. They live in one row of
`search_profile` in the local database and are edited under Config; the scanner
reads that same row, so "saved" and "used by the scanner" cannot drift apart.
A built-in preset is offered as a starting point and is never applied
implicitly.

What the profile controls:

* **Geography** Switzerland is the only hard gate (strict mode). Inside
  Switzerland the preferred-location lists *rank* rather than exclude, in three
  tiers, so a Swiss city that is on no list still appears - it simply scores
  lower. A preferred radius and commute time are stored as preferences and are
  read by no filter.
* **Work model** Remote, hybrid and onsite each acceptable or not, with a
  maximum number of office days. An **unknown** work model is never rejected.
* **Seniority** The preferred levels, plus an "also accepted" list of titles
  that can never be rejected for the wrong seniority - scope decides the rest.
* **Domains** A vocabulary of target responsibility areas and technology
  keywords, plus the down-ranked domains described above.
* **Compensation** An interesting-from figure, a target and a
  published-and-clearly-below floor. In the recommended `ranking` mode none of
  them ever removes a posting: there is no hard minimum. Compensation is worth
  five points and can cost at most those five, and a posting that publishes
  nothing scores full marks rather than being penalised for the silence.

### Personal feedback

Each job card carries a **YES / MAYBE / NO** verdict with an optional reason.
The vocabulary mirrors what the two fit adjustments measure, so a verdict can
later be compared against what the scorer believed:

* *poor fit* - too stakeholder-heavy, too political / external, too
  consulting-heavy, too hands-on IC, too software-development focused, too
  little technical ownership, too little transformation scope, seniority too
  low, compensation likely too low, location/work model poor
* *good fit* - excellent technical ownership, excellent SRE / platform fit,
  excellent transformation scope, excellent technical program fit

A verdict is **recorded and nothing else**: it does not retrain the scorer,
change a filter or hide the job. It is there for a later, explicit calibration
step.

---

## Compensation estimator

`jobscanner/compensation.py` returns a **range**, in this priority order:

1. an explicit salary in the posting (confidence **High**)
2. locally stored market data for that company and role family (**Medium**)
3. a role-family + seniority + Swiss location baseline (**Low**)
4. optional AI commentary, added on top - it never replaces the numbers

```
Estimated TC:  CHF 260k-330k
Base:          CHF 210k-240k
Bonus:         CHF 30k-50k
Equity/LTI:    possible
Confidence:    Medium
```

No fake precision: when only the baseline applies the range is wide and the
confidence says so. Your own compensation targets live in Profile - they are
local workspace data, never part of this repository - and they rank results;
they never reject one.

The market-data table is editable under Config → Salary Model.

---

## AI analysis (optional)

The application is fully usable with AI switched off - deterministic matching and
templates then fill every field the UI shows, and the source of each analysis is
labelled.

With a provider configured, the full job description and the profile are analysed
and the result is stored per job, so reopening the Jobs page never issues a new
request. It returns: fit summary, strongest matches (at most 5), real gaps (at
most 3), seniority fit, recommended application angle, and salary commentary.

AI never analyses everything a scan fetched. It runs **after** deterministic
filtering and scoring, one job at a time, when you open a job's analysis - and
only for jobs that reached the AI threshold in Config (default 70). Anything
below that gets the identically shaped template analysis instead, and says so;
"Re-analyse" forces a real call when you want one anyway.

```bash
export ANTHROPIC_API_KEY=...     # or OPENAI_API_KEY
```

Then Config → AI → enable and pick the provider. **API keys are read from
environment variables only.** They are never written to SQLite and never returned
by the API - Config only ever reports whether a key is present.

---

## Apply assistant

Clicking **Apply** opens the real application page in a controlled Chromium
window and:

1. detects the ATS - adapters for **Greenhouse**, **Lever**, **Workday** and
   **SmartRecruiters**, plus a generic mapper
2. reads every form control's label, name, id, placeholder and accessibility
   attributes
3. fills the objective facts from Profile: first name, last name, email, phone,
   LinkedIn, city, country, nationality, work authorisation, notice period,
   relocation
4. attaches the CV, and the motivation letter when the form clearly asks for one
5. **stops**

### It never submits

There is no code path in this application that submits an application. The submit
control is located only so it can be reported and deliberately left alone; the
browser window stays open for you to review everything and click Submit yourself.
This is enforced by a test and cannot be switched off in Config.

Subjective questions are never answered automatically. "Why this company?", "Why
are you a good fit?", "Describe your leadership style", "Salary expectation",
dropdowns, checkboxes and conditional follow-ups ("If yes, please specify ...")
are all reported as **Review required**. Where AI is enabled you can ask for a
suggested answer - you still write or approve it.

---

## Data

Everything stays on this machine.

```
data/app.db            SQLite - the only database the app writes
data/backups/          automatic copies, taken before every schema change
documents/cv/
documents/motivation/
documents/references/
documents/certificates/
```

`data/` and `documents/` are git-ignored. Personal documents and the database are
never committed, and no personal value is hard-coded anywhere in the source: a
fresh installation starts with a blank profile.

### Portable workspace

`data/app.db` plus `documents/` **is** the workspace, and it moves between
machines as one archive. Document paths are stored relative to the workspace
root (`documents/cv/x.pdf`) and resolved at runtime, so a database written on
one machine works unchanged in a different checkout under a different user
account. An absolute path left by an older version is converted by the normal
additive migration.

Under **Config -> Backup & Transfer**, or from the command line:

```bash
python3 run.py export-workspace ~/Desktop/job-assistant-backup.zip
python3 run.py inspect-workspace ~/Desktop/job-assistant-backup.zip
python3 run.py import-workspace ~/Desktop/job-assistant-backup.zip

# the same thing, when the launcher cannot start:
python3 tools/workspace.py export ~/Desktop/job-assistant-backup.zip
python3 tools/workspace.py import ~/Desktop/job-assistant-backup.zip
```

The archive contains exactly:

```
manifest.json          format version, schema version, timestamp, git revision,
                       document list, SHA-256 checksums, row counts
data/app.db
documents/cv/ motivation/ references/ certificates/ other/
```

It **never** contains `.env`, API keys or tokens, browser profiles or cookies,
caches, the virtual environment or `.git/` - the export collects the two
workspace paths by name rather than sweeping the project folder, so a secret
cannot be picked up by accident. API credentials are configured separately on
the destination machine.

> The export contains your personal profile, application history and documents.
> Store and transfer it securely.

**Import is safe.** The archive is validated first (zip, manifest, format
version, SQLite integrity, required tables, member paths), the current database
and documents are copied to `data/backups/` second, and only then is anything
replaced. An older workspace is migrated before it is installed; a workspace
from a *newer* application is refused with a clear message rather than
downgraded. If anything fails, the previous workspace is restored - a failed
import never leaves a half-written installation.

### Moving to another computer

1. Clone the repository and check out the branch you use.
2. `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. `.venv/bin/python -m playwright install chromium`
4. Start the application once, so the database and folders exist.
5. `python3 run.py import-workspace <archive>` (or Config -> Backup & Transfer).
6. Optionally create `.env` and set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`.
7. Start the application. Profile, search settings, jobs, applications,
   watchlist and documents are all exactly as they were - nothing is re-typed.

### Migration from the previous version

On first start the V1 database (`data/applications.db`) is **copied** to
`data/app.db`; the original file is left exactly where it is, untouched, as your
pre-migration copy. Every schema step is additive and guarded, and a timestamped
backup is written to `data/backups/` before any schema change.

Existing applications, events, discovered jobs, job states, sources and the
company watchlist all survive. German status labels are mapped onto the V2
pipeline (`Beworben` → `Applied`), with the original label preserved in the
application's notes. Jobs scored by V1 are re-explained once with the current
scorer so old and new cards read the same way.

---

## Development

```bash
.venv/bin/python -m pytest tests -q      # 303 tests
.venv/bin/python run.py --reload         # auto-reload
```

| Module | Responsibility |
|---|---|
| `jobscanner/sources/` | one adapter per portal; they fetch, they never filter |
| `jobscanner/sources/registry.py` | maps a watchlist entry onto an adapter + config, and verifies it |
| `jobscanner/watchlist.py` | the company watchlist and per-company source health |
| `jobscanner/normalizer.py` | raw payload → normalised job (location, work model, seniority, dedupe) |
| `jobscanner/locations.py` | Swiss eligibility, cities, cantons, work model |
| `jobscanner/filters.py` | stage 1 hard rules |
| `jobscanner/scoring.py` | stage 2 explainable 0-100 score |
| `jobscanner/pipeline.py` | fetch → normalise → dedupe → filter → score → persist |
| `jobscanner/compensation.py` | compensation estimator |
| `jobscanner/ai/` | optional provider abstraction + cache + template fallback |
| `jobscanner/fit.py` | Operating Style and Career Direction adjustments |
| `jobscanner/apply/` | Playwright assistant, ATS adapters, field mapping |
| `jobscanner/person.py` | the personal profile |
| `jobscanner/documents.py` | document metadata; files stay on disk |
| `jobscanner/applications.py` | pipeline, timeline, linked documents |
| `jobscanner/api/` | FastAPI routers |
| `static/` | the frontend: one HTML file, one CSS file, one JS file |

Stack: Python, FastAPI, SQLite, HTML, CSS, vanilla JavaScript, Playwright. No
frontend framework - there is no technical need for one here.

### Adding a job source

Most useful results come from company career boards, so the normal way to add
one is Config → Company Watchlist: pick the company, pick the source kind, paste
the identifier, then press **Check source**. Nothing becomes ACTIVE until that
live request comes back with real postings.

Config → Job Sources also still accepts a raw source for anything that is not a
single employer. The public aggregator APIs (Arbeitnow, Remotive, Jobicy) carry
very few Swiss leadership roles; each source documents its own limitations in the
Config screen.
