# AI Digest

A deterministic, unattended news digest. A daily GitHub Actions job fetches a
fixed list of trusted AI news feeds, deduplicates new items against an
append-only archive, and renders a single static HTML page holding a rolling
14-day window. Older entries roll off automatically.

GitHub Pages serves the page at https://ai.voloshin.net — a personal news
page kept separate from the main blog.

No language model, no API keys, no paid services. The only network calls are
the source feeds. Output is reproducible, and every item links to its origin.

## How it works

1. A daily GitHub Actions job runs `build_digest.py`.
2. The script reads `sources.yaml`, fetches each feed, and keeps items from
   roughly the last 30 hours.
3. New items (not already in `digest_archive.jsonl`) are appended to the
   archive.
4. The script renders `docs/index.html` from the last 14 days of the archive.
5. The updated page and archive are committed; GitHub Pages serves `docs/`.

## Repository layout

| Path | Purpose |
|------|---------|
| `build_digest.py` | The daily pipeline: fetch, dedup, render. |
| `seed.py` | One-off backfill — populates the archive with the last 14 days. |
| `sources.yaml` | The trusted-source list. Edit freely. |
| `config.json` | Site title, author info, window and fetch settings. |
| `digest_archive.jsonl` | Append-only history of every item ever included. |
| `docs/index.html` | The generated page, served by GitHub Pages. |
| `docs/CNAME` | Custom domain for GitHub Pages. |
| `.github/workflows/digest.yml` | The daily cron job. |

## Setup

1. Create a new **public** GitHub repository and push these files to it.
2. Repo **Settings → Pages → Source: Deploy from a branch**, branch `main`,
   folder `/docs`. Save.
3. Point the `ai.voloshin.net` DNS record (a CNAME) at
   `<your-github-username>.github.io`.
4. Repo **Settings → Pages → Custom domain**: enter `ai.voloshin.net`, save.
   GitHub provisions HTTPS automatically once DNS resolves.
5. The workflow runs daily on the schedule in `digest.yml`; trigger it
   manually any time via **Actions → AI Digest → Run workflow**.

## First run — seed the archive

To launch with a full page instead of waiting for it to fill day by day,
backfill the last 14 days once:

```
pip install feedparser pyyaml
python seed.py
```

`seed.py` fetches every source with a wide lookback, dates each item by its
real publication day, and writes a fresh `digest_archive.jsonl`. Run it once,
review `docs/index.html`, then commit. After that the daily job takes over.

## Local run

```
pip install feedparser pyyaml
python build_digest.py
```

This regenerates `docs/index.html` — open it in a browser to preview.

## Configuration

- **Sources** — add or remove feeds in `sources.yaml`. Each run prints
  per-feed health, so broken feeds are easy to spot and prune.
- **Author** — name, photo, and links live in `config.json`
  (`author_name`, `author_avatar`, `author_links`).
- **Window** — `window_days` in `config.json` controls how long entries stay
  on the page.
- **Schedule** — edit the `cron` line in `.github/workflows/digest.yml`.
