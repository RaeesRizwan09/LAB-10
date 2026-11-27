#!/usr/bin/env python3
"""Lab 10 — query-driven research dossier (Tavily-only retrieval).

A dependency-free backend (Python standard library only) that turns a free-form
query into a structured report the frontend can render.

Retrieval policy
----------------
* Curated corpus match (corpus.json)  -> local report, no network call.
* Everything else                     -> Tavily Search. Tavily is the ONLY
  live search provider. There is no Wikipedia (or other) fallback.
* No TAVILY_API_KEY                   -> a structured "key required" report.

Tavily is called with:
    search_depth       = "advanced"
    include_answer     = True
    include_raw_content= True   (page content, the closest thing to a crawl)
    max_results        = 5

Routes
------
GET  /                  -> frontend
GET  /api/health        -> liveness
GET  /api/research?q=…  -> structured report (shareable)
POST /api/research      -> structured report {"query": "…"}
POST /api/compare       -> Serper/Tavily cost estimate {"volume": 10000}

Run:  python3 app.py [port]
Then open http://localhost:8000
"""

import datetime
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CORPUS_PATH = os.path.join(BASE_DIR, "corpus.json")
ENV_PATH = os.path.join(BASE_DIR, ".env")
USER_AGENT = "Lab10ResearchDossier/1.0 (educational; local)"

