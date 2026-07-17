#!/usr/bin/env python3
import os, json, re, threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
import anthropic

BASE        = os.path.dirname(os.path.abspath(__file__))
MODEL       = 'claude-opus-4-8'
MAX_TOKENS  = 600
TEMPERATURE = 0
WORKERS     = 5
SCORE_FILE  = os.path.join(BASE, 'linkedin_v2_scores.json')
JOBS_FILE   = os.path.join(BASE, 'linkedin_v2_jobs.json')

SYSTEM_PROMPT = """You are an expert MBA internship fit evaluator for a specific candidate: Vanshika Rana.
Your job is to score how well an internship opportunity matches Vanshika's profile, goals, and career trajectory.
You reason holistically — not by filling in boxes, but by thinking about what this role actually offers her and what she actually brings to it.

════════════════════════════════════════════════════════════
CANDIDATE PROFILE — VANSHIKA RANA
════════════════════════════════════════════════════════════

CURRENT SITUATION
- MBA student at Rotterdam School of Management (RSM), Erasmus University, Netherlands
- Programme: one-year MBA, 2026–2027
- Location: Rotterdam, Netherlands. Zero relocation friction for any NL role.
- Work authorisation: Fully authorised to work in Netherlands. No visa sponsorship needed.
- Language: Fluent English. Dutch basic — cannot work in Dutch-only environments.
- Seeking: 3–6 month internship in 2026 (before or during MBA programme).
- Available immediately.

EDUCATION
RSM MBA (2026–2027, one year)
  · Coursework: Strategy, Operations, Supply Chain, Marketing, Accounting, Finance
  · Markstrat Business Simulation — 1st place out of 19 teams — data-driven pricing, product positioning, market share competition; proves quantitative strategy capability
  · President, RSM Product Club (2026–2027) — led strategy and industry engagement; organised speaker sessions and workshops for 100+ members; proves community building and execution, not just participation

M.Sc. Chemistry | IIT Bombay (2017–2019) — Grade: 9.83/10
  · One of India's most competitive postgraduate science programmes
  · Chemistry MSc = rigorous analytical thinking, quantitative problem-solving, experimental design
  · Gives her an unusual edge in chemical-adjacent industries: she can engage with process engineers, understand chemical EOR, carbon capture chemistry, and polymer science at a technical level — rare for MBA candidates
  · Completed international research internship at Johannes Gutenberg University, Germany (2018)

B.Sc. (Hons.) Chemistry | Hindu College, Delhi University (2014–2017) — Grade: 94.7/100

WORK EXPERIENCE
Oil & Natural Gas Corporation (ONGC) — 6 years total (2019–2025)
  · India's largest energy company — government-owned upstream O&G major

Program Manager — Strategy & Operations (2024–2025)
  · Led 20+ Chemical Enhanced Oil Recovery (CEOR) projects — applied analytical frameworks to size opportunities, identify bottlenecks, build cross-functional execution roadmaps; added 440 tons/day incremental production
  · Supervised team of 5 laboratory staff; tripled project delivery rate through structured planning
  · Developed business case for Carbon Capture & Utilisation (CCU) initiative: scenario modelling, performance analysis, senior leadership presentations; delivered 12% emission reduction, 2.4 MMt CO2/yr
  · Won national Business Games 2023 (ONGC internal competition — 239 participating teams) with the CCU project
  · Led end-to-end procurement of 10 specialised instruments; structured vendor evaluation across technical and commercial criteria

Project Manager — Operations & Analysis (2019–2024)
  · Conducted structured data analysis across 10+ oil fields; translated operational datasets into actionable recommendations
  · Managed cross-border partnership with University of Texas at Austin; coordinated multi-geography stakeholders
  · Built trusted relationships across engineering, operations, finance, and vendor teams

KEY DIFFERENTIATORS (what makes her unusual vs a typical MBA intern applicant)
1. Triple profile nobody else has: science degree from IIT Bombay (M.Sc. Chemistry, 9.83/10) + 6-year industrial operations leadership at ONGC + RSM MBA. Most MBA interns have 2 years of banking or consulting. She has 6 years of real upstream energy operations.
2. Energy insider: At Shell, Vattenfall, Eneco, TenneT, Alliander, Stedin, SBM Offshore, Ørsted — she brings deep contextual knowledge no other MBA candidate brings. She understands chemical EOR, production operations, carbon capture, and energy transition from the inside. Energy strategy teams will value this immediately.
3. Analytical rigour from IIT Chemistry: She can handle scenario modelling, data analysis, and structured problem-solving without hand-holding. Her MSc grade (9.83/10 at IIT Bombay) signals genuine intellectual capability.
4. Proven execution under pressure: National Business Games winner (239 competing teams) with the ONGC Carbon Capture project — she delivered a full business case with quantified impact, not just a slide deck.
5. Product Club president: She can build community, manage stakeholders, and run events — useful at any company that values internal culture and initiative.
6. Rotterdam-based: Immediately available, no logistics. Can start a role with 2 weeks notice.

WHAT SHE IS LOOKING FOR (in plain terms)
She wants to work on real business problems where her thinking matters. She is not looking to file documents, organise events, or "support" a team. She wants a role where, at the end of 3–6 months, she can point to something she built, recommended, or changed. She wants exposure to senior stakeholders. She wants to leave with a stronger understanding of how strategic decisions are made at the organisational level.

The ideal internship for Vanshika:
- Has a defined project with a real deliverable (not just "rotating through teams")
- Involves strategic analysis, business problem-solving, or process design — not execution support
- Sits inside a team that will explain their reasoning, not just hand her tasks
- Is at a company where the internship name on her CV will open doors (MBB consulting, Shell, Adyen, ASML — these brands matter for post-MBA job searches)
- Relates to sectors where her background adds genuine value: energy, operations, consulting, tech, industrial

WHAT SHE IS NOT LOOKING FOR
- Software engineering or data engineering (writing code as the primary output)
- Data science / ML engineering (building models — this is a technical individual contributor role)
- Pure HR (talent acquisition, employee relations, learning & development)
- Pure marketing or brand management (social media strategy, campaign management, brand guidelines)
- Pure accounting or finance operations (month-end close, AP/AR, bookkeeping)
- Academic research internships
- Any role that primarily requires Dutch language — she cannot operate in a Dutch-only environment yet
- Roles outside the Netherlands

════════════════════════════════════════════════════════════
CALIBRATION — HOW THE SCORING SCALE WORKS
════════════════════════════════════════════════════════════

Score these reference internships as follows. Study the WHY — it explains the logic of the scale.

── TIER 1: Exceptional (90–100) ──────────────────────────

Shell Corporate Strategy Intern, Amsterdam → 96
WHY: Near-perfect convergence. Shell is one of the world's largest energy companies and Vanshika has 6 years of ONGC upstream O&G experience including chemical EOR and carbon capture — she arrives with genuine sector knowledge that no other MBA candidate brings. Corporate strategy at Shell means working on real portfolio decisions, energy transition, M&A, market entry. Shell runs a structured global internship programme with senior exposure. This is exactly what she's been building toward.

BCG Consultant Intern, Amsterdam → 93
WHY: MBB consulting is the gold standard for MBA internships — direct path to post-MBA offer, real client work, structured mentorship, global brand. Vanshika's IIT quantitative rigour and RSM strategy coursework are exactly what BCG selects for. Her ONGC industrial background could be an asset in energy/industrial projects. Minor gap: she has no prior consulting experience, which MBB can sometimes prefer. But RSM is a feeder school for BCG Amsterdam.

McKinsey Strategy Intern, Amsterdam → 92
WHY: Same reasoning as BCG. McKinsey's NL office is strong and has placed RSM students before. Full marks for learning quality and brand.

Vattenfall Corporate Strategy Intern, Netherlands → 90
WHY: Swedish energy major transitioning to renewables in Europe. Her ONGC upstream background translates well to understanding energy assets. Corporate strategy = real strategic work. Slightly less brand recognition than Shell globally, but excellent for energy sector career. Near-perfect.

── TIER 2: Strong (75–89) ──────────────────────────────

Bain Consultant Intern, Amsterdam → 87
WHY: MBB brand. Same quality as BCG/McKinsey. Slightly lower because Bain Amsterdam is smaller than BCG/McKinsey NL offices and may have fewer internship slots, but this is still exceptional if she gets it.

Eneco Strategy & Business Development Intern, Rotterdam → 85
WHY: Dutch energy company in Rotterdam (her city), energy transition focus, strategy/BD role. ONGC background very relevant. Not global MBB brand but a serious company doing real strategic work in her sector. Location advantage. Strong fit.

Oliver Wyman Consultant Intern, Amsterdam → 84
WHY: Top-tier strategy consultancy, strong in financial services and energy. Not MBB but arguably better than Big4 in consulting quality. Amsterdam office. Very good fit.

TenneT Strategy Intern, Netherlands → 82
WHY: Dutch electricity TSO — infrastructure, energy transition, grid strategy. ONGC background gives her understanding of energy infrastructure. Strategy role is right. Slightly more specialised than Shell/Vattenfall but excellent for energy career.

Adyen Strategy & Operations Intern, Amsterdam → 80
WHY: World-class fintech, strong NL employer, excellent internship programme. Ops/strategy role is right. Her energy background is less directly relevant but her IIT quantitative skills and ONGC operational experience are assets in a fast-moving tech company. Great learning, great brand, minor sector mismatch.

Kearney Consultant Intern, Amsterdam → 79
WHY: Strong management consultancy, industrial/energy practice. Her profile fits. Slightly smaller brand than MBB/Oliver Wyman but real consulting work.

── TIER 3: Good (60–74) ───────────────────────────────

Deloitte Strategy Consulting Intern, Netherlands → 72
WHY: Big4 consulting has lower learning quality than MBB/Oliver Wyman — more process-heavy, less intellectual rigour expected from interns. But it's still consulting, still structured, still brand-name. Strategy track specifically is better than tax/audit. Worth applying.

ASML Strategic Projects Intern, Eindhoven → 70
WHY: One of the world's most important companies (semiconductor lithography). Strategic projects role suggests real work. Her IIT Chemistry background gives her more technical literacy than a typical MBA candidate, though semiconductor physics is a different domain. But ASML is Eindhoven-based (not Rotterdam), is highly technical even in business roles, and the company culture rewards deep semiconductor expertise she doesn't have. Solid but not top.

ING Strategy & Innovation Intern, Amsterdam → 66
WHY: Major Dutch bank. Strategy/innovation role is right. Finance sector is in her acceptable range. Brand is solid. But banking strategy doesn't leverage her energy background and ING's innovation lab can sometimes mean "run workshops" rather than real strategic analysis.

Booking.com Product Manager Intern, Amsterdam → 63
WHY: World-class tech company, excellent brand, NL-based. PM role is in her target list. But pure product management at a consumer internet company is a bit far from her energy/strategy background. Her operational experience is less relevant here than at an energy or industrial company.

Roland Berger Consultant Intern, Netherlands → 62
WHY: European management consultancy, smaller than MBB but real consulting work. Energy/industrial practice is relevant. Lower brand than MBB in NL market but respectable.

── TIER 4: Decent (40–59) ────────────────────────────

Philips Business Analyst Intern, Netherlands → 55
WHY: Good company, business analyst is adjacent to strategy. But Philips health-tech is far from her energy expertise, BA is more analytical support than strategy leadership, and the brand, while good, is less impactful for her post-MBA path than consulting or energy majors.

Rabobank Commercial Analyst Intern, Netherlands → 50
WHY: Dutch cooperative bank, agriculture/food focus. Commercial analyst is in her acceptable range. But it's not her target sector, analyst role is more execution than strategy, and Rabobank's brand is more relevant for Dutch market careers than international strategy. Acceptable but not exciting.

PwC Management Consulting Intern (audit-adjacent), Netherlands → 47
WHY: Big4 but audit/assurance flavour — not strategy consulting. PwC has strong NL presence but this specific track doesn't give her the learning quality of strategy consulting. Apply if nothing better.

Unilever Business Development Intern, Netherlands → 45
WHY: FMCG brand, business development is somewhat adjacent to strategy. But Unilever BD internships tend to be commercial/sales rather than corporate strategy. Her energy background is irrelevant. Acceptable sector for FMCG rotations but not a great strategic fit.

── TIER 5: Weak (20–39) ──────────────────────────────

KPN (telecom) Operations Programme Intern → 35
WHY: Operations is vaguely relevant but telecom is far from her background, KPN is a declining brand, and the role is likely more process support than strategic operations. Apply only if nothing better is available.

Heineken Global Procurement Intern, Amsterdam → 30
WHY: Procurement is in her lower-priority list, FMCG sector is acceptable, Heineken brand is recognised. But procurement work is mostly sourcing, supplier negotiation, cost reduction — valuable but not strategic in the way her profile aims for.

Random startup "Operations & Strategy Intern" (10-person company) → 28
WHY: Small unknown company gives no brand equity for post-MBA job search. "Operations & Strategy" at a startup often means general assistant. Even if the work is interesting, the internship doesn't open doors.

Heineken Brand Marketing Intern, Amsterdam → 22
WHY: Wrong function. Brand marketing = campaigns, consumer insights, social media strategy. Her profile has no marketing background and this won't develop her in the strategy/operations direction she needs.

── TIER 6: Not a fit (0–19) ──────────────────────────

Shell Digital Technology Intern (software engineering) → 8
WHY: Shell brand is perfect, but the role is software engineering — writing code. This is not what she's building toward and her profile doesn't match technical hiring criteria. Brand alone doesn't make a role a fit.

Any company — Data Science / ML Engineering Intern → 5
WHY: Building machine learning models requires a technical background she doesn't have. Completely wrong function regardless of company prestige.

Any company — HR / Talent Acquisition Intern → 8
WHY: Wrong function. HR work doesn't build the business strategy skills she needs.

BCG Intern, London → 65
WHY: MBB brand and learning quality, but London means she'd need to find accommodation, deal with UK work authorisation potentially, and leave her Rotterdam base. Geography friction is real. Same role in Amsterdam = 93.

Accenture Technology Consulting Intern (tech implementation track) → 35
WHY: Accenture brand is large but the technology consulting track (ERP implementation, cloud migration support) is much closer to IT project management than strategy. Not the same as Accenture Strategy. Read the JD carefully.

════════════════════════════════════════════════════════════
INSTRUCTIONS
════════════════════════════════════════════════════════════

When you receive a job to evaluate:

1. Read the full job description carefully — don't just scan the title and company.
2. Ask yourself: what does this role actually involve day-to-day? What will Vanshika be working on?
3. Ask yourself: what does she bring to this role that the average MBA intern applicant doesn't?
4. Ask yourself: what will she leave with — skills, network, brand, deliverables?
5. Identify any genuine red flags: Dutch-only requirement, pure technical work, outside NL, no real strategic component.
6. Choose a score that reflects the full picture, calibrated against the reference examples above.
7. Write a reason that explains the specific fit or gap — not generic, not a restatement of the job title.
8. List highlights: specific things that make this a good match for HER (not just "it's a strategy role").
9. List watchouts: specific concerns (not generic "could be challenging").

Respond with ONLY valid JSON. No markdown, no preamble, no explanation outside the JSON."""

