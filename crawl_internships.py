#!/usr/bin/env python3
"""
crawl_internships.py — NL Intern / MBA Job Search Crawler (Vanshika Rana)
==========================================================================
Crawls career pages of NL companies for strategy, product, operations,
program management, and consulting internship/junior roles.

Updates:
  - crawled_internships.json  (job listings)

Usage:
  python3 crawl_internships.py                  # crawl all companies
  python3 crawl_internships.py shell bcg        # crawl specific companies by keyword
  python3 crawl_internships.py --force          # bypass 24h cache
"""

import html as html_mod
import json
import re
import sys
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin, urlencode, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    os.system("pip3 install requests beautifulsoup4 lxml")
    import requests
    from bs4 import BeautifulSoup

# ── Target role keywords (case-insensitive substring match) ──────────────────
TARGET_ROLES = [
    # Strategy & Consulting
    'strategy intern', 'strategy analyst', 'strategy associate', 'consultant intern',
    'consulting intern', 'intern consultant', 'junior consultant', 'associate consultant',
    'business analyst intern', 'business analyst', 'junior analyst',
    'management consultant', 'strategy & operations', 'corporate strategy',
    'visiting associate', 'summer associate', 'fellow intern',
    # Product Management
    'product manager intern', 'product manager', 'product management intern',
    'associate product manager', 'apm', 'junior product manager',
    'product strategy', 'product analyst', 'product operations',
    # Program / Project Management
    'program manager intern', 'program manager', 'programme manager',
    'project manager intern', 'project manager', 'junior project manager',
    'technical program manager', 'tpm', 'delivery manager',
    # Operations & BizOps
    'operations intern', 'operations analyst', 'business operations',
    'biz ops', 'bizops', 'strategy and operations', 'operations associate',
    'operations manager', 'junior operations', 'go-to-market',
    # Business Development
    'business development intern', 'business development', 'biz dev',
    'commercial intern', 'commercial analyst', 'commercial associate',
    # General / MBA
    'mba intern', 'mba associate', 'graduate intern', 'trainee',
    'intern strategy', 'intern operations', 'intern product',
    'management trainee', 'graduate trainee', 'junior associate',
    # Procurement & Supply Chain
    'procurement intern', 'supply chain intern', 'supply chain analyst',
    'procurement analyst', 'sourcing intern',
    # Broad programme/scheme titles (catch-all)
    'internship programme', 'internship program', 'assessed internship',
    'graduate programme', 'graduate program', 'traineeship',
    'development programme', 'development program',
    'talent lab', 'young talent', 'early career',
]

# For compound matching — intern/junior × broad craft
_SENIORITY_KW = {'intern', 'junior', 'associate', 'trainee', 'graduate', 'mba', 'stage'}
_CRAFT_KW = {'strategy', 'operations', 'product', 'program', 'programme', 'project',
             'analyst', 'consultant', 'manager', 'development', 'procurement', 'commercial',
             'business', 'transformation', 'innovation', 'supply chain'}

