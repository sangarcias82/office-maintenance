# Office Maintenance

Web app for facility maintenance: employees report failures, area managers assign technicians, technicians log work (materials and optional photo evidence), and managers review a filtered report with charts.

**Stack:** FastAPI · SQLAlchemy · SQLite · Jinja2 · HTML/CSS/JS  
**UI languages:** English / Spanish (EN · ES in the header)

No Docker, Redis, or Postgres required.

---

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

Or run the local helper (creates a venv if needed, runs tests, then starts the app):

```bash
./for_testing
```

---

## Demo logins

All seed users share the same password:

| Login (email or employee number) | Role |
|----------------------------------|------|
| `ada.employee@facility.example` or `E-1001` | Employee |
| `tess.tech@facility.example` or `T-2001` | Technician |
| `morgan.manager@facility.example` or `R-3001` | Area manager |

**Password:** `OfficeDemo1!`

Password hashes live in `user_credential`, not on the `AppUser` model exposed to templates.

---

## Features

| Screen | Path | Who |
|--------|------|-----|
| Sign in | `/login` | Everyone |
| Home | `/` | Signed-in users |
| New service request | `/requests/new` | Employee, Area manager |
| Request list (filter / assign) | `/requests` | Role-scoped |
| Log work + photo | `/maintenance/new` | Technician |
| Maintenance report + charts | `/reports` | Area manager |
| Health | `/api/health` | Public |

| Role | Can |
|------|-----|
| Employee | Create requests; see own tickets |
| Technician | See assigned tickets; log work, materials, status, photos |
| Area manager | Create requests; see all tickets; assign technicians; open report |

Protected routes require a session. Failed logins use one shared message (no user enumeration).

---

## Tests

```bash
pytest -q
```

Coverage optional:

```bash
pytest -q --cov=app --cov-report=term-missing
```

---

## Smoke with curl

```bash
# health
curl -s http://127.0.0.1:8000/api/health

# login + home (cookie jar)
curl -s -c jar.txt -b jar.txt -X POST http://127.0.0.1:8000/login \
  -d 'login=ada.employee@facility.example&password=OfficeDemo1!&next=/' -o /dev/null -w '%{http_code}\n'
curl -s -c jar.txt -b jar.txt http://127.0.0.1:8000/ | head
```

---

## Layout

```
app/
  main.py              FastAPI app + session middleware
  models.py            Domain tables (catalogs, users, requests, records)
  auth.py              PBKDF2 passwords + session helpers
  seed.py              Catalogs + demo users + sample tickets
  services/            Request / maintenance / report rules
  routers/             HTTP routes
  templates/           Jinja2 HTML
  static/css|js        Styles + materials rows + canvas charts
tests/
for_testing            Local helper: pytest then uvicorn
requirements.txt
.env.example
scripts/pack_archive.sh
```

---

## Notes

- Default DB is SQLite (`DATABASE_URL` in `.env.example`).
- Demo credentials only (table above). Do not commit real secrets or a `.env` with production keys.
- `scripts/pack_archive.sh` builds a clean zip of the project (no `.git`, `venv`, `*.db`, or `.env`).