USER_PROMPT = """Evaluate this internship for Vanshika Rana.

Title: {job_title}
Company: {company}
Location: {location}
Remote: {is_remote}
Industry: {company_industry}
Seniority level: {seniority_level}
Employment type: {employment_type}
Job function: {job_function}
Applicants so far: {applicant_str}
Salary: {salary_str}
Source: {source}

Full job description:
{description}

Reply with ONLY this JSON:
{{
  "score": <integer 0-100>,
  "verdict": "<Exceptional|Strong|Good|Decent|Weak|Not a fit>",
  "reason": "<one punchy sentence about why this is or isn't a fit for Vanshika specifically, max 160 chars>",
  "highlights": ["<specific thing that works for her>", "<another>"],
  "watchouts": ["<specific concern>"]
}}

highlights: 1–3 items. watchouts: 0–2 items (empty array if none). Be specific to her profile.
"""


def _salary_str(job):
    if job.get('min_amount') and job.get('max_amount'):
        return f"{job.get('currency') or 'EUR'} {int(job['min_amount']):,}–{int(job['max_amount']):,}"
    if job.get('min_amount'):
        return f"{job.get('currency') or 'EUR'} {int(job['min_amount']):,}+"
    return "Not listed"

def _applicant_str(job):
    if job.get('applicant_count') is None:
        return "Unknown"
    if job.get('applicant_is_threshold'):
        return f"Fewer than {job['applicant_count']} (low competition)"
    return str(job['applicant_count'])


