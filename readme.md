# IPO Calendar App — Project Workflow (Interview Reference)

## 1. One-line summary

A Flask backend (with a React frontend) that scrapes live IPO data (GMP, subscription, pricing) from investorgain.com, runs it through a trained ML classifier to estimate each IPO's "apply probability," caches everything for fast repeated reads, persists state in a hosted Postgres database, and emails subscribers when new IPOs open — all deployable on free-tier infrastructure.

## 2. Problem / motivation

The original version pulled data from two SheetDB-backed endpoints (i.e. manually maintained Google Sheets) and POSTed prediction results back to a third sheet. This had two problems: the data was only as fresh as whoever last updated the sheet, and there was no real persistence model — predictions were just written back to the same spreadsheet. The project was rebuilt to pull live data directly from a public source (investorgain.com) and to use real backing services (a database and a cache) instead of a spreadsheet as a data store.

## 3. Tech stack

- **Backend:** Python, Flask, Flask-CORS, Flask-Mail
- **Data/ML:** pandas, scikit-learn (pickled model + scaler), NumPy under the hood via sklearn
- **Scraping:** requests, BeautifulSoup4 (+ lxml parser)
- **Persistence:** PostgreSQL, hosted on Neon (free tier), accessed via psycopg2 with a connection pool
- **Cache:** Redis, hosted on Upstash (free tier, REST-based client)
- **Email:** Gmail SMTP via Flask-Mail
- **Deployment target:** Render (free web service tier), gunicorn as the production WSGI server
- **Frontend:** React (consumes the REST API below; calendar UI)

## 4. High-level architecture

```
investorgain.com (internal JSON API)
        │  scraped by scraper.py
        ▼
 ┌─────────────────┐        ┌────────────────────┐
 │   Upstash Redis  │◄──────►│     scraper.py     │
 │  (shared cache)  │        │  fetch + parse +    │
 └─────────────────┘        │  cache orchestration│
                             └─────────┬───────────┘
                                       │
                    ┌──────────────────┼───────────────────┐
                    ▼                  ▼                    ▼
             ┌─────────────┐   ┌──────────────┐     ┌──────────────┐
             │   app.py    │   │  ML model     │     │   store.py   │
             │ Flask routes│──►│ (pickled,     │     │ (Neon Postgres)│
             │ + notif loop│   │  predict_proba)│     │ notified_ipos │
             └──────┬──────┘   └──────────────┘     │ manual_ipos   │
                    │                                 │ subscribers   │
                    ▼                                 └──────────────┘
             React frontend
             (calendar UI, consumes REST endpoints)
```

## 5. Request/data-flow walkthrough

**Startup:**
1. `app.py` loads `ipo_model.pkl` and `scaler.pkl` into memory.
2. Starts the background notification thread (`schedule_daily_notifications`).
3. Synchronously calls `predict()` once so the cache is primed before the first real request arrives.
4. Starts serving via gunicorn (prod) or the Flask dev server (local).

**A typical `/predict` call:**
1. `scraper.get_raw_ipo_data()` checks Redis for a "still fresh" marker key. If missing/expired, it hits `fetch_all_ipos()`, which calls investorgain.com's internal JSON endpoint directly (not HTML scraping — see §6).
2. Each row is parsed and cleaned (price, GMP, subscription, dates, etc. — see §6 for the specific parsing rules).
3. The cleaned list is written back into Redis (`ipo:data`, no expiry — kept as "last known good" — plus a `ipo:data:fresh` key with a 10-minute TTL as the staleness marker).
4. `app.py` turns this into a pandas DataFrame, drops rows that don't have the features the model needs (unpriced/incomplete IPOs), scales the feature columns with the pre-fit `StandardScaler`, and calls `model.predict_proba()` to get an `Apply_Probability` per IPO.
5. Results are sorted by probability and cached via `scraper.update_ranked_ipos()` (written to `ipo:ranked` in Redis).
6. JSON response returned to the caller.

**A typical `/api/ipo_data` call:**
1. `scraper.get_ipo_data()` returns the ranked cache if present, else the raw cache, refreshing from investorgain.com first if stale.
2. Manually-added IPOs from Postgres (`store.get_manual_ipos()`) are merged in, taking precedence over scraped data for any `(IPO name, Apply Date)` that overlaps.
3. Grouped by `Apply Date` (or filtered to a single date if `?date=` is passed) and returned as JSON.

**Background notification loop (every 10 minutes, in a daemon thread):**
1. Pulls current cached IPO data.
2. Filters to IPOs whose `Apply Date` falls in the current month.
3. For each, checks `store.is_notified(ipo_name, apply_date)` — skips if already sent.
4. If not yet sent: emails everyone in `store.get_subscribers()`, then calls `store.mark_notified()`.

## 6. Component deep-dive

