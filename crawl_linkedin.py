#!/usr/bin/env python3
"""
crawl_linkedin.py — LinkedIn / Indeed / Glassdoor job scraper (no login required)
==================================================================================
Uses JobSpy (python-jobspy) to scrape NL internship/strategy/product/ops roles
from LinkedIn, Indeed, and Glassdoor simultaneously.

Output: linkedin_jobs.json (same schema as crawled_internships.json + extra fields)

Usage:
  python3 crawl_linkedin.py              # incremental (skips already-seen jobs)
  python3 crawl_linkedin.py --force      # fresh scrape, overwrites existing
  python3 crawl_linkedin.py --full       # fetch full descriptions (slower, enables
                                         # applicant count + recruiter email parsing)
  python3 crawl_linkedin.py --linkedin   # LinkedIn only
  python3 crawl_linkedin.py --indeed     # Indeed only
"""

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

try:
    from jobspy import scrape_jobs
    import pandas as pd
except ImportError:
    print("Installing python-jobspy and pandas...")
    os.system("pip3 install python-jobspy pandas")
    from jobspy import scrape_jobs
    import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(BASE, 'linkedin_jobs.json')

# ── Sites to scrape ────────────────────────────────────────────────────────────
ALL_SITES = ["linkedin", "indeed", "glassdoor"]

# ── Search queries ─────────────────────────────────────────────────────────────
# Each tuple: (search_term, use_internship_job_type)
SEARCH_QUERIES = [
    # Strategy & Consulting
    ("strategy intern Netherlands",         True),
    ("consultant intern Netherlands",       True),
    ("business analyst intern Netherlands", True),
    # Product Management
    ("product manager intern Netherlands",  True),
    ("associate product manager Netherlands", False),
    # Operations
    ("operations intern Netherlands",       True),
    ("business operations intern Netherlands", True),
    # MBA / Graduate
    ("MBA intern Netherlands",              False),
    ("graduate trainee Netherlands",        False),
    ("management trainee Netherlands",      False),
    # Program / Project Management
    ("program manager intern Netherlands",  True),
    ("project manager intern Netherlands",  True),
    # Dutch stage (internship) — catches Dutch-posted roles at English-first companies
    ("stage strategie Nederland",           True),
    ("stage product manager Nederland",     True),
    # Targeted consulting firm searches (they use non-standard titles)
    ("Accenture intern Netherlands",        False),
    ("Deloitte intern Netherlands",         False),
    ("KPMG intern Netherlands",             False),
    ("EY intern Netherlands",               False),
    ("PwC intern Netherlands",              False),
]

# ── NL location strings to keep ────────────────────────────────────────────────
NL_HINTS = [
    'netherlands', 'nederland', 'amsterdam', 'rotterdam', 'utrecht', 'eindhoven',
    'hague', 'den haag', 'leiden', 'delft', 'nijmegen', 'groningen', 'tilburg',
    'breda', 'maastricht', 'nl,', ', nl', 'nl '
]

# Locations that indicate non-NL jobs slipping through
NON_NL = [
    'london', 'united kingdom', 'uk,', ' uk ', 'paris', 'berlin', 'warsaw',
    'madrid', 'milan', 'stockholm', 'copenhagen', 'munich', 'zurich',
    'new york', 'san francisco', 'chicago', 'singapore', 'dubai', 'india',
    'bangalore', 'mumbai', 'sydney', 'australia', 'canada', 'toronto',
]

def _is_nl(job: dict) -> bool:
    loc = (job.get('location') or '').lower()
    title = (job.get('job_title') or '').lower()
    text = loc + ' ' + title
    if any(k in text for k in NON_NL):
        return False
    # Accept if location clearly mentions NL, or if location is vague/empty
    # (LinkedIn sometimes omits location for NL-only roles)
    if any(k in loc for k in NL_HINTS):
        return True
    # Accept ambiguous/empty locations — user can filter in dashboard
    if not loc or loc in ('none', 'nan', 'remote'):
        return True
    return False

