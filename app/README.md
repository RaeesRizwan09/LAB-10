# Research Dossier — query-driven structured reports

A small, dependency-free web app built from the **Lab 10 CrewAI** notebook. Type any
topic or a head-to-head comparison; the Python backend retrieves sources and returns a
**structured JSON report** that the frontend renders as cards, tables, charts and citations.

Nothing is hardcoded in the page. The topic lives in the query. **Tavily is the only live
search provider** — there is no Wikipedia or other fallback.

## Run

```bash
cd lab10-app
export TAVILY_API_KEY="tvly-…"   # or put it in app/.env
python3 app.py                   # http://localhost:8000
# or: python3 app.py 8080
```

Only the Python standard library is required.

## How it works

```
browser ──POST /api/research {query}──▶ app.py
                                        │
                                        ├─ 1. curated corpus match (corpus.json)
                                        │      └─ hit  → local report, no network call
                                        │
                                        └─ 2. Tavily Search  (the only live provider)
                                               ├─ key set      → live results
                                               ├─ key missing  → "Tavily key required"
                                               └─ no results   → "No Tavily results"
                                        │
browser ◀──── structured JSON ──────────┘
```

The frontend renders only the blocks present in the response, so a new topic needs no
frontend change — add a corpus entry or a working Tavily key.

### Tavily request

```python
POST https://api.tavily.com/search
{
  "api_key":            "tvly-…",
  "query":              "<user query>",
  "search_depth":       "advanced",
  "max_results":        5,
  "include_answer":     True,
  "include_raw_content": True
}
```

`include_raw_content` returns page content, which is the closest Tavily gets to crawling.
Tavily is a search API, not a general-purpose crawler: it does not spider arbitrary sites.

## API

### `POST /api/research`

```json
{ "query": "Python vs JavaScript" }
```

### `GET /api/research?q=Python%20vs%20JavaScript`

Shareable form of the same request.

### Response shape

Every field except `query`, `mode`, `generated_at` and `title` is optional:

```json
{
  "query": "Serper vs Tavily",
  "mode": "curated | tavily | offline",
  "generated_at": "2026-11-27 10:14 UTC",
  "title": "Serper vs Tavily",
  "subtitle": "AI Search API comparison · 2026",
  "summary": "…",
  "stats":   [{ "value": "5–26.7×", "label": "Serper cheaper per search" }],
  "cards":   [{ "tone": "a|b", "tag": "SERP API", "title": "…", "lede": "…", "points": ["…"] }],
  "table":   { "caption": "…", "label_header": "Searches / month",
               "columns": ["Serper", "Tavily", "Gap"],
               "rows": [{ "label": "1,000", "values": ["$1.00", "$8.00", "8×"] }] },
  "chart":   { "caption": "…", "unit": "USD per 1,000 searches",
               "series": [{ "label": "Serper", "value": 1.0, "tone": "a" }] },
  "calculator": { "enabled": true, "label": "Monthly searches",
                  "min": 1000, "max": 1000000, "step": 1000, "default": 10000 },
  "picks":   [{ "heading": "Pick Serper when…", "tone": "a",
                "items": [{ "title": "…", "body": "…" }] }],
  "warnings": [{ "title": "…", "body": "…" }],
  "sections": [{ "heading": "…", "body": "…" }],
  "sources":  [{ "title": "…", "url": "https://…", "snippet": "…" }],
  "disclaimer": "…"
}
```

### `POST /api/compare`

Cost estimate used by the calculator:

```json
{ "volume": 250000 }
```

```json
{ "volume": 250000.0, "serper": 133.33, "tavily": 2000.0, "gap": 15.0, "note": "…" }
```

### `GET /api/health`

```json
{ "status": "ok", "service": "lab10-research-dossier", "provider": "tavily", "tavily": false, "corpus": 1 }
```

## Adding a topic without code

Append an object to `corpus.json` with a `match` list of keywords and any of the
response blocks above. If a query contains one of the keywords, that curated report is
returned before live retrieval.

```json
[
  {
    "id": "my-topic",
    "match": ["my-topic", "my topic"],
    "title": "My topic",
    "summary": "…",
    "sections": [{ "heading": "Overview", "body": "…" }],
    "sources": [{ "title": "…", "url": "https://…", "snippet": "…" }]
  }
]
```

## Live web search with Tavily (required)

Tavily is the only live search provider. Set the key in the environment or in
`app/.env`:

```bash
export TAVILY_API_KEY="tvly-…"
python3 app.py
```

```
# app/.env
TAVILY_API_KEY=tvly-…
```

Queries that miss the curated corpus go through Tavily Search. If the key is missing,
the backend returns a structured **“Tavily key required”** report — it does not fall back
to Wikipedia or any other provider. If Tavily returns nothing, it returns a structured
**“No Tavily results”** report.

## Files

| File | Purpose |
|---|---|
| `app.py` | HTTP server, Tavily retrieval, report builders, cost model |
| `index.html` | Query-driven frontend (renders the JSON blocks) |
| `corpus.json` | Curated reports (e.g. the Lab 10 Serper vs Tavily comparison) |
| `.env.example` | Template for `TAVILY_API_KEY` (copy to `.env`) |

## Notes

- The Serper/Tavily figures are curated from the Lab 10 notebook research run. Verify
  pricing before committing to a plan.
- Live summaries come from Tavily and may be incomplete; the response includes a
  disclaimer. Tavily is a search API, not a general crawler.
- The app binds the frontend to the backend over `fetch`; no build step, no framework.