MAX_QUERY = 300
COMPARE_RE = re.compile(
    r"^\s*(.+?)\s+(?:vs\.?|versus|compared\s+(?:to|with))\s+(.+?)\s*$", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def load_dotenv():
    """Minimal .env loader (no dependency). Existing env vars win."""
    if not os.path.exists(ENV_PATH):
        return
    try:
        with open(ENV_PATH, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(
                    key.strip(), value.strip().strip('"').strip("'")
                )
    except OSError as exc:
        print("Could not read .env:", exc)


load_dotenv()
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()


def load_corpus():
    try:
        with open(CORPUS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        print("corpus.json could not be loaded:", exc)
        return []


CORPUS = load_corpus()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def tokens(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def sentences(text, limit=3):
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    parts = [p.strip() for p in parts if p.strip()]
    return parts[:limit]


def first_sentences(text, count=2):
    return " ".join(sentences(text, count))


def clean_entity(text):
    return re.sub(r"[\s,;:.!?]+$", "", text.strip()).strip()


def http_json(url, data=None, headers=None, timeout=20):
    req = urllib.request.Request(
        url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Retrieval: curated corpus (local, no network)
# ---------------------------------------------------------------------------
def find_curated(query):
    q = tokens(query)
    best, best_score = None, 0
    for doc in CORPUS:
        match_terms = [m.lower() for m in doc.get("match", [])]
        score = sum(1 for term in match_terms if term in q)
        if score > best_score:
            best, best_score = doc, score
    return best if best_score >= 1 else None


# ---------------------------------------------------------------------------
# Retrieval: Tavily (the only live provider)
# ---------------------------------------------------------------------------
def tavily_search(query, limit=5):
    """Return {'answer': str, 'results': [...]} or None.

    This is the only network retrieval path in the app.
    """
    if not TAVILY_KEY:
        return None
    payload = json.dumps(
        {
            "api_key": TAVILY_KEY,
            "query": query,
            "search_depth": "advanced",
            "max_results": limit,
            "include_answer": True,
            "include_raw_content": True,
        }
    ).encode("utf-8")
    try:
        data = http_json(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
    except Exception as exc:
        print("Tavily request failed:", exc)
        return None

    results = []
    for item in data.get("results", []):
        results.append(
            {
                "title": item.get("title") or "Untitled",
                "url": item.get("url") or "",
                "content": (item.get("content") or "").strip(),
                "raw": (item.get("raw_content") or "").strip(),
                "score": item.get("score"),
            }
        )
    return {"answer": (data.get("answer") or "").strip(), "results": results}


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------
def build_from_curated(query, doc):
    return {
        "query": query,
        "mode": "curated",
        "generated_at": now_iso(),
        "title": doc.get("title", query),
        "subtitle": doc.get("subtitle", ""),
        "summary": doc.get("summary", ""),
        "stats": doc.get("stats", []),
        "cards": doc.get("cards", []),
        "table": doc.get("table"),
        "chart": doc.get("chart"),
        "calculator": doc.get("calculator", {"enabled": False}),
        "picks": doc.get("picks", []),
        "warnings": doc.get("warnings", []),
        "sections": doc.get("sections", []),
        "sources": doc.get("sources", []),
        "disclaimer": doc.get("disclaimer", ""),
    }


def _text_for(result):
    """Prefer page content, fall back to the snippet."""
    return (result.get("raw") or result.get("content") or "").strip()


def build_from_tavily(query, data):
    results = data.get("results") or []
    answer = (data.get("answer") or "").strip()
    if not results and not answer:
        return None

    sources = []
    for result in results:
        sources.append(
            {
                "title": result["title"],
                "url": result["url"],
                "snippet": first_sentences(
                    result.get("content") or _text_for(result), 1
                ),
            }
        )

    sections = []
    for result in results:
        body = _text_for(result)
        sections.append(
            {
                "heading": result["title"],
                "body": (body[:5000] if body else "No page content returned."),
            }
        )

    cards = []
    for idx, result in enumerate(results[:2]):
        text = _text_for(result)
        cards.append(
            {
                "tone": "a" if idx == 0 else "b",
                "tag": "Source",
                "title": result["title"],
                "lede": first_sentences(text, 1) or "No summary available.",
                "points": sentences(text, 3),
            }
        )

    return {
        "query": query,
        "mode": "tavily",
        "generated_at": now_iso(),
        "title": query.strip().title(),
        "subtitle": "Research brief · live Tavily results",
        "summary": answer
        or first_sentences(_text_for(results[0]) if results else "", 2),
        "stats": [
            {"value": str(len(sources)), "label": "Tavily results"},
            {"value": "advanced", "label": "search depth"},
            {"value": "live", "label": "mode"},
        ],
        "cards": cards,
        "table": None,
        "chart": None,
        "calculator": {"enabled": False},
        "picks": [],
        "warnings": [],
        "sections": sections,
        "sources": sources,
        "disclaimer": "Results come from Tavily live search and may be incomplete. Verify before publishing.",
    }


def build_comparison_tavily(query, a, b):
    data_a = tavily_search(a, limit=4)
    data_b = tavily_search(b, limit=4)
    if not data_a and not data_b:
        return None

    cards = []
    for tone, name, data in (("a", a, data_a), ("b", b, data_b)):
        if data and data["results"]:
            top = data["results"][0]
            text = _text_for(top) or (data.get("answer") or "")
            cards.append(
                {
                    "tone": tone,
                    "tag": "Entity A" if tone == "a" else "Entity B",
                    "title": name,
                    "lede": first_sentences(text, 1) or "No summary returned.",
                    "points": sentences(text, 3),
                }
            )
        else:
            cards.append(
                {
                    "tone": tone,
                    "tag": "Entity A" if tone == "a" else "Entity B",
                    "title": name,
                    "lede": "No Tavily results for this side.",
                    "points": [],
                }
            )

    table = None
    if (
        data_a
        and data_b
        and data_a["results"]
        and data_b["results"]
    ):
        top_a, top_b = data_a["results"][0], data_b["results"][0]
        table = {
            "caption": "What the web says",
            "label_header": "Criteria",
            "columns": [a, b],
            "rows": [
                {
                    "label": "Top source",
                    "values": [top_a["title"], top_b["title"]],
                },
                {
                    "label": "Key point",
                    "values": [
                        first_sentences(_text_for(top_a), 1) or "—",
                        first_sentences(_text_for(top_b), 1) or "—",
                    ],
                },
            ],
        }

    sources, seen = [], set()
    for data in (data_a, data_b):
        if not data:
            continue
        for result in data["results"][:3]:
            if result["url"] and result["url"] in seen:
                continue
            seen.add(result["url"])
            sources.append(
                {
                    "title": result["title"],
                    "url": result["url"],
                    "snippet": first_sentences(
                        result.get("content") or _text_for(result), 1
                    ),
                }
            )

    answers = [
        data["answer"]
        for data in (data_a, data_b)
        if data and data.get("answer")
    ]
    summary = " ".join(answers).strip() or f"Live Tavily results for {a} and {b}."

    sections = []
    for name, data in ((a, data_a), (b, data_b)):
        if not data or not data["results"]:
            continue
        body = "\n\n".join(
            f"**{result['title']}** — {first_sentences(_text_for(result), 2)}"
            for result in data["results"][:3]
        )
        sections.append({"heading": f"{name}: what the sources say", "body": body})

    return {
        "query": query,
        "mode": "tavily",
        "generated_at": now_iso(),
        "title": f"{a} vs {b}",
        "subtitle": "Comparison brief · live Tavily results",
        "summary": summary,
        "stats": [
            {"value": str(len(sources)), "label": "Tavily results"},
            {"value": "2", "label": "entities searched"},
            {"value": "live", "label": "mode"},
        ],
        "cards": cards,
        "table": table,
        "chart": None,
        "calculator": {"enabled": False},
        "picks": [],
        "warnings": [],
        "sections": sections,
        "sources": sources,
        "disclaimer": "Results come from Tavily live search and may be incomplete. Verify before publishing.",
    }


def build_no_key(query):
    return {
        "query": query,
        "mode": "offline",
        "generated_at": now_iso(),
        "title": query.strip().title() or "Untitled query",
        "subtitle": "Tavily key required",
        "summary": (
            "This build uses Tavily as its only live search source. "
            "TAVILY_API_KEY is not set, so no web retrieval was performed."
        ),
        "stats": [
            {"value": "0", "label": "sources"},
            {"value": "Tavily", "label": "provider"},
            {"value": "no key", "label": "status"},
        ],
        "cards": [],
        "table": None,
        "chart": None,
        "calculator": {"enabled": False},
        "picks": [],
        "warnings": [],
        "sections": [
            {
                "heading": "Enable live search",
                "body": (
                    "Export TAVILY_API_KEY (or put it in app/.env) and restart app.py. "
                    "Every non-curated query will then call POST https://api.tavily.com/search "
                    "with search_depth=advanced and include_raw_content=true."
                ),
            },
            {
                "heading": "What this query needs",
                "body": (
                    f"Once the key is set, “{query}” will be searched on the live web "
                    "and the top results will be returned as structured cards, sections and citations."
                ),
            },
        ],
        "sources": [],
        "disclaimer": "No live retrieval was performed. Tavily is the only configured search provider.",
    }


def build_no_results(query):
    return {
        "query": query,
        "mode": "offline",
        "generated_at": now_iso(),
        "title": query.strip().title() or "Untitled query",
        "subtitle": "No Tavily results",
        "summary": "Tavily returned no usable results for this query.",
        "stats": [
            {"value": "0", "label": "sources"},
            {"value": "Tavily", "label": "provider"},
            {"value": "no results", "label": "status"},
        ],
        "cards": [],
        "table": None,
        "chart": None,
        "calculator": {"enabled": False},
        "picks": [],
        "warnings": [],
        "sections": [
            {
                "heading": "Try a narrower query",
                "body": (
                    "Tavily works best with specific, well-formed queries. "
                    "Name the product, paper, company or person directly, then search again."
                ),
            }
        ],
        "sources": [],
        "disclaimer": "Tavily is the only configured search provider.",
    }


def build_report(query):
    query = (query or "").strip()
    if len(query) > MAX_QUERY:
        query = query[:MAX_QUERY]

    curated = find_curated(query)
    if curated:
        return build_from_curated(query, curated)

    if not TAVILY_KEY:
        return build_no_key(query)

    match = COMPARE_RE.match(query)
    if match:
        report = build_comparison_tavily(
            query, clean_entity(match.group(1)), clean_entity(match.group(2))
        )
        if report:
            return report

    data = tavily_search(query)
    if data:
        report = build_from_tavily(query, data)
        if report:
            return report

    return build_no_results(query)


# ---------------------------------------------------------------------------
# Cost model for the Serper/Tavily calculator (curated report only)
# ---------------------------------------------------------------------------
def serper_cost(volume):
    if volume <= 100_000:
        return volume / 1_000 * 1.00
    return 100.0 + (volume - 100_000) / 1_000 * (200.0 / 900.0)


def tavily_cost(volume):
    return volume / 1_000 * 8.00


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/health":
            return self._send_json(
                {
                    "status": "ok",
                    "service": "lab10-research-dossier",
                    "provider": "tavily",
                    "tavily": bool(TAVILY_KEY),
                    "corpus": len(CORPUS),
                }
            )

        if path == "/api/research":
            params = urllib.parse.parse_qs(parsed.query)
            query = (params.get("q") or params.get("query") or [""])[0]
            if not query.strip():
                return self._send_json({"error": "missing q parameter"}, 400)
            return self._send_json(build_report(query))

        if path == "/api/comparison":
            return self._send_json(CORPUS[0] if CORPUS else {})

        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/research":
            payload = self._read_json()
            query = str(payload.get("query", "")).strip()
            if not query:
                return self._send_json({"error": "query is required"}, 400)
            if len(query) > MAX_QUERY:
                return self._send_json(
                    {"error": f"query must be at most {MAX_QUERY} characters"}, 400
                )
            try:
                return self._send_json(build_report(query))
            except Exception as exc:
                print("report error:", exc)
                return self._send_json({"error": "report generation failed"}, 500)

        if path == "/api/compare":
            payload = self._read_json()
            try:
                volume = float(payload.get("volume", 0))
            except (TypeError, ValueError):
                return self._send_json({"error": "volume must be a number"}, 400)
            if volume <= 0 or volume > 10_000_000:
                return self._send_json(
                    {"error": "volume must be between 1 and 10,000,000"}, 400
                )
            serper = serper_cost(volume)
            tavily = tavily_cost(volume)
            gap = tavily / serper if serper else 0
            return self._send_json(
                {
                    "volume": volume,
                    "serper": round(serper, 2),
                    "tavily": round(tavily, 2),
                    "gap": round(gap, 1),
                    "note": (
                        "Estimate from the Lab 10 pricing table. Serper uses ~$1.00/1k up to "
                        "100k, tapering to ~$300/1M; Tavily uses $8.00/1k pay-as-you-go."
                    ),
                }
            )

        return self._send_json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))
        sys.stdout.flush()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Lab 10 research dossier at http://localhost:{port}")
    print(f"  provider: Tavily · key: {'set' if TAVILY_KEY else 'MISSING'}")
    print(f"  corpus: {len(CORPUS)} document(s)")
    print("  POST /api/research  {query}")
    print("  GET  /api/research?q=…")
    print("  POST /api/compare   {volume}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
