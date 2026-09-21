# Agent instructions — PAT (Preference Analysis Tool)

## Context.dev integration

- **Env var:** `CONTEXT_DEV_API_KEY` (server-side only). Local dev reads it from
  the gitignored `.env` via `load_dotenv()` at the top of `app.py`. Never
  hardcode the key, never put it in a `dcc.Store` or any browser payload.
- **Wrapper module:** `context_client.py` is the ONLY place that talks to
  Context.dev (official `context.dev` SDK, `client.industry.*` /
  `client.web.*`). Dash callbacks import the wrapper — never the SDK directly.
- **Endpoints in use:**
  - `GET /web/naics` → `context_client.get_naics()` (10 credits/call).
    Docs: https://docs.context.dev/api-reference/web-extraction/naics
- **Call discipline:** one explicit user click = one API call. No polling
  loops, no auto-fetch on page/subcase load. (If bulk work is ever needed, use
  `POST /batch/submit`, not a loop:
  https://docs.context.dev/guides/scrape-websites-in-batches)
- **Errors:** `CompanyNotFound` (404) and `InvalidLookup` (400) are never
  retried. `RateLimited` carries `retry_after` from the `Retry-After` header —
  surface it to the user. Timeouts/connection/5xx use bounded SDK retries then
  raise `UpstreamError`. Debug guide:
  https://docs.context.dev/optimization/troubleshooting
- **Freshness:** pass `maxAgeMs` explicitly when an endpoint supports it and
  the feature needs fresh data (NAICS has no such param; default caching
  applies).
- **Tests:** always mock `context_client` (e.g. `unittest.mock.patch`); live
  calls cost credits and are forbidden in automated tests.

## Industry Analysis view

- Sidebar entry lives in `_ANALYSIS_VIEWS` in `app.py` (`("industry",
  "Industry Analysis", "fa-solid fa-industry")`) — nav links, the
  `switch_analysis_view` callback, and the portfolio-page sidebar pick it up
  automatically. The pane is `view-industry`; its callbacks are
  `industry_init` / `industry_lookup` / `industry_save`.
- The lookup company defaults to the subcase `transferee_name`. The chosen
  code is persisted in subcase `meta` as `naics_code` / `naics_title` via
  `store.set_subcase_meta` (no schema migration needed).
