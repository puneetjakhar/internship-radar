#!/usr/bin/env python3
"""
Score full-time tech product management jobs via Claude Sonnet, detect new ones, send emails to Vanshika.

Modes:
  python notify.py            -- email new jobs found since last run (runs every hour)
  python notify.py --daily    -- email full 24h summary report (runs at 12pm NL)
"""
import json, os, sys, smtplib, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE      = os.path.join(BASE_DIR, 'crawled_internships.json')
LINKEDIN_FILE  = os.path.join(BASE_DIR, 'linkedin_jobs.json')
SEEN_FILE      = os.path.join(BASE_DIR, 'seen_jobs.json')
SCORE_FILE     = os.path.join(BASE_DIR, 'score_cache.json')

SENDER_EMAIL   = os.environ.get('SENDER_EMAIL', 'puneetkumarait@gmail.com')
NOTIFY_EMAIL   = os.environ.get('NOTIFY_EMAIL', 'vanshikarana.1000@gmail.com')
GMAIL_PASS     = os.environ.get('GMAIL_APP_PASSWORD', '')
DASHBOARD_URL  = os.environ.get('DASHBOARD_URL', 'https://puneetjakhar.github.io/vanshika-nl-jobs-dashboard/')
ANTHROPIC_KEY  = os.environ.get('ANTHROPIC_API_KEY', '')

# ── Score cache (persists across runs so daily report doesn't re-score) ────────

_score_cache: dict = {}
_score_cache_lock = threading.Lock()

def _load_score_cache():
    global _score_cache
    if os.path.exists(SCORE_FILE):
        try:
            with open(SCORE_FILE) as f:
                _score_cache = json.load(f)
        except Exception:
            _score_cache = {}

def _save_score_cache():
    with open(SCORE_FILE, 'w') as f:
        json.dump(_score_cache, f, indent=2)

# ── Rule-based fallback scorer ─────────────────────────────────────────────────

SECTOR_MAP = {
    'Energy':      ['shell','eneco','vattenfall','tennet','alliander','stedin','orsted','ørsted','sbm offshore','neste','fugro'],
    'Consulting':  ['bcg','bain','mckinsey','oliver wyman','kearney','strategy&','roland berger','ey-parthenon','deloitte','accenture'],
    'Tech':        ['adyen','booking','uber','databricks','prosus','backbase','catawiki','tomtom','mollie','bol.com','just eat','coolblue','exact'],
    'Industrial':  ['asml','nxp','signify','philips','wolters kluwer','vanderlande','tno'],
    'Finance':     ['ing ','rabobank','abn amro','nn group','aegon','van lanschot'],
    'FMCG':        ['unilever','heineken','akzonobel','dsm','ahold','frieslandcampina'],
}
SECTOR_SCORES = {'Tech':30,'Industrial':22,'Energy':22,'Finance':20,'FMCG':14,'Consulting':12,'Other':12}
COMPANY_BONUS = [
    (20, ['adyen','booking','uber','databricks','prosus','backbase','catawiki','tomtom','mollie','bol.com','just eat','coolblue','exact']),
    (16, ['asml','nxp','signify','philips','wolters kluwer','vanderlande','tno']),
    (14, ['shell','eneco','vattenfall','tennet','alliander','stedin','orsted','ørsted','sbm offshore','neste','fugro']),
    (12, ['ing ','rabobank','abn amro','nn group','aegon','van lanschot']),
    (10, ['unilever','heineken','akzonobel','dsm','firmenich','ahold','frieslandcampina']),
    (6,  ['bcg','bain','mckinsey','oliver wyman','kearney','strategy&','roland berger','ey-parthenon','deloitte','accenture']),
]
ROLES = [
    (50, ['technical product manager','product manager','productmanager','product management']),
    (48, ['associate product manager','apm']),
    (44, ['product owner','product lead']),
    (42, ['product operations','product ops','product strategy','growth product','growth manager']),
    (36, ['technical program manager','program manager','programme manager','tpm']),
    (25, ['head of product','director of product','vp of product']),
]
KW_BONUSES = [
    ('product',3),('platform',2),('data',2),('growth',2),('energy',2),
    ('roadmap',2),('b2b',1),('saas',1),('analytics',1),('strategy',1),
]

