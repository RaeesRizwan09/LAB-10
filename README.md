# LAB-10 — Agentic AI research dossier

Lab 10 notebook plus a query-driven web app that turns any topic into a structured report.

## Notebook

`Lab10.ipynb` — the CrewAI multi-agent system from the lab, including the Serper vs Tavily research run.

## App

`app/` — a dependency-free Python backend and frontend.

```bash
cd app
export TAVILY_API_KEY="tvly-…"   # or put it in app/.env
python3 app.py                   # http://localhost:8000
```

Type any topic, or two things compared with `vs`. The backend checks a curated corpus
first, then retrieves live sources through **Tavily — the only search provider**. There is
no Wikipedia or other fallback: if `TAVILY_API_KEY` is missing, the app returns a
structured "Tavily key required" report. Results come back as structured JSON that the
page renders as cards, tables, charts and citations.

See [`app/README.md`](app/README.md) for the response schema and how to add a topic
without touching the frontend.
