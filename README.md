# Lead Radar

Finds people looking to hire WordPress / Elementor / SEO / AEO / GEO help and shows them on a dashboard.

- `scanner/` Python (standard library only). Fetches sources, filters, scores, de-duplicates.
- `docs/` Static dashboard (served by GitHub Pages). Reads `leads.json` and `status.json`.
- `config.json` Subreddits, feeds, keywords, excluded countries, minimum score.
- `.github/workflows/scan.yml` Runs the scanner every 15 minutes and commits the results.

## Run locally

    python scanner/run.py
    python -m http.server 8000 --directory docs

Open http://localhost:8000.

## Optional secrets (GitHub repo: Settings > Secrets and variables > Actions)

| Secret | Purpose |
|---|---|
| `GEMINI_API_KEY` | Free Gemini key from aistudio.google.com. Turns on AI relevance scoring. |
| `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | Reddit's official API. Without them the scanner uses Reddit's public RSS, which Reddit often rate-limits. |

## Google Alerts

Create an alert at google.com/alerts, set "Deliver to" to **RSS feed**, copy the feed URL and add it to `google_alert_feeds` in `config.json`.

Statuses (Saved / Contacted / Ignored) are stored in each browser's localStorage.