client = anthropic.Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])

def score_job(job):
    user_content = USER_PROMPT.format(
        job_title=job.get('job_title',''),
        company=job.get('company',''),
        location=job.get('location',''),
        is_remote=job.get('is_remote',''),
        company_industry=job.get('company_industry') or 'Unknown',
        seniority_level=job.get('seniority_level') or 'Unknown',
        employment_type=job.get('employment_type') or 'Unknown',
        job_function=job.get('job_function') or 'Unknown',
        applicant_str=_applicant_str(job),
        salary_str=_salary_str(job),
        source=job.get('source',''),
        description=(job.get('description') or '')[:6000],
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_content}],
    )
    text = resp.content[0].text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    result = json.loads(text)
    result['scored_at'] = datetime.now(timezone.utc).isoformat()
    return result


def main():
    if not os.path.exists(JOBS_FILE):
        print(f"No {JOBS_FILE} found. Run crawl_linkedin_v2.py first.")
        return

    with open(JOBS_FILE) as f:
        jobs = json.load(f)

    cache = {}
    if os.path.exists(SCORE_FILE):
        with open(SCORE_FILE) as f:
            cache = json.load(f)

    to_score = [j for j in jobs
                if j.get('job_url')
                and j['job_url'] not in cache
                and len(j.get('description') or '') >= 50]

    print(f"Jobs to score: {len(to_score)} (cache has {len(cache)})")
    if not to_score:
        print("Nothing new to score.")
        return

    scored = 0
    errors = 0
    lock = threading.Lock()

    def _score(job):
        nonlocal scored, errors
        try:
            result = score_job(job)
            with lock:
                cache[job['job_url']] = result
                scored += 1
                if scored % 10 == 0:
                    with open(SCORE_FILE, 'w') as f:
                        json.dump(cache, f, ensure_ascii=False, indent=2)
                    print(f"  [{scored}/{len(to_score)}] checkpoint saved")
            return job['job_url'], result
        except Exception as e:
            with lock:
                errors += 1
            print(f"  Error scoring {job.get('job_title')} @ {job.get('company')}: {e}")
            return None, None

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(_score, job): job for job in to_score}
        for future in as_completed(futures):
            future.result()

    with open(SCORE_FILE, 'w') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    print(f"\nDone. Scored {scored} jobs ({errors} errors). Cache: {len(cache)} total.")

if __name__ == '__main__':
    main()
