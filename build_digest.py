#!/usr/bin/env python3
"""AI Digest builder.

Fetches a predefined set of trusted RSS/Atom feeds, deduplicates new items
against an append-only archive, and renders a single static HTML page holding
a rolling N-day window. The page is published by GitHub Pages.

Deterministic by design: no language model, no API keys, no paid services.
The only network calls are the source feeds. Built to run unattended as a
daily GitHub Actions job.
"""

from __future__ import annotations

import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
SOURCES_PATH = ROOT / "sources.yaml"
ARCHIVE_PATH = ROOT / "digest_archive.jsonl"
OUTPUT_PATH = ROOT / "docs" / "index.html"

USER_AGENT = "ai-digest/1.0 (+https://ai.voloshin.net)"

CATEGORY_ORDER = [
    "Labs & vendors",
    "Agentic-dev tooling",
    "News & analysis",
    "Research & curators",
    "Other",
]


# ---------------------------------------------------------------------------
# Config and sources
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def load_sources() -> list[dict]:
    with SOURCES_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("sources", [])


# ---------------------------------------------------------------------------
# Feed ingestion
# ---------------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def entry_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    return None


def clean_text(raw: str, limit: int, strip_tags: bool = True) -> str:
    text = raw or ""
    if strip_tags:
        text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    return text


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    url = re.sub(r"#.*$", "", url)
    url = re.sub(r"[?&](utm_[^=]+|ref|source)=[^&]*", "", url)
    url = url.rstrip("?&")
    return url.rstrip("/").lower()


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def extract_items(parsed, source: dict, lookback_hours: int,
                  max_items: int) -> list[dict]:
    """Turn a parsed feed into item dicts. Pure -- testable without network."""
    cutoff = now_utc() - timedelta(hours=lookback_hours)
    items: list[dict] = []
    for entry in parsed.entries[:max_items]:
        when = entry_datetime(entry)
        if when and when < cutoff:
            continue
        link = (entry.get("link") or "").strip()
        title = clean_text(entry.get("title", ""), limit=200,
                           strip_tags=False)
        if not link or not title:
            continue
        items.append({
            "title": title,
            "url": link,
            "source": source["name"],
            "category": source.get("category", "Other"),
            "summary": clean_text(entry.get("summary", ""), limit=280),
            "published": when.isoformat() if when else "",
        })
    return items


def fetch_feed(source: dict, lookback_hours: int,
               max_items: int) -> tuple[list[dict], str]:
    try:
        parsed = feedparser.parse(source["url"], agent=USER_AGENT)
    except Exception as exc:  # noqa: BLE001 - feedparser surfaces varied errors
        return [], f"ERROR ({exc})"
    if not parsed.entries:
        reason = getattr(parsed, "bozo_exception", None)
        return [], f"FAILED (no entries{f'; {reason}' if reason else ''})"
    items = extract_items(parsed, source, lookback_hours, max_items)
    return items, f"ok ({len(items)} new of {len(parsed.entries)})"


# ---------------------------------------------------------------------------
# Archive and deduplication
# ---------------------------------------------------------------------------

