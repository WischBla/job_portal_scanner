# Job Assistant

A personal job discovery and application assistant for **one user**: Sebastian Bierwisch.

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

Who Sebastian is: personal and contact data, LinkedIn, nationality and work
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

**Stage 2 - relevance scoring** (`jobscanner/scoring.py`), 0-100:

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

### The built-in search profile

Because there is one user, the criteria ship with the application:

* **Geography** Switzerland only (strict). Zurich, Zug, Lucerne, Bern, Basel
  preferred; St. Gallen, Schwyz, Aargau secondary; Lugano also considered. Other
  Swiss cities still appear - they simply rank lower.
* **Work model** Remote and hybrid preferred, max ~2 office days. Unknown office
  policy is a concern, not a rejection.
* **Seniority** Head of, Director, Senior Director, Principal, Global Lead,
  Technology / Engineering / Operations / Transformation Lead, Senior Engineering
  Manager, Principal and Senior TPM.
* **Domains** Technology / Technical / Engineering Operations, Platform
  Engineering, SRE, Reliability, Cloud, Infrastructure, DevOps, DevSecOps,
  Technology and Engineering Transformation, Technical Program / Project
  Management, Engineering Productivity, Developer Experience, AI Operations,
  AIOps, AI Engineering, Automation, Technology Strategy.

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
confidence says so. Sebastian's targets (minimum CHF 235-250k, target CHF
280-350k+, Big Tech CHF 350k+) live in Profile and rank results; they never
reject one.

The market-data table is editable under Config → Salary Model.

---

## AI analysis (optional)

The application is fully usable with AI switched off - deterministic matching and
templates then fill every field the UI shows, and the source of each analysis is
labelled.

With a provider configured, the full job description and the profile are analysed
and the result is stored per job, so reopening the Jobs page never issues a new
request. It returns: fit summary, strongest matches, real gaps, seniority fit,
recommended application angle, and salary commentary.

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
never committed.

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
.venv/bin/python -m pytest tests -q      # 237 tests
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