### `scraper.py`
- Calls investorgain.com's **internal JSON API** directly (`webnodejs.investorgain.com/cloud/v2/report/data-read/...`, report ID `331` — the "Live IPO GMP" report) rather than scraping the rendered HTML page. This is more stable against front-end/CSS changes, at the cost of depending on an undocumented endpoint that could change without notice — mitigated with browser-like headers (`User-Agent`, `Origin`, `Referer`) and defensive error handling.
- **Field parsing/cleaning per row:**
  - `IPO` name: prefers a dedicated API field, falls back to stripping HTML from a `Name` field.
  - `Price`: handles both a single value and a price band (e.g. `"100-105"`) by taking the upper bound (cap price), since GMP percentages are typically computed against the cap.
  - `GMP`: prefers a numeric API field, falls back to parsing an HTML-formatted field via BeautifulSoup if the numeric one is missing/malformed.
  - `IPO Size`: converts from "₹125.00 Cr" text into an absolute rupee figure (`Cr × 1e7`) for the model.
  - `Subscription`: strips the trailing `x` and parses to float (e.g. `"2.95x"` → `2.95`).
  - Dates (`Apply Date`, `Close Date`, `Listing Date`): parsed and normalized to `YYYY-MM-DD`; invalid/empty values become `None`/empty string rather than raising.
  - `GMP_to_IPO_Ratio`: computed as `GMP / Price` (guarded against divide-by-zero for unpriced IPOs).
  - Deliberately does **not** drop rows just because `price == 0` — IPOs without a finalized price band still show up on the calendar; the `/predict` route is what filters those out before feeding the model.
- **Caching:** originally an in-process dict guarded by a `threading.Lock`; migrated to Upstash Redis so the cache survives process restarts/redeploys and stays consistent if the app ever runs multiple workers. Three keys: `ipo:data` (last-known-good, no expiry), `ipo:data:fresh` (a TTL'd marker — its presence means the data doesn't need re-fetching yet), `ipo:ranked` (last prediction results).
- **Manual override merge:** `_merge_manual_overrides()` layers entries from `store.get_manual_ipos()` on top of scraped data, keyed by `(IPO, Apply Date)`, so a manually-added/edited IPO always wins over the scraped version.

### `store.py`
- All persistence lives in Postgres (Neon), accessed through a small `ThreadedConnectionPool` (1–5 connections) rather than opening a new connection per call.
- **Tables:**
  - `notified_ipos (ipo_name, apply_date, notified_at)` — dedup ledger for email notifications.
  - `manual_ipos (ipo_name, apply_date, data JSONB)` — manually-added/overridden IPO entries (replaces the old SheetDB-based add flow).
  - `subscribers (email, subscribed_at)` — notification recipient list (replaces a hardcoded list, then an env var, now a proper table managed via API).
- Tables are auto-created on import (`_init_db()` runs at module load) — no manual migration step needed on a fresh database.
- SSL is enforced on the connection string (`sslmode=require`) since Neon requires it.

### `app.py` — API surface
| Method | Route | Purpose |
|---|---|---|
| GET | `/` | Health check |
| GET | `/predict` | Scrape → feature-engineer → run model → cache ranked results → return them |
| GET | `/api/ipo_data` | Return all IPO data grouped by date, or filtered to `?date=` |
| GET | `/api/ipo_data_range?start=&end=` | Return IPOs with `Apply Date` in `[start, end]` |
| POST | `/api/add_ipo` | Add/override a manual IPO entry (JSON body) |
| DELETE | `/api/add_ipo?ipo=&date=` | Remove a manual override |
| GET | `/api/subscribers` | List notification subscribers |
| POST | `/api/subscribers` | Add a subscriber (JSON `{"email": ...}`) |
| DELETE | `/api/subscribers?email=` | Remove a subscriber |
| POST | `/api/refresh` | Force a fresh scrape + re-run prediction |

### ML model
- A pre-trained classifier (`ipo_model.pkl`) and a fitted `StandardScaler` (`scaler.pkl`), both loaded once at startup via `pickle`.
- **Features:** `Subscription`, `GMP`, `IPO Price`, `GMP_to_IPO_Ratio`.
- **Output:** `predict_proba()`'s positive-class probability is used as `Apply_Probability` — a rough estimate of how "worth applying to" an IPO is, based on how the model was trained (subscription heat + grey market premium being the core signal).
- Rows missing any required feature, or with `IPO Price <= 0` (not yet priced), are excluded from prediction — but still visible via `/api/ipo_data` for calendar display.

## 7. Caching strategy (why Redis, and how)