def load_archive() -> list[dict]:
    if not ARCHIVE_PATH.exists():
        return []
    records: list[dict] = []
    with ARCHIVE_PATH.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def append_archive(items: list[dict]) -> None:
    with ARCHIVE_PATH.open("a", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def select_new(candidates: list[dict], archive: list[dict]) -> list[dict]:
    """Drop items already in the archive, and collapse duplicates within the
    batch by normalized URL and normalized title (same story, many feeds)."""
    seen_urls = {normalize_url(r["url"]) for r in archive}
    seen_titles = {normalize_title(r["title"]) for r in archive}
    fresh: list[dict] = []
    for item in candidates:
        nurl = normalize_url(item["url"])
        ntitle = normalize_title(item["title"])
        if not nurl or nurl in seen_urls or (ntitle and ntitle in seen_titles):
            continue
        seen_urls.add(nurl)
        seen_titles.add(ntitle)
        fresh.append(item)
    return fresh


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

PAGE_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0;
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
  background: #ffffff;
  color: #1a1a1a;
}
main { max-width: 720px; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
header { border-bottom: 1px solid #e3e3e3; padding-bottom: 1.25rem;
         margin-bottom: 1rem; }
h1 { font-size: 1.7rem; margin: 0 0 .3rem; }
.tagline { margin: .2rem 0; color: #555555; }
.meta { margin: .4rem 0 0; font-size: .85rem; color: #888888; }
h2 { font-size: 1.15rem; margin: 2rem 0 .25rem; }
h3 { font-size: .78rem; text-transform: uppercase; letter-spacing: .05em;
     color: #888888; margin: 1.25rem 0 .4rem; }
ul { list-style: none; margin: 0; padding: 0; }
li { margin: 0 0 .7rem; }
a { color: #1a5fb4; text-decoration: none; font-weight: 600; }
a:hover { text-decoration: underline; }
.summary { color: #444444; font-weight: 400; }
.source { color: #999999; font-size: .82rem; }
footer { margin-top: 3rem; padding-top: 1.25rem;
         border-top: 1px solid #e3e3e3; font-size: .82rem; color: #999999; }
.empty { color: #888888; }
@media (prefers-color-scheme: dark) {
  body { background: #15171a; color: #e6e6e6; }
  header, footer { border-color: #2c2f33; }
  .tagline { color: #aaaaaa; }
  .meta, h3, .source, footer, .empty { color: #7d8590; }
  a { color: #6ab0ff; }
  .summary { color: #c0c4c9; }
}
"""


def _day_heading(day: str) -> str:
    dt = datetime.strptime(day, "%Y-%m-%d")
    return dt.strftime("%A, %B ") + str(dt.day) + dt.strftime(", %Y")


def render_html(archive: list[dict], window_days: int, generated_at: datetime,
                site_title: str, site_tagline: str) -> str:
    cutoff = (generated_at - timedelta(days=window_days)).date().isoformat()
    by_day: dict[str, list[dict]] = {}
    for record in archive:
        day = record.get("date_added", "")
        if day and day >= cutoff:
            by_day.setdefault(day, []).append(record)

    parts: list[str] = []
    if not by_day:
        parts.append('<p class="empty">No items yet. The first run will '
                     'populate this page.</p>')
    else:
        for day in sorted(by_day, reverse=True):
            parts.append('<section class="day">')
            parts.append(f"<h2>{html.escape(_day_heading(day))}</h2>")
            day_items = by_day[day]
            categories = sorted(
                {item.get("category", "Other") for item in day_items},
                key=lambda c: CATEGORY_ORDER.index(c)
                if c in CATEGORY_ORDER else len(CATEGORY_ORDER),
            )
            for category in categories:
                parts.append(f"<h3>{html.escape(category)}</h3>")
                parts.append("<ul>")
                for item in day_items:
                    if item.get("category", "Other") != category:
                        continue
                    title = html.escape(item["title"])
                    url = html.escape(item["url"], quote=True)
                    source = html.escape(item["source"])
                    summary = html.escape(item.get("summary", ""))
                    summary_html = (f' <span class="summary">{summary}</span>'
                                    if summary else "")
                    parts.append(
                        f'<li><a href="{url}" target="_blank" '
                        f'rel="noopener">{title}</a>{summary_html} '
                        f'<span class="source">{source}</span></li>'
                    )
                parts.append("</ul>")
            parts.append("</section>")

    body = "\n".join(parts)
    updated = generated_at.strftime("%Y-%m-%d %H:%M")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(site_title)}</title>
<meta name="description" content="{html.escape(site_tagline)}">
<style>{PAGE_CSS}</style>
</head>
<body>
<main>
<header>
<h1>{html.escape(site_title)}</h1>
<p class="tagline">{html.escape(site_tagline)}</p>
<p class="meta">Rolling {window_days}-day window &middot; last updated \
{updated} UTC</p>
</header>
{body}
<footer>Automated, unattended news page. Built deterministically from a fixed
list of trusted source feeds; every item links to its origin.</footer>
</main>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cfg = load_config()
    sources = load_sources()
    if not sources:
        sys.exit("ERROR: no sources configured in sources.yaml")

    lookback = int(cfg.get("lookback_hours", 30))
    max_per_source = int(cfg.get("max_items_per_source", 6))
    window = int(cfg.get("window_days", 14))
    site_title = cfg.get("site_title", "AI Digest")
    site_tagline = cfg.get("site_tagline", "A daily roundup of AI news.")

    print(f"AI Digest run -- {now_utc().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Fetching {len(sources)} sources:")
    candidates: list[dict] = []
    for source in sources:
        items, status = fetch_feed(source, lookback, max_per_source)
        print(f"  {source['name']:<22} {status}")
        candidates.extend(items)

    archive = load_archive()
    fresh = select_new(candidates, archive)
    today = now_utc().date().isoformat()
    for item in fresh:
        item["date_added"] = today
    print(f"New items this run: {len(fresh)} (archive held {len(archive)})")
    if fresh:
        append_archive(fresh)
        archive.extend(fresh)

    page = render_html(archive, window, now_utc(), site_title, site_tagline)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(page, encoding="utf-8")

    window_cutoff = (now_utc() - timedelta(days=window)).date().isoformat()
    visible = sum(1 for r in archive
                  if r.get("date_added", "") >= window_cutoff)
    print(f"Wrote {OUTPUT_PATH} ({visible} items in the {window}-day window).")
    print("Done.")


if __name__ == "__main__":
    main()
