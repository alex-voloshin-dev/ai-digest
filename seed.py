#!/usr/bin/env python3
"""One-off backfill for the AI Digest archive.

Run this ONCE, before the first daily run, to populate the page with the last
`window_days` of news instead of waiting for it to fill up day by day.

It fetches every source with a wide lookback, stamps each item with its real
publication date (so items group under the day they were actually published),
writes a fresh digest_archive.jsonl, and regenerates docs/index.html.

Re-running rebuilds the archive from whatever the feeds currently expose, so
run it only at the start. Afterwards the daily build_digest.py takes over and
appends new items normally.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta

import build_digest as bd

SCAN_PER_SOURCE = 60  # feed entries to examine per source while seeding


def main() -> None:
    cfg = bd.load_config()
    sources = bd.load_sources()
    if not sources:
        sys.exit("ERROR: no sources configured in sources.yaml")

    window = int(cfg.get("window_days", 14))
    lookback_hours = window * 24
    cutoff = (bd.now_utc() - timedelta(days=window)).date().isoformat()

    print(f"Seeding the last {window} days from {len(sources)} sources...")
    candidates: list[dict] = []
    for source in sources:
        items, status = bd.fetch_feed(source, lookback_hours, SCAN_PER_SOURCE)
        print(f"  {source['name']:<22} {status}")
        candidates.extend(items)

    kept: list[dict] = []
    undated = 0
    for item in bd.select_new(candidates, []):
        day = (item.get("published") or "")[:10]
        if not day:
            undated += 1
            continue
        if day < cutoff:
            continue
        item["date_added"] = day
        kept.append(item)
    kept.sort(key=lambda i: i["date_added"])

    with bd.ARCHIVE_PATH.open("w", encoding="utf-8") as fh:
        for item in kept:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"Wrote {len(kept)} items to {bd.ARCHIVE_PATH.name} "
          f"({undated} undated items skipped).")

    archive = bd.load_archive()
    page = bd.render_html(archive, cfg, bd.now_utc())
    bd.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    bd.OUTPUT_PATH.write_text(page, encoding="utf-8")
    print(f"Wrote {bd.OUTPUT_PATH}")
    print("Done. Review the page, then commit and push.")


if __name__ == "__main__":
    main()
