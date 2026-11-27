# Research Dossier — query-driven structured reports

A small, dependency-free web app built from the **Lab 10 CrewAI** notebook. Type any
topic or a head-to-head comparison; the Python backend retrieves sources and returns a
**structured JSON report** that the frontend renders as cards, tables, charts and citations.

Nothing is hardcoded in the page. The topic lives in the query.

## Run

```bash
cd lab10-app
python3 app.py            # http://localhost:8000
# or: python3 app.py 8080
```

Only the Python standard library is required.

## How it works

```
browser ──POST /api/research {query}──▶ app.py
                                        │
                                        ├─ 1. curated corpus match (corpus.json)
                                        ├─ 2. Tavily Search (if TAVILY_API_KEY is set)
                                        └─ 3. Wikipedia search + page summaries (default)
                                        │
browser ◀──── structured JSON ──────────┘
```

The frontend renders only the blocks present in the response, so a new topic needs no
frontend change — add a corpus entry or a retrieval source.

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
  "mode": "curated | tavily | wikipedia | offline",
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
{ "status": "ok", "service": "lab10-research-dossier", "tavily": false, "corpus": 1 }
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

## Optional: live web search with Tavily

```bash
export TAVILY_API_KEY="tvly-…"
python3 app.py
```

When the key is set, queries that miss the corpus go through Tavily Search first. Without
it, the backend falls back to Wikipedia (no key required).

## Files

| File | Purpose |
|---|---|
| `app.py` | HTTP server, retrieval, report builders, cost model |
| `index.html` | Query-driven frontend (renders the JSON blocks) |
| `corpus.json` | Curated reports (e.g. the Lab 10 Serper vs Tavily comparison) |

## Notes

- The Serper/Tavily figures are curated from the Lab 10 notebook research run. Verify
  pricing before committing to a plan.
- Wikipedia summaries can be incomplete or dated; the response includes a disclaimer.
- The app binds the frontend to the backend over `fetch`; no build step, no framework.
