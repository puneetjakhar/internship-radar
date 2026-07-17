#!/usr/bin/env python3
"""
Score internship jobs via Claude Sonnet, detect new ones, send emails to Vanshika.

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
    'Tech':        ['adyen','booking','uber','databricks','prosus','backbase','catawiki','tomtom'],
    'Industrial':  ['asml','nxp','signify','philips','wolters kluwer','vanderlande','tno'],
    'Finance':     ['ing ','rabobank','abn amro','nn group','aegon','van lanschot'],
    'FMCG':        ['unilever','heineken','akzonobel','dsm','ahold','frieslandcampina'],
}
SECTOR_SCORES = {'Energy':30,'Consulting':28,'Tech':22,'Industrial':20,'Finance':16,'FMCG':15,'Other':8}
COMPANY_BONUS = [
    (20, ['shell','eneco','vattenfall','tennet','alliander','stedin','orsted','ørsted','sbm offshore','neste','fugro']),
    (18, ['bcg','bain','mckinsey','oliver wyman','kearney','strategy&','roland berger','ey-parthenon','deloitte','accenture']),
    (15, ['adyen','booking','uber','databricks','prosus','backbase','catawiki','tomtom']),
    (12, ['unilever','heineken','akzonobel','dsm','firmenich','ahold','frieslandcampina']),
    (12, ['asml','nxp','signify','philips','wolters kluwer','vanderlande','tno']),
    (10, ['ing ','rabobank','abn amro','nn group','aegon','van lanschot']),
]
ROLES = [
    (50, ['strategy intern','corporate strategy intern','strategic planning intern']),
    (48, ['consultant intern','consulting intern','associate consultant intern','management consultant intern']),
    (46, ['operations intern','business operations intern','strategy & operations intern','strategy and operations intern','commercial intern']),
    (45, ['product manager intern','product management intern','associate product manager intern','apm intern']),
    (42, ['business analyst intern','commercial analyst intern']),
    (38, ['mba intern','graduate intern','management trainee intern']),
    (35, ['programme manager intern','program manager intern','project management intern']),
    (30, ['procurement intern','supply chain intern','sourcing intern']),
    (25, ['project manager intern']),
]
KW_BONUSES = [
    ('strategy',3),('operations',3),('sustainability',2),('energy',2),
    ('product',2),('mba',2),('transformation',2),('commercial',1),('analytics',1),('innovation',1),
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

VANSHIKA_PROFILE = """Vanshika is an MBA/master's student seeking internships (3-6 months) in the Netherlands.

What she's looking for (highest to lowest priority):
1. Strategy internships — corporate strategy, strategic planning, market entry, M&A
2. Management consulting internships — BCG, Bain, McKinsey, Deloitte, Accenture, Oliver Wyman, Roland Berger, Kearney
3. Operations / business operations / strategy & operations
4. Product management internships
5. Business analyst / commercial analyst internships
6. MBA / graduate / management trainee programs
7. Procurement, supply chain, program management (lower priority)

Preferred sectors: Energy (Shell, Vattenfall, Eneco, TenneT, Ørsted), Consulting firms, Tech (Adyen, Booking, Databricks), Industrial (ASML, Philips), Finance (ING, Rabobank), FMCG (Unilever, Heineken)

Not a fit: software engineering, data science / ML engineering, HR internships, pure marketing / PR, finance/accounting without strategy component, research internships."""

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

Score this internship opportunity for Vanshika on a scale of 0-100.

Job title: {title}
Company: {company}
Location: {location}
Description: {desc}

Rules:
- 85-100: excellent fit (right role type + preferred sector/company)
- 65-84: good fit (right role or sector, minor gaps)
- 45-64: moderate fit (adjacent role, acceptable sector)
- 20-44: weak fit (tangentially relevant)
- 0-19: not a fit (wrong function entirely)

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

    print(f'{len(scored)} new internships found:')
    for j, pct in scored[:10]:
        print(f'  {pct}% | {j.get("job_title")} @ {j.get("company")}')

    now_str = datetime.now(timezone.utc).strftime('%b %d %H:%M')
    html = build_email(
        f'{len(scored)} new internship{"s" if len(scored) != 1 else ""} found',
        f'Scored by Claude AI · Crawled at {now_str} UTC',
        scored,
    )
    send_email(f'{len(scored)} new NL internships | {now_str} UTC', html)

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
        f'Daily Report: {len(scored)} internships in the last 24 hours',
        f'{today} | Scored by Claude AI | Sorted by match',
        scored,
    )
    send_email(f'Daily internship report | {len(scored)} roles | {today}', html)
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
