#!/usr/bin/env python3
"""
Score full-time tech product management jobs via Claude Sonnet, detect new ones, send emails to Vanshika.

Modes:
  python notify.py            -- email new jobs found since last run (runs every hour)
  python notify.py --daily    -- email full 24h summary report (runs at 12pm NL)
"""
import json, os, re, sys, smtplib, threading
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
    (44, ['product owner','product lead','product builder']),
    (42, ['product operations','product ops','product strategy','product strategist','growth product','growth manager','growth lead']),
    (38, ['product analyst','go-to-market','gtm']),
    (36, ['technical program manager','program manager','programme manager','tpm']),
    (32, ['business analyst','product specialist']),
    (20, ['forward deployed']),
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
    return _apply_seniority(title, min(100, score))

# She has no full-time PM job yet, so seniority in the title bounds the fit.
_SENIORITY_CAPS = [
    (25, re.compile(r'\b(vp|vice president|head of|director|chief|cpo)\b', re.I)),
    (40, re.compile(r'\b(principal|group|staff|lead)\b', re.I)),
    (60, re.compile(r'\b(senior|sr\.?|expert)\b', re.I)),
]
_ENTRY_RE = re.compile(r'\b(associate|junior|jr\.?|new grad|graduate|entry)\b|\bapm\b', re.I)

def _apply_seniority(title: str, score: int) -> int:
    for cap, rx in _SENIORITY_CAPS:
        if rx.search(title):
            return min(score, cap)
    if _ENTRY_RE.search(title):
        return min(100, score + 10)
    return min(score, 85)   # title-only match: never claim a near-perfect fit without AI review

# ── AI scorer ──────────────────────────────────────────────────────────────────

VANSHIKA_PROFILE = """You are a rigorous recruiter-style evaluator. Score how well ONE job fits ONE candidate, Vanshika Rana, who wants a FULL-TIME tech product management role in the Netherlands. Judge the real match between what the job demands and what she has. Do not reward keywords, and do not give a high score just because the title says "Product Manager" or the company is famous.

=== CANDIDATE ===
- MBA, Rotterdam School of Management (RSM), 2024-2026, Strategy & Leadership. President of the RSM Product Club (100+ members), Markstrat product-strategy simulation 1st place, RSM Business Games winner (200+ teams).
- Currently doing an internship at TomTom in the Netherlands.
- About 5 years at ONGC (upstream oil and gas) as Deputy Manager, Production Operations: led operations for large assets, coordinated engineering, finance and regulatory teams, drove process improvement, managed senior stakeholders.
- IIT Bombay degree in a hard science/engineering field: strong quantitative and analytical base.
- Fluent English. Dutch is beginner (A1). Based in Rotterdam.
- Product experience so far: MBA coursework, Product Club leadership and the internship. She has NOT held a full-time product manager job and has not shipped software as a PM over multiple years. She does not code.

=== WHAT FITS ===
Best: Associate Product Manager, Product Manager (entry to mid level, roughly 0-3 years of PM experience asked), MBA or graduate product programmes, Technical PM roles that value analytical and domain depth over coding.
Good: Product Owner, Product Lead, AI Product Builder, product operations, product strategy/strategist, Product Analyst, growth and Go-to-Market (GTM) lead roles, and program manager roles inside product or engineering organisations, at a level open to a post-MBA hire.
Possible: Business Analyst and Product Specialist roles when they sit close to product (score lower if they are pure banking/ERP BA or sales/support specialist roles). Forward Deployed Engineer only if it is non-coding and customer/solutions facing; if it requires writing production code, score 10-25.
Domain edge: energy, energy-tech, industrial, mobility/maps and operations-heavy products, where ONGC and TomTom make her credible. Fintech and pure developer-tooling products are possible but she has no domain edge there.

=== SENIORITY IS THE MAIN FILTER ===
Work out the level the job really asks for, from the title AND the description (years of experience, "proven track record shipping", people management, scope).
- VP, Head of, Director, Chief: score 0-15. She is not a candidate.
- Principal, Group, Staff, Lead PM, or anything asking 7+ years of product management: score 5-25.
- Senior PM, or asking about 4-6 years of product management: score 20-45. At most 45, even at a dream company.
- PM with about 2-3 years asked: score 45-70 depending on domain match.
- Associate PM, junior PM, new-grad or MBA programme, or PM with no hard years requirement: score 65-95 depending on domain, company and language.
If there is no description, judge from the title alone and stay conservative: an unqualified "Product Manager" title with no description scores 50-65.

=== OTHER RULES ===
- Fluent Dutch required, or the posting is written in Dutch: cap at 25.
- Internship, working-student or thesis: 0-10 (she wants full-time).
- Not really product management (product marketing, sales, account management, engineering, design, data science, project management of construction or hardware rollouts): 0-25.
- "Product manager" for physical goods, retail categories, fashion, food or FMCG brands with no digital or technical product: 10-30.
- Requires deep hands-on technical skills she lacks (writing code, ML engineering, a specific engineering degree): reduce by 15-30.
- Outside the Netherlands: 0-10.
Use the full range. Most jobs should land between 20 and 70; scores of 85+ are rare and need level, role type and domain all to match."""

