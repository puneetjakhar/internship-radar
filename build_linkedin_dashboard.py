#!/usr/bin/env python3
"""Rebuild linkedin.html with scored jobs from linkedin_v2_jobs.json + linkedin_v2_scores.json."""
import json, re, os

BASE        = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE   = os.path.join(BASE, 'linkedin_v2_jobs.json')
SCORES_FILE = os.path.join(BASE, 'linkedin_v2_scores.json')
DASH_FILE   = os.path.join(BASE, 'linkedin.html')

# 1. Load jobs (empty list if file not exists)
jobs = []
if os.path.exists(JOBS_FILE):
    with open(JOBS_FILE) as f:
        jobs = json.load(f)

# 2. Load scores (empty dict if file not exists)
scores = {}
if os.path.exists(SCORES_FILE):
    with open(SCORES_FILE) as f:
        scores = json.load(f)

# 3. Filter: keep only jobs with intern/internship in title
_intern_re = re.compile(r'\bintern(ship)?\b', re.IGNORECASE)
jobs = [j for j in jobs if _intern_re.search(j.get('job_title', ''))]

# 4. Filter non-NL: exclude jobs where location contains non-NL keywords
NON_NL = [
    'newcastle', 'london', 'paris', 'berlin', 'warsaw', 'madrid', 'milan',
    'sweden', 'denmark', 'germany', 'france', 'poland', 'united kingdom', 'uk,',
]

def _is_nl_job(j):
    loc = (j.get('location') or '').lower()
    return not any(k in loc for k in NON_NL)

jobs = [j for j in jobs if _is_nl_job(j)]

# 5. Deduplicate by (company.lower().strip(), job_title.lower().strip()) — keep first occurrence
seen_keys = set()
deduped = []
for j in jobs:
    key = (j.get('company', '').lower().strip(), j.get('job_title', '').lower().strip())
    if key not in seen_keys:
        seen_keys.add(key)
        deduped.append(j)
jobs = deduped

# 6. Inject scores
for job in jobs:
    url = job.get('job_url')
    if url and url in scores:
        s = scores[url]
        job['ai_score']      = s['score']
        job['ai_verdict']    = s['verdict']
        job['ai_reason']     = s['reason']
        job['ai_highlights'] = s.get('highlights', [])
        job['ai_watchouts']  = s.get('watchouts', [])

# 7. Add computed fields
SECTOR_MAP = {
    'shell': 'Energy', 'vattenfall': 'Energy', 'eneco': 'Energy', 'tennet': 'Energy',
    'alliander': 'Energy', 'stedin': 'Energy', 'sbm offshore': 'Energy',
    'ørsted': 'Energy', 'orsted': 'Energy', 'neste': 'Energy',
    'bcg': 'Consulting', 'bain': 'Consulting', 'mckinsey': 'Consulting',
    'oliver wyman': 'Consulting', 'roland berger': 'Consulting', 'kearney': 'Consulting',
    'strategy&': 'Consulting', 'kpmg': 'Consulting', 'accenture': 'Consulting',
    'deloitte': 'Consulting', 'pwc': 'Consulting', 'ey': 'Consulting',
    'adyen': 'Tech', 'booking.com': 'Tech', 'booking': 'Tech', 'tomtom': 'Tech',
    'asml': 'Tech', 'uber': 'Tech', 'catawiki': 'Tech', 'backbase': 'Tech',
    'ing': 'Finance', 'abn amro': 'Finance', 'rabobank': 'Finance', 'nn group': 'Finance',
    'nationale-nederlanden': 'Finance', 'apg': 'Finance', 'pggm': 'Finance',
    'heineken': 'FMCG', 'unilever': 'FMCG', 'philips': 'Industrial',
    'dsm': 'Industrial', 'dsm-firmenich': 'Industrial', 'akzonobel': 'Industrial',
    'wolters kluwer': 'Tech', 'randstad': 'Other',
}

def get_sector(job):
    ind = (job.get('company_industry') or '').lower()
    if any(k in ind for k in ['oil', 'gas', 'energy', 'utilities', 'renewable']):
        return 'Energy'
    if any(k in ind for k in ['consult', 'management consult', 'strategy']):
        return 'Consulting'
    if any(k in ind for k in ['software', 'tech', 'internet', 'semiconductor', 'information']):
        return 'Tech'
    if any(k in ind for k in ['financial', 'bank', 'insurance', 'investment', 'capital']):
        return 'Finance'
    if any(k in ind for k in ['consumer goods', 'fmcg', 'food', 'beverage', 'retail']):
        return 'FMCG'
    if any(k in ind for k in ['industrial', 'manufacturing', 'chemical', 'aerospace']):
        return 'Industrial'
    co = job.get('company', '').lower()
    for k, v in SECTOR_MAP.items():
        if k in co:
            return v
    return 'Other'

for job in jobs:
    job['sector']     = get_sector(job)
    job['has_salary'] = bool(job.get('min_amount') or job.get('max_amount'))

# 8. Serialize — ASCII-safe + escape </script> to prevent HTML breakage
jobs_json = json.dumps(jobs, ensure_ascii=True).replace('</', '<\\/')

# 9. Read linkedin.html — newline='' prevents Python treating U+2028/U+2029 as line breaks
with open(DASH_FILE, encoding='utf-8', newline='') as f:
    html = f.read()

# 10. Replace JOBS_DATA — splice from "const JOBS_DATA = " up to the known JS anchor
#     that immediately follows it in the template. This is immune to any broken content
#     that may have been injected by a previous failed build.
JOBS_START  = 'const JOBS_DATA = '
JOBS_ANCHOR = '// ── localStorage for statuses ──'

start_idx  = html.index(JOBS_START)
anchor_idx = html.index(JOBS_ANCHOR)
html = html[:start_idx] + f'const JOBS_DATA = {jobs_json};\n\n' + html[anchor_idx:]

# 11. Write back
with open(DASH_FILE, 'w', encoding='utf-8') as f:
    f.write(html)

# 12. Print stats
ai_scored      = sum(1 for j in jobs if 'ai_score' in j)
has_applicants = sum(1 for j in jobs if j.get('applicant_count'))
by_source = {}
for j in jobs:
    src = j.get('source', 'Unknown')
    by_source[src] = by_source.get(src, 0) + 1

print(f"  Total jobs:        {len(jobs)}")
print(f"  AI scored:         {ai_scored}")
print(f"  With applicants:   {has_applicants}")
print(f"  By source:         {by_source}")
