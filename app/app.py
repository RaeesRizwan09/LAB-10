#!/usr/bin/env python3
"""Lab 10 — query-driven research dossier.

A dependency-free backend (Python standard library only) that turns a free-form
query into a structured report the frontend can render.

Pipeline
--------
1. Look for a matching curated document in corpus.json (e.g. the Lab 10
   Serper vs Tavily comparison).
2. Otherwise retrieve live sources:
     * Tavily Search if TAVILY_API_KEY is set
     * Wikipedia (search + page summaries) as the keyless default
3. Return one JSON schema: title, summary, stats, cards, table, chart,
   calculator, picks, warnings, sections, sources, disclaimer.

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
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CORPUS_PATH = os.path.join(BASE_DIR, "corpus.json")
USER_AGENT = "Lab10ResearchDossier/1.0 (educational; local)"
TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "").strip()

MAX_QUERY = 300
COMPARE_RE = re.compile(
    r"^\s*(.+?)\s+(?:vs\.?|versus|compared\s+(?:to|with))\s+(.+?)\s*$", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def load_corpus():
    try:
        with open(CORPUS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        print("corpus.json could not be loaded:", exc)
        return []


CORPUS = load_corpus()


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def tokens(text):
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def strip_html(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def sentences(text, limit=3):
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    parts = [p.strip() for p in parts if p.strip()]
    return parts[:limit]


def first_sentences(text, count=2):
    return " ".join(sentences(text, count))


def clean_entity(text):
    return re.sub(r"[\s,;:.!?]+$", "", text.strip()).strip()


def http_json(url, data=None, headers=None, timeout=12):
    req = urllib.request.Request(
        url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Retrieval: curated corpus
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
# Retrieval: Wikipedia (keyless default)
# ---------------------------------------------------------------------------
def wiki_search(query, limit=5):
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": limit,
            "srprop": "snippet",
        }
    )
    data = http_json("https://en.wikipedia.org/w/api.php?" + params)
    results = []
    for item in data.get("query", {}).get("search", []):
        results.append(
            {"title": item.get("title", ""), "snippet": strip_html(item.get("snippet", ""))}
        )
    return results


def wiki_summary(title):
    url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(
        title.replace(" ", "_"), safe=""
    )
    try:
        data = http_json(url)
    except Exception:
        return None
    extract = (data.get("extract") or "").strip()
    if not extract:
        return None
    page = (
        (data.get("content_urls") or {}).get("desktop") or {}
    ).get("page", "")
    return {
        "title": data.get("title", title),
        "extract": extract,
        "description": (data.get("description") or "").strip(),
        "url": page,
    }


def wiki_lookup(name):
    results = wiki_search(name, 1)
    if results:
        page = wiki_summary(results[0]["title"])
        if page:
            return page
    return wiki_summary(name)


# ---------------------------------------------------------------------------
# Retrieval: Tavily (optional, needs TAVILY_API_KEY)
# ---------------------------------------------------------------------------
def tavily_search(query, limit=5):
    if not TAVILY_KEY:
        return None
    payload = json.dumps(
        {
            "api_key": TAVILY_KEY,
            "query": query,
            "search_depth": "advanced",
            "max_results": limit,
            "include_answer": True,
        }
    ).encode("utf-8")
    try:
        data = http_json(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            timeout=25,
        )
    except Exception as exc:
        print("Tavily request failed:", exc)
        return None
    return {
        "answer": (data.get("answer") or "").strip(),
        "results": [
            {
                "title": r.get("title") or "Untitled",
                "url": r.get("url") or "",
                "content": (r.get("content") or "").strip(),
            }
            for r in data.get("results", [])
        ],
    }


# ---------------------------------------------------------------------------
# Report builders
# ---------------------------------------------------------------------------
def build_from_curated(query, doc):
    report = {
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
    return report


def build_from_tavily(query, data):
    results = data.get("results", [])
    if not results and not data.get("answer"):
        return None
    sources = [
        {
            "title": r["title"],
            "url": r["url"],
            "snippet": first_sentences(r["content"], 1),
        }
        for r in results
    ]
    sections = [
        {"heading": r["title"], "body": r["content"] or "No snippet returned."}
        for r in results
    ]
    cards = []
    if len(results) >= 2:
        for idx, r in enumerate(results[:2]):
            cards.append(
                {
                    "tone": "a" if idx == 0 else "b",
                    "tag": "Source",
                    "title": r["title"],
                    "lede": first_sentences(r["content"], 1) or "No summary available.",
                    "points": sentences(r["content"], 3),
                }
            )
    return {
        "query": query,
        "mode": "tavily",
        "generated_at": now_iso(),
        "title": query.strip().title(),
        "subtitle": "Research brief · live web sources",
        "summary": data.get("answer") or (sections[0]["body"] if sections else ""),
        "stats": [
            {"value": str(len(sources)), "label": "sources"},
            {"value": "Tavily", "label": "retrieval"},
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
        "disclaimer": "Summaries come from live web search and may be incomplete. Verify before publishing.",
    }


def build_comparison(query, a, b):
    page_a = wiki_lookup(a)
    page_b = wiki_lookup(b)
    if not page_a and not page_b:
        return None

    cards = []
    if page_a:
        cards.append(
            {
                "tone": "a",
                "tag": "Entity A",
                "title": page_a["title"],
                "lede": page_a.get("description") or first_sentences(page_a["extract"], 1),
                "points": sentences(page_a["extract"], 3),
            }
        )
    if page_b:
        cards.append(
            {
                "tone": "b",
                "tag": "Entity B",
                "title": page_b["title"],
                "lede": page_b.get("description") or first_sentences(page_b["extract"], 1),
                "points": sentences(page_b["extract"], 3),
            }
        )

    table = None
    if page_a and page_b:
        table = {
            "caption": "Side-by-side summary",
            "label_header": "Criteria",
            "columns": [page_a["title"], page_b["title"]],
            "rows": [
                {
                    "label": "Overview",
                    "values": [
                        first_sentences(page_a["extract"], 1),
                        first_sentences(page_b["extract"], 1),
                    ],
                },
                {
                    "label": "Focus",
                    "values": [
                        page_a.get("description") or "—",
                        page_b.get("description") or "—",
                    ],
                },
            ],
        }

    sources = []
    for page in (page_a, page_b):
        if page:
            sources.append(
                {
                    "title": page["title"],
                    "url": page["url"],
                    "snippet": page.get("description") or first_sentences(page["extract"], 1),
                }
            )

    body = ""
    if page_a:
        body += f"**{page_a['title']}** — {first_sentences(page_a['extract'], 3)}\n\n"
    if page_b:
        body += f"**{page_b['title']}** — {first_sentences(page_b['extract'], 3)}"

    return {
        "query": query,
        "mode": "wikipedia",
        "generated_at": now_iso(),
        "title": f"{a} vs {b}",
        "subtitle": "Comparison brief · live sources",
        "summary": (
            f"A comparison brief for {a} and {b}, grounded in Wikipedia summaries. "
            "Read the cards for the essentials, then follow the sources for detail."
        ),
        "stats": [
            {"value": str(len(sources)), "label": "sources"},
            {"value": "Wikipedia", "label": "retrieval"},
            {"value": "live", "label": "mode"},
        ],
        "cards": cards,
        "table": table,
        "chart": None,
        "calculator": {"enabled": False},
        "picks": [],
        "warnings": [],
        "sections": [
            {"heading": "What the sources say", "body": body.strip()},
            {
                "heading": "Open questions",
                "body": (
                    f"How do {a} and {b} differ on cost, quality and speed? "
                    "Which one fits the specific workflow and team skill set? "
                    "What evidence would change the decision?"
                ),
            },
        ],
        "sources": sources,
        "disclaimer": "Summaries come from Wikipedia and may be incomplete or dated. Verify before publishing.",
    }


def build_topic(query):
    results = wiki_search(query, 4)
    pages = []
    for result in results[:3]:
        page = wiki_summary(result["title"])
        if page:
            pages.append(page)
    if not pages:
        return None

    first = pages[0]
    sources = [
        {
            "title": page["title"],
            "url": page["url"],
            "snippet": page.get("description") or first_sentences(page["extract"], 1),
        }
        for page in pages
    ]
    sections = [{"heading": page["title"], "body": page["extract"]} for page in pages]

    return {
        "query": query,
        "mode": "wikipedia",
        "generated_at": now_iso(),
        "title": query.strip().title(),
        "subtitle": "Research brief · live sources",
        "summary": first_sentences(first["extract"], 2),
        "stats": [
            {"value": str(len(pages)), "label": "sources"},
            {"value": "Wikipedia", "label": "retrieval"},
            {"value": "live", "label": "mode"},
        ],
        "cards": [],
        "table": None,
        "chart": None,
        "calculator": {"enabled": False},
        "picks": [],
        "warnings": [],
        "sections": sections,
        "sources": sources,
        "disclaimer": "Summaries come from Wikipedia and may be incomplete or dated. Verify before publishing.",
    }


def build_offline(query):
    return {
        "query": query,
        "mode": "offline",
        "generated_at": now_iso(),
        "title": query.strip().title(),
        "subtitle": "Research outline · no live sources",
        "summary": "No live sources were reachable for this query, so here is a structured research outline you can fill in.",
        "stats": [
            {"value": "0", "label": "sources"},
            {"value": "outline", "label": "mode"},
            {"value": "—", "label": "retrieval"},
        ],
        "cards": [],
        "table": None,
        "chart": None,
        "calculator": {"enabled": False},
        "picks": [],
        "warnings": [],
        "sections": [
            {
                "heading": "Scope",
                "body": f"Define what “{query}” should cover: the entities involved, the time window, and the decision this research supports.",
            },
            {
                "heading": "Key questions",
                "body": "What are the main options? What criteria matter (cost, quality, speed, integration)? What evidence would change the decision?",
            },
            {
                "heading": "Suggested next steps",
                "body": "Add a TAVILY_API_KEY for live web search, or run the CrewAI notebook to generate a sourced report.",
            },
        ],
        "sources": [],
        "disclaimer": "Offline outline only — no external facts were retrieved.",
    }


def build_report(query):
    query = (query or "").strip()
    if not query:
        return build_offline("Untitled query")
    if len(query) > MAX_QUERY:
        query = query[:MAX_QUERY]

    curated = find_curated(query)
    if curated:
        return build_from_curated(query, curated)

    if TAVILY_KEY:
        tavily = tavily_search(query)
        if tavily:
            report = build_from_tavily(query, tavily)
            if report:
                return report

    match = COMPARE_RE.match(query)
    if match:
        report = build_comparison(
            query, clean_entity(match.group(1)), clean_entity(match.group(2))
        )
        if report:
            return report

    try:
        report = build_topic(query)
    except Exception as exc:
        print("Live retrieval failed:", exc)
        report = None
    return report or build_offline(query)


# ---------------------------------------------------------------------------
# Cost model for the Serper/Tavily calculator
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
            # Backwards-compatible: return the first curated document.
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
    print(f"  corpus: {len(CORPUS)} document(s) · tavily: {'on' if TAVILY_KEY else 'off (Wikipedia fallback)'}")
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