_ai_disabled = False   # set once the API rejects the key, so we stop calling it this run

def _ai_score(job):
    """Return an AI score 0-100, or None if AI scoring is unavailable."""
    global _ai_disabled
    if _ai_disabled:
        return None
    try:
        import anthropic
    except ImportError:
        return None

    title   = job.get('job_title') or ''
    company = job.get('company') or ''
    location = job.get('location') or ''
    desc    = (job.get('description') or '')[:4000]

    prompt = f"""Score this job for Vanshika.

Job title: {title}
Company: {company}
Location: {location}
Description: {desc or '(no description available)'}

Respond with only a JSON object:
{{"level": "<the seniority this job asks for, and the evidence>", "score": <integer 0-100>, "reason": "<one sentence>"}}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=400,
            system=[{'type': 'text', 'text': VANSHIKA_PROFILE, 'cache_control': {'type': 'ephemeral'}}],
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = response.content[0].text.strip()
        # strip markdown code fences if present
        if text.startswith('```'):
            text = text.split('```')[1].lstrip('json').strip()
        data = json.loads(text[text.index('{'):text.rindex('}') + 1])
        return max(0, min(100, int(data['score'])))
    except Exception as e:
        if any(k in str(e) for k in ('authentication_error', 'invalid x-api-key', 'credit balance is too low')):
            if not _ai_disabled:
                print(f'  ⚠ Anthropic API unusable ({str(e)[:120]}) — AI scoring is off for this run, using rules')
            _ai_disabled = True
        else:
            print(f'  ⚠ AI score failed for "{title}": {e} — using rules')
        return None

def calc_match(job) -> int:
    url = job.get('job_url') or job.get('url') or ''
    with _score_cache_lock:
        if url and url in _score_cache:
            return _score_cache[url]

    score = _ai_score(job) if ANTHROPIC_KEY else None
    if score is None:
        # Rule scores are a stopgap and are not cached, so the job is rescored by AI
        # as soon as the API key works.
        return _rule_score(job)

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

SOURCE_ORDER = ['LinkedIn', 'Company career pages', 'Indeed']

def job_source(job) -> str:
    url = (job.get('job_url') or job.get('url') or '').lower()
    src = (job.get('source') or '').lower()
    if 'linkedin.com' in url or src == 'linkedin':
        return 'LinkedIn'
    if 'indeed.' in url or src == 'indeed':
        return 'Indeed'
    return 'Company career pages'

def group_by_source(scored):
    """[(section name, [(job, score), ...] sorted by score desc), ...], empty sections skipped."""
    groups = {name: [] for name in SOURCE_ORDER}
    for job, pct in scored:
        groups[job_source(job)].append((job, pct))
    return [(name, sorted(g, key=lambda x: x[1], reverse=True)) for name, g in groups.items() if g]

def build_email(heading, subheading, scored):
    def _table(group):
        rows = build_rows(group)
        return f'''      <table style="width:100%;border-collapse:collapse">
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
      </table>'''
    sections = ''.join(
        f'<h2 style="margin:22px 0 8px;font-size:16px;color:#7c3aed">{name} '
        f'<span style="color:#94a3b8;font-weight:400">({len(g)})</span></h2>' + _table(g)
        for name, g in group_by_source(scored)
    )
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
{sections}
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
        f'{today} | Scored by Claude AI | Grouped by source, sorted by match',
        scored,
    )
    send_email(f'Daily product roles report | {len(scored)} roles | {today}', html)
    print(f'Daily report sent: {len(scored)} jobs')

def backfill_scores(all_jobs):
    """AI-score listed jobs that have no cached AI score yet (e.g. scored by rules while the key was down)."""
    if not ANTHROPIC_KEY or _ai_disabled:
        return
    todo = [j for j in all_jobs
            if (j.get('job_url') or j.get('url')) and (j.get('job_url') or j.get('url')) not in _score_cache]
    if not todo:
        return
    print(f'Backfilling AI scores for {len(todo)} jobs without one...')
    score_jobs_parallel(todo)
    _save_score_cache()

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
        backfill_scores(all_jobs)

if __name__ == '__main__':
    main()
