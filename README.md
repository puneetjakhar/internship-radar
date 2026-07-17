# internship-radar

Automated internship crawler for the Netherlands. Scrapes LinkedIn, Indeed, and
company career sites twice per hour during Amsterdam waking hours, scores each
result with Claude, and delivers a filtered email digest plus dashboards.

## What it does

- **Scrapers**: `crawl_internships.py` (company career pages), `crawl_linkedin.py`
  (LinkedIn search), `crawl_linkedin_v2.py` (JobSpy + Adzuna + LinkedIn guest API).
- **Scoring**: `notify.py` uses Claude Sonnet for a fast per-run scoring pass.
  `score_linkedin.py` uses Claude Opus for the higher-quality v2 pipeline with
  cached verdicts.
- **Email**: `notify.py` and `notify_v2.py` send hourly and daily digests via
  Gmail SMTP.
- **Dashboards**: `build_dashboard.py` and `build_linkedin_dashboard.py` regenerate
  static HTML dashboards; the workflow pushes them to a separate GitHub Pages
  repo for viewing.
- **PII hygiene**: `strip_pii.py` runs before every commit and removes any
  recruiter contact fields that scrapers may have collected. No third-party
  personal data lands in a public commit.

## Running it

Everything runs on GitHub Actions on a cron schedule. To run it against your
own inbox and profile you need:

1. Set these seven secrets at
   `Settings → Secrets and variables → Actions` (or `gh secret set <NAME>`):

   | Secret | Purpose |
   |---|---|
   | `GMAIL_APP_PASSWORD` | 16-char Gmail app password for the sender account |
   | `GH_PAT` | Personal access token with `repo` scope (only for pushing compiled dashboards to a separate Pages repo) |
   | `ANTHROPIC_API_KEY` | Claude scoring calls (Sonnet in `notify.py`, Opus in `score_linkedin.py`) |
   | `ADZUNA_APP_ID` | Adzuna Phase 3 of the v2 crawler (optional but recommended) |
   | `ADZUNA_APP_KEY` | Adzuna Phase 3 of the v2 crawler (optional but recommended) |
   | `SENDER_EMAIL` | Gmail address the crawler sends from |
   | `NOTIFY_EMAIL` | Address that receives the digests |

2. Update the `DASHBOARD_URL` env var in `.github/workflows/crawl.yml` and the
   `Deploy dashboard to public GitHub Pages repo` step's target repo to match
   your own Pages repo.
3. Update the candidate profile embedded in `score_linkedin.py`, `notify.py`,
   and `build_dashboard.py` to your own background — the scoring is
   personalised and will not be meaningful without it.

## Local dev

```bash
pip install -r requirements.txt
python crawl_linkedin_v2.py --force
python score_linkedin.py
python build_linkedin_dashboard.py
open linkedin.html
```

## Notes on scraping

Third-party job aggregators change their markup and rate-limit aggressively.
Expect the crawlers to break periodically and need attention. LinkedIn in
particular has terms that restrict automated access; use accordingly.
