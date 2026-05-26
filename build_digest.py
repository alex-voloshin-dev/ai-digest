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
:root {
  --bg: #ffffff; --fg: #1a1a1a; --muted: #5f6772; --faint: #99a0aa;
  --accent: #1a5fb4; --line: #e6e6e6; --chip: #f1f3f6;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14161a; --fg: #e7e9ec; --muted: #9aa3ad; --faint: #6b7480;
    --accent: #6ab0ff; --line: #2a2e35; --chip: #21262d;
  }
}
* { box-sizing: border-box; }
html { color-scheme: light dark; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
}
main { max-width: 720px; margin: 0 auto; padding: 3rem 1.25rem 4rem; }

header { margin-bottom: 2rem; }
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  gap: 1.25rem; flex-wrap: wrap; margin: 0 0 1.4rem;
  padding-bottom: 1rem; border-bottom: 1px solid var(--line);
}
.brand {
  display: inline-block; color: var(--fg); line-height: 0;
}
.brand svg { display: block; height: 32px; width: auto; }
.nav {
  display: flex; gap: 1.1rem; flex-wrap: wrap; align-items: center;
  font-size: .92rem;
}
.nav a {
  font-weight: 600; color: var(--fg); text-decoration: none;
  opacity: .8;
}
.nav a:hover { color: var(--accent); opacity: 1; }
h1 { font-size: 2rem; margin: 0 0 .35rem; letter-spacing: -.01em; }
.tagline { margin: 0 0 1rem; color: var(--muted); font-size: 1.02rem; }
.author { display: flex; align-items: center; gap: .85rem; margin: 0 0 1.1rem; }
.avatar {
  width: 48px; height: 48px; border-radius: 50%; flex-shrink: 0;
  object-fit: cover; border: 1px solid var(--line);
}
.byline { margin: 0 0 .35rem; font-size: .92rem; color: var(--muted); }
.byline strong { color: var(--fg); }
.links { margin: 0; display: flex; flex-wrap: wrap; gap: .4rem; }
.chip {
  display: inline-block; padding: .22rem .66rem; font-size: .82rem;
  font-weight: 600; background: var(--chip); color: var(--accent);
  border-radius: 999px; text-decoration: none;
}
.chip:hover { text-decoration: underline; }
.meta {
  margin: 0; padding-top: .9rem; border-top: 1px solid var(--line);
  font-size: .8rem; color: var(--faint);
}

.day { margin-top: 2.6rem; }
.day h2 {
  font-size: 1.3rem; margin: 0; display: flex; align-items: baseline;
  gap: .6rem; flex-wrap: wrap;
}
.day h2 .count {
  font-size: .72rem; font-weight: 700; color: var(--faint);
  text-transform: uppercase; letter-spacing: .05em;
}

.cat { margin-top: 1.5rem; border-left: 2px solid var(--accent);
       padding-left: 1.05rem; }
.cat h3 {
  margin: 0 0 .35rem; font-size: .75rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .08em; color: var(--accent);
}

.item { padding: .8rem 0; border-bottom: 1px solid var(--line); }
.item:last-child { border-bottom: none; padding-bottom: .1rem; }
.title {
  display: block; color: var(--fg); font-weight: 600; font-size: 1.05rem;
  line-height: 1.4; text-decoration: none;
}
.title:hover { color: var(--accent); }
.summary { margin: .3rem 0 .5rem; color: var(--muted); font-size: .92rem; }
.source {
  display: inline-block; font-size: .72rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .05em; color: var(--faint);
}

