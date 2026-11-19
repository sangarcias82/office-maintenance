# Office Maintenance: multi-company Facility Ops

Web app for facility maintenance: employees report failures, area managers assign technicians, technicians log work (materials and optional photo evidence), and managers review a filtered report with charts.

It serves **several companies (tenants) from one app and one database**:

- Every location, ticket, work log and photo belongs to one company. Users only ever see their own company's data.
- One person can work for several companies with one personal email and password, and a different role in each.
- Each company can have its own look: a front theme hook under `app/fronts/<slug>/` (palette + treatment), plus optional generic colour tokens.

Two demo companies are seeded: **Campus Ops** (office campus) and **Harbor Yard** (yard and logistics).

**Stack:** FastAPI · SQLAlchemy · SQLite · Jinja2 · HTML/CSS/JS  
**UI languages:** English / Spanish (EN · ES in the header)

No Docker, Redis, or Postgres required.

**Contents:**

- Getting started: [Quick start](#quick-start) · [Configuration](#configuration) · [Demo logins](#demo-logins) · [Features](#features)
- Multi-company: [Companies](#companies-tenants) · [Adding a company](#adding-a-company-or-a-membership) · [Fronts](#fronts-per-company-templates) · [Company look](#company-look-themes)
- Rules: [Photo evidence](#photo-evidence) · [Logging work rules](#logging-work-rules) · [Security](#security) · [Languages](#languages)
- Checking it works: [Tests](#tests) · [End-to-end harness](#end-to-end-harness) · [Smoke with curl](#smoke-with-curl)
- Reference: [Upgrade notes](#upgrade-notes) · [Known limitations](#known-limitations) · [Layout](#layout)

---

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# set a real session secret (otherwise a random one is used per start and sessions reset on restart):
python -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

Or run the local helper from the **repository root** (it lives next to `maintenances/`, not inside it). It creates a venv if needed, runs the tests, and only starts the app if they pass:

```bash
cd ..                    # repo root: for_testing + maintenances/
./for_testing              # tests, then start the app
./for_testing --cov        # + coverage report; fails below 90%
./for_testing --e2e        # + end-to-end harness against a throwaway server (see below)
./for_testing --cov --e2e  # everything (flags in any order)
```

`for_testing` is a bash script (Linux, macOS, WSL or Git Bash on Windows).

---

## Configuration

Settings come from environment variables or `maintenances/.env` (`app/config.py`):

| Variable | Default | Notes |
|----------|---------|-------|
| `SECRET_KEY` | placeholder | Signs the session cookie. **Set a long random value.** If it's missing or the placeholder, a random per-process secret is used (see [Security](#security)). |
| `DATABASE_URL` | `sqlite:///<app>/maintenance.db` | `.env.example` uses `./maintenance.db`, relative to the working directory. |
| `UPLOAD_DIR` | `<app>/uploads` | Photo evidence files (never served directly). |
| `SESSION_HTTPS_ONLY` | `false` | `true` behind HTTPS: Secure cookie + HSTS header. |
| `DEMO_PASSWORD` | `OfficeDemo1!` | Password of the seeded demo people (fresh database only). |
| `PASSWORD_HASH_ITERATIONS` | `600000` | PBKDF2 work factor for new hashes (min 1,000; the tests use 1,000). |
| `MAX_REQUEST_BYTES` | `6291456` (6 MB) | Larger request bodies get `413`. |
| `APP_NAME` | `Office Maintenance` | OpenAPI title. |

---

## Demo logins

Pick the **company** on the sign-in page, then use the email or employee number. All seed users share one password.

| Company | Login (email or employee number) | Role |
|---------|----------------------------------|------|
| Campus Ops | `ada.employee@facility.example` or `E-1001` | Employee |
| Campus Ops | `ben.employee@facility.example` / `E-1002`, `cara.employee@facility.example` / `E-1003` | Employee |
| Campus Ops | `tess.tech@facility.example` or `T-2001` | Technician |
| Campus Ops | `theo.tech@facility.example` or `T-2002` | Technician |
| Campus Ops | `morgan.manager@facility.example` or `R-3001` | Area manager |
| Harbor Yard | `hugo.employee@harbor-yard.example` or `HY-E-4001` | Employee |
| Harbor Yard | `hana.tech@harbor-yard.example` or `HY-T-5001` | Technician |
| **Both** | `sam.rivera@mail.example` (personal email) — Campus Ops `E-1004`, Harbor Yard `HY-R-6001` | Employee at Campus Ops, **Area manager at Harbor Yard** |

**Password:** `OfficeDemo1!`

Each company has its own locations and sample tickets: Campus Ops has 5, Harbor Yard has 3. Ticket numbers restart per company, so `REQ-2026-0001` exists in both.

Password hashes live in `account_credential`, not on the models exposed to templates.

---

## Features

| Screen | Path | Who |
|--------|------|-----|
| Sign in | `/login` | Everyone |
| Home | `/` | Signed-in users |
| New service request | `/requests/new` | Employee, Area manager |
| Request list (filter / assign) | `/requests` | Role-scoped |
| Ticket detail (work log, materials, photos) | `/requests/{id}` | Same visibility as the list |
| Photo evidence file | `/evidence/{photo_id}` | Signed in + same visibility as the list |
| Log work + photo | `/maintenance/new` | Technician |
| Maintenance report + charts | `/reports` | Area manager |
| Health | `/api/health` | Public |

The header's right-hand card shows the **company you are signed in to** (label + name) above your name, role and employee number.

| Role | Can |
|------|-----|
| Employee | Create requests; see own tickets |
| Technician | See assigned tickets; log work, materials, status, photos |
| Area manager | Create requests; see all tickets **of their company**; assign technicians of their company; open the company report |

Protected routes require a session; see [Security](#security) for the sign-in, session and CSRF rules.

---

## Companies (tenants)

The `company` table is the tenant entity. It only holds generic tenant data:

| Column | Notes |
|--------|-------|
| `slug` | Unique, stable key (`a-z`, `0-9`, single hyphens, max 60). Cannot be changed once set. |
| `display_name` | Required, trimmed. |
| `description` | Optional. |
| `active` | Defaults to true. Inactive: no new logins, existing sessions end. |
| `theme_tokens` | Optional JSON of generic design tokens (see [Company look](#company-look-themes)). |
| `created_at` / `updated_at` | Set by the database. |

There are no brand-specific columns (logo, brand colour, CSS). A test enforces this for every table.

Seeded demo companies (exactly two):

| Slug | Display name | Kind |
|------|--------------|------|
| `campus-ops` | Campus Ops | Office campus facilities |
| `harbor-yard` | Harbor Yard | Yard and logistics |

### People and memberships (one employee, several companies)

| Table | Meaning |
|-------|---------|
| `user_account` | A **person**: personal email (unique globally), created/updated timestamps. |
| `account_credential` | That person's **one** password hash (PBKDF2). |
| `app_user` | A **membership** of the person in one company: `company_id`, `account_id`, login email, employee number, role, active. |

- A person can have at most one membership per company (`uq_app_user_company_account`). Login email and employee number are unique **per company** (`uq_app_user_company_email`, `uq_app_user_company_employee_number`).
- **Sign-in:** pick the company, then give the login email or employee number of your membership there, plus your personal password. The password belongs to the person, so it's the same for every company.
- The chosen company decides which membership, and so which role and data, the session gets.
- An employee who works for two companies signs in to either with the same personal email and password (demo: Sam Rivera). Deactivating one membership leaves the other working.
- Two **different** people may still use the same login email in different companies; they are separate accounts with separate passwords.
- Emails are stored lowercase.
- Requests, assignments and work records point at the membership (`app_user`), so data stays inside its company.
- Area managers only see and assign technicians from their own company. Another company's technician gets the same "User not found" message as a missing user.

### Locations and service requests belong to a company

- `location.company_id` and `service_request.company_id` are required. Location codes and ticket numbers are unique **per company** (`uq_location_company_code`, `uq_service_request_company_ticket`).
- Maintenance records, materials and photos have no company column. They inherit the company through their request.
- Every service that lists, reads or changes requests uses the **actor's company**: the request list, search, technician work list, log-work form, assignment and report. `create_request` always stores `actor.company_id`. Another company's request ID behaves like a missing one ("Service request not found").
- Catalogs (roles, priorities, statuses, maintenance types) stay global.

**Creating a request validates**, with a translated `BusinessError` and never a 500:

| Problem | Message key |
|---------|-------------|
| Missing, non-numeric, unknown, or **another company's** location | `error.locationInvalid` |
| Location of the company but inactive | `error.locationInactive` |
| Missing / unknown priority | `error.priorityInvalid` |
| Missing / unknown maintenance type | `error.typeInvalid` |
| Blank description | `error.descriptionRequired` |

The new-request form only offers the company's **active** locations. The report's location filter also lists inactive ones, so old tickets stay filterable.

**Ticket numbers** (`REQ-<year>-NNNN`, per company and year) come from the `ticket_counter` table:

- Allocation is one `UPDATE … SET last_number = last_number + 1` inside the request's transaction. Two simultaneous creates therefore queue on the counter row instead of both computing max+1.
- The first ticket of a year starts after the highest existing number. Malformed numbers are ignored, and the comparison is numeric (`10000` follows `9999`).
- If a number is already taken (for example a ticket inserted outside the app), the counter catches up and the create retries, up to 3 times. If it still fails the user sees `error.ticketConflict`.
- Any other database constraint failure becomes `error.requestNotSaved`. No 500 reaches the user.

**Report dates** (`fromDate`, `toDate`) must be `YYYY-MM-DD`. A bad value raises `BusinessError("error.invalidDate")`. The page shows that message and runs the report without the date filter (other filters stay).

### Startup and seeding

`db.init_db()` runs on startup:

1. **Outdated schema check.** If an existing table lacks columns the models need (a `maintenance.db` from an older version), a SQLite file is moved aside as `maintenance.db.outdated-<timestamp>`, so nothing is deleted, and a fresh demo database is created. A warning is logged. For any other database, startup stops with a clear error; use a real migration there.
2. `create_all`.
3. `seed_if_empty` (only on an empty database):
   - both companies;
   - global catalogs;
   - each company's locations;
   - people and memberships (one account per distinct email);
   - each company's sample tickets and work records.

---

## Adding a company or a membership

There is no admin screen yet, so companies and people are added from a Python shell in `maintenances/` (with the venv active). Every value goes through the same model validation as the app:

```python
from sqlalchemy import select
from app.db import SessionLocal, init_db
from app.auth import hash_password
from app.models import Account, AccountCredential, AppUser, Company, Location, Role

init_db()
db = SessionLocal()

# 1. the company (slug is permanent; tokens are optional)
acme = Company(slug="acme-labs", display_name="Acme Labs", description="Lab buildings",
               theme_tokens={"accent": "#6b4f7a", "bg": "#f4f0f6", "bg-deep": "#e4dcea"})
db.add(acme); db.flush()

# 2. its locations
db.add(Location(company_id=acme.id, code="LAB-1", name="Lab 1", building="North"))

# 3a. a new person + membership
role = lambda code: db.scalar(select(Role.id).where(Role.code == code))
person = Account(email="lee@acme.example")
person.credential = AccountCredential(password_hash=hash_password("a long password"))
db.add(person); db.flush()
db.add(AppUser(company_id=acme.id, account_id=person.id, employee_number="AL-1", first_name="Lee",
               last_name="Park", email="lee@acme.example", role_id=role("AREA_MANAGER")))

# 3b. an existing person joins too (same password, own role here)
sam = db.scalar(select(Account).where(Account.email == "sam.rivera@mail.example"))
db.add(AppUser(company_id=acme.id, account_id=sam.id, employee_number="AL-2", first_name="Sam",
               last_name="Rivera", email=sam.email, role_id=role("TECHNICIAN")))
db.commit()
```

The company appears on the login page right away.

**Optional front:** create `app/fronts/acme-labs/templates/partials/brand.html` and/or `partials/theme.css`. Copy the `_default` ones as a starting point. Only files that exist in `_default` can be overridden. Restart the app afterwards.

To retire a company, set `active = False`. Its users can no longer sign in, and their sessions end.

---

## Fronts (per-company templates)

Templates live in the `app/fronts` package:

```
app/fronts/
  __init__.py                      resolver: templates_for(slug), search_path(), resolved_front()
  _default/templates/...           the full template set (base, pages, partials/brand.html)
  <company-slug>/templates/...     only the files that company overrides
  harbor-yard/templates/partials/brand.html   demo override (brand block top-left)
```

- `render()` picks the environment from the signed-in user's company slug. The login page and anonymous pages use `_default`. The slug never comes from the URL.
- Lookup order per file: `<slug>/templates` first, then `_default/templates` (a Jinja `FileSystemLoader` search path). A company can override a single partial and inherit everything else. `{% extends "base.html" %}` and `{% include %}` resolve the same way, so an overridden `base.html` applies to default pages too.
- **Fallback, never a 500:** a company with no folder, an empty folder, or a folder missing a file all fall back to `_default`.
- The slug is re-validated (`a-z0-9-` only), so `../` or `_default` can't become a path.
- Rule: every override must shadow a file that exists in `_default` (a test enforces this). Add files under `app/fronts/<slug>/templates/` and restart the app.
- Demo: Harbor Yard users see "Yard & logistics operations / Harbor Yard Maintenance" top-left (`data-front="harbor-yard"`). Campus Ops and the login page show the default brand (`data-front="_default"`).
- `app/templates` no longer exists. `app/fronts/_default/templates` replaces it.

---

## Company look (themes)

Each page carries a **stable per-company hook** on `<body>`:

```html
<body class="theme-harbor-yard" data-company="harbor-yard" data-theme="harbor-yard">   <!-- Harbor Yard -->
<body class="theme-_default"    data-company="campus-ops"  data-theme="_default">      <!-- Campus Ops -->
<body class="theme-_default"    data-company=""            data-theme="_default">      <!-- login page -->
```

- `data-company` is the signed-in company's slug.
- `data-theme` is the front whose `partials/theme.css` actually rendered. It's read from the template loader, so it reports what was served, not what was configured.

The look is built in three layers, later ones winning. None of them is a brand-specific SQL column:

| Layer | Where | What it does |
|-------|-------|--------------|
| 1. Base design tokens | `static/css/app.css` `:root` | today's look (orange accent, warm beige) for everyone |
| 2. **Front theme hook** | `app/fronts/<slug>/templates/partials/theme.css`, inlined as `<style id="front-theme">` | the company's palette (`:root` variables) plus page treatment keyed on `body[data-theme="<slug>"]` |
| 3. Company tokens (optional) | `company.theme_tokens` (JSON), emitted as `<style id="company-tokens">` | per-company tweaks without a front folder, or on top of it |

**Token names** (the same generic names in all three layers; `app/theme_tokens.py`):

| Token | Controls |
|-------|----------|
| `accent` / `accent-ink` | buttons, active nav, company name, first chart colour / text on accent |
| `accent-2` | secondary highlight (Harbor Yard's hazard stripe and active tab, second chart colour) |
| `bg` / `bg-deep` / `bg-glow` | page background top / bottom / corner glow (hex with alpha) |
| `panel`, `ink`, `muted`, `line` | cards, main text, secondary text, borders |

- `theme_tokens` values must be hex colours (`#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`). Unknown names or other values raise `ValueError` on save.
- They're re-checked when rendering, so even a value written with raw SQL can't inject CSS.
- Report charts read `--accent`, `--accent-2`, `--panel` and `--muted`, so they follow the theme.

**Front files**

| File | `_default` | `harbor-yard` |
|------|------------|---------------|
| `templates/partials/theme.css` | empty (base look) | **palette**: navy accent `#1f5f8b`, steel-blue background `#e9eff4` to `#cfdbe6`, white-blue panels, yellow `#f2b705`. **Treatment**: navy nav bar with a yellow active tab, faint diagonal yard-line pattern, brand title as a navy plate with a hazard stripe and ring marker |
| `templates/partials/brand.html` | kicker + title | `brand-harbor` class and its own kicker/title text |

**Demo looks** (both demo companies seed **no** `theme_tokens`; the look comes from the fronts)

| | Campus Ops | Harbor Yard |
|---|------------|-------------|
| Hook | `data-company="campus-ops"` `data-theme="_default"` | `data-company="harbor-yard"` `data-theme="harbor-yard"` |
| Accent / background | orange `#c45c26` / beige `#f3efe6` (today's look) | navy `#1f5f8b` / steel blue `#e9eff4` |
| Navigation | light pills, orange active tab | navy bar, yellow active tab |
| Title | "OFFICE MAINTENANCE", plain condensed caps | "HARBOR YARD MAINTENANCE" on a navy plate with a yellow stripe |

- Removing Harbor Yard's `theme.css` (or its whole folder) falls back to the default hook and colours, with no error (tested).
- The login page shows the default look, because there is no company before sign-in.

---

## Photo evidence

- **Saving** (log work form): same allowlist as before, JPEG, PNG, GIF and WebP up to 5 MB. The file's first bytes must also match the declared type. A failure is **reported, not ignored** (see *Logging work rules*). Files get a server-generated name and are written to `UPLOAD_DIR/evidence/<company_id>/<request_id>/<uuid>-evidence.<ext>`.
- **Database:** `photo_evidence.relative_path` holds only that relative path. The model rejects absolute paths, backslashes and `..`. No URL is stored.
- **No public mount:** the old `StaticFiles` mount on `/uploads` is gone; `/uploads/...` is a 404.
- **Download:** `GET /evidence/{photo_id}` is behind `require_user`.
  - It serves the file only if the photo's request is visible to the user under the **same rule as the request list and the ticket detail page** (`services/requests.visible_to`): always the user's company; employees see requests they reported, technicians see requests assigned to them, area managers see all of their company.
  - Anonymous users are redirected to `/login?next=/evidence/<id>` and come back after signing in.
  - Other company, ticket not visible to the role, unknown id, missing file, a path outside `UPLOAD_DIR`, or a non-allowlisted extension all give the **same 404**, so existence isn't revealed.
  - Responses carry `Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff`, and `Content-Disposition: inline`.
- **URLs are built at render time** with `evidence_url(photo)` (in the `render()` context, from the route name `evidence_file`). The ticket detail page `/requests/{id}` shows the work log with materials and photo thumbnails. Ticket numbers in the request list link there.
- The demo seed writes small striped PNG placeholders for the sample photos (Campus Ops: 3, Harbor Yard: 1), so evidence can be viewed right away.
- Changing `path_or_url` to `relative_path` (and adding `company.theme_tokens`) is a schema change. A `maintenance.db` from the previous version is moved aside as `maintenance.db.outdated-<timestamp>` on first start (see *Startup and seeding*).

---

## Logging work rules

`services/maintenance.log_work` validates **everything before writing anything**, then saves the record, materials, photo and status change in one transaction. Each refusal is a `BusinessError` with a message key, shown as a flash on the re-rendered form (typed text and material rows are kept; the photo must be attached again).

| Rule | Message key |
|------|-------------|
| Only technicians; only on requests assigned to them | `error.onlyTechniciansLog`, `error.notYourRequest` |
| Request already **COMPLETED or CANCELLED** (`is_final`): no more work | `error.requestClosed` |
| New status must be one of `loggable_statuses()` (In progress, Completed, Cancelled); unknown id | `error.statusNotAllowed`, `error.invalidStatus` |
| Work performed is required | `error.workRequired` |
| Material row with a name needs a quantity that is a finite number **> 0**. No more silent `1.0`; `0`, `-3`, `abc`, `nan`, `inf` and blank are refused | `error.materialQuantity` (names the material) |
| Material row with a quantity needs a name (rows with neither are skipped) | `error.materialName` |
| Photo of a disallowed type or with content that doesn't match its type | `error.evidenceType` |
| Photo larger than 5 MB / empty file | `error.evidenceTooLarge` / `error.evidenceEmpty` |
| Photo couldn't be written to disk | `error.evidenceSaveFailed` |

In every refused case **no record, no material row, no photo row and no file is kept**, and the ticket status doesn't change. If the database step fails after the file was written, the file is deleted again. Closed tickets are no longer offered in the log-work dropdown, and the list hides their **Log** button.

---

## Security

Protected routes require a session.

### Sign-in and sessions

- The login is checked **only inside the chosen company**. A Campus Ops email used with Harbor Yard fails, unless that person also has a Harbor Yard membership (see Sam Rivera).
- Wrong password, unknown user, wrong company, unknown or inactive company, and a missing company all show the same "Invalid credentials" message.
- Every case that names an identity runs exactly one PBKDF2 check (against a dummy hash when there's no matching user), so response time doesn't reveal which case it was. A blank login or password is rejected before any lookup.
- On successful login the session is **rotated**: everything from before login is discarded and a new payload is written with `user_id` (the company membership, so the company is implied). Only the language picked on the login page is kept. (Sessions are signed cookies, so rotation means replacing the whole payload.)
- **Logout clears the whole session**, including the language.
- `next=` after login and on `/lang/...` only accepts same-site paths such as `/reports`. Anything like `//evil.example`, `https://…`, backslashes, or control characters falls back to `/`.
- Deactivating a company (`company.active = false`) blocks new logins and ends existing sessions of its users.

### CSRF protection (`app/csrf.py`, synchronizer-token pattern; no extra frameworks)

- Each session gets one random token (32 bytes, URL-safe) stored in the signed session cookie under `_csrf`. `render()` exposes it to every template as `csrf_token`.
- Every state-changing form posts it in a hidden field: login, logout (header button), new request, assign technician, and log work (multipart with photo).
- Every POST route has `dependencies=[Depends(require_csrf)]`. The dependency runs **before** the route body, compares the form value with the session value in constant time, and on a missing or wrong token raises `CSRFError`.
- **Nothing is processed:** no login, no logout, no insert or update, no file stored.
- `main.py` turns `CSRFError` into a translated flash ("Your form expired or was sent from another site…") and a **303 redirect** back to the page the form came from. That page comes from the `Referer` header, same site only; otherwise the redirect goes to `/`. The failure is logged as a warning.
- Login and logout replace the whole session, so the token rotates with it. A token from before login (or from another session) is useless afterwards.
- The session cookie stays `SameSite=Lax` and `HttpOnly`, a second layer.
- `/lang/<code>` is a GET link that only changes the UI language in the session, so it has no token. No data is written.

### Session secret

The session cookie carries the signed-in membership id and is **signed, not encrypted**. Anyone who knows `SECRET_KEY` can forge a session for any user. The `.env.example` placeholder is public, so `app/security.py` never uses it:

- If `SECRET_KEY` is missing or the placeholder, a random secret is generated at start-up and a warning is logged. Sessions then reset whenever the app restarts. With several worker processes, each would have its own secret, so set a real key.
- If `SECRET_KEY` is shorter than 32 characters, a warning is logged.

### Response headers and limits (`app/security.py`)

Every response, including redirects, errors, static files and evidence, gets:

| Header | Value / purpose |
|--------|-----------------|
| `Content-Security-Policy` | `default-src 'self'`; scripts only from this site (no inline JS; a test checks the templates); styles from this site, inline theme blocks and Google Fonts; `frame-ancestors 'none'`; `form-action 'self'`; `object-src 'none'` |
| `X-Frame-Options: DENY` | no framing (clickjacking would bypass the CSRF token) |
| `X-Content-Type-Options: nosniff` | browsers trust the declared type |
| `Referrer-Policy: same-origin` | the CSRF bounce-back still sees a same-site Referer |
| `Permissions-Policy` | camera, microphone, geolocation off |
| `Cache-Control: no-store` | on HTML pages, which contain company data. Evidence uses `private, no-store`; static files stay cacheable. |
| `Strict-Transport-Security` | only when `SESSION_HTTPS_ONLY=true` |

- Request bodies larger than `MAX_REQUEST_BYTES` (6 MB by default) get `413` **before any route runs**, based on `Content-Length`. A negative or invalid length gets `400`.
- Photos also have their own 5 MB check inside the form, which shows a normal error message.

### Tenant isolation

- Every query for tickets, photos, locations and technicians is filtered by the signed-in membership's company.
- Another company's id behaves exactly like an id that doesn't exist ("Service request not found", same 404 for photos).
- See [Companies](#companies-tenants) and `tests/test_refusals.py`.

---

## Languages

UI text lives in language files, one flat JSON map per language:

```
app/locales/en.json
app/locales/es.json
```

`app/i18n.py` loads them (`t(lang, key)`, `catalog_label(...)`, `error_text(lang, exc)`). English is the fallback. Service errors carry a `message_key` (`error.*`) and are shown in the session language. Report rows and chart labels are translated as well.

To add or change text, edit both files with the same key. `tests/test_i18n.py` fails if:

- the languages have different keys;
- a value is empty;
- `{placeholders}` don't match across languages;
- a key used in templates or code is missing from the files.

Tests never hard-code UI strings. They look up the expected text with the `ui(key, lang)` fixture.

The test `client` posts like a browser: each POST carries the session's current CSRF token, as the rendered forms do. Pass `csrf=False`, or your own `csrf_token`, to test rejections.

---

## Tests

```bash
pytest -q                                         # fast run
pytest -q --cov=app --cov-report=term-missing     # with the coverage gate
```

`.coveragerc` sets `fail_under = 90`, so any coverage run fails below 90%. That includes `../for_testing --cov`; plain `../for_testing` skips coverage.

The suite (about 500 tests, including one real server started for the end-to-end harness) takes roughly 30 s to 2 min depending on the machine (up to about 3 min with `--cov`). It shows no warnings. Three choices keep it that way:

- `tests/conftest.py` sets `PASSWORD_HASH_ITERATIONS=1000`. Same PBKDF2 code path, lower work factor for new hashes. The shipped default stays **600,000**, a test pins it, and values below 1,000 are rejected. Verification reads the count stored in each hash, so existing 600k hashes keep working.
- `httpx2` is installed on Python 3.11+, because Starlette 1.x's TestClient warns when it has to fall back to plain `httpx`.
- The database and the demo evidence files are created and seeded **once per test session** (`seeded_template` fixture). Each test gets a fresh file copy of both, which is as isolated as reseeding but much faster.

| Documented feature | Tests |
|--------------------|-------|
| Sign in / out, company selector, shared failure message, dummy-hash timing, session rotation, logout clearing, safe `next` redirects | `tests/test_auth.py` |
| CSRF: token issued by `render()`, stable per session, rotated at login and logout. Every POST form has the field; every POST route rejects a missing token (found via OpenAPI). Missing, empty, wrong, non-ASCII or other-session tokens write nothing on login, logout, create, assign and log work (incl. multipart). Same-site redirect back, translated flash, scraped-token browser flow, SameSite=Lax | `tests/test_csrf.py` |
| People and memberships: seeded users' companies, demo password per company, one employee in two companies (one password, role per company), per-company uniqueness, inactive company or membership, technician scoping | `tests/test_users.py` |
| New request (validation keys, inactive or other-company location), request list (role scope, search, filters), assign, ticket counter (per company, malformed / numeric, real two-session race, stale counter, retries, no 500s) | `tests/test_requests.py` |
| Company isolation: locations, lists, search, new-request form, direct request-ID access, assignment, reports and report filters, seed integrity, header shows the company above the name | `tests/test_tenancy.py` |
| Log work rules, through the form **and by calling `log_work` directly**: target status must be in `loggable_statuses()` (PENDING and a non-loggable `ON_HOLD` refused); COMPLETED and CANCELLED requests refused for every target status; bad or non-positive quantities (`0`, `-1`, `-0.5`, `abc`, blank, `nan`, `inf`, …) raise `error.materialQuantity` instead of becoming 1.0; photo failures (disallowed type, fake content, oversize, empty, disk error) raise and leave no record, material, photo row or file; allowed types stored | `tests/test_maintenance.py` |
| Company look: **each company asserts its own stable hook** (`data-company`, `data-theme`, `theme-*` class) plus its effective accent and background, resolved like the browser (app.css → front theme.css → company tokens). The two companies differ in theme, accent, both backgrounds and panels. Harbor's palette lives in its front file; company tokens still override; token validation and injection attempts; Harbor front files; per-company switch | `tests/test_theme.py` |
| Report indicators, filters (incl. invalid dates), charts, access | `tests/test_reports.py` |
| EN / ES language files and translated pages | `tests/test_i18n.py` |
| Companies: entity rules, seed, startup wiring, outdated-DB handling, no brand columns | `tests/test_companies.py` |
| Fronts: lookup order, slug validation, Harbor Yard override, per-file fallback (one partial, empty folder), overridden `base.html` via `extends`, every override has a default | `tests/test_fronts.py` |
| Evidence: no `/uploads` mount, relative paths only, anonymous → login → back, visibility matrix per role and company, identical 404s, missing file / path escape / bad extension, upload → detail page → download round trip, save allowlist unchanged, ticket detail page | `tests/test_evidence.py` |
| Health | `tests/test_health.py` |
| **Refuse-safety net** (see below) | `tests/test_refusals.py` |
| Hardening: placeholder/missing secret replaced (forged placeholder cookie refused), security headers on all response types, no inline scripts, 413 size limit | `tests/test_security.py` |
| End-to-end harness against a real uvicorn process (all checks pass), harness helpers, unreachable server exit code | `tests/test_e2e_harness.py` |
| Docs stay in step: every setting in README + `.env.example`, every test file and app module listed, contents links resolve | `tests/test_docs.py` |

### Refuse-safety net (`tests/test_refusals.py`)

Each refusal is checked in **both company directions**. The test compares a full snapshot before and after: all request, record, material, photo and ticket-counter rows, each ticket's status, technician and timestamps, and every file in the upload folder. It must be identical. A 500 also fails the test, because the TestClient re-raises server errors.

| Refusal | Test |
|---------|------|
| Company A technician logs work (with material + photo) on a company B request id → "Service request not found", nothing about B shown; log form and service refuse too | `test_technician_cannot_log_work_on_other_company_request`, `test_log_form_and_service_refuse_other_company_request` |
| Company A manager assigns on a company B request id, with an own or a B technician → "Service request not found"; B technician on own request → "User not found" | `test_manager_cannot_assign_on_other_company_request`, `test_manager_cannot_assign_other_company_technician_on_own_request` |
| `/reports` totals, per-status counts, both chart sums and table rows equal the manager's own company, even after the other company adds tickets; no other-company location names; other-company location filter counts 0 | `test_report_counts_only_own_company`, `test_report_with_other_company_location_filter_counts_zero`, `test_report_service_rows_are_all_own_company` |
| Front fallback: in a copy of the real `app/fronts`, delete Harbor Yard's `brand.html` (default brand, Harbor theme kept), its `theme.css` (`data-theme="_default"`, default orange accent), or its whole folder (all default). Every page is still 200 and still `data-company="harbor-yard"`. A company with no folder renders `_default` | `test_removed_override_falls_back_to_default`, `test_company_without_front_folder_renders_default` |
| Open redirect (`//`, `https://`, backslash, tab, `javascript:`) refused on login POST, login GET, `/lang`, and CSRF bounce | `test_open_redirect_refused_everywhere` |
| Missing CSRF token on login, logout, create, assign, log work → 303, no write, sign-in state unchanged | `test_missing_csrf_token_changes_nothing` |
| Unauthenticated: evidence → login redirect, no image bytes; `/uploads` 404; all protected pages redirect; writes with a *valid* anonymous CSRF token still refused | `test_unauthenticated_evidence_refused`, `test_unauthenticated_pages_redirect_to_login`, `test_unauthenticated_writes_refused_even_with_valid_csrf` |
| Bad report dates (garbage, Feb 30, datetime, 5-digit year, non-ASCII digits, `%00`) → 200 + message; non-numeric ids → 4xx, never 500 | `test_bad_report_date_does_not_500`, `test_bad_report_ids_are_refused_without_500` |
| Create with bogus catalog ids (unknown, negative, SQL-ish text, blank, other company's location) → form error, no row | `test_create_with_bogus_catalog_id_does_not_500` |

These tests catch real regressions. Re-introducing any one of these bugs makes `test_refusals.py` fail:

- request lookup without the company filter;
- report without the company filter;
- resolver without the `_default` fallback;
- login following any `next`;
- CSRF check disabled;
- evidence route without `require_user`;
- report date error not caught;
- create without location validation.

---

## End-to-end harness

`scripts/e2e_check.py` checks the whole multi-company feature against a **running server over real HTTP**. It's standard-library Python, so it needs no curl and no extra packages.

Each demo user is a separate browser with its own cookies. Forms are sent with the CSRF token read from the page, and expected messages come from `app/locales/en.json`.

```bash
cd maintenances
python scripts/e2e_check.py --base http://127.0.0.1:8000    # against your running app (leaves rows tagged e2e-<id>)
cd .. && ./for_testing --e2e                                # against a throwaway server + database on :8765 (E2E_PORT)
```

It prints one `ok`/`FAIL` line per check and exits `0` only if all pass (`2` if the server can't be reached). The checks, in order:

1. Health, login page (both companies, default look), every demo user signs in, wrong company refused.
2. Header shows the company above the name; Sam is Employee at Campus Ops and Area manager at Harbor Yard.
3. Each company's stable look hook: body `data-company` / `data-theme`, Harbor's palette from its front `theme.css`, Campus Ops on the defaults.
4. Request lists and reports are company-scoped: report total equals the table rows, with no other-company data.
5. Lifecycle at Harbor Yard:
   - Hugo creates a ticket, and other-company or bogus ids are refused;
   - the ticket is invisible to Campus Ops;
   - the Campus manager can't assign it, but the Harbor manager assigns Hana;
   - a Campus technician can't log work on it;
   - bad status, quantity and photo are refused with nothing saved;
   - Hana logs work with a photo;
   - the photo is served to Hana, Sam and Hugo, gives 404 to Campus users, and sends anonymous users to login;
   - once closed, no more work can be logged.
6. CSRF without a token writes nothing; open redirects refused; bad report dates give no 500; security headers and cookie flags; oversized body gives `413`; logout clears the session.

Against builds with deliberately broken code, the harness failed every time. Removing the company filter from the request list gave 7 failures; disabling CSRF, accepting work on closed tickets, or removing the size limit each gave 1. `tests/test_e2e_harness.py` runs the same harness against a real uvicorn process in every `pytest` run.

---

## Smoke with curl

The same ground, by hand (the [harness](#end-to-end-harness) automates it).

Every POST needs the session's CSRF token, so scripts do what a browser does: load a page, read the hidden `csrf_token` field, and send it back.

```bash
B=http://127.0.0.1:8000
# token JAR: CSRF token of the session in cookie jar JAR (-L: /login forwards to / when signed in)
token() { curl -sL -c "$1" -b "$1" "$B/login" | grep -oE 'name="csrf_token" value="[^"]+"' | head -1 | cut -d'"' -f4; }
# login JAR COMPANY LOGIN: sign in with the demo password
login() { curl -s -c "$1" -b "$1" -X POST $B/login --data-urlencode csrf_token="$(token "$1")" \
  --data-urlencode company="$2" --data-urlencode login="$3" --data-urlencode 'password=OfficeDemo1!' \
  -o /dev/null -w "$2 %{http_code} -> %{redirect_url}\n"; }

# health
curl -s $B/api/health                                                   # {"status":"ok"}

# login with token -> 303 to /, header shows the company above the name
rm -f ada.cookies; login ada.cookies campus-ops ada.employee@facility.example      # campus-ops 303 -> .../
curl -s -b ada.cookies $B/ | grep -A3 'class="session-company"' | grep -oE 'Campus Ops|Harbor Yard'   # Campus Ops

# CSRF: login WITHOUT a token -> 303 back, not signed in, flash explains why
rm -f x.cookies; curl -s -c x.cookies -b x.cookies $B/login -o /dev/null
curl -s -c x.cookies -b x.cookies -X POST $B/login --data-urlencode company=campus-ops \
  --data-urlencode login=E-1001 --data-urlencode 'password=OfficeDemo1!' -o /dev/null -w '%{http_code}\n'   # 303
curl -s -b x.cookies -c x.cookies $B/login | grep -o 'Your form expired[^<]*'

# CSRF: create request with a wrong token -> nothing written
curl -s -b ada.cookies -c ada.cookies -X POST $B/requests -d csrf_token=forged -d location_id=1 \
  -d priority_id=1 -d maintenance_type_id=1 --data-urlencode 'problem_description=CSRF probe' \
  -o /dev/null -w '%{http_code} -> %{redirect_url}\n'                   # 303 -> .../
curl -s -b ada.cookies $B/requests | grep -c 'CSRF probe'                # 0

# same request with the real token -> created
curl -s -b ada.cookies -c ada.cookies -X POST $B/requests --data-urlencode csrf_token="$(token ada.cookies)" \
  -d location_id=1 -d priority_id=1 -d maintenance_type_id=1 --data-urlencode 'problem_description=With token' \
  -o /dev/null -w '%{http_code} -> %{redirect_url}\n'                   # 303 -> .../requests
curl -s -b ada.cookies $B/requests | grep -c 'With token'               # 1

# same credentials, wrong company -> "Invalid credentials."
rm -f w.cookies; curl -s -c w.cookies -b w.cookies -X POST $B/login --data-urlencode csrf_token="$(token w.cookies)" \
  --data-urlencode company=harbor-yard --data-urlencode login=ada.employee@facility.example \
  --data-urlencode 'password=OfficeDemo1!' | grep -o 'Invalid credentials.'

# off-site next is ignored -> Location: /
rm -f n.cookies; curl -s -c n.cookies -b n.cookies -o /dev/null -D - -X POST $B/login \
  --data-urlencode csrf_token="$(token n.cookies)" --data-urlencode company=campus-ops --data-urlencode login=R-3001 \
  --data-urlencode 'password=OfficeDemo1!' --data-urlencode next=//evil.example | grep -i '^location'

# malformed report date -> 200 with a message
rm -f mgr.cookies; login mgr.cookies campus-ops R-3001
curl -s -b mgr.cookies -o /dev/null -w '%{http_code}\n' "$B/reports?fromDate=not-a-date"   # 200

# one person, two companies: same personal email + password, different role per company
rm -f sam-*.cookies
login sam-campus.cookies campus-ops  sam.rivera@mail.example            # campus-ops 303
login sam-harbor.cookies harbor-yard sam.rivera@mail.example            # harbor-yard 303
curl -s -b sam-campus.cookies $B/ | grep -oE 'Create requests · Own tickets|All tickets · Assign · Reports'   # employee
curl -s -b sam-harbor.cookies $B/ | grep -oE 'Create requests · Own tickets|All tickets · Assign · Reports'   # manager

# isolation: each manager's list only has their company's tickets
curl -s -b mgr.cookies $B/requests        | grep -c 'barrier arm'       # 0  (Harbor Yard ticket)
curl -s -b sam-harbor.cookies $B/requests | grep -c 'barrier arm'       # 1
curl -s -b sam-harbor.cookies $B/requests | grep -c 'Server-room AC'    # 0  (Campus Ops ticket)

# create with another company's location id -> form error, not a 500
HY_LOC=$(curl -s -b sam-harbor.cookies $B/requests/new | tr -s ' \n' ' ' | grep -oE 'value="[0-9]+" ?> North gate' | grep -oE 'value="[0-9]+"' | grep -oE '[0-9]+')
curl -s -b mgr.cookies -c mgr.cookies -X POST $B/requests --data-urlencode csrf_token="$(token mgr.cookies)" \
  -d location_id=$HY_LOC -d priority_id=2 -d maintenance_type_id=1 --data-urlencode problem_description=test \
  -w '\n%{http_code}\n' | grep -oE 'Choose a valid location.|^[0-9]{3}$'

# evidence: no public mount, anonymous -> login, same rules as the list
rm -f tess.cookies cara.cookies; login tess.cookies campus-ops T-2001; login cara.cookies campus-ops E-1003
PHOTO=$(curl -s -b tess.cookies $B/requests | grep -oE 'href="/requests/[0-9]+">REQ-2026-0002' | grep -oE '[0-9]+' | head -1 \
  | xargs -I{} curl -s -b tess.cookies $B/requests/{} | grep -oE 'src="/evidence/[0-9]+"' | head -1 | grep -oE '[0-9]+')
curl -s -o /dev/null -w '%{http_code}\n' $B/uploads/evidence/1/2/ac-filter.png            # 404 (no public mount)
curl -s -o /dev/null -w '%{http_code} -> %{redirect_url}\n' $B/evidence/$PHOTO            # 303 -> /login?next=...
curl -s -b tess.cookies -o /dev/null -w '%{http_code} %{content_type}\n' $B/evidence/$PHOTO   # 200 image/png (assigned tech)
curl -s -b mgr.cookies  -o /dev/null -w '%{http_code}\n' $B/evidence/$PHOTO                # 200 (manager, same company)
curl -s -b cara.cookies -o /dev/null -w '%{http_code}\n' $B/evidence/$PHOTO                # 404 (other employee)
curl -s -b sam-harbor.cookies -o /dev/null -w '%{http_code}\n' $B/evidence/$PHOTO          # 404 (other company)

# fronts: Harbor Yard brand override vs default
curl -s -b sam-harbor.cookies $B/ | grep -oE 'data-front="[^"]+"|Harbor Yard Maintenance' | sort -u   # harbor-yard + override title
curl -s -b mgr.cookies $B/        | grep -oE 'data-front="[^"]+"'                                     # _default

# company look: stable hook per company + Harbor palette from its front file
curl -s -b sam-harbor.cookies $B/ | grep -oE '<body[^>]*>'                # data-company="harbor-yard" data-theme="harbor-yard"
curl -s -b mgr.cookies $B/        | grep -oE '<body[^>]*>'                # data-company="campus-ops" data-theme="_default"
curl -s -b sam-harbor.cookies $B/ | sed -n '/id="front-theme"/,/<\/style>/p' | grep -oE -- '--(accent|bg): #[0-9a-f]+;'   # --accent: #1f5f8b; --bg: #e9eff4;

# log_work rules (Theo: REQ-0003 completed, REQ-0004 open)
rm -f theo.cookies; login theo.cookies campus-ops T-2002
ids() { curl -s -b theo.cookies $B/requests | grep -oE "href=\"/requests/[0-9]+\">$1" | grep -oE '[0-9]+' | head -1; }
DONE=$(ids REQ-2026-0003); OPEN=$(ids REQ-2026-0004)
post_log() { curl -s -b theo.cookies -c theo.cookies -X POST $B/maintenance --form-string csrf_token="$(token theo.cookies)" \
  --form-string request_id="$1" --form-string work_performed="curl check" --form-string service_status_id="$2" "${@:3}" \
  | grep -oE 'flash-(error|ok)">[^<]*'; }
post_log $DONE 2                                                          # already closed
post_log $OPEN 1                                                          # PENDING not allowed
post_log $OPEN 2 --form-string material_name=Filter --form-string material_quantity=-3   # quantity > 0
printf 'not an image' > /tmp/fake.png
post_log $OPEN 2 -F "evidenceFile=@/tmp/fake.png;type=image/png"          # photo not accepted, nothing saved

# logout without token keeps the session; with token clears it
curl -s -b ada.cookies -c ada.cookies -X POST $B/logout -o /dev/null -w '%{http_code} -> %{redirect_url}\n'   # 303 -> .../
curl -s -b ada.cookies $B/ -o /dev/null -w '%{http_code}\n'             # 200 (still signed in)
curl -s -b ada.cookies -c ada.cookies -X POST $B/logout --data-urlencode csrf_token="$(token ada.cookies)" \
  -o /dev/null -D - | grep -iE '^(location|set-cookie)'                 # location: /login, cookie cleared
```

---

## Upgrade notes

Compared with the original single-tenant app:

- **Database:** companies, accounts, memberships, per-company locations and tickets, the ticket counter, evidence paths and theme tokens are all schema changes. On start-up an older `maintenance.db` is moved aside as `maintenance.db.outdated-<timestamp>`, with nothing deleted, and a fresh demo database is created. There are no in-place migrations.
- **Login** needs a company. The password belongs to the person (`account_credential`, previously `user_credential`).
- **`/uploads` is no longer served.** Photos are at `/evidence/{id}` for signed-in users who can see the ticket.
- **Templates** moved from `app/templates` to `app/fronts/_default/templates`.
- **Every POST** needs the form's `csrf_token`; scripts must read it from a page first.
- **`for_testing`** lives at the repo root; `--cov` and `--e2e` are optional.
- **`SECRET_KEY`:** a missing or placeholder value is replaced by a random one at start-up, so sessions reset on restart until you set a real key.

---

## Known limitations

- **No login rate limiting or lockout.** Put the app behind a proxy that rate-limits `POST /login`, or add throttling before exposing it.
- **Demo people are seeded** into every fresh database with `DEMO_PASSWORD`. Change that password, or remove the demo accounts, before real use.
- **No admin UI** for companies and people ([use the shell](#adding-a-company-or-a-membership)); no password change or reset.
- **No migrations:** schema changes set the old SQLite file aside (see above). Other databases stop with a clear error instead.
- **The size limit relies on `Content-Length`.** A chunked upload without it is bounded only by the 5 MB photo check, after the body has been received.
- **A refused oversized upload** may show as a reset connection in some browsers: the server answers `413` without reading the body.
- **Tested on SQLite only.** The per-company ticket counter relies on row locking, which PostgreSQL also provides.
- **Language switching** (`/lang/<code>`) is a GET without a CSRF token; it only changes the UI language.

---

## Layout

```
for_testing                  (repo root, next to maintenances/) pytest [--cov] [--e2e], then uvicorn
maintenances/
  app/
    main.py                  App factory: session + security middleware, error handlers, routers
    config.py                Settings from env / .env
    db.py                    Engine, init_db (outdated-schema check, create_all, seed), get_db
    models.py                Company, Account/AccountCredential, AppUser (membership), catalogs,
                             Location, ServiceRequest, MaintenanceRecord, MaterialUsed,
                             PhotoEvidence, TicketCounter
    auth.py                  PBKDF2 passwords, company-scoped login, session helpers
    csrf.py                  Per-session CSRF token, require_csrf dependency
    security.py              Session secret, security headers, request size limit
    deps.py                  render(): front resolver, i18n, CSRF token, evidence URLs, theme tokens
    exceptions.py            BusinessError(message, message_key, **params)
    i18n.py                  Language files loader, t() / labels / error text
    locales/                 en.json, es.json
    theme_tokens.py          Generic theme token allowlist + hex validation
    timeutil.py              utcnow() as naive UTC
    seed.py                  Demo companies, themes, locations, people, tickets, evidence images
    services/                companies, users, requests (visibility, ticket counter), maintenance
                             (log-work rules), reports, evidence, catalog
    routers/                 auth, web, requests (+ detail), maintenance, reports, evidence, api
    fronts/
      __init__.py            Resolver: templates_for(slug), search_path(), resolved_front()
      _default/templates/    All templates (+ partials/brand.html, partials/theme.css)
      harbor-yard/templates/ Demo override: partials/brand.html, partials/theme.css
    static/css|js            Styles (design tokens), material rows, themed canvas charts
  scripts/
    e2e_check.py             End-to-end harness (stdlib only)
    pack_archive.sh          Clean zip of the project
  tests/                     pytest suite (see Tests)
  requirements.txt
  .coveragerc                Coverage source + 90% gate
  .env.example
```

---

## Notes

- Default DB is SQLite (`DATABASE_URL` in `.env.example`).
- Demo credentials only (table above). Do not commit real secrets or a `.env` with production keys.
- Planning notes that guided this work: single tenant to multi-company, in seven steps (company model, users, locations/requests, session/header, front resolver, themes, isolation tests) plus hardening. All steps are implemented; the limitations above are what's left.
- `scripts/pack_archive.sh` builds a clean zip (`for_testing` + `maintenances/`) with no `.git`, `venv`, `*.db`, or `.env`. It needs `rsync` and `zip`.