# ── Parsing helpers ────────────────────────────────────────────────────────────
_INTERN_RE = re.compile(r'\bintern(ship)?\b', re.IGNORECASE)
_APPLICANT_RE = re.compile(
    r'be among the first\s+(\d+)\s+applicant'
    r'|(\d+)\s+applicants?\b'
    r'|over\s+(\d+)\s+applicants?',
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
_GENERIC_LOCAL = {'noreply', 'no-reply', 'info', 'careers', 'jobs', 'hr',
                  'privacy', 'legal', 'support', 'contact', 'hello', 'team'}

def _parse_applicant_count(text: str) -> int | None:
    m = _APPLICANT_RE.search(text or '')
    if not m:
        return None
    # Pick whichever capture group matched
    val = next(g for g in m.groups() if g is not None)
    return int(val)

def _parse_recruiter_email(text: str) -> str | None:
    for email in _EMAIL_RE.findall(text or ''):
        local = email.split('@')[0].lower()
        if not any(g in local for g in _GENERIC_LOCAL):
            return email
    return None

def _normalize(row: pd.Series) -> dict:
    def s(v):
        val = row.get(v)
        return str(val).strip() if val is not None and str(val) not in ('None', 'NaT', 'nan', '') else ''

    date_posted = None
    dp = row.get('date_posted')
    if dp and str(dp) not in ('None', 'NaT', 'nan'):
        try:
            date_posted = str(dp)[:10]
        except Exception:
            pass

    description = s('description')[:3000]
    applicant_count = _parse_applicant_count(description)
    recruiter_email = _parse_recruiter_email(description)

    return {
        'company':            s('company'),
        'job_title':          s('title'),
        'location':           s('location'),
        'careers_url':        s('company_url'),
        'job_url':            s('job_url'),
        'visa_support':       None,
        'relocation_support': None,
        'date_posted':        date_posted,
        'recruiter_email':    recruiter_email,
        'applicant_count':    applicant_count,
        'description':        description,
        'source':             s('site'),   # linkedin / indeed / glassdoor
    }

def scrape_query(query: str, sites: list, use_internship_type: bool,
                 fetch_descriptions: bool = False) -> pd.DataFrame | None:
    kwargs = dict(
        site_name=sites,
        search_term=query,
        location="Netherlands",
        results_wanted=50,
        hours_old=24 * 28,          # scrape 28-day window; dashboard date filter controls what's shown
        country_indeed="Netherlands",
        linkedin_fetch_description=fetch_descriptions,
        verbose=0,
    )
    if use_internship_type:
        kwargs['job_type'] = 'internship'

    try:
        df = scrape_jobs(**kwargs)
        return df if df is not None and not df.empty else None
    except Exception as e:
        print(f"    [!] Error: {e}")
        return None

def _enrich_linkedin_descriptions(all_jobs: list[dict]) -> None:
    """Fetch descriptions + applicant counts for LinkedIn jobs that have none,
    using LinkedIn's public guest job API (no login required)."""
    to_enrich = [
        j for j in all_jobs
        if j.get('source') == 'linkedin'
        and len(j.get('description', '')) < 50
        and '/view/' in (j.get('job_url') or '')
    ]
    if not to_enrich:
        print("All LinkedIn jobs already have descriptions.")
        return

    print(f"Enriching {len(to_enrich)} LinkedIn jobs via guest API...")
    enriched = 0
    for i, job in enumerate(to_enrich, 1):
        job_id = job['job_url'].rstrip('/').split('/')[-1]
        api_url = f'https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}'
        try:
            r = requests.get(api_url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'en-US,en;q=0.9',
            }, timeout=12)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                # Description
                desc_el = (soup.find('div', class_='show-more-less-html__markup')
                           or soup.find('div', class_='description__text')
                           or soup.find('section', class_='description'))
                if desc_el:
                    job['description'] = desc_el.get_text(separator='\n', strip=True)[:3000]
                    recruiter = _parse_recruiter_email(job['description'])
                    if recruiter:
                        job['recruiter_email'] = recruiter
                # Applicant count
                for sel in ['span.num-applicants__caption', 'figcaption.num-applicants__caption',
                            'span.jobs-unified-top-card__applicant-count']:
                    el = soup.select_one(sel)
                    if el:
                        count = _parse_applicant_count(el.get_text())
                        if count is not None:
                            job['applicant_count'] = count
                        break
                enriched += 1
        except Exception:
            pass

        if i % 20 == 0:
            print(f"  [{i}/{len(to_enrich)}] {enriched} enriched so far...")
            with open(OUT_FILE, 'w') as f:
                json.dump(all_jobs, f, ensure_ascii=False, indent=2)
        time.sleep(0.6)  # polite delay

    print(f"✅ Enriched {enriched}/{len(to_enrich)} LinkedIn jobs with descriptions.")
    with open(OUT_FILE, 'w') as f:
        json.dump(all_jobs, f, ensure_ascii=False, indent=2)