- **Why Upstash specifically:** it exposes Redis over HTTPS REST in addition to the standard protocol. That matters because Render's free tier spins the app down after 15 minutes idle — a persistent TCP connection to a traditional Redis instance would need to be re-established awkwardly around that; the REST client just makes a request when needed.
- **TTL design:** rather than a single "is this stale" timestamp, freshness is tracked with a *separate* TTL'd marker key (`ipo:data:fresh`) from the actual data key (`ipo:data`, no TTL). This means a failed scrape still leaves the last successful data available to serve, instead of the cache going empty the moment the TTL lapses.

## 8. Engineering problems identified and fixed (good interview talking points)

1. **Notification spam bug:** the original loop re-sent an email for every IPO open in the current month on every 10-minute tick, with no dedup. Fixed by tracking `(ipo_name, apply_date)` pairs already notified in Postgres and checking before sending.
2. **Flask debug reloader double-execution:** `app.run(debug=True)` forks a second process via Werkzeug's auto-reloader; the startup `predict()` call and the notification thread were both running twice. Fixed with `use_reloader=False`.
3. **Price-band parsing bug:** the original regex grabbed only the *first* number in a price field, silently truncating bands like `"100-105"` to `100` and, combined with a `price == 0` filter, dropping not-yet-priced IPOs from the calendar entirely. Fixed by taking the max of all numbers found, and removing the blanket zero-price exclusion at the scraper level (moved that filtering to just the `/predict` stage, where it belongs).
4. **Dead code:** an HTML-GMP-parsing helper was written but never actually called (the numeric field was used unconditionally). Wired it in as an explicit fallback.
5. **Thread-safety gap:** the original in-memory cache dict had no locking around reads/writes. Addressed first with a `threading.Lock`, then made moot entirely by moving the cache to Redis (atomic per-command, safe across processes).
6. **Ephemeral storage risk:** the initial "fix" used a local SQLite file for persistence — which would be wiped on every Render free-tier redeploy (no persistent disk on that tier). Migrated persistence to a hosted Postgres instance (Neon) instead.
7. **Hardcoded secrets/config:** a personal email address and mail credentials were hardcoded in source. Moved to environment variables, then the subscriber list itself was moved out of a static env var into a proper database table with CRUD endpoints, so subscribers can be managed without redeploying.

## 9. Deployment

- **Host:** Render, free web service tier. Constraints: no persistent disk, spins down after 15 min idle (30–60s cold start on the next request), 750 free instance-hours/month.
- **Database:** Neon Postgres, free tier (permanent, scales to zero when idle, 0.5 GB storage) — chosen over Render's own free Postgres because Render's free database auto-expires after 30 days.
- **Cache:** Upstash Redis, free tier (permanent, 256 MB / 500K commands per month).
- **Process manager:** gunicorn in production (`gunicorn app:app`), Flask dev server locally.
- **Secrets:** `DATABASE_URL`, `UPSTASH_REDIS_REST_URL`, `UPSTASH_REDIS_REST_TOKEN`, `MAIL_USERNAME`, `MAIL_PASSWORD` — set via Render's dashboard environment variables (never committed; `.env` is git-ignored).

## 10. Trade-offs and deferred decisions (good "what would you do differently" material)

- **Celery was considered** for the notification loop and for making `/predict` non-blocking. Upstash Redis could serve as the broker at no extra cost, but Render's background-worker services aren't free (~$7/month+), so it was deferred in favor of the current in-process thread. A lighter middle ground (APScheduler — in-process, no separate broker/worker needed) was proposed as the next step if the raw `threading` loop needs to become more robust, without paying for a dedicated worker.
- **Undocumented upstream API dependency:** relying on investorgain.com's internal endpoint (rather than public, documented data) means the scraper can break without warning if the site changes. There's no contractual guarantee of stability — a known and accepted risk for a personal project at this scale.
- **SME vs. Mainboard IPOs:** the scraper currently pulls both categories, but whether the ML model was trained on both is an open question worth double-checking — feeding it out-of-distribution categories could produce misleading probabilities.
- **No historical persistence of scraped data:** only the current snapshot is cached; there's no time-series table of past GMP/subscription movement, which would be needed for anything like backtesting the model's predictions against actual listing outcomes.
- **Single-instance cache consistency:** the connection pool and Redis-backed cache are designed to be safe under Render's single free-tier instance; true multi-instance/multi-worker consistency wasn't a design target since the free tier doesn't offer that anyway.

## 11. Likely interview questions this project sets up well

- "Walk me through what happens when a user opens the calendar." → §5.
- "Why Postgres and Redis specifically, and why those hosts?" → §7, §9.
- "What bugs did you find in your own code, and how did you catch them?" → §8.
- "How would this change if traffic grew 100x?" → multi-instance cache/DB consistency, moving off free tiers, Celery for real async job handling, rate-limiting/backoff on the scrape target.
- "What's the riskiest part of this system?" → dependency on an undocumented third-party API with no SLA.