def _rule_score(job) -> int:
    title   = (job.get('job_title') or '').lower()
    company = (job.get('company') or '').lower()
    desc    = (job.get('description') or '').lower()[:800]
    score   = 0
    role_score = 0
    for pts, kws in ROLES:
        if any(k in title for k in kws):
            role_score = pts
            break
    score += role_score if role_score else 15
    sector = next((s for s, kws in SECTOR_MAP.items() if any(k in company for k in kws)), 'Other')
    score += SECTOR_SCORES.get(sector, 8)
    for pts, kws in COMPANY_BONUS:
        if any(k in company for k in kws):
            score += pts
            break
    bonus = 0
    for kw, pts in KW_BONUSES:
        if kw in title:
            bonus += pts
        elif kw in desc:
            bonus += pts // 2
    score += min(10, bonus)
    return min(100, score)

# ── AI scorer ──────────────────────────────────────────────────────────────────

VANSHIKA_PROFILE = """Vanshika Rana is looking for a FULL-TIME tech product management role in the Netherlands.

Background:
- MBA at Rotterdam School of Management (RSM), 2024-2026, Strategy & Leadership. President of the RSM Product Club (100+ members), Markstrat simulation 1st place, RSM Business Games winner.
- Currently doing an internship at TomTom in the Netherlands.
- Around 5-6 years at ONGC (upstream oil and gas) leading production operations and cross-functional teams.
- IIT Bombay science/engineering degree: strong quantitative and analytical foundation.
- Fluent English, beginner Dutch (A1). Based in Rotterdam.
- No full-time product management job yet, so she is realistic for Associate PM, Product Manager and some mid-level PM roles, not for roles demanding many years of prior PM experience.

What she is looking for (highest to lowest priority):
1. Product Manager / Technical Product Manager / Associate Product Manager at tech or tech-enabled companies
2. Product Owner
3. Product operations, product strategy, growth product roles
4. Program Manager / Technical Program Manager in a product or engineering organisation

Preferred employers: tech product companies (Adyen, Booking.com, TomTom, Uber, Databricks, Mollie, bol, Backbase, Catawiki), industrial tech (ASML, Philips, Signify), energy and energy-tech (Shell, Eneco, Vattenfall, TenneT) where her ONGC domain knowledge is a real edge, and fintech/banks with digital product teams.

Not a fit: internships, working-student or thesis roles; software engineering, data science or design roles; product marketing, sales, account management; physical-goods or FMCG brand/category 'product manager' roles with no digital or technical product; roles that require fluent Dutch; roles that require 5+ years of product management experience (Senior/Principal/Head/Director) should score low to moderate, not high."""

