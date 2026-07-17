#!/usr/bin/env python3
"""Build Vanshika's job-search.html and autoapply.html from crawled_internships.json."""
import json, re, html as html_mod, os

BASE = os.path.dirname(os.path.abspath(__file__))
JOBS_JSON    = os.path.join(BASE, 'crawled_internships.json')
LINKEDIN_JSON = os.path.join(BASE, 'linkedin_jobs.json')
DASH_OUT     = os.path.join(BASE, 'job-search.html')
AUTO_OUT     = os.path.join(BASE, 'autoapply.html')

with open(JOBS_JSON) as f:
    crawler_jobs = json.load(f)

# Tag company-crawled jobs with source='crawler'
for j in crawler_jobs:
    j.setdefault('source', 'crawler')

# Load LinkedIn/Indeed/Glassdoor jobs if present
linkedin_jobs = []
if os.path.exists(LINKEDIN_JSON):
    with open(LINKEDIN_JSON) as f:
        linkedin_jobs = json.load(f)
    print(f"  Loaded {len(linkedin_jobs)} jobs from linkedin_jobs.json")

jobs = crawler_jobs + linkedin_jobs

# Only keep jobs with "intern" or "internship" in the title
_intern_re = re.compile(r'\bintern(ship)?\b', re.IGNORECASE)
jobs = [j for j in jobs if _intern_re.search(j.get('job_title', ''))]

# Exclude obviously non-NL locations (check both location field AND title)
def _is_nl_job(j):
    text = ((j.get('location') or '') + ' ' + j.get('job_title', '')).lower()
    non_nl = ['newcastle', 'london', 'paris', 'berlin', 'warsaw', 'madrid', 'milan',
              'sweden', 'denmark', 'germany', 'france', 'poland', 'united kingdom', 'uk,']
    return not any(k in text for k in non_nl)

jobs = [j for j in jobs if _is_nl_job(j)]

# Deduplicate by (company, job_title) — prefer crawler entry if duplicate exists
seen_keys = set()
deduped = []
# Process crawler jobs first so they take priority in dedup
jobs.sort(key=lambda j: 0 if j.get('source') == 'crawler' else 1)
for j in jobs:
    key = (j['company'].lower().strip(), j['job_title'].lower().strip())
    if key not in seen_keys:
        seen_keys.add(key)
        deduped.append(j)
jobs = deduped

# Inject server-side AI scores from score_cache.json
SCORE_CACHE_FILE = os.path.join(BASE, 'score_cache.json')
if os.path.exists(SCORE_CACHE_FILE):
    try:
        with open(SCORE_CACHE_FILE) as f:
            score_cache = json.load(f)
        injected = 0
        for j in jobs:
            url = j.get('job_url') or j.get('url') or ''
            if url and url in score_cache:
                j['ai_score'] = score_cache[url]
                injected += 1
        print(f"  Injected AI scores for {injected}/{len(jobs)} jobs from score_cache.json")
    except Exception as e:
        print(f"  Warning: could not load score_cache.json: {e}")

jobs_json = json.dumps(jobs, ensure_ascii=False)

# ── Sector lookup ──────────────────────────────────────────────────────────────
SECTOR_MAP = {
    'bcg': 'Consulting', 'bain': 'Consulting', 'mckinsey': 'Consulting',
    'oliver wyman': 'Consulting', 'roland berger': 'Consulting', 'kearney': 'Consulting',
    'strategy&': 'Consulting', 'ey-parthenon': 'Consulting', 'kpmg': 'Consulting',
    'accenture': 'Consulting', 'deloitte': 'Consulting', 'consulting': 'Consulting',
    'shell': 'Energy', 'eneco': 'Energy', 'vattenfall': 'Energy', 'tennet': 'Energy',
    'alliander': 'Energy', 'stedin': 'Energy', 'orsted': 'Energy', 'ørsted': 'Energy',
    'sbm offshore': 'Energy', 'energy': 'Energy', 'neste': 'Energy',
    'adyen': 'Tech', 'databricks': 'Tech', 'catawiki': 'Tech', 'booking': 'Tech',
    'tomtom': 'Tech', 'uber': 'Tech', 'bol.com': 'Tech', 'backbase': 'Tech',
    'prosus': 'Tech', 'imec': 'Tech',
    'unilever': 'FMCG', 'heineken': 'FMCG', 'akzonobel': 'FMCG',
    'dsm': 'FMCG', 'firmenich': 'FMCG', 'frieslandcampina': 'FMCG',
    'ahold': 'FMCG', 'bol ': 'FMCG',
    'ing ': 'Finance', 'rabobank': 'Finance', 'abn amro': 'Finance',
    'nn group': 'Finance', 'aegon': 'Finance', 'van lanschot': 'Finance',
    'asml': 'Industrial', 'nxp': 'Industrial', 'signify': 'Industrial',
    'philips': 'Industrial', 'wolters kluwer': 'Industrial', 'vanderlande': 'Industrial',
    'tno': 'Industrial', 'fugro': 'Industrial',
}

def get_sector(company):
    cl = company.lower()
    for key, sec in SECTOR_MAP.items():
        if key in cl:
            return sec
    return 'Other'

