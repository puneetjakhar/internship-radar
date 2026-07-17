#!/usr/bin/env python3
"""
Send v2 email notifications (Opus-scored LinkedIn v2 pipeline).

Modes:
  python notify_v2.py            -- hourly: jobs newly seen in the last 1h
  python notify_v2.py --daily    -- daily:  jobs newly seen in the last 24h

Data source: linkedin_v2_jobs.json (has per-job first_seen_at) +
linkedin_v2_scores.json (Opus scores + verdicts keyed by job_url).

Email style: reuses notify.py's send_email + match_color and mirrors the
v1 purple template. Adds a compact italic verdict row beneath each job.
Links to the v2 dashboard (linkedin.html) rather than v1.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

from notify import match_color, send_email

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
JOBS_FILE   = os.path.join(BASE_DIR, 'linkedin_v2_jobs.json')
SCORES_FILE = os.path.join(BASE_DIR, 'linkedin_v2_scores.json')

DEFAULT_V2_DASHBOARD = 'https://puneetjakhar.github.io/vanshika-nl-jobs-dashboard/linkedin.html'


def _load_jobs():
    if not os.path.exists(JOBS_FILE):
        print(f'⚠ {JOBS_FILE} missing — nothing to notify about')
        return []
    with open(JOBS_FILE) as f:
        return json.load(f)


def _load_scores():
    if not os.path.exists(SCORES_FILE):
        print(f'⚠ {SCORES_FILE} missing — will send without verdicts')
        return {}
    with open(SCORES_FILE) as f:
        return json.load(f)


def _parse_first_seen(job):
    v = job.get('first_seen_at')
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _filter_new(jobs, hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for job in jobs:
        dt = _parse_first_seen(job)
        if dt and dt >= cutoff:
            out.append(job)
    return out


def _enrich_and_sort(jobs, scores):
    for job in jobs:
        url = job.get('job_url') or ''
        s = scores.get(url, {})
        job['_score']   = s.get('score')
        job['_verdict'] = (s.get('verdict') or s.get('reason') or '').strip()
    return sorted(jobs, key=lambda j: j.get('_score') or 0, reverse=True)


def _build_rows(jobs):
    rows = ''
    for job in jobs:
        url     = job.get('job_url') or '#'
        title   = job.get('job_title') or 'Unknown'
        co      = job.get('company') or ''
        loc     = job.get('location') or 'Netherlands'
        date    = job.get('date_posted') or ''
        score   = job.get('_score')
        verdict = job.get('_verdict') or ''
        pct_str = f'{score}%' if score is not None else '—'
        color   = match_color(score) if score is not None else '#94a3b8'
        rows += (
            f'<tr>'
            f'<td style="padding:9px 8px;border-bottom:0;text-align:center">'
            f'<span style="background:{color};color:#fff;border-radius:12px;padding:3px 9px;font-weight:600;font-size:13px">{pct_str}</span></td>'
            f'<td style="padding:9px 8px;border-bottom:0">'
            f'<a href="{url}" style="color:#7c3aed;text-decoration:none;font-weight:600">{title}</a></td>'
            f'<td style="padding:9px 8px;border-bottom:0;color:#475569">{co}</td>'
            f'<td style="padding:9px 8px;border-bottom:0;color:#64748b;font-size:12px">{loc}</td>'
            f'<td style="padding:9px 8px;border-bottom:0;color:#94a3b8;font-size:12px">{date}</td>'
            f'<td style="padding:9px 8px;border-bottom:0;text-align:center">'
            f'<a href="{url}" style="background:#7c3aed;color:#fff;padding:4px 12px;border-radius:6px;text-decoration:none;font-size:12px;white-space:nowrap">Apply</a></td>'
            f'</tr>'
        )
        if verdict:
            rows += (
                f'<tr>'
                f'<td colspan="6" style="padding:0 8px 9px 34px;border-bottom:1px solid #e2e8f0;color:#64748b;font-size:12px;font-style:italic">{verdict}</td>'
                f'</tr>'
            )
        else:
            rows += (
                f'<tr>'
                f'<td colspan="6" style="padding:0 8px 9px;border-bottom:1px solid #e2e8f0"></td>'
                f'</tr>'
            )
    return rows


def _build_email(heading, subheading, jobs, dashboard_url):
    rows = _build_rows(jobs)
    return f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;background:#f8fafc;margin:0;padding:20px">
  <div style="max-width:840px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.08)">
    <div style="background:#7c3aed;padding:22px 28px">
      <h1 style="margin:0;color:#fff;font-size:20px">{heading}</h1>
      <p style="margin:5px 0 0;color:#ede9fe;font-size:13px">{subheading}</p>
    </div>
    <div style="padding:20px 28px">
      <a href="{dashboard_url}" style="display:inline-block;background:#7c3aed;color:#fff;padding:8px 20px;border-radius:8px;text-decoration:none;font-weight:600;margin-bottom:20px">Open v2 Dashboard</a>
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


def run_hourly(jobs, scores):
    filtered = _filter_new(jobs, hours=1)
    if not filtered:
        print('No new v2 jobs in last 1h')
        return
    enriched = _enrich_and_sort(filtered, scores)
    now_str = datetime.now(timezone.utc).strftime('%b %d %H:%M')
    dashboard = os.environ.get('DASHBOARD_URL', DEFAULT_V2_DASHBOARD)
    html = _build_email(
        f'{len(enriched)} new v2 internship{"s" if len(enriched) != 1 else ""}',
        f'v2 pipeline · Opus-scored · Crawled at {now_str} UTC',
        enriched,
        dashboard,
    )
    send_email(f'{len(enriched)} new v2 internships | {now_str} UTC', html)
    print(f'v2 hourly email sent: {len(enriched)} jobs')


def run_daily(jobs, scores):
    filtered = _filter_new(jobs, hours=24)
    if not filtered:
        print('No new v2 jobs in last 24h')
        return
    enriched = _enrich_and_sort(filtered, scores)
    today = datetime.now(timezone.utc).strftime('%b %d, %Y')
    dashboard = os.environ.get('DASHBOARD_URL', DEFAULT_V2_DASHBOARD)
    html = _build_email(
        f'Daily v2 report: {len(enriched)} internship{"s" if len(enriched) != 1 else ""}',
        f'{today} · v2 pipeline · Opus-scored · Sorted by match',
        enriched,
        dashboard,
    )
    send_email(f'Daily v2 report | {len(enriched)} roles | {today}', html)
    print(f'v2 daily email sent: {len(enriched)} jobs')


def main():
    jobs = _load_jobs()
    if not jobs:
        return
    scores = _load_scores()
    if '--daily' in sys.argv:
        run_daily(jobs, scores)
    else:
        run_hourly(jobs, scores)


if __name__ == '__main__':
    main()