footer {
  margin-top: 3.5rem; padding-top: 1.25rem; border-top: 1px solid var(--line);
  font-size: .8rem; color: var(--faint);
}
footer p { margin: 0; }
.empty { color: var(--muted); }
"""

# Inline brand SVG -- transparent background, `currentColor` for the AV>
# glyphs so they adapt to the theme; brand blue (#4AA3FF) for `ai_`.
BRAND_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 580.14 226.58" '
    'role="img" aria-label="AV>ai_">'
    '<g transform="translate(50.000,50.000) scale(0.136986) '
    'translate(-35.000,786.000) scale(1,-1)">'
    '<path d="M219 0 35 730H164L275 254Q284 219 291.5 178.5Q299 138 303 114'
    'Q307 138 314.5 178.5Q322 219 331 255L438 730H565L381 0Z" '
    'fill="currentColor" '
    'transform="translate(0,0) matrix(1 0 0 -1 0 730)"/>'
    '<path d="M219 0 35 730H164L275 254Q284 219 291.5 178.5Q299 138 303 114'
    'Q307 138 314.5 178.5Q322 219 331 255L438 730H565L381 0Z" '
    'fill="currentColor" transform="translate(600,0)"/>'
    '<path d="M76 56V171L366 305Q386 314 405.5 320.0Q425 326 436 329'
    'Q425 331 405.0 337.0Q385 343 366 351L76 486V603L524 392V267Z" '
    'fill="currentColor" transform="translate(1200,0)"/>'
    '<path d="M239 -10Q155 -10 106.5 37.5Q58 85 58 162Q58 242 113.0 289.5'
    'Q168 337 264 337H398V376Q398 459 301 459Q257 459 231.0 442.0'
    'Q205 425 202 394H82Q86 468 144.5 514.0Q203 560 302 560'
    'Q407 560 465.0 511.0Q523 462 523 373V0H401V103Q394 50 351.0 20.0'
    'Q308 -10 239 -10ZM279 92Q334 92 366.0 119.0Q398 146 398 191V256'
    'H270Q229 256 205.0 233.0Q181 210 181 174Q181 137 206.5 114.5'
    'Q232 92 279 92Z" fill="#4AA3FF" transform="translate(1800,0)"/>'
    '<path d="M76 0V113H264V437H96V550H389V113H558V0ZM318 642'
    'Q280 642 258.0 661.5Q236 681 236 714Q236 747 258.0 766.5'
    'Q280 786 318 786Q356 786 378.0 766.5Q400 747 400 714'
    'Q400 681 378.0 661.5Q356 642 318 642Z" '
    'fill="#4AA3FF" transform="translate(2400,0)"/>'
    '<path d="M60 -138V-25H540V-138Z" '
    'fill="#4AA3FF" transform="translate(3000,0)"/>'
    '</g></svg>'
)


def _day_heading(day: str) -> str:
    dt = datetime.strptime(day, "%Y-%m-%d")
    return dt.strftime("%A, %B ") + str(dt.day) + dt.strftime(", %Y")


def _render_links(links: list[dict]) -> str:
    chips: list[str] = []
    for link in links or []:
        url = (link.get("url") or "").strip()
        if not url:
            continue
        label = link.get("label") or url
        chips.append(
            f'<a class="chip" href="{html.escape(url, quote=True)}" '
            f'target="_blank" rel="noopener">{html.escape(label)}</a>'
        )
    return "".join(chips)


def _render_author(cfg: dict) -> str:
    name = cfg.get("author_name", "")
    avatar_url = (cfg.get("author_avatar") or "").strip()
    if not name and not avatar_url:
        return ""
    byline = ""
    if name:
        byline = (f'<p class="byline">Curated by '
                  f'<strong>{html.escape(name)}</strong></p>')
    avatar = ""
    if avatar_url:
        avatar = (f'<img class="avatar" '
                  f'src="{html.escape(avatar_url, quote=True)}" '
                  f'alt="{html.escape(name or "author")}" '
                  f'width="48" height="48">')
    return (f'<div class="author">{avatar}'
            f'<div class="author-text">{byline}</div></div>')


def _render_nav(cfg: dict) -> str:
    """Top-of-page navigation links, drawn from cfg['author_links'].

    Empty-URL entries (placeholder rows in config) are skipped silently."""
    items: list[str] = []
    for link in cfg.get("author_links", []) or []:
        url = (link.get("url") or "").strip()
        if not url:
            continue
        label = link.get("label") or url
        items.append(
            f'<a href="{html.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener">{html.escape(label)}</a>'
        )
    if not items:
        return ""
    return f'<nav class="nav">{"".join(items)}</nav>'


def render_html(archive: list[dict], cfg: dict, generated_at: datetime) -> str:
    window_days = int(cfg.get("window_days", 14))
    site_title = cfg.get("site_title", "AI Digest")
    site_tagline = cfg.get("site_tagline", "")

    cutoff = (generated_at - timedelta(days=window_days)).date().isoformat()
    by_day: dict[str, list[dict]] = {}
    for record in archive:
        day = record.get("date_added", "")
        if day and day >= cutoff:
            by_day.setdefault(day, []).append(record)

    sections: list[str] = []
    if not by_day:
        sections.append('<p class="empty">No items yet — the next run '
                        'will populate this page.</p>')
    for day in sorted(by_day, reverse=True):
        day_items = by_day[day]
        count = len(day_items)
        unit = "item" if count == 1 else "items"
        blocks = ['<div class="day">',
                  f'<h2>{html.escape(_day_heading(day))}'
                  f'<span class="count">{count} {unit}</span></h2>']
        categories = sorted(
            {item.get("category", "Other") for item in day_items},
            key=lambda c: CATEGORY_ORDER.index(c)
            if c in CATEGORY_ORDER else len(CATEGORY_ORDER),
        )
        for category in categories:
            blocks.append('<section class="cat">')
            blocks.append(f'<h3>{html.escape(category)}</h3>')
            for item in day_items:
                if item.get("category", "Other") != category:
                    continue
                title = html.escape(item["title"])
                url = html.escape(item["url"], quote=True)
                source = html.escape(item["source"])
                summary = html.escape(item.get("summary", ""))
                summary_html = (f'<p class="summary">{summary}</p>'
                                if summary else "")
                blocks.append(
                    f'<article class="item">'
                    f'<a class="title" href="{url}" target="_blank" '
                    f'rel="noopener">{title}</a>'
                    f'{summary_html}'
                    f'<span class="source">{source}</span>'
                    f'</article>'
                )
            blocks.append('</section>')
        blocks.append('</div>')
        sections.append("\n".join(blocks))

    body = "\n".join(sections)
    updated = generated_at.strftime("%Y-%m-%d %H:%M")
    author_block = _render_author(cfg)
    nav_block = _render_nav(cfg)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(site_title)}</title>
<meta name="description" content="{html.escape(site_tagline)}">
<link rel="icon" type="image/svg+xml" href="assets/favicon.svg">
<link rel="alternate icon" type="image/x-icon" href="assets/favicon.ico">
<link rel="apple-touch-icon" sizes="180x180" href="assets/apple-touch-icon.png">
<style>{PAGE_CSS}</style>
</head>
<body>
<main>
<header>
<div class="topbar">
<a class="brand" href="https://voloshin.net" aria-label="Alex Voloshin">\
{BRAND_SVG}</a>
{nav_block}
</div>
<h1>{html.escape(site_title)}</h1>
<p class="tagline">{html.escape(site_tagline)}</p>
{author_block}
<p class="meta">Rolling {window_days}-day window &middot; updated \
{updated} UTC</p>
</header>
{body}
<footer>
<p>Automated, unattended news page. Built deterministically from a fixed list
of trusted source feeds; every item links to its origin.</p>
</footer>
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

    page = render_html(archive, cfg, now_utc())
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(page, encoding="utf-8")

    window_cutoff = (now_utc() - timedelta(days=window)).date().isoformat()
    visible = sum(1 for r in archive
                  if r.get("date_added", "") >= window_cutoff)
    print(f"Wrote {OUTPUT_PATH} ({visible} items in the {window}-day window).")
    print("Done.")


if __name__ == "__main__":
    main()