# ── Dashboard HTML ─────────────────────────────────────────────────────────────
DASH_HTML = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>NL Intern Job Search — Vanshika Rana</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg: #0f1117; --surface: #1a1d27; --surface2: #242736; --border: #2e3348;
      --accent: #a855f7; --accent2: #7c3aed; --green: #22c55e; --yellow: #f59e0b;
      --red: #ef4444; --text: #e2e8f0; --text-muted: #8892a4; --radius: 10px;
    }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: var(--bg); color: var(--text); min-height: 100vh; }}
    header {{ background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 16px 24px; display: flex; align-items: center; gap: 16px;
      position: sticky; top: 0; z-index: 100; flex-wrap: wrap; }}
    header h1 {{ font-size: 18px; font-weight: 700;
      background: linear-gradient(135deg, var(--accent), #ec4899);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }}
    .hdr-sub {{ font-size: 12px; color: var(--text-muted); margin-left: auto; }}
    .badge {{ font-size: 11px; padding: 2px 8px; border-radius: 20px;
      background: var(--surface2); color: var(--text-muted); border: 1px solid var(--border); }}
    .badge.purple {{ background: rgba(168,85,247,0.1); color: var(--accent); border-color: rgba(168,85,247,0.3); }}

    .controls {{ padding: 16px 24px; display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }}
    input[type="text"], select {{
      background: var(--surface); border: 1px solid var(--border); color: var(--text);
      border-radius: var(--radius); padding: 9px 13px; font-size: 14px; outline: none;
      transition: border-color 0.2s; }}
    input[type="text"]:focus, select:focus {{ border-color: var(--accent); }}
    #search {{ flex: 1; min-width: 220px; font-size: 15px; }}

    .stats-bar {{ padding: 10px 24px; display: flex; gap: 14px; align-items: center;
      font-size: 13px; color: var(--text-muted); border-bottom: 1px solid var(--border); }}
    .stats-bar strong {{ color: var(--text); }}

    .stat-chips {{ display: flex; gap: 8px; flex-wrap: wrap; margin-left: auto; }}
    .stat-chip {{ font-size: 11px; padding: 3px 10px; border-radius: 20px;
      background: var(--surface2); border: 1px solid var(--border); color: var(--text-muted); }}
    .stat-chip.applied {{ background: rgba(34,197,94,0.1); color: var(--green); border-color: rgba(34,197,94,0.3); }}
    .stat-chip.interview {{ background: rgba(59,130,246,0.1); color: #60a5fa; border-color: rgba(59,130,246,0.3); }}
    .stat-chip.offer {{ background: rgba(168,85,247,0.1); color: var(--accent); border-color: rgba(168,85,247,0.3); }}
    .stat-chip.rejected {{ background: rgba(239,68,68,0.1); color: var(--red); border-color: rgba(239,68,68,0.3); }}
    .stat-chip.bookmarked {{ background: rgba(245,158,11,0.1); color: var(--yellow); border-color: rgba(245,158,11,0.3); }}

    #list-container {{ padding: 16px 24px 60px; display: flex; flex-direction: column; gap: 10px; }}

    .job-card {{ background: var(--surface); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 16px 18px; display: flex; gap: 14px;
      align-items: flex-start; transition: border-color 0.2s; }}
    .job-card:hover {{ border-color: var(--accent); }}
    .job-card.status-applied {{ border-left: 3px solid var(--green); }}
    .job-card.status-interview {{ border-left: 3px solid #60a5fa; }}
    .job-card.status-offer {{ border-left: 3px solid var(--accent); }}
    .job-card.status-rejected {{ border-left: 3px solid var(--red); opacity: 0.6; }}
    .job-card.status-bookmarked {{ border-left: 3px solid var(--yellow); }}

    .job-card-main {{ flex: 1; min-width: 0; }}
    .job-top {{ display: flex; align-items: baseline; gap: 8px; margin-bottom: 3px; flex-wrap: wrap; }}
    .job-company {{ font-size: 11px; color: var(--accent); font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }}
    .job-sector {{ font-size: 10px; color: var(--text-muted); background: var(--surface2);
      border: 1px solid var(--border); border-radius: 10px; padding: 1px 7px; }}
    .source-badge {{ font-size: 10px; border-radius: 10px; padding: 1px 7px; font-weight: 600; }}
    .source-badge.linkedin  {{ background: rgba(0,119,181,0.15); color: #4da6d6; border: 1px solid rgba(0,119,181,0.3); }}
    .source-badge.indeed    {{ background: rgba(34,89,220,0.12); color: #7098e8; border: 1px solid rgba(34,89,220,0.3); }}
    .source-badge.glassdoor {{ background: rgba(12,170,65,0.12); color: #4ecb7a; border: 1px solid rgba(12,170,65,0.3); }}
    .source-badge.crawler   {{ background: var(--surface2); color: var(--text-muted); border: 1px solid var(--border); }}
    .job-title {{ font-size: 15px; font-weight: 700; color: var(--text); margin-bottom: 5px; line-height: 1.3; }}
    .job-meta {{ font-size: 12px; color: var(--text-muted); display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }}
    .job-meta span::before {{ content: '· '; }}
    .job-meta span:first-child::before {{ content: ''; }}
    .job-notes {{ font-size: 12px; color: var(--text-muted); line-height: 1.5; margin-top: 4px; }}

    .job-actions {{ display: flex; flex-direction: column; gap: 6px; align-items: flex-end; min-width: 90px; flex-shrink: 0; }}
    .job-link {{ font-size: 12px; padding: 6px 12px; border-radius: 6px; background: var(--accent);
      color: #fff; text-decoration: none; font-weight: 600; white-space: nowrap; text-align: center; }}
    .job-link.secondary {{ background: var(--surface2); color: var(--text-muted);
      border: 1px solid var(--border); }}
    .job-link:hover {{ opacity: 0.85; }}
    .status-select {{ font-size: 11px; padding: 4px 8px; border-radius: 6px;
      border: 1px solid var(--border); background: var(--surface2); color: var(--text); cursor: pointer; width: 100%; }}

    .match-badge {{ font-size: 11px; font-weight: 700; border-radius: 10px; padding: 2px 8px; white-space: nowrap; }}
    .match-ex  {{ background: rgba(34,197,94,0.15);  color: #22c55e; border: 1px solid rgba(34,197,94,0.35); }}
    .match-str {{ background: rgba(132,204,22,0.15); color: #a3e635; border: 1px solid rgba(132,204,22,0.35); }}
    .match-gd  {{ background: rgba(234,179,8,0.15);  color: #eab308; border: 1px solid rgba(234,179,8,0.35); }}
    .match-ok  {{ background: rgba(249,115,22,0.15); color: #f97316; border: 1px solid rgba(249,115,22,0.35); }}
    .match-low {{ background: var(--surface2); color: var(--text-muted); border: 1px solid var(--border); }}

    #no-results {{ text-align: center; padding: 60px 20px; color: var(--text-muted); display: none; }}
    #no-results h3 {{ font-size: 18px; margin-bottom: 8px; }}
  </style>
</head>
<body>

<header>
  <h1>NL Intern Job Search</h1>
  <span class="badge purple" id="total-badge">– roles</span>
  <span class="hdr-sub">Vanshika Rana · MBA Internship Tracker · May 2026</span>
</header>

<div class="controls">
  <input type="text" id="search" placeholder="Search by company, title, or keyword..." autocomplete="off" oninput="applyFilters()" />
  <select id="sector-filter" onchange="applyFilters()">
    <option value="">All Sectors</option>
    <option value="Consulting">Consulting</option>
    <option value="Energy">Energy</option>
    <option value="Tech">Tech</option>
    <option value="FMCG">FMCG</option>
    <option value="Finance">Finance</option>
    <option value="Industrial">Industrial</option>
    <option value="Other">Other</option>
  </select>
  <select id="status-filter" onchange="applyFilters()">
    <option value="unreviewed">Not Reviewed</option>
    <option value="">All Statuses</option>
    <option value="bookmarked">Bookmarked</option>
    <option value="applied">Applied</option>
    <option value="interview">Interview</option>
    <option value="offer">Offer</option>
    <option value="rejected">Rejected</option>
  </select>
  <select id="source-filter" onchange="applyFilters()">
    <option value="">All Sources</option>
    <option value="crawler">Company Portals</option>
    <option value="linkedin">LinkedIn</option>
    <option value="indeed">Indeed</option>
    <option value="glassdoor">Glassdoor</option>
  </select>
  <select id="sort-by" onchange="applyFilters()">
    <option value="score">Sort: Best Match</option>
    <option value="date">Sort: Newest</option>
    <option value="company">Sort: Company A–Z</option>
  </select>
  <select id="date-filter" onchange="applyFilters()">
    <option value="1">Last 24 hours</option>
    <option value="2">Last 2 days</option>
    <option value="7" selected>Last 7 days</option>
    <option value="14">Last 14 days</option>
    <option value="30">Last 30 days</option>
    <option value="">All time</option>
  </select>
  <select id="applicant-filter" onchange="applyFilters()" title="LinkedIn applicant count — run --enrich to populate">
    <option value="">Any applicants</option>
    <option value="10">Under 10 applicants</option>
    <option value="25">Under 25 applicants</option>
    <option value="50">Under 50 applicants</option>
  </select>
  <button id="ai-score-btn" onclick="aiScoreAll()" style="padding:9px 14px;border-radius:var(--radius);border:1px solid rgba(168,85,247,0.4);background:rgba(168,85,247,0.1);color:var(--accent);cursor:pointer;font-size:13px;font-weight:600;white-space:nowrap" title="Use Claude AI to score all roles against Vanshika's profile">✨ Score with AI</button>
</div>

<div class="stats-bar">
  <span>Showing <strong id="shown-count">–</strong> of <strong id="total-count">–</strong> roles</span>
  <div class="stat-chips">
    <span class="stat-chip bookmarked" id="chip-bookmarked">0 Bookmarked</span>
    <span class="stat-chip applied" id="chip-applied">0 Applied</span>
    <span class="stat-chip interview" id="chip-interview">0 Interview</span>
    <span class="stat-chip offer" id="chip-offer">0 Offer</span>
    <span class="stat-chip rejected" id="chip-rejected">0 Rejected</span>
  </div>
</div>

<div id="list-container"></div>
<div id="no-results"><h3>No roles found</h3><p>Try adjusting your filters.</p></div>

<script>
const JOBS_DATA = {jobs_json};

const SECTOR_MAP = {{
  'bcg': 'Consulting', 'bain': 'Consulting', 'mckinsey': 'Consulting',
  'oliver wyman': 'Consulting', 'roland berger': 'Consulting', 'kearney': 'Consulting',
  'strategy&': 'Consulting', 'ey-parthenon': 'Consulting', 'kpmg': 'Consulting',
  'accenture': 'Consulting', 'deloitte': 'Consulting', 'consulting': 'Consulting',
  'shell': 'Energy', 'eneco': 'Energy', 'vattenfall': 'Energy', 'tennet': 'Energy',
  'alliander': 'Energy', 'stedin': 'Energy', 'orsted': 'Energy', 'ørsted': 'Energy',
  'sbm offshore': 'Energy', 'neste': 'Energy', 'fugro': 'Energy',
  'adyen': 'Tech', 'databricks': 'Tech', 'catawiki': 'Tech', 'booking': 'Tech',
  'tomtom': 'Tech', 'uber': 'Tech', 'backbase': 'Tech', 'prosus': 'Tech',
  'unilever': 'FMCG', 'heineken': 'FMCG', 'akzonobel': 'FMCG', 'dsm': 'FMCG',
  'firmenich': 'FMCG', 'frieslandcampina': 'FMCG', 'ahold': 'FMCG',
  'ing ': 'Finance', 'rabobank': 'Finance', 'abn amro': 'Finance',
  'nn group': 'Finance', 'aegon': 'Finance', 'van lanschot': 'Finance',
  'asml': 'Industrial', 'nxp': 'Industrial', 'signify': 'Industrial',
  'philips': 'Industrial', 'wolters kluwer': 'Industrial', 'vanderlande': 'Industrial',
  'tno': 'Industrial',
}};

function getSector(company) {{
  const cl = company.toLowerCase();
  for (const [key, sec] of Object.entries(SECTOR_MAP)) {{
    if (cl.includes(key)) return sec;
  }}
  return 'Other';
}}

// ── Match scoring ─────────────────────────────────────────────────────────────
function scoreJob(j) {{
  const title = (j.job_title || '').toLowerCase();
  const company = (j.company || '').toLowerCase();
  const sector = getSector(j.company);
  const desc = (j.description || '').toLowerCase().slice(0, 800);
  let score = 0;

  // Role type (0–50)
  const ROLES = [
    [50, ['strategy intern','corporate strategy intern','strategic planning intern']],
    [48, ['consultant intern','consulting intern','associate consultant intern','management consultant intern']],
    [46, ['operations intern','business operations intern','strategy & operations intern','strategy and operations intern','commercial intern']],
    [45, ['product manager intern','product management intern','associate product manager intern','apm intern']],
    [42, ['business analyst intern','business analyst intern','commercial analyst intern']],
    [38, ['mba intern','graduate intern','management trainee intern']],
    [35, ['programme manager intern','program manager intern','project management intern']],
    [30, ['procurement intern','supply chain intern','sourcing intern']],
    [25, ['project manager intern']],
  ];
  for (const [pts, kws] of ROLES) {{
    if (kws.some(k => title.includes(k))) {{ score += pts; break; }}
  }}
  if (score === 0) score += 15;

  // Sector fit (0–30)
  score += ({{ Energy:30, Consulting:28, Tech:22, Industrial:20, Finance:16, FMCG:15, Other:8 }})[sector] || 8;

  // Company bonus (0–20)
  const CO = [
    [20, ['shell','eneco','vattenfall','tennet','alliander','stedin','orsted','ørsted','sbm offshore','neste','fugro']],
    [18, ['bcg','bain','mckinsey','oliver wyman','kearney','strategy&','roland berger','ey-parthenon','deloitte','accenture']],
    [15, ['adyen','booking','uber','databricks','prosus','backbase','catawiki','tomtom']],
    [12, ['unilever','heineken','akzonobel','dsm','firmenich','ahold','frieslandcampina']],
    [12, ['asml','nxp','signify','philips','wolters kluwer','vanderlande','tno']],
    [10, ['ing ','rabobank','abn amro','nn group','aegon','van lanschot']],
  ];
  for (const [pts, kws] of CO) {{
    if (kws.some(k => company.includes(k))) {{ score += pts; break; }}
  }}

  // Keyword signals in title/desc (+0–10)
  let bonus = 0;
  for (const [kw, pts] of [['strategy',3],['operations',3],['sustainability',2],['energy',2],
      ['product',2],['mba',2],['transformation',2],['commercial',1],['analytics',1],['innovation',1]]) {{
    if (title.includes(kw)) bonus += pts;
    else if (desc.includes(kw)) bonus += Math.round(pts / 2);
  }}
  score += Math.min(10, bonus);
  return Math.min(100, score);
}}

// v2: stores {{s, r}} objects — bump key if schema changes again
const AI_SCORES_KEY = 'vanshika_ai_scores_v2';
function getAiScores() {{ try {{ return JSON.parse(localStorage.getItem(AI_SCORES_KEY) || '{{}}'); }} catch {{ return {{}}; }} }}
function saveAiScores(obj) {{ try {{ localStorage.setItem(AI_SCORES_KEY, JSON.stringify(obj)); }} catch {{}} }}

function getEffectiveScore(j) {{
  // Priority: localStorage (user-triggered) > j.ai_score (server-side) > JS rules
  const entry = getAiScores()[jobKey(j)];
  if (entry) return {{ score: entry.s, reason: entry.r || '', ai: true }};
  if (j.ai_score !== undefined) return {{ score: j.ai_score, reason: '', ai: true }};
  return {{ score: scoreJob(j), reason: '', ai: false }};
}}

function matchBadgeHtml(score, isAi, reason) {{
  let cls;
  if (score >= 85) cls = 'ex';
  else if (score >= 70) cls = 'str';
  else if (score >= 55) cls = 'gd';
  else if (score >= 40) cls = 'ok';
  else cls = 'low';
  const sup    = isAi ? '<sup style="font-size:8px;margin-left:1px">AI</sup>' : '';
  const tipTxt = isAi && reason ? reason : (isAi ? 'AI-scored by Claude' : 'Keyword match score');
  return `<span class="match-badge match-${{cls}}" title="${{esc(tipTxt)}}">${{score}}%${{sup}}</span>`;
}}

// ── Full candidate profile (system prompt — prompt-cached across batches) ─────
const AI_SYSTEM_PROMPT = `You score internship job-fit for Vanshika Rana. Reply ONLY with valid JSON — no preamble, no markdown, no other text.

=== CANDIDATE: VANSHIKA RANA ===
Current status: MBA student at Rotterdam School of Management (RSM), Erasmus University, Rotterdam, Netherlands (2024–2026).
Specialisation: Strategy & Leadership.
Location: Rotterdam — zero relocation needed for any NL role.
Work authorisation: Fully authorised to work in Netherlands during studies and internship. No visa complexity.
Language: Fluent English. Dutch at A1 level (beginner) — cannot do Dutch-only roles yet.

EDUCATION
• RSM MBA, Rotterdam (2024–2026) — Strategy & Leadership
  - RSM Business Games 2025: WINNER out of 200+ teams from top European business schools
  - Markstrat Business Simulation: 1st place (competitive pricing, product strategy, market positioning exercise)
  - President, RSM Product Club: 100+ members, ran workshops, company visits, PM case prep
• IIT Bombay — B.Tech Engineering Physics (2015–2019)
  - India's most selective technical university (comparable to MIT/ETH for selectivity)
  - Strong quantitative, analytical, and problem-solving foundation

WORK EXPERIENCE
Oil & Natural Gas Corporation (ONGC) — Deputy Manager, Production Operations (2019–2024, 5 years)
  - Led production operations for large-scale upstream oil & gas assets in India
  - Cross-functional leadership: coordinated engineering, finance, and regulatory teams
  - Drove process improvement and operational excellence across multiple assets
  - Deep energy sector knowledge: upstream O&G, asset lifecycle, energy transition context
  - Managed complex stakeholder relationships at senior levels

STRENGTHS & DIFFERENTIATORS
1. Rare profile: hard science (IIT Bombay) + 5yr industrial leadership (ONGC) + top MBA (RSM)
2. Energy sector insider: direct advantage at Shell, Eneco, Vattenfall, Alliander, SBM, Orsted, Stedin
3. Proven under pressure: Business Games winner = cross-functional execution in competitive setting
4. Quantitative strategy: Markstrat 1st place = pricing/positioning/market share decisions, not just conceptual
5. Community leadership: Product Club president = drove structured learning for 100+ members
6. Rotterdam-based: available immediately, no logistics friction

TARGET ROLES (in priority order)
1. Strategy intern / Corporate strategy intern
2. Consultant intern / Associate consultant intern
3. Operations intern / Business operations intern / Biz ops
4. Product Manager intern / Associate PM intern
5. Business analyst intern / Commercial analyst intern
6. Management trainee / Graduate programme (strategy/ops track)
7. Program/project manager intern (ops-heavy)

CV VARIANTS
- CV_Uber (ops/strategy/energy/consulting focus): best for ONGC-adjacent and strategy roles
- CV_Product (PM/product/analytics focus): best for product, tech, and data-adjacent roles

=== SCORING RUBRIC ===
90–100  Perfect fit: role + sector + company all align with top strengths (e.g. Shell strategy intern, BCG consultant intern, Adyen PM intern)
75–89   Strong fit: 2 of 3 dimensions align well; clear mutual value
60–74   Good fit: relevant role or sector, some match gaps
45–59   Decent fit: worth applying but not a standout match
30–44   Weak fit: significant skill/interest mismatch
0–29    Poor fit: wrong language, wrong seniority, or fundamentally misaligned

PENALISE when:
- Job description or title implies Dutch fluency required ("vloeiend Nederlands", "native Dutch")
- Role is primarily coding/software engineering (not strategy or product management)
- Requires 2+ years specific technical or domain experience beyond her profile
- Role is outside Netherlands or requires relocation`;

// ── AI batch scoring ──────────────────────────────────────────────────────────
async function aiScoreAll() {{
  let apiKey = localStorage.getItem('vanshika_claude_api_key');
  if (!apiKey) {{
    const k = prompt('Enter your Claude API key (sk-ant-…) to enable AI scoring:');
    if (!k || !k.trim()) return;
    apiKey = k.trim();
    localStorage.setItem('vanshika_claude_api_key', apiKey);
  }}
  const btn = document.getElementById('ai-score-btn');
  btn.disabled = true; btn.textContent = '⏳ Scoring…';

  const aiScores = getAiScores();
  const unscored = JOBS_DATA.filter(j => !aiScores[jobKey(j)]);
  if (!unscored.length) {{
    btn.disabled = false; btn.textContent = '✨ Re-score with AI';
    applyFilters(); return;
  }}

  const BATCH = 8;   // smaller batches — full descriptions fit comfortably
  let done = 0;
  try {{
    for (let i = 0; i < unscored.length; i += BATCH) {{
      const batch = unscored.slice(i, i + BATCH);

      const jobLines = batch.map((j, idx) => {{
        const sector  = getSector(j.company);
        const desc    = (j.description || '').trim().slice(0, 1200);
        const descTxt = desc ? `\\n   Description: ${{desc}}` : '';
        return `${{idx}}. Company: ${{j.company}} | Role: ${{j.job_title}} | Location: ${{j.location || 'Netherlands'}} | Sector: ${{sector}}${{descTxt}}`;
      }}).join('\\n\\n');

      const userMsg = `Score these ${{batch.length}} roles for Vanshika. Reply ONLY with a JSON array — no other text:\\n[{{"i":0,"s":88,"r":"one sentence why"}}, ...]\\n\\nRoles:\\n${{jobLines}}`;

      const res = await fetch('https://api.anthropic.com/v1/messages', {{
        method: 'POST',
        headers: {{
          'content-type': 'application/json',
          'x-api-key': apiKey,
          'anthropic-version': '2023-06-01',
          'anthropic-beta': 'prompt-caching-2024-07-31',
          'anthropic-dangerous-direct-browser-access': 'true',
        }},
        body: JSON.stringify({{
          model: 'claude-haiku-4-5-20251001',
          max_tokens: 600,
          system: [{{
            type: 'text',
            text: AI_SYSTEM_PROMPT,
            cache_control: {{ type: 'ephemeral' }},
          }}],
          messages: [{{ role: 'user', content: userMsg }}],
        }}),
      }});

      if (!res.ok) {{
        const err = await res.json().catch(() => ({{}}));
        throw new Error(err.error?.message || 'API ' + res.status);
      }}
      const data = await res.json();
      const text = data.content?.[0]?.text || '[]';
      const match = text.match(/\\[[\\s\\S]*\\]/);
      if (match) {{
        const parsed = JSON.parse(match[0]);
        for (const {{i, s, r}} of parsed) {{
          if (batch[i]) {{
            aiScores[jobKey(batch[i])] = {{
              s: Math.min(100, Math.max(0, Math.round(s))),
              r: (r || '').slice(0, 120),
            }};
          }}
        }}
      }}
      done += batch.length;
      btn.textContent = `⏳ ${{done}}/${{unscored.length}}…`;
      saveAiScores(aiScores);
    }}
    btn.textContent = '✅ AI Scored!';
  }} catch(e) {{
    btn.textContent = '⚠ Error — Retry?';
    console.error('AI scoring error:', e);
  }} finally {{
    btn.disabled = false;
    applyFilters();
    setTimeout(() => {{ if (btn.textContent !== '⚠ Error — Retry?') btn.textContent = '✨ Re-score with AI'; }}, 3000);
  }}
}}

// ── Job utils ─────────────────────────────────────────────────────────────────
function jobKey(j) {{ return j.job_url || (j.company + '|' + j.job_title); }}

const LS_KEY = 'vanshika_job_statuses';
let statuses = {{}};
try {{ statuses = JSON.parse(localStorage.getItem(LS_KEY) || '{{}}'); }} catch {{}}

function setStatus(key, val) {{
  if (val) statuses[key] = val;
  else delete statuses[key];
  localStorage.setItem(LS_KEY, JSON.stringify(statuses));
  applyFilters();
}}

function esc(s) {{
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}}

function formatDate(raw) {{
  if (!raw) return null;
  try {{
    const d = new Date(raw);
    if (isNaN(d)) return null;
    const diff = Math.round((Date.now() - d) / 86400000);
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    if (diff <= 6) return diff + 'd ago';
    if (diff <= 30) return Math.round(diff/7) + 'w ago';
    return d.toLocaleDateString('en-GB', {{day:'numeric',month:'short',year:'numeric'}});
  }} catch {{ return null; }}
}}

function statusLabel(s) {{
  return {{applied:'✅ Applied', interview:'🎯 Interview', offer:'🎉 Offer',
    rejected:'❌ Rejected', bookmarked:'🔖 Bookmarked'}}[s] || '';
}}

// ── Filters + sort ────────────────────────────────────────────────────────────
function applyFilters() {{
  const search  = document.getElementById('search').value.toLowerCase();
  const sector  = document.getElementById('sector-filter').value;
  const statusF = document.getElementById('status-filter').value;
  const sourceF    = document.getElementById('source-filter').value;
  const sortBy     = document.getElementById('sort-by').value;
  const maxDays    = parseInt(document.getElementById('date-filter').value) || 0;
  const maxApplied = parseInt(document.getElementById('applicant-filter').value) || 0;
  const now        = Date.now();

  let filtered = JOBS_DATA.filter(j => {{
    const key = jobKey(j);
    const jobStatus = statuses[key] || '';
    if (statusF === 'unreviewed' && jobStatus) return false;
    if (statusF && statusF !== 'unreviewed' && jobStatus !== statusF) return false;
    if (sector && getSector(j.company) !== sector) return false;
    if (sourceF && (j.source || 'crawler') !== sourceF) return false;
    if (maxDays && j.date_posted) {{
      const d = new Date(j.date_posted);
      if (!isNaN(d) && (now - d.getTime()) > maxDays * 86400000) return false;
    }}
    if (maxApplied) {{
      const count = j.applicant_count;
      if (count == null || count >= maxApplied) return false;
    }}
    if (search) {{
      const hay = (j.company + ' ' + j.job_title + ' ' + (j.location||'') + ' ' + (j.notes||'')).toLowerCase();
      if (!hay.includes(search)) return false;
    }}
    return true;
  }});

  // Sort
  if (sortBy === 'score') {{
    filtered.sort((a, b) => getEffectiveScore(b).score - getEffectiveScore(a).score);
  }} else if (sortBy === 'date') {{
    filtered.sort((a, b) => {{
      const da = a.date_posted ? new Date(a.date_posted).getTime() : 0;
      const db = b.date_posted ? new Date(b.date_posted).getTime() : 0;
      return db - da;
    }});
  }} else if (sortBy === 'company') {{
    filtered.sort((a, b) => (a.company || '').localeCompare(b.company || ''));
  }}

  renderJobs(filtered);
  updateStats();
}}

function renderJobs(list) {{
  const container = document.getElementById('list-container');
  const noRes = document.getElementById('no-results');
  if (!list.length) {{
    container.innerHTML = '';
    noRes.style.display = 'block';
    document.getElementById('shown-count').textContent = '0';
    return;
  }}
  noRes.style.display = 'none';
  document.getElementById('shown-count').textContent = list.length;

  container.innerHTML = list.map(j => {{
    const key = jobKey(j);
    const status = statuses[key] || '';
    const sector = getSector(j.company);
    const dateStr = formatDate(j.date_posted);
    const notes = (j.notes || '').replace(/\\[Direct link.*?\\]/g,'').trim();
    const src = j.source || 'crawler';
    const srcLabel = {{linkedin:'LinkedIn', indeed:'Indeed', glassdoor:'Glassdoor', crawler:'Portal'}}[src] || src;
    const {{ score, reason, ai }} = getEffectiveScore(j);
    const applicants = j.applicant_count != null
      ? `<span style="color:${{j.applicant_count < 10 ? 'var(--green)' : j.applicant_count < 25 ? 'var(--yellow)' : 'var(--text-muted)'}}">${{j.applicant_count}} applicants</span>`
      : '';
    // Recruiter: show email if known, else show "Find" search link for LinkedIn/Indeed jobs
    const recruiterHtml = j.recruiter_email
      ? `<a class="job-link secondary" href="mailto:${{esc(j.recruiter_email)}}" title="Email recruiter">${{esc(j.recruiter_email.split('@')[0])}}</a>`
      : (src !== 'crawler'
          ? `<a class="job-link secondary" href="https://www.linkedin.com/search/results/people/?keywords=${{encodeURIComponent((j.company||'') + ' recruiter netherlands')}}" target="_blank" rel="noopener" title="Find recruiter on LinkedIn">Find Recruiter</a>`
          : '');
    return `<div class="job-card status-${{status || 'none'}}">
      <div class="job-card-main">
        <div class="job-top">
          <span class="job-company">${{esc(j.company)}}</span>
          <span class="job-sector">${{esc(sector)}}</span>
          <span class="source-badge ${{esc(src)}}">${{esc(srcLabel)}}</span>
          ${{matchBadgeHtml(score, ai, reason)}}
        </div>
        <div class="job-title">${{esc(j.job_title)}}</div>
        <div class="job-meta">
          ${{j.location ? '<span>' + esc(j.location) + '</span>' : ''}}
          ${{dateStr ? '<span>' + esc(dateStr) + '</span>' : ''}}
          ${{applicants}}
          ${{status ? '<span style="color:var(--accent)">' + statusLabel(status) + '</span>' : ''}}
        </div>
        ${{notes ? '<div class="job-notes">' + esc(notes) + '</div>' : ''}}
      </div>
      <div class="job-actions">
        <a class="job-link" href="${{esc(j.job_url || j.careers_url)}}" target="_blank" rel="noopener">Apply →</a>
        ${{recruiterHtml}}
        ${{j.careers_url && j.job_url && !j.recruiter_email ? '<a class="job-link secondary" href="' + esc(j.careers_url) + '" target="_blank" rel="noopener">Careers</a>' : ''}}
        <select class="status-select" data-key="${{esc(key)}}" onchange="handleStatusChange(this)" title="Track status">
          <option value="" ${{!status?'selected':''}}>Track…</option>
          <option value="bookmarked" ${{status==='bookmarked'?'selected':''}}>🔖 Bookmark</option>
          <option value="applied" ${{status==='applied'?'selected':''}}>✅ Applied</option>
          <option value="interview" ${{status==='interview'?'selected':''}}>🎯 Interview</option>
          <option value="offer" ${{status==='offer'?'selected':''}}>🎉 Offer</option>
          <option value="rejected" ${{status==='rejected'?'selected':''}}>❌ Rejected</option>
        </select>
      </div>
    </div>`;
  }}).join('');
}}

function handleStatusChange(sel) {{
  const key = sel.getAttribute('data-key');
  setStatus(key, sel.value);
}}

function updateStats() {{
  const counts = {{bookmarked:0, applied:0, interview:0, offer:0, rejected:0}};
  for (const v of Object.values(statuses)) if (counts[v] !== undefined) counts[v]++;
  document.getElementById('chip-bookmarked').textContent = counts.bookmarked + ' Bookmarked';
  document.getElementById('chip-applied').textContent = counts.applied + ' Applied';
  document.getElementById('chip-interview').textContent = counts.interview + ' Interview';
  document.getElementById('chip-offer').textContent = counts.offer + ' Offer';
  document.getElementById('chip-rejected').textContent = counts.rejected + ' Rejected';
  document.getElementById('total-count').textContent = JOBS_DATA.length;
  document.getElementById('total-badge').textContent = JOBS_DATA.length + ' roles';
}}

applyFilters();
</script>
</body>
</html>"""

with open(DASH_OUT, 'w') as f:
    f.write(DASH_HTML)
print(f'✅ Saved dashboard: {DASH_OUT}')

# ── autoapply.html ─────────────────────────────────────────────────────────────
AUTO_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Vanshika — NL Intern Apply Assistant</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #0f1117; --surface: #1a1d27; --surface2: #242736; --border: #2e3348;
      --accent: #a855f7; --accent2: #7c3aed; --green: #22c55e; --yellow: #f59e0b;
      --red: #ef4444; --text: #e2e8f0; --text-muted: #8892a4; --radius: 10px;
    }
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: var(--bg); color: var(--text); min-height: 100vh; display: flex; flex-direction: column; }

    header { background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 14px 20px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    header h1 { font-size: 17px; font-weight: 700;
      background: linear-gradient(135deg, var(--accent), #ec4899);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
    .back-btn { background: var(--surface2); border: 1px solid var(--border); color: var(--text);
      padding: 6px 12px; border-radius: 8px; cursor: pointer; font-size: 13px;
      text-decoration: none; white-space: nowrap; }
    .back-btn:hover { border-color: var(--accent); }
    .hdr-right { margin-left: auto; display: flex; gap: 8px; flex-wrap: wrap; }

    .main-layout { display: flex; flex: 1; min-height: 0; }

    /* LEFT PANEL */
    .left-panel { width: 340px; min-width: 280px; border-right: 1px solid var(--border);
      padding: 20px; display: flex; flex-direction: column; gap: 14px; overflow-y: auto; }
    .panel-title { font-size: 13px; font-weight: 600; color: var(--text-muted);
      text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 4px; }

    .job-info-box { background: var(--surface2); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 14px; }
    .job-info-company { font-size: 11px; color: var(--accent); font-weight: 700;
      text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; }
    .job-info-title { font-size: 15px; font-weight: 700; color: var(--text); margin-bottom: 6px; }
    .job-info-meta { font-size: 12px; color: var(--text-muted); }

    .jd-area { width: 100%; background: var(--surface); border: 1px solid var(--border);
      color: var(--text); border-radius: var(--radius); padding: 10px 12px;
      font-size: 13px; resize: vertical; min-height: 140px; outline: none; font-family: inherit; }
    .jd-area:focus { border-color: var(--accent); }

    .url-row { display: flex; gap: 8px; }
    .url-input { flex: 1; background: var(--surface); border: 1px solid var(--border);
      color: var(--text); border-radius: var(--radius); padding: 8px 12px; font-size: 13px; outline: none; }
    .url-input:focus { border-color: var(--accent); }

    .btn { padding: 8px 16px; border-radius: 8px; border: none; cursor: pointer;
      font-size: 13px; font-weight: 600; transition: all 0.15s; white-space: nowrap; }
    .btn-primary { background: var(--accent); color: #fff; }
    .btn-primary:hover { opacity: 0.88; }
    .btn-secondary { background: var(--surface2); border: 1px solid var(--border); color: var(--text); }
    .btn-secondary:hover { border-color: var(--accent); }

    .cv-status { font-size: 12px; color: var(--text-muted); }
    .cv-pill { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px;
      font-weight: 600; margin: 2px; }

    /* RIGHT PANEL */
    .right-panel { flex: 1; display: flex; flex-direction: column; min-width: 0; overflow-y: auto; }

    .tab-bar { display: flex; gap: 4px; padding: 12px 20px 0; border-bottom: 1px solid var(--border); background: var(--surface); }
    .tab-btn { padding: 8px 16px; border-radius: 8px 8px 0 0; border: 1px solid transparent;
      cursor: pointer; font-size: 13px; font-weight: 500; color: var(--text-muted); background: none;
      transition: all 0.15s; display: flex; align-items: center; gap: 6px; }
    .tab-btn:hover { color: var(--text); }
    .tab-btn.active { background: var(--surface2); border-color: var(--border); border-bottom-color: var(--surface2);
      color: var(--text); }
    .tab-status { font-size: 10px; padding: 1px 5px; border-radius: 8px; }
    .tab-status.loading { background: rgba(245,158,11,0.2); color: var(--yellow); }
    .tab-status.done { background: rgba(34,197,94,0.2); color: var(--green); }
    .tab-status.error { background: rgba(239,68,68,0.2); color: var(--red); }
    .tab-status.cached { background: rgba(168,85,247,0.2); color: var(--accent); }

    .tab-content { display: none; flex: 1; padding: 20px; }
    .tab-content.active { display: block; }

    .generate-btn { width: 100%; padding: 12px; border-radius: var(--radius); border: none;
      cursor: pointer; font-size: 14px; font-weight: 700; color: #fff;
      background: linear-gradient(135deg, var(--accent), var(--accent2));
      box-shadow: 0 4px 20px rgba(168,85,247,0.3); transition: all 0.2s; margin-top: 8px; }
    .generate-btn:hover { transform: translateY(-1px); box-shadow: 0 6px 24px rgba(168,85,247,0.4); }
    .generate-btn:disabled { opacity: 0.5; transform: none; cursor: not-allowed; }

    .output-area { background: var(--surface2); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 20px; min-height: 200px;
      font-size: 14px; line-height: 1.7; white-space: pre-wrap; color: var(--text); }
    .output-area.idle { display: flex; align-items: center; justify-content: center;
      color: var(--text-muted); font-size: 13px; font-style: italic; min-height: 160px; }

    .loading-block { display: flex; flex-direction: column; align-items: center; gap: 12px;
      padding: 40px 20px; color: var(--text-muted); }
    .spinner { width: 32px; height: 32px; border: 3px solid var(--border);
      border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }

    .error-block { background: rgba(239,68,68,0.08); border: 1px solid rgba(239,68,68,0.3);
      border-radius: var(--radius); padding: 16px; color: var(--red); font-size: 13px; }
    .retry-btn { margin-top: 10px; padding: 6px 14px; border-radius: 6px; border: 1px solid var(--red);
      background: none; color: var(--red); cursor: pointer; font-size: 12px; }
    .retry-btn:hover { background: rgba(239,68,68,0.1); }

    .cover-actions { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
    .action-btn { padding: 7px 14px; border-radius: 8px; border: 1px solid var(--border);
      background: var(--surface2); color: var(--text); cursor: pointer; font-size: 13px;
      transition: all 0.15s; }
    .action-btn:hover { border-color: var(--accent); color: var(--accent); }
    .action-btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
    .action-btn.primary:hover { opacity: 0.88; }

    .cover-edit-area { width: 100%; background: var(--surface); border: 1px solid var(--border);
      color: var(--text); border-radius: var(--radius); padding: 16px; font-size: 14px;
      line-height: 1.7; resize: vertical; min-height: 300px; outline: none; font-family: inherit; }
    .cover-edit-area:focus { border-color: var(--accent); }

    .rec-card { background: var(--surface2); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 14px; margin-bottom: 14px; }
    .rec-cv-name { font-size: 13px; font-weight: 700; margin-bottom: 4px; }
    .rec-reason { font-size: 13px; color: var(--text-muted); line-height: 1.5; }
    .cv-change-list { list-style: disc; padding-left: 20px; }
    .cv-change-list li { font-size: 13px; margin-bottom: 6px; line-height: 1.5; }

    .qa-block { margin-bottom: 20px; }
    .qa-question { font-size: 13px; font-weight: 700; color: var(--text); margin-bottom: 6px; }
    .qa-answer { font-size: 13px; color: var(--text-muted); line-height: 1.6; white-space: pre-wrap; }

    .email-idle { border: 1px dashed var(--border); border-radius: var(--radius);
      padding: 24px; text-align: center; color: var(--text-muted); font-size: 13px; }
    .email-input-row { display: flex; gap: 8px; margin-bottom: 14px; align-items: center; }
    .email-input { flex: 1; background: var(--surface); border: 1px solid var(--border); color: var(--text);
      border-radius: var(--radius); padding: 9px 12px; font-size: 14px; outline: none; }
    .email-input:focus { border-color: var(--accent); }
    .email-draft-card { background: var(--surface2); border: 1px solid var(--border);
      border-radius: var(--radius); padding: 20px; font-size: 14px; line-height: 1.7;
      white-space: pre-wrap; color: var(--text); }
    .email-subject { font-size: 13px; color: var(--text-muted); margin-bottom: 14px;
      padding-bottom: 12px; border-bottom: 1px solid var(--border); font-weight: 600; }

    .ready-state { text-align: center; padding: 40px 20px; color: var(--text-muted); }
    .ready-state h3 { font-size: 16px; color: var(--text); margin-bottom: 8px; }
    .ready-state p { font-size: 13px; line-height: 1.6; }
  </style>
</head>
<body>

<header>
  <h1>Vanshika — NL Intern Apply Assistant</h1>
  <a class="back-btn" href="job-search.html">← Dashboard</a>
  <div class="hdr-right">
    <button class="back-btn" id="api-key-btn" onclick="promptApiKey()" style="margin-left:0">🔑 API Key</button>
    <button class="back-btn" id="resume-btn" onclick="loadResumePDFs()">📄 Load CVs</button>
  </div>
</header>

<div class="main-layout">
  <!-- LEFT PANEL -->
  <div class="left-panel">
    <div>
      <div class="panel-title">Current Role</div>
      <div class="job-info-box" id="job-info-box">
        <div style="font-size:13px;color:var(--text-muted);font-style:italic">No role loaded — paste a JD or use the dashboard to select one.</div>
      </div>
    </div>

    <div>
      <div class="panel-title">Job Description</div>
      <textarea class="jd-area" id="jd-input" placeholder="Paste the full job description here… or fetch it from a URL below." oninput="onJdChange()"></textarea>
    </div>

    <div>
      <div class="panel-title">Fetch from URL</div>
      <div class="url-row">
        <input type="text" class="url-input" id="url-input" placeholder="https://careers.company.com/..." />
        <button class="btn btn-secondary" onclick="fetchJD()">Fetch</button>
      </div>
    </div>

    <div>
      <div class="panel-title" style="margin-bottom:8px">CVs Loaded</div>
      <div class="cv-status" id="cv-status">No CVs loaded. Click 📄 Load CVs to select your PDF files.</div>
    </div>

    <button class="generate-btn" id="generate-btn" onclick="generateAll()" disabled>✨ Generate All</button>
  </div>

  <!-- RIGHT PANEL -->
  <div class="right-panel">
    <div class="tab-bar">
      <button class="tab-btn active" onclick="switchTab('cover')" id="tab-cover">Cover Letter <span class="tab-status" id="ts-cover"></span></button>
      <button class="tab-btn" onclick="switchTab('resume')" id="tab-resume">CV Advice <span class="tab-status" id="ts-resume"></span></button>
      <button class="tab-btn" onclick="switchTab('qa')" id="tab-qa">Q&amp;A <span class="tab-status" id="ts-qa"></span></button>
      <button class="tab-btn" onclick="switchTab('email')" id="tab-email">Recruiter Email <span class="tab-status" id="ts-email"></span></button>
    </div>

    <div class="tab-content active" id="content-cover">
      <div id="cover-area" class="output-area idle">Generate a cover letter by clicking ✨ Generate All.</div>
    </div>
    <div class="tab-content" id="content-resume">
      <div id="resume-area" class="output-area idle">CV recommendations will appear here after generation.</div>
    </div>
    <div class="tab-content" id="content-qa">
      <div id="qa-area" class="output-area idle">Application Q&amp;A answers will appear here after generation.</div>
    </div>
    <div class="tab-content" id="content-email">
      <div class="email-input-row">
        <input type="email" class="email-input" id="recruiter-email-input" placeholder="recruiter@company.com" />
        <button class="btn btn-primary" onclick="draftRecruiterEmail()">Draft Email</button>
      </div>
      <div id="email-draft-area" class="email-idle">Enter the recruiter's email and click Draft Email.</div>
    </div>
  </div>
</div>

<script>
// ── Profile ────────────────────────────────────────────────────────────────────
function shortProfile() {
  return `=== CANDIDATE PROFILE: VANSHIKA RANA ===
MBA Student | Rotterdam School of Management, Erasmus University | Rotterdam, Netherlands
Currently in Netherlands on student visa — eligible to work during studies and internship. NL work authorisation confirmed.
IIT Bombay graduate (B.Tech, Engineering Physics, 2019) | ONGC engineer (2019-2024) | Now MBA at RSM (2024-2026).
Do NOT mention visa or work authorisation in cover letters — she already has it. Do NOT mention salary expectations.

=== ACADEMIC BACKGROUND ===
1. Rotterdam School of Management (RSM), Erasmus University — MBA, Rotterdam, NL (2024–2026)
   - Specialisation: Strategy & Leadership
   - RSM Business Games 2025: Winner (out of 200+ teams from leading European b-schools)
   - Markstrat Business Simulation: 1st place (competitive simulation covering pricing, product strategy, market positioning)
   - President, RSM Product Club (100+ members; ran workshops, company visits, PM case prep sessions)

2. IIT Bombay — B.Tech, Engineering Physics (2015–2019)
   - Academic excellence; IIT Bombay is India's most prestigious tech institution (comparable to MIT/ETH for selectivity)
   - Strong analytical, quantitative, and problem-solving foundation

=== WORK EXPERIENCE ===
Oil and Natural Gas Corporation (ONGC) — Deputy Manager, Production Operations (2019–2024, 5 years)
   - Led production operations and strategy for large-scale energy assets in India
   - Cross-functional project leadership: coordinated engineering, finance, and regulatory teams
   - Process improvement initiatives; optimised production efficiency across multiple assets
   - Experience with structured problem solving, operational excellence, and stakeholder management at scale
   - Deep energy sector knowledge: upstream oil & gas, asset lifecycle, energy transition context

=== WHY SHE IS A STRONG CANDIDATE ===
- Unusual combination: IIT Bombay (hard science/engineering) + ONGC (5 yrs ops leadership) + RSM MBA (strategy/leadership)
- Business Games winner proves she can operate under pressure in cross-functional competitive settings
- Markstrat 1st place shows quantitative strategy execution, not just conceptual understanding
- Product Club President = she drove community building and structured learning — leadership beyond academics
- Energy background (ONGC) is a direct differentiator for energy/sustainability roles (Shell, Eneco, Vattenfall, Alliander, SBM)
- RSM Rotterdam location means zero relocation needed for NL internships
- Strong English; working towards Dutch (A1 level currently)

=== CV VARIANTS ===
CV_Uber    -> Operations / general strategy / cross-functional roles / energy / consulting (emphasises ONGC ops leadership)
CV_Product -> Product management / product strategy / product operations / PM intern roles (emphasises RSM Product Club, Markstrat, analytical skills)`;
}

// ── CV Management ──────────────────────────────────────────────────────────────
const RESUME_NAMES = ['CV_Uber', 'CV_Product'];
const RESUME_CACHE_KEY = 'vanshika_resume_texts_v1';
const COLOR_MAP = { CV_Uber: '#5b7fff', CV_Product: '#a855f7' };

function getResumeCacheAll() {
  try { return JSON.parse(localStorage.getItem(RESUME_CACHE_KEY) || '{}'); } catch { return {}; }
}
function getResumeText(name) {
  const c = getResumeCacheAll();
  return c[name] || null;
}
function getLoadedCount() {
  const c = getResumeCacheAll();
  return RESUME_NAMES.filter(n => c[n]).length;
}

async function extractPdfText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async (e) => {
      try {
        const arr = new Uint8Array(e.target.result);
        let text = '';
        // Try pdf.js if available
        if (window.pdfjsLib) {
          const pdf = await pdfjsLib.getDocument({ data: arr }).promise;
          for (let i = 1; i <= pdf.numPages; i++) {
            const page = await pdf.getPage(i);
            const content = await page.getTextContent();
            text += content.items.map(it => it.str).join(' ') + '\\n';
          }
          resolve(text);
        } else {
          // Fallback: decode as text
          const decoded = new TextDecoder('utf-8', { fatal: false }).decode(arr);
          const matches = decoded.match(/BT[\\s\\S]*?ET/g) || [];
          text = matches.join(' ').replace(/[^\\x20-\\x7E\\n]/g, ' ');
          resolve(text || decoded.slice(0, 8000));
        }
      } catch(err) { reject(err); }
    };
    reader.onerror = reject;
    reader.readAsArrayBuffer(file);
  });
}

async function loadResumePDFs() {
  const input = document.createElement('input');
  input.type = 'file'; input.accept = '.pdf'; input.multiple = true;
  input.onchange = async () => {
    if (!input.files.length) return;
    const cache = getResumeCacheAll();
    for (const file of input.files) {
      const rawName = file.name.replace(/\\.pdf$/i, '');
      // Strip VanshikaRana_CV_ prefix → CV_Uber, CV_Product
      const name = rawName.replace(/^VanshikaRana_/i, '');
      if (!RESUME_NAMES.includes(name)) {
        console.warn('Unknown CV file:', rawName, '→', name);
        continue;
      }
      try {
        const text = await extractPdfText(file);
        cache[name] = text;
      } catch(e) { console.error('PDF extract error:', e); }
    }
    localStorage.setItem(RESUME_CACHE_KEY, JSON.stringify(cache));
    updateResumeBtn();
  };
  input.click();
}

function updateResumeBtn() {
  const n = getLoadedCount();
  document.getElementById('resume-btn').textContent = n > 0 ? `📄 CVs (${n}/${RESUME_NAMES.length})` : '📄 Load CVs';
  const cache = getResumeCacheAll();
  const cvStatus = document.getElementById('cv-status');
  if (n === 0) {
    cvStatus.innerHTML = 'No CVs loaded. Click 📄 Load CVs to select your PDF files.';
  } else {
    const pills = RESUME_NAMES.map(n2 => {
      const loaded = !!cache[n2];
      const col = COLOR_MAP[n2] || '#888';
      return `<span class="cv-pill" style="background:${col}22;color:${col};border:1px solid ${col}44">${n2}${loaded?' ✓':' ✗'}</span>`;
    }).join(' ');
    cvStatus.innerHTML = `${pills}`;
  }
}

function getResumeContext(recommendedResume) {
  const text = getResumeText(recommendedResume);
  if (!text) return '';
  return `\\n\\n=== ${recommendedResume} (FULL CV TEXT) ===\\n${text.slice(0, 6000)}`;
}

// ── API Key ────────────────────────────────────────────────────────────────────
function promptApiKey() {
  const existing = localStorage.getItem('vanshika_claude_api_key') || '';
  const k = prompt('Enter your Claude API key (sk-ant-...):', existing);
  if (k === null) return;
  if (!k.trim()) { localStorage.removeItem('vanshika_claude_api_key'); document.getElementById('api-key-btn').textContent = '🔑 API Key'; return; }
  localStorage.setItem('vanshika_claude_api_key', k.trim());
  document.getElementById('api-key-btn').textContent = '🔑 API Key ✓';
  checkReady();
}

function updateApiKeyBtn() {
  const k = localStorage.getItem('vanshika_claude_api_key');
  if (k) document.getElementById('api-key-btn').textContent = '🔑 API Key ✓';
}

// ── Job loading ────────────────────────────────────────────────────────────────
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function loadJob(jobJson) {
  try {
    window._job = typeof jobJson === 'string' ? JSON.parse(jobJson) : jobJson;
    const j = window._job;
    const box = document.getElementById('job-info-box');
    box.innerHTML = `
      <div class="job-info-company">${esc(j.company)}</div>
      <div class="job-info-title">${esc(j.job_title)}</div>
      <div class="job-info-meta">${esc(j.location || '')}${j.date_posted ? ' · ' + j.date_posted : ''}</div>`;
    if (j.description) {
      document.getElementById('jd-input').value = j.description;
    }
    // Try to restore saved recruiter email
    try {
      const saved = localStorage.getItem('vanshika_rec_' + j.company + '_' + j.job_title);
      if (saved) document.getElementById('recruiter-email-input').value = saved;
      else document.getElementById('recruiter-email-input').value = j.recruiter_email || '';
    } catch {}
    checkReady();
  } catch(e) { console.error('loadJob error:', e); }
}

function onJdChange() { checkReady(); }

function checkReady() {
  const hasKey = !!localStorage.getItem('vanshika_claude_api_key');
  const hasJd = !!(document.getElementById('jd-input').value.trim() || (window._job && window._job.job_url));
  document.getElementById('generate-btn').disabled = !(hasKey && hasJd);
}

// ── Claude API ─────────────────────────────────────────────────────────────────
async function callClaude(system, user) {
  const apiKey = localStorage.getItem('vanshika_claude_api_key');
  if (!apiKey) throw new Error('No API key set. Click the 🔑 API Key button.');
  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-api-key': apiKey,
      'anthropic-version': '2023-06-01', 'anthropic-dangerous-direct-browser-access': 'true' },
    body: JSON.stringify({ model: 'claude-opus-4-7', max_tokens: 2048,
      system, messages: [{ role: 'user', content: user }] })
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error?.message || `API error ${res.status}`);
  }
  const data = await res.json();
  return data.content?.[0]?.text || '';
}

// ── Cache helpers ──────────────────────────────────────────────────────────────
function _jdHash() {
  const jd = (document.getElementById('jd-input').value || '').slice(0, 200);
  const j = window._job || {};
  return btoa(encodeURIComponent((j.company || '') + (j.job_title || '') + jd)).slice(0, 32);
}
function _cacheKey(section) { return `vanshika_aa_${section}_${_jdHash()}`; }
function loadCached(section) { try { return localStorage.getItem(_cacheKey(section)) || null; } catch { return null; } }
function saveCache(section, text) { try { localStorage.setItem(_cacheKey(section), text); } catch {} }

// ── Tab management ─────────────────────────────────────────────────────────────
function switchTab(tab) {
  ['cover','resume','qa','email'].forEach(t => {
    document.getElementById('tab-' + t).classList.toggle('active', t === tab);
    document.getElementById('content-' + t).classList.toggle('active', t === tab);
  });
}

function setTabStatus(tab, status) {
  const el = document.getElementById('ts-' + tab);
  el.className = 'tab-status' + (status ? ' ' + status : '');
  el.textContent = { loading: '…', done: '✓', error: '!', cached: '★' }[status] || '';
}

// ── Job context ────────────────────────────────────────────────────────────────
function getJobCtx() {
  const j = window._job || {};
  const jd = (document.getElementById('jd-input').value || '').trim();
  const base = `Company: ${j.company || 'Unknown'}\\nRole: ${j.job_title || 'Unknown'}\\nLocation: ${j.location || 'Netherlands'}`;
  return jd ? base + `\\n\\nFULL JOB DESCRIPTION:\\n${jd}` : base + (j.notes ? `\\nNotes: ${j.notes}` : '');
}

// ── Generate all ───────────────────────────────────────────────────────────────
async function generateAll() {
  const btn = document.getElementById('generate-btn');
  btn.disabled = true; btn.textContent = '⏳ Generating…';
  try {
    await Promise.all([generateCover(), generateResume(), generateQA()]);
  } finally {
    btn.disabled = false; btn.textContent = '✨ Generate All';
    checkReady();
  }
}

// ── Cover Letter ───────────────────────────────────────────────────────────────
async function generateCover() {
  const cached = loadCached('cover');
  if (cached) { renderCover(cached, true); return; }
  document.getElementById('cover-area').innerHTML = '<div class="loading-block"><div class="spinner"></div><div>Writing cover letter…</div></div>';
  setTabStatus('cover', 'loading');
  const j = window._job || {};
  const profile = shortProfile();
  const jdCtx = getJobCtx();
  try {
    const rec = window._recommendedCV || 'CV_Uber';
    const cvCtx = getResumeContext(rec);
    const text = await callClaude(
      'You are an expert cover letter writer for MBA internship applications in the Netherlands. Write in a professional, warm, and confident tone. Never use em-dashes (—). Never use filler phrases like "I am passionate about", "I am reaching out to express my interest", "I hope this finds you well". Be specific, not generic.',
      `Write a cover letter for Vanshika Rana applying for the ${j.job_title || 'internship'} role at ${j.company || 'this company'}.

JOB CONTEXT:
${jdCtx}

CANDIDATE PROFILE:
${profile}${cvCtx}

INSTRUCTIONS:
- Length: 3 short paragraphs, 250-300 words total
- Paragraph 1: Who she is (IIT Bombay + ONGC + RSM MBA) and why this specific role excites her (reference something concrete from the JD)
- Paragraph 2: Her strongest relevant achievement — pick ONE of: ONGC ops leadership, RSM Business Games win, Markstrat 1st place, or Product Club president — whichever matches the role best. Give one concrete detail.
- Paragraph 3: Why this company specifically (something real about their strategy, mission, or market position — not generic praise). Close with enthusiasm for next steps.
- Do NOT mention visa or work permit — she already has it
- Do NOT mention salary
- Sign off: "Warm regards,\\nVanshika Rana"
- No bullet points — flowing prose only
- Format: Start with "Dear Hiring Manager," (or role-specific salutation if clear from JD)`
    );
    saveCache('cover', text);
    renderCover(text);
  } catch(err) {
    document.getElementById('cover-area').innerHTML = `<div class="error-block"><strong>Error:</strong> ${esc(err.message)}<br><button class="retry-btn" onclick="generateCover()">Retry</button></div>`;
    setTabStatus('cover', 'error');
  }
}

function renderCover(text, fromCache = false) {
  window._coverText = text;
  document.getElementById('cover-area').innerHTML = `
    <div class="output-area">${esc(text)}</div>
    <div class="cover-actions">
      <button class="action-btn primary" onclick="copyCover()">Copy</button>
      <button class="action-btn" onclick="editCover()">Edit</button>
      <button class="action-btn" onclick="clearCache('cover');generateCover()" style="margin-left:auto">↺ Refresh</button>
      ${fromCache ? '<span style="font-size:11px;color:var(--text-muted)">★ Cached</span>' : ''}
    </div>`;
  setTabStatus('cover', fromCache ? 'cached' : 'done');
}

function copyCover() {
  navigator.clipboard.writeText(window._coverText || '').then(() => {
    const btn = event.target; btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 2000);
  });
}

function editCover() {
  document.getElementById('cover-area').innerHTML = `
    <textarea class="cover-edit-area" id="cover-edit-ta" spellcheck="true"></textarea>
    <div class="cover-actions">
      <button class="action-btn primary" onclick="saveCoverEdit()">Save</button>
      <button class="action-btn" onclick="renderCover(window._coverText)">Cancel</button>
    </div>`;
  document.getElementById('cover-edit-ta').value = window._coverText || '';
  document.getElementById('cover-edit-ta').focus();
}

function saveCoverEdit() {
  const t = document.getElementById('cover-edit-ta').value;
  saveCache('cover', t);
  renderCover(t);
}

// ── CV Advice ──────────────────────────────────────────────────────────────────
async function generateResume() {
  const cached = loadCached('resume');
  if (cached) { renderResume(cached, true); return; }
  document.getElementById('resume-area').innerHTML = '<div class="loading-block"><div class="spinner"></div><div>Analysing CV fit…</div></div>';
  setTabStatus('resume', 'loading');
  const profile = shortProfile();
  const jdCtx = getJobCtx();
  const j = window._job || {};
  try {
    const raw = await callClaude(
      'You are an expert career advisor for MBA students applying for internships in the Netherlands. Give concrete, actionable advice.',
      `Analyse which CV is best for Vanshika Rana to use for this role, and suggest targeted edits.

JOB:
${jdCtx}

CANDIDATE:
${profile}

CV VARIANTS:
- CV_Uber: Operations / general strategy / cross-functional / energy / consulting emphasis
- CV_Product: Product management / product strategy / PM intern roles

LOADED CV TEXTS:
${RESUME_NAMES.map(n => { const t = getResumeText(n); return t ? `--- ${n} ---\\n${t.slice(0,2000)}` : `--- ${n} --- (not loaded)`; }).join('\\n\\n')}

OUTPUT FORMAT (JSON):
{
  "recommended_cv": "CV_Uber or CV_Product",
  "reason": "2-3 sentence explanation of why this CV fits better",
  "suggested_edits": [
    "Specific bullet edit or addition tailored to this role (3-5 items)"
  ]
}`
    );
    saveCache('resume', raw);
    renderResume(raw);
  } catch(err) {
    document.getElementById('resume-area').innerHTML = `<div class="error-block"><strong>Error:</strong> ${esc(err.message)}<br><button class="retry-btn" onclick="generateResume()">Retry</button></div>`;
    setTabStatus('resume', 'error');
  }
}

function renderResume(raw, fromCache = false) {
  window._resumeRaw = raw;
  let parsed = null;
  try {
    const match = raw.match(/\\{[\\s\\S]*\\}/);
    if (match) parsed = JSON.parse(match[0]);
  } catch {}

  let html = '';
  if (parsed) {
    window._recommendedCV = parsed.recommended_cv;
    const col = COLOR_MAP[parsed.recommended_cv] || '#888';
    html = `<div class="rec-card">
      <div class="rec-cv-name" style="color:${col}">${esc(parsed.recommended_cv)}</div>
      <div class="rec-reason">${esc(parsed.reason)}</div>
    </div>
    <div style="font-size:13px;font-weight:600;margin-bottom:10px;color:var(--text-muted)">SUGGESTED EDITS</div>
    <ul class="cv-change-list">${(parsed.suggested_edits||[]).map(e=>`<li>${esc(e)}</li>`).join('')}</ul>`;
  } else {
    html = `<div class="output-area">${esc(raw)}</div>`;
  }

  document.getElementById('resume-area').innerHTML = html +
    `<div class="cover-actions" style="margin-top:14px">
      <button class="action-btn" onclick="clearCache('resume');generateResume()" style="margin-left:auto">↺ Refresh</button>
      ${fromCache ? '<span style="font-size:11px;color:var(--text-muted)">★ Cached</span>' : ''}
    </div>`;
  setTabStatus('resume', fromCache ? 'cached' : 'done');
}

// ── Q&A ────────────────────────────────────────────────────────────────────────
async function generateQA() {
  const cached = loadCached('qa');
  if (cached) { renderQA(cached, true); return; }
  document.getElementById('qa-area').innerHTML = '<div class="loading-block"><div class="spinner"></div><div>Writing Q&A answers…</div></div>';
  setTabStatus('qa', 'loading');
  const profile = shortProfile();
  const jdCtx = getJobCtx();
  const j = window._job || {};
  try {
    const text = await callClaude(
      'You are an expert at answering MBA internship application questions. Write concise, specific, authentic answers for Vanshika Rana. Never use em-dashes.',
      `Extract application questions from this job description and write ideal answers for Vanshika.

JOB:
${jdCtx}

CANDIDATE:
${profile}

INSTRUCTIONS:
1. If the JD contains explicit application questions (motivation, strengths, experience), answer each one with a short paragraph (100-150 words each).
2. If no explicit questions are found, write answers to these standard MBA internship questions:
   - Why do you want to intern at ${j.company || 'this company'}?
   - Why are you interested in ${(j.job_title || 'this role').replace(/intern/i,'').trim()}?
   - What is your biggest achievement and what did you learn from it?
   - Where do you see yourself in 5 years?
   - Why the Netherlands / why RSM?
3. For NL work authorisation: answer is "I am currently enrolled at RSM Rotterdam and have full authorisation to work in the Netherlands during my studies and internships."
4. Format: Q: [question]\\nA: [answer]\\n\\n for each pair.`
    );
    saveCache('qa', text);
    renderQA(text);
  } catch(err) {
    document.getElementById('qa-area').innerHTML = `<div class="error-block"><strong>Error:</strong> ${esc(err.message)}<br><button class="retry-btn" onclick="generateQA()">Retry</button></div>`;
    setTabStatus('qa', 'error');
  }
}

function renderQA(text, fromCache = false) {
  window._qaText = text;
  const blocks = text.split(/\\n{2,}/);
  let html = blocks.map(block => {
    const qMatch = block.match(/^Q:\\s*(.+)/m);
    const aMatch = block.match(/^A:\\s*([\\s\\S]+)/m);
    if (qMatch && aMatch) {
      return `<div class="qa-block">
        <div class="qa-question">Q: ${esc(qMatch[1].trim())}</div>
        <div class="qa-answer">${esc(aMatch[1].trim())}</div>
      </div>`;
    }
    return block.trim() ? `<div class="qa-block"><div class="qa-answer">${esc(block)}</div></div>` : '';
  }).filter(Boolean).join('');

  document.getElementById('qa-area').innerHTML = html +
    `<div class="cover-actions" style="margin-top:14px">
      <button class="action-btn primary" onclick="copyQA()">Copy All</button>
      <button class="action-btn" onclick="clearCache('qa');generateQA()" style="margin-left:auto">↺ Refresh</button>
      ${fromCache ? '<span style="font-size:11px;color:var(--text-muted)">★ Cached</span>' : ''}
    </div>`;
  setTabStatus('qa', fromCache ? 'cached' : 'done');
}

function copyQA() {
  navigator.clipboard.writeText(window._qaText || '').then(() => {
    const btn = event.target; btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy All', 2000);
  });
}

// ── Recruiter Email ────────────────────────────────────────────────────────────
async function draftRecruiterEmail() {
  const emailVal = document.getElementById('recruiter-email-input').value.trim();
  if (!emailVal) {
    document.getElementById('email-draft-area').innerHTML =
      '<div class="email-idle" style="border-color:var(--red);color:var(--red)">Please enter the recruiter\'s email address first.</div>';
    document.getElementById('recruiter-email-input').focus();
    return;
  }
  const j = window._job || {};
  try { localStorage.setItem('vanshika_rec_' + j.company + '_' + j.job_title, emailVal); } catch {}
  document.getElementById('email-draft-area').innerHTML =
    '<div class="loading-block"><div class="spinner"></div><div>Drafting recruiter email…</div></div>';
  setTabStatus('email', 'loading');
  const profile = shortProfile();
  const jdCtx = getJobCtx();
  const rec = window._recommendedCV || 'CV_Uber';
  const cvCtx = getResumeContext(rec);
  try {
    const text = await callClaude(
      'You are an expert at professional outreach emails for MBA internship applications. Write naturally and concisely. Never use em-dashes. Never use filler openers.',
      `Draft a cold outreach email from Vanshika Rana to a recruiter/hiring manager at ${j.company || 'this company'} for the ${j.job_title || 'internship'} role.

JOB:
${jdCtx}

CANDIDATE:
${profile}${cvCtx}

STRUCTURE (3 short paragraphs, 100-130 words total in body):
Paragraph 1 (2 sentences): Who she is (IIT Bombay + ONGC + RSM MBA) and the specific role she is applying for.
Paragraph 2 (2 sentences): One specific, concrete achievement with a number or result (Business Games win, Markstrat 1st, or ONGC milestone — whichever fits best). One sentence on why ${j.company || 'this company'} specifically (something real, not generic).
Paragraph 3 (2 sentences): She is based in Rotterdam and available immediately for the internship (no relocation needed). Close: CV attached, happy to chat.

FORMAT:
- First line: Subject: [concise subject line]
- Blank line after subject before body
- Blank line between paragraphs
- Sign off: "Best regards," then blank line then "Vanshika Rana" then "linkedin.com/in/vanshika-rana-rsm/" (no email in sign-off)
- Do NOT mention visa / work permit / salary`
    );
    renderEmailDraft(text, emailVal);
  } catch(err) {
    document.getElementById('email-draft-area').innerHTML =
      `<div class="error-block"><strong>Error:</strong> ${esc(err.message)}<br><button class="retry-btn" onclick="draftRecruiterEmail()">Retry</button></div>`;
    setTabStatus('email', 'error');
  }
}

function renderEmailDraft(text, toEmail) {
  text = text.replace(/—/g, ',').replace(/–/g, '-');
  window._emailDraftText = text;
  window._emailDraftTo = toEmail;
  const lines = text.split('\\n');
  const subIdx = lines.findIndex(l => /^subject:/i.test(l.trim()));
  const subject = subIdx !== -1 ? lines[subIdx].replace(/^subject:\\s*/i,'').trim() : '';
  const body = subIdx !== -1 ? lines.slice(subIdx+1).join('\\n').replace(/^\\n+/,'') : text;

  document.getElementById('email-draft-area').innerHTML = `
    <div class="email-draft-card">
      <div class="email-subject">
        <span style="color:var(--text-muted);font-weight:400">To: </span>${esc(toEmail)}<br>
        <span style="color:var(--text-muted);font-weight:400">Subject: </span>${esc(subject)}
      </div>
      <div>${esc(body)}</div>
    </div>
    <div class="cover-actions" style="margin-top:14px">
      <button class="action-btn primary" onclick="copyEmail()">Copy Email</button>
      <button class="action-btn" onclick="openMailto()">Open in Mail</button>
      <button class="action-btn" onclick="editEmail()">Edit</button>
      <button class="action-btn" onclick="draftRecruiterEmail()" style="margin-left:auto">↺ Refresh</button>
    </div>`;
  setTabStatus('email', 'done');
}

function copyEmail() {
  navigator.clipboard.writeText(window._emailDraftText || '').then(() => {
    const btn = event.target; btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy Email', 2000);
  });
}

function openMailto() {
  const t = window._emailDraftText || '';
  const to = window._emailDraftTo || '';
  const lines = t.split('\\n');
  const si = lines.findIndex(l => /^subject:/i.test(l.trim()));
  const subject = si !== -1 ? lines[si].replace(/^subject:\\s*/i,'').trim() : '';
  const body = si !== -1 ? lines.slice(si+1).join('\\n').replace(/^\\n+/,'') : t;
  window.location.href = `mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

function editEmail() {
  document.getElementById('email-draft-area').innerHTML = `
    <textarea class="cover-edit-area" id="email-edit-ta" spellcheck="true"></textarea>
    <div class="cover-actions">
      <button class="action-btn primary" onclick="saveEmailEdit()">Save</button>
      <button class="action-btn" onclick="renderEmailDraft(window._emailDraftText,window._emailDraftTo)">Cancel</button>
    </div>`;
  document.getElementById('email-edit-ta').value = window._emailDraftText || '';
  document.getElementById('email-edit-ta').focus();
}

function saveEmailEdit() {
  renderEmailDraft(document.getElementById('email-edit-ta').value, window._emailDraftTo);
}

// ── Proxy / JD fetch ───────────────────────────────────────────────────────────
async function fetchViaProxy(url) {
  const proxies = [
    async u => { const r = await fetch(`https://api.allorigins.win/get?url=${encodeURIComponent(u)}`, {signal:AbortSignal.timeout(8000)}); if(!r.ok)return null; const j=await r.json(); return j.contents?.length>500?j.contents:null; },
    async u => { const r = await fetch(`https://corsproxy.io/?url=${encodeURIComponent(u)}`, {signal:AbortSignal.timeout(8000)}); if(!r.ok)return null; const t=await r.text(); return t?.length>500?t:null; },
    async u => { const r = await fetch(`https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(u)}`, {signal:AbortSignal.timeout(8000)}); if(!r.ok)return null; const t=await r.text(); return t?.length>500&&!t.toLowerCase().includes('too many')?t:null; },
  ];
  for (const fn of proxies) { try { const h=await fn(url); if(h)return h; } catch {} }
  return null;
}

async function fetchJD() {
  const url = document.getElementById('url-input').value.trim();
  if (!url) return;
  document.getElementById('jd-input').value = 'Fetching…';
  const html = await fetchViaProxy(url);
  if (!html) { document.getElementById('jd-input').value = ''; alert('Could not fetch the page. Try pasting the job description manually.'); return; }
  // Strip tags
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  const text = (tmp.innerText || tmp.textContent || '').replace(/\\s{3,}/g,'\\n\\n').trim();
  document.getElementById('jd-input').value = text.slice(0, 8000);
  checkReady();
}

function clearCache(section) {
  try { localStorage.removeItem(_cacheKey(section)); } catch {}
}

// ── Init ───────────────────────────────────────────────────────────────────────
updateApiKeyBtn();
updateResumeBtn();

// Load job from URL param (vanshika_aa_current_job)
try {
  const stored = localStorage.getItem('vanshika_aa_current_job');
  if (stored) loadJob(stored);
} catch {}
checkReady();
</script>
</body>
</html>"""

with open(AUTO_OUT, 'w') as f:
    f.write(AUTO_HTML)
print(f'✅ Saved autoapply: {AUTO_OUT}')