def main():
    force    = '--force'     in sys.argv
    full     = '--full'      in sys.argv   # fetch full descriptions via JobSpy (slower)
    enrich   = '--enrich'    in sys.argv   # enrich existing LinkedIn jobs via guest API
    li_only  = '--linkedin'  in sys.argv
    in_only  = '--indeed'    in sys.argv
    gl_only  = '--glassdoor' in sys.argv

    # --enrich: load file, fetch descriptions for LinkedIn jobs without them, done
    if enrich:
        if not os.path.exists(OUT_FILE):
            print("No linkedin_jobs.json found. Run without --enrich first.")
            return
        with open(OUT_FILE) as f:
            all_jobs = json.load(f)
        print(f"Loaded {len(all_jobs)} jobs.")
        _enrich_linkedin_descriptions(all_jobs)
        return

    if li_only:
        sites = ['linkedin']
    elif in_only:
        sites = ['indeed']
    elif gl_only:
        sites = ['glassdoor']
    else:
        sites = ALL_SITES

    if full:
        print("⚠  --full mode: fetching descriptions via JobSpy per-job (very slow).")
        print("   Prefer --enrich after a normal scrape for better results.\n")

    # Load existing results
    existing: list[dict] = []
    if os.path.exists(OUT_FILE):
        with open(OUT_FILE) as f:
            existing = json.load(f)

    if force:
        if li_only or in_only or gl_only:
            # Source-specific force: remove only jobs from this source, keep others
            keep_source = ('linkedin' if li_only else 'indeed' if in_only else 'glassdoor')
            existing = [j for j in existing if j.get('source') != keep_source]
            print(f"Force-refreshing {keep_source} jobs only ({len(existing)} other-source jobs kept).")
        else:
            # Full force: wipe everything
            existing = []
            print("Force mode: clearing all existing jobs.")
    else:
        print(f"Loaded {len(existing)} existing jobs (incremental). Use --force to rescrape.")

    seen_keys: set[tuple] = {(j['company'], j['job_title']) for j in existing}
    all_jobs = list(existing)
    new_count = 0

    print(f"\nScraping: {sites}")
    print(f"Queries:  {len(SEARCH_QUERIES)} (parallel, 4 workers)\n")

    def _run_query(args):
        i, (query, use_intern_type) = args
        print(f"[{i}/{len(SEARCH_QUERIES)}] {query!r}")
        df = scrape_query(query, sites, use_intern_type, fetch_descriptions=full)
        if df is None:
            print(f"    [{i}] No results.")
            return []
        rows = []
        for _, row in df.iterrows():
            job = _normalize(row)
            if job['company'] and job['job_title']:
                rows.append(job)
        print(f"    [{i}] {len(df)} scraped → {len(rows)} candidates")
        return rows

    all_rows = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        for batch in executor.map(_run_query, enumerate(SEARCH_QUERIES, 1)):
            all_rows.extend(batch)

    for job in all_rows:
        if not _is_nl(job):
            continue
        if not _INTERN_RE.search(job['job_title']):
            continue
        key = (job['company'], job['job_title'])
        if key not in seen_keys:
            seen_keys.add(key)
            all_jobs.append(job)
            new_count += 1

    with open(OUT_FILE, 'w') as f:
        json.dump(all_jobs, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Done! {new_count} new jobs added. Total: {len(all_jobs)} saved to:")
    print(f"   {OUT_FILE}")
    print(f"\nRun python3 build_dashboard.py to rebuild the dashboard.")

if __name__ == '__main__':
    main()
