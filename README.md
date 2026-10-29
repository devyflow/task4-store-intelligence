# Store Intelligence Dashboard — Task 4

A complete implementation of the data-cleaning, BI, grounded natural-language Q&A, and dashboard-control requirements. The app processes all five supplied stores and exposes a public-ready FastAPI dashboard.

## Requirement coverage

### Part 1 — data preparation and BI

- Reads every `STORE*` directory: users CSV, books YAML, and orders Parquet.
- Repairs malformed USD/EUR price strings and converts EUR to USD at `1 EUR = 1.2 USD`.
- Parses timestamps, extracts calendar dates, removes duplicate IDs, validates foreign keys, and calculates `paid_price = quantity * unit_price`.
- Reconciles user aliases with union-find when any three of name, address, phone, and email match—the supplied assumption that only one field changes.
- Calculates, per store: top five revenue days, real unique users, unique author sets, most popular author set by units, and the highest-spending real customer with every source user ID.
- Renders a daily-revenue chart and professional store-by-store dashboard.

### Part 2 — natural-language Q&A grounded in APIs

The model never receives a raw dataset dump. `agent.py` defines strict function tools and requires a tool call before the first answer:

- `get_store_overview`
- `query_users`
- `query_books`
- `query_revenue`

The application executes parameterized SQLite queries in `data_api.py`, then sends only bounded, computed results to the model. Counts and sums therefore come from SQL, not model arithmetic. The response includes a visible tool trace for grading.

### Part 3 — natural-language dashboard control

The `control_dashboard` tool returns a validated `dashboard_update` action. The browser applies it by switching stores/views and calling the same deterministic filter APIs. Examples:

- `Filter out books that have people's names in the title.`
- `Select users with e-mails starting with s or p in STORE2.`
- `Switch to STORE4 and show only titles containing love.`
- `Reset STORE1 to the overview.`

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env.local
# Set OPENAI_API_KEY in .env.local
python analysis.py
python build_database.py
python -m uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`.

## Verify

```powershell
python -m unittest -v
```

The tests cover malformed prices and EUR conversion, identity reconciliation, full STORE1 analytics, deterministic e-mail-prefix selection, person-name title filtering, and dashboard action validation.

Useful direct checks:

```text
GET /health
GET /api/overview/STORE1
GET /api/users/STORE2?email_prefix=s&email_prefix=p
GET /api/books/STORE1?person_name_filter=exclude
POST /api/chat
```

`POST /api/chat` body:

```json
{"message":"How many users have e-mails starting with s or p?","current_store":"STORE2"}
```

## Deploy

The included `Dockerfile` and `render.yaml` support a Render Blueprint deployment. Set `OPENAI_API_KEY` as a secret environment variable. On a clean start, `app.py` regenerates both `analytics.sqlite3` and `outputs/metrics.json` from the supplied source files, so derived artifacts do not need to be committed.

## Architecture

```text
Raw CSV/YAML/Parquet
        |
        +--> analysis.py ------> dashboard metrics
        |
        +--> build_database.py -> SQLite
                                  |
Browser dashboard <--- FastAPI <-+-> deterministic DataAPI
                                      ^
                                      |
                              strict model tools
```

## Security and reliability

- API keys are loaded from environment files that are excluded from Git.
- SQL filters are parameterized and store/view values are allow-listed.
- Tool schemas use strict argument validation.
- Results returned to the model are bounded; raw datasets are never placed in prompts.
- The agent refuses to estimate when no tool can answer a question.