def _ai_score(job) -> int:
    try:
        import anthropic
    except ImportError:
        return _rule_score(job)

    title   = job.get('job_title') or ''
    company = job.get('company') or ''
    location = job.get('location') or ''
    desc    = (job.get('description') or '')[:2000]

    prompt = f"""{VANSHIKA_PROFILE}

Score this job for Vanshika on a scale of 0-100.

Job title: {title}
Company: {company}
Location: {location}
Description: {desc}

Rules:
- 85-100: excellent fit (digital/technical product role at her level + preferred employer or domain)
- 65-84: good fit (right role type, minor gaps in seniority or domain)
- 45-64: moderate fit (adjacent role, or a product role that is a seniority stretch)
- 20-44: weak fit (tangentially relevant)
- 0-19: not a fit (wrong function, internship, or Dutch required)

Respond with only a JSON object: {{"score": <integer 0-100>, "reason": "<one sentence>"}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = response.content[0].text.strip()
        # strip markdown code fences if present
        if text.startswith('```'):
            text = text.split('```')[1].lstrip('json').strip()
        data = json.loads(text)
        return max(0, min(100, int(data['score'])))
    except Exception as e:
        print(f'  ⚠ AI score failed for "{title}": {e} — using rules')
        return _rule_score(job)

def calc_match(job) -> int:
    url = job.get('job_url') or job.get('url') or ''
    with _score_cache_lock:
        if url and url in _score_cache:
            return _score_cache[url]

    if ANTHROPIC_KEY:
        score = _ai_score(job)
    else:
        print('  ⚠ ANTHROPIC_API_KEY not set — using rule-based scoring')
        score = _rule_score(job)

    if url:
        with _score_cache_lock:
            _score_cache[url] = score
    return score

def score_jobs_parallel(jobs: list) -> list[tuple]:
    """Return [(job, score), ...] sorted descending, scored in parallel."""
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_job = {executor.submit(calc_match, j): j for j in jobs}
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            try:
                score = future.result()
            except Exception as e:
                print(f'  ⚠ Score error: {e}')
                score = _rule_score(job)
            results.append((job, score))
    return sorted(results, key=lambda x: x[1], reverse=True)

# ── Seen-jobs store ────────────────────────────────────────────────────────────

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return json.load(f)
    return {}

def save_seen(seen: dict):
    with open(SEEN_FILE, 'w') as f:
        json.dump(seen, f, indent=2)

def load_all_jobs():
    jobs = []
    for path in [JOBS_FILE, LINKEDIN_FILE]:
        if os.path.exists(path):
            with open(path) as f:
                jobs.extend(json.load(f))
    return jobs

# ── Email builder ──────────────────────────────────────────────────────────────

def match_color(pct):
    if pct >= 85: return '#22c55e'
    if pct >= 70: return '#3b82f6'
    if pct >= 55: return '#f59e0b'
    return '#94a3b8'

def build_rows(scored):
    rows = ''
    for job, pct in scored:
        url   = job.get('job_url') or job.get('url') or job.get('careers_url') or '#'
        title = job.get('job_title') or 'Unknown'
        co    = job.get('company') or ''
        loc   = job.get('location') or 'Netherlands'
        date  = job.get('date_posted') or ''
        color = match_color(pct)
        rows += (
            f'<tr>'
            f'<td style="padding:9px 8px;border-bottom:1px solid #e2e8f0;text-align:center">'
            f'<span style="background:{color};color:#fff;border-radius:12px;padding:3px 9px;font-weight:600;font-size:13px">{pct}%</span></td>'
            f'<td style="padding:9px 8px;border-bottom:1px solid #e2e8f0">'
            f'<a href="{url}" style="color:#7c3aed;text-decoration:none;font-weight:600">{title}</a></td>'
            f'<td style="padding:9px 8px;border-bottom:1px solid #e2e8f0;color:#475569">{co}</td>'
            f'<td style="padding:9px 8px;border-bottom:1px solid #e2e8f0;color:#64748b;font-size:12px">{loc}</td>'
            f'<td style="padding:9px 8px;border-bottom:1px solid #e2e8f0;color:#94a3b8;font-size:12px">{date}</td>'
            f'<td style="padding:9px 8px;border-bottom:1px solid #e2e8f0;text-align:center">'
            f'<a href="{url}" style="background:#7c3aed;color:#fff;padding:4px 12px;border-radius:6px;text-decoration:none;font-size:12px;white-space:nowrap">Apply</a></td>'
            f'</tr>'
        )
    return rows

def build_email(heading, subheading, scored):
    rows = build_rows(scored)
    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f8fafc;margin:0;padding:20px">
  <div style="max-width:840px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08)">
    <div style="background:#7c3aed;padding:22px 28px">
      <h1 style="margin:0;color:#fff;font-size:20px">{heading}</h1>
      <p style="margin:5px 0 0;color:#ede9fe;font-size:13px">{subheading}</p>
    </div>
    <div style="padding:20px 28px">
      <a href="{DASHBOARD_URL}" style="display:inline-block;background:#7c3aed;color:#fff;padding:8px 20px;border-radius:8px;text-decoration:none;font-weight:600;margin-bottom:20px">Open Dashboard</a>
      <table style="width:100%;border-collapse:collapse">
        <thead>
          <tr style="background:#f5f3ff">
            <th style="padding:9px 8px;text-align:left;font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase">Match</th>
            <th style="padding:9px 8px;text-align:left;font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase">Role</th>
            <th style="padding:9px 8px;text-align:left;font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase">Company</th>
            <th style="padding:9px 8px;text-align:left;font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase">Location</th>
            <th style="padding:9px 8px;text-align:left;font-size:11px;color:#64748b;font-weight:600;text-transform:uppercase">Posted</th>
            <th></th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
  </div>
</body></html>'''

def send_email(subject, html_body):
    if not GMAIL_PASS:
        print('GMAIL_APP_PASSWORD not set, skipping email')
        return
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = SENDER_EMAIL
    msg['To']      = NOTIFY_EMAIL
    msg.attach(MIMEText(html_body, 'html'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(SENDER_EMAIL, GMAIL_PASS)
        smtp.sendmail(SENDER_EMAIL, NOTIFY_EMAIL, msg.as_string())
    print(f'Email sent to {NOTIFY_EMAIL}: {subject}')

# ── Modes ──────────────────────────────────────────────────────────────────────

def run_hourly(all_jobs, seen):
    now_iso = datetime.now(timezone.utc).isoformat()
    new_jobs = [j for j in all_jobs if (j.get('job_url') or j.get('url')) and
                (j.get('job_url') or j.get('url')) not in seen]
    if not new_jobs:
        print(f'No new jobs (tracking {len(seen)} seen)')
        return seen

    print(f'Scoring {len(new_jobs)} new jobs via Claude Sonnet...')
    scored = score_jobs_parallel(new_jobs)
    _save_score_cache()

    print(f'{len(scored)} new product roles found:')
    for j, pct in scored[:10]:
        print(f'  {pct}% | {j.get("job_title")} @ {j.get("company")}')

    now_str = datetime.now(timezone.utc).strftime('%b %d %H:%M')
    html = build_email(
        f'{len(scored)} new product role{"s" if len(scored) != 1 else ""} found',
        f'Scored by Claude AI · Crawled at {now_str} UTC',
        scored,
    )
    send_email(f'{len(scored)} new NL product roles | {now_str} UTC', html)

    for j, _ in scored:
        url = j.get('job_url') or j.get('url')
        if url:
            seen[url] = now_iso
    return seen

def run_daily(all_jobs, seen):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    jobs_24h = [
        j for j in all_jobs
        if (j.get('job_url') or j.get('url')) in seen
        and datetime.fromisoformat(seen[j.get('job_url') or j.get('url')]).replace(tzinfo=timezone.utc) >= cutoff
    ]
    if not jobs_24h:
        print('No jobs seen in last 24h for daily report')
        return

    print(f'Scoring {len(jobs_24h)} jobs for daily report (cached scores reused)...')
    scored = score_jobs_parallel(jobs_24h)
    _save_score_cache()

    today = datetime.now(timezone.utc).strftime('%b %d, %Y')
    html = build_email(
        f'Daily Report: {len(scored)} product roles in the last 24 hours',
        f'{today} | Scored by Claude AI | Sorted by match',
        scored,
    )
    send_email(f'Daily product roles report | {len(scored)} roles | {today}', html)
    print(f'Daily report sent: {len(scored)} jobs')

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    daily_mode = '--daily' in sys.argv
    _load_score_cache()
    all_jobs = load_all_jobs()
    if not all_jobs:
        print('No jobs found')
        return

    seen = load_seen()

    if daily_mode:
        run_daily(all_jobs, seen)
    else:
        seen = run_hourly(all_jobs, seen)
        save_seen(seen)

if __name__ == '__main__':
    main()
