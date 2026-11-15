# internship-radar

Automated crawler for full-time tech product management roles in the Netherlands
(Product Manager, Technical PM, Associate PM, Product Owner, plus product operations,
product strategy, growth and program management). The repo and some file names date
from its first life as an internship crawler. It scrapes LinkedIn, Indeed, Glassdoor
and company career sites hourly during Amsterdam waking hours, scores each result
with Claude, and delivers an email digest plus a dashboard.

## What it does

- **Scrapers**: `crawl_internships.py` (company career pages) and `crawl_linkedin.py`
  (LinkedIn, Indeed, Glassdoor via JobSpy). Both keep only product-role titles and
  drop internships, working-student and thesis postings.
- **Scoring**: `notify.py` scores each new role with Claude Sonnet against the
  candidate profile, with a rule-based fallback.
- **Email**: `notify.py` sends hourly new-role digests and a daily summary via Gmail SMTP.
- **Dashboard**: `build_dashboard.py` regenerates the static dashboard and apply
  assistant; the workflow pushes them to a separate GitHub Pages repo.
- **PII hygiene**: `strip_pii.py` runs before every commit and removes any
  recruiter contact fields that scrapers may have collected. No third-party
  personal data lands in a public commit.

## Running it

Everything runs on GitHub Actions on a cron schedule. To run it against your
own inbox and profile you need:

1. Set these five secrets at
   `Settings → Secrets and variables → Actions` (or `gh secret set <NAME>`):

   | Secret | Purpose |
   |---|---|
   | `GMAIL_APP_PASSWORD` | 16-char Gmail app password for the sender account |
   | `GH_PAT` | Personal access token with `repo` scope (only for pushing compiled dashboards to a separate Pages repo) |
   | `ANTHROPIC_API_KEY` | Claude scoring calls (Sonnet in `notify.py`) |
   | `SENDER_EMAIL` | Gmail address the crawler sends from |
   | `NOTIFY_EMAIL` | Address that receives the digests |

2. Update the `DASHBOARD_URL` env var in `.github/workflows/crawl.yml` and the
   `Deploy dashboard to public GitHub Pages repo` step's target repo to match
   your own Pages repo.
3. Update the candidate profile embedded in `notify.py`
   and `build_dashboard.py` to your own background — the scoring is
   personalised and will not be meaningful without it.

## Local dev

```bash
pip install -r requirements.txt
python crawl_internships.py --force
python crawl_linkedin.py --force
python build_dashboard.py
open job-search.html
```

## Notes on scraping

Third-party job aggregators change their markup and rate-limit aggressively.
Expect the crawlers to break periodically and need attention. LinkedIn in
particular has terms that restrict automated access; use accordingly.