# ── NL location keywords ──────────────────────────────────────────────────────
NL_LOCATIONS = [
    'netherlands', 'amsterdam', 'rotterdam', 'eindhoven', 'utrecht',
    'delft', 'hague', 'den haag', 'leiden', 'groningen', 'nl,', ', nl',
    'hilversum', 'zoetermeer', 'breda', 'tilburg', 'nijmegen',
    'veldhoven', 'hoofddorp', 'almere', 'arnhem', 'enschede',
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/html, */*',
    'Accept-Language': 'en-US,en;q=0.9',
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE = os.path.join(BASE_DIR, 'crawled_internships.json')
SPONSORS_FILE = os.path.join(BASE_DIR, 'companies.json')
DESC_CACHE_FILE = os.path.join(BASE_DIR, 'description_cache.json')
CRAWL_CACHE_FILE = os.path.join(BASE_DIR, 'crawl_cache.json')
CRAWL_CACHE_TTL_HOURS = 24

# ── Per-company crawl cache (keyed by company ind_name, TTL 24h) ──────────────
def _load_crawl_cache() -> dict:
    try:
        with open(CRAWL_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_crawl_cache(cache: dict):
    with open(CRAWL_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)

def _crawl_cache_get(cache: dict, name: str) -> list | None:
    """Return cached jobs if fresher than TTL, else None."""
    entry = cache.get(name)
    if not entry:
        return None
    cached_at = entry.get('cached_at', '')
    try:
        dt = datetime.fromisoformat(cached_at)
        age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if age_hours < CRAWL_CACHE_TTL_HOURS:
            return entry['jobs']
    except Exception:
        pass
    return None

def _crawl_cache_set(cache: dict, name: str, jobs: list):
    cache[name] = {
        'cached_at': datetime.now(timezone.utc).isoformat(),
        'jobs': jobs,
    }

CRAWL_CACHE = _load_crawl_cache()

# ── Description cache (persists across runs, keyed by job_url) ────────────────
def _load_desc_cache() -> dict:
    try:
        with open(DESC_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_desc_cache(cache: dict):
    with open(DESC_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)

DESC_CACHE = _load_desc_cache()
_desc_cache_lock = threading.Lock()
_crawl_cache_lock = threading.Lock()

def get_description(job_url: str, fetch_fn) -> str:
    """Return cached description or call fetch_fn() to get and cache it. Thread-safe."""
    with _desc_cache_lock:
        if job_url and job_url in DESC_CACHE:
            return DESC_CACHE[job_url]
    desc = fetch_fn() if fetch_fn else ''
    if job_url and desc:
        with _desc_cache_lock:
            DESC_CACHE[job_url] = desc
            _save_desc_cache(DESC_CACHE)
    return desc

# ── Helpers ───────────────────────────────────────────────────────────────────

def html_to_text(raw, max_len: int = 3000) -> str:
    """Strip HTML tags (including entity-encoded ones) and return plain text."""
    if not raw:
        return ''
    if not isinstance(raw, str):
        raw = json.dumps(raw) if isinstance(raw, (dict, list)) else str(raw)
    # Strip script/style blocks with content before tag removal
    raw = re.sub(r'<script[^>]*>.*?</script>', ' ', raw, flags=re.DOTALL | re.IGNORECASE)
    raw = re.sub(r'<style[^>]*>.*?</style>', ' ', raw, flags=re.DOTALL | re.IGNORECASE)
    unescaped = html_mod.unescape(raw)
    text = re.sub(r'<[^>]+>', ' ', unescaped)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_len]

def is_nl(location: str) -> bool:
    loc = (location or '').lower()
    return any(kw in loc for kw in NL_LOCATIONS)

def is_target_role(title: str) -> bool:
    t = (title or '').lower()
    if any(kw in t for kw in TARGET_ROLES):
        return True
    # Compound match: seniority + craft (catches "Senior Python Engineer", "Lead iOS Developer")
    has_seniority = any(s in t for s in _SENIORITY_KW)
    has_craft = any(c in t for c in _CRAFT_KW)
    return has_seniority and has_craft

def fmt_date(raw: str) -> str | None:
    """Normalise any ISO-ish date string to YYYY-MM-DD."""
    if not raw:
        return None
    try:
        # Handle timezone offsets and Z suffix
        raw = raw.strip().replace('Z', '+00:00')
        # Handle offset like -04:00 vs +0000
        if re.search(r'[+-]\d{4}$', raw):
            raw = raw[:-5] + raw[-5:-2] + ':' + raw[-2:]
        dt = datetime.fromisoformat(raw)
        return dt.date().isoformat()
    except Exception:
        # Try simple YYYY-MM-DD extraction
        m = re.search(r'(\d{4}-\d{2}-\d{2})', raw)
        return m.group(1) if m else None

def extract_emails_from_html(html: str) -> list[str]:
    """Extract recruiter-looking email addresses from HTML."""
    blocklist = re.compile(
        r'^(noreply|no-reply|donotreply|support|info|hello|contact|privacy|'
        r'legal|security|careers|jobs|apply|hr|talent|recruiting|notifications?|'
        r'alerts?|news|feedback|abuse|postmaster|webmaster|admin|team|press|'
        r'marketing|sales|billing|payments?|help|bot|automated|system)\b',
        re.I
    )
    emails = set()
    # mailto links first (most reliable)
    for m in re.finditer(r'href=["\']mailto:([^"\'?#\s]+)', html, re.I):
        emails.add(m.group(1).lower().strip())
    # Plain email patterns in text
    for m in re.finditer(r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b', html):
        emails.add(m.group(1).lower().strip())
    # Filter out system addresses
    result = []
    for e in emails:
        local = e.split('@')[0]
        domain = e.split('@')[1] if '@' in e else ''
        if blocklist.match(local):
            continue
        if any(x in domain for x in ('sentry', 'example', 'test.', 'amazonaws')):
            continue
        if len(local) < 3 or not '.' in domain:
            continue
        result.append(e)
    return result

def get(url: str, **kwargs) -> requests.Response | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, **kwargs)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f'    ⚠ GET failed: {url} — {e}')
        return None

# ── ATS Fetchers ──────────────────────────────────────────────────────────────

def fetch_greenhouse(company: dict) -> list[dict]:
    """Greenhouse public board API: boards-api.greenhouse.io/v1/boards/{board}/jobs"""
    board = company.get('greenhouse_board', '')
    url = f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true'
    print(f'  → Greenhouse API: {url}')
    r = get(url)
    if not r:
        return []
    data = r.json()
    jobs = data.get('jobs', [])
    print(f'    Total jobs on board: {len(jobs)}')

    results = []
    for j in jobs:
        title = j.get('title', '')
        location = j.get('location', {}).get('name', '')
        if not is_nl(location) or not is_target_role(title):
            continue

        job_id = j.get('id')
        job_url = j.get('absolute_url') or f'https://job-boards.greenhouse.io/{board}/jobs/{job_id}'
        date_posted = fmt_date(j.get('first_published') or j.get('updated_at'))
        dept = ''
        if j.get('departments'):
            dept = j['departments'][0].get('name', '')

        # Fetch individual job detail for description + recruiter email
        recruiter_email = None
        content_html = ''
        if job_id:
            detail_url = f'https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}'
            def _fetch_gh_detail():
                dr = get(detail_url)
                return dr.json().get('content', '') if dr else ''
            # Use cache: store raw HTML so email extraction also works on re-runs
            raw_cache_key = f'__html__{job_url}'
            if raw_cache_key in DESC_CACHE:
                content_html = DESC_CACHE[raw_cache_key]
            else:
                content_html = _fetch_gh_detail()
                if content_html:
                    DESC_CACHE[raw_cache_key] = content_html
                    _save_desc_cache(DESC_CACHE)
                time.sleep(0.3)
            description = html_to_text(content_html)
            # If Greenhouse API content is a placeholder (< 100 chars), try fallbacks
            if len(description.strip()) < 100:
                jd_base = company.get('jd_base_url', '')
                if jd_base:
                    # Derive slug from title: lowercase, replace non-alphanum with hyphen
                    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
                    jd_url = f'{jd_base}/{slug}/'
                    pr = get(jd_url)
                    if pr and pr.status_code == 200:
                        page_text = html_to_text(pr.text, max_len=5000)
                        if len(page_text) > 100:
                            description = page_text
                            job_url = jd_url  # Use the real job page as job_url
                            emails = extract_emails_from_html(pr.text)
                            recruiter_email = emails[0] if emails else None
                            time.sleep(0.3)
                if len(description.strip()) < 100 and job_url:
                    pr = get(job_url)
                    if pr:
                        page_text = html_to_text(pr.text, max_len=5000)
                        is_apply_form = (
                            'autofill with mygreenhouse' in pr.text.lower() and
                            'responsibilities' not in pr.text.lower() and
                            'requirements' not in pr.text.lower()
                        )
                        if not is_apply_form and len(page_text) > len(description):
                            description = page_text
                        emails = extract_emails_from_html(pr.text)
                        recruiter_email = emails[0] if emails else None
                        time.sleep(0.3)
            else:
                emails = extract_emails_from_html(content_html)
                recruiter_email = emails[0] if emails else None
                if not recruiter_email:
                    pr = get(job_url)
                    if pr:
                        emails = extract_emails_from_html(pr.text)
                        recruiter_email = emails[0] if emails else None

        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': location if ',' in location else f'{location}, Netherlands',
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': None,
            'relocation_support': None,
            'date_posted': date_posted,
            'recruiter_email': recruiter_email,
            'description': description,
            'notes': f'Dept: {dept}. Via Greenhouse board.' if dept else 'Via Greenhouse board.',
        })

    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_icims(company: dict) -> list[dict]:
    """iCIMS / Jibe API (Booking.com).
    Uses location+woe+regionCode+categories params discovered from Booking's search URL.
    Builds clean job URLs as jobs.booking.com/booking/jobs/{id}.
    """
    api_url = company.get('api_url', '')
    print(f'  → iCIMS API: {api_url}')

    # Fetch all pages for Engineering + ML categories
    seen_ids = set()
    all_jobs = []
    for category in ['Engineering', 'Machine Learning']:
        page = 1
        while True:
            params = {
                'location': 'Netherlands',
                'woe': '12',
                'regionCode': 'NL',
                'stretchUnit': 'MILES',
                'stretch': '25',
                'categories': category,
                'page': page,
            }
            r = get(api_url, params=params)
            if not r:
                break
            try:
                data = r.json()
            except Exception:
                break
            batch = data.get('jobs', [])
            if not batch:
                break
            new = 0
            for j in batch:
                jd = j.get('data', j)
                uid = jd.get('req_id') or jd.get('id') or jd.get('slug', '')
                if uid and uid in seen_ids:
                    continue
                if uid:
                    seen_ids.add(uid)
                all_jobs.append(j)
                new += 1
            print(f'    [{category}] page {page}: {new} new jobs')
            if len(batch) < 10:
                break
            page += 1
            time.sleep(0.4)

    print(f'    Total jobs fetched: {len(all_jobs)}')
    results = []
    for j in all_jobs:
        # iCIMS wraps in 'data' sub-object
        jd = j.get('data', j)
        title = jd.get('title', '')
        city = jd.get('city', '')
        country = jd.get('country', jd.get('country_name', ''))
        location = f"{city}, {country}".strip(', ')
        if not is_nl(location) or not is_target_role(title):
            continue

        # Build clean job URL: extract iCIMS job ID and use jobs.booking.com/booking/jobs/{id}
        raw_url = jd.get('apply_url') or jd.get('absolute_url') or jd.get('url') or ''
        job_id_m = re.search(r'/jobs/(\d+)', raw_url)
        job_url = f"https://jobs.booking.com/booking/jobs/{job_id_m.group(1)}" if job_id_m else raw_url or None
        date_posted = fmt_date(jd.get('posted_date') or jd.get('date_posted'))

        # Recruiter email: fetch the clean job page (has JSON-LD + mailto links)
        recruiter_email = None
        if job_url:
            pr = get(job_url)
            if pr:
                emails = extract_emails_from_html(pr.text)
                recruiter_email = emails[0] if emails else None
            time.sleep(0.4)

        # Check for visa / relocation mentions in description
        desc = (jd.get('description', '') or '').lower()
        visa = True if any(k in desc for k in ['visa', 'kennismigrant', 'highly skilled migrant', 'sponsor']) else None
        reloc = True if any(k in desc for k in ['relocation', 'relocation package', 'moving costs']) else None

        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': location,
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': visa,
            'relocation_support': reloc,
            'date_posted': date_posted,
            'recruiter_email': recruiter_email,
            'notes': 'Via iCIMS/Jibe API.',
        })

    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_lever(company: dict) -> list[dict]:
    """Lever public API: api.lever.co/v0/postings/{company} (or EU endpoint)"""
    board = company.get('lever_board', '')
    base = company.get('lever_base', 'https://api.lever.co/v0/postings')
    # Build URL with location filter where specified
    loc = company.get('location_filter', '')
    params = '?mode=json'
    if loc:
        params += f'&location={requests.utils.quote(loc)}'
    url = f'{base}/{board}{params}'
    print(f'  → Lever API: {url}')
    r = get(url)
    if not r:
        return []
    jobs = r.json()
    print(f'    Total postings: {len(jobs)}')
    results = []
    for j in jobs:
        title = j.get('text', '')
        categories = j.get('categories', {})
        location = categories.get('location', '') or j.get('workplaceType', '')
        all_locs = categories.get('allLocations', []) or []
        nl_match = is_nl(location) or any(is_nl(l) for l in all_locs)
        if not nl_match or not is_target_role(title):
            continue
        job_url = j.get('hostedUrl') or j.get('applyUrl')
        date_posted = fmt_date(str(j.get('createdAt', '') or ''))
        desc_html = j.get('description', '') or j.get('descriptionPlain', '')
        description = html_to_text(desc_html)
        recruiter_email = None
        pr = get(job_url) if job_url else None
        if pr:
            recruiter_email = (extract_emails_from_html(pr.text) or [None])[0]
            time.sleep(0.3)
        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': location,
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': None,
            'relocation_support': None,
            'date_posted': date_posted,
            'recruiter_email': recruiter_email,
            'description': description,
            'notes': 'Via Lever API.',
        })
    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_smartrecruiters(company: dict) -> list[dict]:
    """SmartRecruiters public API"""
    board = company.get('smartrecruiters_id', '')
    url = f'https://api.smartrecruiters.com/v1/companies/{board}/postings?limit=100&country=NLD'
    print(f'  → SmartRecruiters API: {url}')
    r = get(url)
    if not r:
        return []
    data = r.json()
    jobs = data.get('content', [])
    print(f'    Total NL jobs: {len(jobs)}')
    results = []
    for j in jobs:
        title = j.get('name', '')
        if not is_target_role(title):
            continue
        location = j.get('location', {})
        loc_str = f"{location.get('city','')}, {location.get('country','Netherlands')}".strip(', ')
        job_url = j.get('ref')
        date_posted = fmt_date(j.get('releasedDate') or j.get('updatedOn'))
        recruiter_email = None
        if job_url:
            pr = get(job_url)
            if pr:
                recruiter_email = (extract_emails_from_html(pr.text) or [None])[0]
            time.sleep(0.3)
        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': loc_str,
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': None,
            'relocation_support': None,
            'date_posted': date_posted,
            'recruiter_email': recruiter_email,
            'notes': 'Via SmartRecruiters API.',
        })
    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_html(company: dict) -> list[dict]:
    """Generic HTML scraper — tries JSON-LD, __NEXT_DATA__, then plain HTML."""
    url = company.get('careers_url', '')
    print(f'  → HTML scrape: {url}')
    r = get(url)
    if not r:
        return []
    html = r.text
    soup = BeautifulSoup(html, 'lxml')
    results = []

    # 1. JSON-LD JobPosting
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            d = json.loads(tag.string or '')
            arr = d if isinstance(d, list) else [d]
            for item in arr:
                if item.get('@type') != 'JobPosting':
                    continue
                title = item.get('title', '')
                loc = item.get('jobLocation', {})
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                address = loc.get('address', {})
                location = address.get('addressLocality', '') + ', ' + address.get('addressCountry', '')
                if not is_nl(location) or not is_target_role(title):
                    continue
                job_url = item.get('url') or url
                date_posted = fmt_date(item.get('datePosted'))
                desc_html = item.get('description', '')
                emails = extract_emails_from_html(desc_html)
                results.append({
                    'company': company['ind_name'],
                    'job_title': title,
                    'location': location,
                    'careers_url': company['careers_url'],
                    'job_url': job_url,
                    'visa_support': None,
                    'relocation_support': None,
                    'date_posted': date_posted,
                    'recruiter_email': emails[0] if emails else None,
                    'notes': 'Via JSON-LD schema.',
                })
        except Exception:
            pass

    # 2. __NEXT_DATA__
    if not results:
        nd_tag = soup.find('script', id='__NEXT_DATA__')
        if nd_tag:
            try:
                nd = json.loads(nd_tag.string or '')
                nd_str = json.dumps(nd)
                # Extract job-like objects heuristically
                for m in re.finditer(r'"title"\s*:\s*"([^"]+)".*?"(?:url|href|link)"\s*:\s*"(https?://[^"]+)"', nd_str):
                    title, job_url = m.group(1), m.group(2)
                    if is_target_role(title):
                        results.append({
                            'company': company['ind_name'],
                            'job_title': title,
                            'location': 'Netherlands',
                            'careers_url': url,
                            'job_url': job_url,
                            'visa_support': None,
                            'relocation_support': None,
                            'date_posted': None,
                            'recruiter_email': None,
                            'notes': 'Via __NEXT_DATA__.',
                        })
            except Exception:
                pass

    print(f'    Matched NL target roles: {len(results)}')
    return results


def _extract_jobs_from_json(data, company: dict, source_note: str) -> list[dict]:
    """Recursively search a JSON blob for job listing arrays."""
    results = []
    seen_urls = set()

    def search(obj, depth=0):
        if depth > 8 or len(results) > 200:
            return
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            sample = obj[0]
            # Looks like a job array if items have title/name + some url/location field
            has_title = any(k in sample for k in ('title','name','job_title','position'))
            has_link  = any(k in sample for k in ('url','link','href','applyUrl','apply_url','absolute_url','hostedUrl','jobUrl'))
            if has_title or has_link:
                for item in obj:
                    if not isinstance(item, dict):
                        continue
                    title_raw = (item.get('title') or item.get('name') or
                                item.get('job_title') or item.get('position') or '')
                    # title_raw can be a dict in WP REST (e.g. {'rendered': 'Engineer'})
                    if isinstance(title_raw, dict):
                        title_raw = (title_raw.get('rendered') or title_raw.get('text') or
                                     title_raw.get('name') or '')
                    title = str(title_raw).strip()
                    if not title or not is_target_role(title):
                        continue
                    # Location extraction
                    loc_raw = (item.get('location') or item.get('city') or item.get('office') or
                               item.get('locationName') or item.get('country') or
                               item.get('workplaceType') or '')
                    if isinstance(loc_raw, dict):
                        loc_raw = (loc_raw.get('name') or loc_raw.get('city') or
                                   loc_raw.get('label') or loc_raw.get('country') or '')
                    loc_str = str(loc_raw).strip()
                    # Skip jobs with no location (avoids false NL labelling of global roles)
                    # Exception: allow empty location only if company careers URL is NL-specific
                    careers_url_is_nl = is_nl(company.get('careers_url', ''))
                    if not loc_str and not careers_url_is_nl:
                        continue
                    if loc_str and loc_str.lower() not in ('remote', 'hybrid', '') and not is_nl(loc_str):
                        continue
                    # URL
                    job_url = (item.get('url') or item.get('link') or item.get('href') or
                               item.get('applyUrl') or item.get('apply_url') or
                               item.get('absolute_url') or item.get('hostedUrl') or
                               item.get('jobUrl') or '')
                    job_url = str(job_url)
                    if job_url.startswith('/'):
                        from urllib.parse import urlparse
                        base = urlparse(company['careers_url'])
                        job_url = f'{base.scheme}://{base.netloc}{job_url}'
                    if job_url in seen_urls:
                        continue
                    seen_urls.add(job_url)
                    date = fmt_date(str(item.get('datePosted') or item.get('date') or
                                       item.get('published_at') or item.get('publishedAt') or ''))
                    desc = html_to_text(item.get('description') or item.get('descriptionHtml') or
                                        item.get('body') or item.get('content') or '')
                    results.append({
                        'company': company['ind_name'],
                        'job_title': title,
                        'location': loc_str or 'Netherlands',
                        'careers_url': company['careers_url'],
                        'job_url': job_url,
                        'visa_support': None,
                        'relocation_support': None,
                        'date_posted': date,
                        'recruiter_email': None,
                        'description': desc,
                        'notes': source_note,
                    })
        elif isinstance(obj, dict):
            # Check promising keys first
            for key in ('jobs','postings','positions','vacancies','offers','results',
                        'data','items','listing','jobPostings','edges','nodes','hits',
                        'content','requisitions','openings','careers','roles'):
                if key in obj and isinstance(obj[key], (list, dict)):
                    search(obj[key], depth + 1)
            # Recurse shallowly into all values
            if depth < 4:
                for v in obj.values():
                    if isinstance(v, (dict, list)):
                        search(v, depth + 1)

    search(data)
    return results


def fetch_spa(company: dict) -> list[dict]:
    """Playwright SPA scraper: network interception → __NEXT_DATA__ → JSON-LD → DOM heuristics."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    url = company.get('careers_url', '')
    print(f'  → Playwright SPA: {url}')

    captured = []   # (url, json_body) tuples from XHR/fetch responses

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
            ),
            viewport={'width': 1280, 'height': 900},
        )
        page = ctx.new_page()

        def on_response(response):
            ct = response.headers.get('content-type', '')
            if response.status == 200 and 'json' in ct:
                try:
                    captured.append((response.url, response.json()))
                except Exception:
                    pass

        page.on('response', on_response)

        try:
            page.goto(url, wait_until='networkidle', timeout=25000)
        except PWTimeout:
            try:
                page.wait_for_timeout(6000)
            except Exception:
                pass
        except Exception as e:
            print(f'    ⚠ Navigation error: {e}')

        # Dismiss cookie banners (common patterns) so content loads
        for selector in (
            'button[id*="accept"], button[class*="accept"], button[class*="Accept"]',
            'button[id*="cookie"][class*="agree"], #onetrust-accept-btn-handler',
            '[data-testid="cookie-accept"], [aria-label*="Accept"], [aria-label*="accept all"]',
            'button:has-text("Accept all"), button:has-text("Accept cookies")',
            'button:has-text("Akkoord"), button:has-text("Accepteren")',
        ):
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    page.wait_for_timeout(1500)
                    break
            except Exception:
                pass

        # ── Strategy 1: Network interception ─────────────────────────────────
        for req_url, data in captured:
            found = _extract_jobs_from_json(data, company, 'Via XHR interception (Playwright).')
            if found:
                results.extend(found)
                print(f'    ✓ Found {len(found)} jobs via XHR ({req_url[:60]})')
                break

        # ── Strategy 2: window.__NEXT_DATA__ / __NUXT__ ──────────────────────
        if not results:
            for var in ('__NEXT_DATA__', '__NUXT__', '__INITIAL_STATE__', '__APP_STATE__'):
                try:
                    data = page.evaluate(f'window["{var}"]')
                    if data:
                        found = _extract_jobs_from_json(data, company, f'Via {var} (Playwright).')
                        if found:
                            results.extend(found)
                            print(f'    ✓ Found {len(found)} jobs via {var}')
                            break
                except Exception:
                    pass

        # ── Strategy 3: JSON-LD JobPosting ───────────────────────────────────
        if not results:
            content = page.content()
            for m in re.finditer(
                r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                content, re.DOTALL | re.IGNORECASE
            ):
                try:
                    blob = json.loads(m.group(1))
                    arr = blob if isinstance(blob, list) else [blob]
                    for item in arr:
                        if item.get('@type') != 'JobPosting':
                            continue
                        title = item.get('title', '')
                        if not is_target_role(title):
                            continue
                        loc = item.get('jobLocation', {})
                        if isinstance(loc, list):
                            loc = loc[0] if loc else {}
                        addr = loc.get('address', {})
                        location = f"{addr.get('addressLocality','')} {addr.get('addressCountry','')}".strip()
                        if location and not is_nl(location):
                            continue
                        results.append({
                            'company': company['ind_name'],
                            'job_title': title,
                            'location': location or 'Netherlands',
                            'careers_url': company['careers_url'],
                            'job_url': item.get('url', url),
                            'visa_support': None,
                            'relocation_support': None,
                            'date_posted': fmt_date(item.get('datePosted', '')),
                            'recruiter_email': None,
                            'description': html_to_text(item.get('description', '')),
                            'notes': 'Via JSON-LD (Playwright).',
                        })
                    if results:
                        print(f'    ✓ Found {len(results)} jobs via JSON-LD')
                        break
                except Exception:
                    pass

        # ── Strategy 4: DOM heuristics — find job card links ─────────────────
        if not results:
            try:
                cards = page.query_selector_all(
                    'a[href*="/job"], a[href*="/career"], a[href*="/vacatur"], '
                    'a[href*="/position"], a[href*="/opening"], a[href*="/role"]'
                )
                from urllib.parse import urlparse as _up
                base = _up(url)
                for card in cards[:200]:
                    title = (card.get_attribute('aria-label') or card.inner_text() or '').strip()
                    if not title or not is_target_role(title):
                        continue
                    href = card.get_attribute('href') or ''
                    if href.startswith('/'):
                        href = f'{base.scheme}://{base.netloc}{href}'
                    results.append({
                        'company': company['ind_name'],
                        'job_title': title[:120],
                        'location': 'Netherlands',
                        'careers_url': company['careers_url'],
                        'job_url': href,
                        'visa_support': None,
                        'relocation_support': None,
                        'date_posted': None,
                        'recruiter_email': None,
                        'description': '',
                        'notes': 'Via DOM link heuristic (Playwright).',
                    })
                if results:
                    print(f'    ✓ Found {len(results)} jobs via DOM heuristics')
            except Exception as e:
                print(f'    ⚠ DOM heuristic failed: {e}')

        browser.close()

    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_ashby(company: dict) -> list[dict]:
    """Ashby public job board API: api.ashbyhq.com/posting-api/job-board/{board}"""
    board = company.get('ashby_board', '')
    url = f'https://api.ashbyhq.com/posting-api/job-board/{board}'
    print(f'  → Ashby API: {url}')
    r = get(url)
    if not r:
        return []
    data = r.json()
    # Ashby API v1 used 'jobPostings', v2 uses 'jobs'
    jobs = data.get('jobs', data.get('jobPostings', []))
    print(f'    Total postings: {len(jobs)}')
    results = []
    for j in jobs:
        title = j.get('title', '')
        if not is_target_role(title):
            continue
        location = j.get('location', '') or j.get('locationName', '')
        # Also check address.postalAddress for country/city (new Ashby API format)
        addr = (j.get('address') or {}).get('postalAddress') or {}
        addr_str = f"{addr.get('addressLocality','')} {addr.get('addressCountry','')}".strip()
        location_full = f"{location} {addr_str}".strip()
        # Accept NL locations, or remote/hybrid with no explicit non-NL country
        if location_full and not is_nl(location_full) and location.lower() not in ('remote', 'hybrid', ''):
            continue
        job_url = j.get('jobUrl') or j.get('applyUrl') or ''
        date_posted = fmt_date(j.get('publishedAt') or j.get('createdAt'))
        desc_html = j.get('descriptionHtml') or j.get('descriptionPlain') or j.get('description') or ''
        description = html_to_text(desc_html)
        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': location or 'Netherlands',
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': None,
            'relocation_support': None,
            'date_posted': date_posted,
            'description': description,
            'recruiter_email': None,
            'notes': 'Via Ashby API.',
        })
    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_recruitee(company: dict) -> list[dict]:
    """Recruitee public API: {board}.recruitee.com/api/offers/"""
    board = company.get('recruitee_board', '')
    url = f'https://{board}.recruitee.com/api/offers/'
    print(f'  → Recruitee API: {url}')
    r = get(url)
    if not r:
        return []
    data = r.json()
    jobs = data.get('offers', [])
    print(f'    Total postings: {len(jobs)}')
    results = []
    for j in jobs:
        title = j.get('title', '')
        if not is_target_role(title):
            continue
        city = j.get('city', '')
        country = j.get('country', '')
        location = f'{city}, {country}'.strip(', ')
        if location and not is_nl(location):
            continue
        job_url = j.get('careers_url') or f'https://{board}.recruitee.com/o/{j.get("slug", "")}'
        date_posted = fmt_date(j.get('published_at') or j.get('created_at'))
        desc_html = j.get('description', '') or j.get('description_html', '')
        description = html_to_text(desc_html)
        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': location or 'Netherlands',
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': None,
            'relocation_support': None,
            'date_posted': date_posted,
            'recruiter_email': None,
            'description': description,
            'notes': 'Via Recruitee API.',
        })
    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_picnic(company: dict) -> list[dict]:
    """Picnic jobs: slugs embedded in Next.js RSC payload as engineering/{slug}/amsterdam paths."""
    url = company.get('careers_url', 'https://jobs.picnic.app/en/jobs')
    print(f'  → Picnic RSC scrape: {url}')
    r = get(url)
    if not r:
        return []
    # Extract job slugs from RSC __next_f payload: engineering/{slug}/amsterdam
    slugs = list(dict.fromkeys(re.findall(r'engineering/([a-z0-9-]+)/amsterdam', r.text)))
    print(f'    Found {len(slugs)} Amsterdam engineering slugs')
    results = []
    for slug in slugs:
        # Convert slug to display title
        title = slug.replace('-', ' ').title()
        job_url = f'https://jobs.picnic.app/en/jobs/engineering/{slug}/amsterdam/north-holland/netherlands'
        # Fetch individual job page for real title + description
        jr = get(job_url)
        if jr:
            # Look for real title in page
            m_title = re.search(r'<title>([^<|]+)', jr.text)
            if m_title:
                real_title = m_title.group(1).strip()
                if real_title and len(real_title) > 3:
                    title = real_title
            # Try JSON-LD
            for jld_m in re.finditer(
                r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', jr.text, re.DOTALL
            ):
                try:
                    jld = json.loads(jld_m.group(1))
                    items = jld if isinstance(jld, list) else [jld]
                    for item in items:
                        if item.get('@type') == 'JobPosting':
                            title = item.get('title', title)
                            break
                except Exception:
                    pass
            time.sleep(0.3)
        if not is_target_role(title):
            continue
        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': 'Amsterdam, Netherlands',
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': None,
            'relocation_support': None,
            'date_posted': None,
            'recruiter_email': None,
            'description': '',
            'notes': 'Via Picnic RSC slug extraction.',
        })
    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_homerun(company: dict) -> list[dict]:
    """Homerun ATS: jobs embedded as JSON in Vue v-bind attribute in page HTML."""
    url = company.get('careers_url', '')
    print(f'  → Homerun scrape: {url}')
    r = get(url)
    if not r:
        return []
    text = r.text
    # Try v-bind:jobs="[...]" or :jobs="[...]" or data-jobs="[...]" patterns
    for pat in (
        r'v-bind:jobs=[\'"](.*?)[\'"](?=\s|>)',
        r':jobs=[\'"](.*?)[\'"](?=\s|>)',
        r'data-jobs=[\'"](.*?)[\'"](?=\s|>)',
    ):
        m = re.search(pat, text, re.DOTALL)
        if m:
            break
    if not m:
        print('    ⚠ Could not find jobs JSON in Homerun page')
        return []
    try:
        jobs_raw = json.loads(html_mod.unescape(m.group(1)))
    except Exception as e:
        print(f'    ⚠ JSON parse error: {e}')
        return []
    if not isinstance(jobs_raw, list):
        jobs_raw = [jobs_raw]
    print(f'    Total postings: {len(jobs_raw)}')
    results = []
    base_parsed = urlparse(url)
    for j in jobs_raw:
        title = j.get('title', '') or j.get('name', '')
        if not is_target_role(title):
            continue
        location = j.get('location', '') or j.get('city', '') or 'Netherlands'
        if location and location.lower() not in ('remote', 'hybrid', '') and not is_nl(location):
            continue
        job_path = j.get('url', '') or j.get('link', '') or j.get('applyUrl', '')
        if job_path and job_path.startswith('/'):
            job_url = f'{base_parsed.scheme}://{base_parsed.netloc}{job_path}'
        elif job_path and job_path.startswith('http'):
            job_url = job_path
        else:
            job_url = url
        date_posted = fmt_date(j.get('published_at') or j.get('created_at') or j.get('date', ''))
        description = html_to_text(j.get('description', '') or j.get('body', ''))
        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': location,
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': None,
            'relocation_support': None,
            'date_posted': date_posted,
            'recruiter_email': None,
            'description': description,
            'notes': 'Via Homerun.',
        })
    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_kpmg_lunr(company: dict) -> list[dict]:
    """KPMG NL: jobs in static lunr.js JSON index at /en/_lunr/vacancies_en"""
    base_url = 'https://www.werkenbijkpmg.nl'
    api_url = f'{base_url}/en/_lunr/vacancies_en'
    print(f'  → KPMG lunr JSON: {api_url}')
    r = get(api_url)
    if not r:
        return []
    try:
        data = r.json()
    except Exception as e:
        print(f'    ⚠ JSON parse error: {e}')
        return []
    # lunr format: top-level object with a list under one of these keys, or just a list
    if isinstance(data, dict):
        docs = data.get('docs') or data.get('documents') or data.get('vacancies') or []
        if not docs:
            for v in data.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    docs = v
                    break
    else:
        docs = data if isinstance(data, list) else []
    print(f'    Total postings: {len(docs)}')
    results = []
    for j in docs:
        title = (j.get('title') or j.get('name') or j.get('vacancyTitle') or '').strip()
        if not is_target_role(title):
            continue
        location = j.get('location') or j.get('city') or 'Netherlands'
        path = j.get('url') or j.get('link') or j.get('path') or ''
        job_url = f'{base_url}{path}' if path.startswith('/') else path or company['careers_url']
        date_posted = fmt_date(j.get('date') or j.get('published_at') or '')
        desc = j.get('body') or j.get('description') or j.get('content') or ''
        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': location,
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': None,
            'relocation_support': None,
            'date_posted': date_posted,
            'recruiter_email': None,
            'description': html_to_text(desc),
            'notes': 'Via KPMG lunr JSON.',
        })
    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_workday(company: dict) -> list[dict]:
    """Workday public jobs API (POST to wday/cxs endpoint) — searches NL + paginates."""
    tenant = company.get('workday_tenant', '')
    board = company.get('workday_board', '')
    wnum = company.get('workday_num', '5')
    base = f'https://{tenant}.wd{wnum}.myworkdayjobs.com'
    api_url = f'{base}/wday/cxs/{tenant}/{board}/jobs'
    print(f'  → Workday API: {api_url}')

    NL_TERMS = {'netherlands', 'amsterdam', 'rotterdam', 'utrecht', 'eindhoven'}
    all_jobs = []  # list of (job_dict, found_via_nl_term)
    # Search multiple terms to catch NL strategy/ops/product/intern roles
    for search_term in ['netherlands', 'amsterdam', 'intern', 'strategy', 'product manager',
                        'program manager', 'operations', 'analyst', 'consultant', 'trainee']:
        nl_search = search_term in NL_TERMS
        offset = 0
        limit = 20
        while True:
            payload = {'limit': limit, 'offset': offset, 'searchText': search_term}
            try:
                r = requests.post(api_url, json=payload,
                                  headers={**HEADERS, 'Content-Type': 'application/json'}, timeout=15)
                r.raise_for_status()
            except Exception as e:
                print(f'    ⚠ Workday POST failed ({search_term}): {e}')
                break
            data = r.json()
            batch = data.get('jobPostings', [])
            total = data.get('total', 0)
            if not batch:
                break
            all_jobs.extend((j, nl_search) for j in batch)
            print(f'    [{search_term}] offset={offset}: {len(batch)} jobs (total={total})')
            offset += limit
            if offset >= min(total, 100):  # cap at 100 per search term
                break
            time.sleep(0.3)

    # Deduplicate — prefer nl_search=True entries
    seen = {}
    for j, nl_s in all_jobs:
        key = j.get('externalPath') or j.get('title', '')
        if key not in seen or nl_s:
            seen[key] = (j, nl_s)
    unique_jobs = list(seen.values())

    print(f'    Unique postings: {len(unique_jobs)}')
    results = []
    for j, found_via_nl in unique_jobs:
        title = j.get('title', '')
        if not is_target_role(title):
            continue
        location = j.get('locationsText', '')
        # If found via NL search term, trust it's NL even if location says "X Locations"
        if not found_via_nl and location and not is_nl(location):
            continue
        ext_path = j.get('externalPath', '')
        # externalPath is like /job/Location/Title_ID — Workday needs board prefix
        job_url = f'{base}/{board}{ext_path}' if ext_path else company['careers_url']
        date_posted = fmt_date(j.get('postedOn'))
        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': location or 'Netherlands',
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': None,
            'relocation_support': None,
            'date_posted': date_posted,
            'recruiter_email': None,
            'notes': 'Via Workday API.',
        })
    print(f'    Matched NL target roles: {len(results)}')
    return results


def fetch_bitvavo(company: dict) -> list[dict]:
    """Bitvavo custom Next.js careers site — uses RSC endpoint to get job list JSON."""
    url = 'https://jobs.bitvavo.com/find-your-role'
    print(f'  → Bitvavo RSC endpoint: {url}')
    try:
        r = requests.get(url, headers={**HEADERS, 'RSC': '1'}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f'    ⚠ GET failed: {url} — {e}')
        return []

    # RSC flight payload — find embedded jobs JSON array
    m = re.search(r'"jobs":(\[.*?\])', r.text)
    if not m:
        print('    ⚠ Could not find jobs array in RSC payload')
        return []

    try:
        jobs_raw = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f'    ⚠ JSON parse error: {e}')
        return []

    print(f'    Total postings: {len(jobs_raw)}')
    results = []
    for j in jobs_raw:
        title = j.get('title', '')
        if not is_target_role(title):
            continue
        location = j.get('locationName', '') or 'Amsterdam, Netherlands'
        link = j.get('link', '')
        job_url = f'https://jobs.bitvavo.com{link}' if link.startswith('/') else link
        results.append({
            'company': company['ind_name'],
            'job_title': title,
            'location': location if ',' in location else f'{location}, Netherlands',
            'careers_url': company['careers_url'],
            'job_url': job_url,
            'visa_support': None,
            'relocation_support': None,
            'date_posted': None,
            'recruiter_email': None,
            'notes': f'Dept: {j.get("departmentName", "")}. Via Bitvavo RSC endpoint.',
        })
    print(f'    Matched target roles: {len(results)}')
    return results


FETCHERS = {
    'picnic': fetch_picnic,
    'greenhouse': fetch_greenhouse,
    'icims': fetch_icims,
    'lever': fetch_lever,
    'smartrecruiters': fetch_smartrecruiters,
    'ashby': fetch_ashby,
    'recruitee': fetch_recruitee,
    'homerun': fetch_homerun,
    'kpmg_lunr': fetch_kpmg_lunr,
    'workday': fetch_workday,
    'html': fetch_html,
    'spa': fetch_spa,
    'bitvavo': fetch_bitvavo,
}

# ── Core crawler ──────────────────────────────────────────────────────────────

def _crawl_one(company: dict, force: bool) -> tuple[str, list]:
    """Crawl a single company and return (name, jobs). Thread-safe for API types."""
    name = company['ind_name']
    ctype = company.get('type', 'html')

    if not force:
        cached = _crawl_cache_get(CRAWL_CACHE, name)
        if cached is not None:
            print(f'⚡ {name} [{ctype}] — cache ({len(cached)} jobs)')
            return name, cached

    print(f'🔍 {name} [{ctype}]')
    fetcher = FETCHERS.get(ctype, fetch_html)
    try:
        jobs = fetcher(company)
        if not jobs and ctype == 'html':
            print(f'  ↩ {name} — HTML 0, retrying SPA...')
            try:
                jobs = fetch_spa(company)
            except Exception as spa_err:
                print(f'  ⚠ {name} SPA failed: {spa_err}')
        print(f'  ✅ {name}: {len(jobs)} jobs')
        return name, jobs
    except Exception as e:
        print(f'  ❌ {name}: {e}')
        return name, []


def crawl(companies: list[dict], force: bool = False, workers: int = 20) -> list[dict]:
    # html-type uses requests only (Playwright fallback is rare and safe in threads)
    # spa-type uses Playwright — parallel with a smaller pool to avoid resource exhaustion
    spa_companies   = [c for c in companies if c.get('type', 'html') == 'spa']
    other_companies = [c for c in companies if c.get('type', 'html') != 'spa']

    results: dict[str, list] = {}

    def _collect(future_map):
        for future in as_completed(future_map):
            name, jobs = future.result()
            results[name] = jobs
            if jobs:
                with _crawl_cache_lock:
                    _crawl_cache_set(CRAWL_CACHE, name, jobs)
        with _crawl_cache_lock:
            _save_crawl_cache(CRAWL_CACHE)

    # Parallel pass — API + HTML companies
    if other_companies:
        print(f'\n🚀 Parallel crawl: {len(other_companies)} companies ({workers} workers)...')
        with ThreadPoolExecutor(max_workers=workers) as executor:
            _collect({executor.submit(_crawl_one, c, force): c for c in other_companies})

    # Parallel SPA pass — Playwright companies (4 workers, each gets its own browser process)
    if spa_companies:
        spa_workers = min(4, len(spa_companies))
        print(f'\n🌐 Parallel SPA crawl: {len(spa_companies)} Playwright companies ({spa_workers} workers)...')
        with ThreadPoolExecutor(max_workers=spa_workers) as executor:
            _collect({executor.submit(_crawl_one, c, force): c for c in spa_companies})

    # Preserve original order
    all_jobs = []
    for company in companies:
        all_jobs.extend(results.get(company['ind_name'], []))
    return all_jobs

def update_sponsors_careers_url(companies: list[dict]):
    """Add careers_url to matching entries in ind_sponsors.json."""
    with open(SPONSORS_FILE, 'r', encoding='utf-8') as f:
        sponsors = json.load(f)

    updated = 0
    for company in companies:
        kvk = company.get('kvk', '')
        careers_url = company.get('careers_url', '')
        if not kvk or not careers_url:
            continue
        for s in sponsors:
            if s.get('kvk') == kvk:
                if s.get('careers_url') != careers_url:
                    s['careers_url'] = careers_url
                    updated += 1
                break

    with open(SPONSORS_FILE, 'w', encoding='utf-8') as f:
        json.dump(sponsors, f, ensure_ascii=False, indent=2)
    print(f'\n📝 Updated careers_url for {updated} entries in ind_sponsors.json')

def save_jobs(jobs: list[dict]):
    with open(JOBS_FILE, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    print(f'\n💾 Saved {len(jobs)} roles to crawled_internships.json')

# ── Company list ─────────────────────────────────────────────────────────────

COMPANIES = [

    # ════════════════════════════════════════════════════
    # ── GREENHOUSE ───────────────────────────────────
    # ════════════════════════════════════════════════════

    {"ind_name": "Adyen N.V.",              "type": "greenhouse", "greenhouse_board": "adyen",
     "careers_url": "https://careers.adyen.com"},

    {"ind_name": "Catawiki B.V.",           "type": "greenhouse", "greenhouse_board": "catawiki",
     "careers_url": "https://catawiki.careers"},

    {"ind_name": "Backbase B.V.",           "type": "greenhouse", "greenhouse_board": "workatbackbase",
     "careers_url": "https://job-boards.greenhouse.io/workatbackbase"},

    {"ind_name": "Databricks",              "type": "greenhouse", "greenhouse_board": "Databricks",
     "careers_url": "https://www.databricks.com/company/careers"},

    {"ind_name": "Guerrilla Games B.V.",    "type": "greenhouse", "greenhouse_board": "guerrilla-games",
     "careers_url": "https://www.guerrilla-games.com/join"},

    {"ind_name": "Flow Traders B.V.",       "type": "greenhouse", "greenhouse_board": "flowtraders",
     "careers_url": "https://job-boards.greenhouse.io/flowtraders"},

    {"ind_name": "IMC Trading",             "type": "greenhouse", "greenhouse_board": "imc",
     "greenhouse_eu": True,
     "careers_url": "https://job-boards.eu.greenhouse.io/imc"},

    {"ind_name": "Capgemini Invent NL",     "type": "greenhouse", "greenhouse_board": "capgeminideutschlandgmbh",
     "greenhouse_eu": True,
     "careers_url": "https://job-boards.eu.greenhouse.io/capgeminideutschlandgmbh"},

    # ════════════════════════════════════════════════════
    # ── LEVER ────────────────────────────────────────
    # ════════════════════════════════════════════════════

    {"ind_name": "Mollie B.V.",             "type": "lever", "lever_slug": "mollie",
     "careers_url": "https://jobs.eu.lever.co/mollie"},

    {"ind_name": "Prosus N.V.",             "type": "lever", "lever_slug": "prosus", "lever_eu": True,
     "careers_url": "https://jobs.eu.lever.co/prosus"},

    # ════════════════════════════════════════════════════
    # ── SMARTRECRUITERS ──────────────────────────────
    # ════════════════════════════════════════════════════

    {"ind_name": "Booking.com B.V.",        "type": "smartrecruiters", "smartrecruiters_company": "Booking",
     "careers_url": "https://careers.booking.com"},

    {"ind_name": "Deloitte Netherlands",    "type": "smartrecruiters", "smartrecruiters_company": "Deloitte6",
     "careers_url": "https://careers.smartrecruiters.com/Deloitte6"},

    {"ind_name": "Roland Berger Amsterdam", "type": "smartrecruiters", "smartrecruiters_company": "RolandBerger",
     "careers_url": "https://careers.smartrecruiters.com/RolandBerger"},

    {"ind_name": "Turner & Townsend",       "type": "smartrecruiters", "smartrecruiters_company": "TurnerTownsend",
     "careers_url": "https://careers.smartrecruiters.com/TurnerTownsend"},

    {"ind_name": "ASML",                    "type": "smartrecruiters", "smartrecruiters_company": "ASML1",
     "careers_url": "https://careers.smartrecruiters.com/ASML1"},

    {"ind_name": "Heineken International B.V.", "type": "smartrecruiters", "smartrecruiters_company": "HEINEKENInternational",
     "careers_url": "https://careers.theheinekencompany.com"},

    # ════════════════════════════════════════════════════
    # ── WORKDAY ──────────────────────────────────────
    # ════════════════════════════════════════════════════

    {"ind_name": "Shell Netherlands",       "type": "workday",
     "workday_tenant": "shell", "workday_board": "ShellCareers", "workday_num": "3",
     "careers_url": "https://shell.wd3.myworkdayjobs.com/ShellCareers"},

    {"ind_name": "ING Bank N.V.",           "type": "workday",
     "workday_tenant": "ing", "workday_board": "ICSNLDGEN", "workday_num": "3",
     "careers_url": "https://ing.wd3.myworkdayjobs.com/ICSNLDGEN"},

    {"ind_name": "Unilever Netherlands",    "type": "workday",
     "workday_tenant": "unilever", "workday_board": "Unilever_Early_Careers", "workday_num": "3",
     "careers_url": "https://unilever.wd3.myworkdayjobs.com/Unilever_Early_Careers"},

    {"ind_name": "Philips Electronics Nederland B.V.", "type": "workday",
     "workday_tenant": "philips", "workday_board": "jobs-and-careers", "workday_num": "3",
     "careers_url": "https://philips.wd3.myworkdayjobs.com/jobs-and-careers"},

    {"ind_name": "Signify Netherlands B.V.", "type": "workday",
     "workday_tenant": "lighting", "workday_board": "jobs-and-careers", "workday_num": "3",
     "careers_url": "https://lighting.wd3.myworkdayjobs.com/jobs-and-careers"},

    {"ind_name": "NXP Semiconductors Netherlands B.V.", "type": "workday",
     "workday_tenant": "nxp", "workday_board": "careers", "workday_num": "3",
     "careers_url": "https://nxp.wd3.myworkdayjobs.com/careers"},

    {"ind_name": "Wolters Kluwer N.V.",     "type": "workday",
     "workday_tenant": "wk", "workday_board": "External", "workday_num": "3",
     "careers_url": "https://wk.wd3.myworkdayjobs.com/External"},

    {"ind_name": "Alliander N.V.",          "type": "workday",
     "workday_tenant": "alliander", "workday_board": "alliander", "workday_num": "3",
     "careers_url": "https://alliander.wd3.myworkdayjobs.com/alliander"},

    {"ind_name": "Stedin Groep N.V.",       "type": "workday",
     "workday_tenant": "stedin", "workday_board": "WerkenbijStedin", "workday_num": "3",
     "careers_url": "https://stedin.wd3.myworkdayjobs.com/WerkenbijStedin"},

    {"ind_name": "Rabobank Nederland",      "type": "workday",
     "workday_tenant": "rabobank", "workday_board": "jobs", "workday_num": "3",
     "careers_url": "https://rabobank.wd3.myworkdayjobs.com/jobs"},

    {"ind_name": "ABN AMRO Bank N.V.",      "type": "workday",
     "workday_tenant": "abnamro", "workday_board": "External", "workday_num": "3",
     "careers_url": "https://abnamro.wd3.myworkdayjobs.com/External"},

    {"ind_name": "Vanderlande Industries B.V.", "type": "workday",
     "workday_tenant": "vanderlande", "workday_board": "careers", "workday_num": "3",
     "careers_url": "https://vanderlande.wd3.myworkdayjobs.com/careers"},

    {"ind_name": "Accenture Netherlands",   "type": "workday",
     "workday_tenant": "accenture", "workday_board": "AccentureNLCareers", "workday_num": "3",
     "careers_url": "https://accenture.wd3.myworkdayjobs.com/AccentureNLCareers"},

    # ════════════════════════════════════════════════════
    # ── SPA / HTML (consulting & own portals) ────────
    # ════════════════════════════════════════════════════

    # ── Consulting ───────────────────────────────────

    {"ind_name": "BCG Amsterdam",           "type": "spa",
     "careers_url": "https://careers.bcg.com/global/en/locations/the-netherlands"},

    {"ind_name": "Bain & Company Amsterdam", "type": "spa",
     "careers_url": "https://careers.bain.com/jobs/SearchJobs/?3_56_3=2892"},

    {"ind_name": "McKinsey & Company Amsterdam", "type": "spa",
     "careers_url": "https://www.mckinsey.com/nl/careers"},

    {"ind_name": "Oliver Wyman Amsterdam",  "type": "spa",
     "careers_url": "https://careers.marsh.com/global/en/oliver-wyman-early-careers-search"},

    {"ind_name": "EY-Parthenon Netherlands", "type": "spa",
     "careers_url": "https://www.ey.com/en_nl/careers/parthenon/students-and-entry-level"},

    {"ind_name": "PwC / Strategy& Netherlands", "type": "spa",
     "careers_url": "https://www.pwc.nl/en/careers/vacatures.html"},

    {"ind_name": "KPMG Netherlands",        "type": "kpmg_lunr",
     "careers_url": "https://www.werkenbijkpmg.nl/en/internships-theses"},

    {"ind_name": "Kearney Amsterdam",       "type": "spa",
     "careers_url": "https://www.kearney.com/open-positions?country=Netherlands"},

    {"ind_name": "Berenschot",              "type": "html",
     "careers_url": "https://www.berenschot.com/careers-at-berenschot"},

    {"ind_name": "Twynstra Gudde",          "type": "html",
     "careers_url": "https://www.twynstragudde.nl/vacatures"},

    {"ind_name": "Arcadis Netherlands",     "type": "spa",
     "careers_url": "https://careers.arcadis.com/graduates-and-students/"},

    # ── Energy & Sustainability ────────────────────

    {"ind_name": "Eneco",                   "type": "html",
     "careers_url": "https://www.jobsateneco.com/Internships"},

    {"ind_name": "Vattenfall Netherlands",  "type": "spa",
     "careers_url": "https://careers.vattenfall.com/internships"},

    {"ind_name": "TenneT TSO B.V.",         "type": "spa",
     "careers_url": "https://www.werkenbijtennet.nl/en/expertises/interns-and-graduates"},

    {"ind_name": "Gasunie N.V.",            "type": "html",
     "careers_url": "https://www.gasunie.nl/werken-bij-gasunie"},

    {"ind_name": "SBM Offshore N.V.",       "type": "spa",
     "careers_url": "https://careers.sbmoffshore.com/go/The-Netherlands/8785302/"},

    {"ind_name": "Ørsted Netherlands",      "type": "spa",
     "careers_url": "https://orsted.com/en/careers/early-careers"},

    {"ind_name": "Neste Netherlands",       "type": "spa",
     "careers_url": "https://jobs.neste.com"},

    {"ind_name": "Fugro N.V.",              "type": "html",
     "careers_url": "https://www.fugro.com/careers/countries/netherlands"},

    # ── FMCG & Consumer ───────────────────────────

    {"ind_name": "AkzoNobel N.V.",          "type": "spa",
     "careers_url": "https://careers.akzonobel.com/"},

    {"ind_name": "DSM-Firmenich Netherlands", "type": "spa",
     "careers_url": "https://careers.dsm-firmenich.com/en/careers/early-career.html"},

    {"ind_name": "FrieslandCampina",        "type": "spa",
     "careers_url": "https://careers.frieslandcampina.com/nld/en/page/internships"},

    {"ind_name": "Ahold Delhaize",          "type": "spa",
     "careers_url": "https://careers.aholddelhaize.com/young-professionals/internships"},

    {"ind_name": "JDE Peet's",              "type": "html",
     "careers_url": "https://careers-nl.jdepeets.com/job-search/"},

    {"ind_name": "Randstad N.V.",           "type": "spa",
     "careers_url": "https://www.randstad.com/careers-at-randstad/"},

    # ── Financial Services ────────────────────────

    {"ind_name": "NN Group N.V.",           "type": "spa",
     "careers_url": "https://www.nn-careers.com/en/internships"},

    {"ind_name": "Aegon N.V.",              "type": "spa",
     "careers_url": "https://careers.aegon.com/nl/home/netherlands/"},

    {"ind_name": "Van Lanschot Kempen N.V.", "type": "spa",
     "careers_url": "https://careers.vanlanschotkempen.com/en-nl/young-talent"},

    # ── Tech & Digital ────────────────────────────

    {"ind_name": "TomTom International B.V.", "type": "spa",
     "careers_url": "https://www.tomtom.com/careers/"},

    {"ind_name": "Uber B.V.",               "type": "spa",
     "careers_url": "https://www.uber.com/us/en/careers/locations/amsterdam/"},

    {"ind_name": "Bol.com B.V.",            "type": "spa",
     "careers_url": "https://careers.bol.com/en/earlycareers/"},

    {"ind_name": "Coolblue B.V.",           "type": "spa",
     "careers_url": "https://www.careersatcoolblue.com/vacancies/"},

    {"ind_name": "Just Eat Takeaway.com",   "type": "spa",
     "careers_url": "https://careers.justeattakeaway.com/global/en/the-netherlands"},

    {"ind_name": "Exact Software B.V.",     "type": "spa",
     "careers_url": "https://www.exact.com/nl/company/careers"},

    # ── Logistics ─────────────────────────────────

    {"ind_name": "PostNL N.V.",             "type": "spa",
     "careers_url": "https://www.postnl.nl/werkenbij/"},

]

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    # Args: optional company keywords + --force to bypass 24h cache
    args = sys.argv[1:]
    force_crawl = '--force' in args
    workers = next((int(a.split('=')[1]) for a in args if a.startswith('--workers=')), 20)
    filter_kws = [a.lower() for a in args if not a.startswith('--')]

    companies_to_crawl = [
        c for c in COMPANIES
        if not filter_kws or any(kw in c['ind_name'].lower() for kw in filter_kws)
    ]

    if not companies_to_crawl:
        print(f'No companies matched: {filter_kws}')
        sys.exit(1)

    if force_crawl:
        print(f'🚀 Crawling {len(companies_to_crawl)} companies (--force, ignoring cache, --workers={workers})...')
    else:
        print(f'🚀 Crawling {len(companies_to_crawl)} companies (cache <24h reused; use --force to bypass, --workers=N to tune)...')
    jobs = crawl(companies_to_crawl, force=force_crawl, workers=workers)

    if not jobs and not filter_kws:
        print('\n⚠  No matching jobs found. Check TARGET_ROLES / NL_LOCATIONS filters.')
        sys.exit(0)

    # If crawling all companies, replace file. If subset, merge with existing.
    if filter_kws:
        try:
            with open(JOBS_FILE, 'r', encoding='utf-8') as f:
                existing = json.load(f)
            # Remove old entries for companies we just crawled
            crawled_names = {c['ind_name'] for c in companies_to_crawl}
            existing = [j for j in existing if j.get('company') not in crawled_names]
            jobs = existing + jobs
            print(f'\n🔀 Merged with existing: {len(jobs)} total jobs')
        except FileNotFoundError:
            pass

    save_jobs(jobs)
    update_sponsors_careers_url(companies_to_crawl)

    print('\n📊 Summary:')
    from collections import Counter
    for company, count in Counter(j['company'] for j in jobs).items():
        print(f'  {company}: {count} jobs')
    dated = sum(1 for j in jobs if j.get('date_posted'))
    emailed = sum(1 for j in jobs if j.get('recruiter_email'))
    print(f'\n  Date posted available: {dated}/{len(jobs)}')
    print(f'  Recruiter email found: {emailed}/{len(jobs)}')
