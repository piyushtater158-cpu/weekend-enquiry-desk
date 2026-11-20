"""Scrape sunkssd.io public copy into data/corpus/chunks.json.

Only sunkssd.io itself - never Instagram, never a booking widget, per the hard rule
in the design doc. Re-run this periodically to refresh accessed_at (PR-09 keys off it).

Usage: .venv/Scripts/python.exe scripts/scrape_corpus.py
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

URL = "https://www.sunkssd.io/"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "corpus"
OUT_JSON = OUT_DIR / "chunks.json"
OUT_HTML = OUT_DIR / "snapshot.html"
OUT_EVENTS = OUT_DIR / "events.json"

HEADING_TAGS = {"h1", "h2", "h3", "h4"}
SKIP_TAGS = {"script", "style", "nav", "noscript", "svg", "footer"}

# These headings are real public copy but not guest-FAQ content (team bios,
# brand history/travel marketing). Live-tested: including them roughly
# doubles the content-node prompt size and, against the free model this
# project uses, pushed real generation time well past what a demo can wait
# on. The "full corpus, no vector search" decision (P2') was never "every
# scraped sentence regardless of relevance" - it was "don't build a retrieval
# layer for 24 chunks." Curating which chunks constitute the guest-facing
# knowledge base is a normal editorial step, not a reversal of that decision.
EXCLUDED_HEADINGS = {
    "meet the team.", "we travel.", "rewind.", "in good company.",
    "julian trad", "jad aramouni", "lara kays", "rooney nasr",
    "ramy naouss", "christelle helou", "jad mouawad", "alex limneos",
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def extract_sections(soup: BeautifulSoup) -> list[dict]:
    """Walk the body, grouping text under the nearest preceding heading.
    Falls back to a single 'page' section if no headings are found.
    """
    body = soup.body or soup
    for tag in body.find_all(SKIP_TAGS):
        tag.decompose()

    sections: list[dict] = []
    current_heading = "intro"
    current_texts: list[str] = []
    seen_slugs: dict[str, int] = {}

    def flush():
        text = " ".join(t for t in current_texts if t).strip()
        text = re.sub(r"\s+", " ", text)
        if text:
            slug = slugify(current_heading)
            seen_slugs[slug] = seen_slugs.get(slug, 0) + 1
            if seen_slugs[slug] > 1:
                slug = f"{slug}-{seen_slugs[slug]}"
            sections.append({"section_id": slug, "heading": current_heading, "text": text})

    def walk(node):
        nonlocal current_heading, current_texts
        if isinstance(node, NavigableString):
            return
        if not isinstance(node, Tag):
            return
        if node.name in HEADING_TAGS:
            flush()
            current_heading = node.get_text(" ", strip=True) or current_heading
            current_texts = []
            return
        if node.name in ("p", "li", "span", "div", "a", "button"):
            own_text = "".join(
                c for c in node.contents if isinstance(c, NavigableString)
            )
            own_text = own_text.strip()
            if own_text and len(own_text) > 1:
                current_texts.append(own_text)
        for child in node.children:
            walk(child)

    walk(body)
    flush()

    # Collapse near-duplicate/empty sections, keep ones with real content,
    # and drop non-guest-facing sections (see EXCLUDED_HEADINGS above).
    return [
        s for s in sections
        if len(s["text"]) >= 3 and s["heading"].strip().lower() not in EXCLUDED_HEADINGS
    ]


def extract_events(soup: BeautifulSoup, accessed_at: str) -> list[dict]:
    """The site publishes a schema.org EventSeries + per-weekend Event blocks as
    JSON-LD - the authoritative source for published weekends, more precise than
    scraping prose dates. Each Event becomes one published-weekend record.
    """
    events: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        nodes = payload.get("@graph", [payload]) if isinstance(payload, dict) else payload
        for node in nodes:
            if not isinstance(node, dict) or node.get("@type") != "Event":
                continue
            events.append({
                "name": node.get("name"),
                "start_date": node.get("startDate"),
                "end_date": node.get("endDate"),
                "location": (node.get("location") or {}).get("name"),
                "accessed_at": accessed_at,
                "source": "sunkssd.io (schema.org/Event JSON-LD)",
            })
    return events


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        resp = httpx.get(URL, timeout=20, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; WeekendEnquiryDeskBot/1.0; portfolio demo)",
        })
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"ERROR: failed to fetch {URL}: {exc}", file=sys.stderr)
        return 1

    OUT_HTML.write_text(resp.text, encoding="utf-8")
    accessed_at = datetime.now(timezone.utc).isoformat()

    # extract_events must run before extract_sections, which decomposes <script> tags.
    soup = BeautifulSoup(resp.text, "html.parser")
    events = extract_events(soup, accessed_at)
    OUT_EVENTS.write_text(json.dumps(events, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(events)} published-weekend events to {OUT_EVENTS}")

    soup = BeautifulSoup(resp.text, "html.parser")
    sections = extract_sections(soup)

    chunks = [
        {
            "id": f"chunk-{i:03d}",
            "section_id": s["section_id"],
            "heading": s["heading"],
            "text": s["text"],
            "url": URL,
            "accessed_at": accessed_at,
            "source": "sunkssd.io",
        }
        for i, s in enumerate(sections)
    ]

    OUT_JSON.write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(chunks)} chunks to {OUT_JSON}")
    print(f"Snapshot HTML saved to {OUT_HTML} ({len(resp.text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
