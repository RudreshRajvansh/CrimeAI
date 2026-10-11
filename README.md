# CrimeAI

Decision support for Indian police units. CrimeAI turns FIRs and incident records into work items: where to patrol next shift, which incidents are probably one offender, which accused will get default bail unless a chargesheet is filed this week, and which FIRs cite the wrong criminal code after the move from IPC to BNS.

| | |
|---|---|
| **Stack** | FastAPI · SQLAlchemy 2 (async) · PostgreSQL 16 + PostGIS + pgvector · Redis · Celery · scikit-learn · sentence-transformers (multilingual-e5) · Google Gemini · spaCy · React 18 + TypeScript + Vite |

## What it does

| Capability | Who uses it | What they get |
|---|---|---|
| **Shift forecast** | Station House Officers | Next 24 hours by shift (night, morning, afternoon, evening), with districts ranked by probability of elevated crime and the model version that produced each figure |
| **Risk index** | Senior officers | Absolute 0–100 harm-weighted index per district with a trend against the district's own baseline; alerts only when a district moves up a level |
| **Crime series linkage** | Analysts | Incidents linked by modus operandi, place and time, including across police-station boundaries, with an expected next-incident window |
| **Custody clock** | Investigating officers | Chargesheet deadline per accused under BNSS s.187(3) (60/90 days), escalating at 15, 7 and 2 days |
| **FIR intelligence** | Investigating officers, analysts | Entity extraction, IPC→BNS mapping with wrong-code warnings, crime type and severity, PII redaction (Aadhaar with Verhoeff check, PAN, phone, UPI, bank) |
| **Semantic search** | Analysts | "Find incidents like this one" over redacted narratives (pgvector HNSW) |
| **Patrol routing and what-if simulation** | Patrol planners | Risk-weighted routes from a unit's position; Monte Carlo estimates of intervention impact |
| **Link analysis** | Analysts, investigating officers | Cases joined by a shared vehicle, phone, UPI ID or bank account across stations (identifiers stored as keyed hashes), network / timeline / map views, one alert per identifier, and a one-click freeze-request draft citing every linked complaint |
| **Swarm simulator** | Senior officers | 300 Monte Carlo futures of a linked network's next move, replayed as agents on a map, with and without patrols or a freeze on the money trail |
| **Gemini case brief** | Officers who do not read the FIR's language | English translation, summary, modus operandi, missing details and section suggestions checked against the IPC-BNS crosswalk; only PII-redacted text is sent |
| **Integrations** | Station and district | CCTNS and NCRP 1930 file import, e-Sakshya evidence links with the BNSS s.105 check, CCTNS enrichment export |
| **Daily briefing** | Roll call | One-page PDF at 06:30 IST |

Every forecast describes places and shifts, never people.

## Quick start

One command installs Docker if it is missing, builds and starts everything, loads demo data, trains the models and opens the app:

```bash
start.cmd        # Windows
./start.sh       # macOS / Linux
```

It prints the app URL (`http://localhost:5173`) and the demo login (also saved in `crimeai/backend/var/demo-credentials.txt`). Also available: `stop`, `status`, `logs`, `reset` (e.g. `start.cmd stop`). The first run downloads about 3 GB and takes 10–20 minutes.

### Gemini briefs (optional)

Create `crimeai/backend/.env` (gitignored) with a key from [Google AI Studio](https://aistudio.google.com/apikey), then run `start.cmd` again:

```bash
GEMINI_API_KEY=your-key
```

Without a key the feature stays hidden and nothing leaves the server.

### Manual setup

Requires Docker with Compose v2.24+.

```bash
cd crimeai
docker compose -f docker-compose.dev.yml up --build
```

This brings up Postgres (PostGIS + pgvector), Redis, migrations, the API on `:8000`, a worker, the scheduler, and the frontend on `:5173`. To load demo data, which includes a planted cross-station chain-snatching series and custody cases near their deadlines:

```bash
docker compose -f docker-compose.dev.yml exec api python scripts/seed_data.py
```

The seed script prints a generated password for the demo users (`admin@`, `analyst@`, `police@`, `readonly@crimeai.example`), or uses `SEED_PASSWORD` if you set it. Then run `docker compose -f docker-compose.dev.yml exec worker python scripts/bootstrap_demo.py` to read the FIRs, train the models, link cases and detect series.

## Tests

```bash
cd crimeai/backend
pip install -r requirements-dev.txt
pytest tests/unit                 # no services needed
pytest tests/integration          # needs Postgres (PostGIS + pgvector) and Redis, as in CI;
                                  # DATABASE_URL must name a *_test database (tests truncate tables)
```

CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) runs lint, unit tests, integration tests against the production database image (including migrate up → down → up), a frontend type-check, lint and build, Docker builds for the `api` and `worker` targets, and dependency audits.

## Repository layout

```
crimeai/
  backend/
    app/
      api/            HTTP + WebSocket layer, middleware (audit, security headers, errors)
      core/           settings (fail-fast in prod), security, rate limiting, metrics
      services/       use-cases: auth sessions, crimes, FIR intake, ML pipelines, custody clock, briefing
      ml/             forecasting, risk index, clustering, similarity, series linkage, model registry
      nlp/            entity extraction, IPC/BNS crosswalk, PII redaction, document text extraction
      realtime/       per-process alert broker (one Redis subscription, bounded fan-out)
      workers/        Celery app, tasks, runtime (fresh event loop, locks)
    alembic/          schema migrations
    tests/            unit (no services) and integration (Postgres + Redis)
  frontend/           React SPA
```
